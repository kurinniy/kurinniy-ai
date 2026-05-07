import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Dict, FrozenSet, List, Optional
from uuid import uuid4

from ai_me.domain.digest import (
    DailyFoodDigest,
    DigestMealSnapshot,
    DigestRun,
    DigestStatus,
    DigestTrendWindow,
    DigestType,
    UserDigestSettings,
    WeeklyDigestHighlight,
    WeeklyFoodDigest,
)
from ai_me.domain.decision_log import DecisionLogEntry, DecisionStatus
from ai_me.domain.finance import FinanceImportResult, FinanceMonthlySummary
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
        default_timezone_name: str = "Europe/Moscow",
    ) -> None:
        self.store = store
        self.decision_engine = decision_engine or HealthDecisionEngine()
        self.food_photo_analyzer = food_photo_analyzer or DisabledFoodPhotoAnalyzer()
        self.tbank_csv_importer = tbank_csv_importer or TBankCSVImporter()
        self.admin_telegram_user_ids = admin_telegram_user_ids
        self.default_timezone_name = default_timezone_name

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

    def list_users(self, status: Optional[UserStatus] = None) -> List[AppUser]:
        return self.store.list_users(status=status)

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

    def get_digest_settings(self, user_id: int) -> UserDigestSettings:
        current = self.store.get_user_digest_settings(user_id)
        if current is not None:
            return current
        return self.store.upsert_user_digest_settings(
            UserDigestSettings(
                user_id=user_id,
                timezone_name=self.default_timezone_name,
            )
        )

    def set_digest_enabled(self, user_id: int, enabled: bool) -> UserDigestSettings:
        current = self.get_digest_settings(user_id)
        return self.store.upsert_user_digest_settings(
            UserDigestSettings(
                user_id=current.user_id,
                timezone_name=current.timezone_name,
                daily_digest_enabled=enabled,
                daily_digest_time=current.daily_digest_time,
                weekly_digest_enabled=enabled,
                weekly_digest_time=current.weekly_digest_time,
                weekly_digest_weekday=current.weekly_digest_weekday,
            )
        )

    def create_digest_run(
        self,
        user_id: int,
        digest_type: DigestType,
        digest_date: date,
        status: DigestStatus = DigestStatus.PENDING,
        now: Optional[datetime] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> DigestRun:
        return self.store.create_digest_run(
            DigestRun(
                run_id=str(uuid4()),
                user_id=user_id,
                digest_type=digest_type,
                digest_date=digest_date,
                status=status,
                created_at=now or datetime.now(),
                scheduled_for=scheduled_for,
            )
        )

    def list_digest_runs(
        self,
        user_id: int,
        digest_type: Optional[DigestType] = None,
        status: Optional[DigestStatus] = None,
    ) -> List[DigestRun]:
        return self.store.list_digest_runs(user_id, digest_type=digest_type, status=status)

    def update_digest_run(
        self,
        run_id: str,
        status: DigestStatus,
        sent_at: Optional[datetime] = None,
        error_message: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        self.store.update_digest_run(
            run_id,
            status=status,
            sent_at=sent_at,
            error_message=error_message,
            payload=payload,
        )

    def build_daily_food_digest(self, user_id: int, digest_date: date) -> Optional[DailyFoodDigest]:
        meals = self._list_photo_meals_for_date(user_id, digest_date)
        if not meals:
            return None

        trend_windows = [self._build_trend_window(user_id, digest_date, window_days) for window_days in (7, 14, 30)]
        total_calories = sum(meal.calories for meal in meals)
        total_protein_g = round(sum(meal.protein_g for meal in meals), 2)
        total_fat_g = round(sum(meal.fat_g for meal in meals), 2)
        total_carbs_g = round(sum(meal.carbs_g for meal in meals), 2)
        commentary = self._build_daily_digest_commentary(
            digest_date=digest_date,
            meals=meals,
            total_calories=total_calories,
            total_protein_g=total_protein_g,
            total_fat_g=total_fat_g,
            total_carbs_g=total_carbs_g,
            trend_windows=trend_windows,
        )
        return DailyFoodDigest(
            user_id=user_id,
            digest_date=digest_date,
            meals=meals,
            total_calories=total_calories,
            total_protein_g=total_protein_g,
            total_fat_g=total_fat_g,
            total_carbs_g=total_carbs_g,
            trend_windows=trend_windows,
            commentary=commentary,
        )

    def build_weekly_food_digest(self, user_id: int, week_start: date) -> Optional[WeeklyFoodDigest]:
        highlights: List[WeeklyDigestHighlight] = []
        total_meals = 0
        total_calories = 0
        baseline_meals = self._collect_photo_meals(user_id, week_start - timedelta(days=30), week_start - timedelta(days=1))
        for offset in range(7):
            current_date = week_start + timedelta(days=offset)
            day_meals = self._list_photo_meals_for_date(user_id, current_date)
            total_meals += len(day_meals)
            total_calories += sum(meal.calories for meal in day_meals)
            highlights.append(self._pick_weekly_highlight(current_date, day_meals, baseline_meals))

        if total_meals == 0:
            return None

        commentary = self._build_weekly_digest_commentary(
            week_start=week_start,
            total_meals=total_meals,
            total_calories=total_calories,
            highlights=highlights,
        )
        return WeeklyFoodDigest(
            user_id=user_id,
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
            highlights=highlights,
            total_meals=total_meals,
            total_calories=total_calories,
            commentary=commentary,
        )

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
        self.store.create_meal_media(
            MealMedia(
                media_id=str(uuid4()),
                user_id=user_id,
                draft_id=draft.draft_id,
                occurred_at=draft.occurred_at,
                created_at=draft.created_at,
                mime_type=mime_type,
                telegram_file_id=photo_file_id,
                telegram_unique_id=photo_unique_id,
                byte_size=len(image_bytes),
                sha256=hashlib.sha256(image_bytes).hexdigest(),
                image_bytes=image_bytes,
            )
        )
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
        self.store.attach_meal_media_to_meal(user_id, draft_id, meal.entry_id)
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

    def list_meal_media(self, user_id: int, target_date: Optional[date] = None) -> List[MealMedia]:
        return self.store.list_meal_media(user_id, target_date=target_date)

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

    def _list_photo_meals_for_date(self, user_id: int, target_date: date) -> List[DigestMealSnapshot]:
        meals = self.store.list_meals(user_id, target_date)
        meal_media = self.store.list_meal_media(user_id, target_date=target_date)
        media_by_meal_entry_id: Dict[str, List[MealMedia]] = {}
        for media in meal_media:
            if not media.meal_entry_id:
                continue
            media_by_meal_entry_id.setdefault(media.meal_entry_id, []).append(media)

        snapshots = []
        for meal in meals:
            media_items = media_by_meal_entry_id.get(meal.entry_id, [])
            if not media_items:
                continue
            snapshots.append(
                DigestMealSnapshot(
                    meal_entry_id=meal.entry_id,
                    occurred_at=meal.occurred_at,
                    title=meal.title,
                    calories=meal.calories,
                    protein_g=meal.protein_g,
                    fat_g=meal.fat_g,
                    carbs_g=meal.carbs_g,
                    media_items=media_items,
                )
            )
        return snapshots

    def _collect_photo_meals(self, user_id: int, start_date: date, end_date: date) -> List[DigestMealSnapshot]:
        if end_date < start_date:
            return []
        collected = []
        cursor = start_date
        while cursor <= end_date:
            collected.extend(self._list_photo_meals_for_date(user_id, cursor))
            cursor += timedelta(days=1)
        return collected

    def _build_trend_window(self, user_id: int, digest_date: date, window_days: int) -> DigestTrendWindow:
        start_date = digest_date - timedelta(days=window_days)
        end_date = digest_date - timedelta(days=1)
        daily_meals_count: List[int] = []
        daily_calories: List[int] = []
        daily_protein: List[float] = []
        daily_fat: List[float] = []
        daily_carbs: List[float] = []
        cursor = start_date
        while cursor <= end_date:
            meals = self._list_photo_meals_for_date(user_id, cursor)
            if meals:
                daily_meals_count.append(len(meals))
                daily_calories.append(sum(meal.calories for meal in meals))
                daily_protein.append(sum(meal.protein_g for meal in meals))
                daily_fat.append(sum(meal.fat_g for meal in meals))
                daily_carbs.append(sum(meal.carbs_g for meal in meals))
            cursor += timedelta(days=1)

        if not daily_meals_count:
            return DigestTrendWindow(
                days=window_days,
                average_calories=0.0,
                average_protein_g=0.0,
                average_fat_g=0.0,
                average_carbs_g=0.0,
                average_meals_count=0.0,
                days_with_meals=0,
            )
        days_with_meals = len(daily_meals_count)
        return DigestTrendWindow(
            days=window_days,
            average_calories=round(sum(daily_calories) / days_with_meals, 2),
            average_protein_g=round(sum(daily_protein) / days_with_meals, 2),
            average_fat_g=round(sum(daily_fat) / days_with_meals, 2),
            average_carbs_g=round(sum(daily_carbs) / days_with_meals, 2),
            average_meals_count=round(sum(daily_meals_count) / days_with_meals, 2),
            days_with_meals=days_with_meals,
        )

    @staticmethod
    def _percent_delta(current: float, baseline: float) -> Optional[float]:
        if baseline <= 0:
            return None
        return round(((current - baseline) / baseline) * 100, 1)

    def _build_daily_digest_commentary(
        self,
        digest_date: date,
        meals: List[DigestMealSnapshot],
        total_calories: int,
        total_protein_g: float,
        total_fat_g: float,
        total_carbs_g: float,
        trend_windows: List[DigestTrendWindow],
    ) -> str:
        lines = [
            "За %s подтверждено %s блюда(блюд) по фото." % (digest_date.isoformat(), len(meals)),
            "Итог: %s ккал, Б %.1f г, Ж %.1f г, У %.1f г." % (
                total_calories,
                total_protein_g,
                total_fat_g,
                total_carbs_g,
            ),
        ]
        for trend in trend_windows:
            if trend.days_with_meals == 0:
                lines.append("Сравнение с %s днями пока недоступно: недостаточно подтвержденных приемов пищи." % trend.days)
                continue
            calories_delta = self._percent_delta(total_calories, trend.average_calories)
            protein_delta = self._percent_delta(total_protein_g, trend.average_protein_g)
            calories_text = (
                "на %.1f%% %s среднего"
                % (abs(calories_delta), "выше" if calories_delta >= 0 else "ниже")
                if calories_delta is not None
                else "без сравнения по калориям"
            )
            protein_text = (
                "Белок на %.1f%% %s среднего"
                % (abs(protein_delta), "выше" if protein_delta >= 0 else "ниже")
                if protein_delta is not None
                else "Белок без сравнения"
            )
            lines.append(
                "Относительно %s дней: калорийность %s; %s."
                % (trend.days, calories_text, protein_text)
            )
        return "\n".join(lines)

    def _pick_weekly_highlight(
        self,
        digest_date: date,
        meals: List[DigestMealSnapshot],
        baseline_meals: List[DigestMealSnapshot],
    ) -> WeeklyDigestHighlight:
        if not meals:
            return WeeklyDigestHighlight(digest_date=digest_date, meal=None, reason="В этот день нет подтвержденных блюд.")

        if not baseline_meals:
            picked = max(meals, key=lambda item: item.calories)
            return WeeklyDigestHighlight(
                digest_date=digest_date,
                meal=picked,
                score=float(picked.calories),
                reason="Выбрано как самое калорийное блюдо дня при отсутствии исторической базы.",
            )

        avg_calories = sum(item.calories for item in baseline_meals) / len(baseline_meals)
        avg_protein = sum(item.protein_g for item in baseline_meals) / len(baseline_meals)
        avg_fat = sum(item.fat_g for item in baseline_meals) / len(baseline_meals)
        avg_carbs = sum(item.carbs_g for item in baseline_meals) / len(baseline_meals)

        def score(meal: DigestMealSnapshot) -> float:
            return (
                abs(meal.calories - avg_calories) / max(avg_calories, 1.0)
                + abs(meal.protein_g - avg_protein) / max(avg_protein, 1.0)
                + abs(meal.fat_g - avg_fat) / max(avg_fat, 1.0)
                + abs(meal.carbs_g - avg_carbs) / max(avg_carbs, 1.0)
            )

        picked = max(meals, key=score)
        metric_deltas = {
            "калориям": abs(picked.calories - avg_calories) / max(avg_calories, 1.0),
            "белку": abs(picked.protein_g - avg_protein) / max(avg_protein, 1.0),
            "жирам": abs(picked.fat_g - avg_fat) / max(avg_fat, 1.0),
            "углеводам": abs(picked.carbs_g - avg_carbs) / max(avg_carbs, 1.0),
        }
        dominant_metric = max(metric_deltas, key=metric_deltas.get)
        return WeeklyDigestHighlight(
            digest_date=digest_date,
            meal=picked,
            score=round(score(picked), 3),
            reason="Выбрано как блюдо с наибольшим отклонением от личной базы по %s." % dominant_metric,
        )

    def _build_weekly_digest_commentary(
        self,
        week_start: date,
        total_meals: int,
        total_calories: int,
        highlights: List[WeeklyDigestHighlight],
    ) -> str:
        days_with_meals = len([highlight for highlight in highlights if highlight.meal is not None])
        strongest = max(
            (highlight for highlight in highlights if highlight.meal is not None),
            key=lambda item: item.score,
            default=None,
        )
        lines = [
            "За неделю %s — %s подтверждено %s блюда(блюд) по фото на %s ккал."
            % (week_start.isoformat(), (week_start + timedelta(days=6)).isoformat(), total_meals, total_calories),
            "Дни с подтвержденными фото-блюдами: %s из 7." % days_with_meals,
        ]
        if strongest is not None and strongest.meal is not None:
            lines.append(
                "Самое выделяющееся блюдо недели: %s (%s). %s"
                % (
                    strongest.meal.title,
                    strongest.digest_date.strftime("%d.%m"),
                    strongest.reason,
                )
            )
        return "\n".join(lines)

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
