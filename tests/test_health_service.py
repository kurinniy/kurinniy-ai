import unittest
from datetime import date, datetime

from ai_me.domain.decision_log import DecisionStatus
from ai_me.domain.digest import DigestStatus, DigestType
from ai_me.domain.food import FoodItemEstimate, MealDraftStatus, MealMedia, MealPhotoDraft, PhotoLogKind, WATER_PHOTO_SOURCE
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    MealEntry,
    SleepEntry,
    WaterEntry,
    WeightEntry,
)
from ai_me.domain.user import UserGoal, UserSex, UserStatus
from ai_me.services.food_analysis import MealAnalysis
from ai_me.services.health_service import HealthService
from ai_me.services.media_storage import DisabledMediaStorage, StoredMediaObject
from ai_me.storage.memory import InMemoryStore


class StubFoodPhotoAnalyzer:
    def analyze_photo(self, image_bytes: bytes, mime_type: str, caption: str = "") -> MealAnalysis:
        return MealAnalysis(
            title="Chicken rice bowl",
            summary="Likely a rice bowl with chicken and vegetables.",
            calories=620,
            protein_g=38,
            fat_g=18,
            carbs_g=71,
            confidence=0.84,
            items=[
                FoodItemEstimate(
                    title="Chicken",
                    portion_text="150 g",
                    calories=250,
                    protein_g=31,
                    fat_g=8,
                    carbs_g=0,
                ),
                FoodItemEstimate(
                    title="Rice",
                    portion_text="220 g",
                    calories=290,
                    protein_g=5,
                    fat_g=1,
                    carbs_g=63,
                ),
                FoodItemEstimate(
                    title="Вода",
                    portion_text="500 мл",
                    calories=0,
                    protein_g=0,
                    fat_g=0,
                    carbs_g=0,
                    water_ml=500,
                ),
            ],
            water_ml=500,
        )


class StubWaterOnlyPhotoAnalyzer:
    def analyze_photo(self, image_bytes: bytes, mime_type: str, caption: str = "") -> MealAnalysis:
        return MealAnalysis(
            title="Вода",
            summary="Стакан воды без еды.",
            calories=0,
            protein_g=0,
            fat_g=0,
            carbs_g=0,
            confidence=0.93,
            items=[
                FoodItemEstimate(
                    title="Вода",
                    portion_text="700 мл",
                    calories=0,
                    protein_g=0,
                    fat_g=0,
                    carbs_g=0,
                    water_ml=700,
                )
            ],
            water_ml=700,
            is_water_only=True,
        )


class FakeMediaStorage:
    def __init__(self, key_prefix: str = "test-media") -> None:
        self._key_prefix = key_prefix
        self.objects = {}
        self.bucket_name = "test-bucket"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def key_prefix(self) -> str:
        return self._key_prefix

    def store_image(self, *, object_key: str, image_bytes: bytes, mime_type: str) -> StoredMediaObject:
        self.objects[object_key] = image_bytes
        return StoredMediaObject(
            storage_kind="railway_bucket",
            storage_key=object_key,
            bucket_name=self.bucket_name,
            byte_size=len(image_bytes),
        )

    def load_image(self, *, object_key: str) -> bytes:
        return self.objects[object_key]


class HealthServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.media_storage = FakeMediaStorage()
        self.service = HealthService(
            self.store,
            food_photo_analyzer=StubFoodPhotoAnalyzer(),
            media_storage=self.media_storage,
            admin_telegram_user_ids=frozenset({96445950}),
        )
        self.user = self.store.create_user(
            telegram_user_id=96445950,
            chat_id=96445950,
            username="owner",
            first_name="Owner",
            status=UserStatus.ACTIVE,
            is_admin=True,
        )
        self.target_date = date(2026, 5, 6)
        self.service.set_goals(
            self.user.user_id,
            DailyHealthGoals(
                target_date=self.target_date,
                water_ml=2400,
                protein_g=140,
                sleep_hours=8.0,
                steps=10000,
            ),
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_daily_summary_aggregates_logged_events(self) -> None:
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 9, 0),
                title="Breakfast",
                calories=550,
                protein_g=35,
            ),
        )
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-2",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                title="Lunch",
                calories=700,
                protein_g=45,
            ),
        )
        self.service.log_water(
            self.user.user_id,
            WaterEntry(
                entry_id="water-1",
                occurred_at=datetime(2026, 5, 6, 10, 0),
                amount_ml=800,
            ),
        )
        self.service.log_sleep(
            self.user.user_id,
            SleepEntry(
                entry_id="sleep-1",
                start_at=datetime(2026, 5, 5, 23, 30),
                end_at=datetime(2026, 5, 6, 6, 30),
                quality_score=4,
            ),
        )
        self.service.log_activity(
            self.user.user_id,
            ActivityEntry(
                entry_id="activity-1",
                occurred_at=datetime(2026, 5, 6, 18, 0),
                title="Walk",
                duration_minutes=45,
                steps=6200,
            ),
        )
        self.service.log_weight(
            self.user.user_id,
            WeightEntry(
                entry_id="weight-1",
                occurred_at=datetime(2026, 5, 6, 8, 0),
                weight_kg=81.4,
            ),
        )

        summary = self.service.get_daily_summary(self.user.user_id, self.target_date)

        self.assertEqual(summary.meals_count, 2)
        self.assertEqual(summary.calories, 1250)
        self.assertEqual(summary.protein_g, 80)
        self.assertEqual(summary.fat_g, 0)
        self.assertEqual(summary.carbs_g, 0)
        self.assertEqual(summary.water_ml, 800)
        self.assertEqual(summary.sleep_hours, 7.0)
        self.assertEqual(summary.steps, 6200)
        self.assertEqual(summary.activity_minutes, 45)
        self.assertEqual(summary.latest_weight_kg, 81.4)
        self.assertEqual(summary.goals.water_ml, 2400)

    def test_update_user_about_preserves_other_fields(self) -> None:
        updated = self.service.update_user_about(
            self.user.user_id,
            sex=UserSex.MALE,
            age_years=32,
            height_cm=181,
            profile_weight_kg=82.5,
            goal=UserGoal.MASS_GAIN,
        )
        updated = self.service.update_user_about(
            self.user.user_id,
            sex=UserSex.FEMALE,
        )

        self.assertEqual(updated.sex, UserSex.FEMALE)
        self.assertEqual(updated.age_years, 32)
        self.assertEqual(updated.height_cm, 181)
        self.assertEqual(updated.profile_weight_kg, 82.5)
        self.assertEqual(updated.goal, UserGoal.MASS_GAIN)

    def test_update_user_goal_settings_preserves_existing_calorie_range(self) -> None:
        updated = self.service.update_user_goal_settings(
            self.user.user_id,
            target_calories_min=1800,
            target_calories_max=2200,
        )
        updated = self.service.update_user_goal_settings(
            self.user.user_id,
            target_water_ml=2300,
        )

        self.assertEqual(updated.target_water_ml, 2300)
        self.assertEqual(updated.target_protein_g, 120)
        self.assertEqual(updated.target_calories_min, 1800)
        self.assertEqual(updated.target_calories_max, 2200)

    def test_update_user_reminders_turns_off_child_flags_with_master_switch(self) -> None:
        updated = self.service.update_user_reminders(
            self.user.user_id,
            reminders_enabled=True,
            reminder_meal_logging=True,
            reminder_water=True,
            reminder_evening_summary=True,
        )
        updated = self.service.update_user_reminders(
            self.user.user_id,
            reminders_enabled=False,
        )

        self.assertFalse(updated.reminders_enabled)
        self.assertFalse(updated.reminder_meal_logging)
        self.assertFalse(updated.reminder_water)
        self.assertFalse(updated.reminder_evening_summary)

    def test_complete_onboarding_sets_timestamp_once(self) -> None:
        finished = self.service.complete_onboarding(self.user.user_id, now=datetime(2026, 5, 17, 12, 0))
        repeated = self.service.complete_onboarding(self.user.user_id, now=datetime(2026, 5, 17, 12, 5))

        self.assertEqual(finished.onboarding_completed_at, datetime(2026, 5, 17, 12, 0))
        self.assertEqual(repeated.onboarding_completed_at, datetime(2026, 5, 17, 12, 0))

    def test_daily_summary_uses_user_goal_defaults_when_daily_goals_are_not_set(self) -> None:
        self.service.update_user_goal_settings(
            self.user.user_id,
            target_water_ml=2600,
            target_protein_g=150,
        )

        summary = self.service.get_daily_summary(self.user.user_id, date(2026, 5, 8))

        self.assertEqual(summary.goals.water_ml, 2600)
        self.assertEqual(summary.goals.protein_g, 150)

    def test_build_step_progress_insight_compares_with_30_day_average_and_target(self) -> None:
        for offset, steps in enumerate([5000, 7000, 6500, 8000]):
            current_date = date(2026, 5, 2 + offset)
            self.service.log_activity(
                self.user.user_id,
                ActivityEntry(
                    entry_id="activity-%s" % offset,
                    occurred_at=datetime.combine(current_date, datetime.min.time()).replace(hour=18),
                    title="Walk",
                    duration_minutes=45,
                    steps=steps,
                ),
            )

        insight = self.service.build_step_progress_insight(self.user.user_id, date(2026, 5, 5))

        self.assertEqual(insight.reference_date, date(2026, 5, 5))
        self.assertEqual(insight.steps, 8000)
        self.assertEqual(insight.target_steps, 10000)
        self.assertEqual(insight.average_steps_30d, 6625.0)
        self.assertEqual(insight.days_with_data_30d, 4)
        self.assertIn("выше вашей средней", insight.comment)
        self.assertIn("До цели не хватило 2000 шагов", insight.comment)

    def test_evaluate_day_creates_idempotent_decisions(self) -> None:
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 11, 30),
                title="Late breakfast",
                calories=600,
                protein_g=30,
            ),
        )
        self.service.log_water(
            self.user.user_id,
            WaterEntry(
                entry_id="water-1",
                occurred_at=datetime(2026, 5, 6, 12, 0),
                amount_ml=600,
            ),
        )
        self.service.log_sleep(
            self.user.user_id,
            SleepEntry(
                entry_id="sleep-1",
                start_at=datetime(2026, 5, 6, 1, 0),
                end_at=datetime(2026, 5, 6, 6, 0),
            ),
        )
        self.service.log_activity(
            self.user.user_id,
            ActivityEntry(
                entry_id="activity-1",
                occurred_at=datetime(2026, 5, 6, 8, 0),
                title="Gym",
                duration_minutes=60,
                steps=2500,
                intensity="high",
            ),
        )

        first_run = self.service.evaluate_day(
            self.user.user_id,
            target_date=self.target_date,
            now=datetime(2026, 5, 6, 16, 30),
        )
        second_run = self.service.evaluate_day(
            self.user.user_id,
            target_date=self.target_date,
            now=datetime(2026, 5, 6, 16, 45),
        )
        decisions = self.service.list_decisions(self.user.user_id, target_date=self.target_date)

        self.assertEqual(len(first_run), 3)
        self.assertEqual(len(second_run), 0)
        self.assertEqual(len(decisions), 3)
        self.assertEqual([bool(decision.payload) for decision in decisions], [True, True, True])

    def test_decision_status_can_be_updated(self) -> None:
        self.service.log_water(
            self.user.user_id,
            WaterEntry(
                entry_id="water-1",
                occurred_at=datetime(2026, 5, 6, 17, 0),
                amount_ml=300,
            ),
        )
        created = self.service.evaluate_day(
            self.user.user_id,
            target_date=self.target_date,
            now=datetime(2026, 5, 6, 18, 0),
        )

        self.assertEqual(len(created), 2)
        decision_id = created[0].decision_id
        self.service.update_decision_status(self.user.user_id, decision_id, DecisionStatus.EXECUTED)

        decisions = self.service.list_decisions(self.user.user_id, target_date=self.target_date)
        statuses = {decision.decision_id: decision.status for decision in decisions}
        self.assertEqual(statuses[decision_id], DecisionStatus.EXECUTED)

    def test_meal_photo_draft_can_be_confirmed_into_meal_log(self) -> None:
        draft = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="telegram-photo-1",
            photo_unique_id="unique-1",
            image_bytes=b"fake-jpeg-data",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 19, 0),
            caption="Dinner",
        )

        self.assertEqual(draft.status, MealDraftStatus.PENDING)
        self.assertEqual(draft.calories, 620)
        self.assertEqual(len(self.service.list_meal_drafts(self.user.user_id)), 1)

        meal = self.service.confirm_meal_draft(self.user.user_id, draft.draft_id)
        summary = self.service.get_daily_summary(self.user.user_id, self.target_date)
        drafts = self.service.list_meal_drafts(self.user.user_id)

        self.assertEqual(meal.kind, PhotoLogKind.MEAL)
        self.assertEqual(meal.title, "Chicken rice bowl")
        self.assertEqual(summary.meals_count, 1)
        self.assertEqual(summary.calories, 620)
        self.assertEqual(summary.protein_g, 38)
        self.assertEqual(summary.fat_g, 18)
        self.assertEqual(summary.carbs_g, 71)
        self.assertEqual(summary.water_ml, 500)
        self.assertEqual(drafts, [])

    def test_meal_water_is_added_to_daily_water_counter(self) -> None:
        self.service.log_water(
            self.user.user_id,
            WaterEntry(
                entry_id="water-1",
                occurred_at=datetime(2026, 5, 6, 9, 0),
                amount_ml=300,
            ),
        )
        draft = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="telegram-photo-1",
            photo_unique_id="unique-1",
            image_bytes=b"fake-jpeg-data",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 19, 0),
            caption="Dinner with water",
        )

        self.service.confirm_meal_draft(self.user.user_id, draft.draft_id)
        summary = self.service.get_daily_summary(self.user.user_id, self.target_date)

        self.assertEqual(summary.water_ml, 800)

    def test_updated_meal_draft_is_saved_into_final_meal(self) -> None:
        draft = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="telegram-photo-1",
            photo_unique_id="unique-1",
            image_bytes=b"fake-jpeg-data",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 19, 0),
            caption="Dinner",
        )

        updated = self.service.update_meal_draft(
            self.user.user_id,
            draft.draft_id,
            title="Гречка с курицей",
            summary="Гречка и куриная грудка",
            occurred_at=datetime(2026, 5, 6, 20, 15),
        )
        meal = self.service.confirm_meal_draft(self.user.user_id, draft.draft_id)
        summary = self.service.get_daily_summary(self.user.user_id, self.target_date)

        self.assertEqual(updated.title, "Гречка с курицей")
        self.assertEqual(meal.title, "Гречка с курицей")
        self.assertEqual(meal.occurred_at, datetime(2026, 5, 6, 20, 15))
        self.assertEqual(summary.meals_count, 1)

    def test_register_user_creates_second_user_and_keeps_data_isolated(self) -> None:
        second_user = self.service.register_user(
            telegram_user_id=111,
            chat_id=111,
            username="guest",
            first_name="Guest",
            now=datetime(2026, 5, 6, 11, 0),
        )

        self.service.log_water(
            self.user.user_id,
            WaterEntry(
                entry_id="owner-water",
                occurred_at=datetime(2026, 5, 6, 12, 0),
                amount_ml=500,
            ),
        )
        self.service.log_water(
            second_user.user_id,
            WaterEntry(
                entry_id="guest-water",
                occurred_at=datetime(2026, 5, 6, 12, 30),
                amount_ml=900,
            ),
        )

        owner_summary = self.service.get_daily_summary(self.user.user_id, self.target_date)
        guest_summary = self.service.get_daily_summary(second_user.user_id, self.target_date)

        self.assertEqual(second_user.status, UserStatus.ACTIVE)
        self.assertEqual(owner_summary.water_ml, 500)
        self.assertEqual(guest_summary.water_ml, 900)
        self.assertNotEqual(self.service.sync_user(111, 111, "guest", "Guest"), None)

    def test_register_user_returns_existing_user_for_repeated_start(self) -> None:
        second_user = self.service.register_user(
            telegram_user_id=222,
            chat_id=222,
            username="guest",
            first_name="Guest",
            now=datetime(2026, 5, 6, 10, 0),
        )
        repeated = self.service.register_user(
            telegram_user_id=222,
            chat_id=333,
            username="guest-updated",
            first_name="Guest Updated",
            now=datetime(2026, 5, 6, 10, 5),
        )

        self.assertEqual(second_user.user_id, repeated.user_id)
        self.assertEqual(repeated.chat_id, 333)
        self.assertEqual(repeated.username, "guest-updated")
        self.assertEqual(repeated.first_name, "Guest Updated")

    def test_register_blocked_user_is_rejected(self) -> None:
        self.store.create_user(
            telegram_user_id=222,
            chat_id=222,
            username="blocked",
            first_name="Blocked",
            status=UserStatus.BLOCKED,
            is_admin=False,
        )

        with self.assertRaises(ValueError):
            self.service.register_user(
                telegram_user_id=222,
                chat_id=222,
                username="bad",
                first_name="Bad",
                now=datetime(2026, 5, 6, 10, 0),
            )

    def test_digest_settings_default_to_enabled_and_can_be_disabled(self) -> None:
        settings = self.service.get_digest_settings(self.user.user_id)

        self.assertTrue(settings.daily_digest_enabled)
        self.assertTrue(settings.weekly_digest_enabled)
        self.assertEqual(settings.daily_digest_time, "08:00")
        self.assertEqual(settings.weekly_digest_time, "08:00")

        updated = self.service.set_digest_enabled(self.user.user_id, enabled=False)

        self.assertFalse(updated.daily_digest_enabled)
        self.assertFalse(updated.weekly_digest_enabled)

    def test_create_digest_run_records_run_for_user(self) -> None:
        run = self.service.create_digest_run(
            self.user.user_id,
            digest_type=DigestType.DAILY,
            digest_date=self.target_date,
            status=DigestStatus.PENDING,
            now=datetime(2026, 5, 7, 8, 0),
        )
        runs = self.service.list_digest_runs(self.user.user_id, digest_type=DigestType.DAILY)

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_id, run.run_id)
        self.assertEqual(runs[0].digest_date, self.target_date)

    def test_meal_photo_draft_stores_media_and_links_it_on_confirmation(self) -> None:
        draft = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="telegram-photo-1",
            photo_unique_id="unique-1",
            image_bytes=b"fake-jpeg-data",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 19, 0),
            caption="Dinner",
        )

        media_before = self.service.list_meal_media(self.user.user_id, target_date=self.target_date)
        self.assertEqual(len(media_before), 1)
        self.assertEqual(media_before[0].draft_id, draft.draft_id)
        self.assertEqual(media_before[0].meal_entry_id, "")

        meal = self.service.confirm_meal_draft(self.user.user_id, draft.draft_id)
        media_after = self.service.list_meal_media(self.user.user_id, target_date=self.target_date)

        self.assertEqual(media_after[0].meal_entry_id, meal.entry_id)
        self.assertEqual(media_after[0].telegram_file_id, "telegram-photo-1")

    def test_meal_photo_draft_uses_bucket_storage_when_configured(self) -> None:
        media_storage = FakeMediaStorage()
        service = HealthService(
            self.store,
            food_photo_analyzer=StubFoodPhotoAnalyzer(),
            media_storage=media_storage,
            admin_telegram_user_ids=frozenset({96445950}),
        )

        draft = service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="telegram-photo-bucket-1",
            photo_unique_id="unique-bucket-1",
            image_bytes=b"fake-jpeg-data",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 19, 0),
            caption="Dinner",
        )
        service.confirm_meal_draft(self.user.user_id, draft.draft_id)

        media = service.list_meal_media(self.user.user_id, target_date=self.target_date)
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0].storage_kind, "railway_bucket")
        self.assertTrue(media[0].storage_key.startswith("test-media/%s/" % self.user.user_id))
        self.assertEqual(media[0].bucket_name, "test-bucket")
        self.assertEqual(media[0].image_bytes, b"")
        self.assertEqual(media_storage.objects[media[0].storage_key], b"fake-jpeg-data")

        digest = service.build_daily_food_digest(self.user.user_id, self.target_date)
        self.assertEqual(digest.meals[0].media_items[0].image_bytes, b"fake-jpeg-data")

    def test_meal_photo_draft_requires_bucket_storage(self) -> None:
        service = HealthService(
            self.store,
            food_photo_analyzer=StubFoodPhotoAnalyzer(),
            media_storage=DisabledMediaStorage(),
            admin_telegram_user_ids=frozenset({96445950}),
        )

        with self.assertRaisesRegex(RuntimeError, "Bucket storage is not configured"):
            service.create_meal_draft_from_photo(
                self.user.user_id,
                photo_file_id="telegram-photo-no-bucket",
                photo_unique_id="unique-no-bucket",
                image_bytes=b"fake-jpeg-data",
                mime_type="image/jpeg",
                occurred_at=datetime(2026, 5, 6, 19, 0),
                caption="Dinner",
            )

    def test_get_primary_meal_media_for_entry_returns_metadata_when_bucket_is_unavailable(self) -> None:
        service = HealthService(
            self.store,
            food_photo_analyzer=StubFoodPhotoAnalyzer(),
            media_storage=DisabledMediaStorage(),
            admin_telegram_user_ids=frozenset({96445950}),
        )
        service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                title="Lunch",
                calories=700,
                protein_g=40,
            ),
        )
        self.store.create_meal_media(
            MealMedia(
                media_id="media-1",
                user_id=self.user.user_id,
                draft_id="draft-1",
                meal_entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                created_at=datetime(2026, 5, 6, 13, 1),
                mime_type="image/jpeg",
                telegram_file_id="file-1",
                telegram_unique_id="unique-1",
                byte_size=120,
                sha256="abc",
                image_bytes=b"",
                storage_kind="railway_bucket",
                storage_key="bucket/path.jpg",
                bucket_name="bucket",
            )
        )

        media = service.get_primary_meal_media_for_entry(self.user.user_id, "meal-1")

        self.assertIsNotNone(media)
        self.assertEqual(media.media_id, "media-1")
        self.assertEqual(media.image_bytes, b"")

    def test_get_primary_meal_media_for_draft_returns_metadata_when_bucket_is_unavailable(self) -> None:
        service = HealthService(
            self.store,
            food_photo_analyzer=StubFoodPhotoAnalyzer(),
            media_storage=DisabledMediaStorage(),
            admin_telegram_user_ids=frozenset({96445950}),
        )
        self.store.create_meal_draft(
            self.user.user_id,
            MealPhotoDraft(
                draft_id="draft-1",
                created_at=datetime(2026, 5, 6, 13, 1),
                occurred_at=datetime(2026, 5, 6, 13, 0),
                title="Lunch",
                summary="Rice and chicken",
                calories=700,
                protein_g=40,
                fat_g=20,
                carbs_g=60,
                confidence=0.8,
                photo_file_id="file-1",
                photo_unique_id="unique-1",
                status=MealDraftStatus.PENDING,
            ),
        )
        self.store.create_meal_media(
            MealMedia(
                media_id="media-1",
                user_id=self.user.user_id,
                draft_id="draft-1",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                created_at=datetime(2026, 5, 6, 13, 1),
                mime_type="image/jpeg",
                telegram_file_id="file-1",
                telegram_unique_id="unique-1",
                byte_size=120,
                sha256="abc",
                image_bytes=b"",
                storage_kind="railway_bucket",
                storage_key="bucket/path.jpg",
                bucket_name="bucket",
            )
        )

        media = service.get_primary_meal_media_for_draft(self.user.user_id, "draft-1")

        self.assertIsNotNone(media)
        self.assertEqual(media.media_id, "media-1")
        self.assertEqual(media.image_bytes, b"")

    def test_range_store_methods_return_expected_meals_and_strip_image_bytes_when_requested(self) -> None:
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-range-1",
                occurred_at=datetime(2026, 5, 4, 9, 0),
                title="Breakfast",
                calories=300,
                protein_g=20,
            ),
        )
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-range-2",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                title="Lunch",
                calories=500,
                protein_g=30,
            ),
        )
        self.store.create_meal_media(
            MealMedia(
                media_id="media-range-1",
                user_id=self.user.user_id,
                draft_id="draft-range-1",
                meal_entry_id="meal-range-1",
                occurred_at=datetime(2026, 5, 4, 9, 0),
                created_at=datetime(2026, 5, 4, 9, 0),
                mime_type="image/jpeg",
                telegram_file_id="file-range-1",
                telegram_unique_id="uniq-range-1",
                byte_size=3,
                sha256="sha-range-1",
                image_bytes=b"abc",
                storage_kind="railway_bucket",
                storage_key="bucket/a.jpg",
                bucket_name="bucket-a",
                width=120,
                height=80,
            )
        )
        self.store.create_meal_media(
            MealMedia(
                media_id="media-range-2",
                user_id=self.user.user_id,
                draft_id="draft-range-2",
                meal_entry_id="meal-range-2",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                created_at=datetime(2026, 5, 6, 13, 0),
                mime_type="image/jpeg",
                telegram_file_id="file-range-2",
                telegram_unique_id="uniq-range-2",
                byte_size=3,
                sha256="sha-range-2",
                image_bytes=b"xyz",
            )
        )

        meals = self.store.list_meals_in_range(self.user.user_id, date(2026, 5, 4), date(2026, 5, 5))
        media = self.store.list_meal_media_in_range(
            self.user.user_id,
            date(2026, 5, 4),
            date(2026, 5, 6),
            include_image_bytes=False,
        )

        self.assertEqual([meal.entry_id for meal in meals], ["meal-range-1"])
        self.assertEqual([item.media_id for item in media], ["media-range-1", "media-range-2"])
        self.assertEqual(media[0].image_bytes, b"")
        self.assertEqual(media[1].image_bytes, b"")
        self.assertEqual(media[0].storage_key, "bucket/a.jpg")
        self.assertEqual(media[0].bucket_name, "bucket-a")
        self.assertEqual(media[0].width, 120)
        self.assertEqual(media[0].height, 80)

    def test_water_only_photo_draft_is_logged_as_water_not_meal(self) -> None:
        self.service.food_photo_analyzer = StubWaterOnlyPhotoAnalyzer()

        draft = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="telegram-water-1",
            photo_unique_id="water-unique-1",
            image_bytes=b"fake-water-jpeg",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 14, 0),
            caption="Water",
        )

        result = self.service.confirm_meal_draft(self.user.user_id, draft.draft_id)
        summary = self.service.get_daily_summary(self.user.user_id, self.target_date)
        meals = self.service.list_meals(self.user.user_id, self.target_date)
        media = self.service.list_meal_media(self.user.user_id, target_date=self.target_date)

        self.assertTrue(draft.is_water_only)
        self.assertEqual(draft.source, WATER_PHOTO_SOURCE)
        self.assertEqual(result.kind, PhotoLogKind.WATER)
        self.assertEqual(result.water_ml, 700)
        self.assertEqual(summary.meals_count, 0)
        self.assertEqual(summary.calories, 0)
        self.assertEqual(summary.water_ml, 700)
        self.assertEqual(meals, [])
        self.assertEqual(media[0].meal_entry_id, "")

    def test_build_daily_food_digest_uses_confirmed_photo_meals_and_trends(self) -> None:
        historical_dates = [date(2026, 5, 3), date(2026, 5, 4), date(2026, 5, 5)]
        for index, current_date in enumerate(historical_dates, start=1):
            draft = self.service.create_meal_draft_from_photo(
                self.user.user_id,
                photo_file_id="file-%s" % index,
                photo_unique_id="uniq-%s" % index,
                image_bytes=("img-%s" % index).encode("utf-8"),
                mime_type="image/jpeg",
                occurred_at=datetime.combine(current_date, datetime.min.time()).replace(hour=12),
                caption="history",
            )
            self.service.confirm_meal_draft(self.user.user_id, draft.draft_id)

        digest = self.service.build_daily_food_digest(self.user.user_id, historical_dates[-1])

        self.assertIsNotNone(digest)
        self.assertEqual(len(digest.meals), 1)
        self.assertEqual(digest.total_calories, 620)
        self.assertEqual(digest.water_ml, 500)
        self.assertEqual(digest.water_goal_ml, 2000)
        self.assertEqual([trend.days for trend in digest.trend_windows], [7, 14, 30])
        self.assertEqual(digest.commentary_data.meal_pattern.largest_meal.title, "Chicken rice bowl")
        self.assertGreaterEqual(len(digest.commentary_data.comparisons), 3)
        self.assertIn("базы последних 7ми дней", digest.commentary)

    def test_build_daily_food_digest_excludes_confirmed_water_only_photos(self) -> None:
        meal_draft = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="meal-file",
            photo_unique_id="meal-uniq",
            image_bytes=b"meal-img",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 12, 0),
            caption="meal",
        )
        self.service.confirm_meal_draft(self.user.user_id, meal_draft.draft_id)

        self.service.food_photo_analyzer = StubWaterOnlyPhotoAnalyzer()
        water_draft = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="water-file",
            photo_unique_id="water-uniq",
            image_bytes=b"water-img",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 15, 0),
            caption="water",
        )
        self.service.confirm_meal_draft(self.user.user_id, water_draft.draft_id)

        digest = self.service.build_daily_food_digest(self.user.user_id, self.target_date)

        self.assertIsNotNone(digest)
        self.assertEqual(len(digest.meals), 1)
        self.assertEqual(digest.meals[0].title, "Chicken rice bowl")
        self.assertEqual(digest.water_ml, 1200)

    def test_build_weekly_food_digest_selects_highlights(self) -> None:
        for offset in range(7):
            current_date = date(2026, 5, 4) + __import__("datetime").timedelta(days=offset)
            draft = self.service.create_meal_draft_from_photo(
                self.user.user_id,
                photo_file_id="week-file-%s" % offset,
                photo_unique_id="week-uniq-%s" % offset,
                image_bytes=("week-img-%s" % offset).encode("utf-8"),
                mime_type="image/jpeg",
                occurred_at=datetime.combine(current_date, datetime.min.time()).replace(hour=12),
                caption="week",
            )
            self.service.confirm_meal_draft(self.user.user_id, draft.draft_id)

        digest = self.service.build_weekly_food_digest(self.user.user_id, date(2026, 5, 4))

        self.assertIsNotNone(digest)
        self.assertEqual(digest.total_meals, 7)
        self.assertEqual(len(digest.highlights), 7)
        self.assertTrue(any(item.meal is not None for item in digest.highlights))
        self.assertEqual(digest.commentary_data.days_with_meals, 7)
        self.assertTrue(digest.commentary_data.patterns.most_variable_macro in {"белок", "жиры", "углеводы"})
        self.assertIn("Самое выделяющееся блюдо недели", digest.commentary)

    def test_list_recent_meals_returns_latest_first(self) -> None:
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 5, 9, 0),
                title="Breakfast",
                calories=450,
                protein_g=25,
            ),
        )
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-2",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                title="Lunch",
                calories=700,
                protein_g=40,
            ),
        )

        meals = self.service.list_recent_meals(self.user.user_id, limit=2, offset=0, lookback_days=3650)

        self.assertEqual([meal.entry_id for meal in meals], ["meal-2", "meal-1"])
        self.assertEqual(self.service.get_meal_entry(self.user.user_id, "meal-1", lookback_days=3650).title, "Breakfast")
        self.assertEqual(self.service.get_latest_meal(self.user.user_id).entry_id, "meal-2")

    def test_get_latest_meal_prefers_last_saved_entry(self) -> None:
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 5, 9, 0),
                created_at=datetime(2026, 5, 6, 13, 5),
                title="Breakfast",
                calories=450,
                protein_g=25,
            ),
        )
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-2",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                created_at=datetime(2026, 5, 6, 12, 0),
                title="Lunch",
                calories=700,
                protein_g=40,
            ),
        )

        self.assertEqual(self.service.get_latest_meal(self.user.user_id).entry_id, "meal-1")

    def test_update_meal_entry_updates_last_saved_meal(self) -> None:
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                title="Lunch",
                calories=700,
                protein_g=40,
                fat_g=20,
                carbs_g=55,
                notes='{"summary":"Old summary"}',
            ),
        )

        updated = self.service.update_meal_entry(
            self.user.user_id,
            "meal-1",
            title="Better lunch",
            summary="New summary",
            calories=680,
        )

        self.assertEqual(updated.title, "Better lunch")
        self.assertEqual(updated.calories, 680)
        self.assertIn("New summary", updated.notes)

    def test_delete_meal_entry_removes_saved_meal(self) -> None:
        self.service.log_meal(
            self.user.user_id,
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                title="Lunch",
                calories=700,
                protein_g=40,
            ),
        )

        deleted = self.service.delete_meal_entry(self.user.user_id, "meal-1")

        self.assertEqual(deleted.entry_id, "meal-1")
        self.assertEqual(self.service.list_recent_meals(self.user.user_id), [])

    def test_list_recent_food_draft_history_includes_all_statuses(self) -> None:
        first = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="file-1",
            photo_unique_id="unique-1",
            image_bytes=b"meal-photo-1",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 9, 0),
        )
        second = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="file-2",
            photo_unique_id="unique-2",
            image_bytes=b"meal-photo-2",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 11, 0),
        )
        third = self.service.create_meal_draft_from_photo(
            self.user.user_id,
            photo_file_id="file-3",
            photo_unique_id="unique-3",
            image_bytes=b"meal-photo-3",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 13, 0),
        )
        self.service.reject_meal_draft(self.user.user_id, first.draft_id)
        self.service.confirm_meal_draft(self.user.user_id, second.draft_id)

        drafts = self.service.list_recent_food_draft_history(self.user.user_id, limit=10, offset=0)

        self.assertEqual({draft.status for draft in drafts}, {MealDraftStatus.PENDING, MealDraftStatus.CONFIRMED, MealDraftStatus.REJECTED})
        self.assertEqual(self.service.get_meal_draft_any_status(self.user.user_id, third.draft_id).status, MealDraftStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
