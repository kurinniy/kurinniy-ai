import json
from datetime import date, datetime
from typing import List, Optional
from uuid import uuid4

from ai_me.domain.decision_log import DecisionLogEntry, DecisionStatus
from ai_me.domain.finance import FinanceImportResult, FinanceMonthlySummary
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
from ai_me.services.food_analysis import DisabledFoodPhotoAnalyzer, FoodPhotoAnalyzer
from ai_me.services.rules import HealthDecisionEngine
from ai_me.services.tbank_import import TBankCSVImporter
from ai_me.storage.base import HealthStore


class HealthService:
    def __init__(
        self,
        store: HealthStore,
        decision_engine: Optional[HealthDecisionEngine] = None,
        food_photo_analyzer: Optional[FoodPhotoAnalyzer] = None,
        tbank_csv_importer: Optional[TBankCSVImporter] = None,
    ) -> None:
        self.store = store
        self.decision_engine = decision_engine or HealthDecisionEngine()
        self.food_photo_analyzer = food_photo_analyzer or DisabledFoodPhotoAnalyzer()
        self.tbank_csv_importer = tbank_csv_importer or TBankCSVImporter()

    def set_goals(self, goals: DailyHealthGoals) -> None:
        self.store.set_health_goals(goals)

    def log_meal(self, entry: MealEntry) -> None:
        self.store.add_meal(entry)

    def create_meal_draft_from_photo(
        self,
        photo_file_id: str,
        photo_unique_id: str,
        image_bytes: bytes,
        mime_type: str,
        occurred_at: Optional[datetime] = None,
        caption: str = "",
    ) -> MealPhotoDraft:
        analyzed = self.food_photo_analyzer.analyze_photo(
            image_bytes=image_bytes,
            mime_type=mime_type,
            caption=caption,
        )
        draft = MealPhotoDraft(
            draft_id=str(uuid4()),
            created_at=datetime.now(),
            occurred_at=occurred_at or datetime.now(),
            title=analyzed.title,
            summary=analyzed.summary,
            calories=analyzed.calories,
            protein_g=analyzed.protein_g,
            fat_g=analyzed.fat_g,
            carbs_g=analyzed.carbs_g,
            confidence=analyzed.confidence,
            photo_file_id=photo_file_id,
            photo_unique_id=photo_unique_id,
            items=analyzed.items,
        )
        self.store.create_meal_draft(draft)
        return draft

    def confirm_meal_draft(self, draft_id: str) -> MealEntry:
        draft = self._require_meal_draft(draft_id, MealDraftStatus.PENDING)
        meal = MealEntry(
            entry_id=str(uuid4()),
            occurred_at=draft.occurred_at,
            title=draft.title,
            calories=draft.calories,
            protein_g=draft.protein_g,
            fat_g=draft.fat_g,
            carbs_g=draft.carbs_g,
            notes=json.dumps(
                {
                    "source": draft.source,
                    "draft_id": draft.draft_id,
                    "summary": draft.summary,
                    "confidence": draft.confidence,
                },
                sort_keys=True,
            ),
        )
        self.store.add_meal(meal)
        self.store.update_meal_draft_status(draft_id, MealDraftStatus.CONFIRMED)
        return meal

    def reject_meal_draft(self, draft_id: str) -> MealPhotoDraft:
        draft = self._require_meal_draft(draft_id, MealDraftStatus.PENDING)
        self.store.update_meal_draft_status(draft_id, MealDraftStatus.REJECTED)
        return draft

    def list_meal_drafts(self, status: MealDraftStatus = MealDraftStatus.PENDING) -> List[MealPhotoDraft]:
        return self.store.list_meal_drafts(status=status)

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

    def list_meals(self, target_date: date) -> List[MealEntry]:
        return self.store.list_meals(target_date)

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

    def import_tbank_csv(self, file_bytes: bytes, source_file_name: str) -> FinanceImportResult:
        transactions = self.tbank_csv_importer.parse(file_bytes=file_bytes, source_file_name=source_file_name)
        imported_rows = self.store.upsert_finance_transactions(transactions)
        occurred_dates = sorted(transaction.occurred_at for transaction in transactions)
        return FinanceImportResult(
            provider=TBankCSVImporter.PROVIDER,
            source_file_name=source_file_name,
            total_rows=len(transactions),
            imported_rows=imported_rows,
            skipped_rows=len(transactions) - imported_rows,
            first_operation_at=occurred_dates[0] if occurred_dates else None,
            last_operation_at=occurred_dates[-1] if occurred_dates else None,
        )

    def get_finance_monthly_summary(self, month_start: date) -> FinanceMonthlySummary:
        return self.store.build_finance_monthly_summary(month_start)

    def _require_meal_draft(self, draft_id: str, expected_status: MealDraftStatus) -> MealPhotoDraft:
        draft = self.store.get_meal_draft(draft_id)
        if draft is None:
            raise ValueError("Черновик приема пищи не найден: %s" % draft_id)
        if draft.status != expected_status:
            raise ValueError("Черновик приема пищи %s имеет статус %s" % (draft_id, draft.status.value))
        return draft
