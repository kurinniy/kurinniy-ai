from datetime import date, datetime
from typing import List, Optional

from ai_me.domain.decision_log import DecisionLogEntry, DecisionStatus
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    DailyHealthSummary,
    MealEntry,
    SleepEntry,
    WaterEntry,
    WeightEntry,
)
from ai_me.services.rules import HealthDecisionEngine
from ai_me.storage.base import HealthStore


class HealthService:
    def __init__(
        self,
        store: HealthStore,
        decision_engine: Optional[HealthDecisionEngine] = None,
    ) -> None:
        self.store = store
        self.decision_engine = decision_engine or HealthDecisionEngine()

    def set_goals(self, goals: DailyHealthGoals) -> None:
        self.store.set_health_goals(goals)

    def log_meal(self, entry: MealEntry) -> None:
        self.store.add_meal(entry)

    def log_water(self, entry: WaterEntry) -> None:
        self.store.add_water(entry)

    def log_sleep(self, entry: SleepEntry) -> None:
        self.store.add_sleep(entry)

    def log_weight(self, entry: WeightEntry) -> None:
        self.store.add_weight(entry)

    def log_activity(self, entry: ActivityEntry) -> None:
        self.store.add_activity(entry)

    def get_daily_summary(self, target_date: date) -> DailyHealthSummary:
        return self.store.build_health_summary(target_date)

    def evaluate_day(self, target_date: date, now: Optional[datetime] = None) -> List[DecisionLogEntry]:
        current_time = now or datetime.now()
        summary = self.get_daily_summary(target_date)
        decisions = self.decision_engine.evaluate(summary=summary, now=current_time)
        return self.store.upsert_decisions(decisions)

    def list_decisions(
        self,
        status: Optional[DecisionStatus] = None,
        target_date: Optional[date] = None,
    ) -> List[DecisionLogEntry]:
        return self.store.list_decisions(status=status, context_date=target_date)

    def update_decision_status(self, decision_id: str, status: DecisionStatus) -> None:
        self.store.update_decision_status(decision_id=decision_id, status=status)
