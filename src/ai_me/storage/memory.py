from datetime import date
from typing import Dict, Iterable, List, Optional

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


class InMemoryStore:
    def __init__(self) -> None:
        self._goals: Dict[date, DailyHealthGoals] = {}
        self._meals: List[MealEntry] = []
        self._water_entries: List[WaterEntry] = []
        self._sleep_entries: List[SleepEntry] = []
        self._weight_entries: List[WeightEntry] = []
        self._activity_entries: List[ActivityEntry] = []
        self._decisions_by_id: Dict[str, DecisionLogEntry] = {}
        self._decision_ids_by_key: Dict[str, str] = {}

    def close(self) -> None:
        return None

    def set_health_goals(self, goals: DailyHealthGoals) -> None:
        self._goals[goals.target_date] = goals

    def get_health_goals(self, target_date: date) -> DailyHealthGoals:
        return self._goals.get(target_date, DailyHealthGoals(target_date=target_date))

    def add_meal(self, entry: MealEntry) -> None:
        self._meals.append(entry)

    def add_water(self, entry: WaterEntry) -> None:
        self._water_entries.append(entry)

    def add_sleep(self, entry: SleepEntry) -> None:
        self._sleep_entries.append(entry)

    def add_weight(self, entry: WeightEntry) -> None:
        self._weight_entries.append(entry)

    def add_activity(self, entry: ActivityEntry) -> None:
        self._activity_entries.append(entry)

    def build_health_summary(self, target_date: date) -> DailyHealthSummary:
        meals = [entry for entry in self._meals if entry.occurred_at.date() == target_date]
        water_entries = [entry for entry in self._water_entries if entry.occurred_at.date() == target_date]
        sleep_entries = [entry for entry in self._sleep_entries if entry.end_at.date() == target_date]
        activity_entries = [
            entry for entry in self._activity_entries if entry.occurred_at.date() == target_date
        ]
        weight_entries = [entry for entry in self._weight_entries if entry.occurred_at.date() == target_date]

        latest_weight = sorted(weight_entries, key=lambda item: item.occurred_at)[-1].weight_kg if weight_entries else None

        return DailyHealthSummary(
            target_date=target_date,
            meals_count=len(meals),
            calories=sum(entry.calories for entry in meals),
            protein_g=round(sum(entry.protein_g for entry in meals), 2),
            water_ml=sum(entry.amount_ml for entry in water_entries),
            sleep_hours=round(sum(entry.duration_hours for entry in sleep_entries), 2),
            steps=sum(entry.steps for entry in activity_entries),
            activity_minutes=sum(entry.duration_minutes for entry in activity_entries),
            latest_weight_kg=latest_weight,
            goals=self.get_health_goals(target_date),
        )

    def upsert_decisions(self, decisions: Iterable[DecisionLogEntry]) -> List[DecisionLogEntry]:
        inserted = []
        for decision in decisions:
            if decision.decision_key in self._decision_ids_by_key:
                continue
            self._decisions_by_id[decision.decision_id] = decision
            self._decision_ids_by_key[decision.decision_key] = decision.decision_id
            inserted.append(decision)
        return inserted

    def list_decisions(
        self,
        status: Optional[DecisionStatus] = None,
        context_date: Optional[date] = None,
    ) -> List[DecisionLogEntry]:
        decisions = list(self._decisions_by_id.values())
        if status is not None:
            decisions = [item for item in decisions if item.status == status]
        if context_date is not None:
            decisions = [item for item in decisions if item.context_date == context_date]
        return sorted(decisions, key=lambda item: item.created_at)

    def update_decision_status(self, decision_id: str, status: DecisionStatus) -> None:
        current = self._decisions_by_id[decision_id]
        self._decisions_by_id[decision_id] = DecisionLogEntry(
            decision_id=current.decision_id,
            decision_key=current.decision_key,
            created_at=current.created_at,
            agent=current.agent,
            kind=current.kind,
            title=current.title,
            rationale=current.rationale,
            context_date=current.context_date,
            status=status,
            payload=current.payload,
        )
