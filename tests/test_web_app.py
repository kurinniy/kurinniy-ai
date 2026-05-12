import hashlib
import hmac
import json
import unittest
from datetime import datetime, timezone
from urllib.parse import urlencode

from ai_me.config import AppSettings, DatabaseSettings, GoogleDriveSettings, MediaBucketSettings, TelegramSettings, WebSettings
from ai_me.services.health_service import HealthService
from ai_me.storage.memory import InMemoryStore
from ai_me.web.app import create_web_app

try:
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError):  # pragma: no cover
    TestClient = None  # type: ignore


BOT_TOKEN = "123456:bot-token"


def build_init_data(user: dict, auth_date: int) -> str:
    raw_fields = {
        "auth_date": str(auth_date),
        "query_id": "query-1",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
    }
    data_check_string = "\n".join(
        "%s=%s" % (key, value)
        for key, value in sorted(raw_fields.items(), key=lambda item: item[0])
    )
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    raw_fields["hash"] = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(raw_fields)


@unittest.skipIf(TestClient is None, "fastapi test client is unavailable")
class WebAppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.service = HealthService(
            self.store,
            admin_telegram_user_ids=frozenset({96445950}),
            default_timezone_name="Europe/Moscow",
        )
        self.settings = AppSettings(
            database=DatabaseSettings(host="localhost", port=3306, user="root", password="root", database="ai_me"),
            telegram=TelegramSettings(
                bot_token=BOT_TOKEN,
                admin_user_ids=frozenset({96445950}),
                owner_telegram_user_id=96445950,
                timezone_name="Europe/Moscow",
                environment_name="staging",
                registration_mode="open",
                mini_app_url="https://mini-app.example.com",
            ),
            google_drive=GoogleDriveSettings(),
            web=WebSettings(
                session_secret="test-secret",
                session_ttl_seconds=3600,
                init_data_ttl_seconds=3600,
                public_url="https://mini-app.example.com",
            ),
            media_bucket=MediaBucketSettings(),
            environment_name="staging",
            runtime_mode="web",
        )
        self.client = TestClient(create_web_app(settings=self.settings, service=self.service))

    def tearDown(self) -> None:
        self.store.close()

    def test_bootstrap_allows_regular_user(self) -> None:
        init_data = build_init_data(
            {"id": 111, "first_name": "Guest", "username": "guest"},
            auth_date=int(datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc).timestamp()),
        )

        response = self.client.post("/api/webapp/bootstrap", json={"init_data": init_data})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["telegram_user_id"], 111)
        self.assertFalse(response.json()["user"]["is_admin"])

    def test_bootstrap_allows_admin(self) -> None:
        init_data = build_init_data(
            {"id": 96445950, "first_name": "Alex", "username": "kurinniy"},
            auth_date=int(datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc).timestamp()),
        )

        response = self.client.post("/api/webapp/bootstrap", json={"init_data": init_data})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["user"]["telegram_user_id"], 96445950)
        self.assertTrue(payload["user"]["is_admin"])

    def test_session_remains_valid_when_admin_switches_to_user_mode(self) -> None:
        init_data = build_init_data(
            {"id": 96445950, "first_name": "Alex", "username": "kurinniy"},
            auth_date=int(datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc).timestamp()),
        )
        auth_response = self.client.post("/api/webapp/auth", json={"init_data": init_data})
        self.assertEqual(auth_response.status_code, 200)
        token = auth_response.json()["token"]
        admin_user = self.service.get_user_by_telegram_user_id(96445950)
        self.assertIsNotNone(admin_user)
        self.service.set_admin_mode(admin_user.user_id, enabled=False)

        response = self.client.get("/api/me", headers={"Authorization": "Bearer %s" % token})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_admin"])
        self.assertTrue(response.json()["is_admin_account"])
        self.assertFalse(response.json()["admin_mode_enabled"])


if __name__ == "__main__":
    unittest.main()
