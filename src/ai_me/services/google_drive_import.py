import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Optional, Protocol
from uuid import uuid4

from ai_me.domain.health import ActivityEntry
from ai_me.domain.health_import import (
    HealthImportFile,
    HealthImportProvider,
    HealthImportResult,
    HealthImportStatus,
    UserGoogleDriveSettings,
)


logger = logging.getLogger(__name__)


FOLDER_ID_PATTERN = re.compile(r"/folders/([a-zA-Z0-9_-]+)")
FILE_DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class GoogleDriveFile:
    file_id: str
    name: str
    checksum: str
    created_at: datetime
    modified_at: datetime
    size_bytes: int = 0


@dataclass(frozen=True)
class ParsedHealthDay:
    file_date: Optional[date]
    activity_entries: List[ActivityEntry]
    summary: Dict[str, object]


class GoogleDriveClient(Protocol):
    def is_configured(self) -> bool:
        ...

    def ensure_folder_access(self, folder_id: str) -> None:
        ...

    def list_json_files(self, folder_id: str) -> List[GoogleDriveFile]:
        ...

    def download_file(self, file_id: str) -> bytes:
        ...


class DisabledGoogleDriveClient:
    def is_configured(self) -> bool:
        return False

    def ensure_folder_access(self, folder_id: str) -> None:
        raise RuntimeError("Интеграция с Google Drive не настроена.")

    def list_json_files(self, folder_id: str) -> List[GoogleDriveFile]:
        raise RuntimeError("Интеграция с Google Drive не настроена.")

    def download_file(self, file_id: str) -> bytes:
        raise RuntimeError("Интеграция с Google Drive не настроена.")


class ServiceAccountGoogleDriveClient:
    DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

    def __init__(self, *, service_account_json: str = "", service_account_file: str = "") -> None:
        self.service_account_json = service_account_json
        self.service_account_file = service_account_file
        self._service = None

    def is_configured(self) -> bool:
        return bool(self.service_account_json or self.service_account_file)

    def ensure_folder_access(self, folder_id: str) -> None:
        service = self._get_service()
        service.files().get(fileId=folder_id, fields="id,name,mimeType").execute()

    def list_json_files(self, folder_id: str) -> List[GoogleDriveFile]:
        service = self._get_service()
        page_token = None
        files: List[GoogleDriveFile] = []
        while True:
            response = (
                service.files()
                .list(
                    q="'%s' in parents and trashed = false" % folder_id,
                    spaces="drive",
                    fields="nextPageToken, files(id,name,md5Checksum,createdTime,modifiedTime,size,mimeType)",
                    orderBy="createdTime asc",
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("files", []):
                name = str(item.get("name", ""))
                mime_type = str(item.get("mimeType", ""))
                if not name.lower().endswith(".json") and mime_type != "application/json":
                    continue
                files.append(
                    GoogleDriveFile(
                        file_id=str(item.get("id", "")),
                        name=name,
                        checksum=str(item.get("md5Checksum", "")),
                        created_at=_parse_google_timestamp(str(item.get("createdTime", ""))),
                        modified_at=_parse_google_timestamp(str(item.get("modifiedTime", ""))),
                        size_bytes=int(item.get("size", 0) or 0),
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return files

    def download_file(self, file_id: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        service = self._get_service()
        request = service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()

    def _get_service(self):
        if self._service is not None:
            return self._service

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Google Drive dependencies are not installed. Add google-api-python-client and google-auth."
            ) from exc

        credentials_info = None
        if self.service_account_json:
            credentials_info = json.loads(self.service_account_json)
        if credentials_info is not None:
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=[self.DRIVE_SCOPE],
            )
        elif self.service_account_file:
            credentials = service_account.Credentials.from_service_account_file(
                self.service_account_file,
                scopes=[self.DRIVE_SCOPE],
            )
        else:
            raise RuntimeError("Google Drive service account is not configured.")
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self._service


class GoogleDriveHealthJSONParser:
    STEP_COUNT_METRIC = "step_count"
    ACTIVE_ENERGY_METRIC = "active_energy"
    DISTANCE_METRIC = "walking_running_distance"
    FLIGHTS_METRIC = "flights_climbed"
    ACTIVE_ENERGY_KJ_TO_KCAL = 0.239005736

    def parse(self, file_bytes: bytes, file_name: str, file_id: str) -> ParsedHealthDay:
        payload = json.loads(file_bytes.decode("utf-8"))
        metrics = payload.get("data", {}).get("metrics", [])
        if not isinstance(metrics, list):
            raise ValueError("Некорректный JSON health export: ожидается data.metrics")

        metric_index = {
            str(metric.get("name", "")): metric
            for metric in metrics
            if isinstance(metric, dict) and metric.get("name")
        }
        summary = self._build_summary(metric_index)
        file_day = self._detect_file_date(file_name=file_name, metric_index=metric_index)
        activity_entries: List[ActivityEntry] = []

        if summary["steps"] > 0 or summary["active_energy_kcal"] > 0 or summary["distance_km"] > 0:
            entry_date = datetime.combine(file_day or datetime.now().date(), datetime.min.time()).replace(hour=12)
            title = (
                "Импорт Google Drive: шаги %s, дистанция %.2f км, этажи %s"
                % (summary["steps"], summary["distance_km"], summary["flights_climbed"])
            )
            activity_entries.append(
                ActivityEntry(
                    entry_id=self._make_activity_entry_id(file_id=file_id, file_name=file_name),
                    occurred_at=entry_date,
                    title=title,
                    duration_minutes=0,
                    steps=int(summary["steps"]),
                    calories_burned=int(summary["active_energy_kcal"]),
                    intensity=self._pick_intensity(int(summary["steps"])),
                )
            )

        return ParsedHealthDay(
            file_date=file_day,
            activity_entries=activity_entries,
            summary=summary,
        )

    def _build_summary(self, metric_index: Dict[str, dict]) -> Dict[str, object]:
        step_count = self._sum_qty(metric_index.get(self.STEP_COUNT_METRIC, {}))
        distance_km = self._sum_qty(metric_index.get(self.DISTANCE_METRIC, {}))
        active_energy_kj = self._sum_qty(metric_index.get(self.ACTIVE_ENERGY_METRIC, {}))
        flights_climbed = self._sum_qty(metric_index.get(self.FLIGHTS_METRIC, {}))
        return {
            "steps": int(round(step_count)),
            "distance_km": round(distance_km, 2),
            "active_energy_kj": round(active_energy_kj, 2),
            "active_energy_kcal": int(round(active_energy_kj * self.ACTIVE_ENERGY_KJ_TO_KCAL)),
            "flights_climbed": int(round(flights_climbed)),
            "metric_names": sorted(metric_index.keys()),
        }

    @staticmethod
    def _sum_qty(metric: dict) -> float:
        values = metric.get("data", [])
        if not isinstance(values, list):
            return 0.0
        total = 0.0
        for item in values:
            if not isinstance(item, dict):
                continue
            qty = item.get("qty")
            if isinstance(qty, (int, float)):
                total += float(qty)
        return total

    def _detect_file_date(self, file_name: str, metric_index: Dict[str, dict]) -> Optional[date]:
        match = FILE_DATE_PATTERN.search(file_name)
        if match:
            return date.fromisoformat(match.group(1))
        for metric in metric_index.values():
            values = metric.get("data", [])
            if not values:
                continue
            first = values[0]
            if isinstance(first, dict) and isinstance(first.get("date"), str):
                return _parse_health_timestamp(first["date"]).date()
        return None

    @staticmethod
    def _pick_intensity(steps: int) -> str:
        if steps >= 12000:
            return "high"
        if steps >= 6000:
            return "moderate"
        return "low"

    @staticmethod
    def _make_activity_entry_id(file_id: str, file_name: str) -> str:
        raw = "google_drive:%s:%s" % (file_id, file_name)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


class GoogleDriveHealthImportService:
    PROVIDER = HealthImportProvider.GOOGLE_DRIVE

    def __init__(
        self,
        *,
        store,
        google_drive_client: Optional[GoogleDriveClient] = None,
        parser: Optional[GoogleDriveHealthJSONParser] = None,
    ) -> None:
        self.store = store
        self.google_drive_client = google_drive_client or DisabledGoogleDriveClient()
        self.parser = parser or GoogleDriveHealthJSONParser()

    def is_configured(self) -> bool:
        return self.google_drive_client.is_configured()

    def connect_folder(self, user_id: int, folder_input: str, now: Optional[datetime] = None) -> UserGoogleDriveSettings:
        if not self.is_configured():
            raise RuntimeError("Интеграция с Google Drive не настроена.")
        folder_id = extract_google_drive_folder_id(folder_input)
        self.google_drive_client.ensure_folder_access(folder_id)
        current = self.store.get_user_google_drive_settings(user_id)
        return self.store.upsert_user_google_drive_settings(
            UserGoogleDriveSettings(
                user_id=user_id,
                folder_id=folder_id,
                folder_url=normalize_google_drive_folder_url(folder_input, folder_id),
                enabled=True,
                created_at=current.created_at if current else (now or datetime.now()),
                updated_at=now or datetime.now(),
            )
        )

    def get_settings(self, user_id: int) -> Optional[UserGoogleDriveSettings]:
        return self.store.get_user_google_drive_settings(user_id)

    def set_enabled(self, user_id: int, enabled: bool, now: Optional[datetime] = None) -> UserGoogleDriveSettings:
        current = self.store.get_user_google_drive_settings(user_id)
        if current is None:
            raise ValueError("Сначала подключите папку Google Drive.")
        return self.store.upsert_user_google_drive_settings(
            UserGoogleDriveSettings(
                user_id=current.user_id,
                folder_id=current.folder_id,
                folder_url=current.folder_url,
                enabled=enabled,
                created_at=current.created_at,
                updated_at=now or datetime.now(),
            )
        )

    def import_new_files(self, user_id: int, now: Optional[datetime] = None) -> HealthImportResult:
        settings = self.store.get_user_google_drive_settings(user_id)
        if settings is None or not settings.enabled:
            return HealthImportResult(
                user_id=user_id,
                provider=self.PROVIDER,
                scanned_files=0,
                imported_files=0,
                skipped_files=0,
                failed_files=0,
                activity_entries_count=0,
                sleep_entries_count=0,
                weight_entries_count=0,
            )

        files = self.google_drive_client.list_json_files(settings.folder_id)
        imported_files = 0
        skipped_files = 0
        failed_files = 0
        activity_entries_count = 0
        current_time = now or datetime.now()

        for drive_file in files:
            if self.store.get_health_import_file(user_id, self.PROVIDER, drive_file.file_id) is not None:
                skipped_files += 1
                continue
            try:
                payload = self.google_drive_client.download_file(drive_file.file_id)
                parsed = self.parser.parse(payload, file_name=drive_file.name, file_id=drive_file.file_id)
                for entry in parsed.activity_entries:
                    self.store.add_activity(user_id, entry)
                self.store.create_health_import_file(
                    HealthImportFile(
                        import_id=str(uuid4()),
                        user_id=user_id,
                        provider=self.PROVIDER,
                        external_file_id=drive_file.file_id,
                        file_name=drive_file.name,
                        file_date=parsed.file_date,
                        checksum=drive_file.checksum or _sha256_hex(payload),
                        status=HealthImportStatus.IMPORTED,
                        imported_at=current_time,
                        activity_entries_count=len(parsed.activity_entries),
                        sleep_entries_count=0,
                        weight_entries_count=0,
                        raw_metadata_json=json.dumps(
                            {
                                "summary": parsed.summary,
                                "created_at": drive_file.created_at.isoformat(),
                                "modified_at": drive_file.modified_at.isoformat(),
                                "size_bytes": drive_file.size_bytes,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                )
                imported_files += 1
                activity_entries_count += len(parsed.activity_entries)
            except Exception as exc:
                failed_files += 1
                logger.exception(
                    "Google Drive health import failed user_id=%s file_id=%s name=%s error=%s",
                    user_id,
                    drive_file.file_id,
                    drive_file.name,
                    exc,
                )
                self.store.create_health_import_file(
                    HealthImportFile(
                        import_id=str(uuid4()),
                        user_id=user_id,
                        provider=self.PROVIDER,
                        external_file_id=drive_file.file_id,
                        file_name=drive_file.name,
                        file_date=None,
                        checksum=drive_file.checksum,
                        status=HealthImportStatus.FAILED,
                        imported_at=current_time,
                        raw_metadata_json=json.dumps(
                            {
                                "created_at": drive_file.created_at.isoformat(),
                                "modified_at": drive_file.modified_at.isoformat(),
                                "size_bytes": drive_file.size_bytes,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        error_message=str(exc),
                    )
                )

        return HealthImportResult(
            user_id=user_id,
            provider=self.PROVIDER,
            scanned_files=len(files),
            imported_files=imported_files,
            skipped_files=skipped_files,
            failed_files=failed_files,
            activity_entries_count=activity_entries_count,
            sleep_entries_count=0,
            weight_entries_count=0,
        )


def extract_google_drive_folder_id(folder_input: str) -> str:
    raw = folder_input.strip()
    if not raw:
        raise ValueError("Укажите ссылку на папку Google Drive или folder ID.")
    match = FOLDER_ID_PATTERN.search(raw)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", raw):
        return raw
    raise ValueError("Не удалось извлечь folder ID из ссылки Google Drive.")


def normalize_google_drive_folder_url(folder_input: str, folder_id: str) -> str:
    raw = folder_input.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return "https://drive.google.com/drive/folders/%s" % folder_id


def _parse_google_timestamp(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_health_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
