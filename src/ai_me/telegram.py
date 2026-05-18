import json
import logging
import math
import shlex
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib import error as urlerror, parse, request
from uuid import uuid4
from zoneinfo import ZoneInfo

from ai_me.config import TelegramSettings
from ai_me.domain.decision_log import DecisionStatus
from ai_me.domain.digest import DailyFoodDigest, WeeklyFoodDigest
from ai_me.domain.food import MealDraftStatus, MealPhotoDraft, PhotoLogKind
from ai_me.domain.health import DailyHealthSummary, MealEntry, PostSaveCoachingSnapshot, WaterEntry
from ai_me.domain.user import AppUser, UserGoal, UserSex, UserStatus
from ai_me.services.digest_renderer import DigestImageRenderer
from ai_me.services.health_service import HealthService
from ai_me.version import format_release_date_line, format_version_line


logger = logging.getLogger(__name__)


@dataclass
class ResponseDebugTrace:
    label: str
    started_at: float
    steps: List[tuple[str, float]] = field(default_factory=list)

    def add_step(self, label: str, seconds: float) -> None:
        self.steps.append((label, seconds))


class TelegramHealthBot:
    HISTORY_PAGE_SIZE = 10
    HISTORY_EDIT_WINDOW = timedelta(minutes=15)
    HISTORY_DELETE_WINDOW = timedelta(minutes=3)
    ONBOARDING_STEP_COUNT = 3
    ONBOARDING_ASSET_DIR = Path(__file__).resolve().parent / "assets"
    BUTTON_TO_COMMAND = {
        "Добавить еду": "/add_food",
        "Добавить воду": "/add_water",
        "История": "/history",
        "Приемы пищи": "/history_app",
        "Распознавания": "/history_app",
        "Исправить последнюю запись": "/history_fix_last",
        "Отменить последнюю запись": "/history_delete_last",
        "История и правки в приложении": "/history_app",
        "Прогресс": "/progress",
        "Профиль": "/profile",
        "Цели": "/profile_goals",
        "Напоминания": "/profile_reminders",
        "Обо мне": "/profile_about",
        "Как это работает": "/how_it_works",
        "Назад": "/menu",
        "Добавить еще": "/add_water",
        "+250 мл": "/water 250",
        "+500 мл": "/water 500",
        "+750 мл": "/water 750",
        "Свой объем": "/water_custom",
        "Сводка за сегодня": "/summary",
        "Финансы за месяц": "/finance_month",
        "Google Drive": "/drive_status",
        "Открытые решения": "/decisions",
        "Импорт Т-Банк": "/import_tbank",
        "Черновики еды": "/drafts",
        "Кто я": "/whoami",
        "Помощь": "/help",
    }

    def __init__(
        self,
        service: HealthService,
        settings: TelegramSettings,
        digest_renderer: Optional[DigestImageRenderer] = None,
    ) -> None:
        self.service = service
        self.settings = settings
        self.base_url = "https://api.telegram.org/bot%s/" % settings.bot_token
        self.timezone = ZoneInfo(settings.timezone_name)
        self.digest_renderer = digest_renderer or DigestImageRenderer()
        self._photo_processing_user_ids = set()
        self._photo_rate_limit_until_by_user: Dict[int, datetime] = {}
        self._pending_custom_water_user_ids = set()
        self._pending_draft_edit_states: Dict[int, Dict[str, str]] = {}
        self._pending_draft_clarifications: Dict[int, Dict[str, object]] = {}
        self._pending_last_meal_delete_by_user: Dict[int, str] = {}
        self._pending_profile_edit_states: Dict[int, Dict[str, str]] = {}
        self._active_response_debug: Optional[ResponseDebugTrace] = None

    def run_forever(self) -> None:
        self._ensure_polling_mode()
        self._sync_bot_commands()
        self._sync_mini_app_menu_button()
        logger.info("Telegram long polling started environment=%s", self.settings.environment_name)
        offset = None
        while True:
            try:
                updates = self._get_updates(offset=offset)
                if updates:
                    logger.info("Received %s Telegram update(s)", len(updates))
                for update in updates:
                    offset = update["update_id"] + 1
                    self._handle_update(update)
            except Exception as exc:  # pragma: no cover
                logger.exception("Polling error: %s", exc)
                time.sleep(3)

    def _handle_update(self, update: Dict[str, object]) -> None:
        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            logger.info("Processing callback query update_id=%s", update.get("update_id"))
            self._handle_callback_query(callback_query)
            return

        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return

        chat = message.get("chat", {})
        user = message.get("from", {})
        chat_id = chat.get("id")
        user_id = user.get("id")
        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            logger.warning("Skipping update without valid chat_id/user_id: %s", update)
            return
        if not self._is_private_chat(chat):
            logger.warning("Rejected non-private chat update chat_id=%s chat_type=%s", chat_id, chat.get("type"))
            self._send_message(chat_id, self._private_chat_only_text())
            return

        username = self._telegram_string(user.get("username"))
        first_name = self._telegram_string(user.get("first_name"))
        app_user = self.service.sync_user(
            telegram_user_id=user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
        )
        if app_user is not None and app_user.status == UserStatus.BLOCKED:
            self._send_message(chat_id, "Ваш доступ к боту заблокирован.")
            return
        if app_user is not None:
            self._sync_mini_app_menu_button(chat_id=chat_id, app_user=app_user)

        text = message.get("text")
        caption = message.get("caption")
        photo = message.get("photo")
        document = message.get("document")

        debug_label = "message"
        if isinstance(text, str):
            debug_label = "text %s" % self._normalize_command_text(text.strip())
        elif isinstance(photo, list) and photo:
            debug_label = "photo"
        elif isinstance(document, dict):
            debug_label = "document"
        previous_debug = self._begin_response_debug(debug_label)
        try:
            if isinstance(photo, list) and photo:
                logger.info("Received photo message chat_id=%s user_id=%s", chat_id, user_id)
                if app_user is None:
                    self._send_message(chat_id, self._registration_required_text())
                    return
                current_time = self._local_now()
                rate_limit_error = self._try_begin_photo_processing(app_user, now=current_time)
                if rate_limit_error is not None:
                    self._send_message(chat_id, rate_limit_error)
                    return
                photo_started_at = time.perf_counter()
                try:
                    self._handle_photo_message(
                        chat_id=chat_id,
                        app_user=app_user,
                        photo=photo,
                        caption=caption if isinstance(caption, str) else "",
                    )
                finally:
                    self._finish_photo_processing(app_user, now=self._local_now())
                    self._record_response_debug_step("обработка фото", photo_started_at)
                return

            if isinstance(document, dict):
                logger.info("Received document message chat_id=%s user_id=%s", chat_id, user_id)
                if app_user is None:
                    self._send_message(chat_id, self._registration_required_text())
                    return
                document_started_at = time.perf_counter()
                self._handle_document_message(chat_id=chat_id, app_user=app_user, document=document)
                self._record_response_debug_step("обработка документа", document_started_at)
                return

            if isinstance(text, str):
                logger.info("Received text command chat_id=%s user_id=%s text=%s", chat_id, user_id, text.strip())
                raw_text = text.strip()
                normalized_text = self._normalize_command_text(raw_text)
                pending_started_at = time.perf_counter()
                pending_draft_response = self._handle_pending_draft_edit_input(
                    app_user=app_user,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                )
                self._record_response_debug_step("проверка pending draft", pending_started_at)
                if pending_draft_response is not None:
                    pending_text, pending_markup = pending_draft_response
                    self._send_message(chat_id, pending_text, reply_markup=pending_markup)
                    return
                pending_started_at = time.perf_counter()
                pending_response = self._handle_pending_custom_water_input(
                    app_user=app_user,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                )
                self._record_response_debug_step("проверка pending water", pending_started_at)
                if pending_response is not None:
                    pending_text, pending_markup = pending_response
                    self._send_message(chat_id, pending_text, reply_markup=pending_markup)
                    return
                pending_started_at = time.perf_counter()
                pending_delete_response = self._handle_pending_last_meal_delete_input(
                    app_user=app_user,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                )
                self._record_response_debug_step("проверка pending delete", pending_started_at)
                if pending_delete_response is not None:
                    pending_text, pending_markup = pending_delete_response
                    self._send_message(chat_id, pending_text, reply_markup=pending_markup)
                    return
                pending_started_at = time.perf_counter()
                pending_profile_response = self._handle_pending_profile_input(
                    app_user=app_user,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                )
                self._record_response_debug_step("проверка pending profile", pending_started_at)
                if pending_profile_response is not None:
                    pending_text, pending_markup = pending_profile_response
                    self._send_message(chat_id, pending_text, reply_markup=pending_markup)
                    return
                route_started_at = time.perf_counter()
                response = self._route_command(
                    text=normalized_text,
                    chat_id=chat_id,
                    user_id=user_id,
                    username=username,
                    first_name=first_name,
                    app_user=app_user,
                )
                self._record_response_debug_step("маршрутизация команды", route_started_at)
                reply_user_started_at = time.perf_counter()
                reply_user = app_user
                if self._should_reload_reply_user(normalized_text):
                    reply_user = self.service.get_user_by_telegram_user_id(user_id) or app_user
                self._record_response_debug_step("загрузка reply user", reply_user_started_at)
                if normalized_text == "/start" and reply_user is not None:
                    self._send_onboarding_step(chat_id, step=1)
                    return
                if response:
                    markup_started_at = time.perf_counter()
                    reply_markup = self._reply_markup_for_response(
                        text=normalized_text,
                        original_app_user=app_user,
                        reply_user=reply_user,
                    )
                    self._record_response_debug_step("сборка reply markup", markup_started_at)
                    parse_mode = "Markdown" if normalized_text.startswith("/digest_preview") else None
                    try:
                        self._send_message(
                            chat_id,
                            response,
                            reply_markup=reply_markup,
                            parse_mode=parse_mode,
                        )
                    except Exception as exc:
                        if reply_markup is None:
                            raise
                        logger.warning(
                            "sendMessage with reply_markup failed chat_id=%s text=%s error=%s; retrying without markup",
                            chat_id,
                            normalized_text,
                            exc,
                        )
                        self._send_message(
                            chat_id,
                            response,
                            parse_mode=parse_mode,
                        )
        finally:
            self._finish_response_debug(previous_debug)
    
    def _handle_callback_query(self, callback_query: Dict[str, object]) -> None:
        user = callback_query.get("from", {})
        user_id = user.get("id")
        data = callback_query.get("data")
        query_id = callback_query.get("id")
        message = callback_query.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        if not isinstance(data, str) or not isinstance(query_id, str) or not isinstance(chat_id, int) or not isinstance(user_id, int):
            logger.warning("Skipping malformed callback query: %s", callback_query)
            return
        if not self._is_private_chat(chat):
            self._answer_callback_query(query_id, "Бот работает только в личных сообщениях.")
            return
        username = self._telegram_string(user.get("username"))
        first_name = self._telegram_string(user.get("first_name"))
        app_user = self.service.sync_user(
            telegram_user_id=user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
        )
        logger.info(
            "Received callback query_id=%s user_id=%s chat_id=%s message_id=%s data=%s",
            query_id,
            user_id,
            chat_id,
            message_id,
            data,
        )
        if app_user is None:
            logger.warning("Rejected callback from unregistered user_id=%s", user_id)
            self._answer_callback_query(query_id, "Сначала отправьте /start, чтобы подключить бота.")
            return
        if app_user.status == UserStatus.BLOCKED:
            self._answer_callback_query(query_id, "Ваш доступ к боту заблокирован.")
            return

        if data.startswith("onboarding:"):
            self._handle_onboarding_callback(
                query_id=query_id,
                chat_id=chat_id,
                message_id=message_id,
                app_user=app_user,
                data=data,
            )
            return

        if data.startswith("meal_confirm:"):
            draft_id = data.split(":", 1)[1]
            logger.info("Confirming meal draft_id=%s user_id=%s", draft_id, app_user.user_id)
            self._try_answer_callback_query(query_id, "Сохраняю прием пищи...")
            try:
                meal = self.service.confirm_meal_draft(app_user.user_id, draft_id)
            except ValueError as exc:
                logger.warning("Meal confirmation failed draft_id=%s error=%s", draft_id, exc)
                self._send_message(chat_id, "Не удалось сохранить прием пищи: %s" % exc)
                return
            if isinstance(message_id, int):
                edit_text = (
                    "Вода сохранена\n"
                    "Напиток: %s\n"
                    "Статус: подтверждено"
                    % meal.title
                    if meal.kind == PhotoLogKind.WATER
                    else (
                        "Прием пищи сохранен\n"
                        "Блюдо: %s\n"
                        "Статус: подтверждено"
                        % meal.title
                    )
                )
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=edit_text,
                )
            logger.info("Meal confirmed draft_id=%s title=%s", draft_id, meal.title)
            if meal.kind == PhotoLogKind.WATER:
                self._send_message(
                    chat_id,
                    "Вода сохранена: %s л." % self._format_liters(meal.water_ml),
                )
            else:
                self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
                self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
                saved_meal = meal.meal_entry or self.service.get_meal_entry(app_user.user_id, meal.entry_id)
                self._send_message(
                    chat_id,
                    self._format_saved_meal_text(app_user, saved_meal),
                    reply_markup=self._saved_meal_reply_markup(saved_meal.entry_id),
                )
            return

        if data.startswith("meal_edit_menu:"):
            draft_id = data.split(":", 1)[1]
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
            self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
            draft = self.service.get_meal_draft(app_user.user_id, draft_id)
            self._try_answer_callback_query(query_id, "Можно исправить черновик.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_draft_edit_menu_text(draft),
                    reply_markup=self._meal_draft_edit_menu_reply_markup(draft),
                )
            return

        if data.startswith("meal_edit_back:"):
            draft_id = data.split(":", 1)[1]
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
            self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
            draft = self.service.get_meal_draft(app_user.user_id, draft_id)
            self._try_answer_callback_query(query_id, "Возвращаю черновик.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_draft_card_text(draft),
                    reply_markup=self._meal_draft_card_reply_markup(draft),
                )
            return

        if data.startswith("meal_edit_title:"):
            draft_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, draft_id, "title")
            self._try_answer_callback_query(query_id, "Введите новое название.")
            self._send_message(
                chat_id,
                "Введите новое название блюда.",
                reply_markup=self._draft_edit_prompt_reply_markup(draft_id),
            )
            return

        if data.startswith("meal_edit_summary:"):
            draft_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, draft_id, "summary")
            self._try_answer_callback_query(query_id, "Введите новый состав.")
            self._send_message(
                chat_id,
                "Введите краткое описание или состав блюда.",
                reply_markup=self._draft_edit_prompt_reply_markup(draft_id),
            )
            return

        if data.startswith("meal_edit_time:"):
            draft_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, draft_id, "time")
            self._try_answer_callback_query(query_id, "Введите новое время.")
            self._send_message(
                chat_id,
                "Введите время в формате HH:MM или выберите «Сейчас».",
                reply_markup=self._draft_time_prompt_reply_markup(draft_id),
            )
            return

        if data.startswith("meal_edit_time_now:"):
            draft_id = data.split(":", 1)[1]
            draft = self.service.update_meal_draft(
                app_user.user_id,
                draft_id,
                occurred_at=self._local_now(),
            )
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
            self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
            self._try_answer_callback_query(query_id, "Время обновлено.")
            self._send_message(
                chat_id,
                self._format_meal_draft_card_text(draft),
                reply_markup=self._meal_draft_card_reply_markup(draft),
            )
            return

        if data.startswith("meal_edit_macros:"):
            draft_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, draft_id, "macros")
            self._try_answer_callback_query(query_id, "Введите калории и БЖУ.")
            self._send_message(
                chat_id,
                "Введите: калории белки жиры углеводы. Например: 420 31 12 46",
                reply_markup=self._draft_edit_prompt_reply_markup(draft_id),
            )
            return

        if data.startswith("meal_edit_portion_menu:"):
            draft_id = data.split(":", 1)[1]
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
            draft = self.service.get_meal_draft(app_user.user_id, draft_id)
            self._try_answer_callback_query(query_id, "Выберите порцию.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_draft_portion_text(draft),
                    reply_markup=self._meal_draft_portion_reply_markup(draft),
                )
            return

        if data.startswith("meal_edit_portion:"):
            _, draft_id, portion_kind = data.split(":", 2)
            factor = {
                "smaller": 0.8,
                "standard": 1.0,
                "bigger": 1.2,
            }[portion_kind]
            draft = self.service.scale_meal_draft_portion(app_user.user_id, draft_id, factor)
            self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
            self._try_answer_callback_query(query_id, "Порция обновлена.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_draft_card_text(draft),
                    reply_markup=self._meal_draft_card_reply_markup(draft),
                )
            return

        if data.startswith("meal_edit_portion_custom:"):
            draft_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, draft_id, "portion")
            self._try_answer_callback_query(query_id, "Введите свою порцию.")
            self._send_message(
                chat_id,
                self._portion_prompt_text(),
                reply_markup=self._draft_edit_prompt_reply_markup(draft_id),
            )
            return

        if data.startswith("meal_clarify_portion:"):
            _, draft_id, action = data.split(":", 2)
            state = self._pending_draft_clarifications.get(app_user.user_id)
            if state is None or state.get("draft_id") != draft_id or state.get("kind") != "portion":
                self._try_answer_callback_query(query_id, "Это уточнение уже неактуально.")
                return
            if action == "manual":
                self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
                draft = self.service.get_meal_draft(app_user.user_id, draft_id)
                self._try_answer_callback_query(query_id, "Можно поправить вручную.")
                if isinstance(message_id, int):
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._format_meal_draft_edit_menu_text(draft),
                        reply_markup=self._meal_draft_edit_menu_reply_markup(draft),
                    )
                return
            if action == "skip":
                self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
                draft = self.service.get_meal_draft(app_user.user_id, draft_id)
                self._try_answer_callback_query(query_id, "Оставляю как есть.")
                if isinstance(message_id, int):
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._format_meal_draft_card_text(draft),
                        reply_markup=self._meal_draft_card_reply_markup(draft),
                    )
                return
            factor = {
                "small": 0.8,
                "medium": 1.0,
                "large": 1.2,
            }.get(action)
            if factor is None:
                self._try_answer_callback_query(query_id, "Не удалось применить уточнение.")
                return
            draft = self.service.scale_meal_draft_portion(app_user.user_id, draft_id, factor)
            self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
            self._try_answer_callback_query(query_id, "Спасибо, уточнение учтено.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_draft_card_text(draft),
                    reply_markup=self._meal_draft_card_reply_markup(draft),
                )
            return

        if data.startswith("meal_clarify_title:"):
            _, draft_id, action = data.split(":", 2)
            state = self._pending_draft_clarifications.get(app_user.user_id)
            if state is None or state.get("draft_id") != draft_id or state.get("kind") != "title":
                self._try_answer_callback_query(query_id, "Это уточнение уже неактуально.")
                return
            if action == "manual":
                self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
                draft = self.service.get_meal_draft(app_user.user_id, draft_id)
                self._try_answer_callback_query(query_id, "Можно поправить вручную.")
                if isinstance(message_id, int):
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._format_meal_draft_edit_menu_text(draft),
                        reply_markup=self._meal_draft_edit_menu_reply_markup(draft),
                    )
                return
            if action == "skip":
                self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
                draft = self.service.get_meal_draft(app_user.user_id, draft_id)
                self._try_answer_callback_query(query_id, "Оставляю как есть.")
                if isinstance(message_id, int):
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._format_meal_draft_card_text(draft),
                        reply_markup=self._meal_draft_card_reply_markup(draft),
                    )
                return
            options = state.get("options")
            if not isinstance(options, list):
                self._try_answer_callback_query(query_id, "Не удалось применить уточнение.")
                return
            try:
                title = str(options[int(action)])
            except (ValueError, IndexError, TypeError):
                self._try_answer_callback_query(query_id, "Не удалось применить уточнение.")
                return
            draft = self.service.update_meal_draft(
                app_user.user_id,
                draft_id,
                title=title,
                summary=title,
            )
            self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
            self._try_answer_callback_query(query_id, "Спасибо, уточнение учтено.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_draft_card_text(draft),
                    reply_markup=self._meal_draft_card_reply_markup(draft),
                )
            return

        if data.startswith("meal_rewrite_prompt:"):
            draft_id = data.split(":", 1)[1]
            self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
            self._set_pending_draft_edit_state(app_user.user_id, draft_id, "rewrite")
            self._try_answer_callback_query(query_id, "Опишите правильное блюдо.")
            self._send_message(
                chat_id,
                "Введите правильное название блюда или короткое описание.\nНапример: гречка с куриной грудкой",
                reply_markup=self._draft_edit_prompt_reply_markup(draft_id),
            )
            return

        if data.startswith("meal_reject:"):
            draft_id = data.split(":", 1)[1]
            logger.info("Rejecting meal draft_id=%s user_id=%s", draft_id, app_user.user_id)
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
            self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft_id)
            self._try_answer_callback_query(query_id, "Отклоняю черновик...")
            try:
                draft = self.service.reject_meal_draft(app_user.user_id, draft_id)
            except ValueError as exc:
                logger.warning("Meal rejection failed draft_id=%s error=%s", draft_id, exc)
                self._send_message(chat_id, "Не удалось отклонить черновик: %s" % exc)
                return
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        "Черновик приема пищи отклонен\n"
                        "Блюдо: %s\n"
                        "Статус: отклонено"
                    )
                    % draft.title,
                )
            logger.info("Meal rejected draft_id=%s title=%s", draft_id, draft.title)
            self._send_message(chat_id, "Черновик приема пищи отклонен: %s." % draft.title)
            return

        if data.startswith("meal_saved_cancel:"):
            entry_id = data.split(":", 1)[1]
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            if not self._is_meal_recoverable_for_delete(meal):
                self._try_answer_callback_query(query_id, "Быстрая отмена уже недоступна.")
                if isinstance(message_id, int):
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._history_delete_in_app_text(),
                        reply_markup=self._history_app_inline_reply_markup(),
                    )
                return
            deleted = self.service.delete_meal_entry(app_user.user_id, entry_id)
            self._try_answer_callback_query(query_id, "Сохранение отменено.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="Сохранение отменено.\nЗапись удалена: %s." % deleted.title,
                )
            return

        if data.startswith("history_last_meal_edit:"):
            entry_id = data.split(":", 1)[1]
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            if not self._is_meal_recoverable_for_edit(meal):
                self._try_answer_callback_query(query_id, "Полные правки доступны в приложении.")
                if isinstance(message_id, int):
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._history_edit_in_app_text(),
                        reply_markup=self._history_app_inline_reply_markup(),
                    )
                return
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=entry_id)
            self._try_answer_callback_query(query_id, "Можно быстро исправить последнюю запись.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_last_meal_edit_menu_text(meal),
                    reply_markup=self._last_meal_edit_menu_reply_markup(meal),
                )
            return

        if data.startswith("history_last_meal_delete_confirm:"):
            entry_id = data.split(":", 1)[1]
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            if not self._is_meal_recoverable_for_delete(meal):
                self._try_answer_callback_query(query_id, "Быстрое удаление уже недоступно.")
                if isinstance(message_id, int):
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._history_delete_in_app_text(),
                        reply_markup=self._history_app_inline_reply_markup(),
                    )
                return
            deleted = self.service.delete_meal_entry(app_user.user_id, entry_id)
            self._try_answer_callback_query(query_id, "Последняя запись удалена.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        "Последняя запись удалена.\n"
                        "Удалено: %s.\n\n"
                        "Если нужна история и более глубокие правки, откройте приложение."
                    )
                    % deleted.title,
                    reply_markup=self._history_app_inline_reply_markup(),
                )
            return

        if data.startswith("history_last_meal_delete_prompt:"):
            entry_id = data.split(":", 1)[1]
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            if not self._is_meal_recoverable_for_delete(meal):
                self._try_answer_callback_query(query_id, "Быстрое удаление уже недоступно.")
                if isinstance(message_id, int):
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._history_delete_in_app_text(),
                        reply_markup=self._history_app_inline_reply_markup(),
                    )
                return
            self._try_answer_callback_query(query_id, "Подтвердите удаление.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="Удалить последнюю запись?",
                    reply_markup=self._history_delete_confirm_reply_markup(entry_id),
                )
            return

        if data.startswith("history_last_meal_delete_cancel:"):
            entry_id = data.split(":", 1)[1]
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            self._try_answer_callback_query(query_id, "Удаление отменено.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_last_meal_recovery_text(meal),
                    reply_markup=self._last_meal_recovery_reply_markup(meal),
                )
            return

        if data.startswith("meal_entry_edit_back:"):
            entry_id = data.split(":", 1)[1]
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=entry_id)
            self._try_answer_callback_query(query_id, "Возвращаю карточку записи.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_last_meal_recovery_text(meal),
                    reply_markup=self._last_meal_recovery_reply_markup(meal),
                )
            return

        if data.startswith("meal_entry_edit_title:"):
            entry_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, entry_id, "title", target_type="meal")
            self._try_answer_callback_query(query_id, "Введите новое название.")
            self._send_message(
                chat_id,
                "Введите новое название для последней записи.",
                reply_markup=self._meal_entry_edit_prompt_reply_markup(entry_id),
            )
            return

        if data.startswith("meal_entry_edit_portion_menu:"):
            entry_id = data.split(":", 1)[1]
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=entry_id)
            self._try_answer_callback_query(query_id, "Выберите порцию.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_entry_portion_text(meal),
                    reply_markup=self._meal_entry_portion_reply_markup(meal),
                )
            return

        if data.startswith("meal_entry_edit_portion:"):
            _, entry_id, portion_kind = data.split(":", 2)
            factor = {
                "smaller": 0.8,
                "standard": 1.0,
                "bigger": 1.2,
            }[portion_kind]
            updated = self.service.scale_meal_entry_portion(app_user.user_id, entry_id, factor)
            self._try_answer_callback_query(query_id, "Порция обновлена.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_last_meal_recovery_text(updated),
                    reply_markup=self._last_meal_recovery_reply_markup(updated),
                )
            return

        if data.startswith("meal_entry_edit_portion_custom:"):
            entry_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, entry_id, "portion", target_type="meal")
            self._try_answer_callback_query(query_id, "Введите свою порцию.")
            self._send_message(
                chat_id,
                self._portion_prompt_text(),
                reply_markup=self._meal_entry_edit_prompt_reply_markup(entry_id),
            )
            return

        if data.startswith("meal_entry_edit_summary:"):
            entry_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, entry_id, "summary", target_type="meal")
            self._try_answer_callback_query(query_id, "Введите новый состав.")
            self._send_message(
                chat_id,
                "Коротко опишите состав или комментарий к записи.",
                reply_markup=self._meal_entry_edit_prompt_reply_markup(entry_id),
            )
            return

        if data.startswith("meal_entry_edit_time:"):
            entry_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, entry_id, "time", target_type="meal")
            self._try_answer_callback_query(query_id, "Введите новое время.")
            self._send_message(
                chat_id,
                "Введите новое время в формате HH:MM.",
                reply_markup=self._meal_entry_time_prompt_reply_markup(entry_id),
            )
            return

        if data.startswith("meal_entry_edit_time_now:"):
            entry_id = data.split(":", 1)[1]
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            updated = self.service.update_meal_entry(
                app_user.user_id,
                entry_id,
                occurred_at=datetime.combine(meal.occurred_at.date(), self._local_now().time()),
            )
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=entry_id)
            self._try_answer_callback_query(query_id, "Время обновлено.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_last_meal_recovery_text(updated),
                    reply_markup=self._last_meal_recovery_reply_markup(updated),
                )
            return

        if data.startswith("meal_entry_edit_macros:"):
            entry_id = data.split(":", 1)[1]
            self._set_pending_draft_edit_state(app_user.user_id, entry_id, "macros", target_type="meal")
            self._try_answer_callback_query(query_id, "Введите новые калории и БЖУ.")
            self._send_message(
                chat_id,
                "Введите 4 значения: калории белки жиры углеводы.\nНапример: 420 31 12 46",
                reply_markup=self._meal_entry_edit_prompt_reply_markup(entry_id),
            )
            return

        if data == "profile_home":
            self._clear_pending_profile_edit_state(app_user.user_id)
            self._try_answer_callback_query(query_id, "Открываю профиль.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._profile_home_text(app_user),
                    reply_markup=self._profile_home_reply_markup(),
                )
            return

        if data == "profile_back_to_menu":
            self._clear_pending_profile_edit_state(app_user.user_id)
            self._try_answer_callback_query(query_id, "Возвращаю в главное меню.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._home_text(app_user),
                    reply_markup=self._menu_reply_markup(app_user),
                )
            return

        if data == "profile_about":
            self._clear_pending_profile_edit_state(app_user.user_id)
            self._try_answer_callback_query(query_id, "Открываю раздел «Обо мне».")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._profile_about_text(app_user),
                    reply_markup=self._profile_about_reply_markup(app_user),
                )
            return

        if data == "profile_goals":
            self._clear_pending_profile_edit_state(app_user.user_id)
            self._try_answer_callback_query(query_id, "Открываю цели.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._profile_goals_text(app_user),
                    reply_markup=self._profile_goals_reply_markup(),
                )
            return

        if data == "profile_reminders":
            self._clear_pending_profile_edit_state(app_user.user_id)
            self._try_answer_callback_query(query_id, "Открываю напоминания.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._profile_reminders_text(app_user),
                    reply_markup=self._profile_reminders_reply_markup(app_user),
                )
            return

        if data.startswith("profile_about_edit:"):
            field = data.split(":", 1)[1]
            self._set_pending_profile_edit_state(app_user.user_id, field)
            self._try_answer_callback_query(query_id, "Введите новое значение.")
            self._send_message(
                chat_id,
                self._profile_about_prompt_text(field),
                reply_markup=self._profile_prompt_reply_markup("profile_about"),
            )
            return

        if data.startswith("profile_goal_edit:"):
            field = data.split(":", 1)[1]
            self._set_pending_profile_edit_state(app_user.user_id, field)
            self._try_answer_callback_query(query_id, "Введите новое значение цели.")
            self._send_message(
                chat_id,
                self._profile_goal_prompt_text(field),
                reply_markup=self._profile_prompt_reply_markup("profile_goals"),
            )
            return

        if data.startswith("profile_about_sex:"):
            sex_key = data.split(":", 1)[1]
            updated = self.service.update_user_about(app_user.user_id, sex=UserSex(sex_key))
            self._try_answer_callback_query(query_id, "Сохранил пол.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._profile_about_text(updated),
                    reply_markup=self._profile_about_reply_markup(updated),
                )
            return

        if data.startswith("profile_about_goal:"):
            goal_key = data.split(":", 1)[1]
            updated = self.service.update_user_about(app_user.user_id, goal=UserGoal(goal_key))
            self._try_answer_callback_query(query_id, "Цель сохранена.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._profile_about_text(updated),
                    reply_markup=self._profile_about_reply_markup(updated),
                )
            return

        if data == "profile_goals_reset":
            updated = self.service.reset_user_goal_settings(app_user.user_id)
            self._try_answer_callback_query(query_id, "Цели сброшены.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._profile_goals_text(updated),
                    reply_markup=self._profile_goals_reply_markup(),
                )
            return

        if data.startswith("profile_reminders_toggle:"):
            toggle_key = data.split(":", 1)[1]
            updated = self._toggle_profile_reminder(app_user, toggle_key)
            self._try_answer_callback_query(query_id, "Настройки напоминаний обновлены.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._profile_reminders_text(updated),
                    reply_markup=self._profile_reminders_reply_markup(updated),
                )
            return

        logger.warning("Unknown callback action query_id=%s data=%s", query_id, data)
        self._answer_callback_query(query_id, "Неизвестное действие.")

    def _route_command(
        self,
        text: str,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        username: str = "",
        first_name: str = "",
        app_user: Optional[AppUser] = None,
    ) -> str:
        text = self._normalize_command_text(text)
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return "Не удалось разобрать команду: %s" % exc
        if not parts:
            return ""

        command = parts[0].split("@", 1)[0].lower()
        args = parts[1:]
        try:
            if command == "/start":
                return self._handle_start(
                    args=args,
                    chat_id=chat_id,
                    user_id=user_id,
                    username=username,
                    first_name=first_name,
                    app_user=app_user,
                )
            if command == "/help":
                if app_user is not None:
                    self._pending_custom_water_user_ids.discard(app_user.user_id)
                return self._help_text(app_user)
            if command == "/menu":
                if app_user is None:
                    return self._registration_required_text()
                self._pending_custom_water_user_ids.discard(app_user.user_id)
                return self._home_text(app_user)
            if command == "/whoami":
                return self._handle_whoami(chat_id=chat_id, user_id=user_id, app_user=app_user)

            if app_user is None:
                return self._registration_required_text()

            if command == "/add_food":
                return self._add_food_text()
            if command == "/add_water":
                self._pending_custom_water_user_ids.discard(app_user.user_id)
                return self._add_water_text()
            if command == "/water_custom":
                self._pending_custom_water_user_ids.add(app_user.user_id)
                return self._custom_water_prompt_text()
            if command == "/water":
                self._pending_custom_water_user_ids.discard(app_user.user_id)
                return self._handle_log_water(app_user, args)
            if command == "/history":
                self._clear_pending_last_meal_delete(app_user.user_id)
                return self._history_home_text()
            if command == "/history_fix_last":
                self._clear_pending_last_meal_delete(app_user.user_id)
                return self._history_fix_last_text(app_user)
            if command == "/history_delete_last":
                return self._handle_history_delete_last(app_user)
            if command == "/history_app":
                self._clear_pending_last_meal_delete(app_user.user_id)
                return self._history_app_text()
            if command == "/progress":
                return self._handle_progress(app_user)
            if command == "/profile":
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_home_text(app_user)
            if command == "/profile_about":
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_about_text(app_user)
            if command == "/profile_goals":
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_goals_text(app_user)
            if command == "/profile_reminders":
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_reminders_text(app_user)
            if command == "/how_it_works":
                return self._how_it_works_text()

            if command == "/import_tbank":
                self._ensure_admin(app_user)
                return self._handle_import_tbank()
            if command == "/finance_month":
                return self._handle_finance_month(app_user, args)
            if command == "/connect_drive":
                return self._handle_connect_drive(app_user, args)
            if command == "/drive_status":
                return self._handle_drive_status(app_user)
            if command == "/drive_on":
                return self._handle_drive_toggle(app_user, enabled=True)
            if command == "/drive_off":
                return self._handle_drive_toggle(app_user, enabled=False)
            if command == "/digest_status":
                return self._handle_digest_status(app_user)
            if command == "/user_mode":
                return self._handle_admin_mode(app_user, enabled=False)
            if command == "/admin_mode":
                return self._handle_admin_mode(app_user, enabled=True)
            if command == "/digest_on":
                return self._handle_digest_toggle(app_user, enabled=True)
            if command == "/digest_off":
                return self._handle_digest_toggle(app_user, enabled=False)
            if command == "/digest_preview":
                if chat_id is not None:
                    self._send_daily_digest_preview(chat_id=chat_id, app_user=app_user, args=args)
                    return ""
                return self._handle_digest_preview(app_user, args)
            if command == "/weekly_digest_preview":
                if chat_id is not None:
                    self._send_weekly_digest_preview(chat_id=chat_id, app_user=app_user, args=args)
                    return ""
                return self._handle_weekly_digest_preview(app_user, args)
            if command == "/confirm_meal":
                return self._handle_confirm_meal(app_user, args)
            if command == "/reject_meal":
                return self._handle_reject_meal(app_user, args)
            if command == "/drafts":
                return self._handle_drafts(app_user)
            if command == "/summary":
                return self._handle_summary(app_user, args)
            if command == "/decisions":
                return self._handle_decisions(app_user, args)
        except ValueError as exc:
            return "Некорректные аргументы команды: %s" % exc
        return "Неизвестная команда.\n\n%s" % self._help_text(app_user)

    def _handle_start(
        self,
        args: List[str],
        chat_id: Optional[int],
        user_id: Optional[int],
        username: str,
        first_name: str,
        app_user: Optional[AppUser],
    ) -> str:
        if app_user is not None:
            self._pending_custom_water_user_ids.discard(app_user.user_id)
            return self._onboarding_caption(step=1)
        if chat_id is None or user_id is None:
            return self._registration_required_text()
        self.service.register_user(
            telegram_user_id=user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            now=self._local_now(),
        )
        return self._onboarding_caption(step=1)

    def _handle_whoami(self, chat_id: Optional[int], user_id: Optional[int], app_user: Optional[AppUser]) -> str:
        lines = [
            "Данные Telegram",
            "окружение=%s" % self.settings.environment_name,
            "user_id=%s" % (user_id if user_id is not None else "неизвестно"),
            "chat_id=%s" % (chat_id if chat_id is not None else "неизвестно"),
            "режим_доступа=%s" % self.settings.registration_mode,
        ]
        if app_user is None:
            lines.append("статус_аккаунта=не_подключен")
        else:
            lines.append("app_user_id=%s" % app_user.user_id)
            lines.append("статус_аккаунта=%s" % app_user.status.value)
            lines.append("роль=%s" % ("admin" if app_user.has_admin_access else "user"))
            if app_user.is_admin:
                lines.append("режим_админа=%s" % ("включен" if app_user.admin_mode_enabled else "выключен"))
        return "\n".join(lines)

    @staticmethod
    def _handle_import_tbank() -> str:
        return (
            "Импорт операций Т-Банка\n"
            "1. В личном кабинете на tbank.ru на компьютере открой вкладку «Операции».\n"
            "2. Выбери период и нужные продукты.\n"
            "3. Нажми «Поделиться» и выгрузи операции в CSV.\n"
            "4. Отправь CSV-файл сюда одним сообщением.\n\n"
            "После загрузки я импортирую операции и покажу результат."
        )

    def _handle_finance_month(self, app_user: AppUser, args: List[str]) -> str:
        self._ensure_admin(app_user)
        if args:
            year, month = args[0].split("-", 1)
            month_start = date(int(year), int(month), 1)
        else:
            today = self._local_today()
            month_start = date(today.year, today.month, 1)
        summary = self.service.get_finance_monthly_summary(app_user.user_id, month_start)
        lines = [
            "Финансовая сводка за %s" % month_start.strftime("%m.%Y"),
            "Операций: %s" % summary.transaction_count,
            "Доходы: %.2f ₽" % summary.income_total,
            "Расходы: %.2f ₽" % summary.expense_total,
            "Чистый поток: %.2f ₽" % summary.net_total,
        ]
        if summary.top_expense_categories:
            lines.append("Топ категорий расходов:")
            for item in summary.top_expense_categories:
                lines.append("- %s: %.2f ₽ (%s)" % (item.category, item.amount, item.transaction_count))
        else:
            lines.append("Расходов за этот месяц пока нет.")
        return "\n".join(lines)

    def _handle_connect_drive(self, app_user: AppUser, args: List[str]) -> str:
        self._ensure_admin(app_user)
        if not self.service.google_drive_is_configured():
            return "Интеграция с Google Drive пока не настроена на сервере."
        if not args:
            return (
                "Подключение Google Drive\n"
                "Пришлите ссылку на папку Google Drive или folder ID.\n"
                "Папка должна быть открыта для service account, настроенного на сервере.\n\n"
                "Пример:\n"
                "/connect_drive https://drive.google.com/drive/folders/..."
            )
        try:
            settings = self.service.connect_google_drive_folder(
                app_user.user_id,
                folder_input=args[0],
                now=self._local_now(),
            )
        except RuntimeError as exc:
            logger.warning("Google Drive connect failed user_id=%s error=%s", app_user.user_id, exc)
            return str(exc)
        return (
            "Папка Google Drive подключена.\n"
            "folder_id=%s\n"
            "Импорт: %s"
            % (
                settings.folder_id,
                "включен" if settings.enabled else "выключен",
            )
        )

    def _handle_drive_status(self, app_user: AppUser) -> str:
        self._ensure_admin(app_user)
        settings = self.service.get_google_drive_settings(app_user.user_id)
        if settings is None:
            return (
                "Google Drive не подключен.\n"
                "Подключите папку командой:\n"
                "/connect_drive <folder_url>"
            )
        import_files = self.service.list_health_import_files(app_user.user_id, provider=None)
        last_import = import_files[-1] if import_files else None
        lines = [
            "Статус Google Drive",
            "Импорт: %s" % ("включен" if settings.enabled else "выключен"),
            "folder_id=%s" % settings.folder_id,
        ]
        if settings.last_successful_import_at is not None:
            lines.append("Последняя успешная проверка: %s" % settings.last_successful_import_at.strftime("%Y-%m-%d %H:%M"))
        if last_import is not None:
            lines.append("Последний импорт: %s" % last_import.imported_at.strftime("%Y-%m-%d %H:%M"))
            lines.append("Последний файл: %s" % last_import.file_name)
            lines.append("Статус файла: %s" % last_import.status.value)
        else:
            lines.append("Импортов пока не было.")
        return "\n".join(lines)

    def _handle_drive_toggle(self, app_user: AppUser, enabled: bool) -> str:
        self._ensure_admin(app_user)
        settings = self.service.set_google_drive_enabled(
            app_user.user_id,
            enabled=enabled,
            now=self._local_now(),
        )
        return (
            "Импорт Google Drive %s.\n"
            "folder_id=%s"
            % (
                "включен" if settings.enabled else "выключен",
                settings.folder_id,
            )
        )

    def _handle_digest_status(self, app_user: AppUser) -> str:
        settings = self.service.get_digest_settings(app_user.user_id)
        return (
            "Настройки digest\n"
            "Часовой пояс: %s\n"
            "Ежедневная сводка: %s в %s\n"
            "Недельная сводка: %s по понедельникам в %s"
            % (
                settings.timezone_name,
                "включена" if settings.daily_digest_enabled else "выключена",
                settings.daily_digest_time,
                "включена" if settings.weekly_digest_enabled else "выключена",
                settings.weekly_digest_time,
            )
        )

    def _handle_digest_toggle(self, app_user: AppUser, enabled: bool) -> str:
        settings = self.service.set_digest_enabled(app_user.user_id, enabled=enabled)
        state = "включен" if enabled else "выключен"
        return (
            "Digest %s.\n"
            "Ежедневная сводка: %s в %s\n"
            "Недельная сводка: %s по понедельникам в %s"
            % (
                state,
                "включена" if settings.daily_digest_enabled else "выключена",
                settings.daily_digest_time,
                "включена" if settings.weekly_digest_enabled else "выключена",
                settings.weekly_digest_time,
            )
        )

    def _handle_admin_mode(self, app_user: AppUser, enabled: bool) -> str:
        if not app_user.is_admin:
            raise ValueError("Команда доступна только администратору.")
        updated = self.service.set_admin_mode(app_user.user_id, enabled=enabled)
        if enabled:
            return "Режим администратора включен.\n\n%s" % self._help_text(updated)
        return "Режим обычного пользователя включен.\n\n%s" % self._help_text(updated)

    def _handle_digest_preview(self, app_user: AppUser, args: List[str]) -> str:
        target_date = date.fromisoformat(args[0]) if args else (self._local_today() - timedelta(days=1))
        digest = self.service.build_daily_food_digest(app_user.user_id, target_date)
        if digest is None:
            return "Для %s нет подтвержденных фото-блюд для daily digest." % target_date.isoformat()
        step_progress = self.service.build_step_progress_insight(app_user.user_id, target_date) if app_user.has_admin_access else None
        return self._format_daily_digest_text(digest, preview=True, step_progress=step_progress)

    def _handle_weekly_digest_preview(self, app_user: AppUser, args: List[str]) -> str:
        week_start = self._resolve_weekly_digest_preview_week_start(args)
        digest = self.service.build_weekly_food_digest(app_user.user_id, week_start)
        if digest is None:
            return "Для недели %s — %s нет подтвержденных фото-блюд для weekly digest." % (
                week_start.isoformat(),
                (week_start + timedelta(days=6)).isoformat(),
            )
        return self._format_weekly_digest_text(digest, preview=True)

    def _resolve_weekly_digest_preview_week_start(self, args: List[str]) -> date:
        if args:
            if args[0].strip().lower() == "prev":
                base_date = self._local_today() - timedelta(days=7)
            else:
                base_date = date.fromisoformat(args[0])
        else:
            base_date = self._local_today()
        week_start = base_date - timedelta(days=base_date.weekday())
        return week_start

    def _handle_confirm_meal(self, app_user: AppUser, args: List[str]) -> str:
        if len(args) != 1:
            return "Использование: /confirm_meal <draft_id>"
        self._clear_pending_draft_clarification(app_user.user_id, draft_id=args[0])
        meal = self.service.confirm_meal_draft(app_user.user_id, args[0])
        if meal.kind == PhotoLogKind.WATER:
            return "Вода сохранена: %s л." % self._format_liters(meal.water_ml)
        return "Прием пищи сохранен: %s." % meal.title

    def _handle_reject_meal(self, app_user: AppUser, args: List[str]) -> str:
        if len(args) != 1:
            return "Использование: /reject_meal <draft_id>"
        self._clear_pending_draft_clarification(app_user.user_id, draft_id=args[0])
        draft = self.service.reject_meal_draft(app_user.user_id, args[0])
        return "Черновик приема пищи отклонен: %s." % draft.title

    def _handle_drafts(self, app_user: AppUser) -> str:
        drafts = self.service.list_meal_drafts(app_user.user_id, status=MealDraftStatus.PENDING)
        if not drafts:
            return "Нет ожидающих черновиков приема пищи."
        lines = ["Ожидающие черновики приема пищи:"]
        for draft in drafts[:10]:
            lines.append(
                "- %s | %s ккал | уверенность %.2f | id=%s"
                % (draft.title, draft.calories, draft.confidence, draft.draft_id)
            )
        return "\n".join(lines)

    def _handle_summary(self, app_user: AppUser, args: List[str]) -> str:
        if args:
            target_date = date.fromisoformat(args[0])
        else:
            target_date = self._local_today()
        self.service.evaluate_day(app_user.user_id, target_date, now=self._local_now())
        summary = self.service.get_daily_summary(app_user.user_id, target_date)
        yesterday_steps = self.service.build_step_progress_insight(app_user.user_id, target_date - timedelta(days=1))
        response = (
            "Сводка за %s\n"
            "Приемы пищи: %s\n"
            "Калории: %s\n"
            "Белок: %.1f / %s г\n"
            "Жиры: %.1f г\n"
            "Углеводы: %.1f г\n"
            "Вода: %s / %s мл\n"
            "Сон: %.2f / %.1f ч\n"
            "Шаги: %s / %s"
            % (
                target_date.isoformat(),
                summary.meals_count,
                summary.calories,
                summary.protein_g,
                summary.goals.protein_g,
                summary.fat_g,
                summary.carbs_g,
                summary.water_ml,
                summary.goals.water_ml,
                summary.sleep_hours,
                summary.goals.sleep_hours,
                summary.steps,
                summary.goals.steps,
            )
        )
        response += (
            "\nШаги за вчера (%s): %s / %s"
            % (
                yesterday_steps.reference_date.isoformat(),
                yesterday_steps.steps,
                yesterday_steps.target_steps,
            )
        )
        if yesterday_steps.average_steps_30d is not None:
            response += "\n30-дневная средняя: %.1f" % yesterday_steps.average_steps_30d
        else:
            response += "\n30-дневная средняя: нет данных"
        response += "\nКомментарий по шагам: %s" % yesterday_steps.comment
        return response

    def _handle_decisions(self, app_user: AppUser, args: List[str]) -> str:
        if args:
            target_date = date.fromisoformat(args[0])
        else:
            target_date = self._local_today()
        self.service.evaluate_day(app_user.user_id, target_date, now=self._local_now())
        decisions = self.service.list_decisions(
            app_user.user_id,
            status=DecisionStatus.OPEN,
            target_date=target_date,
        )
        if not decisions:
            return "Нет открытых решений на %s." % target_date.isoformat()

        lines = ["Открытые решения на %s:" % target_date.isoformat()]
        for decision in decisions:
            lines.append("- [%s] %s" % (decision.kind.value, decision.title))
        return "\n".join(lines)

    def _help_text(self, app_user: Optional[AppUser]) -> str:
        if app_user is None:
            return (
                "Окружение: %s\n" % self.settings.environment_name
                + "%s\n" % format_version_line()
                + "%s\n" % format_release_date_line()
                + "Доступ: открыт для всех, только в личных сообщениях.\n"
                + "Чтобы начать работу, отправьте команду:\n"
                + "/start\n\n"
                + "Доступные команды без подключения:\n"
                + "/start\n"
                + "/whoami\n"
                + "/help"
            )

        commands = [
            "Справка",
            "Окружение: %s" % self.settings.environment_name,
            format_version_line(),
            format_release_date_line(),
            self._mini_app_help_line(app_user),
            "Команды:",
            "/whoami",
            "Отправь фото еды, чтобы я сразу сохранил запись или предложил её проверить.",
            "/menu",
            "/digest_status",
            "/digest_on",
            "/digest_off",
            "/digest_preview [YYYY-MM-DD]",
            "/weekly_digest_preview [YYYY-MM-DD]",
            "/confirm_meal <draft_id>",
            "/reject_meal <draft_id>",
            "/drafts",
            "/summary [YYYY-MM-DD]",
            "/decisions [YYYY-MM-DD]",
        ]
        if app_user.is_admin:
            commands.extend(
                [
                    "/user_mode",
                    "/admin_mode",
                ]
            )
        if app_user.has_admin_access:
            commands.extend(
                [
                    "/import_tbank",
                    "/finance_month [YYYY-MM]",
                    "/connect_drive <folder_url>",
                    "/drive_status",
                    "/drive_on",
                    "/drive_off",
                ]
            )
        return "\n".join(commands)

    def _welcome_text(self, app_user: AppUser) -> str:
        first_name = app_user.first_name or "Привет"
        return (
            "Привет, %s!\n"
            "Я помогу вести дневник еды без ручного подсчета: отправляй фото, а я соберу запись и покажу прогресс.\n\n"
            "Что можно сделать прямо сейчас:\n"
            "- Добавить еду\n"
            "- Добавить воду\n"
            "- Как это работает"
        ) % first_name

    @staticmethod
    def _should_show_onboarding(app_user: AppUser) -> bool:
        return app_user.onboarding_completed_at is None

    @classmethod
    def _onboarding_indicator(cls, step: int) -> str:
        return " ".join("●" if index == step else "○" for index in range(1, cls.ONBOARDING_STEP_COUNT + 1))

    @classmethod
    def _onboarding_caption(cls, step: int) -> str:
        steps = {
            1: (
                "Фотографируйте еду — остальное я беру на себя",
                "Просто отправьте фото в чат — я распознаю и сохраню блюдо.",
            ),
            2: (
                "Каждый день — наглядная сводка",
                "Я могу присылать ежедневные сводки — общую картину за день и короткие выводы.\n\n"
                "Так проще замечать свои привычки и менять их.",
            ),
            3: (
                "История и профиль — в мини-приложении",
                "Историю приемов пищи, профиль и личные настройки удобно смотреть в мини-приложении — голубая кнопка *Открыть*.",
            ),
        }
        title, body = steps[step]
        return "%s\n\n%s\n\n%s" % (title, body, cls._onboarding_indicator(step))

    @classmethod
    def _onboarding_asset_path(cls, step: int) -> Path:
        return cls.ONBOARDING_ASSET_DIR / ("onboarding-step-%s.jpg" % step)

    @classmethod
    def _onboarding_reply_markup(cls, step: int) -> str:
        keyboard: List[List[Dict[str, str]]] = []
        if step < cls.ONBOARDING_STEP_COUNT:
            keyboard.append(
                [
                    {"text": "Пропустить", "callback_data": "onboarding:skip"},
                    {"text": "⬅️ Далее", "callback_data": "onboarding:next:%s" % (step + 1)},
                ]
            )
        else:
            keyboard.append([{"text": "Начать", "callback_data": "onboarding:start"}])
            keyboard.append([{"text": "Пропустить", "callback_data": "onboarding:skip"}])
        return json.dumps({"inline_keyboard": keyboard}, ensure_ascii=False)

    def _home_text(self, app_user: AppUser) -> str:
        return (
            "Главный экран\n"
            "Теперь просто отправьте фото еды в чат — я распознаю блюдо и сохраню запись."
        )

    @staticmethod
    def _add_food_text() -> str:
        return (
            "Добавить еду\n"
            "Просто отправьте фото еды одним сообщением. Я распознаю блюдо и либо сохраню запись сразу, либо попрошу быстро её проверить."
        )

    @staticmethod
    def _add_water_text() -> str:
        return "Сколько воды добавить?"

    @staticmethod
    def _custom_water_prompt_text() -> str:
        return "Введите объем воды в мл, например: 330"

    @staticmethod
    def _history_home_text() -> str:
        return (
            "История\n"
            "В чате доступны только быстрые исправления последней записи.\n\n"
            "Что можно сделать:\n"
            "- Исправить последнюю запись\n"
            "- Отменить последнюю запись\n"
            "- История и правки в приложении"
        )

    def _history_fix_last_text(self, app_user: AppUser) -> str:
        pending_draft = self._get_latest_recoverable_pending_draft(app_user)
        if pending_draft is not None:
            return self._format_meal_draft_card_text(pending_draft)
        meal = self._get_latest_recoverable_meal(app_user)
        if meal is not None:
            return self._format_last_meal_recovery_text(meal)
        return self._history_edit_in_app_text()

    def _history_delete_last_text(self, app_user: AppUser) -> str:
        meal = self._get_latest_deletable_meal(app_user)
        if meal is not None:
            return "Удалить последнюю запись?"
        return self._history_delete_in_app_text()

    def _handle_history_delete_last(self, app_user: AppUser) -> str:
        meal = self._get_latest_deletable_meal(app_user)
        if meal is None:
            self._clear_pending_last_meal_delete(app_user.user_id)
            return self._history_delete_in_app_text()
        self._set_pending_last_meal_delete(app_user.user_id, meal.entry_id)
        return "Удалить последнюю запись?"

    @staticmethod
    def _history_edit_in_app_text() -> str:
        return (
            "Полное редактирование доступно в приложении.\n"
            "Откройте историю и правки в Mini App."
        )

    @staticmethod
    def _history_delete_in_app_text() -> str:
        return (
            "Быстрое удаление доступно только для самой последней записи и только сразу после сохранения.\n"
            "Если нужна история и более глубокие правки, откройте приложение."
        )

    @staticmethod
    def _history_app_text() -> str:
        return "Полная история, редактирование и удаление доступны в приложении."

    @staticmethod
    def _profile_home_text(app_user: AppUser) -> str:
        return (
            "Профиль\n"
            "Здесь можно настроить личные параметры, цели и напоминания.\n\n"
            "Сейчас:\n"
            "- Цель по воде: %s л\n"
            "- Цель по белку: %s г\n"
            "- Напоминания: %s"
        ) % (
            TelegramHealthBot._format_liters_fixed(app_user.target_water_ml),
            app_user.target_protein_g,
            "включены" if app_user.reminders_enabled else "выключены",
        )

    @staticmethod
    def _profile_about_text(app_user: AppUser) -> str:
        return (
            "Обо мне\n"
            "Пол: %s\n"
            "Возраст: %s\n"
            "Рост: %s\n"
            "Вес: %s\n"
            "Цель: %s"
        ) % (
            TelegramHealthBot._profile_sex_label(app_user.sex),
            ("%s лет" % app_user.age_years) if app_user.age_years is not None else "не указан",
            ("%s см" % app_user.height_cm) if app_user.height_cm is not None else "не указан",
            ("%s кг" % TelegramHealthBot._format_decimal(app_user.profile_weight_kg))
            if app_user.profile_weight_kg is not None
            else "не указан",
            TelegramHealthBot._profile_goal_label(app_user.goal),
        )

    @staticmethod
    def _profile_goals_text(app_user: AppUser) -> str:
        calories_text = "не задан"
        if app_user.target_calories_min is not None and app_user.target_calories_max is not None:
            if app_user.target_calories_min == app_user.target_calories_max:
                calories_text = "%s ккал" % app_user.target_calories_min
            else:
                calories_text = "%s–%s ккал" % (app_user.target_calories_min, app_user.target_calories_max)
        return (
            "Цели\n"
            "Вода: %s л\n"
            "Белок: %s г\n"
            "Калории: %s\n\n"
            "Можно изменить отдельную цель или сбросить все к рекомендованным."
        ) % (
            TelegramHealthBot._format_liters_fixed(app_user.target_water_ml),
            app_user.target_protein_g,
            calories_text,
        )

    @staticmethod
    def _profile_reminders_text(app_user: AppUser) -> str:
        return (
            "Напоминания\n"
            "Главный переключатель: %s\n"
            "Записать еду: %s\n"
            "Вода: %s\n"
            "Вечерний итог дня: %s\n\n"
            "Все напоминания можно выключить одним переключателем."
        ) % (
            "включен" if app_user.reminders_enabled else "выключен",
            "включено" if app_user.reminder_meal_logging else "выключено",
            "включено" if app_user.reminder_water else "выключено",
            "включено" if app_user.reminder_evening_summary else "выключено",
        )

    @staticmethod
    def _profile_about_prompt_text(field: str) -> str:
        prompts = {
            "age": "Введите возраст полным числом. Например: 32",
            "height": "Введите рост в сантиметрах. Например: 176",
            "weight": "Введите вес в килограммах. Например: 81.5",
        }
        return prompts[field]

    @staticmethod
    def _profile_goal_prompt_text(field: str) -> str:
        prompts = {
            "water_goal": "Введите цель по воде в мл. Например: 2200",
            "protein_goal": "Введите цель по белку в граммах. Например: 110",
            "calorie_goal": "Введите цель по калориям: 1800 или диапазон 1800-2200",
        }
        return prompts[field]

    @staticmethod
    def _coming_soon_text(section_name: str) -> str:
        return "%s\nЭтот раздел скоро появится. Пока можно отправить фото еды или открыть /help." % section_name

    @staticmethod
    def _how_it_works_text() -> str:
        return (
            "Как это работает\n"
            "1. Отправьте фото еды одним сообщением.\n"
            "2. Я распознаю блюдо и сохраню запись.\n"
            "3. Сохранённую запись можно удалить или отредактировать.\n"
            "4. В Профиле можно настроить цели по воде, калориям и БЖУ.\n"
            "5. Настроить отчёты — ежедневный, еженедельный."
        )

    def _handle_log_water(self, app_user: AppUser, args: List[str]) -> str:
        if len(args) != 1:
            raise ValueError("укажите объем воды в мл, например: /water 500")
        amount_ml = self._parse_water_amount(args[0])
        target_date = self._local_today()
        progress = self.service.log_water_and_get_progress(
            app_user.user_id,
            WaterEntry(
                entry_id=str(uuid4()),
                occurred_at=self._local_now(),
                amount_ml=amount_ml,
            ),
            target_date,
        )
        remaining_ml = max(0, progress.goal_water_ml - progress.water_ml)
        return (
            "+%s мл воды добавлено.\n"
            "Сегодня: %s / %s л\n"
            "Осталось до цели: %s мл."
        ) % (
            amount_ml,
            self._format_liters_fixed(progress.water_ml),
            self._format_liters_fixed(progress.goal_water_ml),
            remaining_ml,
        )

    def _handle_progress(self, app_user: AppUser) -> str:
        return self._handle_summary(app_user, [])

    def _format_last_meal_recovery_text(self, meal: MealEntry) -> str:
        summary = self._extract_meal_summary_from_notes(meal)
        lines = [
            "Последняя запись",
            "Название: %s" % meal.title,
            "Время: %s" % meal.occurred_at.strftime("%d.%m.%Y %H:%M"),
            "Калории: %s ккал" % self._format_integer_with_spaces(meal.calories),
            "Б %s г • Ж %s г • У %s г"
            % (
                self._format_decimal(meal.protein_g),
                self._format_decimal(meal.fat_g),
                self._format_decimal(meal.carbs_g),
            ),
        ]
        if summary:
            lines.append("Состав: %s" % summary)
        lines.append("")
        lines.append("Эту запись еще можно быстро исправить в чате.")
        return "\n".join(lines)

    @staticmethod
    def _format_last_meal_edit_menu_text(meal: MealEntry) -> str:
        return "Что изменить в последней записи «%s»?" % meal.title

    def _get_latest_recoverable_pending_draft(self, app_user: AppUser) -> Optional[MealPhotoDraft]:
        draft = self.service.get_latest_pending_meal_draft(app_user.user_id)
        if draft is None:
            return None
        return draft if self._is_draft_recoverable_for_edit(draft) else None

    def _get_latest_recoverable_meal(self, app_user: AppUser) -> Optional[MealEntry]:
        meal = self.service.get_latest_meal(app_user.user_id)
        if meal is None:
            return None
        return meal if self._is_meal_recoverable_for_edit(meal) else None

    def _get_latest_deletable_meal(self, app_user: AppUser) -> Optional[MealEntry]:
        meal = self.service.get_latest_meal(app_user.user_id)
        if meal is None:
            return None
        return meal if self._is_meal_recoverable_for_delete(meal) else None

    def _is_draft_recoverable_for_edit(self, draft: MealPhotoDraft) -> bool:
        return self._local_now() - draft.created_at <= self.HISTORY_EDIT_WINDOW

    def _is_meal_recoverable_for_edit(self, meal: MealEntry) -> bool:
        reference_time = meal.created_at or meal.occurred_at
        return self._local_now() - reference_time <= self.HISTORY_EDIT_WINDOW

    def _is_meal_recoverable_for_delete(self, meal: MealEntry) -> bool:
        reference_time = meal.created_at or meal.occurred_at
        return self._local_now() - reference_time <= self.HISTORY_DELETE_WINDOW

    @staticmethod
    def _parse_water_amount(raw_value: str) -> int:
        try:
            amount_ml = int(raw_value)
        except ValueError as exc:
            raise ValueError("объем воды должен быть целым числом в мл") from exc
        if amount_ml < 50 or amount_ml > 3000:
            raise ValueError("объем воды должен быть от 50 до 3000 мл")
        return amount_ml

    @staticmethod
    def _parse_age_years(raw_value: str) -> int:
        try:
            age_years = int(raw_value)
        except ValueError as exc:
            raise ValueError("Возраст нужно ввести целым числом. Например: 32") from exc
        if age_years < 10 or age_years > 120:
            raise ValueError("Возраст должен быть в диапазоне от 10 до 120 лет.")
        return age_years

    @staticmethod
    def _parse_height_cm(raw_value: str) -> int:
        try:
            height_cm = int(raw_value)
        except ValueError as exc:
            raise ValueError("Рост нужно ввести целым числом в сантиметрах. Например: 176") from exc
        if height_cm < 100 or height_cm > 250:
            raise ValueError("Рост должен быть в диапазоне от 100 до 250 см.")
        return height_cm

    @staticmethod
    def _parse_weight_kg(raw_value: str) -> float:
        try:
            weight_kg = float(raw_value.replace(",", "."))
        except ValueError as exc:
            raise ValueError("Вес нужно ввести в килограммах. Например: 81.5") from exc
        if weight_kg < 30 or weight_kg > 300:
            raise ValueError("Вес должен быть в диапазоне от 30 до 300 кг.")
        return round(weight_kg, 1)

    @staticmethod
    def _parse_water_goal_ml(raw_value: str) -> int:
        try:
            amount_ml = int(raw_value)
        except ValueError as exc:
            raise ValueError("Цель по воде нужно ввести числом в мл. Например: 2200") from exc
        if amount_ml < 500 or amount_ml > 6000:
            raise ValueError("Цель по воде должна быть в диапазоне от 500 до 6000 мл.")
        return amount_ml

    @staticmethod
    def _parse_protein_goal_g(raw_value: str) -> int:
        try:
            protein_g = int(raw_value)
        except ValueError as exc:
            raise ValueError("Цель по белку нужно ввести целым числом. Например: 110") from exc
        if protein_g < 30 or protein_g > 300:
            raise ValueError("Цель по белку должна быть в диапазоне от 30 до 300 г.")
        return protein_g

    @staticmethod
    def _parse_calorie_goal(raw_value: str) -> tuple[int, int]:
        normalized = raw_value.replace(" ", "")
        if "-" in normalized:
            left, right = normalized.split("-", 1)
            try:
                calories_min = int(left)
                calories_max = int(right)
            except ValueError as exc:
                raise ValueError("Введите калории как 1800 или диапазон 1800-2200.") from exc
        else:
            try:
                calories_min = calories_max = int(normalized)
            except ValueError as exc:
                raise ValueError("Введите калории как 1800 или диапазон 1800-2200.") from exc
        if calories_min < 800 or calories_max > 6000 or calories_min > calories_max:
            raise ValueError("Калории должны быть в разумном диапазоне, например 1800-2200.")
        return calories_min, calories_max

    @staticmethod
    def _profile_sex_label(sex: Optional[UserSex]) -> str:
        if sex == UserSex.MALE:
            return "мужчина"
        if sex == UserSex.FEMALE:
            return "женщина"
        return "не указан"

    @staticmethod
    def _profile_goal_label(goal: Optional[UserGoal]) -> str:
        if goal == UserGoal.MAINTENANCE:
            return "поддержание"
        if goal == UserGoal.WEIGHT_LOSS:
            return "похудение"
        if goal == UserGoal.MASS_GAIN:
            return "набор массы"
        return "не указана"

    def _format_new_decisions(self, decisions: Iterable) -> str:
        decision_list = list(decisions)
        if not decision_list:
            return "Новых решений нет."
        lines = ["Новые решения:"]
        for decision in decision_list:
            lines.append("- [%s] %s" % (decision.kind.value, decision.title))
        return "\n".join(lines)

    def _response_debug_enabled(self) -> bool:
        return False

    def _begin_response_debug(self, label: str) -> Optional[ResponseDebugTrace]:
        previous = self._active_response_debug
        if self._response_debug_enabled():
            self._active_response_debug = ResponseDebugTrace(label=label, started_at=time.perf_counter())
        return previous

    def _finish_response_debug(self, previous: Optional[ResponseDebugTrace]) -> None:
        self._active_response_debug = previous

    def _record_response_debug_step(self, label: str, started_at: float) -> None:
        trace = self._active_response_debug
        if trace is None:
            return
        trace.add_step(label, time.perf_counter() - started_at)

    def _add_response_debug_step(self, label: str, seconds: float) -> None:
        trace = self._active_response_debug
        if trace is None:
            return
        trace.add_step(label, seconds)

    def _append_response_debug_text(self, text: str) -> str:
        trace = self._active_response_debug
        if trace is None or "Отладка:" in text:
            return text
        lines = ["", "Отладка:", "Сценарий: %s" % trace.label]
        for label, seconds in trace.steps:
            lines.append("- %s: %.2f сек" % (label, seconds))
        lines.append("Генерация ответа: %.2f сек" % (time.perf_counter() - trace.started_at))
        return text.rstrip() + "\n\n" + "\n".join(lines)

    def _get_updates(self, offset: Optional[int]) -> List[Dict[str, object]]:
        params = {
            "timeout": self.settings.polling_timeout_seconds,
        }
        if offset is not None:
            params["offset"] = offset
        return self._telegram_api("getUpdates", params)

    def _send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ):
        text = self._append_response_debug_text(text)
        params = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        return self._telegram_api("sendMessage", params)

    def send_text_message(self, chat_id: int, text: str):
        return self._send_message(chat_id, text)

    def _send_photo_bytes(
        self,
        chat_id: int,
        photo_bytes: bytes,
        *,
        filename: str = "digest.jpg",
        caption: Optional[str] = None,
        reply_markup: Optional[str] = None,
        mime_type: str = "image/jpeg",
    ):
        params: Dict[str, object] = {"chat_id": chat_id}
        if caption:
            params["caption"] = self._append_response_debug_text(caption)
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return self._telegram_api_multipart(
            "sendPhoto",
            params=params,
            file_field_name="photo",
            filename=filename,
            file_bytes=photo_bytes,
            mime_type=mime_type,
        )

    def _send_onboarding_step(self, chat_id: int, *, step: int) -> None:
        asset_path = self._onboarding_asset_path(step)
        photo_bytes = asset_path.read_bytes()
        self._send_photo_bytes(
            chat_id,
            photo_bytes,
            filename=asset_path.name,
            caption=self._onboarding_caption(step),
            reply_markup=self._onboarding_reply_markup(step),
            mime_type="image/jpeg",
        )

    def _delete_message(self, chat_id: int, message_id: int) -> None:
        self._telegram_api(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )

    def _try_delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            self._delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Message delete failed chat_id=%s message_id=%s error=%s",
                chat_id,
                message_id,
                exc,
            )

    def _edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[str] = None,
    ) -> None:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": self._append_response_debug_text(text),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._telegram_api("editMessageText", payload)

    def _try_edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[str] = None,
    ) -> None:
        try:
            self._edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Callback message edit failed chat_id=%s message_id=%s error=%s",
                chat_id,
                message_id,
                exc,
            )

    def _send_meal_draft(self, chat_id: int, draft: MealPhotoDraft, app_user: Optional[AppUser] = None) -> None:
        if draft.is_water_only:
            self._telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": self._append_response_debug_text(
                        (
                        "Черновик воды\n"
                        "Напиток: %s\n"
                        "Объем: %s л\n"
                        "Описание: %s\n"
                        "Уверенность: %.2f\n"
                        "ID черновика: %s"
                        % (
                            draft.title,
                            self._format_liters(draft.water_ml),
                            draft.summary,
                            draft.confidence,
                            draft.draft_id,
                        )
                        )
                    ),
                    "reply_markup": json.dumps(
                        {
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "Подтвердить",
                                        "callback_data": "meal_confirm:%s" % draft.draft_id,
                                    },
                                    {
                                        "text": "Отклонить",
                                        "callback_data": "meal_reject:%s" % draft.draft_id,
                                    },
                                ]
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
            )
            return
        if app_user is not None:
            self._clear_pending_draft_clarification(app_user.user_id, draft_id=draft.draft_id)
        draft_card_started_at = time.perf_counter()
        draft_text = self._format_low_confidence_draft_text(draft)
        self._record_response_debug_step("сборка карточки черновика", draft_card_started_at)
        self._telegram_api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": self._append_response_debug_text(draft_text),
                "reply_markup": self._meal_draft_card_reply_markup(draft),
            },
        )

    def _send_daily_digest_preview(self, chat_id: int, app_user: AppUser, args: List[str]) -> None:
        target_date = date.fromisoformat(args[0]) if args else (self._local_today() - timedelta(days=1))
        if self.send_daily_digest(
            chat_id=chat_id,
            user_id=app_user.user_id,
            digest_date=target_date,
            preview=True,
            include_step_insight=app_user.has_admin_access,
        ) is None:
            self._send_message(chat_id, "Для %s нет подтвержденных фото-блюд для daily digest." % target_date.isoformat())

    def _send_weekly_digest_preview(self, chat_id: int, app_user: AppUser, args: List[str]) -> None:
        week_start = self._resolve_weekly_digest_preview_week_start(args)
        if self.send_weekly_digest(chat_id=chat_id, user_id=app_user.user_id, week_start=week_start, preview=True) is None:
            self._send_message(
                chat_id,
                "Для недели %s — %s нет подтвержденных фото-блюд для weekly digest."
                % (week_start.isoformat(), (week_start + timedelta(days=6)).isoformat()),
            )

    def send_daily_digest(
        self,
        chat_id: int,
        user_id: int,
        digest_date: date,
        preview: bool = False,
        include_step_insight: bool = False,
    ) -> Optional[Dict[str, object]]:
        started_at = time.perf_counter()
        build_timings: Dict[str, float] = {}
        digest = self.service.build_daily_food_digest(user_id, digest_date, debug_timings=build_timings)
        if digest is None:
            return None
        step_progress = None
        step_insight_seconds = 0.0
        if include_step_insight:
            step_insight_started_at = time.perf_counter()
            step_progress = self.service.build_step_progress_insight(
                user_id,
                digest_date,
                target_steps=digest.steps_goal,
            )
            step_insight_seconds = time.perf_counter() - step_insight_started_at
        photo_result = None
        render_started_at = time.perf_counter()
        mosaic_bytes = self.digest_renderer.render_daily_mosaic(digest)
        image_generation_seconds = time.perf_counter() - render_started_at
        send_photo_seconds = 0.0
        if mosaic_bytes is not None:
            send_photo_started_at = time.perf_counter()
            photo_result = self._send_photo_bytes(
                chat_id,
                mosaic_bytes,
                filename="daily-digest-%s.jpg" % digest_date.isoformat(),
            )
            send_photo_seconds = time.perf_counter() - send_photo_started_at
        debug_info = None
        app_user = self.service.get_user_by_id(user_id)
        if self._response_debug_enabled() and app_user is not None and app_user.has_admin_access:
            debug_info = {
                "build_label": "daily digest",
                "build_seconds": build_timings.get("build_digest_seconds", 0.0),
                "build_steps": self._build_daily_digest_debug_steps(build_timings, step_insight_seconds),
                "image_generation_seconds": image_generation_seconds,
                "send_photo_seconds": send_photo_seconds,
                "total_response_seconds": time.perf_counter() - started_at,
            }
        text_result = self._send_message(
            chat_id,
            self._format_daily_digest_text(
                digest,
                preview=preview,
                step_progress=step_progress,
                debug_info=debug_info,
            ),
            parse_mode="Markdown",
        )
        payload: Dict[str, object] = {
            "digest_type": "daily",
            "digest_date": digest_date.isoformat(),
        }
        if isinstance(photo_result, dict):
            payload["photo_message_id"] = photo_result.get("message_id", "")
        if isinstance(text_result, dict):
            payload["text_message_id"] = text_result.get("message_id", "")
        return payload

    def send_weekly_digest(self, chat_id: int, user_id: int, week_start: date, preview: bool = False) -> Optional[Dict[str, object]]:
        started_at = time.perf_counter()
        build_timings: Dict[str, float] = {}
        digest = self.service.build_weekly_food_digest(user_id, week_start, debug_timings=build_timings)
        if digest is None:
            return None
        photo_result = None
        render_started_at = time.perf_counter()
        mosaic_bytes = self.digest_renderer.render_weekly_mosaic(digest)
        image_generation_seconds = time.perf_counter() - render_started_at
        send_photo_seconds = 0.0
        if mosaic_bytes is not None:
            send_photo_started_at = time.perf_counter()
            photo_result = self._send_photo_bytes(
                chat_id,
                mosaic_bytes,
                filename="weekly-digest-%s.jpg" % week_start.isoformat(),
            )
            send_photo_seconds = time.perf_counter() - send_photo_started_at
        debug_info = None
        app_user = self.service.get_user_by_id(user_id)
        if self._response_debug_enabled() and app_user is not None and app_user.has_admin_access:
            debug_info = {
                "build_label": "weekly digest",
                "build_seconds": build_timings.get("build_digest_seconds", 0.0),
                "build_steps": self._build_weekly_digest_debug_steps(build_timings),
                "image_generation_seconds": image_generation_seconds,
                "send_photo_seconds": send_photo_seconds,
                "total_response_seconds": time.perf_counter() - started_at,
            }
        text_result = self._send_message(
            chat_id,
            self._format_weekly_digest_text(digest, preview=preview, debug_info=debug_info),
        )
        payload: Dict[str, object] = {
            "digest_type": "weekly",
            "week_start": digest.week_start.isoformat(),
            "week_end": digest.week_end.isoformat(),
        }
        if isinstance(photo_result, dict):
            payload["photo_message_id"] = photo_result.get("message_id", "")
        if isinstance(text_result, dict):
            payload["text_message_id"] = text_result.get("message_id", "")
        return payload

    @staticmethod
    def _format_daily_digest_text(
        digest: DailyFoodDigest,
        preview: bool = False,
        step_progress=None,
        debug_info: Optional[Dict[str, float]] = None,
    ) -> str:
        grouped_meals = TelegramHealthBot._group_daily_digest_meals(digest.meals)
        lines = [
            ("**Daily digest preview за %s**" % digest.digest_date.isoformat())
            if preview
            else ("**Сводка по еде за %s**" % digest.digest_date.isoformat()),
            "",
            "Блюд: %s"
            % TelegramHealthBot._format_count_with_noun(len(digest.meals), ("блюдо", "блюда", "блюд")),
            "Калории: %s" % TelegramHealthBot._format_integer_with_spaces(digest.total_calories),
            "Белок: %s г" % TelegramHealthBot._format_decimal(digest.total_protein_g),
            "Жиры: %s г" % TelegramHealthBot._format_decimal(digest.total_fat_g),
            "Углеводы: %s г" % TelegramHealthBot._format_decimal(digest.total_carbs_g),
            "Вода: %s л / %s л"
            % (
                TelegramHealthBot._format_liters(digest.water_ml),
                TelegramHealthBot._format_liters(digest.water_goal_ml),
            ),
            "",
            "Список блюд:",
        ]
        for label in ("утро", "день", "вечер"):
            lines.append(label)
            meals = grouped_meals[label]
            if meals:
                for meal in meals:
                    lines.append("- %s | %s | %s ккал" % (meal.occurred_at.strftime("%H:%M"), meal.title, meal.calories))
            else:
                lines.append("не было записей")
        if grouped_meals["ночь"]:
            lines.append("ночь")
            for meal in grouped_meals["ночь"]:
                lines.append("- %s | %s | %s ккал" % (meal.occurred_at.strftime("%H:%M"), meal.title, meal.calories))
        lines.append("")
        lines.append(digest.commentary)
        if step_progress is not None:
            lines.extend(
                [
                    "",
                    "Шаги за день: %s / %s" % (step_progress.steps, step_progress.target_steps),
                    (
                        "30-дневная средняя: %.1f" % step_progress.average_steps_30d
                        if step_progress.average_steps_30d is not None
                        else "30-дневная средняя: нет данных"
                    ),
                    "Комментарий по шагам: %s" % step_progress.comment,
                ]
            )
        if debug_info is not None:
            TelegramHealthBot._append_digest_debug_lines(lines, debug_info)
        return "\n".join(lines)

    @staticmethod
    def _group_daily_digest_meals(meals):
        groups = {
            "утро": [],
            "день": [],
            "вечер": [],
            "ночь": [],
        }
        for meal in meals:
            hour = meal.occurred_at.hour
            if 6 <= hour < 12:
                groups["утро"].append(meal)
            elif 12 <= hour < 16:
                groups["день"].append(meal)
            elif 16 <= hour < 21:
                groups["вечер"].append(meal)
            else:
                groups["ночь"].append(meal)
        return groups

    @staticmethod
    def _format_decimal(value: float) -> str:
        return ("%.1f" % value).rstrip("0").rstrip(".")

    @staticmethod
    def _format_integer_with_spaces(value: int) -> str:
        return f"{value:,}".replace(",", " ")

    @staticmethod
    def _format_count_with_noun(value: int, forms: tuple[str, str, str]) -> str:
        mod100 = value % 100
        mod10 = value % 10
        if 11 <= mod100 <= 14:
            noun = forms[2]
        elif mod10 == 1:
            noun = forms[0]
        elif 2 <= mod10 <= 4:
            noun = forms[1]
        else:
            noun = forms[2]
        return f"{value} {noun}"

    @staticmethod
    def _format_liters(amount_ml: int) -> str:
        return ("%.1f" % (amount_ml / 1000.0)).rstrip("0").rstrip(".")

    @staticmethod
    def _format_liters_fixed(amount_ml: int) -> str:
        return "%.1f" % (amount_ml / 1000.0)

    @staticmethod
    def _format_weekly_digest_text(
        digest: WeeklyFoodDigest,
        preview: bool = False,
        debug_info: Optional[Dict[str, float]] = None,
    ) -> str:
        lines = [
            ("Weekly digest preview за %s — %s" % (digest.week_start.isoformat(), digest.week_end.isoformat()))
            if preview
            else ("Недельная сводка по еде за %s — %s" % (digest.week_start.isoformat(), digest.week_end.isoformat())),
            "Блюд за неделю: %s" % digest.total_meals,
            "Калории за неделю: %s" % digest.total_calories,
            "Выделяющиеся блюда по дням:",
        ]
        for highlight in digest.highlights:
            if highlight.meal is None:
                lines.append("- %s | нет блюда" % highlight.digest_date.isoformat())
                continue
            lines.append(
                "- %s | %s | %s ккал | %s"
                % (
                    highlight.digest_date.isoformat(),
                    highlight.meal.title,
                    highlight.meal.calories,
                    highlight.reason,
                )
            )
        lines.append("")
        lines.append(digest.commentary)
        if debug_info is not None:
            TelegramHealthBot._append_digest_debug_lines(lines, debug_info)
        return "\n".join(lines)

    @staticmethod
    def _append_digest_debug_lines(lines: List[str], debug_info: Dict[str, object]) -> None:
        lines.extend(
            [
                "",
                "Отладка:",
                "Сбор %s: %.2f сек" % (debug_info["build_label"], debug_info["build_seconds"]),
            ]
        )
        for step_label, seconds in debug_info.get("build_steps", []):
            lines.append("  - %s: %.2f сек" % (step_label, seconds))
        lines.extend(
            [
                "Рендер изображения: %.2f сек" % debug_info["image_generation_seconds"],
                "Отправка фото в Telegram: %.2f сек" % debug_info["send_photo_seconds"],
                "Полный ответ до отправки текста: %.2f сек" % debug_info["total_response_seconds"],
            ]
        )

    @staticmethod
    def _build_daily_digest_debug_steps(build_timings: Dict[str, float], step_insight_seconds: float) -> List[tuple[str, float]]:
        return [
            ("история за 30 дней", build_timings.get("historical_cache_seconds", 0.0)),
            ("текущий день", build_timings.get("digest_day_cache_seconds", 0.0)),
            ("слияние cache", build_timings.get("cache_merge_seconds", 0.0)),
            ("загрузка изображений текущего дня", build_timings.get("media_hydration_seconds", 0.0)),
            ("daily summary", build_timings.get("daily_summary_seconds", 0.0)),
            ("trend windows 7/14/30", build_timings.get("trend_windows_seconds", 0.0)),
            ("построение commentary data", build_timings.get("commentary_data_seconds", 0.0)),
            ("построение текста", build_timings.get("commentary_text_seconds", 0.0)),
            ("построение step insight", step_insight_seconds),
        ]

    @staticmethod
    def _build_weekly_digest_debug_steps(build_timings: Dict[str, float]) -> List[tuple[str, float]]:
        return [
            ("baseline за 30 дней", build_timings.get("baseline_collection_seconds", 0.0)),
            ("загрузка недели с изображениями", build_timings.get("week_cache_seconds", 0.0)),
            ("слияние cache", build_timings.get("cache_merge_seconds", 0.0)),
            ("сбор блюд по 7 дням", build_timings.get("week_meals_collection_seconds", 0.0)),
            ("выбор highlight по дням", build_timings.get("highlight_selection_seconds", 0.0)),
            ("построение commentary data", build_timings.get("commentary_data_seconds", 0.0)),
            ("построение текста", build_timings.get("commentary_text_seconds", 0.0)),
        ]

    def _answer_callback_query(self, callback_query_id: str, text: str) -> None:
        self._telegram_api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_query_id,
                "text": text,
            },
        )

    def _try_answer_callback_query(self, callback_query_id: str, text: str) -> None:
        try:
            self._answer_callback_query(callback_query_id, text)
        except Exception as exc:  # pragma: no cover
            logger.warning("Callback answer failed callback_query_id=%s error=%s", callback_query_id, exc)

    def _handle_onboarding_callback(
        self,
        *,
        query_id: str,
        chat_id: int,
        message_id: Optional[int],
        app_user: AppUser,
        data: str,
    ) -> None:
        if data == "onboarding:skip":
            updated = self.service.complete_onboarding(app_user.user_id, now=self._local_now())
            self._try_answer_callback_query(query_id, "Онбординг можно открыть позже.")
            self._send_message(chat_id, self._home_text(updated), reply_markup=self._menu_reply_markup(updated))
            if isinstance(message_id, int):
                self._try_delete_message(chat_id, message_id)
            return
        if data == "onboarding:start":
            updated = self.service.complete_onboarding(app_user.user_id, now=self._local_now())
            self._try_answer_callback_query(query_id, "Поехали.")
            self._send_message(chat_id, self._home_text(updated), reply_markup=self._menu_reply_markup(updated))
            if isinstance(message_id, int):
                self._try_delete_message(chat_id, message_id)
            return
        if data.startswith("onboarding:next:"):
            try:
                step = int(data.rsplit(":", 1)[1])
            except ValueError:
                self._try_answer_callback_query(query_id, "Не удалось открыть следующий шаг.")
                return
            if step < 1 or step > self.ONBOARDING_STEP_COUNT:
                self._try_answer_callback_query(query_id, "Такого шага онбординга нет.")
                return
            self._try_answer_callback_query(query_id, "Дальше")
            self._send_onboarding_step(chat_id, step=step)
            if isinstance(message_id, int):
                self._try_delete_message(chat_id, message_id)
            return
        self._try_answer_callback_query(query_id, "Неизвестное действие.")

    def _handle_photo_message(self, chat_id: int, app_user: AppUser, photo: List[dict], caption: str) -> None:
        largest_photo = max(photo, key=lambda item: item.get("file_size", 0))
        file_id = largest_photo.get("file_id")
        file_unique_id = largest_photo.get("file_unique_id")
        if not isinstance(file_id, str) or not isinstance(file_unique_id, str):
            self._send_message(chat_id, "Метаданные фотографии неполные.")
            return

        try:
            file_lookup_started_at = time.perf_counter()
            file_info = self._telegram_api("getFile", {"file_id": file_id})
            self._record_response_debug_step("получение file_path", file_lookup_started_at)
            file_path = file_info.get("file_path")
            if not isinstance(file_path, str):
                raise ValueError("Telegram не вернул путь к файлу")
            download_started_at = time.perf_counter()
            image_bytes = self._download_telegram_file(file_path)
            self._record_response_debug_step("загрузка фото из Telegram", download_started_at)
            processing_timings: Dict[str, float] = {}
            process_result = self.service.create_photo_log_from_photo(
                app_user.user_id,
                photo_file_id=file_id,
                photo_unique_id=file_unique_id,
                image_bytes=image_bytes,
                mime_type=self._guess_mime_type(file_path),
                occurred_at=self._local_now(),
                caption=caption,
                debug_timings=processing_timings,
            )
            if "photo analysis" in processing_timings:
                self._add_response_debug_step("распознавание фото", processing_timings["photo analysis"])
            if "store draft and media" in processing_timings:
                self._add_response_debug_step("подготовка черновика", processing_timings["store draft and media"])
            if "direct save transaction" in processing_timings:
                self._add_response_debug_step("автосохранение записи", processing_timings["direct save transaction"])
        except Exception as exc:
            logger.exception("Food photo analysis failed chat_id=%s file_id=%s error=%s", chat_id, file_id, exc)
            self._send_message(chat_id, "Не удалось распознать фото еды: %s" % exc)
            return

        if process_result.photo_log is not None and process_result.photo_log.kind == PhotoLogKind.MEAL:
            saved_meal = process_result.photo_log.meal_entry or self.service.get_meal_entry(
                app_user.user_id,
                process_result.photo_log.entry_id,
            )
            logger.info("Food photo auto-saved successfully chat_id=%s entry_id=%s", chat_id, saved_meal.entry_id)
            card_started_at = time.perf_counter()
            saved_text = self._format_saved_meal_text(app_user, saved_meal)
            self._record_response_debug_step("сборка карточки сохраненной записи", card_started_at)
            self._telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": self._append_response_debug_text(saved_text),
                    "reply_markup": self._saved_meal_reply_markup(saved_meal.entry_id),
                },
            )
            return

        draft = process_result.draft
        if draft is None:
            raise RuntimeError("Photo processing returned no draft or saved entry")
        logger.info("Food photo analyzed successfully chat_id=%s draft_id=%s", chat_id, draft.draft_id)
        reply_started_at = time.perf_counter()
        self._send_meal_draft(chat_id, draft, app_user=app_user)
        self._record_response_debug_step("подготовка ответа по фото", reply_started_at)

    def _handle_document_message(self, chat_id: int, app_user: AppUser, document: Dict[str, object]) -> None:
        if not app_user.has_admin_access:
            self._send_message(chat_id, "Загрузка CSV Т-Банка доступна только администратору.")
            return
        file_id = document.get("file_id")
        file_name = document.get("file_name")
        mime_type = document.get("mime_type")
        if not isinstance(file_id, str):
            self._send_message(chat_id, "Не удалось получить файл из сообщения.")
            return
        if not self._is_supported_tbank_file(file_name=file_name, mime_type=mime_type):
            self._send_message(
                chat_id,
                "Пока поддерживаю только CSV-файлы с операциями Т-Банка. Выгрузи операции в CSV и отправь файл сюда.",
            )
            return

        try:
            file_lookup_started_at = time.perf_counter()
            file_info = self._telegram_api("getFile", {"file_id": file_id})
            self._record_response_debug_step("получение файла документа", file_lookup_started_at)
            file_path = file_info.get("file_path")
            if not isinstance(file_path, str):
                raise ValueError("Telegram не вернул путь к файлу")
            download_started_at = time.perf_counter()
            file_bytes = self._download_telegram_file(file_path)
            self._record_response_debug_step("загрузка документа", download_started_at)
            import_started_at = time.perf_counter()
            result = self.service.import_tbank_csv(
                app_user.user_id,
                file_bytes=file_bytes,
                source_file_name=file_name if isinstance(file_name, str) else "tbank.csv",
            )
            self._record_response_debug_step("импорт CSV", import_started_at)
        except Exception as exc:
            logger.exception("T-Bank import failed chat_id=%s file_id=%s error=%s", chat_id, file_id, exc)
            self._send_message(chat_id, "Не удалось импортировать файл Т-Банка: %s" % exc)
            return

        self._send_message(chat_id, self._format_tbank_import_result(result))

    def _ensure_polling_mode(self) -> None:
        try:
            self._telegram_api(
                "deleteWebhook",
                {
                    "drop_pending_updates": "false",
                },
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Webhook cleanup failed: %s", exc)

    def _sync_bot_commands(self) -> None:
        commands = [
            {"command": "start", "description": "Подключить и открыть бота"},
            {"command": "menu", "description": "Показать кнопки и список команд"},
            {"command": "summary", "description": "Сводка за сегодня"},
            {"command": "finance_month", "description": "Финансовая сводка за месяц"},
            {"command": "connect_drive", "description": "Подключить папку Google Drive"},
            {"command": "drive_status", "description": "Статус импорта Google Drive"},
            {"command": "drive_on", "description": "Включить импорт Google Drive"},
            {"command": "drive_off", "description": "Выключить импорт Google Drive"},
            {"command": "decisions", "description": "Открытые решения"},
            {"command": "digest_status", "description": "Статус ежедневных и недельных digest"},
            {"command": "digest_on", "description": "Включить ежедневные и недельные digest"},
            {"command": "digest_off", "description": "Выключить ежедневные и недельные digest"},
            {"command": "digest_preview", "description": "Предпросмотр daily digest"},
            {"command": "weekly_digest_preview", "description": "Предпросмотр weekly digest"},
            {"command": "import_tbank", "description": "Импорт CSV из Т-Банка"},
            {"command": "drafts", "description": "Черновики приема пищи"},
            {"command": "user_mode", "description": "Режим обычного пользователя (admin)"},
            {"command": "admin_mode", "description": "Вернуть режим администратора (admin)"},
            {"command": "whoami", "description": "Мои Telegram ID"},
            {"command": "help", "description": "Справка по командам"},
        ]
        try:
            payload = {
                "commands": json.dumps(commands, ensure_ascii=False),
            }
            self._telegram_api("setMyCommands", payload)
            self._telegram_api("setMyCommands", {**payload, "language_code": "ru"})
        except Exception as exc:  # pragma: no cover
            logger.warning("Bot command sync failed: %s", exc)

    def _sync_mini_app_menu_button(self, chat_id: Optional[int] = None, app_user: Optional[AppUser] = None) -> None:
        if not self.settings.mini_app_url:
            return
        menu_button = {
            "type": "web_app",
            "text": "Открыть",
            "web_app": {"url": self.settings.mini_app_url},
        }
        params = {
            "menu_button": json.dumps(menu_button, ensure_ascii=False),
        }
        if chat_id is not None:
            params["chat_id"] = chat_id
        try:
            self._telegram_api("setChatMenuButton", params)
        except Exception as exc:  # pragma: no cover
            logger.warning("Mini App menu button sync failed: %s", exc)

    def _mini_app_help_line(self, app_user: AppUser) -> str:
        if not self.settings.mini_app_url:
            return "Mini App: пока не настроен в этом окружении."
        return "Mini App: откройте через кнопку меню «Открыть»."

    @classmethod
    def _normalize_command_text(cls, text: str) -> str:
        return cls.BUTTON_TO_COMMAND.get(text, text)

    def _reply_markup_for_response(
        self,
        text: str,
        original_app_user: Optional[AppUser],
        reply_user: Optional[AppUser],
    ) -> Optional[str]:
        if text == "/start" and reply_user is not None:
            return None
        if reply_user is None:
            return None
        if text in {"/add_water", "/water_custom"}:
            return self._water_prompt_reply_markup()
        if text.startswith("/water "):
            return self._water_result_reply_markup()
        if text == "/history":
            return self._history_reply_markup()
        if text == "/history_app":
            return self._history_app_inline_reply_markup()
        if text == "/history_fix_last":
            return self._history_fix_reply_markup(reply_user)
        if text == "/history_delete_last":
            return self._history_delete_reply_markup(reply_user)
        if text == "/profile":
            return self._profile_home_reply_markup()
        if text == "/profile_about":
            return self._profile_about_reply_markup(reply_user)
        if text == "/profile_goals":
            return self._profile_goals_reply_markup()
        if text == "/profile_reminders":
            return self._profile_reminders_reply_markup(reply_user)
        if text in {
            "/start",
            "/help",
            "/menu",
            "/user_mode",
            "/admin_mode",
            "/add_food",
            "/history",
            "/progress",
            "/how_it_works",
        }:
            return self._menu_reply_markup(reply_user)
        return None

    @staticmethod
    def _should_reload_reply_user(text: str) -> bool:
        return text in {"/start", "/user_mode", "/admin_mode"}

    @classmethod
    def _welcome_reply_markup(cls) -> str:
        return json.dumps(
            {
                "resize_keyboard": True,
                "keyboard": [
                    [{"text": "Добавить еду"}],
                    [{"text": "Добавить воду"}],
                    [{"text": "Как это работает"}],
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def _water_prompt_reply_markup(cls) -> str:
        return json.dumps(
            {
                "resize_keyboard": True,
                "keyboard": [
                    [{"text": "+250 мл"}, {"text": "+500 мл"}, {"text": "+750 мл"}],
                    [{"text": "Свой объем"}],
                    [{"text": "Назад"}],
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def _water_result_reply_markup(cls) -> str:
        return json.dumps(
            {
                "resize_keyboard": True,
                "keyboard": [
                    [{"text": "Добавить еще"}, {"text": "Прогресс"}],
                    [{"text": "Добавить еду"}],
                    [{"text": "Назад"}],
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def _history_reply_markup(cls) -> str:
        return json.dumps(
            {
                "resize_keyboard": True,
                "keyboard": [
                    [{"text": "Исправить последнюю запись"}],
                    [{"text": "Отменить последнюю запись"}],
                    [{"text": "История и правки в приложении"}],
                    [{"text": "Назад"}],
                ],
            },
            ensure_ascii=False,
        )

    def _history_fix_reply_markup(self, app_user: AppUser) -> Optional[str]:
        pending_draft = self._get_latest_recoverable_pending_draft(app_user)
        if pending_draft is not None:
            return self._meal_draft_card_reply_markup(pending_draft)
        meal = self._get_latest_recoverable_meal(app_user)
        if meal is not None:
            return self._last_meal_recovery_reply_markup(meal)
        return self._history_app_inline_reply_markup()

    def _history_delete_reply_markup(self, app_user: AppUser) -> Optional[str]:
        meal = self._get_latest_deletable_meal(app_user)
        if meal is not None:
            return self._history_delete_prompt_reply_markup()
        return self._history_app_inline_reply_markup()

    @classmethod
    def _history_delete_prompt_reply_markup(cls) -> str:
        return json.dumps(
            {
                "resize_keyboard": True,
                "keyboard": [
                    [{"text": "Да, удалить"}, {"text": "Отмена"}],
                ],
            },
            ensure_ascii=False,
        )

    def _history_app_inline_reply_markup(self) -> Optional[str]:
        if not self.settings.mini_app_url:
            return None
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Открыть", "web_app": {"url": self.settings.mini_app_url}},
                    ]
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _profile_home_reply_markup() -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [{"text": "Цели", "callback_data": "profile_goals"}],
                    [{"text": "Напоминания", "callback_data": "profile_reminders"}],
                    [{"text": "Обо мне", "callback_data": "profile_about"}],
                    [{"text": "Назад", "callback_data": "profile_back_to_menu"}],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _profile_about_reply_markup(app_user: AppUser) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Мужчина" + (" ✓" if app_user.sex == UserSex.MALE else ""), "callback_data": "profile_about_sex:male"},
                        {"text": "Женщина" + (" ✓" if app_user.sex == UserSex.FEMALE else ""), "callback_data": "profile_about_sex:female"},
                    ],
                    [
                        {"text": "Возраст", "callback_data": "profile_about_edit:age"},
                        {"text": "Рост", "callback_data": "profile_about_edit:height"},
                    ],
                    [
                        {"text": "Вес", "callback_data": "profile_about_edit:weight"},
                    ],
                    [
                        {"text": "Поддержание" + (" ✓" if app_user.goal == UserGoal.MAINTENANCE else ""), "callback_data": "profile_about_goal:maintenance"},
                    ],
                    [
                        {"text": "Похудение" + (" ✓" if app_user.goal == UserGoal.WEIGHT_LOSS else ""), "callback_data": "profile_about_goal:weight_loss"},
                        {"text": "Набор массы" + (" ✓" if app_user.goal == UserGoal.MASS_GAIN else ""), "callback_data": "profile_about_goal:mass_gain"},
                    ],
                    [{"text": "Назад", "callback_data": "profile_home"}],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _profile_goals_reply_markup() -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Вода", "callback_data": "profile_goal_edit:water_goal"},
                        {"text": "Белок", "callback_data": "profile_goal_edit:protein_goal"},
                    ],
                    [
                        {"text": "Калории", "callback_data": "profile_goal_edit:calorie_goal"},
                    ],
                    [
                        {"text": "Сбросить к рекомендованным", "callback_data": "profile_goals_reset"},
                    ],
                    [{"text": "Назад", "callback_data": "profile_home"}],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _profile_reminders_reply_markup(app_user: AppUser) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {
                            "text": ("Выключить все" if app_user.reminders_enabled else "Включить напоминания"),
                            "callback_data": "profile_reminders_toggle:enabled",
                        }
                    ],
                    [
                        {"text": "Записать еду" + (" ✓" if app_user.reminder_meal_logging else ""), "callback_data": "profile_reminders_toggle:meal"},
                    ],
                    [
                        {"text": "Напомнить про воду" + (" ✓" if app_user.reminder_water else ""), "callback_data": "profile_reminders_toggle:water"},
                    ],
                    [
                        {"text": "Вечерний итог дня" + (" ✓" if app_user.reminder_evening_summary else ""), "callback_data": "profile_reminders_toggle:evening"},
                    ],
                    [{"text": "Назад", "callback_data": "profile_home"}],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _profile_prompt_reply_markup(back_callback: str) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [{"text": "Назад", "callback_data": back_callback}],
                ]
            },
            ensure_ascii=False,
        )

    @classmethod
    def _menu_reply_markup(cls, app_user: AppUser) -> str:
        keyboard = [
            [{"text": "Добавить еду"}, {"text": "Добавить воду"}],
            [{"text": "История"}, {"text": "Прогресс"}],
            [{"text": "Профиль"}, {"text": "Как это работает"}],
            [{"text": "Помощь"}],
        ]
        if app_user.has_admin_access:
            keyboard = [
                [{"text": "Добавить еду"}, {"text": "Добавить воду"}],
                [{"text": "История"}, {"text": "Прогресс"}],
                [{"text": "Профиль"}, {"text": "Как это работает"}],
                [{"text": "Помощь"}],
                [{"text": "Финансы за месяц"}, {"text": "Google Drive"}],
                [{"text": "Импорт Т-Банк"}, {"text": "Открытые решения"}],
            ]
        return json.dumps(
            {
                "resize_keyboard": True,
                "keyboard": keyboard,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _is_private_chat(chat: Dict[str, object]) -> bool:
        return chat.get("type") == "private" or "type" not in chat

    @staticmethod
    def _telegram_string(value: object) -> str:
        return str(value).strip() if isinstance(value, str) else ""

    @staticmethod
    def _is_supported_tbank_file(file_name: object, mime_type: object) -> bool:
        normalized_name = str(file_name).lower() if isinstance(file_name, str) else ""
        normalized_type = str(mime_type).lower() if isinstance(mime_type, str) else ""
        return (
            normalized_name.endswith(".csv")
            or normalized_type in {"text/csv", "application/csv", "application/vnd.ms-excel"}
        )

    @staticmethod
    def _format_tbank_import_result(result) -> str:
        lines = [
            "Импорт операций Т-Банка завершен",
            "Файл: %s" % result.source_file_name,
            "Операций в файле: %s" % result.total_rows,
            "Импортировано новых: %s" % result.imported_rows,
            "Пропущено дублей: %s" % result.skipped_rows,
        ]
        if result.first_operation_at and result.last_operation_at:
            lines.append(
                "Период операций: %s — %s"
                % (
                    result.first_operation_at.strftime("%d.%m.%Y %H:%M"),
                    result.last_operation_at.strftime("%d.%m.%Y %H:%M"),
                )
            )
        return "\n".join(lines)

    def _registration_required_text(self) -> str:
        return (
            "Бот работает только в личных сообщениях.\n"
            "Отправьте команду /start, чтобы начать работу."
        )

    def _handle_pending_draft_edit_input(
        self,
        app_user: Optional[AppUser],
        raw_text: str,
        normalized_text: str,
    ) -> Optional[tuple[str, str]]:
        if app_user is None:
            return None
        state = self._pending_draft_edit_states.get(app_user.user_id)
        if state is None:
            return None
        if normalized_text in {
            "/menu",
            "/help",
            "/start",
            "/add_food",
            "/add_water",
            "/history",
            "/history_fix_last",
            "/history_delete_last",
            "/history_app",
            "/progress",
            "/profile",
            "/how_it_works",
        }:
            self._clear_pending_draft_edit_state(app_user.user_id)
            return None
        draft_id = state["draft_id"]
        field = state["field"]
        target_type = state.get("target_type", "draft")
        try:
            if target_type == "meal" and field == "title":
                draft = self.service.update_meal_entry(app_user.user_id, draft_id, title=raw_text.strip())
            elif target_type == "meal" and field == "portion":
                factor = self._parse_portion_factor(raw_text)
                draft = self.service.scale_meal_entry_portion(app_user.user_id, draft_id, factor)
            elif target_type == "meal" and field == "summary":
                draft = self.service.update_meal_entry(app_user.user_id, draft_id, summary=raw_text.strip())
            elif target_type == "meal" and field == "time":
                current_meal = self.service.get_meal_entry(app_user.user_id, draft_id)
                draft = self.service.update_meal_entry(
                    app_user.user_id,
                    draft_id,
                    occurred_at=self._parse_meal_time(raw_text, current_meal.occurred_at),
                )
            elif target_type == "meal" and field == "macros":
                calories, protein_g, fat_g, carbs_g = self._parse_draft_macros(raw_text)
                draft = self.service.update_meal_entry(
                    app_user.user_id,
                    draft_id,
                    calories=calories,
                    protein_g=protein_g,
                    fat_g=fat_g,
                    carbs_g=carbs_g,
                )
            elif field == "title":
                draft = self.service.update_meal_draft(app_user.user_id, draft_id, title=raw_text.strip())
            elif field == "portion":
                factor = self._parse_portion_factor(raw_text)
                draft = self.service.scale_meal_draft_portion(app_user.user_id, draft_id, factor)
            elif field == "summary":
                draft = self.service.update_meal_draft(app_user.user_id, draft_id, summary=raw_text.strip())
            elif field == "rewrite":
                cleaned = raw_text.strip()
                draft = self.service.update_meal_draft(app_user.user_id, draft_id, title=cleaned, summary=cleaned)
            elif field == "time":
                draft = self.service.update_meal_draft(
                    app_user.user_id,
                    draft_id,
                    occurred_at=self._parse_draft_time(raw_text, self.service.get_meal_draft(app_user.user_id, draft_id)),
                )
            elif field == "macros":
                calories, protein_g, fat_g, carbs_g = self._parse_draft_macros(raw_text)
                draft = self.service.update_meal_draft(
                    app_user.user_id,
                    draft_id,
                    calories=calories,
                    protein_g=protein_g,
                    fat_g=fat_g,
                    carbs_g=carbs_g,
                )
            else:
                return None
        except ValueError as exc:
            reply_markup = (
                self._meal_entry_edit_prompt_reply_markup(draft_id)
                if target_type == "meal"
                else self._draft_edit_prompt_reply_markup(draft_id)
            )
            return str(exc), reply_markup
        self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
        if target_type == "meal":
            return self._format_last_meal_recovery_text(draft), self._last_meal_recovery_reply_markup(draft)
        return self._format_meal_draft_card_text(draft), self._meal_draft_card_reply_markup(draft)

    def _set_pending_draft_edit_state(self, user_id: int, draft_id: str, field: str, target_type: str = "draft") -> None:
        self._pending_draft_edit_states[user_id] = {"draft_id": draft_id, "field": field, "target_type": target_type}

    def _clear_pending_draft_edit_state(self, user_id: int, draft_id: Optional[str] = None) -> None:
        state = self._pending_draft_edit_states.get(user_id)
        if state is None:
            return
        if draft_id is not None and state.get("draft_id") != draft_id:
            return
        self._pending_draft_edit_states.pop(user_id, None)

    def _set_pending_draft_clarification(
        self,
        user_id: int,
        draft_id: str,
        kind: str,
        options: Optional[List[str]] = None,
    ) -> None:
        state: Dict[str, object] = {"draft_id": draft_id, "kind": kind}
        if options is not None:
            state["options"] = options
        self._pending_draft_clarifications[user_id] = state

    def _clear_pending_draft_clarification(self, user_id: int, draft_id: Optional[str] = None) -> None:
        state = self._pending_draft_clarifications.get(user_id)
        if state is None:
            return
        if draft_id is not None and state.get("draft_id") != draft_id:
            return
        self._pending_draft_clarifications.pop(user_id, None)

    @staticmethod
    def _build_title_clarification_options(draft: MealPhotoDraft) -> List[str]:
        options: List[str] = []
        for title in [draft.title, *(item.title for item in draft.items)]:
            normalized = title.strip()
            if not normalized:
                continue
            if normalized in options:
                continue
            options.append(normalized)
        return options[:3]

    def _build_draft_clarification(self, draft: MealPhotoDraft) -> Optional[Dict[str, object]]:
        if draft.is_water_only or draft.confidence >= 0.85:
            return None
        if draft.confidence >= 0.65:
            return {
                "kind": "portion",
                "text": (
                    "Хочу быстро уточнить порцию, чтобы запись была точнее.\n"
                    "Какой размер порции больше похож на реальный?"
                ),
                "reply_markup": self._meal_draft_portion_clarification_reply_markup(draft.draft_id),
            }
        options = self._build_title_clarification_options(draft)
        if len(options) < 2:
            return {
                "kind": "portion",
                "text": (
                    "Не до конца уверен в распознавании.\n"
                    "Давайте быстро уточним порцию, а если нужно — потом поправите вручную."
                ),
                "reply_markup": self._meal_draft_portion_clarification_reply_markup(draft.draft_id),
            }
        return {
            "kind": "title",
            "text": "Не до конца уверен в блюде. Что ближе всего к фото?",
            "reply_markup": self._meal_draft_title_clarification_reply_markup(draft.draft_id, options),
            "options": options,
        }

    @staticmethod
    def _is_draft_determined(draft: MealPhotoDraft) -> bool:
        if not draft.title.strip():
            return False
        if draft.summary.strip():
            return True
        if draft.items:
            return True
        return draft.calories > 0 or draft.protein_g > 0 or draft.fat_g > 0 or draft.carbs_g > 0

    def _should_auto_save_draft(self, draft: MealPhotoDraft) -> bool:
        return draft.confidence > 0.6 and self._is_draft_determined(draft)

    def _set_pending_last_meal_delete(self, user_id: int, entry_id: str) -> None:
        self._pending_last_meal_delete_by_user[user_id] = entry_id

    def _clear_pending_last_meal_delete(self, user_id: int, entry_id: Optional[str] = None) -> None:
        current_entry_id = self._pending_last_meal_delete_by_user.get(user_id)
        if current_entry_id is None:
            return
        if entry_id is not None and current_entry_id != entry_id:
            return
        self._pending_last_meal_delete_by_user.pop(user_id, None)

    def _handle_pending_last_meal_delete_input(
        self,
        app_user: Optional[AppUser],
        raw_text: str,
        normalized_text: str,
    ) -> Optional[tuple[str, str]]:
        if app_user is None:
            return None
        entry_id = self._pending_last_meal_delete_by_user.get(app_user.user_id)
        if entry_id is None:
            return None
        if normalized_text in {
            "/menu",
            "/help",
            "/start",
            "/add_food",
            "/add_water",
            "/history",
            "/history_fix_last",
            "/history_delete_last",
            "/history_app",
            "/progress",
            "/profile",
            "/how_it_works",
        }:
            self._clear_pending_last_meal_delete(app_user.user_id)
            return None
        if raw_text == "Отмена":
            self._clear_pending_last_meal_delete(app_user.user_id, entry_id=entry_id)
            return "Удаление отменено.", self._history_reply_markup()
        if raw_text != "Да, удалить":
            return (
                "Подтвердите удаление кнопкой «Да, удалить» или нажмите «Отмена».",
                self._history_delete_prompt_reply_markup(),
            )
        try:
            meal = self.service.get_meal_entry(app_user.user_id, entry_id)
        except ValueError:
            self._clear_pending_last_meal_delete(app_user.user_id, entry_id=entry_id)
            return self._history_delete_in_app_text(), self._history_reply_markup()
        if not self._is_meal_recoverable_for_delete(meal):
            self._clear_pending_last_meal_delete(app_user.user_id, entry_id=entry_id)
            return self._history_delete_in_app_text(), self._history_reply_markup()
        deleted = self.service.delete_meal_entry(app_user.user_id, entry_id)
        self._clear_pending_last_meal_delete(app_user.user_id, entry_id=entry_id)
        return (
            "Последняя запись удалена.\n"
            "Удалено: %s.\n\n"
            "Если нужна история и более глубокие правки, откройте приложение."
        ) % deleted.title, self._history_reply_markup()

    def _handle_pending_profile_input(
        self,
        app_user: Optional[AppUser],
        raw_text: str,
        normalized_text: str,
    ) -> Optional[tuple[str, str]]:
        if app_user is None:
            return None
        state = self._pending_profile_edit_states.get(app_user.user_id)
        if state is None:
            return None
        if normalized_text in {
            "/menu",
            "/help",
            "/start",
            "/add_food",
            "/add_water",
            "/history",
            "/history_fix_last",
            "/history_delete_last",
            "/history_app",
            "/progress",
            "/profile",
            "/profile_about",
            "/profile_goals",
            "/profile_reminders",
            "/how_it_works",
        }:
            self._clear_pending_profile_edit_state(app_user.user_id)
            return None
        field = state["field"]
        try:
            if field == "age":
                updated = self.service.update_user_about(app_user.user_id, age_years=self._parse_age_years(raw_text))
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_about_text(updated), self._profile_about_reply_markup(updated)
            if field == "height":
                updated = self.service.update_user_about(app_user.user_id, height_cm=self._parse_height_cm(raw_text))
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_about_text(updated), self._profile_about_reply_markup(updated)
            if field == "weight":
                updated = self.service.update_user_about(
                    app_user.user_id,
                    profile_weight_kg=self._parse_weight_kg(raw_text),
                )
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_about_text(updated), self._profile_about_reply_markup(updated)
            if field == "water_goal":
                updated = self.service.update_user_goal_settings(
                    app_user.user_id,
                    target_water_ml=self._parse_water_goal_ml(raw_text),
                )
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_goals_text(updated), self._profile_goals_reply_markup()
            if field == "protein_goal":
                updated = self.service.update_user_goal_settings(
                    app_user.user_id,
                    target_protein_g=self._parse_protein_goal_g(raw_text),
                )
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_goals_text(updated), self._profile_goals_reply_markup()
            if field == "calorie_goal":
                calories_min, calories_max = self._parse_calorie_goal(raw_text)
                updated = self.service.update_user_goal_settings(
                    app_user.user_id,
                    target_calories_min=calories_min,
                    target_calories_max=calories_max,
                )
                self._clear_pending_profile_edit_state(app_user.user_id)
                return self._profile_goals_text(updated), self._profile_goals_reply_markup()
        except ValueError as exc:
            back_callback = "profile_about" if field in {"age", "height", "weight"} else "profile_goals"
            return str(exc), self._profile_prompt_reply_markup(back_callback)
        return None

    def _set_pending_profile_edit_state(self, user_id: int, field: str) -> None:
        self._pending_profile_edit_states[user_id] = {"field": field}

    def _clear_pending_profile_edit_state(self, user_id: int) -> None:
        self._pending_profile_edit_states.pop(user_id, None)

    def _toggle_profile_reminder(self, app_user: AppUser, toggle_key: str) -> AppUser:
        if toggle_key == "enabled":
            return self.service.update_user_reminders(
                app_user.user_id,
                reminders_enabled=not app_user.reminders_enabled,
            )
        if toggle_key == "meal":
            return self.service.update_user_reminders(
                app_user.user_id,
                reminders_enabled=True,
                reminder_meal_logging=not app_user.reminder_meal_logging,
            )
        if toggle_key == "water":
            return self.service.update_user_reminders(
                app_user.user_id,
                reminders_enabled=True,
                reminder_water=not app_user.reminder_water,
            )
        if toggle_key == "evening":
            return self.service.update_user_reminders(
                app_user.user_id,
                reminders_enabled=True,
                reminder_evening_summary=not app_user.reminder_evening_summary,
            )
        raise ValueError("Неизвестный переключатель напоминаний.")

    @staticmethod
    def _format_meal_draft_card_text(draft: MealPhotoDraft) -> str:
        return (
            "Похоже, это %s.\n\n"
            "%s ккал\n"
            "Б %s г • Ж %s г • У %s г\n"
            "Время: %s\n"
            "Состав: %s"
        ) % (
            draft.title,
            draft.calories,
            TelegramHealthBot._format_decimal(draft.protein_g),
            TelegramHealthBot._format_decimal(draft.fat_g),
            TelegramHealthBot._format_decimal(draft.carbs_g),
            draft.occurred_at.strftime("%H:%M"),
            draft.summary,
        )

    @staticmethod
    def _format_low_confidence_draft_text(draft: MealPhotoDraft) -> str:
        prefix = (
            "Я не до конца уверен, что правильно распознал блюдо.\n"
            "Проверьте запись перед сохранением.\n\n"
            if not TelegramHealthBot._is_draft_determined(draft)
            else (
                "Я не до конца уверен в распознавании.\n"
                "Проверьте запись перед сохранением.\n\n"
            )
        )
        return prefix + TelegramHealthBot._format_meal_draft_card_text(draft)

    @staticmethod
    def _format_auto_saved_meal_text(meal: MealEntry, trailing_note: Optional[str] = None) -> str:
        summary = TelegramHealthBot._extract_meal_summary_from_notes(meal)
        lines = [
            "Сохранено: %s." % meal.title,
            "",
            "%s ккал" % TelegramHealthBot._format_integer_with_spaces(meal.calories),
            "Б %s г • Ж %s г • У %s г"
            % (
                TelegramHealthBot._format_decimal(meal.protein_g),
                TelegramHealthBot._format_decimal(meal.fat_g),
                TelegramHealthBot._format_decimal(meal.carbs_g),
            ),
            "Время: %s" % meal.occurred_at.strftime("%H:%M"),
        ]
        if summary:
            lines.append("Состав: %s" % summary)
        lines.append("")
        lines.append(trailing_note or "Если нужно, запись можно быстро изменить или отменить.")
        return "\n".join(lines)

    @staticmethod
    def _format_meal_draft_edit_menu_text(draft: MealPhotoDraft) -> str:
        return "Что изменить в черновике «%s»?" % draft.title

    @staticmethod
    def _format_meal_draft_portion_text(draft: MealPhotoDraft) -> str:
        return (
            "Какую порцию поставить?\n"
            "Сейчас: %s ккал, Б %s г • Ж %s г • У %s г"
        ) % (
            draft.calories,
            TelegramHealthBot._format_decimal(draft.protein_g),
            TelegramHealthBot._format_decimal(draft.fat_g),
            TelegramHealthBot._format_decimal(draft.carbs_g),
        )

    @staticmethod
    def _format_meal_entry_portion_text(meal: MealEntry) -> str:
        return (
            "Какую порцию поставить для сохранённой записи?\n"
            "Сейчас: %s ккал, Б %s г • Ж %s г • У %s г"
        ) % (
            TelegramHealthBot._format_integer_with_spaces(meal.calories),
            TelegramHealthBot._format_decimal(meal.protein_g),
            TelegramHealthBot._format_decimal(meal.fat_g),
            TelegramHealthBot._format_decimal(meal.carbs_g),
        )

    @staticmethod
    def _portion_prompt_text() -> str:
        return (
            "Введите коэффициент порции.\n"
            "Например: 0.5, 1, 1.25 или 125%."
        )

    @staticmethod
    def _meal_draft_card_reply_markup(draft: MealPhotoDraft) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Сохранить", "callback_data": "meal_confirm:%s" % draft.draft_id},
                        {"text": "Изменить", "callback_data": "meal_edit_menu:%s" % draft.draft_id},
                    ],
                    [
                        {"text": "Отмена", "callback_data": "meal_reject:%s" % draft.draft_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_draft_edit_menu_reply_markup(draft: MealPhotoDraft) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Название", "callback_data": "meal_edit_title:%s" % draft.draft_id},
                        {"text": "Порция", "callback_data": "meal_edit_portion_menu:%s" % draft.draft_id},
                    ],
                    [
                        {"text": "Время", "callback_data": "meal_edit_time:%s" % draft.draft_id},
                        {"text": "Состав", "callback_data": "meal_edit_summary:%s" % draft.draft_id},
                    ],
                    [
                        {"text": "Калории и БЖУ", "callback_data": "meal_edit_macros:%s" % draft.draft_id},
                    ],
                    [
                        {"text": "Назад к черновику", "callback_data": "meal_edit_back:%s" % draft.draft_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_draft_portion_reply_markup(draft: MealPhotoDraft) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Меньше", "callback_data": "meal_edit_portion:%s:smaller" % draft.draft_id},
                        {"text": "Стандарт", "callback_data": "meal_edit_portion:%s:standard" % draft.draft_id},
                        {"text": "Больше", "callback_data": "meal_edit_portion:%s:bigger" % draft.draft_id},
                    ],
                    [
                        {"text": "Своя порция", "callback_data": "meal_edit_portion_custom:%s" % draft.draft_id},
                    ],
                    [
                        {"text": "Назад к черновику", "callback_data": "meal_edit_back:%s" % draft.draft_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_draft_portion_clarification_reply_markup(draft_id: str) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Маленькая", "callback_data": "meal_clarify_portion:%s:small" % draft_id},
                        {"text": "Средняя", "callback_data": "meal_clarify_portion:%s:medium" % draft_id},
                        {"text": "Большая", "callback_data": "meal_clarify_portion:%s:large" % draft_id},
                    ],
                    [
                        {"text": "Изменить вручную", "callback_data": "meal_clarify_portion:%s:manual" % draft_id},
                    ],
                    [
                        {"text": "Пропустить", "callback_data": "meal_clarify_portion:%s:skip" % draft_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_draft_title_clarification_reply_markup(draft_id: str, options: List[str]) -> str:
        keyboard = [
            [{"text": title, "callback_data": "meal_clarify_title:%s:%s" % (draft_id, index)}]
            for index, title in enumerate(options)
        ]
        keyboard.append(
            [{"text": "Изменить вручную", "callback_data": "meal_clarify_title:%s:manual" % draft_id}]
        )
        keyboard.append(
            [{"text": "Пропустить", "callback_data": "meal_clarify_title:%s:skip" % draft_id}]
        )
        return json.dumps({"inline_keyboard": keyboard}, ensure_ascii=False)

    @staticmethod
    def _draft_edit_prompt_reply_markup(draft_id: str) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [{"text": "Назад к черновику", "callback_data": "meal_edit_back:%s" % draft_id}],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _draft_time_prompt_reply_markup(draft_id: str) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Сейчас", "callback_data": "meal_edit_time_now:%s" % draft_id},
                        {"text": "Назад к черновику", "callback_data": "meal_edit_back:%s" % draft_id},
                    ]
                ]
            },
            ensure_ascii=False,
        )

    def _format_saved_meal_text(self, app_user: AppUser, meal: MealEntry) -> str:
        summary = self.service.get_post_save_coaching_snapshot(app_user.user_id, meal.occurred_at.date())
        coaching = self._build_optional_post_save_coaching(app_user, summary)
        trailing_note = coaching or "Если нужно, запись можно быстро изменить или отменить."
        return self._format_auto_saved_meal_text(meal, trailing_note=trailing_note)

    @staticmethod
    def _build_optional_post_save_coaching(app_user: AppUser, summary: PostSaveCoachingSnapshot) -> str:
        if not app_user.reminders_enabled:
            return ""
        if app_user.reminder_meal_logging:
            if summary.meals_count == 1:
                return "Хорошее начало: первая запись за день уже сохранена."
            if summary.meals_count >= 3:
                return "Хороший ритм: сегодня вы уже регулярно записываете еду."
        water_goal_ml = summary.goals.water_ml
        if app_user.reminder_water and water_goal_ml > 0 and summary.water_ml < (water_goal_ml / 2):
            return "Если был напиток, воду можно добавить отдельно одним тапом."
        return ""

    @staticmethod
    def _saved_meal_reply_markup(entry_id: str) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Изменить", "callback_data": "history_last_meal_edit:%s" % entry_id},
                        {"text": "Отмена", "callback_data": "meal_saved_cancel:%s" % entry_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _last_meal_recovery_reply_markup(meal: MealEntry) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Исправить", "callback_data": "history_last_meal_edit:%s" % meal.entry_id},
                        {"text": "Отменить запись", "callback_data": "history_last_meal_delete_prompt:%s" % meal.entry_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _history_delete_confirm_reply_markup(entry_id: str) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Да, удалить", "callback_data": "history_last_meal_delete_confirm:%s" % entry_id},
                        {"text": "Отмена", "callback_data": "history_last_meal_delete_cancel:%s" % entry_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _last_meal_edit_menu_reply_markup(meal: MealEntry) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Название", "callback_data": "meal_entry_edit_title:%s" % meal.entry_id},
                        {"text": "Порция", "callback_data": "meal_entry_edit_portion_menu:%s" % meal.entry_id},
                    ],
                    [
                        {"text": "Время", "callback_data": "meal_entry_edit_time:%s" % meal.entry_id},
                        {"text": "Состав", "callback_data": "meal_entry_edit_summary:%s" % meal.entry_id},
                    ],
                    [
                        {"text": "Калории и БЖУ", "callback_data": "meal_entry_edit_macros:%s" % meal.entry_id},
                    ],
                    [
                        {"text": "Назад к записи", "callback_data": "meal_entry_edit_back:%s" % meal.entry_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_entry_portion_reply_markup(meal: MealEntry) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Меньше", "callback_data": "meal_entry_edit_portion:%s:smaller" % meal.entry_id},
                        {"text": "Стандарт", "callback_data": "meal_entry_edit_portion:%s:standard" % meal.entry_id},
                        {"text": "Больше", "callback_data": "meal_entry_edit_portion:%s:bigger" % meal.entry_id},
                    ],
                    [
                        {"text": "Своя порция", "callback_data": "meal_entry_edit_portion_custom:%s" % meal.entry_id},
                    ],
                    [
                        {"text": "Назад к записи", "callback_data": "meal_entry_edit_back:%s" % meal.entry_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_entry_edit_prompt_reply_markup(entry_id: str) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [{"text": "Назад к записи", "callback_data": "meal_entry_edit_back:%s" % entry_id}],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_entry_time_prompt_reply_markup(entry_id: str) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Сейчас", "callback_data": "meal_entry_edit_time_now:%s" % entry_id},
                        {"text": "Назад к записи", "callback_data": "meal_entry_edit_back:%s" % entry_id},
                    ]
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _extract_meal_summary_from_notes(meal: MealEntry) -> str:
        if not meal.notes:
            return ""
        try:
            payload = json.loads(meal.notes)
        except (TypeError, ValueError):
            return ""
        summary = payload.get("summary")
        return str(summary).strip() if summary else ""

    @staticmethod
    def _parse_draft_macros(raw_text: str) -> tuple[int, float, float, float]:
        normalized = raw_text.replace(",", " ")
        parts = [part for part in normalized.split() if part]
        if len(parts) != 4:
            raise ValueError("Введите 4 значения: калории белки жиры углеводы. Например: 420 31 12 46")
        try:
            calories = int(parts[0])
            protein_g = float(parts[1])
            fat_g = float(parts[2])
            carbs_g = float(parts[3])
        except ValueError as exc:
            raise ValueError("Калории и БЖУ должны быть числами.") from exc
        if calories < 0 or protein_g < 0 or fat_g < 0 or carbs_g < 0:
            raise ValueError("Калории и БЖУ не могут быть отрицательными.")
        return calories, protein_g, fat_g, carbs_g

    @staticmethod
    def _parse_portion_factor(raw_text: str) -> float:
        normalized = raw_text.strip().replace(",", ".")
        if not normalized:
            raise ValueError("Введите коэффициент порции. Например: 0.5, 1, 1.25 или 125%.")
        try:
            if normalized.endswith("%"):
                factor = float(normalized[:-1].strip()) / 100.0
            else:
                factor = float(normalized)
        except ValueError as exc:
            raise ValueError("Введите коэффициент порции. Например: 0.5, 1, 1.25 или 125%.") from exc
        if factor <= 0:
            raise ValueError("Порция должна быть больше нуля.")
        if factor < 0.1 or factor > 5:
            raise ValueError("Коэффициент порции должен быть в диапазоне от 0.1 до 5.")
        return factor

    @staticmethod
    def _parse_draft_time(raw_text: str, draft: MealPhotoDraft) -> datetime:
        try:
            parsed_time = datetime.strptime(raw_text.strip(), "%H:%M").time()
        except ValueError as exc:
            raise ValueError("Введите время в формате HH:MM, например: 13:45") from exc
        return datetime.combine(draft.occurred_at.date(), parsed_time)

    @staticmethod
    def _parse_meal_time(raw_text: str, occurred_at: datetime) -> datetime:
        try:
            parsed_time = datetime.strptime(raw_text.strip(), "%H:%M").time()
        except ValueError as exc:
            raise ValueError("Введите время в формате HH:MM, например: 13:45") from exc
        return datetime.combine(occurred_at.date(), parsed_time)

    def _handle_pending_custom_water_input(
        self,
        app_user: Optional[AppUser],
        raw_text: str,
        normalized_text: str,
    ) -> Optional[tuple[str, str]]:
        if app_user is None or app_user.user_id not in self._pending_custom_water_user_ids:
            return None
        if normalized_text in {
            "/menu",
            "/help",
            "/start",
            "/add_water",
            "/add_food",
            "/history",
            "/history_fix_last",
            "/history_delete_last",
            "/history_app",
            "/progress",
            "/profile",
            "/how_it_works",
        }:
            self._pending_custom_water_user_ids.discard(app_user.user_id)
            return None
        try:
            amount_ml = self._parse_water_amount(raw_text)
        except ValueError:
            return (
                "Нужен объем воды в мл числом от 50 до 3000. Например: 330",
                self._water_custom_input_reply_markup(),
            )
        self._pending_custom_water_user_ids.discard(app_user.user_id)
        response = self._handle_log_water(app_user, [str(amount_ml)])
        return response, self._water_result_reply_markup()

    @classmethod
    def _water_custom_input_reply_markup(cls) -> str:
        return json.dumps(
            {
                "resize_keyboard": True,
                "keyboard": [
                    [{"text": "Назад"}],
                ],
            },
            ensure_ascii=False,
        )

    def _try_begin_photo_processing(self, app_user: AppUser, now: datetime) -> Optional[str]:
        if app_user.has_admin_access:
            return None
        if app_user.user_id in self._photo_processing_user_ids:
            return "Подождите, предыдущее фото еще обрабатывается."
        available_at = self._photo_rate_limit_until_by_user.get(app_user.user_id)
        if available_at is not None and now < available_at:
            seconds_left = max(1, math.ceil((available_at - now).total_seconds()))
            return "Подождите %s сек. перед следующим фото." % seconds_left
        self._photo_processing_user_ids.add(app_user.user_id)
        return None

    def _finish_photo_processing(self, app_user: AppUser, now: datetime) -> None:
        if app_user.has_admin_access:
            return
        self._photo_processing_user_ids.discard(app_user.user_id)
        if self.settings.food_photo_rate_limit_seconds <= 0:
            self._photo_rate_limit_until_by_user.pop(app_user.user_id, None)
            return
        self._photo_rate_limit_until_by_user[app_user.user_id] = now + timedelta(
            seconds=self.settings.food_photo_rate_limit_seconds
        )

    @staticmethod
    def _private_chat_only_text() -> str:
        return "Бот работает только в личных сообщениях."

    @staticmethod
    def _ensure_admin(app_user: AppUser) -> None:
        if not app_user.has_admin_access:
            raise ValueError("Этот раздел доступен только администратору.")

    def _telegram_api(self, method: str, params: Dict[str, object]):
        encoded = parse.urlencode(params).encode()
        req = request.Request(self.base_url + method, data=encoded)
        try:
            with request.urlopen(req, timeout=self.settings.polling_timeout_seconds + 10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.error("Telegram API HTTP error method=%s status=%s body=%s", method, exc.code, body)
            raise
        if not payload.get("ok"):
            logger.error("Telegram API error method=%s payload=%s", method, payload)
            raise RuntimeError("Telegram API error for %s: %s" % (method, payload))
        return payload["result"]

    def _telegram_api_multipart(
        self,
        method: str,
        *,
        params: Dict[str, object],
        file_field_name: str,
        filename: str,
        file_bytes: bytes,
        mime_type: str,
    ):
        boundary = "----AiMeBoundary%s" % int(time.time() * 1000)
        body_chunks: List[bytes] = []
        for key, value in params.items():
            body_chunks.extend(
                [
                    ("--%s\r\n" % boundary).encode("utf-8"),
                    ('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode("utf-8"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        body_chunks.extend(
            [
                ("--%s\r\n" % boundary).encode("utf-8"),
                (
                    'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    % (file_field_name, filename)
                ).encode("utf-8"),
                ("Content-Type: %s\r\n\r\n" % mime_type).encode("utf-8"),
                file_bytes,
                b"\r\n",
                ("--%s--\r\n" % boundary).encode("utf-8"),
            ]
        )
        req = request.Request(
            self.base_url + method,
            data=b"".join(body_chunks),
            headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary},
        )
        try:
            with request.urlopen(req, timeout=self.settings.polling_timeout_seconds + 20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.error("Telegram multipart HTTP error method=%s status=%s body=%s", method, exc.code, body)
            raise
        if not payload.get("ok"):
            logger.error("Telegram multipart API error method=%s payload=%s", method, payload)
            raise RuntimeError("Telegram API error for %s: %s" % (method, payload))
        return payload["result"]

    def _local_now(self) -> datetime:
        return datetime.now(self.timezone).replace(tzinfo=None)

    def _local_today(self) -> date:
        return self._local_now().date()

    def _download_telegram_file(self, file_path: str) -> bytes:
        with request.urlopen(
            "https://api.telegram.org/file/bot%s/%s" % (self.settings.bot_token, file_path),
            timeout=self.settings.polling_timeout_seconds + 20,
        ) as response:
            return response.read()

    @staticmethod
    def _guess_mime_type(file_path: str) -> str:
        lower_path = file_path.lower()
        if lower_path.endswith(".png"):
            return "image/png"
        if lower_path.endswith(".webp"):
            return "image/webp"
        return "image/jpeg"
