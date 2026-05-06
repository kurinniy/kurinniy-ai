# ai-me

Current stage of a personal assistant system focused on three concrete capabilities:

- `Health`: stores daily health signals such as meals, water, sleep, weight, and activity.
- `DecisionLog`: stores assistant recommendations, alerts, and confirmation requests derived from those signals.
- `Telegram Interface`: accepts health events over Telegram using long polling.

The current implementation is intentionally small, but now targets a deployable setup for Railway:

- MySQL as the primary database.
- Telegram long polling as the first user interface.
- Standard-library HTTP calls for Telegram so the runtime stays small.

## Structure

- `src/ai_me/domain`: domain entities and enums.
- `src/ai_me/storage`: repository protocol, MySQL adapter, and in-memory test adapter.
- `src/ai_me/services`: application services and health rule evaluation.
- `src/ai_me/telegram.py`: Telegram long-polling worker.
- `src/ai_me/config.py`: environment-driven app configuration.
- `tests`: unit tests for the first stage.

## Run Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Environment

Required:

- `TELEGRAM_BOT_TOKEN`
- `MYSQL_URL` or Railway-style MySQL variables:
  - `MYSQLHOST`
  - `MYSQLPORT`
  - `MYSQLDATABASE`
  - `MYSQLUSER`
  - `MYSQLPASSWORD`

Optional:

- `ALLOWED_TELEGRAM_USER_IDS`: comma-separated Telegram user ids allowed to use the bot.
- `APP_TIMEZONE`: for example `Europe/Moscow`.
- `TELEGRAM_POLLING_TIMEOUT_SECONDS`: defaults to `30`.

## Run The Bot

```bash
PYTHONPATH=src python3 -m ai_me.main
```

## Current Capabilities

- Set health goals for a date from Telegram.
- Log meals, water intake, sleep, weight, and activity from Telegram.
- Build a daily health summary from raw events.
- Generate idempotent decisions for common cases:
  - low water intake late in the day;
  - low protein intake after lunch;
  - poor sleep before planned activity.
- Track decision lifecycle with statuses such as `open`, `accepted`, and `executed`.
- Restrict the bot to a known Telegram user list when needed.

## Railway Notes

This repo includes a `Dockerfile`, so Railway can build and run the Telegram worker as a long-lived service. The app does not need a public inbound URL while it uses Telegram long polling.

## Next Step

The next practical slice is to add:

- user profiles instead of a single shared health stream;
- scheduled daily brief generation;
- admin commands to resolve or dismiss decisions from Telegram.
