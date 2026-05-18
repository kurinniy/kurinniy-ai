# ai-me

Current stage of a personal assistant system focused on three concrete capabilities:

- `Health`: stores daily health signals such as meals, water, and weight.
- `DecisionLog`: stores assistant recommendations, alerts, and confirmation requests derived from those signals.
- `Telegram Interface`: accepts health events over Telegram using long polling.
- `Food Pipeline`: accepts food photos, creates a meal draft, and logs it after confirmation.
- `Multi-user Access`: supports open onboarding for multiple Telegram users in private chats.
- `Telegram Mini App`: provides a web dashboard inside Telegram while the bot remains the channel for onboarding, digests, and fallback actions.

The current implementation is intentionally small, but now targets a deployable setup for Railway:

- MySQL as the primary database.
- Telegram long polling as the first user interface.
- Standard-library HTTP calls for Telegram so the runtime stays small.

## Structure

- `src/ai_me/domain`: domain entities and enums.
- `src/ai_me/storage`: repository protocol, MySQL adapter, and in-memory test adapter.
- `src/ai_me/services`: application services and health rule evaluation.
- `src/ai_me/telegram.py`: Telegram long-polling worker.
- `src/ai_me/web`: FastAPI backend for the Telegram Mini App.
- `src/ai_me/config.py`: environment-driven app configuration.
- `frontend`: React/Vite client for the Telegram Mini App.
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
- `APP_RUNTIME_MODE`: `bot` or `digest_worker`; defaults to `bot`.
- `DIGEST_SCHEDULER_POLL_INTERVAL_SECONDS`: defaults to `7200` (2 hours).
- `OWNER_TELEGRAM_USER_ID`: Telegram user id, which receives existing single-user data during migration. Defaults to `96445950`.
- `ADMIN_TELEGRAM_USER_IDS`: comma-separated Telegram user ids with administrator privileges.
- `ALLOWED_TELEGRAM_USER_IDS`: legacy fallback for admin ids.
- `APP_TIMEZONE`: for example `Europe/Moscow`.
- `TELEGRAM_POLLING_TIMEOUT_SECONDS`: defaults to `30`.
- `TELEGRAM_FOOD_PHOTO_RATE_LIMIT_SECONDS`: cooldown between food photos for regular users; defaults to `15`.
- `MINI_APP_URL`: public HTTPS URL of the Telegram Mini App. When set, the bot syncs a menu button `Открыть приложение`.
- `WEB_HOST`: defaults to `0.0.0.0`.
- `WEB_PORT`: defaults to `8000` locally. On Railway the app also respects `PORT`.
- `WEBAPP_SESSION_SECRET`: optional secret for signed Mini App sessions. If empty, the bot token is used.
- `WEBAPP_SESSION_TTL_SECONDS`: defaults to `86400`.
- `WEBAPP_INIT_DATA_TTL_SECONDS`: defaults to `3600`.
- `OPENAI_API_KEY`: required for food photo analysis.
- `OPENAI_MODEL`: required for food photo analysis.
- `BUCKET`: Railway Bucket name for meal photo storage.
- `ENDPOINT`: S3-compatible Railway Bucket endpoint.
- `ACCESS_KEY_ID`: access key for the bucket.
- `SECRET_ACCESS_KEY`: secret key for the bucket.
- `BUCKET_KEY_PREFIX`: object key prefix; defaults to `meal-media`.

For a copy-paste starting point, use [.env.example](/Users/kurinniy/Documents/Projects/ai-me/.env.example).
For staging, use [.env.staging.example](/Users/kurinniy/Documents/Projects/ai-me/.env.staging.example).

## Run The Bot

```bash
PYTHONPATH=src python3 -m ai_me.main
```

## Run The Digest Worker

```bash
APP_RUNTIME_MODE=digest_worker PYTHONPATH=src python3 -m ai_me.main
```

The digest worker:

- checks active users every `DIGEST_SCHEDULER_POLL_INTERVAL_SECONDS`;
- sends `daily digest` after `08:00` in the user's timezone for yesterday;
- sends `weekly digest` on Monday after `08:00` for the previous Monday-Sunday window;
- uses `digest_runs` to avoid duplicate sends once a digest is marked `sent` or `skipped`.

## Run The Mini App Backend

```bash
APP_RUNTIME_MODE=web PYTHONPATH=src python3 -m ai_me.main
```

The web runtime exposes:

- `POST /api/webapp/auth` for Telegram `initData` authentication;
- `GET /api/me` for the current user context;
- `GET /api/dashboard` for the read-only dashboard payload;
- `GET /healthz` for Railway health checks.

## Run The Mini App Frontend

Install frontend dependencies once:

```bash
cd frontend
npm install
```

Start local development:

```bash
npm run dev
```

By default Vite runs on `http://127.0.0.1:5173` and proxies `/api` and `/healthz` to the local FastAPI server on `http://127.0.0.1:8000`.

For a production build:

```bash
cd frontend
npm run build
```

The build output goes to `frontend/dist` and is served by the Python web runtime and the production Docker image.

## Meal Photo Storage

Meal photos are stored in the configured Railway Bucket. MySQL keeps only media metadata and links to bucket objects.

## Telegram Mini App

Current Mini App scope:

- read-only dashboard for summary and open decisions;
- Telegram WebApp authentication with signed `initData` validation on the backend;
- menu button sync from the bot when `MINI_APP_URL` is configured.

The bot remains responsible for:

- onboarding in private chats;
- daily and weekly digest delivery;
- food photo intake and confirmation;
- fallback command handling.

## Access Model

The bot now works only in `private chats` and uses open onboarding.

Boot sequence:

1. Set `OWNER_TELEGRAM_USER_ID` for the current owner account.
2. Deploy the bot.
3. The store creates that owner user automatically and migrates legacy single-user rows onto that owner.
4. Open the bot from any user account and send `/start`.
5. New users are created automatically and continue working in their own isolated scope.

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

- Create meal drafts from Telegram food photos.
- Support multiple Telegram users with isolated data by `user_id`.
- Register new users with `/start`.
- Reject group chats and work only in Telegram private chats.
- Build a daily health summary from raw events.
- Expose Telegram and internal account data through `/whoami`.
- Generate idempotent decisions for common cases:
  - low water intake late in the day;
  - low protein intake after lunch.
- Track decision lifecycle with statuses such as `open`, `accepted`, and `executed`.

## Railway Notes

This repo includes a single `Dockerfile`, so Railway can build the bot, digest worker, and Mini App web service from the same image.

At startup the bot proactively clears any existing Telegram webhook before entering long polling mode, which makes migration from a webhook setup less error-prone.

For automatic digests, run a second Railway service from the same repo with:

- the same MySQL database as the bot for that environment;
- the same Telegram bot token for that environment;
- `APP_RUNTIME_MODE=digest_worker`.

For the Telegram Mini App, run a third Railway service from the same repo with:

- the same MySQL database as the bot for that environment;
- the same Telegram bot token for that environment;
- `APP_RUNTIME_MODE=web`;
- `MINI_APP_URL` set to the public HTTPS URL of that Railway web service;
- `WEBAPP_SESSION_SECRET` set explicitly;
- Railway public networking enabled, because Telegram Mini Apps require a public HTTPS URL.

Recommended per environment:

- `bot-service`: `APP_RUNTIME_MODE=bot`
- `digest-worker`: `APP_RUNTIME_MODE=digest_worker`
- `mini-app-web`: `APP_RUNTIME_MODE=web`

If you migrate meal photos out of MySQL, add the same bucket variables to:

- `bot-service`, because it writes new photos to the bucket;
- `digest-worker`, because it reads bucket-backed photos while rendering digests.

The web service is stateless. It reads the current user state from MySQL and signs short-lived Mini App session tokens.

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
- If you use the Mini App in staging, point `MINI_APP_URL` to the staging web service, not production.
