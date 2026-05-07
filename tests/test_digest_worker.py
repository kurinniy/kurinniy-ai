import unittest
from datetime import date, datetime, timezone

from ai_me.digest_worker import DigestSchedulerWorker
from ai_me.domain.digest import DigestStatus, DigestType, UserDigestSettings
from ai_me.domain.health_import import UserGoogleDriveSettings
from ai_me.domain.user import UserStatus
from ai_me.services.google_drive_import import GoogleDriveHealthImportService
from ai_me.services.health_service import HealthService
from ai_me.storage.memory import InMemoryStore


class FakeDigestBot:
    def __init__(self) -> None:
        self.daily_calls = []
        self.weekly_calls = []
        self.daily_result = {"text_message_id": 101}
        self.weekly_result = {"text_message_id": 202}

    def send_daily_digest(self, chat_id: int, user_id: int, digest_date: date, preview: bool = False):
        self.daily_calls.append((chat_id, user_id, digest_date, preview))
        return self.daily_result

    def send_weekly_digest(self, chat_id: int, user_id: int, week_start: date, preview: bool = False):
        self.weekly_calls.append((chat_id, user_id, week_start, preview))
        return self.weekly_result


class FakeGoogleDriveClient:
    def is_configured(self) -> bool:
        return True

    def ensure_folder_access(self, folder_id: str) -> None:
        return None

    def list_json_files(self, folder_id: str):
        return [
            type(
                "DriveFile",
                (),
                {
                    "file_id": "file-1",
                    "name": "HealthAutoExport-2026-05-01.json",
                    "checksum": "checksum-1",
                    "created_at": datetime(2026, 5, 1, 9, 0),
                    "modified_at": datetime(2026, 5, 1, 9, 5),
                    "size_bytes": 128,
                },
            )()
        ]

    def download_file(self, file_id: str) -> bytes:
        return (
            b'{"data":{"metrics":['
            b'{"name":"step_count","units":"count","data":[{"qty":1500,"date":"2026-05-01 10:00:00 +0300","source":"iPhone"}]},'
            b'{"name":"active_energy","units":"kJ","data":[{"qty":100,"date":"2026-05-01 10:00:00 +0300","source":"iPhone"}]}'
            b"]}}"
        )


class DigestSchedulerWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.service = HealthService(self.store, default_timezone_name="Europe/Moscow")
        self.user = self.store.create_user(
            telegram_user_id=96445950,
            chat_id=96445950,
            username="owner",
            first_name="Owner",
            status=UserStatus.ACTIVE,
            is_admin=True,
        )
        self.bot = FakeDigestBot()
        self.worker = DigestSchedulerWorker(service=self.service, bot=self.bot, poll_interval_seconds=60)

    def tearDown(self) -> None:
        self.store.close()

    def test_run_once_sends_daily_digest_after_scheduled_time(self) -> None:
        self.store.upsert_user_digest_settings(
            UserDigestSettings(
                user_id=self.user.user_id,
                timezone_name="Europe/Moscow",
                daily_digest_enabled=True,
                daily_digest_time="08:00",
                weekly_digest_enabled=False,
                weekly_digest_time="08:00",
            )
        )

        self.worker.run_once(now_utc=datetime(2026, 5, 7, 5, 10, tzinfo=timezone.utc))

        self.assertEqual(self.bot.daily_calls, [(96445950, self.user.user_id, date(2026, 5, 6), False)])
        runs = self.service.list_digest_runs(self.user.user_id, digest_type=DigestType.DAILY)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, DigestStatus.SENT)

    def test_run_once_sends_weekly_digest_on_monday_morning(self) -> None:
        self.store.upsert_user_digest_settings(
            UserDigestSettings(
                user_id=self.user.user_id,
                timezone_name="Europe/Moscow",
                daily_digest_enabled=False,
                daily_digest_time="08:00",
                weekly_digest_enabled=True,
                weekly_digest_time="08:00",
                weekly_digest_weekday=0,
            )
        )

        self.worker.run_once(now_utc=datetime(2026, 5, 11, 5, 5, tzinfo=timezone.utc))

        self.assertEqual(self.bot.weekly_calls, [(96445950, self.user.user_id, date(2026, 5, 4), False)])
        runs = self.service.list_digest_runs(self.user.user_id, digest_type=DigestType.WEEKLY)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, DigestStatus.SENT)

    def test_run_once_marks_digest_as_skipped_when_bot_returns_no_payload(self) -> None:
        self.store.upsert_user_digest_settings(
            UserDigestSettings(
                user_id=self.user.user_id,
                timezone_name="Europe/Moscow",
                daily_digest_enabled=True,
                daily_digest_time="08:00",
                weekly_digest_enabled=False,
                weekly_digest_time="08:00",
            )
        )
        self.bot.daily_result = None

        self.worker.run_once(now_utc=datetime(2026, 5, 7, 5, 10, tzinfo=timezone.utc))

        runs = self.service.list_digest_runs(self.user.user_id, digest_type=DigestType.DAILY)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, DigestStatus.SKIPPED)

    def test_run_once_does_not_resend_already_sent_digest(self) -> None:
        self.store.upsert_user_digest_settings(
            UserDigestSettings(
                user_id=self.user.user_id,
                timezone_name="Europe/Moscow",
                daily_digest_enabled=True,
                daily_digest_time="08:00",
                weekly_digest_enabled=False,
                weekly_digest_time="08:00",
            )
        )
        self.service.create_digest_run(
            self.user.user_id,
            digest_type=DigestType.DAILY,
            digest_date=date(2026, 5, 6),
            status=DigestStatus.SENT,
            now=datetime(2026, 5, 7, 8, 0),
        )

        self.worker.run_once(now_utc=datetime(2026, 5, 7, 5, 10, tzinfo=timezone.utc))

        self.assertEqual(self.bot.daily_calls, [])

    def test_run_once_imports_google_drive_health_files_before_digest(self) -> None:
        self.service = HealthService(
            self.store,
            default_timezone_name="Europe/Moscow",
            google_drive_import_service=GoogleDriveHealthImportService(
                store=self.store,
                google_drive_client=FakeGoogleDriveClient(),
            ),
        )
        self.worker = DigestSchedulerWorker(service=self.service, bot=self.bot, poll_interval_seconds=60)
        self.store.upsert_user_google_drive_settings(
            UserGoogleDriveSettings(
                user_id=self.user.user_id,
                folder_id="folder-123",
                folder_url="https://drive.google.com/drive/folders/folder-123",
                enabled=True,
                created_at=datetime(2026, 5, 7, 8, 0),
                updated_at=datetime(2026, 5, 7, 8, 0),
            )
        )
        self.store.upsert_user_digest_settings(
            UserDigestSettings(
                user_id=self.user.user_id,
                timezone_name="Europe/Moscow",
                daily_digest_enabled=False,
                daily_digest_time="08:00",
                weekly_digest_enabled=False,
                weekly_digest_time="08:00",
            )
        )

        self.worker.run_once(now_utc=datetime(2026, 5, 7, 5, 10, tzinfo=timezone.utc))

        summary = self.store.build_health_summary(self.user.user_id, date(2026, 5, 1))
        self.assertEqual(summary.steps, 1500)
