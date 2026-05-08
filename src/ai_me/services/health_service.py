import hashlib
import json
import math
from datetime import date, datetime, timedelta
from typing import Dict, FrozenSet, List, Optional
from uuid import uuid4

from ai_me.domain.digest import (
    DailyFoodDigest,
    DailyDigestCommentaryData,
    DailyDigestFlags,
    DailyDigestMacroBalance,
    DailyDigestMealPattern,
    DailyDigestStability,
    DigestComparison,
    DigestMealFeature,
    DigestMealSnapshot,
    DigestRun,
    DigestStreak,
    DigestStatus,
    DigestTrendWindow,
    DigestType,
    UserDigestSettings,
    WeeklyDigestCommentaryData,
    WeeklyDigestConsistency,
    WeeklyDigestDayMetric,
    WeeklyDigestFlags,
    WeeklyDigestHighlight,
    WeeklyDigestHighlightSummary,
    WeeklyDigestPatterns,
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
    StepProgressInsight,
    WaterEntry,
    WeightEntry,
)
from ai_me.domain.health_import import HealthImportProvider, HealthImportResult, UserGoogleDriveSettings
from ai_me.domain.user import AppUser, UserStatus
from ai_me.services.food_analysis import DisabledFoodPhotoAnalyzer, FoodPhotoAnalyzer
from ai_me.services.google_drive_import import GoogleDriveHealthImportService
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
        google_drive_import_service: Optional[GoogleDriveHealthImportService] = None,
        admin_telegram_user_ids: FrozenSet[int] = frozenset(),
        default_timezone_name: str = "Europe/Moscow",
    ) -> None:
        self.store = store
        self.decision_engine = decision_engine or HealthDecisionEngine()
        self.food_photo_analyzer = food_photo_analyzer or DisabledFoodPhotoAnalyzer()
        self.tbank_csv_importer = tbank_csv_importer or TBankCSVImporter()
        self.google_drive_import_service = google_drive_import_service or GoogleDriveHealthImportService(store=store)
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

    def get_user_by_telegram_user_id(self, telegram_user_id: int) -> Optional[AppUser]:
        return self.store.get_user_by_telegram_user_id(telegram_user_id)

    def list_users_with_google_drive_enabled(self) -> List[AppUser]:
        users = self.store.list_users_with_google_drive_enabled()
        return [user for user in users if user.status == UserStatus.ACTIVE]

    def register_user(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: str,
        first_name: str,
        now: Optional[datetime] = None,
    ) -> AppUser:
        _ = now or datetime.now()
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

        return self.store.create_user(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            status=UserStatus.ACTIVE,
            is_admin=telegram_user_id in self.admin_telegram_user_ids,
        )

    def google_drive_is_configured(self) -> bool:
        return self.google_drive_import_service.is_configured()

    def connect_google_drive_folder(
        self,
        user_id: int,
        folder_input: str,
        now: Optional[datetime] = None,
    ) -> UserGoogleDriveSettings:
        return self.google_drive_import_service.connect_folder(user_id, folder_input=folder_input, now=now)

    def get_google_drive_settings(self, user_id: int) -> Optional[UserGoogleDriveSettings]:
        return self.google_drive_import_service.get_settings(user_id)

    def set_google_drive_enabled(self, user_id: int, enabled: bool, now: Optional[datetime] = None) -> UserGoogleDriveSettings:
        return self.google_drive_import_service.set_enabled(user_id, enabled=enabled, now=now)

    def import_google_drive_health_data(self, user_id: int, now: Optional[datetime] = None) -> HealthImportResult:
        return self.google_drive_import_service.import_new_files(user_id, now=now)

    def list_health_import_files(
        self,
        user_id: int,
        provider: Optional[HealthImportProvider] = None,
    ):
        return self.store.list_health_import_files(user_id, provider=provider)

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
        commentary_data = self._build_daily_commentary_data(
            user_id=user_id,
            digest_date=digest_date,
            meals=meals,
            total_calories=total_calories,
            total_protein_g=total_protein_g,
            total_fat_g=total_fat_g,
            total_carbs_g=total_carbs_g,
            trend_windows=trend_windows,
        )
        commentary = self._build_daily_digest_commentary(
            digest_date=digest_date,
            meals=meals,
            total_calories=total_calories,
            total_protein_g=total_protein_g,
            total_fat_g=total_fat_g,
            total_carbs_g=total_carbs_g,
            commentary_data=commentary_data,
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
            commentary_data=commentary_data,
            commentary=commentary,
        )

    def build_weekly_food_digest(self, user_id: int, week_start: date) -> Optional[WeeklyFoodDigest]:
        highlights: List[WeeklyDigestHighlight] = []
        total_meals = 0
        total_calories = 0
        total_protein_g = 0.0
        daily_calories: List[int] = []
        daily_protein: List[float] = []
        late_heavy_dinners_days = 0
        baseline_meals = self._collect_photo_meals(user_id, week_start - timedelta(days=30), week_start - timedelta(days=1))
        for offset in range(7):
            current_date = week_start + timedelta(days=offset)
            day_meals = self._list_photo_meals_for_date(user_id, current_date)
            total_meals += len(day_meals)
            day_total_calories = sum(meal.calories for meal in day_meals)
            day_total_protein = round(sum(meal.protein_g for meal in day_meals), 2)
            total_calories += day_total_calories
            total_protein_g += day_total_protein
            if day_meals:
                daily_calories.append(day_total_calories)
                daily_protein.append(day_total_protein)
                largest_meal = max(day_meals, key=lambda meal: meal.calories)
                if day_total_calories > 0 and largest_meal.occurred_at.hour >= 19 and largest_meal.calories / day_total_calories >= 0.4:
                    late_heavy_dinners_days += 1
            highlights.append(self._pick_weekly_highlight(current_date, day_meals, baseline_meals))

        if total_meals == 0:
            return None

        commentary_data = self._build_weekly_commentary_data(
            user_id=user_id,
            week_start=week_start,
            total_meals=total_meals,
            total_calories=total_calories,
            total_protein_g=round(total_protein_g, 2),
            daily_calories=daily_calories,
            daily_protein=daily_protein,
            highlights=highlights,
            late_heavy_dinners_days=late_heavy_dinners_days,
        )
        commentary = self._build_weekly_digest_commentary(
            week_start=week_start,
            total_meals=total_meals,
            total_calories=total_calories,
            highlights=highlights,
            commentary_data=commentary_data,
        )
        return WeeklyFoodDigest(
            user_id=user_id,
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
            highlights=highlights,
            total_meals=total_meals,
            total_calories=total_calories,
            commentary_data=commentary_data,
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
            water_ml=analyzed.water_ml,
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
            water_ml=draft.water_ml,
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

    def build_step_progress_insight(self, user_id: int, reference_date: date) -> StepProgressInsight:
        summary = self.get_daily_summary(user_id, reference_date)
        window_start = reference_date - timedelta(days=29)
        entries = self.store.list_activity_entries(user_id, date_from=window_start, date_to=reference_date)
        steps_by_day: Dict[date, int] = {}
        for entry in entries:
            entry_date = entry.occurred_at.date()
            steps_by_day[entry_date] = steps_by_day.get(entry_date, 0) + entry.steps

        days_with_data = len(steps_by_day)
        average_steps_30d: Optional[float] = None
        if days_with_data > 0:
            average_steps_30d = round(sum(steps_by_day.values()) / days_with_data, 1)

        comment = self._build_step_progress_comment(
            steps=summary.steps,
            target_steps=summary.goals.steps,
            average_steps_30d=average_steps_30d,
            days_with_data_30d=days_with_data,
        )
        return StepProgressInsight(
            reference_date=reference_date,
            steps=summary.steps,
            target_steps=summary.goals.steps,
            average_steps_30d=average_steps_30d,
            days_with_data_30d=days_with_data,
            comment=comment,
        )

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

    @staticmethod
    def _build_step_progress_comment(
        *,
        steps: int,
        target_steps: int,
        average_steps_30d: Optional[float],
        days_with_data_30d: int,
    ) -> str:
        target_delta = steps - target_steps
        if target_delta >= 0:
            target_text = "Цель по шагам выполнена с запасом %s." % target_delta
        else:
            target_text = "До цели не хватило %s шагов." % abs(target_delta)

        if average_steps_30d is None or days_with_data_30d < 3 or average_steps_30d <= 0:
            return "%s Пока недостаточно данных для сравнения с 30-дневной средней." % target_text

        average_delta = steps - average_steps_30d
        average_delta_pct = round(abs(average_delta) / average_steps_30d * 100)
        if average_delta > 0:
            average_text = "Это на %s%% выше вашей средней за последние 30 дней." % average_delta_pct
        elif average_delta < 0:
            average_text = "Это на %s%% ниже вашей средней за последние 30 дней." % average_delta_pct
        else:
            average_text = "Это ровно на уровне вашей средней за последние 30 дней."
        return "%s %s" % (average_text, target_text)

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

    def _build_comparisons_from_trends(
        self,
        total_calories: int,
        total_protein_g: float,
        total_fat_g: float,
        total_carbs_g: float,
        trend_windows: List[DigestTrendWindow],
    ) -> List[DigestComparison]:
        return [
            DigestComparison(
                days=trend.days,
                calories_delta_pct=self._percent_delta(total_calories, trend.average_calories),
                protein_delta_pct=self._percent_delta(total_protein_g, trend.average_protein_g),
                fat_delta_pct=self._percent_delta(total_fat_g, trend.average_fat_g),
                carbs_delta_pct=self._percent_delta(total_carbs_g, trend.average_carbs_g),
            )
            for trend in trend_windows
        ]

    def _build_daily_commentary_data(
        self,
        user_id: int,
        digest_date: date,
        meals: List[DigestMealSnapshot],
        total_calories: int,
        total_protein_g: float,
        total_fat_g: float,
        total_carbs_g: float,
        trend_windows: List[DigestTrendWindow],
    ) -> DailyDigestCommentaryData:
        comparisons = self._build_comparisons_from_trends(
            total_calories=total_calories,
            total_protein_g=total_protein_g,
            total_fat_g=total_fat_g,
            total_carbs_g=total_carbs_g,
            trend_windows=trend_windows,
        )
        first_meal = min(meals, key=lambda meal: meal.occurred_at)
        last_meal = max(meals, key=lambda meal: meal.occurred_at)
        largest_meal = max(meals, key=lambda meal: meal.calories)
        protein_leader = max(meals, key=lambda meal: meal.protein_g)
        meal_pattern = DailyDigestMealPattern(
            first_meal_time=first_meal.occurred_at.strftime("%H:%M"),
            last_meal_time=last_meal.occurred_at.strftime("%H:%M"),
            eating_window_hours=round((last_meal.occurred_at - first_meal.occurred_at).total_seconds() / 3600, 1),
            largest_meal=DigestMealFeature(
                title=largest_meal.title,
                time_text=largest_meal.occurred_at.strftime("%H:%M"),
                calories=largest_meal.calories,
                share_of_day_pct=round((largest_meal.calories / max(total_calories, 1)) * 100, 1),
            ),
            protein_leader=DigestMealFeature(
                title=protein_leader.title,
                time_text=protein_leader.occurred_at.strftime("%H:%M"),
                protein_g=protein_leader.protein_g,
                calories=protein_leader.calories,
            ),
        )
        protein_macro_kcal = total_protein_g * 4
        fat_macro_kcal = total_fat_g * 9
        carbs_macro_kcal = total_carbs_g * 4
        macro_total = protein_macro_kcal + fat_macro_kcal + carbs_macro_kcal
        macro_balance = DailyDigestMacroBalance(
            protein_share_pct=round((protein_macro_kcal / macro_total) * 100, 1) if macro_total else 0.0,
            fat_share_pct=round((fat_macro_kcal / macro_total) * 100, 1) if macro_total else 0.0,
            carbs_share_pct=round((carbs_macro_kcal / macro_total) * 100, 1) if macro_total else 0.0,
            protein_density_g_per_1000kcal=round((total_protein_g / total_calories) * 1000, 1) if total_calories else 0.0,
        )
        stability = DailyDigestStability(
            calories_streak=self._compute_digest_streak(user_id, digest_date, metric="calories"),
            protein_streak=self._compute_digest_streak(user_id, digest_date, metric="protein_g"),
        )
        comparison_7d = next((item for item in comparisons if item.days == 7), None)
        flags = DailyDigestFlags(
            late_heavy_meal=(
                meal_pattern.largest_meal is not None
                and largest_meal.occurred_at.hour >= 19
                and meal_pattern.largest_meal.share_of_day_pct >= 40.0
            ),
            high_fat_day=(
                macro_balance.fat_share_pct >= 40.0
                or (comparison_7d is not None and (comparison_7d.fat_delta_pct or 0.0) >= 15.0)
            ),
            low_meal_count=len(meals) <= 2,
            protein_good=(
                macro_balance.protein_density_g_per_1000kcal >= 60.0
                or (comparison_7d is not None and (comparison_7d.protein_delta_pct or 0.0) >= 5.0)
            ),
        )
        return DailyDigestCommentaryData(
            comparisons=comparisons,
            meal_pattern=meal_pattern,
            macro_balance=macro_balance,
            stability=stability,
            flags=flags,
        )

    def _build_daily_digest_commentary(
        self,
        digest_date: date,
        meals: List[DigestMealSnapshot],
        total_calories: int,
        total_protein_g: float,
        total_fat_g: float,
        total_carbs_g: float,
        commentary_data: DailyDigestCommentaryData,
    ) -> str:
        lines = [
            "За %s подтверждено %s блюда(блюд) по фото на %s ккал." % (digest_date.isoformat(), len(meals), total_calories),
        ]
        primary_comparison = next(
            (item for item in commentary_data.comparisons if item.days == 7 and item.calories_delta_pct is not None),
            None,
        ) or next((item for item in commentary_data.comparisons if item.calories_delta_pct is not None), None)
        if primary_comparison is not None:
            calories_direction = "выше" if (primary_comparison.calories_delta_pct or 0.0) >= 0 else "ниже"
            comparison_text = "Это на %.1f%% %s %s-дневной базы" % (
                abs(primary_comparison.calories_delta_pct or 0.0),
                calories_direction,
                primary_comparison.days,
            )
            if primary_comparison.protein_delta_pct is not None:
                protein_direction = "выше" if primary_comparison.protein_delta_pct >= 0 else "ниже"
                comparison_text += ", при этом белок был на %.1f%% %s среднего." % (
                    abs(primary_comparison.protein_delta_pct),
                    protein_direction,
                )
            else:
                comparison_text += "."
            lines.append(comparison_text)

        largest_meal = commentary_data.meal_pattern.largest_meal
        if largest_meal is not None:
            meal_sentence = (
                "Самым плотным был прием пищи %s в %s: %s ккал, это %.1f%% дневной калорийности."
                % (
                    largest_meal.title,
                    largest_meal.time_text,
                    largest_meal.calories,
                    largest_meal.share_of_day_pct,
                )
            )
            lines.append(meal_sentence)

        if commentary_data.flags.late_heavy_meal:
            lines.append("День выглядит с заметной концентрацией калорий вечером.")
        elif commentary_data.flags.protein_good:
            lines.append("По белку день выглядит сильнее обычного и без явного провала по структуре.")
        elif commentary_data.flags.high_fat_day:
            lines.append("День выглядит более жирным, чем твоя обычная база последних недель.")
        else:
            calories_streak = commentary_data.stability.calories_streak
            if calories_streak.days >= 2 and calories_streak.direction != "flat":
                lines.append(
                    "Это %s-й день подряд с тем же направлением по калорийности относительно 7-дневной базы."
                    % calories_streak.days
                )
            else:
                lines.append(
                    "Окно приема пищи составило %.1f ч, а белковая плотность дня — %.1f г на 1000 ккал."
                    % (
                        commentary_data.meal_pattern.eating_window_hours,
                        commentary_data.macro_balance.protein_density_g_per_1000kcal,
                    )
                )
        return "\n".join(lines[:4])

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

    def _build_weekly_commentary_data(
        self,
        user_id: int,
        week_start: date,
        total_meals: int,
        total_calories: int,
        total_protein_g: float,
        daily_calories: List[int],
        daily_protein: List[float],
        highlights: List[WeeklyDigestHighlight],
        late_heavy_dinners_days: int,
    ) -> WeeklyDigestCommentaryData:
        days_with_meals = len(daily_calories)
        average_daily_calories = round(total_calories / max(days_with_meals, 1), 1) if days_with_meals else 0.0
        average_daily_protein_g = round(total_protein_g / max(days_with_meals, 1), 1) if days_with_meals else 0.0
        comparisons = []
        for window_days in (7, 14, 30):
            baseline = self._build_period_average(
                user_id=user_id,
                start_date=week_start - timedelta(days=window_days),
                end_date=week_start - timedelta(days=1),
            )
            comparisons.append(
                DigestComparison(
                    days=window_days,
                    calories_delta_pct=self._percent_delta(average_daily_calories, baseline["average_calories"]),
                    protein_delta_pct=self._percent_delta(average_daily_protein_g, baseline["average_protein_g"]),
                )
            )

        highest_calorie_day = None
        highest_protein_day = None
        if daily_calories:
            max_calories_index = daily_calories.index(max(daily_calories))
            highest_calorie_day = WeeklyDigestDayMetric(
                date_text=(week_start + timedelta(days=max_calories_index)).isoformat(),
                calories=daily_calories[max_calories_index],
            )
        if daily_protein:
            max_protein_index = daily_protein.index(max(daily_protein))
            highest_protein_day = WeeklyDigestDayMetric(
                date_text=(week_start + timedelta(days=max_protein_index)).isoformat(),
                protein_g=daily_protein[max_protein_index],
            )

        strongest = max((item for item in highlights if item.meal is not None), key=lambda item: item.score, default=None)
        daily_fat_totals = []
        daily_carb_totals = []
        for offset in range(7):
            day_meals = self._list_photo_meals_for_date(user_id, week_start + timedelta(days=offset))
            if day_meals:
                daily_fat_totals.append(sum(meal.fat_g for meal in day_meals))
                daily_carb_totals.append(sum(meal.carbs_g for meal in day_meals))
        variability = {
            "белок": self._coefficient_of_variation(daily_protein),
            "жиры": self._coefficient_of_variation(daily_fat_totals),
            "углеводы": self._coefficient_of_variation(daily_carb_totals),
        }
        most_variable_macro = max(variability, key=variability.get) if (daily_protein or daily_fat_totals or daily_carb_totals) else ""
        previous_week = self._build_period_average(
            user_id=user_id,
            start_date=week_start - timedelta(days=7),
            end_date=week_start - timedelta(days=1),
        )
        current_cv = self._coefficient_of_variation(daily_calories)
        previous_cv = previous_week["calorie_cv"]
        comparison_7d = next((item for item in comparisons if item.days == 7), None)
        return WeeklyDigestCommentaryData(
            days_with_meals=days_with_meals,
            average_daily_calories=average_daily_calories,
            average_daily_protein_g=average_daily_protein_g,
            comparisons=comparisons,
            patterns=WeeklyDigestPatterns(
                late_heavy_dinners_days=late_heavy_dinners_days,
                high_protein_days=sum(1 for value in daily_protein if value >= 120.0),
                low_calorie_days=sum(1 for value in daily_calories if average_daily_calories and value < average_daily_calories * 0.85),
                most_variable_macro=most_variable_macro,
            ),
            highlights=WeeklyDigestHighlightSummary(
                highest_calorie_day=highest_calorie_day,
                highest_protein_day=highest_protein_day,
                most_distinct_meal_title=strongest.meal.title if strongest is not None and strongest.meal is not None else "",
                most_distinct_meal_date=strongest.digest_date.isoformat() if strongest is not None else "",
                most_distinct_meal_reason=strongest.reason if strongest is not None else "",
            ),
            consistency=WeeklyDigestConsistency(
                daily_calorie_cv=current_cv,
                is_more_stable_than_prev_week=(previous_cv > 0 and current_cv > 0 and current_cv < previous_cv),
            ),
            flags=WeeklyDigestFlags(
                week_heavier_than_usual=bool(comparison_7d and (comparison_7d.calories_delta_pct or 0.0) >= 10.0),
                protein_stable=bool(comparison_7d and abs(comparison_7d.protein_delta_pct or 0.0) <= 10.0),
                evening_overload_pattern=late_heavy_dinners_days >= 2,
            ),
        )

    def _build_weekly_digest_commentary(
        self,
        week_start: date,
        total_meals: int,
        total_calories: int,
        highlights: List[WeeklyDigestHighlight],
        commentary_data: WeeklyDigestCommentaryData,
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
            "Дни с подтвержденными фото-блюдами: %s из 7, средняя калорийность дня — %.1f ккал."
            % (days_with_meals, commentary_data.average_daily_calories),
        ]
        comparison_7d = next(
            (item for item in commentary_data.comparisons if item.days == 7 and item.calories_delta_pct is not None),
            None,
        )
        if comparison_7d is not None:
            lines.append(
                "Это на %.1f%% %s предыдущих 7 дней, а белок изменился на %.1f%%."
                % (
                    abs(comparison_7d.calories_delta_pct or 0.0),
                    "выше" if (comparison_7d.calories_delta_pct or 0.0) >= 0 else "ниже",
                    comparison_7d.protein_delta_pct or 0.0,
                )
            )
        if commentary_data.flags.evening_overload_pattern:
            lines.append(
                "Главный паттерн недели — %s дня(дней) с наиболее плотным приемом пищи вечером."
                % commentary_data.patterns.late_heavy_dinners_days
            )
        if strongest is not None and strongest.meal is not None:
            lines.append(
                "Самое выделяющееся блюдо недели: %s (%s). %s"
                % (
                    strongest.meal.title,
                    strongest.digest_date.strftime("%d.%m"),
                    strongest.reason,
                )
            )
        elif commentary_data.highlights.highest_calorie_day is not None:
            lines.append(
                "Самый калорийный день недели — %s с %s ккал."
                % (
                    commentary_data.highlights.highest_calorie_day.date_text,
                    commentary_data.highlights.highest_calorie_day.calories,
                )
            )
        if commentary_data.flags.week_heavier_than_usual:
            lines.append("Неделя выглядит тяжелее твоей обычной базы, особенно по вечерней нагрузке.")
        elif commentary_data.flags.protein_stable:
            lines.append("Неделя выглядит стабильной по белку без заметного отклонения от привычной базы.")
        else:
            lines.append(
                "Наиболее плавающим макроэлементом недели были %s, а стабильность по калориям %s относительно прошлой недели."
                % (
                    commentary_data.patterns.most_variable_macro or "макросы",
                    "улучшилась" if commentary_data.consistency.is_more_stable_than_prev_week else "не улучшилась",
                )
            )
        return "\n".join(lines[:5])

    def _build_period_average(self, user_id: int, start_date: date, end_date: date) -> Dict[str, float]:
        if end_date < start_date:
            return {
                "average_calories": 0.0,
                "average_protein_g": 0.0,
                "average_fat_g": 0.0,
                "average_carbs_g": 0.0,
                "calorie_cv": 0.0,
            }
        daily_calories: List[int] = []
        daily_protein: List[float] = []
        daily_fat: List[float] = []
        daily_carbs: List[float] = []
        cursor = start_date
        while cursor <= end_date:
            meals = self._list_photo_meals_for_date(user_id, cursor)
            if meals:
                daily_calories.append(sum(meal.calories for meal in meals))
                daily_protein.append(sum(meal.protein_g for meal in meals))
                daily_fat.append(sum(meal.fat_g for meal in meals))
                daily_carbs.append(sum(meal.carbs_g for meal in meals))
            cursor += timedelta(days=1)
        divisor = max(len(daily_calories), 1)
        return {
            "average_calories": round(sum(daily_calories) / divisor, 2) if daily_calories else 0.0,
            "average_protein_g": round(sum(daily_protein) / divisor, 2) if daily_protein else 0.0,
            "average_fat_g": round(sum(daily_fat) / divisor, 2) if daily_fat else 0.0,
            "average_carbs_g": round(sum(daily_carbs) / divisor, 2) if daily_carbs else 0.0,
            "calorie_cv": self._coefficient_of_variation(daily_calories),
        }

    def _compute_digest_streak(self, user_id: int, digest_date: date, metric: str) -> DigestStreak:
        direction = ""
        days = 0
        cursor = digest_date
        while True:
            meals = self._list_photo_meals_for_date(user_id, cursor)
            if not meals:
                break
            current_total = self._metric_total(meals, metric)
            baseline = self._build_period_average(
                user_id=user_id,
                start_date=cursor - timedelta(days=7),
                end_date=cursor - timedelta(days=1),
            )
            baseline_key = "average_%s" % metric
            baseline_value = baseline.get(baseline_key, 0.0)
            if baseline_value <= 0:
                break
            delta = self._percent_delta(current_total, baseline_value)
            if delta is None or abs(delta) < 5.0:
                current_direction = "flat"
            else:
                current_direction = "above_7d_avg" if delta > 0 else "below_7d_avg"
            if not direction:
                direction = current_direction
            if current_direction != direction:
                break
            days += 1
            cursor -= timedelta(days=1)
            if days >= 14:
                break
        return DigestStreak(direction=direction or "flat", days=days)

    @staticmethod
    def _metric_total(meals: List[DigestMealSnapshot], metric: str) -> float:
        if metric == "calories":
            return float(sum(meal.calories for meal in meals))
        return round(sum(getattr(meal, metric) for meal in meals), 2)

    @staticmethod
    def _coefficient_of_variation(values: List[float]) -> float:
        if len(values) <= 1:
            return 0.0
        mean = sum(values) / len(values)
        if mean <= 0:
            return 0.0
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        return round(math.sqrt(variance) / mean, 3)
