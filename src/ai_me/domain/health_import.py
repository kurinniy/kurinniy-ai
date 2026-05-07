from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional


class HealthImportProvider(str, Enum):
    GOOGLE_DRIVE = "google_drive"


class HealthImportStatus(str, Enum):
    PENDING = "pending"
    IMPORTED = "imported"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class UserGoogleDriveSettings:
    user_id: int
    folder_id: str
    folder_url: str
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class HealthImportFile:
    import_id: str
    user_id: int
    provider: HealthImportProvider
    external_file_id: str
    file_name: str
    file_date: Optional[date]
    checksum: str
    status: HealthImportStatus
    imported_at: datetime
    activity_entries_count: int = 0
    sleep_entries_count: int = 0
    weight_entries_count: int = 0
    raw_metadata_json: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class HealthImportResult:
    user_id: int
    provider: HealthImportProvider
    scanned_files: int
    imported_files: int
    skipped_files: int
    failed_files: int
    activity_entries_count: int
    sleep_entries_count: int
    weight_entries_count: int

