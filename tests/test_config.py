import unittest

from ai_me.config import AppSettings, DatabaseSettings, TelegramSettings


class ConfigTest(unittest.TestCase):
    def test_database_settings_can_be_loaded_from_mysql_url(self) -> None:
        settings = DatabaseSettings.from_env(
            {"MYSQL_URL": "mysql://demo:secret@db.internal:3307/ai_me"}
        )
        self.assertEqual(settings.host, "db.internal")
        self.assertEqual(settings.port, 3307)
        self.assertEqual(settings.user, "demo")
        self.assertEqual(settings.password, "secret")
        self.assertEqual(settings.database, "ai_me")

    def test_app_settings_can_be_loaded_from_railway_style_env(self) -> None:
        settings = AppSettings.from_env(
            {
                "APP_ENV": "staging",
                "MYSQLHOST": "mysql.railway.internal",
                "MYSQLPORT": "3306",
                "MYSQLDATABASE": "railway",
                "MYSQLUSER": "root",
                "MYSQLPASSWORD": "secret",
                "TELEGRAM_BOT_TOKEN": "123:abc",
                "ALLOWED_TELEGRAM_USER_IDS": "11, 42",
                "APP_TIMEZONE": "Europe/Moscow",
                "OPENAI_API_KEY": "sk-test",
                "OPENAI_MODEL": "gpt-test",
            }
        )
        self.assertEqual(settings.database.host, "mysql.railway.internal")
        self.assertEqual(settings.telegram.allowed_user_ids, frozenset({11, 42}))
        self.assertEqual(settings.telegram.timezone_name, "Europe/Moscow")
        self.assertEqual(settings.telegram.environment_name, "staging")
        self.assertEqual(settings.environment_name, "staging")
        self.assertEqual(settings.food_vision_api_key, "sk-test")
        self.assertEqual(settings.food_vision_model, "gpt-test")

    def test_telegram_settings_require_token(self) -> None:
        with self.assertRaises(ValueError):
            TelegramSettings.from_env({})

    def test_default_food_model_is_used_when_api_key_exists(self) -> None:
        settings = AppSettings.from_env(
            {
                "MYSQLHOST": "mysql.railway.internal",
                "MYSQLPORT": "3306",
                "MYSQLDATABASE": "railway",
                "MYSQLUSER": "root",
                "MYSQLPASSWORD": "secret",
                "TELEGRAM_BOT_TOKEN": "123:abc",
                "OPENAI_API_KEY": "sk-test",
            }
        )
        self.assertEqual(settings.food_vision_model, "gpt-4.1-mini")


if __name__ == "__main__":
    unittest.main()
