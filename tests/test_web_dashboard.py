import unittest
from datetime import date, datetime

from ai_me.domain.decision_log import DecisionKind, DecisionLogEntry, DecisionStatus
from ai_me.domain.health import DailyHealthGoals, DailyHealthSummary, MealEntry, StepProgressInsight
from ai_me.domain.health_import import UserGoogleDriveSettings
from ai_me.domain.user import AppUser, UserStatus
from ai_me.web.dashboard import build_dashboard_payload


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

    def get_google_drive_settings(self, user_id: int):
        return UserGoogleDriveSettings(
            user_id=user_id,
            folder_id="folder-123",
            folder_url="https://drive.google.com/drive/folders/folder-123",
            enabled=True,
            created_at=datetime(2026, 5, 7, 8, 0),
            updated_at=datetime(2026, 5, 7, 8, 0),
        )

    def build_step_progress_insight(self, user_id: int, reference_date: date):
        return StepProgressInsight(
            reference_date=reference_date,
            steps=6200,
            target_steps=10000,
            average_steps_30d=5400.0,
            days_with_data_30d=30,
            comment="Вы выше средней за 30 дней, но ниже цели.",
        )


class WebDashboardTest(unittest.TestCase):
    def test_dashboard_payload_contains_only_current_sections(self) -> None:
        payload = build_dashboard_payload(
            service=DashboardServiceStub(),
            app_user=AppUser(
                user_id=1,
                telegram_user_id=96445950,
                chat_id=96445950,
                username="kurinniy",
                first_name="Alexander",
                status=UserStatus.ACTIVE,
                is_admin=True,
                created_at=datetime(2026, 5, 7, 8, 0),
            ),
            target_date=date(2026, 5, 7),
        )

        self.assertIn("summary", payload)
        self.assertIn("decisions", payload)
        self.assertIn("drive", payload)
        self.assertNotIn("finance", payload)
        self.assertNotIn("digest", payload)
        self.assertNotIn("drafts", payload)
        self.assertNotIn("recent_imports", payload["drive"])


if __name__ == "__main__":
    unittest.main()
