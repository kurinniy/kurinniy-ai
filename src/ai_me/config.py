import os
from dataclasses import dataclass
from typing import FrozenSet, Mapping, Optional
from urllib.parse import urlparse, unquote


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "DatabaseSettings":
        mysql_url = env.get("MYSQL_URL") or env.get("DATABASE_URL")
        if mysql_url:
            parsed = urlparse(mysql_url)
            if not parsed.hostname or not parsed.path:
                raise ValueError("MYSQL_URL is missing hostname or database name")
            return cls(
                host=parsed.hostname,
                port=parsed.port or 3306,
                user=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
                database=parsed.path.lstrip("/"),
            )

        host = env.get("MYSQLHOST") or env.get("DB_HOST")
        database = env.get("MYSQLDATABASE") or env.get("DB_NAME")
        user = env.get("MYSQLUSER") or env.get("DB_USER")
        password = env.get("MYSQLPASSWORD") or env.get("DB_PASSWORD")
        port_raw = env.get("MYSQLPORT") or env.get("DB_PORT") or "3306"

        if not all([host, database, user, password]):
            raise ValueError(
                "MySQL configuration is incomplete. Set MYSQL_URL or MYSQLHOST/MYSQLDATABASE/"
                "MYSQLUSER/MYSQLPASSWORD."
            )

        return cls(
            host=host,
            port=int(port_raw),
            user=user,
            password=password,
            database=database,
        )

    def as_mysql_connector_kwargs(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "charset": "utf8mb4",
        }


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    polling_timeout_seconds: int = 30
    allowed_user_ids: FrozenSet[int] = frozenset()
    admin_user_ids: FrozenSet[int] = frozenset()
    owner_telegram_user_id: int = 96445950
    timezone_name: str = "UTC"
    environment_name: str = "production"
    registration_mode: str = "invite_only"
    mini_app_url: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "TelegramSettings":
        bot_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")

        allowed_user_ids = _parse_int_set(env.get("ALLOWED_TELEGRAM_USER_IDS"))
        timeout = int(env.get("TELEGRAM_POLLING_TIMEOUT_SECONDS", "30"))
        timezone_name = env.get("APP_TIMEZONE", "UTC")
        environment_name = env.get("APP_ENV", "production").strip() or "production"
        owner_telegram_user_id = int(env.get("OWNER_TELEGRAM_USER_ID", "96445950"))
        admin_user_ids = _parse_int_set(env.get("ADMIN_TELEGRAM_USER_IDS"))
        if not admin_user_ids:
            admin_user_ids = allowed_user_ids
        admin_user_ids = frozenset(set(admin_user_ids) | {owner_telegram_user_id})
        return cls(
            bot_token=bot_token,
            polling_timeout_seconds=timeout,
            allowed_user_ids=allowed_user_ids,
            admin_user_ids=admin_user_ids,
            owner_telegram_user_id=owner_telegram_user_id,
            timezone_name=timezone_name,
            environment_name=environment_name,
            registration_mode="invite_only",
            mini_app_url=env.get("MINI_APP_URL", "").strip(),
        )


@dataclass(frozen=True)
class GoogleDriveSettings:
    service_account_json: str = ""
    service_account_file: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.service_account_json or self.service_account_file)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "GoogleDriveSettings":
        return cls(
            service_account_json=env.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip(),
            service_account_file=env.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip(),
        )


@dataclass(frozen=True)
class WebSettings:
    host: str = "0.0.0.0"
    port: int = 8000
    public_url: str = ""
    session_secret: str = ""
    session_ttl_seconds: int = 86400
    init_data_ttl_seconds: int = 3600

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "WebSettings":
        return cls(
            host=env.get("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=int(env.get("PORT") or env.get("WEB_PORT") or "8000"),
            public_url=(env.get("MINI_APP_URL", "").strip() or env.get("WEB_PUBLIC_URL", "").strip()),
            session_secret=env.get("WEBAPP_SESSION_SECRET", "").strip(),
            session_ttl_seconds=int(env.get("WEBAPP_SESSION_TTL_SECONDS", "86400")),
            init_data_ttl_seconds=int(env.get("WEBAPP_INIT_DATA_TTL_SECONDS", "3600")),
        )


@dataclass(frozen=True)
class AppSettings:
    database: DatabaseSettings
    telegram: TelegramSettings
    google_drive: GoogleDriveSettings
    web: WebSettings
    environment_name: str = "production"
    runtime_mode: str = "bot"
    scheduler_poll_interval_seconds: int = 60
    food_vision_api_key: str = ""
    food_vision_model: str = ""

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AppSettings":
        source = env or os.environ
        return cls(
            database=DatabaseSettings.from_env(source),
            telegram=TelegramSettings.from_env(source),
            google_drive=GoogleDriveSettings.from_env(source),
            web=WebSettings.from_env(source),
            environment_name=source.get("APP_ENV", "production").strip() or "production",
            runtime_mode=(source.get("APP_RUNTIME_MODE", "bot").strip() or "bot"),
            scheduler_poll_interval_seconds=int(source.get("DIGEST_SCHEDULER_POLL_INTERVAL_SECONDS", "60")),
            food_vision_api_key=source.get("OPENAI_API_KEY", "").strip(),
            food_vision_model=(source.get("OPENAI_MODEL", "").strip() or "gpt-4.1-mini"),
        )


def _parse_int_set(raw: Optional[str]) -> FrozenSet[int]:
    if not raw:
        return frozenset()
    values = []
    for chunk in raw.split(","):
        item = chunk.strip()
        if item:
            values.append(int(item))
    return frozenset(values)
