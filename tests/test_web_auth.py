import hmac
import hashlib
import json
import unittest
from datetime import datetime, timezone
from urllib.parse import urlencode

from ai_me.web.auth import (
    TelegramInitDataError,
    WebSessionError,
    issue_web_session_token,
    validate_telegram_init_data,
    validate_web_session_token,
)


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


class WebAuthTest(unittest.TestCase):
    def test_validate_telegram_init_data_accepts_valid_payload(self) -> None:
        init_data = build_init_data(
            {"id": 96445950, "first_name": "Alex", "username": "kurinniy"},
            auth_date=int(datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc).timestamp()),
        )

        validated = validate_telegram_init_data(
            init_data,
            bot_token=BOT_TOKEN,
            max_age_seconds=3600,
            now=datetime(2026, 5, 7, 10, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(validated.telegram_user_id, 96445950)
        self.assertEqual(validated.first_name, "Alex")
        self.assertEqual(validated.username, "kurinniy")

    def test_validate_telegram_init_data_rejects_invalid_hash(self) -> None:
        init_data = build_init_data(
            {"id": 96445950, "first_name": "Alex"},
            auth_date=int(datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc).timestamp()),
        ) + "broken"

        with self.assertRaises(TelegramInitDataError):
            validate_telegram_init_data(
                init_data,
                bot_token=BOT_TOKEN,
                max_age_seconds=3600,
                now=datetime(2026, 5, 7, 10, 30, tzinfo=timezone.utc),
            )

    def test_issue_and_validate_web_session_token(self) -> None:
        token = issue_web_session_token(
            user_id=1,
            telegram_user_id=96445950,
            secret="secret",
            ttl_seconds=3600,
            now=datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc),
        )

        session = validate_web_session_token(
            token,
            secret="secret",
            now=datetime(2026, 5, 7, 10, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(session.user_id, 1)
        self.assertEqual(session.telegram_user_id, 96445950)

    def test_validate_web_session_token_rejects_expired_token(self) -> None:
        token = issue_web_session_token(
            user_id=1,
            telegram_user_id=96445950,
            secret="secret",
            ttl_seconds=60,
            now=datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc),
        )

        with self.assertRaises(WebSessionError):
            validate_web_session_token(
                token,
                secret="secret",
                now=datetime(2026, 5, 7, 10, 5, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
