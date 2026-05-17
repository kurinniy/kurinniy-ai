import base64
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta

from ai_me.config import TelegramSettings
from ai_me.domain.digest import DailyFoodDigest, DigestMealSnapshot, WeeklyDigestHighlight, WeeklyFoodDigest
from ai_me.domain.finance import FinanceCategoryTotal, FinanceImportResult, FinanceMonthlySummary
from ai_me.domain.food import FoodItemEstimate, MealDraftStatus, MealMedia, MealPhotoDraft, PhotoLogKind, PhotoLogResult, WATER_PHOTO_SOURCE
from ai_me.domain.health import DailyHealthGoals, DailyHealthSummary, MealEntry, StepProgressInsight
from ai_me.domain.health_import import HealthImportFile, HealthImportProvider, HealthImportStatus, UserGoogleDriveSettings
from ai_me.domain.user import AppUser, UserGoal, UserSex, UserStatus
from ai_me.services.food_analysis import OpenAIFoodPhotoAnalyzer
from ai_me.telegram import TelegramHealthBot
from ai_me.version import format_release_date_line, format_version_line


VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2WZgAAAABJRU5ErkJggg=="
)


class DummyHealthService:
    def __init__(self) -> None:
        self.confirmed_draft_ids = []
        self.last_import = None
        self.drive_settings_by_user_id = {}
        self.health_import_files_by_user_id = {}
        self.water_only_photo_mode = False
        self.next_photo_draft = None
        self.logged_water_entries = []
        self.meals_by_user_id = {
            1: self._build_recent_meals(),
            2: self._build_recent_meals(),
            3: self._build_recent_meals(),
        }
        self.meal_drafts_by_user_id = {
            1: self._build_recent_drafts(),
            2: self._build_recent_drafts(),
            3: self._build_recent_drafts(),
        }
        self.users_by_telegram_id = {
            42: AppUser(
                user_id=1,
                telegram_user_id=42,
                chat_id=777,
                username="owner",
                first_name="Owner",
                status=UserStatus.ACTIVE,
                is_admin=True,
                created_at=datetime(2026, 5, 6, 9, 0),
            ),
            77: AppUser(
                user_id=2,
                telegram_user_id=77,
                chat_id=778,
                username="guest",
                first_name="Guest",
                status=UserStatus.ACTIVE,
                is_admin=False,
                created_at=datetime(2026, 5, 6, 9, 30),
            ),
        }

    def _build_meal_draft(self) -> MealPhotoDraft:
        return MealPhotoDraft(
            draft_id="draft-1",
            created_at=datetime(2026, 5, 6, 12, 0),
            occurred_at=datetime(2026, 5, 6, 12, 0),
            title="Chicken rice bowl",
            summary="Rice bowl",
            calories=620,
            protein_g=38,
            fat_g=18,
            carbs_g=71,
            confidence=0.91,
            photo_file_id="file-1",
            photo_unique_id="u-1",
            items=[
                FoodItemEstimate(
                    title="Курица",
                    portion_text="150 г",
                    calories=250,
                    protein_g=31,
                    fat_g=8,
                    carbs_g=0,
                )
            ],
        )

    def _build_recent_meals(self):
        return [
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 12, 40),
                title="Курица с рисом",
                calories=420,
                protein_g=35,
                fat_g=12,
                carbs_g=41,
                notes=json.dumps({"summary": "Курица, рис и овощи"}),
            ),
            MealEntry(
                entry_id="meal-2",
                occurred_at=datetime(2026, 5, 6, 9, 10),
                title="Омлет",
                calories=310,
                protein_g=24,
                fat_g=18,
                carbs_g=4,
                notes=json.dumps({"summary": "Яйца и сыр"}),
            ),
        ]

    def _build_recent_drafts(self):
        pending = self._build_meal_draft()
        confirmed = replace(
            pending,
            draft_id="draft-2",
            created_at=datetime(2026, 5, 6, 11, 20),
            occurred_at=datetime(2026, 5, 6, 11, 20),
            title="Паста с индейкой",
            summary="Паста, индейка и соус",
            status=MealDraftStatus.CONFIRMED,
        )
        rejected = replace(
            pending,
            draft_id="draft-3",
            created_at=datetime(2026, 5, 6, 8, 45),
            occurred_at=datetime(2026, 5, 6, 8, 45),
            title="Тост с авокадо",
            summary="Тост, авокадо и яйцо",
            status=MealDraftStatus.REJECTED,
        )
        return {
            pending.draft_id: pending,
            confirmed.draft_id: confirmed,
            rejected.draft_id: rejected,
        }

    def _build_water_draft(self) -> MealPhotoDraft:
        return MealPhotoDraft(
            draft_id="draft-1",
            created_at=datetime(2026, 5, 6, 12, 0),
            occurred_at=datetime(2026, 5, 6, 12, 0),
            title="Вода",
            summary="Стакан воды",
            calories=0,
            protein_g=0,
            fat_g=0,
            carbs_g=0,
            confidence=0.91,
            photo_file_id="file-1",
            photo_unique_id="u-1",
            source=WATER_PHOTO_SOURCE,
            items=[
                FoodItemEstimate(
                    title="Вода",
                    portion_text="500 мл",
                    calories=0,
                    protein_g=0,
                    fat_g=0,
                    carbs_g=0,
                    water_ml=500,
                )
            ],
            water_ml=500,
        )

    def get_user_by_telegram_user_id(self, telegram_user_id: int):
        return self.users_by_telegram_id.get(telegram_user_id)

    def get_user_by_id(self, user_id: int):
        return next((user for user in self.users_by_telegram_id.values() if user.user_id == user_id), None)

    def sync_user(self, telegram_user_id: int, chat_id: int, username: str = "", first_name: str = ""):
        user = self.users_by_telegram_id.get(telegram_user_id)
        if user is None:
            return None
        return replace(
            user,
            chat_id=chat_id,
            username=username or user.username,
            first_name=first_name or user.first_name,
        )

    def register_user(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: str,
        first_name: str,
        now=None,
    ):
        existing = self.users_by_telegram_id.get(telegram_user_id)
        if existing is not None:
            if existing.status == UserStatus.BLOCKED:
                raise ValueError("Ваш доступ к боту заблокирован.")
            user = replace(
                existing,
                chat_id=chat_id,
                username=username,
                first_name=first_name,
            )
            self.users_by_telegram_id[telegram_user_id] = user
            return user
        user = AppUser(
            user_id=3,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            status=UserStatus.ACTIVE,
            is_admin=False,
            created_at=now,
        )
        self.users_by_telegram_id[telegram_user_id] = user
        return user

    def set_admin_mode(self, user_id: int, enabled: bool):
        for telegram_user_id, user in list(self.users_by_telegram_id.items()):
            if user.user_id != user_id:
                continue
            updated = replace(
                user,
                admin_mode_enabled=enabled if user.is_admin else False,
            )
            self.users_by_telegram_id[telegram_user_id] = updated
            return updated
        raise ValueError("Пользователь не найден.")

    def update_user_about(self, user_id, **kwargs):
        user = self.get_user_by_id(user_id)
        if user is None:
            raise ValueError("Пользователь не найден.")
        updated = replace(user, **kwargs)
        self.users_by_telegram_id[updated.telegram_user_id] = updated
        return updated

    def complete_onboarding(self, user_id: int, *, now=None):
        user = self.get_user_by_id(user_id)
        if user is None:
            raise ValueError("Пользователь не найден.")
        updated = replace(user, onboarding_completed_at=now)
        self.users_by_telegram_id[updated.telegram_user_id] = updated
        return updated

    def update_user_goal_settings(self, user_id, **kwargs):
        user = self.get_user_by_id(user_id)
        if user is None:
            raise ValueError("Пользователь не найден.")
        updated = replace(
            user,
            target_water_ml=kwargs.get("target_water_ml", user.target_water_ml),
            target_protein_g=kwargs.get("target_protein_g", user.target_protein_g),
            target_calories_min=kwargs.get("target_calories_min", user.target_calories_min),
            target_calories_max=kwargs.get("target_calories_max", user.target_calories_max),
        )
        self.users_by_telegram_id[updated.telegram_user_id] = updated
        return updated

    def reset_user_goal_settings(self, user_id):
        user = self.get_user_by_id(user_id)
        if user is None:
            raise ValueError("Пользователь не найден.")
        updated = replace(
            user,
            target_water_ml=2000,
            target_protein_g=120,
            target_calories_min=None,
            target_calories_max=None,
        )
        self.users_by_telegram_id[updated.telegram_user_id] = updated
        return updated

    def update_user_reminders(self, user_id, **kwargs):
        user = self.get_user_by_id(user_id)
        if user is None:
            raise ValueError("Пользователь не найден.")
        updated = replace(
            user,
            reminders_enabled=kwargs.get("reminders_enabled", user.reminders_enabled),
            reminder_meal_logging=kwargs.get("reminder_meal_logging", user.reminder_meal_logging),
            reminder_water=kwargs.get("reminder_water", user.reminder_water),
            reminder_evening_summary=kwargs.get("reminder_evening_summary", user.reminder_evening_summary),
        )
        if not updated.reminders_enabled:
            updated = replace(
                updated,
                reminder_meal_logging=False,
                reminder_water=False,
                reminder_evening_summary=False,
            )
        self.users_by_telegram_id[updated.telegram_user_id] = updated
        return updated

    def get_digest_settings(self, user_id):
        return type(
            "DigestSettings",
            (),
            {
                "timezone_name": "Europe/Moscow",
                "daily_digest_enabled": True,
                "daily_digest_time": "08:00",
                "weekly_digest_enabled": True,
                "weekly_digest_time": "08:00",
            },
        )()

    def set_digest_enabled(self, user_id, enabled: bool):
        return type(
            "DigestSettings",
            (),
            {
                "timezone_name": "Europe/Moscow",
                "daily_digest_enabled": enabled,
                "daily_digest_time": "08:00",
                "weekly_digest_enabled": enabled,
                "weekly_digest_time": "08:00",
            },
        )()

    def google_drive_is_configured(self):
        return True

    def connect_google_drive_folder(self, user_id, folder_input: str, now=None):
        if "denied" in folder_input:
            raise RuntimeError("Нет доступа к папке Google Drive. Проверьте, что папка расшарена на service account, и повторите попытку.")
        settings = UserGoogleDriveSettings(
            user_id=user_id,
            folder_id="folder-123",
            folder_url=folder_input,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        self.drive_settings_by_user_id[user_id] = settings
        return settings

    def get_google_drive_settings(self, user_id):
        return self.drive_settings_by_user_id.get(user_id)

    def set_google_drive_enabled(self, user_id, enabled: bool, now=None):
        current = self.drive_settings_by_user_id[user_id]
        settings = UserGoogleDriveSettings(
            user_id=current.user_id,
            folder_id=current.folder_id,
            folder_url=current.folder_url,
            enabled=enabled,
            created_at=current.created_at,
            updated_at=now,
        )
        self.drive_settings_by_user_id[user_id] = settings
        return settings

    def list_health_import_files(self, user_id, provider=None):
        return self.health_import_files_by_user_id.get(user_id, [])

    def build_daily_food_digest(self, user_id, digest_date, debug_timings=None):
        if debug_timings is not None:
            debug_timings.update(
                {
                    "build_digest_seconds": 1.14,
                    "historical_cache_seconds": 0.27,
                    "digest_day_cache_seconds": 0.08,
                    "cache_merge_seconds": 0.03,
                    "media_hydration_seconds": 0.04,
                    "daily_summary_seconds": 0.05,
                    "trend_windows_seconds": 0.41,
                    "commentary_data_seconds": 0.12,
                    "commentary_text_seconds": 0.02,
                }
            )
        media = MealMedia(
            media_id="media-1",
            user_id=user_id,
            draft_id="draft-1",
            occurred_at=datetime(2026, 5, 6, 12, 0),
            created_at=datetime(2026, 5, 6, 12, 0),
            mime_type="image/jpeg",
            telegram_file_id="file-1",
            telegram_unique_id="u-1",
            byte_size=1234,
            sha256="abc",
            image_bytes=VALID_PNG_BYTES,
            meal_entry_id="meal-1",
        )
        return DailyFoodDigest(
            user_id=user_id,
            digest_date=digest_date,
            meals=[
                DigestMealSnapshot(
                    meal_entry_id="meal-1",
                    occurred_at=datetime(2026, 5, 6, 12, 0),
                    title="Курица с рисом",
                    calories=620,
                    protein_g=38,
                    fat_g=18,
                    carbs_g=71,
                    media_items=[media],
                )
            ],
            total_calories=620,
            total_protein_g=38.0,
            total_fat_g=18.0,
            total_carbs_g=71.0,
            water_ml=1200,
            water_goal_ml=2000,
            steps_goal=10000,
            commentary="Относительно 7 дней калорийность выше среднего.",
        )

    def build_weekly_food_digest(self, user_id, week_start, debug_timings=None):
        if debug_timings is not None:
            debug_timings.update(
                {
                    "build_digest_seconds": 1.25,
                    "baseline_collection_seconds": 0.31,
                    "week_cache_seconds": 0.16,
                    "cache_merge_seconds": 0.04,
                    "week_meals_collection_seconds": 0.42,
                    "highlight_selection_seconds": 0.18,
                    "commentary_data_seconds": 0.09,
                    "commentary_text_seconds": 0.03,
                }
            )
        media = MealMedia(
            media_id="media-1",
            user_id=user_id,
            draft_id="draft-1",
            occurred_at=datetime(2026, 5, 5, 12, 0),
            created_at=datetime(2026, 5, 5, 12, 0),
            mime_type="image/jpeg",
            telegram_file_id="file-1",
            telegram_unique_id="u-1",
            byte_size=1234,
            sha256="abc",
            image_bytes=VALID_PNG_BYTES,
            meal_entry_id="meal-1",
        )
        return WeeklyFoodDigest(
            user_id=user_id,
            week_start=week_start,
            week_end=week_start + __import__("datetime").timedelta(days=6),
            highlights=[
                WeeklyDigestHighlight(
                    digest_date=week_start,
                    meal=DigestMealSnapshot(
                        meal_entry_id="meal-1",
                        occurred_at=datetime(2026, 5, 5, 12, 0),
                        title="Курица с рисом",
                        calories=620,
                        protein_g=38,
                        fat_g=18,
                        carbs_g=71,
                        media_items=[media],
                    ),
                    score=1.2,
                    reason="Выбрано как блюдо с наибольшим отклонением от личной базы по калориям.",
                )
            ],
            total_meals=5,
            total_calories=3100,
            commentary="Самое выделяющееся блюдо недели: Курица с рисом.",
        )

    def log_water(self, user_id, entry) -> None:
        self.logged_water_entries.append((user_id, entry))

    def log_meal(self, user_id, entry) -> None:
        return None

    def log_weight(self, user_id, entry) -> None:
        return None

    def log_sleep(self, user_id, entry) -> None:
        return None

    def log_activity(self, user_id, entry) -> None:
        return None

    def set_goals(self, user_id, goals) -> None:
        return None

    def evaluate_day(self, user_id, target_date, now=None):
        return []

    def get_daily_summary(self, user_id, target_date):
        water_ml = sum(entry.amount_ml for logged_user_id, entry in self.logged_water_entries if logged_user_id == user_id)
        user = self.get_user_by_id(user_id)
        return DailyHealthSummary(
            target_date=target_date,
            meals_count=1,
            calories=620,
            protein_g=38,
            fat_g=18,
            carbs_g=71,
            water_ml=water_ml,
            sleep_hours=0,
            steps=0,
            activity_minutes=0,
            latest_weight_kg=None,
            goals=DailyHealthGoals(
                target_date=target_date,
                water_ml=user.target_water_ml if user is not None else 2000,
                protein_g=user.target_protein_g if user is not None else 120,
            ),
        )

    def list_meals(self, user_id, target_date):
        return list(self.meals_by_user_id.get(user_id, []))

    def list_recent_meals(self, user_id, limit=10, offset=0, lookback_days=365):
        meals = sorted(
            self.meals_by_user_id.get(user_id, []),
            key=lambda meal: meal.created_at or meal.occurred_at,
            reverse=True,
        )
        return meals[offset : offset + limit]

    def get_latest_meal(self, user_id):
        meals = self.list_recent_meals(user_id, limit=1)
        return meals[0] if meals else None

    def get_meal_entry(self, user_id, entry_id, lookback_days=365):
        for meal in self.meals_by_user_id.get(user_id, []):
            if meal.entry_id == entry_id:
                return meal
        raise ValueError("Прием пищи не найден.")

    def update_meal_entry(self, user_id, entry_id, **kwargs):
        for index, meal in enumerate(self.meals_by_user_id.get(user_id, [])):
            if meal.entry_id != entry_id:
                continue
            notes = kwargs.pop("notes", meal.notes)
            summary = kwargs.pop("summary", None)
            if summary is not None:
                try:
                    payload = json.loads(notes) if notes else {}
                except (TypeError, ValueError):
                    payload = {}
                payload["summary"] = summary
                notes = json.dumps(payload)
            updated = replace(meal, notes=notes, **kwargs)
            self.meals_by_user_id[user_id][index] = updated
            return updated
        raise ValueError("Прием пищи не найден.")

    def delete_meal_entry(self, user_id, entry_id):
        meals = self.meals_by_user_id.get(user_id, [])
        for meal in meals:
            if meal.entry_id == entry_id:
                self.meals_by_user_id[user_id] = [item for item in meals if item.entry_id != entry_id]
                return meal
        raise ValueError("Прием пищи не найден.")

    def scale_meal_entry_portion(self, user_id, entry_id, factor):
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

    def build_step_progress_insight(self, user_id, reference_date, target_steps=None):
        return StepProgressInsight(
            reference_date=reference_date,
            steps=6200,
            target_steps=target_steps or 10000,
            average_steps_30d=5400.0,
            days_with_data_30d=30,
            comment="Это на 15% выше вашей средней за последние 30 дней. До цели не хватило 3800 шагов.",
        )

    def list_decisions(self, user_id, status=None, target_date=None):
        return []

    def list_meal_drafts(self, user_id, status=MealDraftStatus.PENDING):
        if self.water_only_photo_mode:
            return [self._build_water_draft()]
        drafts = list(self.meal_drafts_by_user_id.get(user_id, {}).values())
        return [draft for draft in drafts if draft.status == status]

    def list_recent_food_draft_history(self, user_id, limit=10, offset=0):
        drafts = list(self.meal_drafts_by_user_id.get(user_id, {}).values())
        drafts = [draft for draft in drafts if not draft.is_water_only]
        drafts.sort(key=lambda draft: draft.created_at, reverse=True)
        return drafts[offset : offset + limit]

    def get_latest_pending_meal_draft(self, user_id):
        drafts = [
            draft
            for draft in self.meal_drafts_by_user_id.get(user_id, {}).values()
            if draft.status == MealDraftStatus.PENDING and not draft.is_water_only
        ]
        drafts.sort(key=lambda draft: draft.created_at, reverse=True)
        return drafts[0] if drafts else None

    def create_meal_draft_from_photo(self, user_id, **kwargs):
        if self.next_photo_draft is not None:
            draft = self.next_photo_draft
            self.next_photo_draft = None
        else:
            draft = self._build_water_draft() if self.water_only_photo_mode else self._build_meal_draft()
        self.meal_drafts_by_user_id.setdefault(user_id, {})[draft.draft_id] = draft
        return draft

    def get_meal_draft(self, user_id, draft_id):
        if self.water_only_photo_mode:
            return self._build_water_draft()
        return self.meal_drafts_by_user_id[user_id][draft_id]

    def get_meal_draft_any_status(self, user_id, draft_id):
        return self.get_meal_draft(user_id, draft_id)

    def update_meal_draft(self, user_id, draft_id, **kwargs):
        draft = self.get_meal_draft(user_id, draft_id)
        updated = replace(draft, **kwargs)
        self.meal_drafts_by_user_id.setdefault(user_id, {})[draft_id] = updated
        return updated

    def scale_meal_draft_portion(self, user_id, draft_id, factor):
        draft = self.get_meal_draft(user_id, draft_id)
        updated = replace(
            draft,
            calories=max(0, int(round(draft.calories * factor))),
            protein_g=round(draft.protein_g * factor, 1),
            fat_g=round(draft.fat_g * factor, 1),
            carbs_g=round(draft.carbs_g * factor, 1),
            water_ml=max(0, int(round(draft.water_ml * factor))),
        )
        self.meal_drafts_by_user_id.setdefault(user_id, {})[draft_id] = updated
        return updated

    def confirm_meal_draft(self, user_id, draft_id):
        self.confirmed_draft_ids.append((user_id, draft_id))
        if self.water_only_photo_mode:
            return PhotoLogResult(
                entry_id="water-1",
                kind=PhotoLogKind.WATER,
                title="Вода",
                occurred_at=datetime(2026, 5, 6, 12, 0),
                water_ml=500,
            )
        draft = self.meal_drafts_by_user_id[user_id][draft_id]
        self.meal_drafts_by_user_id[user_id][draft_id] = replace(draft, status=MealDraftStatus.CONFIRMED)
        return PhotoLogResult(
            entry_id="meal-1",
            kind=PhotoLogKind.MEAL,
            title=draft.title,
            occurred_at=draft.occurred_at,
            water_ml=draft.water_ml,
        )

    def reject_meal_draft(self, user_id, draft_id):
        if self.water_only_photo_mode:
            return type("Draft", (), {"title": "Вода"})()
        draft = self.meal_drafts_by_user_id[user_id][draft_id]
        self.meal_drafts_by_user_id[user_id][draft_id] = replace(draft, status=MealDraftStatus.REJECTED)
        return draft

    def import_tbank_csv(self, user_id, file_bytes: bytes, source_file_name: str):
        self.last_import = (user_id, file_bytes, source_file_name)
        return FinanceImportResult(
            provider="tbank",
            source_file_name=source_file_name,
            total_rows=2,
            imported_rows=2,
            skipped_rows=0,
            first_operation_at=datetime(2026, 5, 1, 9, 0),
            last_operation_at=datetime(2026, 5, 2, 18, 30),
        )

    def get_finance_monthly_summary(self, user_id, month_start: date):
        return FinanceMonthlySummary(
            month_start=month_start,
            month_end=date(2026, 6, 1),
            transaction_count=4,
            income_total=25000.0,
            expense_total=3000.5,
            net_total=21999.5,
            top_expense_categories=[
                FinanceCategoryTotal(category="Продукты", amount=2300.5, transaction_count=2),
                FinanceCategoryTotal(category="Такси", amount=700.0, transaction_count=1),
            ],
        )


class TelegramHealthBotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DummyHealthService()
        self.bot = TelegramHealthBot(
            service=self.service,
            settings=TelegramSettings(
                bot_token="123:abc",
                allowed_user_ids=frozenset(),
                admin_user_ids=frozenset({42}),
                owner_telegram_user_id=42,
                timezone_name="Europe/Moscow",
                environment_name="staging",
                registration_mode="open",
                mini_app_url="https://staging-mini-app.example.com",
            ),
        )
        self.bot._local_now = lambda: datetime(2026, 5, 6, 12, 42)

    def test_whoami_command_exposes_user_context(self) -> None:
        response = self.bot._route_command("/whoami", chat_id=777, user_id=42, app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Данные Telegram", response)
        self.assertIn("окружение=staging", response)
        self.assertIn("режим_доступа=open", response)
        self.assertIn("app_user_id=1", response)
        self.assertIn("роль=admin", response)
        self.assertIn("режим_админа=включен", response)

    def test_admin_can_switch_to_user_mode_and_back(self) -> None:
        response = self.bot._route_command("/user_mode", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Режим обычного пользователя включен", response)
        self.assertFalse(self.service.users_by_telegram_id[42].has_admin_access)

        whoami = self.bot._route_command("/whoami", chat_id=777, user_id=42, app_user=self.service.users_by_telegram_id[42])
        self.assertIn("роль=user", whoami)
        self.assertIn("режим_админа=выключен", whoami)

        response = self.bot._route_command("/admin_mode", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Режим администратора включен", response)
        self.assertTrue(self.service.users_by_telegram_id[42].has_admin_access)

    def test_regular_user_cannot_switch_mode(self) -> None:
        response = self.bot._route_command("/user_mode", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("только администратору", response)

    def test_help_for_unregistered_user_explains_open_start_flow(self) -> None:
        response = self.bot._route_command("/help")
        self.assertIn(format_version_line(), response)
        self.assertIn(format_release_date_line(), response)
        self.assertIn("Доступ: открыт для всех", response)
        self.assertIn("/start", response)
        self.assertNotIn("/start <invite_code>", response)

    def test_help_for_registered_user_does_not_list_removed_manual_health_commands(self) -> None:
        response = self.bot._route_command("/help", app_user=self.service.users_by_telegram_id[42])
        self.assertIn(format_version_line(), response)
        self.assertIn(format_release_date_line(), response)
        self.assertIn("Mini App: откройте через кнопку меню", response)
        self.assertIn("/connect_drive <folder_url>", response)
        self.assertIn("/drive_status", response)
        self.assertNotIn("/water", response)
        self.assertNotIn("/meal <calories>", response)
        self.assertNotIn("/weight", response)
        self.assertNotIn("/sleep", response)
        self.assertNotIn("/activity", response)
        self.assertNotIn("/goals", response)

    def test_help_for_regular_user_hides_admin_only_features(self) -> None:
        response = self.bot._route_command("/help", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Mini App: откройте через кнопку меню", response)
        self.assertNotIn("/finance_month", response)
        self.assertNotIn("/connect_drive", response)
        self.assertNotIn("/drive_status", response)
        self.assertNotIn("/import_tbank", response)
        self.assertIn("/digest_status", response)
        self.assertIn("/drafts", response)

    def test_help_for_admin_in_user_mode_hides_admin_features_but_keeps_switch_commands(self) -> None:
        self.service.set_admin_mode(1, enabled=False)
        response = self.bot._route_command("/help", app_user=self.service.users_by_telegram_id[42])
        self.assertNotIn("/finance_month", response)
        self.assertNotIn("/connect_drive", response)
        self.assertIn("/admin_mode", response)
        self.assertIn("/user_mode", response)

    def test_start_registers_new_user_without_invite(self) -> None:
        response = self.bot._route_command(
            "/start",
            chat_id=999,
            user_id=999,
            username="newuser",
            first_name="New",
            app_user=None,
        )
        self.assertIn("Фотографируйте еду", response)
        self.assertIn("● ○ ○", response)
        self.assertIn(999, self.service.users_by_telegram_id)

    def test_start_for_existing_user_returns_home_screen(self) -> None:
        response = self.bot._route_command("/start", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Главный экран", response)
        self.assertIn("Добавить еду", response)
        self.assertIn("Добавить воду", response)
        self.assertNotIn("digest", response)
        self.assertNotIn("Бот уже подключен", response)

    def test_menu_for_existing_user_returns_home_screen(self) -> None:
        response = self.bot._route_command("/menu", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Главный экран", response)
        self.assertIn("Ежедневная сводка: включен", response)
        self.assertIn("Недельная сводка: включен", response)
        self.assertIn("Как это работает", response)

    def test_profile_command_opens_profile_home_with_sections(self) -> None:
        response = self.bot._route_command("/profile", app_user=self.service.users_by_telegram_id[77])

        self.assertIn("Профиль", response)
        self.assertIn("Цель по воде: 2.0 л", response)
        self.assertIn("Цель по белку: 120 г", response)
        self.assertIn("Напоминания: выключены", response)

        reply_markup = self.bot._reply_markup_for_response(
            text="/profile",
            original_app_user=self.service.users_by_telegram_id[77],
            reply_user=self.service.users_by_telegram_id[77],
        )
        self.assertIn("profile_goals", reply_markup)
        self.assertIn("profile_reminders", reply_markup)
        self.assertIn("profile_about", reply_markup)

    def test_profile_about_callback_updates_sex_without_losing_existing_fields(self) -> None:
        user = self.service.update_user_about(
            2,
            age_years=31,
            height_cm=176,
            profile_weight_kg=79.5,
            goal=UserGoal.WEIGHT_LOSS,
        )
        self.service.users_by_telegram_id[user.telegram_user_id] = user
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "profile_about_sex:female",
                "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 778, "type": "private"},
                },
            }
        )

        updated = self.service.users_by_telegram_id[77]
        self.assertEqual(updated.sex, UserSex.FEMALE)
        self.assertEqual(updated.age_years, 31)
        self.assertEqual(updated.height_cm, 176)
        self.assertEqual(updated.profile_weight_kg, 79.5)
        self.assertEqual(updated.goal, UserGoal.WEIGHT_LOSS)
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Пол: женщина", calls[1][1]["text"])

    def test_pending_profile_age_input_updates_about_section(self) -> None:
        self.bot._set_pending_profile_edit_state(2, "age")

        response = self.bot._handle_pending_profile_input(
            app_user=self.service.users_by_telegram_id[77],
            raw_text="32",
            normalized_text="32",
        )

        self.assertIsNotNone(response)
        text, reply_markup = response
        self.assertIn("Возраст: 32 лет", text)
        self.assertIn("profile_about_edit:age", reply_markup)
        self.assertEqual(self.service.users_by_telegram_id[77].age_years, 32)
        self.assertNotIn(2, self.bot._pending_profile_edit_states)

    def test_pending_profile_input_keeps_retry_state_after_validation_error(self) -> None:
        self.bot._set_pending_profile_edit_state(2, "height")

        response = self.bot._handle_pending_profile_input(
            app_user=self.service.users_by_telegram_id[77],
            raw_text="20",
            normalized_text="20",
        )

        self.assertIsNotNone(response)
        text, reply_markup = response
        self.assertIn("Рост должен быть в диапазоне", text)
        self.assertIn("profile_about", reply_markup)
        self.assertIn(2, self.bot._pending_profile_edit_states)

        retry_response = self.bot._handle_pending_profile_input(
            app_user=self.service.users_by_telegram_id[77],
            raw_text="176",
            normalized_text="176",
        )
        self.assertIsNotNone(retry_response)
        retry_text, _ = retry_response
        self.assertIn("Рост: 176 см", retry_text)
        self.assertEqual(self.service.users_by_telegram_id[77].height_cm, 176)
        self.assertNotIn(2, self.bot._pending_profile_edit_states)

    def test_pending_profile_goal_input_updates_calorie_range(self) -> None:
        self.bot._set_pending_profile_edit_state(2, "calorie_goal")

        response = self.bot._handle_pending_profile_input(
            app_user=self.service.users_by_telegram_id[77],
            raw_text="1800-2200",
            normalized_text="1800-2200",
        )

        self.assertIsNotNone(response)
        text, reply_markup = response
        self.assertIn("Калории: 1800–2200 ккал", text)
        self.assertIn("profile_goals_reset", reply_markup)
        updated = self.service.users_by_telegram_id[77]
        self.assertEqual(updated.target_calories_min, 1800)
        self.assertEqual(updated.target_calories_max, 2200)

    def test_profile_reminders_toggle_updates_flags(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "profile_reminders_toggle:water",
                "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 778, "type": "private"},
                },
            }
        )

        updated = self.service.users_by_telegram_id[77]
        self.assertTrue(updated.reminders_enabled)
        self.assertTrue(updated.reminder_water)
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Главный переключатель: включен", calls[1][1]["text"])
        self.assertIn("Вода: включено", calls[1][1]["text"])

    def test_start_for_new_user_does_not_use_technical_digest_term(self) -> None:
        response = self.bot._route_command("/start", chat_id=999, user_id=999, username="new", first_name="New")
        self.assertNotIn("digest", response)

    def test_how_it_works_command_returns_onboarding_steps(self) -> None:
        response = self.bot._route_command("/how_it_works", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Как это работает", response)
        self.assertIn("1. Отправьте фото еды", response)
        self.assertIn("4. После этого запись попадет в сводку и историю.", response)

    def test_history_command_returns_history_home(self) -> None:
        response = self.bot._route_command("/history", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("История", response)
        self.assertIn("Исправить последнюю запись", response)
        self.assertIn("Отменить последнюю запись", response)
        self.assertIn("История и правки в приложении", response)
        self.assertNotIn("Неизвестная команда", response)

    def test_history_fix_last_returns_last_meal_card(self) -> None:
        response = self.bot._route_command("/history_fix_last", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Последняя запись", response)
        self.assertIn("Название: Курица с рисом", response)
        self.assertIn("Эту запись еще можно быстро исправить", response)

    def test_history_delete_last_returns_confirmation_prompt(self) -> None:
        response = self.bot._route_command("/history_delete_last", app_user=self.service.users_by_telegram_id[77])
        self.assertEqual(response, "Удалить последнюю запись?")

    def test_history_delete_last_reply_markup_uses_visible_buttons(self) -> None:
        self.bot._route_command("/history_delete_last", app_user=self.service.users_by_telegram_id[77])
        reply_markup = self.bot._reply_markup_for_response(
            text="/history_delete_last",
            original_app_user=self.service.users_by_telegram_id[77],
            reply_user=self.service.users_by_telegram_id[77],
        )
        self.assertIn("Да, удалить", reply_markup)
        self.assertIn("Отмена", reply_markup)
        self.assertIn("\"keyboard\"", reply_markup)
        self.assertNotIn("callback_data", reply_markup)

    def test_history_falls_back_to_app_when_entry_is_outside_recovery_window(self) -> None:
        self.bot._local_now = lambda: datetime(2026, 5, 6, 13, 10)
        response = self.bot._route_command("/history_fix_last", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Полное редактирование доступно в приложении.", response)

    def test_history_app_cta_text_is_human_friendly(self) -> None:
        response = self.bot._route_command("/history_app", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Полная история, редактирование и удаление доступны в приложении.", response)

    def test_history_delete_falls_back_to_app_when_no_recent_entry(self) -> None:
        self.bot._local_now = lambda: datetime(2026, 5, 6, 12, 50)
        response = self.bot._route_command("/history_delete_last", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Быстрое удаление доступно только", response)

    def test_history_fix_last_uses_save_time_not_meal_time(self) -> None:
        self.service.meals_by_user_id[2] = [
            MealEntry(
                entry_id="meal-old-occurrence",
                occurred_at=datetime(2026, 5, 5, 8, 0),
                created_at=datetime(2026, 5, 6, 12, 39),
                title="Поздно сохраненный завтрак",
                calories=300,
                protein_g=20,
                notes='{"summary":"Старое блюдо, сохраненное только что"}',
            ),
            MealEntry(
                entry_id="meal-fresh-occurrence",
                occurred_at=datetime(2026, 5, 6, 12, 20),
                created_at=datetime(2026, 5, 6, 12, 20),
                title="Более раннее сохранение",
                calories=450,
                protein_g=30,
                notes='{"summary":"Обычный обед"}',
            ),
        ]
        self.bot._local_now = lambda: datetime(2026, 5, 6, 12, 40)

        response = self.bot._route_command("/history_fix_last", app_user=self.service.users_by_telegram_id[77])

        self.assertIn("Поздно сохраненный завтрак", response)

    def test_history_delete_last_uses_save_time_window_not_meal_time(self) -> None:
        self.service.meals_by_user_id[2] = [
            MealEntry(
                entry_id="meal-old-occurrence",
                occurred_at=datetime(2026, 5, 5, 8, 0),
                created_at=datetime(2026, 5, 6, 12, 38),
                title="Поздно сохраненный завтрак",
                calories=300,
                protein_g=20,
            ),
        ]
        self.bot._local_now = lambda: datetime(2026, 5, 6, 12, 40)

        response = self.bot._route_command("/history_delete_last", app_user=self.service.users_by_telegram_id[77])

        self.assertEqual(response, "Удалить последнюю запись?")

    def test_pending_history_delete_confirm_deletes_last_meal(self) -> None:
        self.bot._set_pending_last_meal_delete(2, "meal-1")
        response = self.bot._handle_pending_last_meal_delete_input(
            app_user=self.service.users_by_telegram_id[77],
            raw_text="Да, удалить",
            normalized_text="Да, удалить",
        )
        self.assertIsNotNone(response)
        text, reply_markup = response
        self.assertIn("Последняя запись удалена.", text)
        self.assertIn("История и правки в приложении", reply_markup)
        self.assertEqual(len(self.service.meals_by_user_id[2]), 1)

    def test_pending_history_delete_cancel_keeps_last_meal(self) -> None:
        self.bot._set_pending_last_meal_delete(2, "meal-1")
        response = self.bot._handle_pending_last_meal_delete_input(
            app_user=self.service.users_by_telegram_id[77],
            raw_text="Отмена",
            normalized_text="Отмена",
        )
        self.assertIsNotNone(response)
        text, reply_markup = response
        self.assertEqual(text, "Удаление отменено.")
        self.assertIn("Отменить последнюю запись", reply_markup)
        self.assertEqual(len(self.service.meals_by_user_id[2]), 2)

    def test_history_reply_markup_is_used_for_history_commands(self) -> None:
        reply_markup = self.bot._reply_markup_for_response(
            text="/history",
            original_app_user=self.service.users_by_telegram_id[77],
            reply_user=self.service.users_by_telegram_id[77],
        )
        self.assertIn("Исправить последнюю запись", reply_markup)
        self.assertIn("Отменить последнюю запись", reply_markup)
        self.assertIn("История и правки в приложении", reply_markup)

    def test_add_food_command_explains_photo_flow(self) -> None:
        response = self.bot._route_command("/add_food", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Просто отправьте фото еды", response)

    def test_add_water_command_opens_quick_water_flow(self) -> None:
        response = self.bot._route_command("/add_water", app_user=self.service.users_by_telegram_id[77])
        self.assertEqual(response, "Сколько воды добавить?")

    def test_water_presets_save_water_and_return_progress(self) -> None:
        user = self.service.users_by_telegram_id[77]
        cases = (
            ("/water 250", "+250 мл воды добавлено.", "Сегодня: 0.2 / 2.0 л", "Осталось до цели: 1750 мл."),
            ("/water 500", "+500 мл воды добавлено.", "Сегодня: 0.8 / 2.0 л", "Осталось до цели: 1250 мл."),
            ("/water 750", "+750 мл воды добавлено.", "Сегодня: 1.5 / 2.0 л", "Осталось до цели: 500 мл."),
        )
        for command, added_text, today_text, remaining_text in cases:
            with self.subTest(command=command):
                response = self.bot._route_command(command, app_user=user)
                self.assertIn(added_text, response)
                self.assertIn(today_text, response)
                self.assertIn(remaining_text, response)

    def test_water_custom_volume_prompt_and_manual_input(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._route_command("/water_custom", app_user=self.service.users_by_telegram_id[77])
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "330",
                    "chat": {"id": 778, "type": "private"},
                    "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                },
            }
        )

        send_message = next(item for item in messages if item[0] == "sendMessage")
        self.assertIn("+330 мл воды добавлено.", send_message[1]["text"])
        self.assertIn("Сегодня: 0.3 / 2.0 л", send_message[1]["text"])
        self.assertIn("Осталось до цели: 1670 мл.", send_message[1]["text"])

    def test_water_custom_volume_rejects_invalid_input(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._route_command("/water_custom", app_user=self.service.users_by_telegram_id[77])
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "abc",
                    "chat": {"id": 778, "type": "private"},
                    "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                },
            }
        )

        send_message = next(item for item in messages if item[0] == "sendMessage")
        self.assertIn("Нужен объем воды в мл числом от 50 до 3000", send_message[1]["text"])

    def test_water_custom_volume_back_returns_to_home_screen(self) -> None:
        self.bot._route_command("/water_custom", app_user=self.service.users_by_telegram_id[77])
        response = self.bot._route_command("/menu", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Главный экран", response)

    def test_progress_command_returns_summary(self) -> None:
        response = self.bot._route_command("/progress", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Сводка за", response)

    def test_digest_status_command_returns_current_settings(self) -> None:
        response = self.bot._route_command("/digest_status", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Настройки digest", response)
        self.assertIn("Ежедневная сводка: включена в 08:00", response)
        self.assertIn("Недельная сводка: включена по понедельникам в 08:00", response)

    def test_connect_drive_command_saves_folder_for_user(self) -> None:
        response = self.bot._route_command(
            "/connect_drive https://drive.google.com/drive/folders/folder-123",
            app_user=self.service.users_by_telegram_id[42],
        )
        self.assertIn("Папка Google Drive подключена", response)
        self.assertEqual(
            self.service.drive_settings_by_user_id[1].folder_url,
            "https://drive.google.com/drive/folders/folder-123",
        )

    def test_connect_drive_command_returns_access_error_message(self) -> None:
        response = self.bot._route_command(
            "/connect_drive https://drive.google.com/drive/folders/denied-folder",
            app_user=self.service.users_by_telegram_id[42],
        )
        self.assertIn("Нет доступа к папке Google Drive", response)

    def test_drive_status_reports_missing_folder_before_connect(self) -> None:
        response = self.bot._route_command("/drive_status", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Google Drive не подключен", response)
        self.assertIn("/connect_drive <folder_url>", response)

    def test_drive_off_command_disables_import(self) -> None:
        self.service.drive_settings_by_user_id[1] = UserGoogleDriveSettings(
            user_id=1,
            folder_id="folder-123",
            folder_url="https://drive.google.com/drive/folders/folder-123",
            enabled=True,
            created_at=datetime(2026, 5, 7, 8, 0),
            updated_at=datetime(2026, 5, 7, 8, 0),
        )
        response = self.bot._route_command("/drive_off", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Импорт Google Drive выключен", response)
        self.assertFalse(self.service.drive_settings_by_user_id[1].enabled)

    def test_digest_off_command_disables_both_digests(self) -> None:
        response = self.bot._route_command("/digest_off", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Digest выключен", response)
        self.assertIn("Ежедневная сводка: выключена", response)
        self.assertIn("Недельная сводка: выключена", response)

    def test_digest_preview_command_returns_daily_preview(self) -> None:
        response = self.bot._route_command("/digest_preview 2026-05-06", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Daily digest preview за 2026-05-06", response)
        self.assertIn("Вода: 1.2 л / 2 л", response)
        self.assertIn("Список блюд:", response)
        self.assertIn("Курица с рисом", response)
        self.assertIn("Шаги за день: 6200 / 10000", response)
        self.assertIn("Комментарий по шагам:", response)

    def test_digest_preview_for_regular_user_hides_step_block(self) -> None:
        response = self.bot._route_command("/digest_preview 2026-05-06", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Daily digest preview за 2026-05-06", response)
        self.assertIn("Список блюд:", response)
        self.assertNotIn("Шаги за день:", response)
        self.assertNotIn("Комментарий по шагам:", response)

    def test_weekly_digest_preview_command_returns_weekly_preview(self) -> None:
        response = self.bot._route_command(
            "/weekly_digest_preview 2026-05-06",
            app_user=self.service.users_by_telegram_id[42],
        )
        self.assertIn("Weekly digest preview", response)
        self.assertIn("Выделяющиеся блюда по дням:", response)
        self.assertIn("Курица с рисом", response)

    def test_weekly_digest_preview_without_args_returns_current_week_preview(self) -> None:
        self.bot._local_today = lambda: date(2026, 5, 10)
        response = self.bot._route_command(
            "/weekly_digest_preview",
            app_user=self.service.users_by_telegram_id[42],
        )
        self.assertIn("Weekly digest preview за 2026-05-04 — 2026-05-10", response)
        self.assertIn("Выделяющиеся блюда по дням:", response)
        self.assertNotIn("Daily digest preview", response)

    def test_weekly_digest_preview_prev_returns_previous_week_preview(self) -> None:
        self.bot._local_today = lambda: date(2026, 5, 10)
        response = self.bot._route_command(
            "/weekly_digest_preview prev",
            app_user=self.service.users_by_telegram_id[42],
        )
        self.assertIn("Weekly digest preview за 2026-04-27 — 2026-05-03", response)
        self.assertIn("Выделяющиеся блюда по дням:", response)
        self.assertNotIn("Daily digest preview", response)

    def test_digest_preview_update_sends_mosaic_photo_and_text(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        def fake_telegram_api_multipart(method, **kwargs):
            calls.append((method, kwargs))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._telegram_api_multipart = fake_telegram_api_multipart

        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/digest_preview 2026-05-06",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "sendPhoto")
        self.assertEqual(business_calls[1][0], "sendMessage")
        self.assertIn("Daily digest preview за 2026-05-06", business_calls[1][1]["text"])
        self.assertIn("Вода: 1.2 л / 2 л", business_calls[1][1]["text"])
        self.assertIn("Шаги за день: 6200 / 10000", business_calls[1][1]["text"])
        self.assertIn("Отладка:", business_calls[1][1]["text"])
        self.assertIn("Сбор daily digest:", business_calls[1][1]["text"])
        self.assertIn("история за 30 дней:", business_calls[1][1]["text"])
        self.assertIn("текущий день:", business_calls[1][1]["text"])
        self.assertIn("слияние cache:", business_calls[1][1]["text"])
        self.assertIn("загрузка изображений текущего дня:", business_calls[1][1]["text"])
        self.assertIn("daily summary:", business_calls[1][1]["text"])
        self.assertIn("trend windows 7/14/30:", business_calls[1][1]["text"])
        self.assertIn("построение commentary data:", business_calls[1][1]["text"])
        self.assertIn("построение текста:", business_calls[1][1]["text"])
        self.assertIn("построение step insight:", business_calls[1][1]["text"])
        self.assertIn("Рендер изображения:", business_calls[1][1]["text"])
        self.assertIn("Отправка фото в Telegram:", business_calls[1][1]["text"])
        self.assertIn("Полный ответ до отправки текста:", business_calls[1][1]["text"])
        self.assertEqual(business_calls[1][1]["parse_mode"], "Markdown")

    def test_digest_preview_update_for_regular_user_hides_step_block(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        def fake_telegram_api_multipart(method, **kwargs):
            calls.append((method, kwargs))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._telegram_api_multipart = fake_telegram_api_multipart

        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/digest_preview 2026-05-06",
                    "chat": {"id": 778, "type": "private"},
                    "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                },
            }
        )

        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "sendPhoto")
        self.assertEqual(business_calls[1][0], "sendMessage")
        self.assertNotIn("Шаги за день:", business_calls[1][1]["text"])
        self.assertNotIn("Комментарий по шагам:", business_calls[1][1]["text"])
        self.assertNotIn("Отладка:", business_calls[1][1]["text"])
        self.assertEqual(business_calls[1][1]["parse_mode"], "Markdown")

    def test_format_daily_digest_text_groups_meals_by_day_periods(self) -> None:
        digest = DailyFoodDigest(
            user_id=1,
            digest_date=date(2026, 5, 11),
            meals=[
                DigestMealSnapshot(
                    meal_entry_id="meal-1",
                    occurred_at=datetime(2026, 5, 11, 9, 48),
                    title="Завтрак",
                    calories=570,
                    protein_g=10,
                    fat_g=20,
                    carbs_g=30,
                    media_items=[],
                ),
                DigestMealSnapshot(
                    meal_entry_id="meal-2",
                    occurred_at=datetime(2026, 5, 11, 14, 19),
                    title="Обед",
                    calories=850,
                    protein_g=20,
                    fat_g=30,
                    carbs_g=40,
                    media_items=[],
                ),
                DigestMealSnapshot(
                    meal_entry_id="meal-3",
                    occurred_at=datetime(2026, 5, 11, 20, 58),
                    title="Ужин",
                    calories=60,
                    protein_g=5,
                    fat_g=6,
                    carbs_g=7,
                    media_items=[],
                ),
            ],
            total_calories=2230,
            total_protein_g=114.5,
            total_fat_g=100.5,
            total_carbs_g=203.5,
            water_ml=800,
            water_goal_ml=2000,
            steps_goal=10000,
            commentary=(
                "За 2026-05-11 подтверждено 3 блюда по фото на 2230 ккал.\n"
                "Это на 4.7% выше базы последних 7ми дней, при этом белок был на 9.6% выше среднего."
            ),
        )

        text = TelegramHealthBot._format_daily_digest_text(digest, preview=False)

        self.assertIn("**Сводка по еде за 2026-05-11**", text)
        self.assertIn("\n\nБлюд: 3 блюда\n", text)
        self.assertIn("Калории: 2 230", text)
        self.assertIn("Белок: 114.5 г", text)
        self.assertIn("Жиры: 100.5 г", text)
        self.assertIn("Углеводы: 203.5 г", text)
        self.assertIn("Вода: 0.8 л / 2 л", text)
        self.assertIn("утро\n- 09:48 | Завтрак | 570 ккал", text)
        self.assertIn("день\n- 14:19 | Обед | 850 ккал", text)
        self.assertIn("вечер\n- 20:58 | Ужин | 60 ккал", text)
        self.assertNotIn("ночь\n", text)
        self.assertNotIn("не было записей", text)
        self.assertIn("\n\nЗа 2026-05-11 подтверждено 3 блюда по фото на 2230 ккал.", text)

    def test_weekly_digest_preview_update_sends_mosaic_photo_and_text(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        def fake_telegram_api_multipart(method, **kwargs):
            calls.append((method, kwargs))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._telegram_api_multipart = fake_telegram_api_multipart

        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/weekly_digest_preview 2026-05-06",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "sendPhoto")
        self.assertEqual(business_calls[1][0], "sendMessage")
        self.assertIn("Weekly digest preview", business_calls[1][1]["text"])
        self.assertIn("Отладка:", business_calls[1][1]["text"])
        self.assertIn("Сбор weekly digest:", business_calls[1][1]["text"])
        self.assertIn("baseline за 30 дней:", business_calls[1][1]["text"])
        self.assertIn("загрузка недели с изображениями:", business_calls[1][1]["text"])
        self.assertIn("слияние cache:", business_calls[1][1]["text"])
        self.assertIn("сбор блюд по 7 дням:", business_calls[1][1]["text"])
        self.assertIn("выбор highlight по дням:", business_calls[1][1]["text"])
        self.assertIn("построение commentary data:", business_calls[1][1]["text"])
        self.assertIn("построение текста:", business_calls[1][1]["text"])
        self.assertIn("Рендер изображения:", business_calls[1][1]["text"])
        self.assertIn("Отправка фото в Telegram:", business_calls[1][1]["text"])
        self.assertIn("Полный ответ до отправки текста:", business_calls[1][1]["text"])

    def test_weekly_digest_preview_prev_update_sends_previous_week(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        def fake_telegram_api_multipart(method, **kwargs):
            calls.append((method, kwargs))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._telegram_api_multipart = fake_telegram_api_multipart
        self.bot._local_today = lambda: date(2026, 5, 10)

        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/weekly_digest_preview prev",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "sendPhoto")
        self.assertEqual(business_calls[1][0], "sendMessage")
        self.assertIn("Weekly digest preview за 2026-04-27 — 2026-05-03", business_calls[1][1]["text"])

    def test_non_private_chat_is_rejected(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/help",
                    "chat": {"id": -100, "type": "group"},
                    "from": {"id": 42},
                },
            }
        )

        self.assertEqual(messages[0][0], "sendMessage")
        self.assertIn("только в личных сообщениях", messages[0][1]["text"])

    def test_help_message_attaches_reply_keyboard_only_for_registered_user(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/help",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        send_message = next(item for item in messages if item[0] == "sendMessage")
        markup = json.loads(send_message[1]["reply_markup"])
        self.assertEqual(markup["keyboard"][0][0]["text"], "Добавить еду")
        self.assertEqual(markup["keyboard"][0][1]["text"], "Добавить воду")
        self.assertEqual(markup["keyboard"][2][1]["text"], "Как это работает")
        self.assertEqual(markup["keyboard"][4][0]["text"], "Финансы за месяц")
        self.assertEqual(markup["keyboard"][4][1]["text"], "Google Drive")

    def test_user_mode_command_updates_reply_keyboard_immediately(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/user_mode",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        send_message = next(item for item in messages if item[0] == "sendMessage")
        markup = json.loads(send_message[1]["reply_markup"])
        flat = [button["text"] for row in markup["keyboard"] for button in row]
        self.assertNotIn("Финансы за месяц", flat)
        self.assertNotIn("Google Drive", flat)
        self.assertIn("Добавить еду", flat)
        self.assertIn("Прогресс", flat)
        self.assertIn("Как это работает", flat)

    def test_help_message_for_regular_user_hides_admin_buttons(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/help",
                    "chat": {"id": 778, "type": "private"},
                    "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                },
            }
        )

        send_message = next(item for item in messages if item[0] == "sendMessage")
        markup = json.loads(send_message[1]["reply_markup"])
        flat = [button["text"] for row in markup["keyboard"] for button in row]
        self.assertNotIn("Финансы за месяц", flat)
        self.assertNotIn("Google Drive", flat)
        self.assertNotIn("Импорт Т-Банк", flat)
        self.assertIn("Добавить еду", flat)
        self.assertIn("Профиль", flat)
        self.assertIn("Как это работает", flat)

    def test_help_message_for_admin_in_user_mode_hides_admin_buttons(self) -> None:
        self.service.set_admin_mode(1, enabled=False)
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/help",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        send_message = next(item for item in messages if item[0] == "sendMessage")
        markup = json.loads(send_message[1]["reply_markup"])
        flat = [button["text"] for row in markup["keyboard"] for button in row]
        self.assertNotIn("Финансы за месяц", flat)
        self.assertNotIn("Google Drive", flat)
        self.assertNotIn("Импорт Т-Банк", flat)
        self.assertIn("Добавить еду", flat)
        self.assertIn("Как это работает", flat)

    def test_start_message_for_new_user_sends_first_onboarding_step(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        def fake_telegram_api_multipart(method, **kwargs):
            calls.append((method, kwargs))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._telegram_api_multipart = fake_telegram_api_multipart
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/start",
                    "chat": {"id": 999, "type": "private"},
                    "from": {"id": 999, "username": "newuser", "first_name": "New"},
                },
            }
        )

        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "sendPhoto")
        self.assertIn("Фотографируйте еду", business_calls[0][1]["params"]["caption"])
        self.assertIn("● ○ ○", business_calls[0][1]["params"]["caption"])
        self.assertIn("onboarding:next:2", business_calls[0][1]["params"]["reply_markup"])
        self.assertIn("onboarding:skip", business_calls[0][1]["params"]["reply_markup"])

    def test_onboarding_next_sends_second_step_and_deletes_previous_message(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        def fake_telegram_api_multipart(method, **kwargs):
            calls.append((method, kwargs))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._telegram_api_multipart = fake_telegram_api_multipart
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "onboarding:next:2",
                "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 778, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[1][0], "sendPhoto")
        self.assertIn("Каждый день —", calls[1][1]["params"]["caption"])
        self.assertIn("○ ● ○", calls[1][1]["params"]["caption"])
        self.assertEqual(calls[2][0], "deleteMessage")

    def test_onboarding_skip_marks_user_complete_and_opens_home(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "onboarding:skip",
                "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 778, "type": "private"},
                },
            }
        )

        self.assertIsNotNone(self.service.users_by_telegram_id[77].onboarding_completed_at)
        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[1][0], "sendMessage")
        self.assertIn("Главный экран", calls[1][1]["text"])
        self.assertEqual(calls[2][0], "deleteMessage")

    def test_onboarding_start_marks_user_complete_and_opens_home(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "onboarding:start",
                "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 778, "type": "private"},
                },
            }
        )

        self.assertIsNotNone(self.service.users_by_telegram_id[77].onboarding_completed_at)
        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[1][0], "sendMessage")
        self.assertIn("Главный экран", calls[1][1]["text"])

    def test_completed_onboarding_is_not_shown_again_on_start(self) -> None:
        self.service.users_by_telegram_id[77] = replace(
            self.service.users_by_telegram_id[77],
            onboarding_completed_at=datetime(2026, 5, 17, 12, 0),
        )
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/start",
                    "chat": {"id": 778, "type": "private"},
                    "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                },
            }
        )

        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "sendMessage")
        self.assertIn("Главный экран", business_calls[0][1]["text"])

    def test_sync_bot_commands_registers_menu_entries(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._sync_bot_commands()

        self.assertEqual(calls[0][0], "setMyCommands")
        self.assertEqual(calls[1][0], "setMyCommands")
        self.assertNotIn("language_code", calls[0][1])
        self.assertEqual(calls[1][1]["language_code"], "ru")
        commands = json.loads(calls[0][1]["commands"])
        commands_ru = json.loads(calls[1][1]["commands"])
        command_names = [item["command"] for item in commands]
        self.assertEqual(command_names[0], "start")
        self.assertEqual(command_names[1], "menu")
        self.assertIn("connect_drive", command_names)
        self.assertIn("drive_status", command_names)
        self.assertIn("decisions", command_names)
        self.assertIn("digest_status", command_names)
        self.assertIn("digest_on", command_names)
        self.assertIn("digest_off", command_names)
        self.assertIn("digest_preview", command_names)
        self.assertIn("weekly_digest_preview", command_names)
        self.assertIn("drafts", command_names)
        self.assertIn("user_mode", command_names)
        self.assertIn("admin_mode", command_names)
        self.assertIn("whoami", command_names)
        self.assertIn("help", command_names)
        self.assertFalse(any(item["command"] == "create_invite" for item in commands))
        self.assertEqual(command_names, [item["command"] for item in commands_ru])

    def test_sync_mini_app_menu_button_registers_default_commands_globally(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._sync_mini_app_menu_button()

        self.assertEqual(calls[0][0], "setChatMenuButton")
        menu_button = json.loads(calls[0][1]["menu_button"])
        self.assertEqual(menu_button["type"], "web_app")
        self.assertEqual(menu_button["text"], "Открыть приложение")

    def test_sync_mini_app_menu_button_registers_web_app_for_admin_chat(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._sync_mini_app_menu_button(chat_id=777, app_user=self.service.users_by_telegram_id[42])

        self.assertEqual(calls[0][0], "setChatMenuButton")
        self.assertEqual(calls[0][1]["chat_id"], 777)
        menu_button = json.loads(calls[0][1]["menu_button"])
        self.assertEqual(menu_button["type"], "web_app")
        self.assertEqual(menu_button["text"], "Открыть приложение")
        self.assertEqual(menu_button["web_app"]["url"], "https://staging-mini-app.example.com")

    def test_sync_mini_app_menu_button_registers_commands_for_regular_user_chat(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._sync_mini_app_menu_button(chat_id=778, app_user=self.service.users_by_telegram_id[77])

        self.assertEqual(calls[0][0], "setChatMenuButton")
        self.assertEqual(calls[0][1]["chat_id"], 778)
        menu_button = json.loads(calls[0][1]["menu_button"])
        self.assertEqual(menu_button["type"], "web_app")
        self.assertEqual(menu_button["web_app"]["url"], "https://staging-mini-app.example.com")

    def test_document_imports_tbank_csv_in_user_scope(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            if method == "getFile":
                return {"file_path": "docs/tbank.csv"}
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._download_telegram_file = lambda path: (
            b"\xef\xbb\xbf\xd0\x94\xd0\xb0\xd1\x82\xd0\xb0 \xd0\xbe\xd0\xbf\xd0\xb5\xd1\x80\xd0\xb0\xd1\x86\xd0\xb8\xd0\xb8;"
            b"\xd0\xa1\xd1\x83\xd0\xbc\xd0\xbc\xd0\xb0 \xd0\xbf\xd0\xbb\xd0\xb0\xd1\x82\xd0\xb5\xd0\xb6\xd0\xb0;"
            b"\xd0\x9e\xd0\xbf\xd0\xb8\xd1\x81\xd0\xb0\xd0\xbd\xd0\xb8\xd0\xb5\n"
            b"01.05.2026;-1500;\xd0\x9f\xd1\x80\xd0\xbe\xd0\xb4\xd1\x83\xd0\xba\xd1\x82\xd1\x8b\n"
        )

        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                    "document": {
                        "file_id": "file-1",
                        "file_name": "tbank.csv",
                        "mime_type": "text/csv",
                    },
                },
            }
        )

        self.assertEqual(self.service.last_import[0], 1)
        self.assertEqual(self.service.last_import[2], "tbank.csv")
        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "getFile")
        self.assertEqual(business_calls[1][0], "sendMessage")
        self.assertIn("Импорт операций Т-Банка завершен", business_calls[1][1]["text"])

    def test_document_import_is_rejected_for_regular_user(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "chat": {"id": 778, "type": "private"},
                    "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                    "document": {
                        "file_id": "file-1",
                        "file_name": "tbank.csv",
                        "mime_type": "text/csv",
                    },
                },
            }
        )

        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "sendMessage")
        self.assertIn("только администратору", business_calls[0][1]["text"])
        self.assertIsNone(self.service.last_import)

    def test_regular_user_photo_is_rate_limited(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            if method == "getFile":
                return {"file_path": "photos/meal.jpg"}
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._download_telegram_file = lambda path: VALID_PNG_BYTES
        timestamps = iter(
            [
                datetime(2026, 5, 8, 12, 0, 0),
                datetime(2026, 5, 8, 12, 0, 0),
                datetime(2026, 5, 8, 12, 0, 0),
                datetime(2026, 5, 8, 12, 0, 0),
                datetime(2026, 5, 8, 12, 0, 1),
            ]
        )
        self.bot._local_now = lambda: next(timestamps)

        first_update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 778, "type": "private"},
                "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                "photo": [{"file_id": "file-1", "file_unique_id": "u-1", "file_size": 100}],
            },
        }
        second_update = {
            "update_id": 2,
            "message": {
                "chat": {"id": 778, "type": "private"},
                "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                "photo": [{"file_id": "file-2", "file_unique_id": "u-2", "file_size": 100}],
            },
        }

        self.bot._handle_update(first_update)
        self.bot._handle_update(second_update)

        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "getFile")
        self.assertEqual(business_calls[1][0], "sendMessage")
        self.assertEqual(business_calls[2][0], "sendMessage")
        self.assertIn("Подождите 14 сек. перед следующим фото.", business_calls[2][1]["text"])

    def test_regular_user_cannot_send_second_photo_while_first_is_processing(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            if method == "getFile":
                return {"file_path": "photos/meal.jpg"}
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._download_telegram_file = lambda path: VALID_PNG_BYTES
        self.bot._local_now = lambda: datetime(2026, 5, 8, 12, 0, 0)

        second_update = {
            "update_id": 2,
            "message": {
                "chat": {"id": 778, "type": "private"},
                "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                "photo": [{"file_id": "file-2", "file_unique_id": "u-2", "file_size": 100}],
            },
        }

        original_handle_photo_message = self.bot._handle_photo_message

        def nested_handle_photo_message(chat_id, app_user, photo, caption):
            self.bot._handle_update(second_update)
            return original_handle_photo_message(chat_id, app_user, photo, caption)

        self.bot._handle_photo_message = nested_handle_photo_message

        first_update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 778, "type": "private"},
                "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                "photo": [{"file_id": "file-1", "file_unique_id": "u-1", "file_size": 100}],
            },
        }

        self.bot._handle_update(first_update)

        business_calls = [call for call in calls if call[0] != "setChatMenuButton"]
        self.assertEqual(business_calls[0][0], "sendMessage")
        self.assertIn("предыдущее фото еще обрабатывается", business_calls[0][1]["text"])
        self.assertEqual(business_calls[1][0], "getFile")
        self.assertEqual(business_calls[2][0], "sendMessage")


    def test_drafts_command_lists_pending_drafts(self) -> None:
        response = self.bot._route_command("/drafts", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Ожидающие черновики приема пищи", response)
        self.assertIn("draft-1", response)

    def test_confirm_meal_command_confirms_draft_in_user_scope(self) -> None:
        response = self.bot._route_command("/confirm_meal draft-1", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Прием пищи сохранен", response)
        self.assertEqual(self.service.confirmed_draft_ids, [(1, "draft-1")])

    def test_confirm_meal_command_saves_water_when_photo_is_water_only(self) -> None:
        self.service.water_only_photo_mode = True

        response = self.bot._route_command("/confirm_meal draft-1", app_user=self.service.users_by_telegram_id[42])

        self.assertIn("Вода сохранена: 0.5 л.", response)
        self.assertEqual(self.service.confirmed_draft_ids, [(1, "draft-1")])

    def test_confirm_callback_edits_message_and_sends_confirmation(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_confirm:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(self.service.confirmed_draft_ids, [(1, "draft-1")])
        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertEqual(calls[2][0], "sendMessage")
        self.assertIn("Сохранено:", calls[2][1]["text"])
        self.assertIn("Время:", calls[2][1]["text"])
        self.assertIn("Б ", calls[2][1]["text"])
        self.assertIn("meal_saved_cancel", calls[2][1]["reply_markup"])

    def test_confirm_callback_continues_when_callback_answer_fails(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            if method == "answerCallbackQuery":
                raise RuntimeError("telegram 400")
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_confirm:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(self.service.confirmed_draft_ids, [(1, "draft-1")])
        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertEqual(calls[2][0], "sendMessage")

    def test_confirm_callback_uses_water_text_for_water_only_photo(self) -> None:
        self.service.water_only_photo_mode = True
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_confirm:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Вода сохранена", calls[1][1]["text"])
        self.assertEqual(calls[2][0], "sendMessage")
        self.assertIn("Вода сохранена: 0.5 л.", calls[2][1]["text"])

    def test_unregistered_callback_gets_start_prompt(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_confirm:draft-1",
                "from": {"id": 999, "username": "new", "first_name": "New"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 999, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertIn("Сначала отправьте /start", calls[0][1]["text"])

    def test_meal_draft_message_uses_russian_labels(self) -> None:
        draft = self.service.list_meal_drafts(1)[0]
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._send_meal_draft(777, draft)

        self.assertEqual(messages[0][0], "sendMessage")
        text = messages[0][1]["text"]
        self.assertIn("Похоже, это Chicken rice bowl.", text)
        self.assertIn("Сохранить", messages[0][1]["reply_markup"])
        self.assertIn("Изменить", messages[0][1]["reply_markup"])
        self.assertIn("Отмена", messages[0][1]["reply_markup"])
        self.assertIn("Состав:", text)

    def test_water_only_draft_message_uses_water_labels(self) -> None:
        self.service.water_only_photo_mode = True
        draft = self.service.list_meal_drafts(1)[0]
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._send_meal_draft(777, draft)

        text = messages[0][1]["text"]
        self.assertIn("Черновик воды", text)
        self.assertIn("Объем: 0.5 л", text)
        self.assertNotIn("Калории:", text)

    def test_high_confidence_draft_auto_saves_for_registered_user(self) -> None:
        draft = replace(self.service.list_meal_drafts(1)[0], confidence=0.91)
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._send_meal_draft(777, draft, app_user=self.service.users_by_telegram_id[42])

        self.assertEqual(messages[0][0], "sendMessage")
        self.assertIn("Сохранено:", messages[0][1]["text"])
        self.assertIn("Изменить", messages[0][1]["reply_markup"])
        self.assertIn("Отмена", messages[0][1]["reply_markup"])
        self.assertNotIn("Сохранить", messages[0][1]["reply_markup"])
        self.assertEqual(self.service.confirmed_draft_ids, [(1, "draft-1")])

    def test_low_confidence_draft_requires_manual_save(self) -> None:
        draft = replace(
            self.service.list_meal_drafts(1)[0],
            confidence=0.42,
        )
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._send_meal_draft(777, draft, app_user=self.service.users_by_telegram_id[42])

        self.assertEqual(messages[0][0], "sendMessage")
        self.assertIn("не до конца уверен", messages[0][1]["text"])
        self.assertIn("Сохранить", messages[0][1]["reply_markup"])
        self.assertIn("Изменить", messages[0][1]["reply_markup"])
        self.assertIn("Отмена", messages[0][1]["reply_markup"])
        self.assertEqual(self.service.confirmed_draft_ids, [])

    def test_edit_callback_opens_edit_menu(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_edit_menu:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Что изменить", calls[1][1]["text"])
        self.assertIn("Название", calls[1][1]["reply_markup"])
        self.assertIn("Порция", calls[1][1]["reply_markup"])
        self.assertIn("Время", calls[1][1]["reply_markup"])
        self.assertIn("Состав", calls[1][1]["reply_markup"])
        self.assertIn("Калории и БЖУ", calls[1][1]["reply_markup"])

    def test_history_last_meal_edit_callback_opens_edit_menu(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "history_last_meal_edit:meal-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Что изменить в последней записи", calls[1][1]["text"])
        self.assertIn("Название", calls[1][1]["reply_markup"])
        self.assertIn("Порция", calls[1][1]["reply_markup"])
        self.assertIn("Время", calls[1][1]["reply_markup"])
        self.assertIn("Калории и БЖУ", calls[1][1]["reply_markup"])

    def test_history_last_meal_delete_prompt_callback_opens_confirmation(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "history_last_meal_delete_prompt:meal-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertEqual(calls[1][1]["text"], "Удалить последнюю запись?")
        self.assertIn("Да, удалить", calls[1][1]["reply_markup"])
        self.assertIn("Отмена", calls[1][1]["reply_markup"])

    def test_history_last_meal_delete_confirm_callback_deletes_entry(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "history_last_meal_delete_confirm:meal-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Последняя запись удалена.", calls[1][1]["text"])
        self.assertEqual(len(self.service.meals_by_user_id[1]), 1)

    def test_history_last_meal_delete_cancel_callback_keeps_entry(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "history_last_meal_delete_cancel:meal-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Последняя запись", calls[1][1]["text"])
        self.assertEqual(len(self.service.meals_by_user_id[1]), 2)

    def test_meal_entry_edit_title_callback_prompts_for_input(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_entry_edit_title:meal-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        send_message = [item for item in calls if item[0] == "sendMessage"][-1]
        self.assertIn("Введите новое название", send_message[1]["text"])

    def test_pending_meal_entry_edit_input_updates_last_meal(self) -> None:
        self.bot._set_pending_draft_edit_state(1, "meal-1", "title", target_type="meal")

        response_text, response_markup = self.bot._handle_pending_draft_edit_input(
            app_user=self.service.users_by_telegram_id[42],
            raw_text="Исправленная курица",
            normalized_text="Исправленная курица",
        )

        self.assertIn("Исправленная курица", response_text)
        self.assertIn("Исправить", response_markup)
        self.assertEqual(self.service.get_meal_entry(1, "meal-1").title, "Исправленная курица")

    def test_history_fix_last_retries_without_markup_when_send_message_fails(self) -> None:
        calls = []
        first_send = {"done": False}

        def fake_telegram_api(method, params):
            calls.append((method, params))
            if method == "sendMessage" and not first_send["done"]:
                first_send["done"] = True
                raise RuntimeError("telegram 400")
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "Исправить последнюю запись",
                    "chat": {"id": 778, "type": "private"},
                    "from": {"id": 77, "username": "guest", "first_name": "Guest"},
                },
            }
        )

        send_calls = [params for method, params in calls if method == "sendMessage"]
        self.assertEqual(len(send_calls), 2)
        self.assertIn("reply_markup", send_calls[0])
        self.assertNotIn("reply_markup", send_calls[1])
        self.assertIn("Последняя запись", send_calls[1]["text"])

    def test_edit_title_flow_updates_draft_card(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_edit_title:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )
        self.bot._handle_update(
            {
                "update_id": 2,
                "message": {
                    "text": "Гречка с курицей",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        send_message = [item for item in messages if item[0] == "sendMessage"][-1]
        self.assertIn("Похоже, это Гречка с курицей.", send_message[1]["text"])
        self.assertEqual(self.service.get_meal_draft(1, "draft-1").title, "Гречка с курицей")

    def test_edit_summary_flow_updates_draft_card(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_edit_summary:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )
        self.bot._handle_update(
            {
                "update_id": 2,
                "message": {
                    "text": "Гречка и куриная грудка",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        send_message = [item for item in messages if item[0] == "sendMessage"][-1]
        self.assertIn("Состав: Гречка и куриная грудка", send_message[1]["text"])
        self.assertEqual(self.service.get_meal_draft(1, "draft-1").summary, "Гречка и куриная грудка")

    def test_edit_portion_flow_updates_calories(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_edit_portion:draft-1:bigger",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("744 ккал", calls[1][1]["text"])

    def test_edit_portion_custom_flow_updates_draft_card(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_edit_portion_custom:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )
        self.bot._handle_update(
            {
                "update_id": 2,
                "message": {
                    "text": "125%",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        send_message = [item for item in messages if item[0] == "sendMessage"][-1]
        self.assertIn("775 ккал", send_message[1]["text"])

    def test_saved_meal_edit_portion_flow_updates_calories(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_entry_edit_portion:meal-1:bigger",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("504 ккал", calls[1][1]["text"])

    def test_saved_meal_edit_portion_custom_flow_updates_card(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_entry_edit_portion_custom:meal-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )
        self.bot._handle_update(
            {
                "update_id": 2,
                "message": {
                    "text": "50%",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        send_message = [item for item in messages if item[0] == "sendMessage"][-1]
        self.assertIn("210 ккал", send_message[1]["text"])

    def test_saved_meal_cancel_deletes_entry(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_saved_cancel:meal-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Сохранение отменено.", calls[1][1]["text"])
        self.assertEqual(len(self.service.meals_by_user_id[1]), 1)

    def test_saved_meal_cancel_falls_back_to_app_when_entry_is_old(self) -> None:
        calls = []
        stale_meal = replace(
            self.service.get_meal_entry(1, "meal-1"),
            created_at=datetime(2026, 5, 6, 9, 0),
        )
        self.service.meals_by_user_id[1][0] = stale_meal

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_saved_cancel:meal-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Быстрое удаление доступно только для самой последней записи", calls[1][1]["text"])

    def test_saved_meal_reply_markup_uses_edit_and_cancel(self) -> None:
        calls = []
        draft = replace(self.service.list_meal_drafts(1)[0], confidence=0.91)

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._send_meal_draft(777, draft, app_user=self.service.users_by_telegram_id[42])

        self.assertIn("history_last_meal_edit", calls[0][1]["reply_markup"])
        self.assertIn("meal_saved_cancel", calls[0][1]["reply_markup"])
        self.assertIn("Отмена", calls[0][1]["reply_markup"])

    def test_edit_time_flow_rejects_invalid_time(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_edit_time:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )
        self.bot._handle_update(
            {
                "update_id": 2,
                "message": {
                    "text": "25:61",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        send_message = [item for item in messages if item[0] == "sendMessage"][-1]
        self.assertIn("Введите время в формате HH:MM", send_message[1]["text"])

    def test_edit_macros_flow_rejects_invalid_numbers(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_edit_macros:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )
        self.bot._handle_update(
            {
                "update_id": 2,
                "message": {
                    "text": "420 thirty 12 46",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        send_message = [item for item in messages if item[0] == "sendMessage"][-1]
        self.assertIn("Калории и БЖУ должны быть числами", send_message[1]["text"])

    def test_not_this_meal_flow_updates_title_and_summary(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_rewrite_prompt:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )
        self.bot._handle_update(
            {
                "update_id": 2,
                "message": {
                    "text": "гречка с куриной грудкой",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        draft = self.service.get_meal_draft(1, "draft-1")
        self.assertEqual(draft.title, "гречка с куриной грудкой")
        self.assertEqual(draft.summary, "гречка с куриной грудкой")

    def test_edit_back_returns_to_draft_card(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_edit_back:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Похоже, это Chicken rice bowl.", calls[1][1]["text"])

    def test_confirm_after_edit_saves_edited_version(self) -> None:
        self.service.update_meal_draft(1, "draft-1", title="Гречка с курицей")

        response = self.bot._route_command("/confirm_meal draft-1", app_user=self.service.users_by_telegram_id[42])

        self.assertIn("Прием пищи сохранен", response)
        self.assertIn("Гречка с курицей", response)

    def test_saved_meal_text_adds_meal_logging_coaching_when_enabled(self) -> None:
        app_user = replace(
            self.service.users_by_telegram_id[42],
            reminders_enabled=True,
            reminder_meal_logging=True,
        )
        meal = self.service.get_meal_entry(1, "meal-1")

        text = self.bot._format_saved_meal_text(app_user, meal)

        self.assertIn("Хорошее начало", text)
        self.assertNotIn("Если нужно, запись можно быстро изменить или отменить.", text)

    def test_saved_meal_text_adds_water_coaching_when_enabled(self) -> None:
        app_user = replace(
            self.service.users_by_telegram_id[42],
            reminders_enabled=True,
            reminder_water=True,
        )
        meal = self.service.get_meal_entry(1, "meal-1")

        text = self.bot._format_saved_meal_text(app_user, meal)

        self.assertIn("воду можно добавить отдельно одним тапом", text)

    def test_food_analysis_parser_handles_markdown_wrapped_json(self) -> None:
        parsed = OpenAIFoodPhotoAnalyzer._parse_json_text(
            """```json
            {
              "title": "Chicken rice bowl",
              "summary": "Rice bowl",
              "calories": 620,
              "protein_g": 38,
              "fat_g": 18,
              "carbs_g": 71,
              "confidence": 0.84,
              "items": []
            }
            ```"""
        )
        self.assertEqual(parsed["title"], "Chicken rice bowl")

    def test_summary_includes_food_breakdown(self) -> None:
        response = self.bot._route_command("/summary 2026-05-06", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Сводка за 2026-05-06", response)
        self.assertIn("Приемы пищи: 1", response)
        self.assertNotIn("Еда:", response)
        self.assertNotIn("12:00 | Курица с рисом", response)
        self.assertNotIn("Б 38.0 / Ж 18.0 / У 71.0", response)
        self.assertNotIn("Активность:", response)
        self.assertNotIn("Вес:", response)
        self.assertIn("Шаги за вчера (2026-05-05): 6200 / 10000", response)
        self.assertIn("30-дневная средняя: 5400.0", response)
        self.assertIn("Комментарий по шагам:", response)

    def test_removed_invite_command_is_unknown(self) -> None:
        response = self.bot._route_command("/create_invite 10 2", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Неизвестная команда", response)

    def test_finance_command_is_rejected_for_regular_user(self) -> None:
        response = self.bot._route_command("/finance_month", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("только администратору", response)

    def test_finance_command_is_rejected_for_admin_in_user_mode(self) -> None:
        self.service.set_admin_mode(1, enabled=False)
        response = self.bot._route_command("/finance_month", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("только администратору", response)

    def test_connect_drive_command_is_rejected_for_regular_user(self) -> None:
        response = self.bot._route_command(
            "/connect_drive https://drive.google.com/drive/folders/folder-123",
            app_user=self.service.users_by_telegram_id[77],
        )
        self.assertIn("только администратору", response)

    def test_water_slash_command_is_supported_as_fallback(self) -> None:
        response = self.bot._route_command("/water 500", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("+500 мл воды добавлено.", response)
        self.assertIn("Сегодня: 0.5 / 2.0 л", response)


if __name__ == "__main__":
    unittest.main()
