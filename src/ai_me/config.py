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
    timezone_name: str = "UTC"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "TelegramSettings":
        bot_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")

        allowed_user_ids = _parse_int_set(env.get("ALLOWED_TELEGRAM_USER_IDS"))
        timeout = int(env.get("TELEGRAM_POLLING_TIMEOUT_SECONDS", "30"))
        timezone_name = env.get("APP_TIMEZONE", "UTC")
        return cls(
            bot_token=bot_token,
            polling_timeout_seconds=timeout,
            allowed_user_ids=allowed_user_ids,
            timezone_name=timezone_name,
        )


@dataclass(frozen=True)
class AppSettings:
    database: DatabaseSettings
    telegram: TelegramSettings
    food_vision_api_key: str = ""
    food_vision_model: str = ""

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AppSettings":
        source = env or os.environ
        return cls(
            database=DatabaseSettings.from_env(source),
            telegram=TelegramSettings.from_env(source),
            food_vision_api_key=source.get("OPENAI_API_KEY", "").strip(),
            food_vision_model=source.get("OPENAI_MODEL", "").strip(),
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
