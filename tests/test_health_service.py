import unittest
from datetime import date, datetime

from ai_me.domain.food import FoodItemEstimate, MealDraftStatus
from ai_me.domain.decision_log import DecisionStatus
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    MealEntry,
    SleepEntry,
    WaterEntry,
    WeightEntry,
)
from ai_me.services.health_service import HealthService
from ai_me.services.food_analysis import MealAnalysis
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
            ],
        )


class HealthServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.service = HealthService(self.store, food_photo_analyzer=StubFoodPhotoAnalyzer())
        self.target_date = date(2026, 5, 6)
        self.service.set_goals(
            DailyHealthGoals(
                target_date=self.target_date,
                water_ml=2400,
                protein_g=140,
                sleep_hours=8.0,
                steps=10000,
            )
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_daily_summary_aggregates_logged_events(self) -> None:
        self.service.log_meal(
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 9, 0),
                title="Breakfast",
                calories=550,
                protein_g=35,
            )
        )
        self.service.log_meal(
            MealEntry(
                entry_id="meal-2",
                occurred_at=datetime(2026, 5, 6, 13, 0),
                title="Lunch",
                calories=700,
                protein_g=45,
            )
        )
        self.service.log_water(
            WaterEntry(
                entry_id="water-1",
                occurred_at=datetime(2026, 5, 6, 10, 0),
                amount_ml=800,
            )
        )
        self.service.log_sleep(
            SleepEntry(
                entry_id="sleep-1",
                start_at=datetime(2026, 5, 5, 23, 30),
                end_at=datetime(2026, 5, 6, 6, 30),
                quality_score=4,
            )
        )
        self.service.log_activity(
            ActivityEntry(
                entry_id="activity-1",
                occurred_at=datetime(2026, 5, 6, 18, 0),
                title="Walk",
                duration_minutes=45,
                steps=6200,
            )
        )
        self.service.log_weight(
            WeightEntry(
                entry_id="weight-1",
                occurred_at=datetime(2026, 5, 6, 8, 0),
                weight_kg=81.4,
            )
        )

        summary = self.service.get_daily_summary(self.target_date)

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

    def test_evaluate_day_creates_idempotent_decisions(self) -> None:
        self.service.log_meal(
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 11, 30),
                title="Late breakfast",
                calories=600,
                protein_g=30,
            )
        )
        self.service.log_water(
            WaterEntry(
                entry_id="water-1",
                occurred_at=datetime(2026, 5, 6, 12, 0),
                amount_ml=600,
            )
        )
        self.service.log_sleep(
            SleepEntry(
                entry_id="sleep-1",
                start_at=datetime(2026, 5, 6, 1, 0),
                end_at=datetime(2026, 5, 6, 6, 0),
            )
        )
        self.service.log_activity(
            ActivityEntry(
                entry_id="activity-1",
                occurred_at=datetime(2026, 5, 6, 8, 0),
                title="Gym",
                duration_minutes=60,
                steps=2500,
                intensity="high",
            )
        )

        first_run = self.service.evaluate_day(
            target_date=self.target_date,
            now=datetime(2026, 5, 6, 16, 30),
        )
        second_run = self.service.evaluate_day(
            target_date=self.target_date,
            now=datetime(2026, 5, 6, 16, 45),
        )
        decisions = self.service.list_decisions(target_date=self.target_date)

        self.assertEqual(len(first_run), 3)
        self.assertEqual(len(second_run), 0)
        self.assertEqual(len(decisions), 3)
        self.assertEqual(
            [bool(decision.payload) for decision in decisions],
            [True, True, True],
        )

    def test_decision_status_can_be_updated(self) -> None:
        self.service.log_water(
            WaterEntry(
                entry_id="water-1",
                occurred_at=datetime(2026, 5, 6, 17, 0),
                amount_ml=300,
            )
        )
        created = self.service.evaluate_day(
            target_date=self.target_date,
            now=datetime(2026, 5, 6, 18, 0),
        )

        self.assertEqual(len(created), 2)
        decision_id = created[0].decision_id
        self.service.update_decision_status(decision_id, DecisionStatus.EXECUTED)

        decisions = self.service.list_decisions(target_date=self.target_date)
        statuses = {decision.decision_id: decision.status for decision in decisions}
        self.assertEqual(statuses[decision_id], DecisionStatus.EXECUTED)

    def test_meal_photo_draft_can_be_confirmed_into_meal_log(self) -> None:
        draft = self.service.create_meal_draft_from_photo(
            photo_file_id="telegram-photo-1",
            photo_unique_id="unique-1",
            image_bytes=b"fake-jpeg-data",
            mime_type="image/jpeg",
            occurred_at=datetime(2026, 5, 6, 19, 0),
            caption="Dinner",
        )

        self.assertEqual(draft.status, MealDraftStatus.PENDING)
        self.assertEqual(draft.calories, 620)
        self.assertEqual(len(self.service.list_meal_drafts()), 1)

        meal = self.service.confirm_meal_draft(draft.draft_id)
        summary = self.service.get_daily_summary(self.target_date)
        drafts = self.service.list_meal_drafts()

        self.assertEqual(meal.title, "Chicken rice bowl")
        self.assertEqual(summary.meals_count, 1)
        self.assertEqual(summary.calories, 620)
        self.assertEqual(summary.protein_g, 38)
        self.assertEqual(summary.fat_g, 18)
        self.assertEqual(summary.carbs_g, 71)
        self.assertEqual(drafts, [])


if __name__ == "__main__":
    unittest.main()
