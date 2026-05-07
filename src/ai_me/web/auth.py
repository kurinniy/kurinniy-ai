import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from urllib.parse import parse_qsl


class TelegramInitDataError(ValueError):
    pass


class WebSessionError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedTelegramMiniAppUser:
    telegram_user_id: int
    username: str
    first_name: str
    last_name: str
    auth_date: datetime
    raw_init_data: str


@dataclass(frozen=True)
class WebSession:
    user_id: int
    telegram_user_id: int
    expires_at: datetime


def validate_telegram_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int = 3600,
    now: Optional[datetime] = None,
) -> ValidatedTelegramMiniAppUser:
    if not init_data.strip():
        raise TelegramInitDataError("Пустой initData.")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", "")
    if not received_hash:
        raise TelegramInitDataError("В initData отсутствует hash.")

    data_check_string = "\n".join(
        "%s=%s" % (key, value)
        for key, value in sorted(parsed.items(), key=lambda item: item[0])
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, calculated_hash):
        raise TelegramInitDataError("Подпись initData не прошла проверку.")

    auth_timestamp = parsed.get("auth_date")
    if not auth_timestamp:
        raise TelegramInitDataError("В initData отсутствует auth_date.")
    auth_date = datetime.fromtimestamp(int(auth_timestamp), tz=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    if current_time - auth_date > timedelta(seconds=max_age_seconds):
        raise TelegramInitDataError("Данные Mini App устарели, откройте приложение заново.")

    user_raw = parsed.get("user")
    if not user_raw:
        raise TelegramInitDataError("В initData отсутствуют данные пользователя.")
    try:
        user_payload = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise TelegramInitDataError("Некорректный JSON пользователя в initData.") from exc

    telegram_user_id = user_payload.get("id")
    if not isinstance(telegram_user_id, int):
        raise TelegramInitDataError("Некорректный Telegram user id в initData.")

    return ValidatedTelegramMiniAppUser(
        telegram_user_id=telegram_user_id,
        username=str(user_payload.get("username", "") or ""),
        first_name=str(user_payload.get("first_name", "") or ""),
        last_name=str(user_payload.get("last_name", "") or ""),
        auth_date=auth_date,
        raw_init_data=init_data,
    )


def issue_web_session_token(
    *,
    user_id: int,
    telegram_user_id: int,
    secret: str,
    ttl_seconds: int,
    now: Optional[datetime] = None,
) -> str:
    current_time = now or datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "telegram_user_id": telegram_user_id,
        "exp": int((current_time + timedelta(seconds=ttl_seconds)).timestamp()),
        "iat": int(current_time.timestamp()),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_token = _b64url_encode(payload_bytes)
    signature = hmac.new(secret.encode("utf-8"), payload_token.encode("utf-8"), hashlib.sha256).digest()
    return "%s.%s" % (payload_token, _b64url_encode(signature))


def validate_web_session_token(
    token: str,
    *,
    secret: str,
    now: Optional[datetime] = None,
) -> WebSession:
    if "." not in token:
        raise WebSessionError("Некорректный формат session token.")
    payload_token, signature_token = token.split(".", 1)
    expected_signature = hmac.new(secret.encode("utf-8"), payload_token.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_encode(expected_signature), signature_token):
        raise WebSessionError("Подпись session token не прошла проверку.")

    try:
        payload = json.loads(_b64url_decode(payload_token).decode("utf-8"))
    except Exception as exc:  # pragma: no cover
        raise WebSessionError("Некорректный payload session token.") from exc

    current_time = now or datetime.now(timezone.utc)
    expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
    if current_time >= expires_at:
        raise WebSessionError("Сессия Mini App истекла.")

    return WebSession(
        user_id=int(payload["user_id"]),
        telegram_user_id=int(payload["telegram_user_id"]),
        expires_at=expires_at,
    )


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _b64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + padding)
