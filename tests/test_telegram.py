import unittest

from ai_me.config import TelegramSettings
from ai_me.telegram import TelegramHealthBot


class DummyHealthService:
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
        raise NotImplementedError

    def list_decisions(self, status=None, target_date=None):
        return []


class TelegramHealthBotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = TelegramHealthBot(
            service=DummyHealthService(),
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


if __name__ == "__main__":
    unittest.main()
