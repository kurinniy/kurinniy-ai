from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from ai_me.config import AppSettings
from ai_me.services.health_service import HealthService
from ai_me.version import APP_VERSION
from ai_me.web.auth import (
    TelegramInitDataError,
    WebSessionError,
    issue_web_session_token,
    validate_telegram_init_data,
    validate_web_session_token,
)
from ai_me.web.dashboard import build_dashboard_payload

try:  # pragma: no cover
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore


def create_web_app(settings: AppSettings, service: HealthService):
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("Web runtime dependencies are not installed. Add fastapi and uvicorn.")

    app = FastAPI(title="ai-me mini app", version=APP_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if frontend_dist.exists():
        assets_dir = frontend_dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    session_secret = settings.web.session_secret or settings.telegram.bot_token

    def _build_auth_response(app_user):
        token = issue_web_session_token(
            user_id=app_user.user_id,
            telegram_user_id=app_user.telegram_user_id,
            secret=session_secret,
            ttl_seconds=settings.web.session_ttl_seconds,
            now=datetime.now(timezone.utc),
        )
        return {
            "token": token,
            "expires_in": settings.web.session_ttl_seconds,
            "user": {
                "user_id": app_user.user_id,
                "telegram_user_id": app_user.telegram_user_id,
                "first_name": app_user.first_name,
                "username": app_user.username,
                "is_admin": app_user.is_admin,
                "status": app_user.status.value,
            },
        }

    def _authenticate_init_data(init_data: str):
        try:
            validated = validate_telegram_init_data(
                init_data,
                bot_token=settings.telegram.bot_token,
                max_age_seconds=settings.web.init_data_ttl_seconds,
                now=datetime.now(timezone.utc),
            )
        except TelegramInitDataError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        try:
            app_user = service.register_user(
                telegram_user_id=validated.telegram_user_id,
                chat_id=validated.telegram_user_id,
                username=validated.username,
                first_name=validated.first_name,
            )
        except ValueError as exc:
            if "заблокирован" in str(exc):
                raise HTTPException(status_code=403, detail="blocked") from exc
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if app_user.status.value == "blocked":
            raise HTTPException(status_code=403, detail="blocked")
        return app_user

    def _resolve_current_user(authorization: Optional[str] = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing_authorization")
        token = authorization.split(" ", 1)[1].strip()
        try:
            session = validate_web_session_token(
                token,
                secret=session_secret,
                now=datetime.now(timezone.utc),
            )
        except WebSessionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        app_user = service.get_user_by_telegram_user_id(session.telegram_user_id)
        if app_user is None:
            raise HTTPException(status_code=403, detail="registration_required")
        if app_user.user_id != session.user_id:
            raise HTTPException(status_code=401, detail="session_user_mismatch")
        return app_user

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "runtime_mode": "web", "environment": settings.environment_name}

    @app.post("/api/webapp/auth")
    def webapp_auth(payload: dict):
        init_data = str(payload.get("init_data", "") or "")
        app_user = _authenticate_init_data(init_data)
        return _build_auth_response(app_user)

    @app.post("/api/webapp/bootstrap")
    def webapp_bootstrap(payload: dict):
        init_data = str(payload.get("init_data", "") or "")
        target_date_raw = str(payload.get("target_date", "") or "")
        effective_date = date.fromisoformat(target_date_raw) if target_date_raw else datetime.now().date()
        app_user = _authenticate_init_data(init_data)
        auth_payload = _build_auth_response(app_user)
        dashboard = build_dashboard_payload(
            service=service,
            app_user=app_user,
            target_date=effective_date,
        )
        return {
            **auth_payload,
            "dashboard": dashboard,
        }

    @app.get("/api/me")
    def api_me(app_user=Depends(_resolve_current_user)):
        return {
            "user_id": app_user.user_id,
            "telegram_user_id": app_user.telegram_user_id,
            "username": app_user.username,
            "first_name": app_user.first_name,
            "is_admin": app_user.is_admin,
            "status": app_user.status.value,
            "environment": settings.environment_name,
        }

    @app.get("/api/dashboard")
    def api_dashboard(
        target_date: Optional[str] = None,
        app_user=Depends(_resolve_current_user),
    ):
        effective_date = date.fromisoformat(target_date) if target_date else datetime.now().date()
        return build_dashboard_payload(
            service=service,
            app_user=app_user,
            target_date=effective_date,
        )

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        index_file = frontend_dist / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="ru">
              <head>
                <meta charset="utf-8" />
                <title>ai-me mini app</title>
              </head>
              <body>
                <h1>Mini App frontend не собран</h1>
                <p>Соберите frontend в <code>frontend/dist</code> и перезапустите web runtime.</p>
              </body>
            </html>
            """.strip()
        )

    return app
