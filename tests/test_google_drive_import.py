import json
import unittest
from datetime import datetime

from ai_me.domain.health_import import HealthImportProvider
from ai_me.domain.user import UserStatus
from ai_me.services.google_drive_import import (
    GoogleDriveFile,
    GoogleDriveHealthImportService,
    GoogleDriveHealthJSONParser,
    extract_google_drive_folder_id,
)
from ai_me.storage.memory import InMemoryStore


def _sample_health_export() -> bytes:
    payload = {
        "data": {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [
                        {"qty": 1200, "date": "2026-05-01 10:00:00 +0300", "source": "iPhone"},
                        {"qty": 800, "date": "2026-05-01 12:00:00 +0300", "source": "iPhone"},
                    ],
                },
                {
                    "name": "walking_running_distance",
                    "units": "km",
                    "data": [
                        {"qty": 0.9, "date": "2026-05-01 10:00:00 +0300", "source": "iPhone"},
                        {"qty": 1.1, "date": "2026-05-01 12:00:00 +0300", "source": "iPhone"},
                    ],
                },
                {
                    "name": "active_energy",
                    "units": "kJ",
                    "data": [
                        {"qty": 100, "date": "2026-05-01 10:00:00 +0300", "source": "iPhone"},
                        {"qty": 200, "date": "2026-05-01 12:00:00 +0300", "source": "iPhone"},
                    ],
                },
                {
                    "name": "flights_climbed",
                    "units": "count",
                    "data": [
                        {"qty": 4, "date": "2026-05-01 10:00:00 +0300", "source": "iPhone"},
                    ],
                },
            ]
        }
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class FakeGoogleDriveClient:
    def __init__(self) -> None:
        self.checked_folder_ids = []
        self.files = [
            GoogleDriveFile(
                file_id="file-1",
                name="HealthAutoExport-2026-05-01.json",
                checksum="checksum-1",
                created_at=datetime(2026, 5, 1, 9, 0),
                modified_at=datetime(2026, 5, 1, 9, 5),
                size_bytes=1024,
            )
        ]
        self.payload_by_file_id = {"file-1": _sample_health_export()}

    def is_configured(self) -> bool:
        return True

    def ensure_folder_access(self, folder_id: str) -> None:
        self.checked_folder_ids.append(folder_id)

    def list_json_files(self, folder_id: str):
        return list(self.files)

    def download_file(self, file_id: str) -> bytes:
        return self.payload_by_file_id[file_id]


class GoogleDriveHealthImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.user = self.store.create_user(
            telegram_user_id=96445950,
            chat_id=96445950,
            username="owner",
            first_name="Owner",
            status=UserStatus.ACTIVE,
            is_admin=True,
        )
        self.client = FakeGoogleDriveClient()
        self.service = GoogleDriveHealthImportService(
            store=self.store,
            google_drive_client=self.client,
            parser=GoogleDriveHealthJSONParser(),
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_extract_folder_id_from_google_drive_url(self) -> None:
        folder_id = extract_google_drive_folder_id("https://drive.google.com/drive/folders/folder-123_ABC")
        self.assertEqual(folder_id, "folder-123_ABC")

    def test_connect_folder_stores_user_settings(self) -> None:
        settings = self.service.connect_folder(
            self.user.user_id,
            "https://drive.google.com/drive/folders/folder-123",
            now=datetime(2026, 5, 7, 8, 0),
        )
        self.assertEqual(settings.folder_id, "folder-123")
        self.assertEqual(self.client.checked_folder_ids, ["folder-123"])

    def test_import_new_files_creates_activity_entry_and_history(self) -> None:
        self.service.connect_folder(self.user.user_id, "folder-123", now=datetime(2026, 5, 7, 8, 0))

        result = self.service.import_new_files(self.user.user_id, now=datetime(2026, 5, 7, 8, 5))

        self.assertEqual(result.provider, HealthImportProvider.GOOGLE_DRIVE)
        self.assertEqual(result.scanned_files, 1)
        self.assertEqual(result.imported_files, 1)
        self.assertEqual(result.activity_entries_count, 1)
        summary = self.store.build_health_summary(self.user.user_id, datetime(2026, 5, 1).date())
        self.assertEqual(summary.steps, 2000)
        self.assertEqual(summary.activity_minutes, 0)
        imported_files = self.store.list_health_import_files(self.user.user_id, provider=HealthImportProvider.GOOGLE_DRIVE)
        self.assertEqual(len(imported_files), 1)
        self.assertEqual(imported_files[0].status.value, "imported")

    def test_import_new_files_is_idempotent_for_same_drive_file(self) -> None:
        self.service.connect_folder(self.user.user_id, "folder-123", now=datetime(2026, 5, 7, 8, 0))

        self.service.import_new_files(self.user.user_id, now=datetime(2026, 5, 7, 8, 5))
        result = self.service.import_new_files(self.user.user_id, now=datetime(2026, 5, 7, 8, 10))

        self.assertEqual(result.scanned_files, 1)
        self.assertEqual(result.imported_files, 0)
        self.assertEqual(result.skipped_files, 1)
        imported_files = self.store.list_health_import_files(self.user.user_id, provider=HealthImportProvider.GOOGLE_DRIVE)
        self.assertEqual(len(imported_files), 1)


if __name__ == "__main__":
    unittest.main()
