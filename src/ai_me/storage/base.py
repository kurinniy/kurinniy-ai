from datetime import date, datetime
from typing import Iterable, List, Optional, Protocol

from ai_me.domain.digest import DigestRun, DigestStatus, DigestType, UserDigestSettings
from ai_me.domain.decision_log import DecisionLogEntry, DecisionStatus
from ai_me.domain.finance import FinanceMonthlySummary, FinanceTransaction
from ai_me.domain.food import MealDraftStatus, MealMedia, MealPhotoDraft
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    DailyHealthSummary,
    MealEntry,
    SleepEntry,
    WaterEntry,
    WeightEntry,
)
from ai_me.domain.health_import import HealthImportFile, HealthImportProvider, UserGoogleDriveSettings
from ai_me.domain.user import AppUser, InviteCode, InviteStatus, UserStatus


class HealthStore(Protocol):
    def close(self) -> None:
        ...

    def list_users(self, status: Optional[UserStatus] = None) -> List[AppUser]:
        ...

    def get_user_by_telegram_user_id(self, telegram_user_id: int) -> Optional[AppUser]:
        ...

    def create_user(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: str,
        first_name: str,
        status: UserStatus,
        is_admin: bool,
    ) -> AppUser:
        ...

    def update_user_profile(self, user: AppUser, chat_id: int, username: str, first_name: str) -> AppUser:
        ...

    def update_user_admin_mode(self, user_id: int, enabled: bool) -> AppUser:
        ...

    def get_user_google_drive_settings(self, user_id: int) -> Optional[UserGoogleDriveSettings]:
        ...

    def upsert_user_google_drive_settings(self, settings: UserGoogleDriveSettings) -> UserGoogleDriveSettings:
        ...

    def list_users_with_google_drive_enabled(self) -> List[AppUser]:
        ...

    def create_health_import_file(self, imported_file: HealthImportFile) -> HealthImportFile:
        ...

    def get_health_import_file(
        self,
        user_id: int,
        provider: HealthImportProvider,
        external_file_id: str,
    ) -> Optional[HealthImportFile]:
        ...

    def list_health_import_files(
        self,
        user_id: int,
        provider: Optional[HealthImportProvider] = None,
    ) -> List[HealthImportFile]:
        ...

    def create_invite(self, invite: InviteCode) -> InviteCode:
        ...

    def get_invite(self, code: str) -> Optional[InviteCode]:
        ...

    def list_invites(self, status: Optional[InviteStatus] = None) -> List[InviteCode]:
        ...

    def increment_invite_usage(self, code: str, status: InviteStatus) -> None:
        ...

    def update_invite_status(self, code: str, status: InviteStatus) -> None:
        ...

    def get_user_digest_settings(self, user_id: int) -> Optional[UserDigestSettings]:
        ...

    def upsert_user_digest_settings(self, settings: UserDigestSettings) -> UserDigestSettings:
        ...

    def create_digest_run(self, run: DigestRun) -> DigestRun:
        ...

    def list_digest_runs(
        self,
        user_id: int,
        digest_type: Optional[DigestType] = None,
        status: Optional[DigestStatus] = None,
    ) -> List[DigestRun]:
        ...

    def update_digest_run(
        self,
        run_id: str,
        status: DigestStatus,
        sent_at: Optional[datetime] = None,
        error_message: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        ...

    def set_health_goals(self, user_id: int, goals: DailyHealthGoals) -> None:
        ...

    def get_health_goals(self, user_id: int, target_date: date) -> DailyHealthGoals:
        ...

    def add_meal(self, user_id: int, entry: MealEntry) -> None:
        ...

    def get_meal(self, user_id: int, entry_id: str) -> Optional[MealEntry]:
        ...

    def list_meals(self, user_id: int, target_date: date) -> List[MealEntry]:
        ...

    def list_meals_in_range(self, user_id: int, start_date: date, end_date: date) -> List[MealEntry]:
        ...

    def update_meal(self, user_id: int, entry: MealEntry) -> None:
        ...

    def delete_meal(self, user_id: int, entry_id: str) -> None:
        ...

    def create_meal_draft(self, user_id: int, draft: MealPhotoDraft) -> None:
        ...

    def create_meal_media(self, media: MealMedia) -> None:
        ...

    def list_meal_media(self, user_id: int, target_date: Optional[date] = None) -> List[MealMedia]:
        ...

    def list_meal_media_in_range(
        self,
        user_id: int,
        start_date: date,
        end_date: date,
        include_image_bytes: bool = True,
    ) -> List[MealMedia]:
        ...

    def list_meal_media_by_ids(self, user_id: int, media_ids: List[str]) -> List[MealMedia]:
        ...

    def attach_meal_media_to_meal(self, user_id: int, draft_id: str, meal_entry_id: str) -> None:
        ...

    def get_meal_draft(self, user_id: int, draft_id: str) -> Optional[MealPhotoDraft]:
        ...

    def list_meal_drafts(self, user_id: int, status: MealDraftStatus) -> List[MealPhotoDraft]:
        ...

    def update_meal_draft(self, user_id: int, draft: MealPhotoDraft) -> None:
        ...

    def update_meal_draft_status(self, user_id: int, draft_id: str, status: MealDraftStatus) -> None:
        ...

    def add_water(self, user_id: int, entry: WaterEntry) -> None:
        ...

    def add_sleep(self, user_id: int, entry: SleepEntry) -> None:
        ...

    def add_weight(self, user_id: int, entry: WeightEntry) -> None:
        ...

    def add_activity(self, user_id: int, entry: ActivityEntry) -> None:
        ...

    def delete_activity(self, user_id: int, entry_id: str) -> None:
        ...

    def get_daily_water_total(self, user_id: int, target_date: date) -> int:
        ...

    def list_daily_step_totals(self, user_id: int, date_from: date, date_to: date) -> List[tuple[date, int]]:
        ...

    def list_activity_entries(self, user_id: int, date_from: date, date_to: date) -> List[ActivityEntry]:
        ...

    def build_health_summary(self, user_id: int, target_date: date) -> DailyHealthSummary:
        ...

    def upsert_decisions(self, user_id: int, decisions: Iterable[DecisionLogEntry]) -> List[DecisionLogEntry]:
        ...

    def list_decisions(
        self,
        user_id: int,
        status: Optional[DecisionStatus] = None,
        context_date: Optional[date] = None,
    ) -> List[DecisionLogEntry]:
        ...

    def update_decision_status(self, user_id: int, decision_id: str, status: DecisionStatus) -> None:
        ...

    def upsert_finance_transactions(self, user_id: int, transactions: Iterable[FinanceTransaction]) -> int:
        ...

    def build_finance_monthly_summary(self, user_id: int, month_start: date) -> FinanceMonthlySummary:
        ...
