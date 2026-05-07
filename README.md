# ai-me

Current stage of a personal assistant system focused on three concrete capabilities:

- `Health`: stores daily health signals such as meals, water, sleep, weight, and activity.
- `DecisionLog`: stores assistant recommendations, alerts, and confirmation requests derived from those signals.
- `Telegram Interface`: accepts health events over Telegram using long polling.
- `Food Pipeline`: accepts food photos, creates a meal draft, and logs it after confirmation.

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

- `APP_ENV`: defaults to `production`; use `staging` for a staging bot/deploy.
- `ALLOWED_TELEGRAM_USER_IDS`: comma-separated Telegram user ids allowed to use the bot.
- `APP_TIMEZONE`: for example `Europe/Moscow`.
- `TELEGRAM_POLLING_TIMEOUT_SECONDS`: defaults to `30`.
- `OPENAI_API_KEY`: required for food photo analysis.
- `OPENAI_MODEL`: required for food photo analysis.

For a copy-paste starting point, use [.env.example](/Users/kurinniy/Documents/Projects/ai-me/.env.example).
For staging, use [.env.staging.example](/Users/kurinniy/Documents/Projects/ai-me/.env.staging.example).

## Run The Bot

```bash
PYTHONPATH=src python3 -m ai_me.main
```

## First Bot Boot

1. Start the bot without `ALLOWED_TELEGRAM_USER_IDS`.
2. Send `/whoami` to the bot from your Telegram account.
3. Copy the returned `user_id` into `ALLOWED_TELEGRAM_USER_IDS`.
4. Redeploy or restart the service.

This keeps the first boot simple and lets you lock the bot down immediately after you identify your Telegram account.

## Food Photo Flow

1. Send a food photo to the bot.
2. The bot downloads the image from Telegram, runs food analysis, and creates a meal draft.
3. The bot sends back calories and macros with `Confirm` and `Reject` buttons.
4. On confirmation, the meal is written into the health log and included in the daily summary.

Fallback commands:

- `/drafts`
- `/confirm_meal <draft_id>`
- `/reject_meal <draft_id>`

## Current Capabilities

- Set health goals for a date from Telegram.
- Log meals, water intake, sleep, weight, and activity from Telegram.
- Create meal drafts from Telegram food photos.
- Build a daily health summary from raw events.
- Expose `user_id` and `chat_id` through `/whoami` for first-run setup.
- Generate idempotent decisions for common cases:
  - low water intake late in the day;
  - low protein intake after lunch;
  - poor sleep before planned activity.
- Track decision lifecycle with statuses such as `open`, `accepted`, and `executed`.
- Restrict the bot to a known Telegram user list when needed.

## Railway Notes

This repo includes a `Dockerfile`, so Railway can build and run the Telegram worker as a long-lived service. The app does not need a public inbound URL while it uses Telegram long polling.

At startup the bot proactively clears any existing Telegram webhook before entering long polling mode, which makes migration from a webhook setup less error-prone.

## Staging Setup

Use a separate staging bot and a separate staging MySQL database.

Recommended setup:

1. Create a second Telegram bot in `@BotFather` for staging.
2. Create a second Railway project or a second isolated service group for staging.
3. Attach a separate MySQL service to staging.
4. Set `APP_ENV=staging`.
5. Set the staging bot token in `TELEGRAM_BOT_TOKEN`.
6. Point staging at the staging MySQL database.

Important:

- Do not reuse the production `TELEGRAM_BOT_TOKEN` in staging. Two long-polling workers on the same bot token will conflict on `getUpdates`.
- Do not reuse the production database in staging.
- `/whoami` and `/help` show the current environment name, so it is easy to verify which bot you are talking to.

## Next Step

The next practical slice is to add:

- user profiles instead of a single shared health stream;
- scheduled daily brief generation;
- admin commands to resolve or dismiss decisions from Telegram.
