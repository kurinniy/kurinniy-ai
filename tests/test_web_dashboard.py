import unittest
from datetime import date, datetime

from ai_me.domain.decision_log import DecisionKind, DecisionLogEntry, DecisionStatus
from ai_me.domain.food import MealDraftStatus, MealPhotoDraft
from ai_me.domain.health import DailyHealthGoals, DailyHealthSummary, MealEntry, StepProgressInsight
from ai_me.domain.user import AppUser, UserStatus
from ai_me.web.dashboard import (
    build_dashboard_payload,
    build_meal_entry_detail_payload,
    build_recognition_detail_payload,
)


class DashboardServiceStub:
    def evaluate_day(self, user_id: int, target_date: date):
        return []

    def get_daily_summary(self, user_id: int, target_date: date):
        return DailyHealthSummary(
            target_date=target_date,
            meals_count=1,
            calories=620,
            protein_g=38.0,
            fat_g=18.0,
            carbs_g=71.0,
            water_ml=1200,
            sleep_hours=7.5,
            steps=8300,
            activity_minutes=45,
            latest_weight_kg=81.2,
            goals=DailyHealthGoals(target_date=target_date),
        )

    def list_meals(self, user_id: int, target_date: date):
        return [
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 7, 12, 0),
                title="Курица с рисом",
                calories=620,
                protein_g=38.0,
                fat_g=18.0,
                carbs_g=71.0,
            )
        ]

    def list_decisions(self, user_id: int, status=None, target_date=None):
        return [
            DecisionLogEntry(
                decision_id="decision-1",
                decision_key="water-low",
                created_at=datetime(2026, 5, 7, 9, 0),
                agent="health",
                kind=DecisionKind.RECOMMENDATION,
                title="Увеличить воду",
                rationale="Воды меньше цели.",
                context_date=target_date or date(2026, 5, 7),
                status=DecisionStatus.OPEN,
            )
        ]

    def build_step_progress_insight(self, user_id: int, reference_date: date):
        return StepProgressInsight(
            reference_date=reference_date,
            steps=6200,
            target_steps=10000,
            average_steps_30d=5400.0,
            days_with_data_30d=30,
            comment="Вы выше средней за 30 дней, но ниже цели.",
        )

    def list_recent_meals(self, user_id: int, limit: int = 40, offset: int = 0, lookback_days: int = 365):
        return [
            MealEntry(
                entry_id="meal-2",
                occurred_at=datetime(2026, 5, 8, 19, 10),
                created_at=datetime(2026, 5, 8, 19, 12),
                title="Паста",
                calories=780,
                protein_g=24.0,
                fat_g=28.0,
                carbs_g=90.0,
                notes='{"summary":"Паста с соусом"}',
            ),
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 7, 12, 0),
                created_at=datetime(2026, 5, 7, 12, 5),
                title="Курица с рисом",
                calories=620,
                protein_g=38.0,
                fat_g=18.0,
                carbs_g=71.0,
                notes='{"summary":"Курица, рис и овощи"}',
            ),
        ][offset : offset + limit]

    def list_recent_food_draft_history(self, user_id: int, limit: int = 40, offset: int = 0):
        return [
            MealPhotoDraft(
                draft_id="draft-1",
                created_at=datetime(2026, 5, 8, 19, 5),
                occurred_at=datetime(2026, 5, 8, 19, 0),
                title="Паста",
                summary="Паста с томатным соусом",
                calories=780,
                protein_g=24.0,
                fat_g=28.0,
                carbs_g=90.0,
                confidence=0.84,
                photo_file_id="file-1",
                photo_unique_id="unique-1",
                status=MealDraftStatus.CONFIRMED,
            ),
            MealPhotoDraft(
                draft_id="draft-2",
                created_at=datetime(2026, 5, 8, 8, 20),
                occurred_at=datetime(2026, 5, 8, 8, 15),
                title="Омлет",
                summary="Омлет с сыром",
                calories=430,
                protein_g=27.0,
                fat_g=30.0,
                carbs_g=6.0,
                confidence=0.76,
                photo_file_id="file-2",
                photo_unique_id="unique-2",
                status=MealDraftStatus.PENDING,
            ),
        ][offset : offset + limit]

    def get_meal_entry(self, user_id: int, entry_id: str, lookback_days: int = 365):
        for meal in self.list_recent_meals(user_id, limit=10, lookback_days=lookback_days):
            if meal.entry_id == entry_id:
                return meal
        raise ValueError("meal not found")

    def get_meal_draft_any_status(self, user_id: int, draft_id: str):
        for draft in self.list_recent_food_draft_history(user_id, limit=10):
            if draft.draft_id == draft_id:
                return draft
        raise ValueError("draft not found")

    def get_primary_meal_media_for_entry(self, user_id: int, entry_id: str):
        return None

    def get_primary_meal_media_for_draft(self, user_id: int, draft_id: str):
        return None


class WebDashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DashboardServiceStub()
        self.app_user = AppUser(
            user_id=1,
            telegram_user_id=96445950,
            chat_id=96445950,
            username="kurinniy",
            first_name="Alexander",
            status=UserStatus.ACTIVE,
            is_admin=True,
            created_at=datetime(2026, 5, 7, 8, 0),
        )

    def test_dashboard_payload_contains_history_and_recognitions(self) -> None:
        payload = build_dashboard_payload(
            service=self.service,
            app_user=self.app_user,
            target_date=date(2026, 5, 7),
        )

        self.assertIn("summary", payload)
        self.assertIn("decisions", payload)
        self.assertIn("history", payload)
        self.assertIn("recognitions", payload)
        self.assertEqual(payload["history"]["days"][0]["entries"][0]["entry_id"], "meal-2")
        self.assertEqual(payload["recognitions"]["items"][0]["status_label"], "Сохранено")
        self.assertNotIn("drive", payload)
        self.assertNotIn("finance", payload)
        self.assertNotIn("digest", payload)
        self.assertNotIn("drafts", payload)

    def test_meal_entry_detail_payload_contains_summary(self) -> None:
        payload = build_meal_entry_detail_payload(
            service=self.service,
            user_id=1,
            entry_id="meal-1",
        )

        self.assertEqual(payload["entry_id"], "meal-1")
        self.assertEqual(payload["summary"], "Курица, рис и овощи")
        self.assertEqual(payload["status_label"], "Сохранено")
        self.assertIsNone(payload["photo_data_url"])

    def test_recognition_detail_payload_contains_status(self) -> None:
        payload = build_recognition_detail_payload(
            service=self.service,
            user_id=1,
            draft_id="draft-2",
        )

        self.assertEqual(payload["draft_id"], "draft-2")
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["status_label"], "Ожидает решения")
        self.assertEqual(payload["summary"], "Омлет с сыром")


if __name__ == "__main__":
    unittest.main()
