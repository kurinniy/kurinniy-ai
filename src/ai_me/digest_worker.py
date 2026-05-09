import logging
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Callable, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ai_me.domain.digest import DigestStatus, DigestType, UserDigestSettings
from ai_me.domain.user import AppUser, UserStatus
from ai_me.services.health_service import HealthService
from ai_me.telegram import TelegramHealthBot


logger = logging.getLogger(__name__)


class DigestSchedulerWorker:
    GOOGLE_DRIVE_STALE_HOURS = 24

    def __init__(
        self,
        service: HealthService,
        bot: TelegramHealthBot,
        poll_interval_seconds: int = 60,
    ) -> None:
        self.service = service
        self.bot = bot
        self.poll_interval_seconds = poll_interval_seconds

    def run_forever(self) -> None:
        logger.info("Digest scheduler worker started poll_interval_seconds=%s", self.poll_interval_seconds)
        while True:
            try:
                self.run_once()
            except Exception as exc:  # pragma: no cover
                logger.exception("Digest scheduler loop failed: %s", exc)
            time.sleep(self.poll_interval_seconds)

    def run_once(self, now_utc: Optional[datetime] = None) -> None:
        current_utc = now_utc or datetime.now(timezone.utc)
        users = self.service.list_users(status=UserStatus.ACTIVE)
        for user in users:
            self._process_google_drive_import(user, current_utc=current_utc)
            self._process_user(user, current_utc=current_utc)

    def _process_google_drive_import(self, user: AppUser, current_utc: datetime) -> None:
        if not user.has_admin_access:
            return
        settings = self.service.get_google_drive_settings(user.user_id)
        if settings is None or not settings.enabled:
            return
        self._notify_if_google_drive_import_is_stale(user, settings, current_utc=current_utc)
        result = self.service.import_google_drive_health_data(user.user_id, now=current_utc.replace(tzinfo=None))
        if result.scanned_files == 0:
            return
        logger.info(
            "Google Drive health import user_id=%s scanned=%s imported=%s updated=%s skipped=%s failed=%s activity_entries=%s",
            user.user_id,
            result.scanned_files,
            result.imported_files,
            result.updated_files,
            result.skipped_files,
            result.failed_files,
            result.activity_entries_count,
        )

    def _notify_if_google_drive_import_is_stale(
        self,
        user: AppUser,
        settings,
        *,
        current_utc: datetime,
    ) -> None:
        if settings.last_successful_import_at is None:
            return
        current_local = current_utc.replace(tzinfo=None)
        stale_for = current_local - settings.last_successful_import_at
        if stale_for < timedelta(hours=self.GOOGLE_DRIVE_STALE_HOURS):
            return
        if (
            settings.last_stale_alert_sent_at is not None
            and settings.last_stale_alert_sent_at >= settings.last_successful_import_at
        ):
            return

        alert_text = (
            "Алерт Google Drive импорта\n"
            "Пользователь: %s (%s)\n"
            "user_id=%s\n"
            "folder_id=%s\n"
            "Последний успешный импорт: %s\n"
            "Отставание: %.1f ч"
        ) % (
            user.first_name or user.username or str(user.telegram_user_id),
            user.telegram_user_id,
            user.user_id,
            settings.folder_id,
            settings.last_successful_import_at.strftime("%Y-%m-%d %H:%M"),
            round(stale_for.total_seconds() / 3600, 1),
        )
        admin_users = [
            admin_user
            for admin_user in self.service.list_users(status=UserStatus.ACTIVE)
            if admin_user.is_admin
        ]
        for admin_user in admin_users:
            self.bot.send_text_message(admin_user.chat_id, alert_text)
        self.service.set_google_drive_alert_sent(user.user_id, sent_at=current_local)

    def _process_user(self, user: AppUser, current_utc: datetime) -> None:
        settings = self.service.get_digest_settings(user.user_id)
        try:
            user_zone = ZoneInfo(settings.timezone_name)
        except ZoneInfoNotFoundError:
            logger.warning("Skipping user_id=%s invalid timezone=%s", user.user_id, settings.timezone_name)
            return
        user_now = current_utc.astimezone(user_zone).replace(tzinfo=None)

        if settings.daily_digest_enabled:
            scheduled_daily = self._scheduled_datetime(user_now.date(), settings.daily_digest_time)
            if user_now >= scheduled_daily:
                self._process_daily_digest(user, settings, user_now, scheduled_daily)

        if settings.weekly_digest_enabled and user_now.weekday() == settings.weekly_digest_weekday:
            scheduled_weekly = self._scheduled_datetime(user_now.date(), settings.weekly_digest_time)
            if user_now >= scheduled_weekly:
                self._process_weekly_digest(user, settings, user_now, scheduled_weekly)

    def _process_daily_digest(
        self,
        user: AppUser,
        settings: UserDigestSettings,
        user_now: datetime,
        scheduled_for: datetime,
    ) -> None:
        digest_date = user_now.date() - timedelta(days=1)
        run = self._prepare_run(
            user=user,
            digest_type=DigestType.DAILY,
            digest_date=digest_date,
            scheduled_for=scheduled_for,
        )
        if run is None:
            return
        self._execute_run(
            run_id=run.run_id,
            user=user,
            digest_type=DigestType.DAILY,
            digest_date=digest_date,
            send_fn=lambda: self.bot.send_daily_digest(
                chat_id=user.chat_id,
                user_id=user.user_id,
                digest_date=digest_date,
                include_step_insight=user.has_admin_access,
            ),
            completed_at=user_now,
        )

    def _process_weekly_digest(
        self,
        user: AppUser,
        settings: UserDigestSettings,
        user_now: datetime,
        scheduled_for: datetime,
    ) -> None:
        previous_week_reference = user_now.date() - timedelta(days=7)
        week_start = previous_week_reference - timedelta(days=previous_week_reference.weekday())
        run = self._prepare_run(
            user=user,
            digest_type=DigestType.WEEKLY,
            digest_date=week_start,
            scheduled_for=scheduled_for,
        )
        if run is None:
            return
        self._execute_run(
            run_id=run.run_id,
            user=user,
            digest_type=DigestType.WEEKLY,
            digest_date=week_start,
            send_fn=lambda: self.bot.send_weekly_digest(
                chat_id=user.chat_id,
                user_id=user.user_id,
                week_start=week_start,
            ),
            completed_at=user_now,
        )

    def _prepare_run(
        self,
        user: AppUser,
        digest_type: DigestType,
        digest_date: date,
        scheduled_for: datetime,
    ):
        existing = self._find_digest_run(user.user_id, digest_type, digest_date)
        if existing is not None:
            if existing.status in {DigestStatus.SENT, DigestStatus.SKIPPED, DigestStatus.PROCESSING}:
                return None
            self.service.update_digest_run(
                existing.run_id,
                status=DigestStatus.PROCESSING,
                error_message="",
                payload=existing.payload,
            )
            return self._find_digest_run(user.user_id, digest_type, digest_date)

        return self.service.create_digest_run(
            user.user_id,
            digest_type=digest_type,
            digest_date=digest_date,
            status=DigestStatus.PROCESSING,
            now=scheduled_for,
            scheduled_for=scheduled_for,
        )

    def _execute_run(
        self,
        run_id: str,
        user: AppUser,
        digest_type: DigestType,
        digest_date: date,
        send_fn: Callable[[], Optional[Dict[str, object]]],
        completed_at: datetime,
    ) -> None:
        try:
            payload = send_fn()
            if payload is None:
                logger.info("Skipping digest user_id=%s type=%s digest_date=%s: no data", user.user_id, digest_type.value, digest_date)
                self.service.update_digest_run(
                    run_id,
                    status=DigestStatus.SKIPPED,
                    sent_at=completed_at,
                    payload={"reason": "no_confirmed_photo_meals"},
                )
                return
            self.service.update_digest_run(
                run_id,
                status=DigestStatus.SENT,
                sent_at=completed_at,
                payload={key: str(value) for key, value in payload.items()},
            )
            logger.info("Digest sent user_id=%s type=%s digest_date=%s", user.user_id, digest_type.value, digest_date)
        except Exception as exc:
            logger.exception(
                "Digest send failed user_id=%s type=%s digest_date=%s error=%s",
                user.user_id,
                digest_type.value,
                digest_date,
                exc,
            )
            self.service.update_digest_run(
                run_id,
                status=DigestStatus.FAILED,
                error_message=str(exc),
            )

    def _find_digest_run(self, user_id: int, digest_type: DigestType, digest_date: date):
        runs = self.service.list_digest_runs(user_id, digest_type=digest_type)
        for run in runs:
            if run.digest_date == digest_date:
                return run
        return None

    @staticmethod
    def _scheduled_datetime(target_date: date, time_text: str) -> datetime:
        hour_str, minute_str = time_text.split(":", 1)
        return datetime.combine(target_date, dt_time(hour=int(hour_str), minute=int(minute_str)))
