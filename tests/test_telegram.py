import unittest

from datetime import date, datetime

from ai_me.domain.food import FoodItemEstimate, MealDraftStatus, MealPhotoDraft
from ai_me.domain.health import DailyHealthGoals, DailyHealthSummary, MealEntry
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
            meals_count=1,
            calories=620,
            protein_g=38,
            fat_g=18,
            carbs_g=71,
            water_ml=0,
            sleep_hours=0,
            steps=0,
            activity_minutes=0,
            latest_weight_kg=None,
            goals=DailyHealthGoals(target_date=target_date),
        )

    def list_meals(self, target_date):
        return [
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 12, 0),
                title="Курица с рисом",
                calories=620,
                protein_g=38,
                fat_g=18,
                carbs_g=71,
            )
        ]

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
                "from": {"id": 42},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777},
                },
            }
        )

        self.assertEqual(self.service.confirmed_draft_ids, ["draft-1"])
        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[0][1]["text"], "Прием пищи сохранен.")
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIn("Прием пищи сохранен", calls[1][1]["text"])
        self.assertEqual(calls[2][0], "sendMessage")
        self.assertIn("Прием пищи сохранен", calls[2][1]["text"])

    def test_confirm_callback_still_answers_when_message_edit_fails(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            if method == "editMessageText":
                raise RuntimeError("message can't be edited")
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_confirm:draft-1",
                "from": {"id": 42},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777},
                },
            }
        )

        self.assertEqual(self.service.confirmed_draft_ids, ["draft-1"])
        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[0][1]["text"], "Прием пищи сохранен.")
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertEqual(calls[2][0], "sendMessage")
        self.assertIn("Прием пищи сохранен", calls[2][1]["text"])

    def test_meal_draft_message_uses_russian_labels(self) -> None:
        draft = self.service.list_meal_drafts()[0]
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._send_meal_draft(777, draft)

        self.assertEqual(messages[0][0], "sendMessage")
        text = messages[0][1]["text"]
        self.assertIn("Черновик приема пищи", text)
        self.assertIn("Состав:", text)
        self.assertIn("Ингредиенты:", text)

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
        response = self.bot._route_command("/summary 2026-05-06")
        self.assertIn("Еда:", response)
        self.assertIn("Курица с рисом", response)
        self.assertIn("Б 38.0 / Ж 18.0 / У 71.0", response)


if __name__ == "__main__":
    unittest.main()
