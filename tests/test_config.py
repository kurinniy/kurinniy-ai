import unittest

from ai_me.config import AppSettings, DatabaseSettings, MediaBucketSettings, TelegramSettings


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
                "APP_RUNTIME_MODE": "digest_worker",
                "DIGEST_SCHEDULER_POLL_INTERVAL_SECONDS": "45",
                "OPENAI_API_KEY": "sk-test",
                "OPENAI_MODEL": "gpt-test",
            }
        )
        self.assertEqual(settings.database.host, "mysql.railway.internal")
        self.assertEqual(settings.telegram.allowed_user_ids, frozenset({11, 42}))
        self.assertEqual(settings.telegram.admin_user_ids, frozenset({11, 42, 96445950}))
        self.assertEqual(settings.telegram.owner_telegram_user_id, 96445950)
        self.assertEqual(settings.telegram.food_photo_rate_limit_seconds, 15)
        self.assertEqual(settings.telegram.timezone_name, "Europe/Moscow")
        self.assertEqual(settings.telegram.environment_name, "staging")
        self.assertEqual(settings.telegram.registration_mode, "open")
        self.assertEqual(settings.telegram.mini_app_url, "")
        self.assertEqual(settings.environment_name, "staging")
        self.assertEqual(settings.runtime_mode, "digest_worker")
        self.assertEqual(settings.scheduler_poll_interval_seconds, 45)
        self.assertEqual(settings.food_vision_api_key, "sk-test")
        self.assertEqual(settings.food_vision_model, "gpt-test")
        self.assertFalse(settings.google_drive.enabled)
        self.assertEqual(settings.google_drive.lookback_days, 2)
        self.assertEqual(settings.web.port, 8000)

    def test_telegram_settings_require_token(self) -> None:
        with self.assertRaises(ValueError):
            TelegramSettings.from_env({})

    def test_telegram_settings_load_food_photo_rate_limit(self) -> None:
        settings = TelegramSettings.from_env(
            {
                "TELEGRAM_BOT_TOKEN": "123:abc",
                "TELEGRAM_FOOD_PHOTO_RATE_LIMIT_SECONDS": "25",
            }
        )
        self.assertEqual(settings.food_photo_rate_limit_seconds, 25)

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

    def test_google_drive_settings_can_be_loaded_from_env(self) -> None:
        settings = AppSettings.from_env(
            {
                "MYSQLHOST": "mysql.railway.internal",
                "MYSQLPORT": "3306",
                "MYSQLDATABASE": "railway",
                "MYSQLUSER": "root",
                "MYSQLPASSWORD": "secret",
                "TELEGRAM_BOT_TOKEN": "123:abc",
                "GOOGLE_SERVICE_ACCOUNT_JSON": '{"type":"service_account"}',
            }
        )
        self.assertTrue(settings.google_drive.enabled)
        self.assertEqual(settings.google_drive.service_account_json, '{"type":"service_account"}')
        self.assertEqual(settings.google_drive.lookback_days, 2)

    def test_media_bucket_settings_can_be_loaded_from_env(self) -> None:
        settings = AppSettings.from_env(
            {
                "MYSQLHOST": "mysql.railway.internal",
                "MYSQLPORT": "3306",
                "MYSQLDATABASE": "railway",
                "MYSQLUSER": "root",
                "MYSQLPASSWORD": "secret",
                "TELEGRAM_BOT_TOKEN": "123:abc",
                "BUCKET": "meal-media",
                "ENDPOINT": "https://bucket.example.com",
                "ACCESS_KEY_ID": "key-id",
                "SECRET_ACCESS_KEY": "secret-key",
                "BUCKET_KEY_PREFIX": "photo-cache",
            }
        )
        self.assertEqual(
            settings.media_bucket,
            MediaBucketSettings(
                bucket_name="meal-media",
                endpoint="https://bucket.example.com",
                access_key_id="key-id",
                secret_access_key="secret-key",
                region="auto",
                key_prefix="photo-cache",
            ),
        )
        self.assertTrue(settings.media_bucket.enabled)

    def test_scheduler_poll_interval_defaults_to_two_hours(self) -> None:
        settings = AppSettings.from_env(
            {
                "MYSQLHOST": "mysql.railway.internal",
                "MYSQLPORT": "3306",
                "MYSQLDATABASE": "railway",
                "MYSQLUSER": "root",
                "MYSQLPASSWORD": "secret",
                "TELEGRAM_BOT_TOKEN": "123:abc",
            }
        )
        self.assertEqual(settings.scheduler_poll_interval_seconds, 7200)

    def test_web_settings_can_be_loaded_from_env(self) -> None:
        settings = AppSettings.from_env(
            {
                "MYSQLHOST": "mysql.railway.internal",
                "MYSQLPORT": "3306",
                "MYSQLDATABASE": "railway",
                "MYSQLUSER": "root",
                "MYSQLPASSWORD": "secret",
                "TELEGRAM_BOT_TOKEN": "123:abc",
                "APP_RUNTIME_MODE": "web",
                "WEB_PORT": "9000",
                "MINI_APP_URL": "https://mini.example.com",
                "WEBAPP_SESSION_SECRET": "session-secret",
            }
        )
        self.assertEqual(settings.runtime_mode, "web")
        self.assertEqual(settings.web.port, 9000)
        self.assertEqual(settings.web.public_url, "https://mini.example.com")
        self.assertEqual(settings.telegram.mini_app_url, "https://mini.example.com")
        self.assertEqual(settings.web.session_secret, "session-secret")


if __name__ == "__main__":
    unittest.main()
