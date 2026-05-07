import base64
import json
import unittest
from datetime import date, datetime

from ai_me.config import TelegramSettings
from ai_me.domain.digest import DailyFoodDigest, DigestMealSnapshot, WeeklyDigestHighlight, WeeklyFoodDigest
from ai_me.domain.finance import FinanceCategoryTotal, FinanceImportResult, FinanceMonthlySummary
from ai_me.domain.food import FoodItemEstimate, MealDraftStatus, MealMedia, MealPhotoDraft
from ai_me.domain.health import DailyHealthGoals, DailyHealthSummary, MealEntry
from ai_me.domain.health_import import HealthImportFile, HealthImportProvider, HealthImportStatus, UserGoogleDriveSettings
from ai_me.domain.user import AppUser, InviteCode, InviteStatus, UserStatus
from ai_me.services.food_analysis import OpenAIFoodPhotoAnalyzer
from ai_me.telegram import TelegramHealthBot


VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2WZgAAAABJRU5ErkJggg=="
)


class DummyHealthService:
    def __init__(self) -> None:
        self.confirmed_draft_ids = []
        self.last_import = None
        self.created_invites = []
        self.drive_settings_by_user_id = {}
        self.health_import_files_by_user_id = {}
        self.users_by_telegram_id = {
            42: AppUser(
                user_id=1,
                telegram_user_id=42,
                chat_id=777,
                username="owner",
                first_name="Owner",
                status=UserStatus.ACTIVE,
                is_admin=True,
                created_at=datetime(2026, 5, 6, 9, 0),
            ),
            77: AppUser(
                user_id=2,
                telegram_user_id=77,
                chat_id=778,
                username="guest",
                first_name="Guest",
                status=UserStatus.ACTIVE,
                is_admin=False,
                created_at=datetime(2026, 5, 6, 9, 30),
            ),
        }

    def sync_user(self, telegram_user_id: int, chat_id: int, username: str = "", first_name: str = ""):
        user = self.users_by_telegram_id.get(telegram_user_id)
        if user is None:
            return None
        return AppUser(
            user_id=user.user_id,
            telegram_user_id=user.telegram_user_id,
            chat_id=chat_id,
            username=username or user.username,
            first_name=first_name or user.first_name,
            status=user.status,
            is_admin=user.is_admin,
            created_at=user.created_at,
        )

    def register_user_with_invite(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: str,
        first_name: str,
        invite_code: str,
        now=None,
    ):
        if invite_code != "invite-1":
            raise ValueError("Инвайт не найден или недействителен.")
        user = AppUser(
            user_id=3,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            status=UserStatus.ACTIVE,
            is_admin=False,
            created_at=now,
        )
        self.users_by_telegram_id[telegram_user_id] = user
        return user

    def create_invite(self, created_by_user_id: int, days_valid: int = 7, max_uses: int = 1, now=None):
        invite = InviteCode(
            code="NEWCODE123",
            created_by_user_id=created_by_user_id,
            created_at=now or datetime(2026, 5, 6, 10, 0),
            expires_at=datetime(2026, 5, 13, 10, 0),
            max_uses=max_uses,
            used_count=0,
            status=InviteStatus.ACTIVE,
        )
        self.created_invites.append(invite)
        return invite

    def list_invites(self, status=None):
        return [
            InviteCode(
                code="ABC123",
                created_by_user_id=1,
                created_at=datetime(2026, 5, 6, 10, 0),
                expires_at=datetime(2026, 5, 13, 10, 0),
                max_uses=1,
                used_count=0,
                status=InviteStatus.ACTIVE,
            )
        ]

    def revoke_invite(self, code: str) -> None:
        return None

    def get_digest_settings(self, user_id):
        return type(
            "DigestSettings",
            (),
            {
                "timezone_name": "Europe/Moscow",
                "daily_digest_enabled": True,
                "daily_digest_time": "08:00",
                "weekly_digest_enabled": True,
                "weekly_digest_time": "08:00",
            },
        )()

    def set_digest_enabled(self, user_id, enabled: bool):
        return type(
            "DigestSettings",
            (),
            {
                "timezone_name": "Europe/Moscow",
                "daily_digest_enabled": enabled,
                "daily_digest_time": "08:00",
                "weekly_digest_enabled": enabled,
                "weekly_digest_time": "08:00",
            },
        )()

    def google_drive_is_configured(self):
        return True

    def connect_google_drive_folder(self, user_id, folder_input: str, now=None):
        settings = UserGoogleDriveSettings(
            user_id=user_id,
            folder_id="folder-123",
            folder_url=folder_input,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        self.drive_settings_by_user_id[user_id] = settings
        return settings

    def get_google_drive_settings(self, user_id):
        return self.drive_settings_by_user_id.get(user_id)

    def set_google_drive_enabled(self, user_id, enabled: bool, now=None):
        current = self.drive_settings_by_user_id[user_id]
        settings = UserGoogleDriveSettings(
            user_id=current.user_id,
            folder_id=current.folder_id,
            folder_url=current.folder_url,
            enabled=enabled,
            created_at=current.created_at,
            updated_at=now,
        )
        self.drive_settings_by_user_id[user_id] = settings
        return settings

    def list_health_import_files(self, user_id, provider=None):
        return self.health_import_files_by_user_id.get(user_id, [])

    def build_daily_food_digest(self, user_id, digest_date):
        media = MealMedia(
            media_id="media-1",
            user_id=user_id,
            draft_id="draft-1",
            occurred_at=datetime(2026, 5, 6, 12, 0),
            created_at=datetime(2026, 5, 6, 12, 0),
            mime_type="image/jpeg",
            telegram_file_id="file-1",
            telegram_unique_id="u-1",
            byte_size=1234,
            sha256="abc",
            image_bytes=VALID_PNG_BYTES,
            meal_entry_id="meal-1",
        )
        return DailyFoodDigest(
            user_id=user_id,
            digest_date=digest_date,
            meals=[
                DigestMealSnapshot(
                    meal_entry_id="meal-1",
                    occurred_at=datetime(2026, 5, 6, 12, 0),
                    title="Курица с рисом",
                    calories=620,
                    protein_g=38,
                    fat_g=18,
                    carbs_g=71,
                    media_items=[media],
                )
            ],
            total_calories=620,
            total_protein_g=38.0,
            total_fat_g=18.0,
            total_carbs_g=71.0,
            commentary="Относительно 7 дней калорийность выше среднего.",
        )

    def build_weekly_food_digest(self, user_id, week_start):
        media = MealMedia(
            media_id="media-1",
            user_id=user_id,
            draft_id="draft-1",
            occurred_at=datetime(2026, 5, 5, 12, 0),
            created_at=datetime(2026, 5, 5, 12, 0),
            mime_type="image/jpeg",
            telegram_file_id="file-1",
            telegram_unique_id="u-1",
            byte_size=1234,
            sha256="abc",
            image_bytes=VALID_PNG_BYTES,
            meal_entry_id="meal-1",
        )
        return WeeklyFoodDigest(
            user_id=user_id,
            week_start=week_start,
            week_end=week_start + __import__("datetime").timedelta(days=6),
            highlights=[
                WeeklyDigestHighlight(
                    digest_date=week_start,
                    meal=DigestMealSnapshot(
                        meal_entry_id="meal-1",
                        occurred_at=datetime(2026, 5, 5, 12, 0),
                        title="Курица с рисом",
                        calories=620,
                        protein_g=38,
                        fat_g=18,
                        carbs_g=71,
                        media_items=[media],
                    ),
                    score=1.2,
                    reason="Выбрано как блюдо с наибольшим отклонением от личной базы по калориям.",
                )
            ],
            total_meals=5,
            total_calories=3100,
            commentary="Самое выделяющееся блюдо недели: Курица с рисом.",
        )

    def log_water(self, user_id, entry) -> None:
        return None

    def log_meal(self, user_id, entry) -> None:
        return None

    def log_weight(self, user_id, entry) -> None:
        return None

    def log_sleep(self, user_id, entry) -> None:
        return None

    def log_activity(self, user_id, entry) -> None:
        return None

    def set_goals(self, user_id, goals) -> None:
        return None

    def evaluate_day(self, user_id, target_date, now=None):
        return []

    def get_daily_summary(self, user_id, target_date):
        return DailyHealthSummary(
            target_date=target_date,
            meals_count=1,
            calories=620,
            protein_g=38,
            fat_g=18,
            carbs_g=71,
            water_ml=0,
            sleep_hours=0,
            steps=0,
            activity_minutes=0,
            latest_weight_kg=None,
            goals=DailyHealthGoals(target_date=target_date),
        )

    def list_meals(self, user_id, target_date):
        return [
            MealEntry(
                entry_id="meal-1",
                occurred_at=datetime(2026, 5, 6, 12, 0),
                title="Курица с рисом",
                calories=620,
                protein_g=38,
                fat_g=18,
                carbs_g=71,
            )
        ]

    def list_decisions(self, user_id, status=None, target_date=None):
        return []

    def list_meal_drafts(self, user_id, status=MealDraftStatus.PENDING):
        return [
            MealPhotoDraft(
                draft_id="draft-1",
                created_at=datetime(2026, 5, 6, 12, 0),
                occurred_at=datetime(2026, 5, 6, 12, 0),
                title="Chicken rice bowl",
                summary="Rice bowl",
                calories=620,
                protein_g=38,
                fat_g=18,
                carbs_g=71,
                confidence=0.84,
                photo_file_id="file-1",
                photo_unique_id="u-1",
                items=[
                    FoodItemEstimate(
                        title="Курица",
                        portion_text="150 г",
                        calories=250,
                        protein_g=31,
                        fat_g=8,
                        carbs_g=0,
                    )
                ],
            )
        ]

    def create_meal_draft_from_photo(self, user_id, **kwargs):
        return self.list_meal_drafts(user_id)[0]

    def confirm_meal_draft(self, user_id, draft_id):
        self.confirmed_draft_ids.append((user_id, draft_id))
        return type(
            "Meal",
            (),
            {
                "title": "Chicken rice bowl",
                "occurred_at": datetime(2026, 5, 6, 12, 0),
            },
        )()

    def reject_meal_draft(self, user_id, draft_id):
        return type("Draft", (), {"title": "Chicken rice bowl"})()

    def import_tbank_csv(self, user_id, file_bytes: bytes, source_file_name: str):
        self.last_import = (user_id, file_bytes, source_file_name)
        return FinanceImportResult(
            provider="tbank",
            source_file_name=source_file_name,
            total_rows=2,
            imported_rows=2,
            skipped_rows=0,
            first_operation_at=datetime(2026, 5, 1, 9, 0),
            last_operation_at=datetime(2026, 5, 2, 18, 30),
        )

    def get_finance_monthly_summary(self, user_id, month_start: date):
        return FinanceMonthlySummary(
            month_start=month_start,
            month_end=date(2026, 6, 1),
            transaction_count=4,
            income_total=25000.0,
            expense_total=3000.5,
            net_total=21999.5,
            top_expense_categories=[
                FinanceCategoryTotal(category="Продукты", amount=2300.5, transaction_count=2),
                FinanceCategoryTotal(category="Такси", amount=700.0, transaction_count=1),
            ],
        )


class TelegramHealthBotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DummyHealthService()
        self.bot = TelegramHealthBot(
            service=self.service,
            settings=TelegramSettings(
                bot_token="123:abc",
                allowed_user_ids=frozenset(),
                admin_user_ids=frozenset({42}),
                owner_telegram_user_id=42,
                timezone_name="Europe/Moscow",
                environment_name="staging",
                registration_mode="invite_only",
            ),
        )

    def test_whoami_command_exposes_user_context(self) -> None:
        response = self.bot._route_command("/whoami", chat_id=777, user_id=42, app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Данные Telegram", response)
        self.assertIn("окружение=staging", response)
        self.assertIn("режим_доступа=invite_only", response)
        self.assertIn("app_user_id=1", response)
        self.assertIn("роль=admin", response)

    def test_help_for_unregistered_user_explains_invite_flow(self) -> None:
        response = self.bot._route_command("/help")
        self.assertIn("Версия: 0.1", response)
        self.assertIn("Дата релиза: 2026-05-07", response)
        self.assertIn("Доступ: только по инвайту", response)
        self.assertIn("/start <invite_code>", response)

    def test_help_for_registered_user_does_not_list_removed_manual_health_commands(self) -> None:
        response = self.bot._route_command("/help", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Версия: 0.1", response)
        self.assertIn("Дата релиза: 2026-05-07", response)
        self.assertIn("/connect_drive <folder_url>", response)
        self.assertIn("/drive_status", response)
        self.assertNotIn("/water", response)
        self.assertNotIn("/meal <calories>", response)
        self.assertNotIn("/weight", response)
        self.assertNotIn("/sleep", response)
        self.assertNotIn("/activity", response)
        self.assertNotIn("/goals", response)

    def test_start_with_invite_registers_new_user(self) -> None:
        response = self.bot._route_command(
            "/start invite-1",
            chat_id=999,
            user_id=999,
            username="newuser",
            first_name="New",
            app_user=None,
        )
        self.assertIn("Подключение завершено", response)
        self.assertIn("/summary", response)
        self.assertIn(999, self.service.users_by_telegram_id)

    def test_digest_status_command_returns_current_settings(self) -> None:
        response = self.bot._route_command("/digest_status", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Настройки digest", response)
        self.assertIn("Ежедневная сводка: включена в 08:00", response)
        self.assertIn("Недельная сводка: включена по понедельникам в 08:00", response)

    def test_connect_drive_command_saves_folder_for_user(self) -> None:
        response = self.bot._route_command(
            "/connect_drive https://drive.google.com/drive/folders/folder-123",
            app_user=self.service.users_by_telegram_id[42],
        )
        self.assertIn("Папка Google Drive подключена", response)
        self.assertEqual(
            self.service.drive_settings_by_user_id[1].folder_url,
            "https://drive.google.com/drive/folders/folder-123",
        )

    def test_drive_status_reports_missing_folder_before_connect(self) -> None:
        response = self.bot._route_command("/drive_status", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Google Drive не подключен", response)
        self.assertIn("/connect_drive <folder_url>", response)

    def test_drive_off_command_disables_import(self) -> None:
        self.service.drive_settings_by_user_id[1] = UserGoogleDriveSettings(
            user_id=1,
            folder_id="folder-123",
            folder_url="https://drive.google.com/drive/folders/folder-123",
            enabled=True,
            created_at=datetime(2026, 5, 7, 8, 0),
            updated_at=datetime(2026, 5, 7, 8, 0),
        )
        response = self.bot._route_command("/drive_off", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Импорт Google Drive выключен", response)
        self.assertFalse(self.service.drive_settings_by_user_id[1].enabled)

    def test_digest_off_command_disables_both_digests(self) -> None:
        response = self.bot._route_command("/digest_off", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Digest выключен", response)
        self.assertIn("Ежедневная сводка: выключена", response)
        self.assertIn("Недельная сводка: выключена", response)

    def test_digest_preview_command_returns_daily_preview(self) -> None:
        response = self.bot._route_command("/digest_preview 2026-05-06", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Daily digest preview за 2026-05-06", response)
        self.assertIn("Список блюд:", response)
        self.assertIn("Курица с рисом", response)

    def test_weekly_digest_preview_command_returns_weekly_preview(self) -> None:
        response = self.bot._route_command(
            "/weekly_digest_preview 2026-05-06",
            app_user=self.service.users_by_telegram_id[42],
        )
        self.assertIn("Weekly digest preview", response)
        self.assertIn("Выделяющиеся блюда по дням:", response)
        self.assertIn("Курица с рисом", response)

    def test_digest_preview_update_sends_mosaic_photo_and_text(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        def fake_telegram_api_multipart(method, **kwargs):
            calls.append((method, kwargs))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._telegram_api_multipart = fake_telegram_api_multipart

        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/digest_preview 2026-05-06",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        self.assertEqual(calls[0][0], "sendPhoto")
        self.assertEqual(calls[1][0], "sendMessage")
        self.assertIn("Daily digest preview за 2026-05-06", calls[1][1]["text"])

    def test_weekly_digest_preview_update_sends_mosaic_photo_and_text(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        def fake_telegram_api_multipart(method, **kwargs):
            calls.append((method, kwargs))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._telegram_api_multipart = fake_telegram_api_multipart

        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/weekly_digest_preview 2026-05-06",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        self.assertEqual(calls[0][0], "sendPhoto")
        self.assertEqual(calls[1][0], "sendMessage")
        self.assertIn("Weekly digest preview", calls[1][1]["text"])

    def test_non_private_chat_is_rejected(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/help",
                    "chat": {"id": -100, "type": "group"},
                    "from": {"id": 42},
                },
            }
        )

        self.assertEqual(messages[0][0], "sendMessage")
        self.assertIn("только в личных сообщениях", messages[0][1]["text"])

    def test_help_message_attaches_reply_keyboard_only_for_registered_user(self) -> None:
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "text": "/help",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                },
            }
        )

        self.assertEqual(messages[0][0], "sendMessage")
        markup = json.loads(messages[0][1]["reply_markup"])
        self.assertEqual(markup["keyboard"][0][0]["text"], "Сводка за сегодня")
        self.assertEqual(markup["keyboard"][1][0]["text"], "Google Drive")
        self.assertEqual(markup["keyboard"][1][1]["text"], "Импорт Т-Банк")

    def test_sync_bot_commands_registers_menu_entries(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._sync_bot_commands()

        self.assertEqual(calls[0][0], "setMyCommands")
        commands = json.loads(calls[0][1]["commands"])
        self.assertEqual(commands[0]["command"], "start")
        self.assertEqual(commands[1]["command"], "menu")
        self.assertEqual(commands[4]["command"], "connect_drive")
        self.assertEqual(commands[5]["command"], "drive_status")
        self.assertEqual(commands[8]["command"], "decisions")
        self.assertEqual(commands[9]["command"], "digest_status")
        self.assertEqual(commands[10]["command"], "digest_on")
        self.assertEqual(commands[11]["command"], "digest_off")
        self.assertEqual(commands[12]["command"], "digest_preview")
        self.assertEqual(commands[13]["command"], "weekly_digest_preview")
        self.assertEqual(commands[16]["command"], "create_invite")

    def test_document_imports_tbank_csv_in_user_scope(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            if method == "getFile":
                return {"file_path": "docs/tbank.csv"}
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._download_telegram_file = lambda path: (
            b"\xef\xbb\xbf\xd0\x94\xd0\xb0\xd1\x82\xd0\xb0 \xd0\xbe\xd0\xbf\xd0\xb5\xd1\x80\xd0\xb0\xd1\x86\xd0\xb8\xd0\xb8;"
            b"\xd0\xa1\xd1\x83\xd0\xbc\xd0\xbc\xd0\xb0 \xd0\xbf\xd0\xbb\xd0\xb0\xd1\x82\xd0\xb5\xd0\xb6\xd0\xb0;"
            b"\xd0\x9e\xd0\xbf\xd0\xb8\xd1\x81\xd0\xb0\xd0\xbd\xd0\xb8\xd0\xb5\n"
            b"01.05.2026;-1500;\xd0\x9f\xd1\x80\xd0\xbe\xd0\xb4\xd1\x83\xd0\xba\xd1\x82\xd1\x8b\n"
        )

        self.bot._handle_update(
            {
                "update_id": 1,
                "message": {
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                    "document": {
                        "file_id": "file-1",
                        "file_name": "tbank.csv",
                        "mime_type": "text/csv",
                    },
                },
            }
        )

        self.assertEqual(self.service.last_import[0], 1)
        self.assertEqual(self.service.last_import[2], "tbank.csv")
        self.assertEqual(calls[0][0], "getFile")
        self.assertEqual(calls[1][0], "sendMessage")
        self.assertIn("Импорт операций Т-Банка завершен", calls[1][1]["text"])

    def test_drafts_command_lists_pending_drafts(self) -> None:
        response = self.bot._route_command("/drafts", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Ожидающие черновики приема пищи", response)
        self.assertIn("draft-1", response)

    def test_confirm_meal_command_confirms_draft_in_user_scope(self) -> None:
        response = self.bot._route_command("/confirm_meal draft-1", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Прием пищи сохранен", response)
        self.assertEqual(self.service.confirmed_draft_ids, [(1, "draft-1")])

    def test_confirm_callback_edits_message_and_sends_confirmation(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_confirm:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(self.service.confirmed_draft_ids, [(1, "draft-1")])
        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertEqual(calls[2][0], "sendMessage")

    def test_confirm_callback_continues_when_callback_answer_fails(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            if method == "answerCallbackQuery":
                raise RuntimeError("telegram 400")
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_confirm:draft-1",
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 777, "type": "private"},
                },
            }
        )

        self.assertEqual(self.service.confirmed_draft_ids, [(1, "draft-1")])
        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertEqual(calls[2][0], "sendMessage")

    def test_unregistered_callback_gets_invite_prompt(self) -> None:
        calls = []

        def fake_telegram_api(method, params):
            calls.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._handle_callback_query(
            {
                "id": "query-1",
                "data": "meal_confirm:draft-1",
                "from": {"id": 999, "username": "new", "first_name": "New"},
                "message": {
                    "message_id": 555,
                    "chat": {"id": 999, "type": "private"},
                },
            }
        )

        self.assertEqual(calls[0][0], "answerCallbackQuery")
        self.assertIn("Сначала подключите бота", calls[0][1]["text"])

    def test_meal_draft_message_uses_russian_labels(self) -> None:
        draft = self.service.list_meal_drafts(1)[0]
        messages = []

        def fake_telegram_api(method, params):
            messages.append((method, params))
            return True

        self.bot._telegram_api = fake_telegram_api
        self.bot._send_meal_draft(777, draft)

        self.assertEqual(messages[0][0], "sendMessage")
        text = messages[0][1]["text"]
        self.assertIn("Черновик приема пищи", text)
        self.assertIn("Состав:", text)
        self.assertIn("Ингредиенты:", text)

    def test_food_analysis_parser_handles_markdown_wrapped_json(self) -> None:
        parsed = OpenAIFoodPhotoAnalyzer._parse_json_text(
            """```json
            {
              "title": "Chicken rice bowl",
              "summary": "Rice bowl",
              "calories": 620,
              "protein_g": 38,
              "fat_g": 18,
              "carbs_g": 71,
              "confidence": 0.84,
              "items": []
            }
            ```"""
        )
        self.assertEqual(parsed["title"], "Chicken rice bowl")

    def test_summary_includes_food_breakdown(self) -> None:
        response = self.bot._route_command("/summary 2026-05-06", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Сводка за 2026-05-06", response)
        self.assertIn("Приемы пищи: 1", response)
        self.assertIn("Еда:", response)
        self.assertIn("12:00 | Курица с рисом", response)
        self.assertIn("Б 38.0 / Ж 18.0 / У 71.0", response)

    def test_admin_can_create_invite(self) -> None:
        response = self.bot._route_command("/create_invite 10 2", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Инвайт создан", response)
        self.assertIn("NEWCODE123", response)

    def test_non_admin_cannot_create_invite(self) -> None:
        response = self.bot._route_command("/create_invite", app_user=self.service.users_by_telegram_id[77])
        self.assertIn("Команда доступна только администратору", response)

    def test_removed_manual_health_command_is_unknown(self) -> None:
        response = self.bot._route_command("/water 500", app_user=self.service.users_by_telegram_id[42])
        self.assertIn("Неизвестная команда", response)


if __name__ == "__main__":
    unittest.main()
