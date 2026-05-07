import json
from datetime import date, datetime, timedelta
from typing import FrozenSet, List, Optional
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
from ai_me.domain.user import AppUser, InviteCode, InviteStatus, UserStatus
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
        admin_telegram_user_ids: FrozenSet[int] = frozenset(),
    ) -> None:
        self.store = store
        self.decision_engine = decision_engine or HealthDecisionEngine()
        self.food_photo_analyzer = food_photo_analyzer or DisabledFoodPhotoAnalyzer()
        self.tbank_csv_importer = tbank_csv_importer or TBankCSVImporter()
        self.admin_telegram_user_ids = admin_telegram_user_ids

    def sync_user(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: str = "",
        first_name: str = "",
    ) -> Optional[AppUser]:
        user = self.store.get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            return None
        should_be_admin = telegram_user_id in self.admin_telegram_user_ids
        if user.is_admin != should_be_admin:
            return self.store.create_user(
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                username=username,
                first_name=first_name,
                status=user.status,
                is_admin=should_be_admin,
            )
        if user.chat_id != chat_id or user.username != username or user.first_name != first_name:
            return self.store.update_user_profile(user, chat_id=chat_id, username=username, first_name=first_name)
        return user

    def register_user_with_invite(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: str,
        first_name: str,
        invite_code: str,
        now: Optional[datetime] = None,
    ) -> AppUser:
        current_time = now or datetime.now()
        existing = self.sync_user(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
        )
        if existing is not None:
            if existing.status == UserStatus.BLOCKED:
                raise ValueError("Ваш доступ к боту заблокирован.")
            return existing

        invite = self._require_active_invite(invite_code, now=current_time)
        user = self.store.create_user(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            status=UserStatus.ACTIVE,
            is_admin=telegram_user_id in self.admin_telegram_user_ids,
        )
        next_used_count = invite.used_count + 1
        next_status = InviteStatus.EXHAUSTED if next_used_count >= invite.max_uses else InviteStatus.ACTIVE
        self.store.increment_invite_usage(invite.code, status=next_status)
        return user

    def create_invite(
        self,
        created_by_user_id: int,
        days_valid: int = 7,
        max_uses: int = 1,
        now: Optional[datetime] = None,
    ) -> InviteCode:
        if days_valid <= 0:
            raise ValueError("Срок действия инвайта должен быть больше нуля дней.")
        if max_uses <= 0:
            raise ValueError("Количество использований инвайта должно быть больше нуля.")
        created_at = now or datetime.now()
        invite = InviteCode(
            code=uuid4().hex[:12].upper(),
            created_by_user_id=created_by_user_id,
            created_at=created_at,
            expires_at=created_at + timedelta(days=days_valid),
            max_uses=max_uses,
            used_count=0,
            status=InviteStatus.ACTIVE,
        )
        return self.store.create_invite(invite)

    def list_invites(self, status: Optional[InviteStatus] = None) -> List[InviteCode]:
        return self.store.list_invites(status=status)

    def revoke_invite(self, code: str) -> None:
        invite = self.store.get_invite(code)
        if invite is None:
            raise ValueError("Инвайт не найден: %s" % code)
        self.store.update_invite_status(code, InviteStatus.REVOKED)

    def set_goals(self, user_id: int, goals: DailyHealthGoals) -> None:
        self.store.set_health_goals(user_id, goals)

    def log_meal(self, user_id: int, entry: MealEntry) -> None:
        self.store.add_meal(user_id, entry)

    def create_meal_draft_from_photo(
        self,
        user_id: int,
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
        self.store.create_meal_draft(user_id, draft)
        return draft

    def confirm_meal_draft(self, user_id: int, draft_id: str) -> MealEntry:
        draft = self._require_meal_draft(user_id, draft_id, MealDraftStatus.PENDING)
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
        self.store.add_meal(user_id, meal)
        self.store.update_meal_draft_status(user_id, draft_id, MealDraftStatus.CONFIRMED)
        return meal

    def reject_meal_draft(self, user_id: int, draft_id: str) -> MealPhotoDraft:
        draft = self._require_meal_draft(user_id, draft_id, MealDraftStatus.PENDING)
        self.store.update_meal_draft_status(user_id, draft_id, MealDraftStatus.REJECTED)
        return draft

    def list_meal_drafts(
        self,
        user_id: int,
        status: MealDraftStatus = MealDraftStatus.PENDING,
    ) -> List[MealPhotoDraft]:
        return self.store.list_meal_drafts(user_id, status=status)

    def log_water(self, user_id: int, entry: WaterEntry) -> None:
        self.store.add_water(user_id, entry)

    def log_sleep(self, user_id: int, entry: SleepEntry) -> None:
        self.store.add_sleep(user_id, entry)

    def log_weight(self, user_id: int, entry: WeightEntry) -> None:
        self.store.add_weight(user_id, entry)

    def log_activity(self, user_id: int, entry: ActivityEntry) -> None:
        self.store.add_activity(user_id, entry)

    def get_daily_summary(self, user_id: int, target_date: date) -> DailyHealthSummary:
        return self.store.build_health_summary(user_id, target_date)

    def list_meals(self, user_id: int, target_date: date) -> List[MealEntry]:
        return self.store.list_meals(user_id, target_date)

    def evaluate_day(self, user_id: int, target_date: date, now: Optional[datetime] = None) -> List[DecisionLogEntry]:
        current_time = now or datetime.now()
        summary = self.get_daily_summary(user_id, target_date)
        decisions = self.decision_engine.evaluate(summary=summary, now=current_time)
        return self.store.upsert_decisions(user_id, decisions)

    def list_decisions(
        self,
        user_id: int,
        status: Optional[DecisionStatus] = None,
        target_date: Optional[date] = None,
    ) -> List[DecisionLogEntry]:
        return self.store.list_decisions(user_id, status=status, context_date=target_date)

    def update_decision_status(self, user_id: int, decision_id: str, status: DecisionStatus) -> None:
        self.store.update_decision_status(user_id=user_id, decision_id=decision_id, status=status)

    def import_tbank_csv(self, user_id: int, file_bytes: bytes, source_file_name: str) -> FinanceImportResult:
        transactions = self.tbank_csv_importer.parse(file_bytes=file_bytes, source_file_name=source_file_name)
        imported_rows = self.store.upsert_finance_transactions(user_id, transactions)
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

    def get_finance_monthly_summary(self, user_id: int, month_start: date) -> FinanceMonthlySummary:
        return self.store.build_finance_monthly_summary(user_id, month_start)

    def _require_meal_draft(self, user_id: int, draft_id: str, expected_status: MealDraftStatus) -> MealPhotoDraft:
        draft = self.store.get_meal_draft(user_id, draft_id)
        if draft is None:
            raise ValueError("Черновик приема пищи не найден: %s" % draft_id)
        if draft.status != expected_status:
            raise ValueError("Черновик приема пищи %s имеет статус %s" % (draft_id, draft.status.value))
        return draft

    def _require_active_invite(self, code: str, now: datetime) -> InviteCode:
        invite = self.store.get_invite(code)
        if invite is None:
            raise ValueError("Инвайт не найден или недействителен.")
        if invite.status == InviteStatus.REVOKED:
            raise ValueError("Инвайт отозван.")
        if invite.expires_at is not None and invite.expires_at < now:
            self.store.update_invite_status(invite.code, InviteStatus.EXPIRED)
            raise ValueError("Срок действия инвайта истек.")
        if invite.used_count >= invite.max_uses or invite.status == InviteStatus.EXHAUSTED:
            self.store.update_invite_status(invite.code, InviteStatus.EXHAUSTED)
            raise ValueError("Инвайт уже использован.")
        return invite
