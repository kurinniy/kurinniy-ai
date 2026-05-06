from datetime import date
from typing import Iterable, List, Optional, Protocol

from ai_me.domain.decision_log import DecisionLogEntry, DecisionStatus
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


class HealthStore(Protocol):
    def close(self) -> None:
        ...

    def set_health_goals(self, goals: DailyHealthGoals) -> None:
        ...

    def get_health_goals(self, target_date: date) -> DailyHealthGoals:
        ...

    def add_meal(self, entry: MealEntry) -> None:
        ...

    def list_meals(self, target_date: date) -> List[MealEntry]:
        ...

    def create_meal_draft(self, draft: MealPhotoDraft) -> None:
        ...

    def get_meal_draft(self, draft_id: str) -> Optional[MealPhotoDraft]:
        ...

    def list_meal_drafts(self, status: MealDraftStatus) -> List[MealPhotoDraft]:
        ...

    def update_meal_draft_status(self, draft_id: str, status: MealDraftStatus) -> None:
        ...

    def add_water(self, entry: WaterEntry) -> None:
        ...

    def add_sleep(self, entry: SleepEntry) -> None:
        ...

    def add_weight(self, entry: WeightEntry) -> None:
        ...

    def add_activity(self, entry: ActivityEntry) -> None:
        ...

    def build_health_summary(self, target_date: date) -> DailyHealthSummary:
        ...

    def upsert_decisions(self, decisions: Iterable[DecisionLogEntry]) -> List[DecisionLogEntry]:
        ...

    def list_decisions(
        self,
        status: Optional[DecisionStatus] = None,
        context_date: Optional[date] = None,
    ) -> List[DecisionLogEntry]:
        ...

    def update_decision_status(self, decision_id: str, status: DecisionStatus) -> None:
        ...
