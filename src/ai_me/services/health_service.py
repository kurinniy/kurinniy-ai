import hashlib
import json
import logging
import math
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Dict, FrozenSet, List, Optional
from uuid import uuid4

from PIL import Image

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
from ai_me.domain.food import (
    MEAL_PHOTO_SOURCE,
    WATER_PHOTO_SOURCE,
    MealDraftStatus,
    MealMedia,
    MealPhotoDraft,
    PhotoLogKind,
    PhotoProcessingResult,
    PhotoLogResult,
)
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    DailyHealthSummary,
    MealEntry,
    PostSaveCoachingSnapshot,
    SleepEntry,
    StepProgressInsight,
    WaterProgressSnapshot,
    WaterEntry,
    WeightEntry,
)
from ai_me.domain.health_import import HealthImportProvider, HealthImportResult, UserGoogleDriveSettings
from ai_me.domain.user import AppUser, UserGoal, UserSex, UserStatus
from ai_me.services.food_analysis import DisabledFoodPhotoAnalyzer, FoodPhotoAnalyzer
from ai_me.services.google_drive_import import GoogleDriveHealthImportService
from ai_me.services.media_storage import (
    DisabledMediaStorage,
    MediaStorage,
    build_meal_media_object_key,
)
from ai_me.services.rules import HealthDecisionEngine
from ai_me.services.tbank_import import TBankCSVImporter
from ai_me.storage.base import HealthStore


logger = logging.getLogger(__name__)


class HealthService:
    def __init__(
        self,
        store: HealthStore,
        decision_engine: Optional[HealthDecisionEngine] = None,
        food_photo_analyzer: Optional[FoodPhotoAnalyzer] = None,
        tbank_csv_importer: Optional[TBankCSVImporter] = None,
        media_storage: Optional[MediaStorage] = None,
        google_drive_import_service: Optional[GoogleDriveHealthImportService] = None,
        admin_telegram_user_ids: FrozenSet[int] = frozenset(),
        default_timezone_name: str = "Europe/Moscow",
    ) -> None:
        self.store = store
        self.decision_engine = decision_engine or HealthDecisionEngine()
        self.food_photo_analyzer = food_photo_analyzer or DisabledFoodPhotoAnalyzer()
        self.tbank_csv_importer = tbank_csv_importer or TBankCSVImporter()
        self.media_storage = media_storage or DisabledMediaStorage()
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

    def get_user_by_id(self, user_id: int) -> Optional[AppUser]:
        return next((item for item in self.store.list_users() if item.user_id == user_id), None)

    def update_user_about(
        self,
        user_id: int,
        *,
        sex: Optional[UserSex] = None,
        age_years: Optional[int] = None,
        height_cm: Optional[int] = None,
        profile_weight_kg: Optional[float] = None,
        goal: Optional[UserGoal] = None,
    ) -> AppUser:
        user = self._require_user(user_id)
        updated = replace(
            user,
            sex=sex if sex is not None else user.sex,
            age_years=age_years if age_years is not None else user.age_years,
            height_cm=height_cm if height_cm is not None else user.height_cm,
            profile_weight_kg=profile_weight_kg if profile_weight_kg is not None else user.profile_weight_kg,
            goal=goal if goal is not None else user.goal,
        )
        return self.store.update_user_settings(updated)

    def update_user_goal_settings(
        self,
        user_id: int,
        *,
        target_water_ml: Optional[int] = None,
        target_protein_g: Optional[int] = None,
        target_calories_min: Optional[int] = None,
        target_calories_max: Optional[int] = None,
    ) -> AppUser:
        user = self._require_user(user_id)
        updated = replace(
            user,
            target_water_ml=target_water_ml if target_water_ml is not None else user.target_water_ml,
            target_protein_g=target_protein_g if target_protein_g is not None else user.target_protein_g,
            target_calories_min=target_calories_min if target_calories_min is not None else user.target_calories_min,
            target_calories_max=target_calories_max if target_calories_max is not None else user.target_calories_max,
        )
        return self.store.update_user_settings(updated)

    def reset_user_goal_settings(self, user_id: int) -> AppUser:
        user = self._require_user(user_id)
        updated = replace(
            user,
            target_water_ml=2000,
            target_protein_g=120,
            target_calories_min=None,
            target_calories_max=None,
        )
        return self.store.update_user_settings(updated)

    def update_user_reminders(
        self,
        user_id: int,
        *,
        reminders_enabled: Optional[bool] = None,
        reminder_meal_logging: Optional[bool] = None,
        reminder_water: Optional[bool] = None,
        reminder_evening_summary: Optional[bool] = None,
    ) -> AppUser:
        user = self._require_user(user_id)
        enabled = user.reminders_enabled if reminders_enabled is None else reminders_enabled
        meal_logging = user.reminder_meal_logging if reminder_meal_logging is None else reminder_meal_logging
        water = user.reminder_water if reminder_water is None else reminder_water
        evening_summary = (
            user.reminder_evening_summary if reminder_evening_summary is None else reminder_evening_summary
        )
        if not enabled:
            meal_logging = False
            water = False
            evening_summary = False
        elif reminder_meal_logging or reminder_water or reminder_evening_summary:
            enabled = True
        updated = replace(
            user,
            reminders_enabled=enabled,
            reminder_meal_logging=meal_logging,
            reminder_water=water,
            reminder_evening_summary=evening_summary,
        )
        return self.store.update_user_settings(updated)

    def set_admin_mode(self, user_id: int, enabled: bool) -> AppUser:
        user = self.get_user_by_id(user_id)
        if user is None:
            raise ValueError("Пользователь не найден.")
        if not user.is_admin:
            raise ValueError("Команда доступна только администратору.")
        return self.store.update_user_admin_mode(user_id, enabled=enabled)

    def complete_onboarding(self, user_id: int, *, now: Optional[datetime] = None) -> AppUser:
        user = self._require_user(user_id)
        if user.onboarding_completed_at is not None:
            return user
        return self.store.complete_user_onboarding(user_id, completed_at=now or datetime.now())

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

    def _require_user(self, user_id: int) -> AppUser:
        user = self.get_user_by_id(user_id)
        if user is None:
            raise ValueError("Пользователь не найден.")
        return user

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

    def set_google_drive_alert_sent(self, user_id: int, sent_at: Optional[datetime] = None) -> UserGoogleDriveSettings:
        return self.google_drive_import_service.set_stale_alert_sent(user_id, sent_at=sent_at)

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

    def _build_daily_food_digest(
        self,
        user_id: int,
        digest_date: date,
        debug_timings: Optional[Dict[str, float]] = None,
    ) -> Optional[DailyFoodDigest]:
        overall_started_at = time.perf_counter()
        history_start = digest_date - timedelta(days=30)
        historical_cache_started_at = time.perf_counter()
        historical_cache = self._build_photo_meals_cache(
            user_id=user_id,
            start_date=history_start,
            end_date=digest_date - timedelta(days=1),
            include_image_bytes=False,
        )
        historical_cache_seconds = time.perf_counter() - historical_cache_started_at
        digest_day_cache_started_at = time.perf_counter()
        digest_day_cache = self._build_photo_meals_cache(
            user_id=user_id,
            start_date=digest_date,
            end_date=digest_date,
            include_image_bytes=False,
        )
        digest_day_cache_seconds = time.perf_counter() - digest_day_cache_started_at
        cache_merge_started_at = time.perf_counter()
        photo_meals_cache = self._merge_photo_meal_caches(historical_cache, digest_day_cache)
        cache_merge_seconds = time.perf_counter() - cache_merge_started_at
        meals = self._list_photo_meals_from_cache(photo_meals_cache, digest_date)
        if not meals:
            if debug_timings is not None:
                debug_timings.update(
                    {
                        "build_digest_seconds": time.perf_counter() - overall_started_at,
                        "historical_cache_seconds": historical_cache_seconds,
                        "digest_day_cache_seconds": digest_day_cache_seconds,
                        "cache_merge_seconds": cache_merge_seconds,
                    }
                )
            return None
        media_hydration_started_at = time.perf_counter()
        meals = self._hydrate_primary_media_bytes(user_id, meals)
        media_hydration_seconds = time.perf_counter() - media_hydration_started_at
        daily_summary_started_at = time.perf_counter()
        manual_water_ml = self.store.get_daily_water_total(user_id, digest_date)
        goals = self.store.get_health_goals(user_id, digest_date)
        daily_summary_seconds = time.perf_counter() - daily_summary_started_at

        trend_windows_started_at = time.perf_counter()
        trend_windows = [
            self._build_trend_window_from_cache(photo_meals_cache, digest_date, window_days)
            for window_days in (7, 14, 30)
        ]
        trend_windows_seconds = time.perf_counter() - trend_windows_started_at
        total_calories = sum(meal.calories for meal in meals)
        total_protein_g = round(sum(meal.protein_g for meal in meals), 2)
        total_fat_g = round(sum(meal.fat_g for meal in meals), 2)
        total_carbs_g = round(sum(meal.carbs_g for meal in meals), 2)
        commentary_data_started_at = time.perf_counter()
        commentary_data = self._build_daily_commentary_data(
            digest_date=digest_date,
            meals=meals,
            total_calories=total_calories,
            total_protein_g=total_protein_g,
            total_fat_g=total_fat_g,
            total_carbs_g=total_carbs_g,
            trend_windows=trend_windows,
            photo_meals_cache=photo_meals_cache,
        )
        commentary_data_seconds = time.perf_counter() - commentary_data_started_at
        commentary_text_started_at = time.perf_counter()
        commentary = self._build_daily_digest_commentary(
            digest_date=digest_date,
            meals=meals,
            total_calories=total_calories,
            total_protein_g=total_protein_g,
            total_fat_g=total_fat_g,
            total_carbs_g=total_carbs_g,
            commentary_data=commentary_data,
        )
        commentary_text_seconds = time.perf_counter() - commentary_text_started_at
        if debug_timings is not None:
            debug_timings.update(
                {
                    "build_digest_seconds": time.perf_counter() - overall_started_at,
                    "historical_cache_seconds": historical_cache_seconds,
                    "digest_day_cache_seconds": digest_day_cache_seconds,
                    "cache_merge_seconds": cache_merge_seconds,
                    "media_hydration_seconds": media_hydration_seconds,
                    "daily_summary_seconds": daily_summary_seconds,
                    "trend_windows_seconds": trend_windows_seconds,
                    "commentary_data_seconds": commentary_data_seconds,
                    "commentary_text_seconds": commentary_text_seconds,
                }
            )
        return DailyFoodDigest(
            user_id=user_id,
            digest_date=digest_date,
            meals=meals,
            total_calories=total_calories,
            total_protein_g=total_protein_g,
            total_fat_g=total_fat_g,
            total_carbs_g=total_carbs_g,
            water_ml=manual_water_ml,
            water_goal_ml=goals.water_ml,
            steps_goal=goals.steps,
            trend_windows=trend_windows,
            commentary_data=commentary_data,
            commentary=commentary,
        )

    def build_daily_food_digest(
        self,
        user_id: int,
        digest_date: date,
        debug_timings: Optional[Dict[str, float]] = None,
    ) -> Optional[DailyFoodDigest]:
        return self._build_daily_food_digest(user_id, digest_date, debug_timings=debug_timings)

    def build_weekly_food_digest(
        self,
        user_id: int,
        week_start: date,
        debug_timings: Optional[Dict[str, float]] = None,
    ) -> Optional[WeeklyFoodDigest]:
        overall_started_at = time.perf_counter()
        highlights: List[WeeklyDigestHighlight] = []
        total_meals = 0
        total_calories = 0
        total_protein_g = 0.0
        daily_calories: List[int] = []
        daily_protein: List[float] = []
        late_heavy_dinners_days = 0
        baseline_started_at = time.perf_counter()
        baseline_end = week_start - timedelta(days=1)
        baseline_start = week_start - timedelta(days=30)
        baseline_cache = self._build_photo_meals_cache(
            user_id=user_id,
            start_date=baseline_start,
            end_date=baseline_end,
            include_image_bytes=False,
        )
        baseline_meals = self._collect_photo_meals_from_cache(baseline_cache, baseline_start, baseline_end)
        baseline_seconds = time.perf_counter() - baseline_started_at
        week_cache_started_at = time.perf_counter()
        week_cache = self._build_photo_meals_cache(
            user_id=user_id,
            start_date=week_start,
            end_date=week_start + timedelta(days=6),
            include_image_bytes=False,
        )
        week_cache_seconds = time.perf_counter() - week_cache_started_at
        cache_merge_started_at = time.perf_counter()
        weekly_photo_meals_cache = self._merge_photo_meal_caches(baseline_cache, week_cache)
        cache_merge_seconds = time.perf_counter() - cache_merge_started_at
        day_meals_seconds = 0.0
        highlight_pick_seconds = 0.0
        for offset in range(7):
            current_date = week_start + timedelta(days=offset)
            day_started_at = time.perf_counter()
            day_meals = self._list_photo_meals_from_cache(weekly_photo_meals_cache, current_date)
            day_meals_seconds += time.perf_counter() - day_started_at
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
            highlight_started_at = time.perf_counter()
            highlights.append(self._pick_weekly_highlight(current_date, day_meals, baseline_meals))
            highlight_pick_seconds += time.perf_counter() - highlight_started_at

        if total_meals == 0:
            if debug_timings is not None:
                debug_timings.update(
                    {
                        "baseline_collection_seconds": baseline_seconds,
                        "week_cache_seconds": week_cache_seconds,
                        "cache_merge_seconds": cache_merge_seconds,
                        "week_meals_collection_seconds": day_meals_seconds,
                        "highlight_selection_seconds": highlight_pick_seconds,
                        "build_digest_seconds": time.perf_counter() - overall_started_at,
                    }
                )
            return None

        highlight_media_hydration_started_at = time.perf_counter()
        highlights = self._hydrate_weekly_highlight_media(user_id, highlights)
        highlight_media_hydration_seconds = time.perf_counter() - highlight_media_hydration_started_at

        commentary_data_started_at = time.perf_counter()
        commentary_data = self._build_weekly_commentary_data(
            week_start=week_start,
            total_meals=total_meals,
            total_calories=total_calories,
            total_protein_g=round(total_protein_g, 2),
            daily_calories=daily_calories,
            daily_protein=daily_protein,
            highlights=highlights,
            late_heavy_dinners_days=late_heavy_dinners_days,
            photo_meals_cache=weekly_photo_meals_cache,
        )
        commentary_data_seconds = time.perf_counter() - commentary_data_started_at
        commentary_text_started_at = time.perf_counter()
        commentary = self._build_weekly_digest_commentary(
            week_start=week_start,
            total_meals=total_meals,
            total_calories=total_calories,
            highlights=highlights,
            commentary_data=commentary_data,
        )
        commentary_text_seconds = time.perf_counter() - commentary_text_started_at
        if debug_timings is not None:
            debug_timings.update(
                {
                    "baseline_collection_seconds": baseline_seconds,
                    "week_cache_seconds": week_cache_seconds,
                    "cache_merge_seconds": cache_merge_seconds,
                    "week_meals_collection_seconds": day_meals_seconds,
                    "highlight_selection_seconds": highlight_pick_seconds,
                    "highlight_media_hydration_seconds": highlight_media_hydration_seconds,
                    "commentary_data_seconds": commentary_data_seconds,
                    "commentary_text_seconds": commentary_text_seconds,
                    "build_digest_seconds": time.perf_counter() - overall_started_at,
                }
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
        normalized = entry if entry.created_at is not None else replace(entry, created_at=entry.occurred_at)
        self.store.add_meal(user_id, normalized)

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
        draft = self._build_photo_draft(
            photo_file_id=photo_file_id,
            photo_unique_id=photo_unique_id,
            image_bytes=image_bytes,
            mime_type=mime_type,
            occurred_at=occurred_at,
            caption=caption,
        )
        self.store.create_meal_draft(user_id, draft)
        self.store.create_meal_media(
            self._build_photo_media(
                user_id=user_id,
                draft_id=draft.draft_id,
                meal_entry_id="",
                occurred_at=draft.occurred_at,
                created_at=draft.created_at,
                photo_file_id=photo_file_id,
                photo_unique_id=photo_unique_id,
                image_bytes=image_bytes,
                mime_type=mime_type,
            )
        )
        return draft

    def create_photo_log_from_photo(
        self,
        user_id: int,
        photo_file_id: str,
        photo_unique_id: str,
        image_bytes: bytes,
        mime_type: str,
        occurred_at: Optional[datetime] = None,
        caption: str = "",
        debug_timings: Optional[Dict[str, float]] = None,
    ) -> PhotoProcessingResult:
        analyze_started_at = time.perf_counter()
        draft = self._build_photo_draft(
            photo_file_id=photo_file_id,
            photo_unique_id=photo_unique_id,
            image_bytes=image_bytes,
            mime_type=mime_type,
            occurred_at=occurred_at,
            caption=caption,
        )
        analyze_finished_at = time.perf_counter()
        if debug_timings is not None:
            debug_timings["photo analysis"] = round(analyze_finished_at - analyze_started_at, 2)

        if not draft.is_water_only and draft.confidence > 0.6 and self._is_draft_determined(draft):
            meal = self._build_meal_entry_from_draft(draft, draft_id_override="")
            media = self._build_photo_media(
                user_id=user_id,
                draft_id="",
                meal_entry_id=meal.entry_id,
                occurred_at=draft.occurred_at,
                created_at=draft.created_at,
                photo_file_id=photo_file_id,
                photo_unique_id=photo_unique_id,
                image_bytes=image_bytes,
                mime_type=mime_type,
            )
            save_started_at = time.perf_counter()
            self.store.create_meal_with_media(user_id, meal, media)
            if debug_timings is not None:
                debug_timings["direct save transaction"] = round(time.perf_counter() - save_started_at, 2)
            return PhotoProcessingResult(
                photo_log=PhotoLogResult(
                    entry_id=meal.entry_id,
                    kind=PhotoLogKind.MEAL,
                    title=meal.title,
                    occurred_at=meal.occurred_at,
                    water_ml=meal.water_ml,
                    meal_entry=meal,
                )
            )

        store_started_at = time.perf_counter()
        self.store.create_meal_draft(user_id, draft)
        self.store.create_meal_media(
            self._build_photo_media(
                user_id=user_id,
                draft_id=draft.draft_id,
                meal_entry_id="",
                occurred_at=draft.occurred_at,
                created_at=draft.created_at,
                photo_file_id=photo_file_id,
                photo_unique_id=photo_unique_id,
                image_bytes=image_bytes,
                mime_type=mime_type,
            )
        )
        if debug_timings is not None:
            debug_timings["store draft and media"] = round(time.perf_counter() - store_started_at, 2)
        return PhotoProcessingResult(draft=draft)

    def confirm_meal_draft(self, user_id: int, draft_id: str) -> PhotoLogResult:
        draft = self._require_meal_draft(user_id, draft_id, MealDraftStatus.PENDING)
        now = datetime.now()
        if draft.is_water_only:
            water = WaterEntry(
                entry_id=str(uuid4()),
                occurred_at=draft.occurred_at,
                amount_ml=draft.water_ml,
            )
            self.store.confirm_meal_draft_as_water(user_id, draft_id, water)
            return PhotoLogResult(
                entry_id=water.entry_id,
                kind=PhotoLogKind.WATER,
                title=draft.title,
                occurred_at=draft.occurred_at,
                water_ml=draft.water_ml,
            )
        meal = self._build_meal_entry_from_draft(draft, created_at=now)
        self.store.confirm_meal_draft_as_meal(user_id, draft_id, meal)
        return PhotoLogResult(
            entry_id=meal.entry_id,
            kind=PhotoLogKind.MEAL,
            title=meal.title,
            occurred_at=meal.occurred_at,
            water_ml=meal.water_ml,
            meal_entry=meal,
        )

    def reject_meal_draft(self, user_id: int, draft_id: str) -> MealPhotoDraft:
        draft = self._require_meal_draft(user_id, draft_id, MealDraftStatus.PENDING)
        self.store.update_meal_draft_status(user_id, draft_id, MealDraftStatus.REJECTED)
        return draft

    def get_meal_draft(self, user_id: int, draft_id: str) -> MealPhotoDraft:
        return self._require_meal_draft(user_id, draft_id, MealDraftStatus.PENDING)

    def update_meal_draft(
        self,
        user_id: int,
        draft_id: str,
        *,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        occurred_at: Optional[datetime] = None,
        calories: Optional[int] = None,
        protein_g: Optional[float] = None,
        fat_g: Optional[float] = None,
        carbs_g: Optional[float] = None,
    ) -> MealPhotoDraft:
        draft = self._require_meal_draft(user_id, draft_id, MealDraftStatus.PENDING)
        updated = replace(
            draft,
            title=title if title is not None else draft.title,
            summary=summary if summary is not None else draft.summary,
            occurred_at=occurred_at if occurred_at is not None else draft.occurred_at,
            calories=calories if calories is not None else draft.calories,
            protein_g=protein_g if protein_g is not None else draft.protein_g,
            fat_g=fat_g if fat_g is not None else draft.fat_g,
            carbs_g=carbs_g if carbs_g is not None else draft.carbs_g,
        )
        self.store.update_meal_draft(user_id, updated)
        return updated

    def scale_meal_draft_portion(self, user_id: int, draft_id: str, factor: float) -> MealPhotoDraft:
        draft = self._require_meal_draft(user_id, draft_id, MealDraftStatus.PENDING)
        updated = replace(
            draft,
            calories=max(0, int(round(draft.calories * factor))),
            protein_g=round(draft.protein_g * factor, 1),
            fat_g=round(draft.fat_g * factor, 1),
            carbs_g=round(draft.carbs_g * factor, 1),
            water_ml=max(0, int(round(draft.water_ml * factor))),
        )
        self.store.update_meal_draft(user_id, updated)
        return updated

    def list_meal_drafts(
        self,
        user_id: int,
        status: MealDraftStatus = MealDraftStatus.PENDING,
    ) -> List[MealPhotoDraft]:
        return self.store.list_meal_drafts(user_id, status=status)

    def get_meal_draft_any_status(self, user_id: int, draft_id: str) -> MealPhotoDraft:
        draft = self.store.get_meal_draft(user_id, draft_id)
        if draft is None:
            raise ValueError("Черновик приема пищи не найден: %s" % draft_id)
        return draft

    def list_recent_food_draft_history(
        self,
        user_id: int,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> List[MealPhotoDraft]:
        drafts: List[MealPhotoDraft] = []
        for status in (
            MealDraftStatus.PENDING,
            MealDraftStatus.CONFIRMED,
            MealDraftStatus.REJECTED,
        ):
            drafts.extend(self.store.list_meal_drafts(user_id, status=status))
        drafts = [draft for draft in drafts if not draft.is_water_only]
        drafts.sort(key=lambda draft: draft.created_at, reverse=True)
        return drafts[offset : offset + limit]

    def list_recent_meals(
        self,
        user_id: int,
        *,
        limit: int = 10,
        offset: int = 0,
        lookback_days: int = 365,
    ) -> List[MealEntry]:
        end_date = date.today()
        start_date = end_date - timedelta(days=lookback_days)
        meals = self.store.list_meals_in_range(user_id, start_date, end_date)
        meals.sort(key=lambda meal: meal.occurred_at, reverse=True)
        return meals[offset : offset + limit]

    def get_latest_meal(self, user_id: int) -> Optional[MealEntry]:
        meals = self.store.list_meals_in_range(user_id, date.today() - timedelta(days=365), date.today())
        meals.sort(key=lambda meal: meal.created_at or meal.occurred_at, reverse=True)
        return meals[0] if meals else None

    def get_meal_entry(
        self,
        user_id: int,
        entry_id: str,
        *,
        lookback_days: int = 365,
    ) -> MealEntry:
        end_date = date.today()
        start_date = end_date - timedelta(days=lookback_days)
        meals = self.store.list_meals_in_range(user_id, start_date, end_date)
        for meal in meals:
            if meal.entry_id == entry_id:
                return meal
        raise ValueError("Прием пищи не найден: %s" % entry_id)

    def update_meal_entry(
        self,
        user_id: int,
        entry_id: str,
        *,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        occurred_at: Optional[datetime] = None,
        calories: Optional[int] = None,
        protein_g: Optional[float] = None,
        fat_g: Optional[float] = None,
        carbs_g: Optional[float] = None,
        water_ml: Optional[int] = None,
    ) -> MealEntry:
        meal = self.get_meal_entry(user_id, entry_id)
        updated = replace(
            meal,
            title=title if title is not None else meal.title,
            occurred_at=occurred_at if occurred_at is not None else meal.occurred_at,
            calories=calories if calories is not None else meal.calories,
            protein_g=protein_g if protein_g is not None else meal.protein_g,
            fat_g=fat_g if fat_g is not None else meal.fat_g,
            carbs_g=carbs_g if carbs_g is not None else meal.carbs_g,
            water_ml=water_ml if water_ml is not None else meal.water_ml,
            notes=self._update_meal_notes(meal.notes, summary=summary),
        )
        self.store.update_meal(user_id, updated)
        return updated

    def scale_meal_entry_portion(self, user_id: int, entry_id: str, factor: float) -> MealEntry:
        meal = self.get_meal_entry(user_id, entry_id)
        return self.update_meal_entry(
            user_id,
            entry_id,
            calories=max(0, int(round(meal.calories * factor))),
            protein_g=round(meal.protein_g * factor, 1),
            fat_g=round(meal.fat_g * factor, 1),
            carbs_g=round(meal.carbs_g * factor, 1),
            water_ml=max(0, int(round(meal.water_ml * factor))),
        )

    def delete_meal_entry(self, user_id: int, entry_id: str) -> MealEntry:
        meal = self.get_meal_entry(user_id, entry_id)
        self.store.delete_meal(user_id, entry_id)
        return meal

    def get_latest_pending_meal_draft(self, user_id: int) -> Optional[MealPhotoDraft]:
        drafts = [draft for draft in self.store.list_meal_drafts(user_id, status=MealDraftStatus.PENDING) if not draft.is_water_only]
        drafts.sort(key=lambda draft: draft.created_at, reverse=True)
        return drafts[0] if drafts else None

    def list_meal_media(self, user_id: int, target_date: Optional[date] = None) -> List[MealMedia]:
        return self.store.list_meal_media(user_id, target_date=target_date)

    def get_primary_meal_media_for_entry(self, user_id: int, entry_id: str) -> Optional[MealMedia]:
        meal = self.get_meal_entry(user_id, entry_id)
        media_items = [
            media
            for media in self.store.list_meal_media(user_id, target_date=meal.occurred_at.date())
            if media.meal_entry_id == entry_id
        ]
        if not media_items:
            return None
        media_items.sort(key=lambda item: item.created_at)
        return self._safe_hydrate_media_bytes(media_items[0], context="meal_entry:%s" % entry_id)

    def get_primary_meal_media_for_draft(self, user_id: int, draft_id: str) -> Optional[MealMedia]:
        draft = self.get_meal_draft_any_status(user_id, draft_id)
        media_items = [
            media
            for media in self.store.list_meal_media(user_id, target_date=draft.occurred_at.date())
            if media.draft_id == draft_id
        ]
        if not media_items:
            return None
        media_items.sort(key=lambda item: item.created_at)
        return self._safe_hydrate_media_bytes(media_items[0], context="draft:%s" % draft_id)

    def log_water(self, user_id: int, entry: WaterEntry) -> None:
        self.store.add_water(user_id, entry)

    def log_sleep(self, user_id: int, entry: SleepEntry) -> None:
        self.store.add_sleep(user_id, entry)

    def log_weight(self, user_id: int, entry: WeightEntry) -> None:
        self.store.add_weight(user_id, entry)

    def log_activity(self, user_id: int, entry: ActivityEntry) -> None:
        self.store.add_activity(user_id, entry)

    def log_water_and_get_progress(self, user_id: int, entry: WaterEntry, target_date: date) -> WaterProgressSnapshot:
        return self.store.add_water_and_get_progress(user_id, entry, target_date)

    def get_daily_summary(self, user_id: int, target_date: date) -> DailyHealthSummary:
        return self.store.build_health_summary(user_id, target_date)

    def get_post_save_coaching_snapshot(self, user_id: int, target_date: date) -> PostSaveCoachingSnapshot:
        return self.store.build_post_save_coaching_snapshot(user_id, target_date)

    def build_step_progress_insight(
        self,
        user_id: int,
        reference_date: date,
        *,
        target_steps: Optional[int] = None,
    ) -> StepProgressInsight:
        window_start = reference_date - timedelta(days=29)
        daily_steps = self.store.list_daily_step_totals(user_id, date_from=window_start, date_to=reference_date)
        steps_by_day: Dict[date, int] = dict(daily_steps)
        current_steps = steps_by_day.get(reference_date, 0)

        days_with_data = len(steps_by_day)
        average_steps_30d: Optional[float] = None
        if days_with_data > 0:
            average_steps_30d = round(sum(steps_by_day.values()) / days_with_data, 1)

        resolved_target_steps = target_steps
        if resolved_target_steps is None:
            resolved_target_steps = self.store.get_health_goals(user_id, reference_date).steps

        comment = self._build_step_progress_comment(
            steps=current_steps,
            target_steps=resolved_target_steps,
            average_steps_30d=average_steps_30d,
            days_with_data_30d=days_with_data,
        )
        return StepProgressInsight(
            reference_date=reference_date,
            steps=current_steps,
            target_steps=resolved_target_steps,
            average_steps_30d=average_steps_30d,
            days_with_data_30d=days_with_data,
            comment=comment,
        )

    def _hydrate_primary_media_bytes(
        self,
        user_id: int,
        meals: List[DigestMealSnapshot],
    ) -> List[DigestMealSnapshot]:
        primary_media_ids = [
            meal.media_items[0].media_id
            for meal in meals
            if meal.media_items
        ]
        if not primary_media_ids:
            return meals

        hydrated_media = self.store.list_meal_media_by_ids(user_id, primary_media_ids)
        media_by_id = {media.media_id: media for media in hydrated_media}
        hydrated_meals: List[DigestMealSnapshot] = []
        for meal in meals:
            if not meal.media_items:
                hydrated_meals.append(meal)
                continue
            primary_media = media_by_id.get(meal.media_items[0].media_id)
            if primary_media is None:
                hydrated_meals.append(meal)
                continue
            primary_media = self._hydrate_media_bytes(primary_media)
            hydrated_meals.append(
                DigestMealSnapshot(
                    meal_entry_id=meal.meal_entry_id,
                    occurred_at=meal.occurred_at,
                    title=meal.title,
                    calories=meal.calories,
                    protein_g=meal.protein_g,
                    fat_g=meal.fat_g,
                    carbs_g=meal.carbs_g,
                    water_ml=meal.water_ml,
                    media_items=[primary_media],
                )
            )
        return hydrated_meals

    def _hydrate_weekly_highlight_media(
        self,
        user_id: int,
        highlights: List[WeeklyDigestHighlight],
    ) -> List[WeeklyDigestHighlight]:
        meals_to_hydrate = [
            highlight.meal
            for highlight in highlights
            if highlight.meal is not None
        ]
        hydrated_by_entry_id = {
            meal.meal_entry_id: meal
            for meal in self._hydrate_primary_media_bytes(user_id, meals_to_hydrate)
        }
        hydrated_highlights: List[WeeklyDigestHighlight] = []
        for highlight in highlights:
            if highlight.meal is None:
                hydrated_highlights.append(highlight)
                continue
            hydrated_highlights.append(
                WeeklyDigestHighlight(
                    digest_date=highlight.digest_date,
                    meal=hydrated_by_entry_id.get(highlight.meal.meal_entry_id, highlight.meal),
                    score=highlight.score,
                    reason=highlight.reason,
                )
            )
        return hydrated_highlights

    def _hydrate_media_bytes(self, media: MealMedia) -> MealMedia:
        if media.image_bytes or not media.storage_key:
            return media
        image_bytes = self.media_storage.load_image(object_key=media.storage_key)
        return MealMedia(
            media_id=media.media_id,
            user_id=media.user_id,
            draft_id=media.draft_id,
            occurred_at=media.occurred_at,
            created_at=media.created_at,
            mime_type=media.mime_type,
            telegram_file_id=media.telegram_file_id,
            telegram_unique_id=media.telegram_unique_id,
            byte_size=media.byte_size,
            sha256=media.sha256,
            image_bytes=image_bytes,
            meal_entry_id=media.meal_entry_id,
            storage_kind=media.storage_kind,
            storage_key=media.storage_key,
            bucket_name=media.bucket_name,
            width=media.width,
            height=media.height,
        )

    def _safe_hydrate_media_bytes(self, media: MealMedia, *, context: str) -> MealMedia:
        try:
            return self._hydrate_media_bytes(media)
        except Exception as exc:
            logger.warning(
                "Failed to hydrate meal media bytes context=%s media_id=%s storage_key=%s error=%s",
                context,
                media.media_id,
                media.storage_key,
                exc,
            )
            return media

    def _store_media_in_bucket(
        self,
        *,
        user_id: int,
        media_id: str,
        occurred_at: datetime,
        mime_type: str,
        image_bytes: bytes,
    ) -> Dict[str, object]:
        width = 0
        height = 0
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                width, height = image.size
        except Exception:
            width = 0
            height = 0

        if not self.media_storage.enabled:
            raise RuntimeError("Bucket storage is not configured for meal media uploads.")

        stored = self.media_storage.store_image(
            object_key=self._build_media_storage_key(
                user_id=user_id,
                media_id=media_id,
                occurred_at=occurred_at,
                mime_type=mime_type,
            ),
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        return {
            "storage_kind": stored.storage_kind,
            "storage_key": stored.storage_key,
            "bucket_name": stored.bucket_name,
            "image_bytes": b"",
            "width": width,
            "height": height,
        }

    def _build_media_storage_key(self, *, user_id: int, media_id: str, occurred_at: datetime, mime_type: str) -> str:
        return build_meal_media_object_key(
            key_prefix=getattr(self.media_storage, "key_prefix", "meal-media") or "meal-media",
            user_id=user_id,
            media_id=media_id,
            occurred_at=occurred_at,
            mime_type=mime_type,
        )

    def list_meals(self, user_id: int, target_date: date) -> List[MealEntry]:
        return self.store.list_meals(user_id, target_date)

    def evaluate_day(
        self,
        user_id: int,
        target_date: date,
        now: Optional[datetime] = None,
        debug_timings: Optional[Dict[str, float]] = None,
        use_lightweight_summary: bool = False,
    ) -> List[DecisionLogEntry]:
        current_time = now or datetime.now()
        summary_started_at = time.perf_counter()
        summary = (
            self.store.build_decision_summary(user_id, target_date)
            if use_lightweight_summary
            else self.get_daily_summary(user_id, target_date)
        )
        decision_engine_started_at = time.perf_counter()
        decisions = self.decision_engine.evaluate(summary=summary, now=current_time)
        upsert_started_at = time.perf_counter()
        inserted = self.store.upsert_decisions(user_id, decisions)
        if debug_timings is not None:
            debug_timings.update(
                {
                    "build daily summary": round(decision_engine_started_at - summary_started_at, 2),
                    "decision engine": round(upsert_started_at - decision_engine_started_at, 2),
                    "upsert decisions": round(time.perf_counter() - upsert_started_at, 2),
                }
            )
        return inserted

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
    def _update_meal_notes(raw_notes: str, *, summary: Optional[str] = None) -> str:
        if summary is None:
            return raw_notes
        try:
            payload = json.loads(raw_notes) if raw_notes else {}
        except (TypeError, ValueError):
            payload = {}
        payload["summary"] = summary
        return json.dumps(payload, sort_keys=True)

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

    def _build_photo_draft(
        self,
        *,
        photo_file_id: str,
        photo_unique_id: str,
        image_bytes: bytes,
        mime_type: str,
        occurred_at: Optional[datetime],
        caption: str,
    ) -> MealPhotoDraft:
        analyzed = self.food_photo_analyzer.analyze_photo(
            image_bytes=image_bytes,
            mime_type=mime_type,
            caption=caption,
        )
        is_water_only = self._is_water_only_analysis(analyzed)
        created_at = datetime.now()
        return MealPhotoDraft(
            draft_id=str(uuid4()),
            created_at=created_at,
            occurred_at=occurred_at or created_at,
            title=analyzed.title,
            summary=analyzed.summary,
            calories=analyzed.calories,
            protein_g=analyzed.protein_g,
            fat_g=analyzed.fat_g,
            carbs_g=analyzed.carbs_g,
            confidence=analyzed.confidence,
            photo_file_id=photo_file_id,
            photo_unique_id=photo_unique_id,
            source=WATER_PHOTO_SOURCE if is_water_only else MEAL_PHOTO_SOURCE,
            items=analyzed.items,
            water_ml=analyzed.water_ml,
        )

    def _build_photo_media(
        self,
        *,
        user_id: int,
        draft_id: str,
        meal_entry_id: str,
        occurred_at: datetime,
        created_at: datetime,
        photo_file_id: str,
        photo_unique_id: str,
        image_bytes: bytes,
        mime_type: str,
    ) -> MealMedia:
        media_id = str(uuid4())
        stored_media_payload = self._store_media_in_bucket(
            user_id=user_id,
            media_id=media_id,
            occurred_at=occurred_at,
            mime_type=mime_type,
            image_bytes=image_bytes,
        )
        return MealMedia(
            media_id=media_id,
            user_id=user_id,
            draft_id=draft_id,
            meal_entry_id=meal_entry_id,
            occurred_at=occurred_at,
            created_at=created_at,
            mime_type=mime_type,
            telegram_file_id=photo_file_id,
            telegram_unique_id=photo_unique_id,
            byte_size=len(image_bytes),
            sha256=hashlib.sha256(image_bytes).hexdigest(),
            image_bytes=stored_media_payload["image_bytes"],
            storage_kind=stored_media_payload["storage_kind"],
            storage_key=stored_media_payload["storage_key"],
            bucket_name=stored_media_payload["bucket_name"],
            width=stored_media_payload["width"],
            height=stored_media_payload["height"],
        )

    @staticmethod
    def _is_draft_determined(draft: MealPhotoDraft) -> bool:
        if not draft.title.strip():
            return False
        if draft.summary.strip():
            return True
        if draft.items:
            return True
        return draft.calories > 0 or draft.protein_g > 0 or draft.fat_g > 0 or draft.carbs_g > 0

    @staticmethod
    def _build_meal_entry_from_draft(
        draft: MealPhotoDraft,
        *,
        created_at: Optional[datetime] = None,
        draft_id_override: Optional[str] = None,
    ) -> MealEntry:
        entry_created_at = created_at or datetime.now()
        return MealEntry(
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
                    "draft_id": draft.draft_id if draft_id_override is None else draft_id_override,
                    "summary": draft.summary,
                    "confidence": draft.confidence,
                },
                sort_keys=True,
            ),
            created_at=entry_created_at,
        )

    def _list_photo_meals_for_date(self, user_id: int, target_date: date) -> List[DigestMealSnapshot]:
        meals = self.store.list_meals(user_id, target_date)
        meal_media = self.store.list_meal_media(user_id, target_date=target_date)
        return self._build_photo_meal_snapshots(meals, meal_media)

    @staticmethod
    def _build_photo_meal_snapshots(meals: List[MealEntry], meal_media: List[MealMedia]) -> List[DigestMealSnapshot]:
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
                    water_ml=meal.water_ml,
                    media_items=media_items,
                )
            )
        return snapshots

    def _build_photo_meals_cache(
        self,
        user_id: int,
        start_date: date,
        end_date: date,
        include_image_bytes: bool,
    ) -> Dict[date, List[DigestMealSnapshot]]:
        if end_date < start_date:
            return {}
        meals = self.store.list_meals_in_range(user_id, start_date, end_date)
        meal_media = self.store.list_meal_media_in_range(
            user_id,
            start_date=start_date,
            end_date=end_date,
            include_image_bytes=include_image_bytes,
        )
        meals_by_date: Dict[date, List[MealEntry]] = {}
        media_by_date: Dict[date, List[MealMedia]] = {}
        for meal in meals:
            meals_by_date.setdefault(meal.occurred_at.date(), []).append(meal)
        for media in meal_media:
            media_by_date.setdefault(media.occurred_at.date(), []).append(media)

        cache: Dict[date, List[DigestMealSnapshot]] = {}
        cursor = start_date
        while cursor <= end_date:
            cache[cursor] = self._build_photo_meal_snapshots(
                meals_by_date.get(cursor, []),
                media_by_date.get(cursor, []),
            )
            cursor += timedelta(days=1)
        return cache

    @staticmethod
    def _merge_photo_meal_caches(*caches: Dict[date, List[DigestMealSnapshot]]) -> Dict[date, List[DigestMealSnapshot]]:
        merged: Dict[date, List[DigestMealSnapshot]] = {}
        for cache in caches:
            merged.update(cache)
        return merged

    @staticmethod
    def _list_photo_meals_from_cache(
        photo_meals_cache: Dict[date, List[DigestMealSnapshot]],
        target_date: date,
    ) -> List[DigestMealSnapshot]:
        return list(photo_meals_cache.get(target_date, []))

    @staticmethod
    def _is_water_only_analysis(analyzed) -> bool:
        return analyzed.is_water_only or (
            analyzed.water_ml > 0
            and analyzed.calories <= 0
            and analyzed.protein_g <= 0
            and analyzed.fat_g <= 0
            and analyzed.carbs_g <= 0
        )

    def _collect_photo_meals(self, user_id: int, start_date: date, end_date: date) -> List[DigestMealSnapshot]:
        if end_date < start_date:
            return []
        collected = []
        cursor = start_date
        while cursor <= end_date:
            collected.extend(self._list_photo_meals_for_date(user_id, cursor))
            cursor += timedelta(days=1)
        return collected

    def _collect_photo_meals_from_cache(
        self,
        photo_meals_cache: Dict[date, List[DigestMealSnapshot]],
        start_date: date,
        end_date: date,
    ) -> List[DigestMealSnapshot]:
        if end_date < start_date:
            return []
        collected: List[DigestMealSnapshot] = []
        cursor = start_date
        while cursor <= end_date:
            collected.extend(self._list_photo_meals_from_cache(photo_meals_cache, cursor))
            cursor += timedelta(days=1)
        return collected

    def _build_trend_window(self, user_id: int, digest_date: date, window_days: int) -> DigestTrendWindow:
        start_date = digest_date - timedelta(days=window_days)
        end_date = digest_date - timedelta(days=1)
        return self._build_trend_window_from_cache(
            self._build_photo_meals_cache(
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                include_image_bytes=False,
            ),
            digest_date,
            window_days,
        )

    def _build_trend_window_from_cache(
        self,
        photo_meals_cache: Dict[date, List[DigestMealSnapshot]],
        digest_date: date,
        window_days: int,
    ) -> DigestTrendWindow:
        start_date = digest_date - timedelta(days=window_days)
        end_date = digest_date - timedelta(days=1)
        daily_meals_count: List[int] = []
        daily_calories: List[int] = []
        daily_protein: List[float] = []
        daily_fat: List[float] = []
        daily_carbs: List[float] = []
        cursor = start_date
        while cursor <= end_date:
            meals = self._list_photo_meals_from_cache(photo_meals_cache, cursor)
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
        digest_date: date,
        meals: List[DigestMealSnapshot],
        total_calories: int,
        total_protein_g: float,
        total_fat_g: float,
        total_carbs_g: float,
        trend_windows: List[DigestTrendWindow],
        photo_meals_cache: Dict[date, List[DigestMealSnapshot]],
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
            calories_streak=self._compute_digest_streak_from_cache(photo_meals_cache, digest_date, metric="calories"),
            protein_streak=self._compute_digest_streak_from_cache(photo_meals_cache, digest_date, metric="protein_g"),
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
            baseline_label = (
                "базы последних 7ми дней"
                if primary_comparison.days == 7
                else "базы последних %s дней" % primary_comparison.days
            )
            comparison_text = "Это на %.1f%% %s %s" % (
                abs(primary_comparison.calories_delta_pct or 0.0),
                calories_direction,
                baseline_label,
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
                    "Это %s-й день подряд с тем же направлением по калорийности относительно базы последних 7ми дней."
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
        week_start: date,
        total_meals: int,
        total_calories: int,
        total_protein_g: float,
        daily_calories: List[int],
        daily_protein: List[float],
        highlights: List[WeeklyDigestHighlight],
        late_heavy_dinners_days: int,
        photo_meals_cache: Dict[date, List[DigestMealSnapshot]],
    ) -> WeeklyDigestCommentaryData:
        days_with_meals = len(daily_calories)
        average_daily_calories = round(total_calories / max(days_with_meals, 1), 1) if days_with_meals else 0.0
        average_daily_protein_g = round(total_protein_g / max(days_with_meals, 1), 1) if days_with_meals else 0.0
        comparisons = []
        for window_days in (7, 14, 30):
            baseline = self._build_period_average_from_cache(
                photo_meals_cache,
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
            day_meals = self._list_photo_meals_from_cache(photo_meals_cache, week_start + timedelta(days=offset))
            if day_meals:
                daily_fat_totals.append(sum(meal.fat_g for meal in day_meals))
                daily_carb_totals.append(sum(meal.carbs_g for meal in day_meals))
        variability = {
            "белок": self._coefficient_of_variation(daily_protein),
            "жиры": self._coefficient_of_variation(daily_fat_totals),
            "углеводы": self._coefficient_of_variation(daily_carb_totals),
        }
        most_variable_macro = max(variability, key=variability.get) if (daily_protein or daily_fat_totals or daily_carb_totals) else ""
        previous_week = self._build_period_average_from_cache(
            photo_meals_cache,
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
        return self._build_period_average_from_cache(
            self._build_photo_meals_cache(
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                include_image_bytes=False,
            ),
            start_date=start_date,
            end_date=end_date,
        )

    def _build_period_average_from_cache(
        self,
        photo_meals_cache: Dict[date, List[DigestMealSnapshot]],
        start_date: date,
        end_date: date,
    ) -> Dict[str, float]:
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
            meals = self._list_photo_meals_from_cache(photo_meals_cache, cursor)
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
        return self._compute_digest_streak_from_cache(
            self._build_photo_meals_cache(
                user_id=user_id,
                start_date=digest_date - timedelta(days=20),
                end_date=digest_date,
                include_image_bytes=False,
            ),
            digest_date=digest_date,
            metric=metric,
        )

    def _compute_digest_streak_from_cache(
        self,
        photo_meals_cache: Dict[date, List[DigestMealSnapshot]],
        digest_date: date,
        metric: str,
    ) -> DigestStreak:
        direction = ""
        days = 0
        cursor = digest_date
        while True:
            meals = self._list_photo_meals_from_cache(photo_meals_cache, cursor)
            if not meals:
                break
            current_total = self._metric_total(meals, metric)
            baseline = self._build_period_average_from_cache(
                photo_meals_cache,
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
