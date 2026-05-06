import unittest

from datetime import date

from ai_me.domain.food import MealDraftStatus, MealPhotoDraft
from ai_me.domain.health import DailyHealthGoals, DailyHealthSummary
from ai_me.config import TelegramSettings
from ai_me.services.food_analysis import OpenAIFoodPhotoAnalyzer
from ai_me.telegram import TelegramHealthBot


class DummyHealthService:
    def __init__(self) -> None:
        self.confirmed_draft_ids = []

    def log_water(self, entry) -> None:
        return None

    def log_meal(self, entry) -> None:
        return None

    def log_weight(self, entry) -> None:
        return None

    def log_sleep(self, entry) -> None:
        return None

    def log_activity(self, entry) -> None:
        return None

    def set_goals(self, goals) -> None:
        return None

    def evaluate_day(self, target_date, now=None):
        return []

    def get_daily_summary(self, target_date):
        return DailyHealthSummary(
            target_date=target_date,
            meals_count=0,
            calories=0,
            protein_g=0,
            fat_g=0,
            carbs_g=0,
            water_ml=0,
            sleep_hours=0,
            steps=0,
            activity_minutes=0,
            latest_weight_kg=None,
            goals=DailyHealthGoals(target_date=target_date),
        )

    def list_decisions(self, status=None, target_date=None):
        return []

    def list_meal_drafts(self, status=MealDraftStatus.PENDING):
        return [
            MealPhotoDraft(
                draft_id="draft-1",
                created_at=__import__("datetime").datetime(2026, 5, 6, 12, 0),
                occurred_at=__import__("datetime").datetime(2026, 5, 6, 12, 0),
                title="Chicken rice bowl",
                summary="Rice bowl",
                calories=620,
                protein_g=38,
                fat_g=18,
                carbs_g=71,
                confidence=0.84,
                photo_file_id="file-1",
                photo_unique_id="u-1",
            )
        ]

    def confirm_meal_draft(self, draft_id):
        self.confirmed_draft_ids.append(draft_id)
        return type(
            "Meal",
            (),
            {
                "title": "Chicken rice bowl",
                "occurred_at": __import__("datetime").datetime(2026, 5, 6, 12, 0),
            },
        )()

    def reject_meal_draft(self, draft_id):
        return type("Draft", (), {"title": "Chicken rice bowl"})()


class TelegramHealthBotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DummyHealthService()
        self.bot = TelegramHealthBot(
            service=self.service,
            settings=TelegramSettings(
                bot_token="123:abc",
                allowed_user_ids=frozenset(),
                timezone_name="Europe/Moscow",
            ),
        )

    def test_whoami_command_exposes_ids(self) -> None:
        response = self.bot._route_command("/whoami", chat_id=777, user_id=42)
        self.assertIn("user_id=42", response)
        self.assertIn("chat_id=777", response)
        self.assertIn("allowlist=disabled", response)

    def test_help_lists_whoami_command(self) -> None:
        response = self.bot._route_command("/help")
        self.assertIn("/whoami", response)
        self.assertIn("Send a food photo", response)

    def test_drafts_command_lists_pending_drafts(self) -> None:
        response = self.bot._route_command("/drafts")
        self.assertIn("Pending meal drafts", response)
        self.assertIn("draft-1", response)

    def test_confirm_meal_command_confirms_draft(self) -> None:
        response = self.bot._route_command("/confirm_meal draft-1")
        self.assertIn("Meal logged", response)
        self.assertEqual(self.service.confirmed_draft_ids, ["draft-1"])

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


if __name__ == "__main__":
    unittest.main()
