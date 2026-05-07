from datetime import date
from typing import Iterable, List, Optional, Protocol

from ai_me.domain.decision_log import DecisionLogEntry, DecisionStatus
from ai_me.domain.finance import FinanceMonthlySummary, FinanceTransaction
from ai_me.domain.food import MealDraftStatus, MealPhotoDraft
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    DailyHealthSummary,
    MealEntry,
    SleepEntry,
    WaterEntry,
    WeightEntry,
)
from ai_me.domain.user import AppUser, InviteCode, InviteStatus, UserStatus


class HealthStore(Protocol):
    def close(self) -> None:
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

    def set_health_goals(self, user_id: int, goals: DailyHealthGoals) -> None:
        ...

    def get_health_goals(self, user_id: int, target_date: date) -> DailyHealthGoals:
        ...

    def add_meal(self, user_id: int, entry: MealEntry) -> None:
        ...

    def list_meals(self, user_id: int, target_date: date) -> List[MealEntry]:
        ...

    def create_meal_draft(self, user_id: int, draft: MealPhotoDraft) -> None:
        ...

    def get_meal_draft(self, user_id: int, draft_id: str) -> Optional[MealPhotoDraft]:
        ...

    def list_meal_drafts(self, user_id: int, status: MealDraftStatus) -> List[MealPhotoDraft]:
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
