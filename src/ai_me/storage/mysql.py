import json
import logging
from datetime import date, datetime, time
from typing import Iterable, List, Optional

from ai_me.domain.digest import DigestRun, DigestStatus, DigestType, UserDigestSettings
from ai_me.domain.decision_log import DecisionKind, DecisionLogEntry, DecisionStatus
from ai_me.domain.finance import FinanceCategoryTotal, FinanceMonthlySummary, FinanceTransaction
from ai_me.domain.food import FoodItemEstimate, MealDraftStatus, MealMedia, MealPhotoDraft
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    DailyHealthSummary,
    MealEntry,
    SleepEntry,
    WaterEntry,
    WeightEntry,
)
from ai_me.domain.health_import import HealthImportFile, HealthImportProvider, HealthImportStatus, UserGoogleDriveSettings
from ai_me.domain.user import AppUser, InviteCode, InviteStatus, UserStatus

try:
    import mysql.connector
except ImportError:  # pragma: no cover
    mysql = None
else:  # pragma: no cover
    mysql = mysql.connector


logger = logging.getLogger(__name__)


class MySQLStore:
    OWNER_TELEGRAM_USER_ID = 96445950

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        charset: str = "utf8mb4",
        owner_telegram_user_id: int = OWNER_TELEGRAM_USER_ID,
    ) -> None:
        if mysql is None:
            raise RuntimeError(
                "mysql-connector-python is not installed. Install dependencies before using MySQLStore."
            )
        self._connect_kwargs = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": charset,
        }
        self.owner_telegram_user_id = owner_telegram_user_id
        self._init_schema()

    def close(self) -> None:
        return None

    def list_users(self, status: Optional[UserStatus] = None) -> List[AppUser]:
        query = "SELECT * FROM users"
        params = []
        if status is not None:
            query += " WHERE status = %s"
            params.append(status.value)
        query += " ORDER BY user_id ASC"
        rows = self._fetchall(query, tuple(params))
        return [self._to_user(row) for row in rows]

    def get_user_by_telegram_user_id(self, telegram_user_id: int) -> Optional[AppUser]:
        row = self._fetchone(
            "SELECT * FROM users WHERE telegram_user_id = %s",
            (telegram_user_id,),
        )
        return self._to_user(row) if row else None

    def create_user(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: str,
        first_name: str,
        status: UserStatus,
        is_admin: bool,
    ) -> AppUser:
        self._execute(
            """
            INSERT INTO users (telegram_user_id, chat_id, username, first_name, status, is_admin, admin_mode_enabled, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                chat_id = VALUES(chat_id),
                username = VALUES(username),
                first_name = VALUES(first_name),
                status = VALUES(status),
                is_admin = VALUES(is_admin),
                admin_mode_enabled = CASE
                    WHEN VALUES(is_admin) = 0 THEN 0
                    WHEN is_admin = 0 THEN 1
                    ELSE admin_mode_enabled
                END
            """,
            (
                telegram_user_id,
                chat_id,
                username,
                first_name,
                status.value,
                1 if is_admin else 0,
                1 if is_admin else 0,
                datetime.now(),
            ),
        )
        user = self.get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            raise RuntimeError("Не удалось создать пользователя Telegram %s" % telegram_user_id)
        return user

    def update_user_profile(self, user: AppUser, chat_id: int, username: str, first_name: str) -> AppUser:
        self._execute(
            """
            UPDATE users
            SET chat_id = %s,
                username = %s,
                first_name = %s
            WHERE user_id = %s
            """,
            (chat_id, username, first_name, user.user_id),
        )
        updated = self.get_user_by_telegram_user_id(user.telegram_user_id)
        if updated is None:
            raise RuntimeError("Не удалось обновить профиль пользователя %s" % user.telegram_user_id)
        return updated

    def update_user_admin_mode(self, user_id: int, enabled: bool) -> AppUser:
        self._execute(
            """
            UPDATE users
            SET admin_mode_enabled = CASE WHEN is_admin = 1 THEN %s ELSE 0 END
            WHERE user_id = %s
            """,
            (1 if enabled else 0, user_id),
        )
        row = self._fetchone("SELECT * FROM users WHERE user_id = %s", (user_id,))
        if row is None:
            raise RuntimeError("Не удалось обновить режим администратора для пользователя %s" % user_id)
        return self._to_user(row)

    def get_user_google_drive_settings(self, user_id: int) -> Optional[UserGoogleDriveSettings]:
        row = self._fetchone(
            "SELECT * FROM user_google_drive_settings WHERE user_id = %s",
            (user_id,),
        )
        return self._to_user_google_drive_settings(row) if row else None

    def upsert_user_google_drive_settings(self, settings: UserGoogleDriveSettings) -> UserGoogleDriveSettings:
        self._execute(
            """
            INSERT INTO user_google_drive_settings (
                user_id,
                folder_id,
                folder_url,
                enabled,
                created_at,
                updated_at,
                last_successful_import_at,
                last_stale_alert_sent_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                folder_id = VALUES(folder_id),
                folder_url = VALUES(folder_url),
                enabled = VALUES(enabled),
                updated_at = VALUES(updated_at),
                last_successful_import_at = VALUES(last_successful_import_at),
                last_stale_alert_sent_at = VALUES(last_stale_alert_sent_at)
            """,
            (
                settings.user_id,
                settings.folder_id,
                settings.folder_url,
                1 if settings.enabled else 0,
                settings.created_at or datetime.now(),
                settings.updated_at or datetime.now(),
                settings.last_successful_import_at,
                settings.last_stale_alert_sent_at,
            ),
        )
        saved = self.get_user_google_drive_settings(settings.user_id)
        if saved is None:
            raise RuntimeError("Не удалось сохранить Google Drive settings для пользователя %s" % settings.user_id)
        return saved

    def list_users_with_google_drive_enabled(self) -> List[AppUser]:
        rows = self._fetchall(
            """
            SELECT u.*
            FROM users u
            INNER JOIN user_google_drive_settings s ON s.user_id = u.user_id
            WHERE s.enabled = 1
            ORDER BY u.user_id ASC
            """,
            (),
        )
        return [self._to_user(row) for row in rows]

    def create_health_import_file(self, imported_file: HealthImportFile) -> HealthImportFile:
        self._execute(
            """
            INSERT INTO health_import_files (
                import_id,
                user_id,
                provider,
                external_file_id,
                file_name,
                file_date,
                checksum,
                status,
                imported_at,
                activity_entries_count,
                sleep_entries_count,
                weight_entries_count,
                raw_metadata_json,
                error_message
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                file_name = VALUES(file_name),
                file_date = VALUES(file_date),
                checksum = VALUES(checksum),
                status = VALUES(status),
                imported_at = VALUES(imported_at),
                activity_entries_count = VALUES(activity_entries_count),
                sleep_entries_count = VALUES(sleep_entries_count),
                weight_entries_count = VALUES(weight_entries_count),
                raw_metadata_json = VALUES(raw_metadata_json),
                error_message = VALUES(error_message)
            """,
            (
                imported_file.import_id,
                imported_file.user_id,
                imported_file.provider.value,
                imported_file.external_file_id,
                imported_file.file_name,
                imported_file.file_date,
                imported_file.checksum,
                imported_file.status.value,
                imported_file.imported_at,
                imported_file.activity_entries_count,
                imported_file.sleep_entries_count,
                imported_file.weight_entries_count,
                imported_file.raw_metadata_json,
                imported_file.error_message,
            ),
        )
        row = self._fetchone(
            """
            SELECT *
            FROM health_import_files
            WHERE user_id = %s
              AND provider = %s
              AND external_file_id = %s
            """,
            (imported_file.user_id, imported_file.provider.value, imported_file.external_file_id),
        )
        if row is None:
            raise RuntimeError("Не удалось сохранить health import file %s" % imported_file.import_id)
        return self._to_health_import_file(row)

    def get_health_import_file(
        self,
        user_id: int,
        provider: HealthImportProvider,
        external_file_id: str,
    ) -> Optional[HealthImportFile]:
        row = self._fetchone(
            """
            SELECT *
            FROM health_import_files
            WHERE user_id = %s
              AND provider = %s
              AND external_file_id = %s
            """,
            (user_id, provider.value, external_file_id),
        )
        return self._to_health_import_file(row) if row else None

    def list_health_import_files(
        self,
        user_id: int,
        provider: Optional[HealthImportProvider] = None,
    ) -> List[HealthImportFile]:
        query = "SELECT * FROM health_import_files WHERE user_id = %s"
        params = [user_id]
        if provider is not None:
            query += " AND provider = %s"
            params.append(provider.value)
        query += " ORDER BY imported_at ASC, file_name ASC"
        rows = self._fetchall(query, tuple(params))
        return [self._to_health_import_file(row) for row in rows]

    def create_invite(self, invite: InviteCode) -> InviteCode:
        self._execute(
            """
            INSERT INTO invites (code, created_by_user_id, created_at, expires_at, max_uses, used_count, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                created_by_user_id = VALUES(created_by_user_id),
                created_at = VALUES(created_at),
                expires_at = VALUES(expires_at),
                max_uses = VALUES(max_uses),
                used_count = VALUES(used_count),
                status = VALUES(status)
            """,
            (
                invite.code,
                invite.created_by_user_id,
                invite.created_at,
                invite.expires_at,
                invite.max_uses,
                invite.used_count,
                invite.status.value,
            ),
        )
        saved = self.get_invite(invite.code)
        if saved is None:
            raise RuntimeError("Не удалось сохранить инвайт %s" % invite.code)
        return saved

    def get_invite(self, code: str) -> Optional[InviteCode]:
        row = self._fetchone("SELECT * FROM invites WHERE code = %s", (code,))
        return self._to_invite(row) if row else None

    def list_invites(self, status: Optional[InviteStatus] = None) -> List[InviteCode]:
        query = "SELECT * FROM invites"
        params = []
        if status is not None:
            query += " WHERE status = %s"
            params.append(status.value)
        query += " ORDER BY created_at DESC"
        rows = self._fetchall(query, tuple(params))
        return [self._to_invite(row) for row in rows]

    def increment_invite_usage(self, code: str, status: InviteStatus) -> None:
        self._execute(
            """
            UPDATE invites
            SET used_count = used_count + 1,
                status = %s
            WHERE code = %s
            """,
            (status.value, code),
        )

    def update_invite_status(self, code: str, status: InviteStatus) -> None:
        self._execute(
            """
            UPDATE invites
            SET status = %s
            WHERE code = %s
            """,
            (status.value, code),
        )

    def get_user_digest_settings(self, user_id: int) -> Optional[UserDigestSettings]:
        row = self._fetchone(
            "SELECT * FROM user_digest_settings WHERE user_id = %s",
            (user_id,),
        )
        return self._to_user_digest_settings(row) if row else None

    def upsert_user_digest_settings(self, settings: UserDigestSettings) -> UserDigestSettings:
        self._execute(
            """
            INSERT INTO user_digest_settings (
                user_id,
                timezone_name,
                daily_digest_enabled,
                daily_digest_time,
                weekly_digest_enabled,
                weekly_digest_time,
                weekly_digest_weekday
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                timezone_name = VALUES(timezone_name),
                daily_digest_enabled = VALUES(daily_digest_enabled),
                daily_digest_time = VALUES(daily_digest_time),
                weekly_digest_enabled = VALUES(weekly_digest_enabled),
                weekly_digest_time = VALUES(weekly_digest_time),
                weekly_digest_weekday = VALUES(weekly_digest_weekday)
            """,
            (
                settings.user_id,
                settings.timezone_name,
                1 if settings.daily_digest_enabled else 0,
                settings.daily_digest_time,
                1 if settings.weekly_digest_enabled else 0,
                settings.weekly_digest_time,
                settings.weekly_digest_weekday,
            ),
        )
        saved = self.get_user_digest_settings(settings.user_id)
        if saved is None:
            raise RuntimeError("Не удалось сохранить digest settings для пользователя %s" % settings.user_id)
        return saved

    def create_digest_run(self, run: DigestRun) -> DigestRun:
        self._execute(
            """
            INSERT INTO digest_runs (
                run_id,
                user_id,
                digest_type,
                digest_date,
                status,
                created_at,
                scheduled_for,
                sent_at,
                error_message,
                payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                scheduled_for = VALUES(scheduled_for),
                sent_at = VALUES(sent_at),
                error_message = VALUES(error_message),
                payload_json = VALUES(payload_json)
            """,
            (
                run.run_id,
                run.user_id,
                run.digest_type.value,
                run.digest_date,
                run.status.value,
                run.created_at,
                run.scheduled_for,
                run.sent_at,
                run.error_message,
                json.dumps(run.payload, sort_keys=True),
            ),
        )
        row = self._fetchone("SELECT * FROM digest_runs WHERE run_id = %s", (run.run_id,))
        if row is None:
            raise RuntimeError("Не удалось сохранить digest run %s" % run.run_id)
        return self._to_digest_run(row)

    def list_digest_runs(
        self,
        user_id: int,
        digest_type: Optional[DigestType] = None,
        status: Optional[DigestStatus] = None,
    ) -> List[DigestRun]:
        query = "SELECT * FROM digest_runs WHERE user_id = %s"
        params = [user_id]
        if digest_type is not None:
            query += " AND digest_type = %s"
            params.append(digest_type.value)
        if status is not None:
            query += " AND status = %s"
            params.append(status.value)
        query += " ORDER BY digest_date ASC, created_at ASC"
        rows = self._fetchall(query, tuple(params))
        return [self._to_digest_run(row) for row in rows]

    def update_digest_run(
        self,
        run_id: str,
        status: DigestStatus,
        sent_at: Optional[datetime] = None,
        error_message: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        current = self._fetchone("SELECT * FROM digest_runs WHERE run_id = %s", (run_id,))
        if current is None:
            raise ValueError("Digest run не найден: %s" % run_id)
        payload_json = current["payload_json"] if payload is None else json.dumps(payload, sort_keys=True)
        self._execute(
            """
            UPDATE digest_runs
            SET status = %s,
                sent_at = %s,
                error_message = %s,
                payload_json = %s
            WHERE run_id = %s
            """,
            (status.value, sent_at, error_message, payload_json, run_id),
        )

    def set_health_goals(self, user_id: int, goals: DailyHealthGoals) -> None:
        self._execute(
            """
            INSERT INTO health_goals (user_id, target_date, water_ml, protein_g, sleep_hours, steps)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                water_ml = VALUES(water_ml),
                protein_g = VALUES(protein_g),
                sleep_hours = VALUES(sleep_hours),
                steps = VALUES(steps)
            """,
            (
                user_id,
                goals.target_date,
                goals.water_ml,
                goals.protein_g,
                goals.sleep_hours,
                goals.steps,
            ),
        )

    def get_health_goals(self, user_id: int, target_date: date) -> DailyHealthGoals:
        row = self._fetchone(
            "SELECT * FROM health_goals WHERE user_id = %s AND target_date = %s",
            (user_id, target_date),
        )
        if not row:
            return DailyHealthGoals(target_date=target_date)
        return DailyHealthGoals(
            target_date=row["target_date"],
            water_ml=row["water_ml"],
            protein_g=row["protein_g"],
            sleep_hours=float(row["sleep_hours"]),
            steps=row["steps"],
        )

    def add_meal(self, user_id: int, entry: MealEntry) -> None:
        self._execute(
            """
            INSERT INTO meals (entry_id, user_id, occurred_at, title, calories, protein_g, fat_g, carbs_g, water_ml, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entry.entry_id,
                user_id,
                entry.occurred_at,
                entry.title,
                entry.calories,
                entry.protein_g,
                entry.fat_g,
                entry.carbs_g,
                entry.water_ml,
                entry.notes,
            ),
        )

    def list_meals(self, user_id: int, target_date: date) -> List[MealEntry]:
        day_start = datetime.combine(target_date, time.min)
        day_end = datetime.combine(target_date, time.max)
        rows = self._fetchall(
            """
            SELECT *
            FROM meals
            WHERE user_id = %s
              AND occurred_at BETWEEN %s AND %s
            ORDER BY occurred_at ASC
            """,
            (user_id, day_start, day_end),
        )
        return [self._to_meal_entry(row) for row in rows]

    def list_meals_in_range(self, user_id: int, start_date: date, end_date: date) -> List[MealEntry]:
        day_start = datetime.combine(start_date, time.min)
        day_end = datetime.combine(end_date, time.max)
        rows = self._fetchall(
            """
            SELECT *
            FROM meals
            WHERE user_id = %s
              AND occurred_at BETWEEN %s AND %s
            ORDER BY occurred_at ASC
            """,
            (user_id, day_start, day_end),
        )
        return [self._to_meal_entry(row) for row in rows]

    def create_meal_draft(self, user_id: int, draft: MealPhotoDraft) -> None:
        self._execute(
            """
            INSERT INTO meal_photo_drafts (
                draft_id,
                user_id,
                created_at,
                occurred_at,
                title,
                summary,
                calories,
                protein_g,
                fat_g,
                carbs_g,
                water_ml,
                confidence,
                photo_file_id,
                photo_unique_id,
                status,
                source,
                items_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                draft.draft_id,
                user_id,
                draft.created_at,
                draft.occurred_at,
                draft.title,
                draft.summary,
                draft.calories,
                draft.protein_g,
                draft.fat_g,
                draft.carbs_g,
                draft.water_ml,
                draft.confidence,
                draft.photo_file_id,
                draft.photo_unique_id,
                draft.status.value,
                draft.source,
                json.dumps([item.__dict__ for item in draft.items], sort_keys=True),
            ),
        )

    def create_meal_media(self, media: MealMedia) -> None:
        self._execute(
            """
            INSERT INTO meal_media (
                media_id,
                user_id,
                draft_id,
                meal_entry_id,
                occurred_at,
                created_at,
                mime_type,
                telegram_file_id,
                telegram_unique_id,
                byte_size,
                sha256,
                storage_kind,
                storage_key,
                bucket_name,
                width,
                height,
                image_bytes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                media.media_id,
                media.user_id,
                media.draft_id,
                media.meal_entry_id or None,
                media.occurred_at,
                media.created_at,
                media.mime_type,
                media.telegram_file_id,
                media.telegram_unique_id,
                media.byte_size,
                media.sha256,
                media.storage_kind,
                media.storage_key,
                media.bucket_name,
                media.width,
                media.height,
                media.image_bytes,
            ),
        )

    def list_meal_media(self, user_id: int, target_date: Optional[date] = None) -> List[MealMedia]:
        query = """
            SELECT *
            FROM meal_media
            WHERE user_id = %s
        """
        params = [user_id]
        if target_date is not None:
            day_start = datetime.combine(target_date, time.min)
            day_end = datetime.combine(target_date, time.max)
            query += " AND occurred_at BETWEEN %s AND %s"
            params.extend([day_start, day_end])
        query += " ORDER BY occurred_at ASC, created_at ASC"
        rows = self._fetchall(query, tuple(params))
        return [self._to_meal_media(row) for row in rows]

    def list_meal_media_in_range(
        self,
        user_id: int,
        start_date: date,
        end_date: date,
        include_image_bytes: bool = True,
    ) -> List[MealMedia]:
        day_start = datetime.combine(start_date, time.min)
        day_end = datetime.combine(end_date, time.max)
        select_fields = "*" if include_image_bytes else (
            "media_id, user_id, draft_id, meal_entry_id, occurred_at, created_at, mime_type, "
            "telegram_file_id, telegram_unique_id, byte_size, sha256, storage_kind, storage_key, "
            "bucket_name, width, height, NULL AS image_bytes"
        )
        rows = self._fetchall(
            """
            SELECT %s
            FROM meal_media
            WHERE user_id = %%s
              AND occurred_at BETWEEN %%s AND %%s
            ORDER BY occurred_at ASC, created_at ASC
            """
            % select_fields,
            (user_id, day_start, day_end),
        )
        return [self._to_meal_media(row) for row in rows]

    def list_meal_media_by_ids(self, user_id: int, media_ids: List[str]) -> List[MealMedia]:
        if not media_ids:
            return []
        placeholders = ", ".join(["%s"] * len(media_ids))
        rows = self._fetchall(
            """
            SELECT *
            FROM meal_media
            WHERE user_id = %s
              AND media_id IN ({placeholders})
            ORDER BY occurred_at ASC, created_at ASC
            """.format(placeholders=placeholders),
            tuple([user_id] + media_ids),
        )
        return [self._to_meal_media(row) for row in rows]

    def attach_meal_media_to_meal(self, user_id: int, draft_id: str, meal_entry_id: str) -> None:
        self._execute(
            """
            UPDATE meal_media
            SET meal_entry_id = %s
            WHERE user_id = %s
              AND draft_id = %s
            """,
            (meal_entry_id, user_id, draft_id),
        )

    def list_legacy_meal_media_for_migration(self, limit: int = 100) -> List[MealMedia]:
        rows = self._fetchall(
            """
            SELECT *
            FROM meal_media
            WHERE storage_kind = %s
              AND OCTET_LENGTH(image_bytes) > 0
            ORDER BY occurred_at ASC, created_at ASC
            LIMIT %s
            """,
            ("db_blob", limit),
        )
        return [self._to_meal_media(row) for row in rows]

    def mark_meal_media_bucket_migrated(
        self,
        media_id: str,
        *,
        storage_key: str,
        bucket_name: str,
        width: int,
        height: int,
    ) -> None:
        self._execute(
            """
            UPDATE meal_media
            SET storage_kind = %s,
                storage_key = %s,
                bucket_name = %s,
                width = %s,
                height = %s,
                image_bytes = %s
            WHERE media_id = %s
            """,
            ("railway_bucket", storage_key, bucket_name, width, height, b"", media_id),
        )

    def get_meal_draft(self, user_id: int, draft_id: str) -> Optional[MealPhotoDraft]:
        row = self._fetchone(
            "SELECT * FROM meal_photo_drafts WHERE user_id = %s AND draft_id = %s",
            (user_id, draft_id),
        )
        return self._to_meal_draft(row) if row else None

    def list_meal_drafts(self, user_id: int, status: MealDraftStatus) -> List[MealPhotoDraft]:
        rows = self._fetchall(
            """
            SELECT *
            FROM meal_photo_drafts
            WHERE user_id = %s AND status = %s
            ORDER BY created_at ASC
            """,
            (user_id, status.value),
        )
        return [self._to_meal_draft(row) for row in rows]

    def update_meal_draft_status(self, user_id: int, draft_id: str, status: MealDraftStatus) -> None:
        self._execute(
            """
            UPDATE meal_photo_drafts
            SET status = %s
            WHERE user_id = %s AND draft_id = %s
            """,
            (status.value, user_id, draft_id),
        )

    def add_water(self, user_id: int, entry: WaterEntry) -> None:
        self._execute(
            """
            INSERT INTO water_entries (entry_id, user_id, occurred_at, amount_ml)
            VALUES (%s, %s, %s, %s)
            """,
            (entry.entry_id, user_id, entry.occurred_at, entry.amount_ml),
        )

    def add_sleep(self, user_id: int, entry: SleepEntry) -> None:
        self._execute(
            """
            INSERT INTO sleep_entries (entry_id, user_id, start_at, end_at, quality_score, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                entry.entry_id,
                user_id,
                entry.start_at,
                entry.end_at,
                entry.quality_score,
                entry.notes,
            ),
        )

    def add_weight(self, user_id: int, entry: WeightEntry) -> None:
        self._execute(
            """
            INSERT INTO weight_entries (entry_id, user_id, occurred_at, weight_kg)
            VALUES (%s, %s, %s, %s)
            """,
            (entry.entry_id, user_id, entry.occurred_at, entry.weight_kg),
        )

    def add_activity(self, user_id: int, entry: ActivityEntry) -> None:
        self._execute(
            """
            INSERT INTO activity_entries (
                entry_id,
                user_id,
                occurred_at,
                title,
                duration_minutes,
                steps,
                calories_burned,
                intensity
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                occurred_at = VALUES(occurred_at),
                title = VALUES(title),
                duration_minutes = VALUES(duration_minutes),
                steps = VALUES(steps),
                calories_burned = VALUES(calories_burned),
                intensity = VALUES(intensity)
            """,
            (
                entry.entry_id,
                user_id,
                entry.occurred_at,
                entry.title,
                entry.duration_minutes,
                entry.steps,
                entry.calories_burned,
                entry.intensity,
            ),
        )

    def delete_activity(self, user_id: int, entry_id: str) -> None:
        self._execute(
            "DELETE FROM activity_entries WHERE user_id = %s AND entry_id = %s",
            (user_id, entry_id),
        )

    def get_daily_water_total(self, user_id: int, target_date: date) -> int:
        day_start = datetime.combine(target_date, time.min)
        day_end = datetime.combine(target_date, time.max)
        row = self._fetchone(
            """
            SELECT COALESCE(SUM(amount_ml), 0) AS water_ml
            FROM water_entries
            WHERE user_id = %s
              AND occurred_at BETWEEN %s AND %s
            """,
            (user_id, day_start, day_end),
        )
        if not row:
            return 0
        return int(row["water_ml"])

    def list_daily_step_totals(self, user_id: int, date_from: date, date_to: date) -> List[tuple[date, int]]:
        period_start = datetime.combine(date_from, time.min)
        period_end = datetime.combine(date_to, time.max)
        rows = self._fetchall(
            """
            SELECT DATE(occurred_at) AS activity_date,
                   COALESCE(SUM(steps), 0) AS steps
            FROM activity_entries
            WHERE user_id = %s
              AND occurred_at BETWEEN %s AND %s
            GROUP BY DATE(occurred_at)
            ORDER BY activity_date ASC
            """,
            (user_id, period_start, period_end),
        )
        return [(row["activity_date"], int(row["steps"])) for row in rows]

    def list_activity_entries(self, user_id: int, date_from: date, date_to: date) -> List[ActivityEntry]:
        period_start = datetime.combine(date_from, time.min)
        period_end = datetime.combine(date_to, time.max)
        rows = self._fetchall(
            """
            SELECT *
            FROM activity_entries
            WHERE user_id = %s
              AND occurred_at BETWEEN %s AND %s
            ORDER BY occurred_at ASC
            """,
            (user_id, period_start, period_end),
        )
        return [self._to_activity_entry(row) for row in rows]

    def build_health_summary(self, user_id: int, target_date: date) -> DailyHealthSummary:
        day_start = datetime.combine(target_date, time.min)
        day_end = datetime.combine(target_date, time.max)

        meals = self._fetchone(
            """
            SELECT COUNT(*) AS meals_count,
                   COALESCE(SUM(calories), 0) AS calories,
                   COALESCE(SUM(protein_g), 0) AS protein_g,
                   COALESCE(SUM(fat_g), 0) AS fat_g,
                   COALESCE(SUM(carbs_g), 0) AS carbs_g,
                   COALESCE(SUM(water_ml), 0) AS meal_water_ml
            FROM meals
            WHERE user_id = %s
              AND occurred_at BETWEEN %s AND %s
            """,
            (user_id, day_start, day_end),
        )
        water = self._fetchone(
            """
            SELECT COALESCE(SUM(amount_ml), 0) AS water_ml
            FROM water_entries
            WHERE user_id = %s
              AND occurred_at BETWEEN %s AND %s
            """,
            (user_id, day_start, day_end),
        )
        activity = self._fetchone(
            """
            SELECT COALESCE(SUM(steps), 0) AS steps,
                   COALESCE(SUM(duration_minutes), 0) AS activity_minutes
            FROM activity_entries
            WHERE user_id = %s
              AND occurred_at BETWEEN %s AND %s
            """,
            (user_id, day_start, day_end),
        )
        latest_weight = self._fetchone(
            """
            SELECT weight_kg
            FROM weight_entries
            WHERE user_id = %s
              AND occurred_at BETWEEN %s AND %s
            ORDER BY occurred_at DESC
            LIMIT 1
            """,
            (user_id, day_start, day_end),
        )
        sleep_rows = self._fetchall(
            """
            SELECT start_at, end_at
            FROM sleep_entries
            WHERE user_id = %s
              AND end_at BETWEEN %s AND %s
            """,
            (user_id, day_start, day_end),
        )

        sleep_hours = 0.0
        for row in sleep_rows:
            sleep_hours += round((row["end_at"] - row["start_at"]).total_seconds() / 3600, 2)

        return DailyHealthSummary(
            target_date=target_date,
            meals_count=int(meals["meals_count"]),
            calories=int(meals["calories"]),
            protein_g=round(float(meals["protein_g"]), 2),
            fat_g=round(float(meals["fat_g"]), 2),
            carbs_g=round(float(meals["carbs_g"]), 2),
            water_ml=int(water["water_ml"]) + int(meals["meal_water_ml"]),
            sleep_hours=round(sleep_hours, 2),
            steps=int(activity["steps"]),
            activity_minutes=int(activity["activity_minutes"]),
            latest_weight_kg=float(latest_weight["weight_kg"]) if latest_weight else None,
            goals=self.get_health_goals(user_id, target_date),
        )

    def upsert_decisions(self, user_id: int, decisions: Iterable[DecisionLogEntry]) -> List[DecisionLogEntry]:
        inserted = []
        for decision in decisions:
            rowcount = self._execute(
                """
                INSERT INTO decision_log (
                    decision_id,
                    user_id,
                    decision_key,
                    created_at,
                    agent,
                    kind,
                    title,
                    rationale,
                    context_date,
                    status,
                    payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    decision_id = decision_id
                """,
                (
                    decision.decision_id,
                    user_id,
                    decision.decision_key,
                    decision.created_at,
                    decision.agent,
                    decision.kind.value,
                    decision.title,
                    decision.rationale,
                    decision.context_date,
                    decision.status.value,
                    json.dumps(decision.payload, sort_keys=True),
                ),
            )
            if rowcount == 1:
                inserted.append(decision)
        return inserted

    def list_decisions(
        self,
        user_id: int,
        status: Optional[DecisionStatus] = None,
        context_date: Optional[date] = None,
    ) -> List[DecisionLogEntry]:
        query = """
            SELECT *
            FROM decision_log
            WHERE user_id = %s
        """
        params = [user_id]
        if status is not None:
            query += " AND status = %s"
            params.append(status.value)
        if context_date is not None:
            query += " AND context_date = %s"
            params.append(context_date)
        query += " ORDER BY created_at ASC"
        rows = self._fetchall(query, tuple(params))
        return [self._to_decision(row) for row in rows]

    def update_decision_status(self, user_id: int, decision_id: str, status: DecisionStatus) -> None:
        self._execute(
            "UPDATE decision_log SET status = %s WHERE user_id = %s AND decision_id = %s",
            (status.value, user_id, decision_id),
        )

    def upsert_finance_transactions(self, user_id: int, transactions: Iterable[FinanceTransaction]) -> int:
        inserted = 0
        for transaction in transactions:
            rowcount = self._execute(
                """
                INSERT INTO finance_transactions (
                    user_id,
                    transaction_key,
                    provider,
                    occurred_at,
                    amount,
                    currency,
                    title,
                    category,
                    mcc,
                    status,
                    account_name,
                    source_file_name,
                    raw_payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    transaction_key = transaction_key
                """,
                (
                    user_id,
                    transaction.transaction_key,
                    transaction.provider,
                    transaction.occurred_at,
                    transaction.amount,
                    transaction.currency,
                    transaction.title,
                    transaction.category,
                    transaction.mcc,
                    transaction.status,
                    transaction.account_name,
                    transaction.source_file_name,
                    transaction.raw_payload,
                ),
            )
            if rowcount == 1:
                inserted += 1
        return inserted

    def build_finance_monthly_summary(self, user_id: int, month_start: date) -> FinanceMonthlySummary:
        if month_start.month == 12:
            next_month = date(month_start.year + 1, 1, 1)
        else:
            next_month = date(month_start.year, month_start.month + 1, 1)
        period_start = datetime.combine(month_start, time.min)
        period_end = datetime.combine(next_month, time.min)

        totals = self._fetchone(
            """
            SELECT COUNT(*) AS transaction_count,
                   COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0) AS income_total,
                   COALESCE(ABS(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)), 0) AS expense_total
            FROM finance_transactions
            WHERE user_id = %s
              AND occurred_at >= %s
              AND occurred_at < %s
            """,
            (user_id, period_start, period_end),
        )
        category_rows = self._fetchall(
            """
            SELECT COALESCE(NULLIF(category, ''), 'Без категории') AS category,
                   ABS(SUM(amount)) AS expense_amount,
                   COUNT(*) AS transaction_count
            FROM finance_transactions
            WHERE user_id = %s
              AND occurred_at >= %s
              AND occurred_at < %s
              AND amount < 0
            GROUP BY COALESCE(NULLIF(category, ''), 'Без категории')
            ORDER BY expense_amount DESC
            LIMIT 5
            """,
            (user_id, period_start, period_end),
        )
        top_categories = [
            FinanceCategoryTotal(
                category=row["category"],
                amount=round(float(row["expense_amount"]), 2),
                transaction_count=int(row["transaction_count"]),
            )
            for row in category_rows
        ]
        income_total = round(float(totals["income_total"]), 2)
        expense_total = round(float(totals["expense_total"]), 2)
        return FinanceMonthlySummary(
            month_start=month_start,
            month_end=next_month,
            transaction_count=int(totals["transaction_count"]),
            income_total=income_total,
            expense_total=expense_total,
            net_total=round(income_total - expense_total, 2),
            top_expense_categories=top_categories,
        )

    def _init_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                telegram_user_id BIGINT NOT NULL UNIQUE,
                chat_id BIGINT NOT NULL,
                username VARCHAR(255) NOT NULL DEFAULT '',
                first_name VARCHAR(255) NOT NULL DEFAULT '',
                status VARCHAR(32) NOT NULL,
                is_admin TINYINT(1) NOT NULL DEFAULT 0,
                admin_mode_enabled TINYINT(1) NOT NULL DEFAULT 1,
                created_at DATETIME(6) NOT NULL,
                INDEX idx_users_status (status)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS invites (
                code VARCHAR(32) PRIMARY KEY,
                created_by_user_id BIGINT NOT NULL,
                created_at DATETIME(6) NOT NULL,
                expires_at DATETIME(6) NULL,
                max_uses INT NOT NULL,
                used_count INT NOT NULL DEFAULT 0,
                status VARCHAR(32) NOT NULL,
                INDEX idx_invites_status (status),
                INDEX idx_invites_expires_at (expires_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS user_digest_settings (
                user_id BIGINT NOT NULL PRIMARY KEY,
                timezone_name VARCHAR(64) NOT NULL,
                daily_digest_enabled TINYINT(1) NOT NULL DEFAULT 1,
                daily_digest_time VARCHAR(5) NOT NULL DEFAULT '08:00',
                weekly_digest_enabled TINYINT(1) NOT NULL DEFAULT 1,
                weekly_digest_time VARCHAR(5) NOT NULL DEFAULT '08:00',
                weekly_digest_weekday INT NOT NULL DEFAULT 0
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS user_google_drive_settings (
                user_id BIGINT NOT NULL PRIMARY KEY,
                folder_id VARCHAR(255) NOT NULL,
                folder_url TEXT NOT NULL,
                enabled TINYINT(1) NOT NULL DEFAULT 1,
                created_at DATETIME(6) NOT NULL,
                updated_at DATETIME(6) NOT NULL,
                last_successful_import_at DATETIME(6) NULL,
                last_stale_alert_sent_at DATETIME(6) NULL,
                INDEX idx_google_drive_enabled (enabled)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS digest_runs (
                run_id VARCHAR(64) NOT NULL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                digest_type VARCHAR(16) NOT NULL,
                digest_date DATE NOT NULL,
                status VARCHAR(32) NOT NULL,
                created_at DATETIME(6) NOT NULL,
                scheduled_for DATETIME(6) NULL,
                sent_at DATETIME(6) NULL,
                error_message TEXT NOT NULL,
                payload_json LONGTEXT NOT NULL,
                UNIQUE KEY uk_digest_user_type_date (user_id, digest_type, digest_date),
                INDEX idx_digest_runs_user_status (user_id, status),
                INDEX idx_digest_runs_user_date (user_id, digest_date)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS health_import_files (
                import_id VARCHAR(64) NOT NULL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                provider VARCHAR(32) NOT NULL,
                external_file_id VARCHAR(255) NOT NULL,
                file_name VARCHAR(255) NOT NULL,
                file_date DATE NULL,
                checksum VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL,
                imported_at DATETIME(6) NOT NULL,
                activity_entries_count INT NOT NULL DEFAULT 0,
                sleep_entries_count INT NOT NULL DEFAULT 0,
                weight_entries_count INT NOT NULL DEFAULT 0,
                raw_metadata_json LONGTEXT NOT NULL,
                error_message TEXT NOT NULL,
                UNIQUE KEY uk_health_import_user_provider_file (user_id, provider, external_file_id),
                INDEX idx_health_import_user_provider (user_id, provider),
                INDEX idx_health_import_user_file_date (user_id, file_date)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS health_goals (
                goal_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                target_date DATE NOT NULL,
                water_ml INT NOT NULL,
                protein_g INT NOT NULL,
                sleep_hours DOUBLE NOT NULL,
                steps INT NOT NULL,
                UNIQUE KEY uk_health_goals_user_date (user_id, target_date),
                INDEX idx_health_goals_target_date (target_date)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS meals (
                entry_id VARCHAR(64) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                occurred_at DATETIME(6) NOT NULL,
                title VARCHAR(255) NOT NULL,
                calories INT NOT NULL,
                protein_g DOUBLE NOT NULL,
                fat_g DOUBLE NOT NULL DEFAULT 0,
                carbs_g DOUBLE NOT NULL DEFAULT 0,
                water_ml INT NOT NULL DEFAULT 0,
                notes TEXT NOT NULL,
                INDEX idx_meals_user_occurred_at (user_id, occurred_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS meal_photo_drafts (
                draft_id VARCHAR(64) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at DATETIME(6) NOT NULL,
                occurred_at DATETIME(6) NOT NULL,
                title VARCHAR(255) NOT NULL,
                summary TEXT NOT NULL,
                calories INT NOT NULL,
                protein_g DOUBLE NOT NULL,
                fat_g DOUBLE NOT NULL,
                carbs_g DOUBLE NOT NULL,
                water_ml INT NOT NULL DEFAULT 0,
                confidence DOUBLE NOT NULL,
                photo_file_id VARCHAR(255) NOT NULL,
                photo_unique_id VARCHAR(255) NOT NULL,
                status VARCHAR(64) NOT NULL,
                source VARCHAR(64) NOT NULL,
                items_json LONGTEXT NOT NULL,
                INDEX idx_meal_drafts_user_status_created_at (user_id, status, created_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS meal_media (
                media_id VARCHAR(64) NOT NULL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                draft_id VARCHAR(64) NOT NULL,
                meal_entry_id VARCHAR(64) NULL,
                occurred_at DATETIME(6) NOT NULL,
                created_at DATETIME(6) NOT NULL,
                mime_type VARCHAR(64) NOT NULL,
                telegram_file_id VARCHAR(255) NOT NULL,
                telegram_unique_id VARCHAR(255) NOT NULL,
                byte_size INT NOT NULL,
                sha256 VARCHAR(64) NOT NULL,
                storage_kind VARCHAR(32) NOT NULL,
                storage_key VARCHAR(255) NOT NULL DEFAULT '',
                bucket_name VARCHAR(255) NOT NULL DEFAULT '',
                width INT NOT NULL DEFAULT 0,
                height INT NOT NULL DEFAULT 0,
                image_bytes LONGBLOB NOT NULL,
                INDEX idx_meal_media_user_occurred_at (user_id, occurred_at),
                INDEX idx_meal_media_user_draft_id (user_id, draft_id),
                INDEX idx_meal_media_user_meal_entry_id (user_id, meal_entry_id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS water_entries (
                entry_id VARCHAR(64) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                occurred_at DATETIME(6) NOT NULL,
                amount_ml INT NOT NULL,
                INDEX idx_water_user_occurred_at (user_id, occurred_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS sleep_entries (
                entry_id VARCHAR(64) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                start_at DATETIME(6) NOT NULL,
                end_at DATETIME(6) NOT NULL,
                quality_score INT NULL,
                notes TEXT NOT NULL,
                INDEX idx_sleep_user_end_at (user_id, end_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS weight_entries (
                entry_id VARCHAR(64) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                occurred_at DATETIME(6) NOT NULL,
                weight_kg DOUBLE NOT NULL,
                INDEX idx_weight_user_occurred_at (user_id, occurred_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS activity_entries (
                entry_id VARCHAR(64) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                occurred_at DATETIME(6) NOT NULL,
                title VARCHAR(255) NOT NULL,
                duration_minutes INT NOT NULL,
                steps INT NOT NULL,
                calories_burned INT NOT NULL,
                intensity VARCHAR(32) NOT NULL,
                INDEX idx_activity_user_occurred_at (user_id, occurred_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS decision_log (
                decision_id VARCHAR(64) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                decision_key VARCHAR(128) NOT NULL,
                created_at DATETIME(6) NOT NULL,
                agent VARCHAR(64) NOT NULL,
                kind VARCHAR(64) NOT NULL,
                title VARCHAR(255) NOT NULL,
                rationale TEXT NOT NULL,
                context_date DATE NOT NULL,
                status VARCHAR(64) NOT NULL,
                payload LONGTEXT NOT NULL,
                UNIQUE KEY uk_decision_user_key (user_id, decision_key),
                INDEX idx_decision_user_context_date (user_id, context_date),
                INDEX idx_decision_user_status (user_id, status)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS finance_transactions (
                finance_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                transaction_key VARCHAR(64) NOT NULL,
                provider VARCHAR(32) NOT NULL,
                occurred_at DATETIME(6) NOT NULL,
                amount DOUBLE NOT NULL,
                currency VARCHAR(16) NOT NULL,
                title VARCHAR(255) NOT NULL,
                category VARCHAR(255) NOT NULL,
                mcc VARCHAR(32) NOT NULL,
                status VARCHAR(64) NOT NULL,
                account_name VARCHAR(255) NOT NULL,
                source_file_name VARCHAR(255) NOT NULL,
                raw_payload LONGTEXT NOT NULL,
                UNIQUE KEY uk_finance_user_key (user_id, transaction_key),
                INDEX idx_finance_user_occurred_at (user_id, occurred_at),
                INDEX idx_finance_user_provider (user_id, provider)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
        ]
        connection = self._connect()
        try:
            cursor = connection.cursor()
            for statement in statements:
                cursor.execute(statement)
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        self._apply_schema_migrations()
        self._ensure_owner_user_and_backfill()

    def _apply_schema_migrations(self) -> None:
        self._ensure_column(
            "users",
            "admin_mode_enabled",
            "ALTER TABLE users ADD COLUMN admin_mode_enabled TINYINT(1) NOT NULL DEFAULT 1 AFTER is_admin",
        )
        self._ensure_column("meals", "fat_g", "ALTER TABLE meals ADD COLUMN fat_g DOUBLE NOT NULL DEFAULT 0 AFTER protein_g")
        self._ensure_column("meals", "carbs_g", "ALTER TABLE meals ADD COLUMN carbs_g DOUBLE NOT NULL DEFAULT 0 AFTER fat_g")
        self._ensure_column("meals", "water_ml", "ALTER TABLE meals ADD COLUMN water_ml INT NOT NULL DEFAULT 0 AFTER carbs_g")
        self._ensure_column("meal_photo_drafts", "fat_g", "ALTER TABLE meal_photo_drafts ADD COLUMN fat_g DOUBLE NOT NULL DEFAULT 0 AFTER protein_g")
        self._ensure_column("meal_photo_drafts", "carbs_g", "ALTER TABLE meal_photo_drafts ADD COLUMN carbs_g DOUBLE NOT NULL DEFAULT 0 AFTER fat_g")
        self._ensure_column("meal_photo_drafts", "water_ml", "ALTER TABLE meal_photo_drafts ADD COLUMN water_ml INT NOT NULL DEFAULT 0 AFTER carbs_g")
        self._ensure_column("meal_media", "storage_key", "ALTER TABLE meal_media ADD COLUMN storage_key VARCHAR(255) NOT NULL DEFAULT '' AFTER storage_kind")
        self._ensure_column("meal_media", "bucket_name", "ALTER TABLE meal_media ADD COLUMN bucket_name VARCHAR(255) NOT NULL DEFAULT '' AFTER storage_key")
        self._ensure_column("meal_media", "width", "ALTER TABLE meal_media ADD COLUMN width INT NOT NULL DEFAULT 0 AFTER bucket_name")
        self._ensure_column("meal_media", "height", "ALTER TABLE meal_media ADD COLUMN height INT NOT NULL DEFAULT 0 AFTER width")

        self._ensure_column(
            "health_goals",
            "user_id",
            "ALTER TABLE health_goals ADD COLUMN user_id BIGINT NULL AFTER target_date",
        )
        self._ensure_column("meals", "user_id", "ALTER TABLE meals ADD COLUMN user_id BIGINT NULL AFTER entry_id")
        self._ensure_column(
            "meal_photo_drafts",
            "user_id",
            "ALTER TABLE meal_photo_drafts ADD COLUMN user_id BIGINT NULL AFTER draft_id",
        )
        self._ensure_column("water_entries", "user_id", "ALTER TABLE water_entries ADD COLUMN user_id BIGINT NULL AFTER entry_id")
        self._ensure_column("sleep_entries", "user_id", "ALTER TABLE sleep_entries ADD COLUMN user_id BIGINT NULL AFTER entry_id")
        self._ensure_column("weight_entries", "user_id", "ALTER TABLE weight_entries ADD COLUMN user_id BIGINT NULL AFTER entry_id")
        self._ensure_column(
            "activity_entries",
            "user_id",
            "ALTER TABLE activity_entries ADD COLUMN user_id BIGINT NULL AFTER entry_id",
        )
        self._ensure_column(
            "user_google_drive_settings",
            "last_successful_import_at",
            "ALTER TABLE user_google_drive_settings ADD COLUMN last_successful_import_at DATETIME(6) NULL AFTER updated_at",
        )
        self._ensure_column(
            "user_google_drive_settings",
            "last_stale_alert_sent_at",
            "ALTER TABLE user_google_drive_settings ADD COLUMN last_stale_alert_sent_at DATETIME(6) NULL AFTER last_successful_import_at",
        )
        self._ensure_column("decision_log", "user_id", "ALTER TABLE decision_log ADD COLUMN user_id BIGINT NULL AFTER decision_id")
        self._ensure_column(
            "finance_transactions",
            "user_id",
            "ALTER TABLE finance_transactions ADD COLUMN user_id BIGINT NULL AFTER transaction_key",
        )

        if not self._column_exists("health_goals", "goal_id"):
            self._execute(
                """
                ALTER TABLE health_goals
                DROP PRIMARY KEY,
                ADD COLUMN goal_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST
                """,
                (),
            )
        if not self._column_exists("finance_transactions", "finance_id"):
            self._execute(
                """
                ALTER TABLE finance_transactions
                DROP PRIMARY KEY,
                ADD COLUMN finance_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST
                """,
                (),
            )

        if self._index_exists("decision_log", "decision_key"):
            self._execute("ALTER TABLE decision_log DROP INDEX decision_key", ())
        self._ensure_unique_index("health_goals", "uk_health_goals_user_date", "CREATE UNIQUE INDEX uk_health_goals_user_date ON health_goals (user_id, target_date)")
        self._ensure_index("meals", "idx_meals_user_occurred_at", "CREATE INDEX idx_meals_user_occurred_at ON meals (user_id, occurred_at)")
        self._ensure_index(
            "meal_photo_drafts",
            "idx_meal_drafts_user_status_created_at",
            "CREATE INDEX idx_meal_drafts_user_status_created_at ON meal_photo_drafts (user_id, status, created_at)",
        )
        self._ensure_index(
            "water_entries",
            "idx_water_user_occurred_at",
            "CREATE INDEX idx_water_user_occurred_at ON water_entries (user_id, occurred_at)",
        )
        self._ensure_index(
            "sleep_entries",
            "idx_sleep_user_end_at",
            "CREATE INDEX idx_sleep_user_end_at ON sleep_entries (user_id, end_at)",
        )
        self._ensure_index(
            "weight_entries",
            "idx_weight_user_occurred_at",
            "CREATE INDEX idx_weight_user_occurred_at ON weight_entries (user_id, occurred_at)",
        )
        self._ensure_index(
            "activity_entries",
            "idx_activity_user_occurred_at",
            "CREATE INDEX idx_activity_user_occurred_at ON activity_entries (user_id, occurred_at)",
        )
        self._ensure_unique_index(
            "decision_log",
            "uk_decision_user_key",
            "CREATE UNIQUE INDEX uk_decision_user_key ON decision_log (user_id, decision_key)",
        )
        self._ensure_index(
            "decision_log",
            "idx_decision_user_context_date",
            "CREATE INDEX idx_decision_user_context_date ON decision_log (user_id, context_date)",
        )
        self._ensure_index(
            "decision_log",
            "idx_decision_user_status",
            "CREATE INDEX idx_decision_user_status ON decision_log (user_id, status)",
        )
        self._ensure_unique_index(
            "finance_transactions",
            "uk_finance_user_key",
            "CREATE UNIQUE INDEX uk_finance_user_key ON finance_transactions (user_id, transaction_key)",
        )
        self._ensure_index(
            "finance_transactions",
            "idx_finance_user_occurred_at",
            "CREATE INDEX idx_finance_user_occurred_at ON finance_transactions (user_id, occurred_at)",
        )
        self._ensure_index(
            "finance_transactions",
            "idx_finance_user_provider",
            "CREATE INDEX idx_finance_user_provider ON finance_transactions (user_id, provider)",
        )

    def _ensure_owner_user_and_backfill(self) -> None:
        owner = self.get_user_by_telegram_user_id(self.owner_telegram_user_id)
        if owner is None:
            owner = self.create_user(
                telegram_user_id=self.owner_telegram_user_id,
                chat_id=self.owner_telegram_user_id,
                username="",
                first_name="",
                status=UserStatus.ACTIVE,
                is_admin=True,
            )
        elif not owner.is_admin or owner.status != UserStatus.ACTIVE:
            self._execute(
                """
                UPDATE users
                SET is_admin = 1,
                    admin_mode_enabled = 1,
                    status = %s
                WHERE user_id = %s
                """,
                (UserStatus.ACTIVE.value, owner.user_id),
            )

        user_tables = [
            "health_goals",
            "meals",
            "meal_photo_drafts",
            "water_entries",
            "sleep_entries",
            "weight_entries",
            "activity_entries",
            "decision_log",
            "finance_transactions",
        ]
        for table_name in user_tables:
            if not self._column_exists(table_name, "user_id"):
                continue
            self._execute(
                "UPDATE %s SET user_id = %%s WHERE user_id IS NULL" % table_name,
                (owner.user_id,),
            )

    def _ensure_column(self, table_name: str, column_name: str, alter_statement: str) -> None:
        if self._column_exists(table_name, column_name):
            return
        logger.info("Applying MySQL schema migration: add %s.%s", table_name, column_name)
        self._execute(alter_statement, ())

    def _ensure_index(self, table_name: str, index_name: str, create_statement: str) -> None:
        if self._index_exists(table_name, index_name):
            return
        logger.info("Applying MySQL schema migration: add index %s.%s", table_name, index_name)
        self._execute(create_statement, ())

    def _ensure_unique_index(self, table_name: str, index_name: str, create_statement: str) -> None:
        self._ensure_index(table_name, index_name, create_statement)

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        row = self._fetchone(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (self._connect_kwargs["database"], table_name, column_name),
        )
        return row is not None

    def _index_exists(self, table_name: str, index_name: str) -> bool:
        row = self._fetchone(
            """
            SELECT 1
            FROM information_schema.statistics
            WHERE table_schema = %s
              AND table_name = %s
              AND index_name = %s
            LIMIT 1
            """,
            (self._connect_kwargs["database"], table_name, index_name),
        )
        return row is not None

    def _connect(self):
        connection = mysql.connect(**self._connect_kwargs)
        connection.autocommit = False
        return connection

    def _execute(self, query: str, params: tuple) -> int:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(query, params)
            connection.commit()
            return cursor.rowcount
        finally:
            cursor.close()
            connection.close()

    def _fetchone(self, query: str, params: tuple):
        rows = self._fetchall(query, params)
        return rows[0] if rows else None

    def _fetchall(self, query: str, params: tuple):
        connection = self._connect()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _to_user(row: dict) -> AppUser:
        return AppUser(
            user_id=int(row["user_id"]),
            telegram_user_id=int(row["telegram_user_id"]),
            chat_id=int(row["chat_id"]),
            username=row["username"] or "",
            first_name=row["first_name"] or "",
            status=UserStatus(row["status"]),
            is_admin=bool(row["is_admin"]),
            admin_mode_enabled=bool(row.get("admin_mode_enabled", 1)),
            created_at=row["created_at"],
        )

    @staticmethod
    def _to_invite(row: dict) -> InviteCode:
        return InviteCode(
            code=row["code"],
            created_by_user_id=int(row["created_by_user_id"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            max_uses=int(row["max_uses"]),
            used_count=int(row["used_count"]),
            status=InviteStatus(row["status"]),
        )

    @staticmethod
    def _to_user_digest_settings(row: dict) -> UserDigestSettings:
        return UserDigestSettings(
            user_id=int(row["user_id"]),
            timezone_name=row["timezone_name"],
            daily_digest_enabled=bool(row["daily_digest_enabled"]),
            daily_digest_time=row["daily_digest_time"],
            weekly_digest_enabled=bool(row["weekly_digest_enabled"]),
            weekly_digest_time=row["weekly_digest_time"],
            weekly_digest_weekday=int(row["weekly_digest_weekday"]),
        )

    @staticmethod
    def _to_user_google_drive_settings(row: dict) -> UserGoogleDriveSettings:
        return UserGoogleDriveSettings(
            user_id=int(row["user_id"]),
            folder_id=row["folder_id"],
            folder_url=row["folder_url"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_successful_import_at=row.get("last_successful_import_at"),
            last_stale_alert_sent_at=row.get("last_stale_alert_sent_at"),
        )

    @staticmethod
    def _to_digest_run(row: dict) -> DigestRun:
        return DigestRun(
            run_id=row["run_id"],
            user_id=int(row["user_id"]),
            digest_type=DigestType(row["digest_type"]),
            digest_date=row["digest_date"],
            status=DigestStatus(row["status"]),
            created_at=row["created_at"],
            scheduled_for=row["scheduled_for"],
            sent_at=row["sent_at"],
            error_message=row["error_message"] or "",
            payload=json.loads(row["payload_json"]) if row["payload_json"] else {},
        )

    @staticmethod
    def _to_health_import_file(row: dict) -> HealthImportFile:
        return HealthImportFile(
            import_id=row["import_id"],
            user_id=int(row["user_id"]),
            provider=HealthImportProvider(row["provider"]),
            external_file_id=row["external_file_id"],
            file_name=row["file_name"],
            file_date=row["file_date"],
            checksum=row["checksum"],
            status=HealthImportStatus(row["status"]),
            imported_at=row["imported_at"],
            activity_entries_count=int(row["activity_entries_count"]),
            sleep_entries_count=int(row["sleep_entries_count"]),
            weight_entries_count=int(row["weight_entries_count"]),
            raw_metadata_json=row["raw_metadata_json"] or "",
            error_message=row["error_message"] or "",
        )

    @staticmethod
    def _to_decision(row: dict) -> DecisionLogEntry:
        return DecisionLogEntry(
            decision_id=row["decision_id"],
            decision_key=row["decision_key"],
            created_at=row["created_at"],
            agent=row["agent"],
            kind=DecisionKind(row["kind"]),
            title=row["title"],
            rationale=row["rationale"],
            context_date=row["context_date"],
            status=DecisionStatus(row["status"]),
            payload=json.loads(row["payload"]),
        )

    @staticmethod
    def _to_activity_entry(row: dict) -> ActivityEntry:
        return ActivityEntry(
            entry_id=row["entry_id"],
            occurred_at=row["occurred_at"],
            title=row["title"],
            duration_minutes=int(row["duration_minutes"]),
            steps=int(row["steps"]),
            calories_burned=int(row["calories_burned"]),
            intensity=row["intensity"] or "moderate",
        )

    @staticmethod
    def _to_meal_draft(row: dict) -> MealPhotoDraft:
        items = [
            FoodItemEstimate(
                title=item["title"],
                portion_text=item["portion_text"],
                calories=item["calories"],
                protein_g=item["protein_g"],
                fat_g=item["fat_g"],
                carbs_g=item["carbs_g"],
                water_ml=int(item.get("water_ml", 0)),
            )
            for item in json.loads(row["items_json"])
        ]
        return MealPhotoDraft(
            draft_id=row["draft_id"],
            created_at=row["created_at"],
            occurred_at=row["occurred_at"],
            title=row["title"],
            summary=row["summary"],
            calories=row["calories"],
            protein_g=float(row["protein_g"]),
            fat_g=float(row["fat_g"]),
            carbs_g=float(row["carbs_g"]),
            water_ml=int(row.get("water_ml", 0)),
            confidence=float(row["confidence"]),
            photo_file_id=row["photo_file_id"],
            photo_unique_id=row["photo_unique_id"],
            status=MealDraftStatus(row["status"]),
            source=row["source"],
            items=items,
        )

    @staticmethod
    def _to_meal_media(row: dict) -> MealMedia:
        return MealMedia(
            media_id=row["media_id"],
            user_id=int(row["user_id"]),
            draft_id=row["draft_id"],
            occurred_at=row["occurred_at"],
            created_at=row["created_at"],
            mime_type=row["mime_type"],
            telegram_file_id=row["telegram_file_id"],
            telegram_unique_id=row["telegram_unique_id"],
            byte_size=int(row["byte_size"]),
            sha256=row["sha256"],
            image_bytes=row["image_bytes"] or b"",
            meal_entry_id=row["meal_entry_id"] or "",
            storage_kind=row["storage_kind"],
            storage_key=row.get("storage_key") or "",
            bucket_name=row.get("bucket_name") or "",
            width=int(row.get("width", 0) or 0),
            height=int(row.get("height", 0) or 0),
        )

    @staticmethod
    def _to_meal_entry(row: dict) -> MealEntry:
        return MealEntry(
            entry_id=row["entry_id"],
            occurred_at=row["occurred_at"],
            title=row["title"],
            calories=int(row["calories"]),
            protein_g=float(row["protein_g"]),
            fat_g=float(row["fat_g"]),
            carbs_g=float(row["carbs_g"]),
            water_ml=int(row.get("water_ml", 0)),
            notes=row["notes"],
        )
