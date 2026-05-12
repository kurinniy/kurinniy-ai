import json
import logging
import math
import shlex
import time
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional
from urllib import error as urlerror, parse, request
from uuid import uuid4
from zoneinfo import ZoneInfo

from ai_me.config import TelegramSettings
from ai_me.domain.decision_log import DecisionStatus
from ai_me.domain.digest import DailyFoodDigest, WeeklyFoodDigest
from ai_me.domain.food import MealDraftStatus, MealPhotoDraft, PhotoLogKind
from ai_me.domain.health import MealEntry, WaterEntry
from ai_me.domain.user import AppUser, UserStatus
from ai_me.services.digest_renderer import DigestImageRenderer
from ai_me.services.health_service import HealthService
from ai_me.version import format_release_date_line, format_version_line


logger = logging.getLogger(__name__)


class TelegramHealthBot:
    HISTORY_PAGE_SIZE = 10
    BUTTON_TO_COMMAND = {
        "Добавить еду": "/add_food",
        "Добавить воду": "/add_water",
        "История": "/history",
        "Приемы пищи": "/history_meals",
        "Распознавания": "/history_recognitions",
        "Прогресс": "/progress",
        "Профиль": "/profile",
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
        self._pending_meal_edit_states: Dict[int, Dict[str, str]] = {}

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
            try:
                self._handle_photo_message(
                    chat_id=chat_id,
                    app_user=app_user,
                    photo=photo,
                    caption=caption if isinstance(caption, str) else "",
                )
            finally:
                self._finish_photo_processing(app_user, now=self._local_now())
            return

        if isinstance(document, dict):
            logger.info("Received document message chat_id=%s user_id=%s", chat_id, user_id)
            if app_user is None:
                self._send_message(chat_id, self._registration_required_text())
                return
            self._handle_document_message(chat_id=chat_id, app_user=app_user, document=document)
            return

        if isinstance(text, str):
            logger.info("Received text command chat_id=%s user_id=%s text=%s", chat_id, user_id, text.strip())
            raw_text = text.strip()
            normalized_text = self._normalize_command_text(raw_text)
            pending_draft_response = self._handle_pending_draft_edit_input(
                app_user=app_user,
                raw_text=raw_text,
                normalized_text=normalized_text,
            )
            if pending_draft_response is not None:
                pending_text, pending_markup = pending_draft_response
                self._send_message(chat_id, pending_text, reply_markup=pending_markup)
                return
            pending_meal_response = self._handle_pending_meal_edit_input(
                app_user=app_user,
                raw_text=raw_text,
                normalized_text=normalized_text,
            )
            if pending_meal_response is not None:
                pending_text, pending_markup = pending_meal_response
                self._send_message(chat_id, pending_text, reply_markup=pending_markup)
                return
            pending_response = self._handle_pending_custom_water_input(
                app_user=app_user,
                raw_text=raw_text,
                normalized_text=normalized_text,
            )
            if pending_response is not None:
                pending_text, pending_markup = pending_response
                self._send_message(chat_id, pending_text, reply_markup=pending_markup)
                return
            response = self._route_command(
                text=normalized_text,
                chat_id=chat_id,
                user_id=user_id,
                username=username,
                first_name=first_name,
                app_user=app_user,
            )
            if response:
                reply_user = self.service.get_user_by_telegram_user_id(user_id) or app_user
                self._send_message(
                    chat_id,
                    response,
                    reply_markup=self._reply_markup_for_response(
                        text=normalized_text,
                        original_app_user=app_user,
                        reply_user=reply_user,
                    ),
                    parse_mode="Markdown" if normalized_text.startswith("/digest_preview") else None,
                )

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
            decisions = self.service.evaluate_day(app_user.user_id, meal.occurred_at.date(), now=self._local_now())
            logger.info("Meal confirmed draft_id=%s title=%s", draft_id, meal.title)
            if meal.kind == PhotoLogKind.WATER:
                self._send_message(
                    chat_id,
                    "Вода сохранена: %s л.\n%s" % (self._format_liters(meal.water_ml), self._format_new_decisions(decisions)),
                )
            else:
                self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
                self._send_message(
                    chat_id,
                    self._format_saved_meal_text(app_user, meal.title, meal.occurred_at.date()),
                    reply_markup=self._post_save_meal_reply_markup(),
                )
            return

        if data.startswith("meal_edit_menu:"):
            draft_id = data.split(":", 1)[1]
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
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
            self._try_answer_callback_query(query_id, "Порция обновлена.")
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

        if data.startswith("history_meals:"):
            offset = int(data.split(":", 1)[1])
            self._try_answer_callback_query(query_id, "Открываю историю приемов пищи.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_history_text(app_user, offset=offset),
                    reply_markup=self._meal_history_reply_markup(app_user, offset=offset),
                )
            return

        if data.startswith("history_recognitions:"):
            offset = int(data.split(":", 1)[1])
            self._try_answer_callback_query(query_id, "Открываю историю распознаваний.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_recognition_history_text(app_user, offset=offset),
                    reply_markup=self._recognition_history_reply_markup(app_user, offset=offset),
                )
            return

        if data.startswith("history_meal_open:"):
            _, entry_id, offset_text = data.split(":", 2)
            self._try_answer_callback_query(query_id, "Открываю прием пищи.")
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_history_detail_text(meal),
                    reply_markup=self._meal_history_detail_reply_markup(entry_id, offset=int(offset_text)),
                )
            return

        if data.startswith("history_meal_edit:"):
            _, entry_id, offset_text = data.split(":", 2)
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            self._clear_pending_meal_edit_state(app_user.user_id, entry_id=entry_id)
            self._try_answer_callback_query(query_id, "Можно исправить сохраненную запись.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_saved_meal_edit_menu_text(meal),
                    reply_markup=self._saved_meal_edit_menu_reply_markup(meal, offset=int(offset_text)),
                )
            return

        if data.startswith("meal_entry_edit_back:"):
            _, entry_id, offset_text = data.split(":", 2)
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            self._clear_pending_meal_edit_state(app_user.user_id, entry_id=entry_id)
            self._try_answer_callback_query(query_id, "Возвращаю запись.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_history_detail_text(meal),
                    reply_markup=self._meal_history_detail_reply_markup(entry_id, offset=int(offset_text)),
                )
            return

        if data.startswith("meal_entry_edit_title:"):
            _, entry_id, offset_text = data.split(":", 2)
            self._set_pending_meal_edit_state(app_user.user_id, entry_id, "title", int(offset_text))
            self._try_answer_callback_query(query_id, "Введите новое название.")
            self._send_message(
                chat_id,
                "Введите новое название приема пищи.",
                reply_markup=self._meal_entry_prompt_reply_markup(entry_id, offset=int(offset_text)),
            )
            return

        if data.startswith("meal_entry_edit_time:"):
            _, entry_id, offset_text = data.split(":", 2)
            self._set_pending_meal_edit_state(app_user.user_id, entry_id, "time", int(offset_text))
            self._try_answer_callback_query(query_id, "Введите новое время.")
            self._send_message(
                chat_id,
                "Введите время в формате HH:MM или выберите «Сейчас».",
                reply_markup=self._meal_entry_time_prompt_reply_markup(entry_id, offset=int(offset_text)),
            )
            return

        if data.startswith("meal_entry_edit_time_now:"):
            _, entry_id, offset_text = data.split(":", 2)
            old_meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            meal = self.service.update_meal_entry(
                app_user.user_id,
                entry_id,
                occurred_at=self._local_now(),
            )
            self._refresh_day_after_saved_meal_change(app_user, old_date=old_meal.occurred_at.date(), new_date=meal.occurred_at.date())
            self._clear_pending_meal_edit_state(app_user.user_id, entry_id=entry_id)
            self._try_answer_callback_query(query_id, "Время обновлено.")
            self._send_message(
                chat_id,
                self._format_saved_meal_updated_text(app_user, meal, old_date=old_meal.occurred_at.date()),
                reply_markup=self._saved_meal_result_reply_markup(offset=int(offset_text)),
            )
            return

        if data.startswith("meal_entry_edit_macros:"):
            _, entry_id, offset_text = data.split(":", 2)
            self._set_pending_meal_edit_state(app_user.user_id, entry_id, "macros", int(offset_text))
            self._try_answer_callback_query(query_id, "Введите калории и БЖУ.")
            self._send_message(
                chat_id,
                "Введите: калории белки жиры углеводы. Например: 420 31 12 46",
                reply_markup=self._meal_entry_prompt_reply_markup(entry_id, offset=int(offset_text)),
            )
            return

        if data.startswith("meal_entry_edit_portion_menu:"):
            _, entry_id, offset_text = data.split(":", 2)
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            self._clear_pending_meal_edit_state(app_user.user_id, entry_id=entry_id)
            self._try_answer_callback_query(query_id, "Выберите порцию.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_saved_meal_portion_text(meal),
                    reply_markup=self._saved_meal_portion_reply_markup(meal, offset=int(offset_text)),
                )
            return

        if data.startswith("meal_entry_edit_portion:"):
            _, entry_id, offset_text, portion_kind = data.split(":", 3)
            factor = {
                "smaller": 0.8,
                "standard": 1.0,
                "bigger": 1.2,
            }[portion_kind]
            old_meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            meal = self.service.scale_meal_entry_portion(app_user.user_id, entry_id, factor)
            self._refresh_day_after_saved_meal_change(app_user, old_date=old_meal.occurred_at.date(), new_date=meal.occurred_at.date())
            self._try_answer_callback_query(query_id, "Порция обновлена.")
            self._send_message(
                chat_id,
                self._format_saved_meal_updated_text(app_user, meal, old_date=old_meal.occurred_at.date()),
                reply_markup=self._saved_meal_result_reply_markup(offset=int(offset_text)),
            )
            return

        if data.startswith("meal_entry_delete_confirm:"):
            _, entry_id, offset_text = data.split(":", 2)
            try:
                meal = self.service.get_meal_entry(app_user.user_id, entry_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            self._try_answer_callback_query(query_id, "Подтвердите удаление.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="Удалить запись «%s»?" % meal.title,
                    reply_markup=self._saved_meal_delete_confirm_reply_markup(entry_id, offset=int(offset_text)),
                )
            return

        if data.startswith("meal_entry_delete_cancel:"):
            _, entry_id, offset_text = data.split(":", 2)
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
                    text=self._format_saved_meal_edit_menu_text(meal),
                    reply_markup=self._saved_meal_edit_menu_reply_markup(meal, offset=int(offset_text)),
                )
            return

        if data.startswith("meal_entry_delete:"):
            _, entry_id, offset_text = data.split(":", 2)
            old_meal = self.service.delete_meal_entry(app_user.user_id, entry_id)
            self._refresh_day_after_saved_meal_change(app_user, old_date=old_meal.occurred_at.date(), new_date=None)
            self._clear_pending_meal_edit_state(app_user.user_id, entry_id=entry_id)
            self._try_answer_callback_query(query_id, "Запись удалена.")
            self._send_message(
                chat_id,
                self._format_saved_meal_deleted_text(app_user, old_meal),
                reply_markup=self._saved_meal_result_reply_markup(offset=int(offset_text)),
            )
            return

        if data.startswith("history_draft_open:"):
            _, draft_id, offset_text = data.split(":", 2)
            self._try_answer_callback_query(query_id, "Открываю распознавание.")
            try:
                draft = self.service.get_meal_draft_any_status(app_user.user_id, draft_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            if isinstance(message_id, int):
                if draft.status == MealDraftStatus.PENDING:
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._format_meal_draft_card_text(draft),
                        reply_markup=self._history_pending_draft_reply_markup(draft, offset=int(offset_text)),
                    )
                else:
                    self._try_edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=self._format_history_draft_detail_text(draft),
                        reply_markup=self._history_draft_detail_reply_markup(draft, offset=int(offset_text)),
                    )
            return

        if data.startswith("history_draft_edit:"):
            _, draft_id, _offset_text = data.split(":", 2)
            try:
                draft = self.service.get_meal_draft_any_status(app_user.user_id, draft_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            if draft.status != MealDraftStatus.PENDING:
                self._try_answer_callback_query(query_id, "Редактировать можно только ожидающие черновики.")
                return
            self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
            self._try_answer_callback_query(query_id, "Можно исправить черновик.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._format_meal_draft_edit_menu_text(draft),
                    reply_markup=self._meal_draft_edit_menu_reply_markup(draft),
                )
            return

        if data.startswith("history_draft_retry:"):
            _, draft_id, _offset_text = data.split(":", 2)
            try:
                draft = self.service.get_meal_draft_any_status(app_user.user_id, draft_id)
            except ValueError as exc:
                self._send_message(chat_id, str(exc))
                return
            self._try_answer_callback_query(query_id, "Отправьте фото блюда еще раз.")
            self._send_message(
                chat_id,
                "Распознавание для «%s» можно запустить заново: просто отправьте новое фото блюда одним сообщением."
                % draft.title,
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
                    self._clear_pending_draft_edit_state(app_user.user_id)
                    self._clear_pending_meal_edit_state(app_user.user_id)
                return self._help_text(app_user)
            if command == "/menu":
                if app_user is None:
                    return self._registration_required_text()
                self._pending_custom_water_user_ids.discard(app_user.user_id)
                self._clear_pending_draft_edit_state(app_user.user_id)
                self._clear_pending_meal_edit_state(app_user.user_id)
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
                return self._history_home_text()
            if command == "/history_meals":
                return self._format_meal_history_text(app_user)
            if command == "/history_recognitions":
                return self._format_recognition_history_text(app_user)
            if command == "/progress":
                return self._handle_progress(app_user)
            if command == "/profile":
                return self._coming_soon_text("Профиль")
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
            self._clear_pending_draft_edit_state(app_user.user_id)
            self._clear_pending_meal_edit_state(app_user.user_id)
            return self._home_text(app_user)
        if chat_id is None or user_id is None:
            return self._registration_required_text()
        registered_user = self.service.register_user(
            telegram_user_id=user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            now=self._local_now(),
        )
        return self._welcome_text(registered_user)

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
        meal = self.service.confirm_meal_draft(app_user.user_id, args[0])
        decisions = self.service.evaluate_day(app_user.user_id, meal.occurred_at.date(), now=self._local_now())
        if meal.kind == PhotoLogKind.WATER:
            return "Вода сохранена: %s л.\n%s" % (self._format_liters(meal.water_ml), self._format_new_decisions(decisions))
        return "Прием пищи сохранен: %s.\n%s" % (meal.title, self._format_new_decisions(decisions))

    def _handle_reject_meal(self, app_user: AppUser, args: List[str]) -> str:
        if len(args) != 1:
            return "Использование: /reject_meal <draft_id>"
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
            "Отправь фото еды, чтобы создать черновик приема пищи.",
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
            "Я помогу вести питание без ручного ввода: отправляй фото еды, а я соберу черновик, сводку и digest.\n\n"
            "Что можно сделать прямо сейчас:\n"
            "- Добавить еду\n"
            "- Добавить воду\n"
            "- Как это работает"
        ) % first_name

    def _home_text(self, app_user: AppUser) -> str:
        digest_settings = self.service.get_digest_settings(app_user.user_id)
        daily_status = "включен" if digest_settings.daily_digest_enabled else "выключен"
        weekly_status = "включен" if digest_settings.weekly_digest_enabled else "выключен"
        return (
            "Главный экран\n"
            "Просто отправьте фото еды одним сообщением — я распознаю блюдо и создам черновик для подтверждения.\n\n"
            "Сейчас:\n"
            "- Ежедневный digest: %s\n"
            "- Недельный digest: %s\n\n"
            "Основные действия:\n"
            "- Добавить еду\n"
            "- Добавить воду\n"
            "- История\n"
            "- Прогресс\n"
            "- Профиль\n"
            "- Как это работает"
        ) % (daily_status, weekly_status)

    @staticmethod
    def _add_food_text() -> str:
        return (
            "Добавить еду\n"
            "Просто отправьте фото еды одним сообщением. Я распознаю блюдо и создам черновик для подтверждения."
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
            "Здесь можно быстро вернуться к недавним действиям.\n\n"
            "Доступно:\n"
            "- Приемы пищи\n"
            "- Распознавания"
        )

    @staticmethod
    def _coming_soon_text(section_name: str) -> str:
        return "%s\nЭтот раздел скоро появится. Пока можно отправить фото еды или открыть /help." % section_name

    @staticmethod
    def _how_it_works_text() -> str:
        return (
            "Как это работает\n"
            "1. Отправьте фото еды одним сообщением.\n"
            "2. Я распознаю блюдо и соберу черновик приема пищи.\n"
            "3. Вы подтвердите или отклоните черновик.\n"
            "4. После подтверждения запись попадет в сводку и digest."
        )

    def _handle_log_water(self, app_user: AppUser, args: List[str]) -> str:
        if len(args) != 1:
            raise ValueError("укажите объем воды в мл, например: /water 500")
        amount_ml = self._parse_water_amount(args[0])
        self.service.log_water(
            app_user.user_id,
            WaterEntry(
                entry_id=str(uuid4()),
                occurred_at=self._local_now(),
                amount_ml=amount_ml,
            ),
        )
        summary = self.service.get_daily_summary(app_user.user_id, self._local_today())
        remaining_ml = max(0, summary.goals.water_ml - summary.water_ml)
        return (
            "+%s мл воды добавлено.\n"
            "Сегодня: %s / %s л\n"
            "Осталось до цели: %s мл."
        ) % (
            amount_ml,
            self._format_liters_fixed(summary.water_ml),
            self._format_liters_fixed(summary.goals.water_ml),
            remaining_ml,
        )

    def _handle_progress(self, app_user: AppUser) -> str:
        return self._handle_summary(app_user, [])

    def _refresh_day_after_saved_meal_change(
        self,
        app_user: AppUser,
        *,
        old_date: date,
        new_date: Optional[date],
    ) -> None:
        self.service.evaluate_day(app_user.user_id, old_date, now=self._local_now())
        if new_date is not None and new_date != old_date:
            self.service.evaluate_day(app_user.user_id, new_date, now=self._local_now())

    def _format_saved_meal_updated_text(self, app_user: AppUser, meal: MealEntry, *, old_date: date) -> str:
        summary = self.service.get_daily_summary(app_user.user_id, meal.occurred_at.date())
        return (
            "Изменения сохранены.\n"
            "Обновленный прием пищи: %s, %s ккал.\n\n"
            "Сегодня:\n"
            "%s приема пищи\n"
            "Калории: %s\n"
            "Белок: %s / %s г"
        ) % (
            meal.title,
            self._format_integer_with_spaces(meal.calories),
            summary.meals_count,
            self._format_integer_with_spaces(summary.calories),
            self._format_decimal(summary.protein_g),
            self._format_decimal(summary.goals.protein_g),
        )

    def _format_saved_meal_deleted_text(self, app_user: AppUser, meal: MealEntry) -> str:
        summary = self.service.get_daily_summary(app_user.user_id, meal.occurred_at.date())
        return (
            "Запись удалена.\n"
            "Удаленный прием пищи: %s.\n\n"
            "Сегодня:\n"
            "%s приема пищи\n"
            "Калории: %s\n"
            "Белок: %s / %s г"
        ) % (
            meal.title,
            summary.meals_count,
            self._format_integer_with_spaces(summary.calories),
            self._format_decimal(summary.protein_g),
            self._format_decimal(summary.goals.protein_g),
        )

    def _format_meal_history_text(self, app_user: AppUser, offset: int = 0) -> str:
        meals = self.service.list_recent_meals(
            app_user.user_id,
            limit=self.HISTORY_PAGE_SIZE,
            offset=offset,
        )
        if not meals:
            return "Пока нет сохраненных приемов пищи. Отправьте первое фото еды."
        lines = ["Последние приемы пищи:"]
        for index, meal in enumerate(meals, start=offset + 1):
            lines.append(
                "%s. %s • %s • %s ккал"
                % (index, meal.occurred_at.strftime("%H:%M"), meal.title, self._format_integer_with_spaces(meal.calories))
            )
        return "\n".join(lines)

    def _format_recognition_history_text(self, app_user: AppUser, offset: int = 0) -> str:
        drafts = self.service.list_recent_food_draft_history(
            app_user.user_id,
            limit=self.HISTORY_PAGE_SIZE,
            offset=offset,
        )
        if not drafts:
            return "Пока нет распознаваний. Просто отправьте фото еды в чат."
        lines = ["Последние распознавания:"]
        for index, draft in enumerate(drafts, start=offset + 1):
            confidence_text = " • уверенность %s" % self._format_decimal(draft.confidence) if draft.confidence > 0 else ""
            lines.extend(
                [
                    "%s. %s" % (index, draft.title),
                    "   %s • %s%s"
                    % (
                        draft.occurred_at.strftime("%H:%M"),
                        self._meal_draft_status_text(draft.status),
                        confidence_text,
                    ),
                    "   %s" % draft.summary,
                ]
            )
        return "\n".join(lines)

    def _format_meal_history_detail_text(self, meal: MealEntry) -> str:
        summary = self._extract_meal_summary_from_notes(meal)
        lines = [
            "Прием пищи",
            "Название: %s" % meal.title,
            "Время: %s" % meal.occurred_at.strftime("%d.%m.%Y %H:%M"),
            "Калории: %s" % self._format_integer_with_spaces(meal.calories),
            "Белок: %s г" % self._format_decimal(meal.protein_g),
            "Жиры: %s г" % self._format_decimal(meal.fat_g),
            "Углеводы: %s г" % self._format_decimal(meal.carbs_g),
        ]
        if summary:
            lines.append("Состав: %s" % summary)
        return "\n".join(lines)

    @staticmethod
    def _format_saved_meal_edit_menu_text(meal: MealEntry) -> str:
        return "Что изменить в записи «%s»?" % meal.title

    @staticmethod
    def _format_saved_meal_portion_text(meal: MealEntry) -> str:
        return (
            "Какую порцию поставить?\n"
            "Сейчас: %s ккал, Б %s г • Ж %s г • У %s г"
        ) % (
            TelegramHealthBot._format_integer_with_spaces(meal.calories),
            TelegramHealthBot._format_decimal(meal.protein_g),
            TelegramHealthBot._format_decimal(meal.fat_g),
            TelegramHealthBot._format_decimal(meal.carbs_g),
        )

    def _format_history_draft_detail_text(self, draft: MealPhotoDraft) -> str:
        lines = [
            "Распознавание",
            "Название: %s" % draft.title,
            "Время: %s" % draft.occurred_at.strftime("%d.%m.%Y %H:%M"),
            "Статус: %s" % self._meal_draft_status_text(draft.status),
            "Уверенность: %s" % self._format_decimal(draft.confidence),
            "Состав: %s" % draft.summary,
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_water_amount(raw_value: str) -> int:
        try:
            amount_ml = int(raw_value)
        except ValueError as exc:
            raise ValueError("объем воды должен быть целым числом в мл") from exc
        if amount_ml < 50 or amount_ml > 3000:
            raise ValueError("объем воды должен быть от 50 до 3000 мл")
        return amount_ml

    def _format_new_decisions(self, decisions: Iterable) -> str:
        decision_list = list(decisions)
        if not decision_list:
            return "Новых решений нет."
        lines = ["Новые решения:"]
        for decision in decision_list:
            lines.append("- [%s] %s" % (decision.kind.value, decision.title))
        return "\n".join(lines)

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
    ):
        params: Dict[str, object] = {"chat_id": chat_id}
        if caption:
            params["caption"] = caption
        return self._telegram_api_multipart(
            "sendPhoto",
            params=params,
            file_field_name="photo",
            filename=filename,
            file_bytes=photo_bytes,
            mime_type="image/jpeg",
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
            "text": text,
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

    def _send_meal_draft(self, chat_id: int, draft: MealPhotoDraft) -> None:
        if draft.is_water_only:
            self._telegram_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
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
        self._telegram_api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": self._format_meal_draft_card_text(draft),
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
        if app_user is not None and app_user.has_admin_access:
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
        if app_user is not None and app_user.has_admin_access:
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

    def _handle_photo_message(self, chat_id: int, app_user: AppUser, photo: List[dict], caption: str) -> None:
        largest_photo = max(photo, key=lambda item: item.get("file_size", 0))
        file_id = largest_photo.get("file_id")
        file_unique_id = largest_photo.get("file_unique_id")
        if not isinstance(file_id, str) or not isinstance(file_unique_id, str):
            self._send_message(chat_id, "Метаданные фотографии неполные.")
            return

        try:
            file_info = self._telegram_api("getFile", {"file_id": file_id})
            file_path = file_info.get("file_path")
            if not isinstance(file_path, str):
                raise ValueError("Telegram не вернул путь к файлу")
            image_bytes = self._download_telegram_file(file_path)
            draft = self.service.create_meal_draft_from_photo(
                app_user.user_id,
                photo_file_id=file_id,
                photo_unique_id=file_unique_id,
                image_bytes=image_bytes,
                mime_type=self._guess_mime_type(file_path),
                occurred_at=self._local_now(),
                caption=caption,
            )
        except Exception as exc:
            logger.exception("Food photo analysis failed chat_id=%s file_id=%s error=%s", chat_id, file_id, exc)
            self._send_message(chat_id, "Не удалось распознать фото еды: %s" % exc)
            return

        logger.info("Food photo analyzed successfully chat_id=%s draft_id=%s", chat_id, draft.draft_id)
        self._send_meal_draft(chat_id, draft)

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
            file_info = self._telegram_api("getFile", {"file_id": file_id})
            file_path = file_info.get("file_path")
            if not isinstance(file_path, str):
                raise ValueError("Telegram не вернул путь к файлу")
            file_bytes = self._download_telegram_file(file_path)
            result = self.service.import_tbank_csv(
                app_user.user_id,
                file_bytes=file_bytes,
                source_file_name=file_name if isinstance(file_name, str) else "tbank.csv",
            )
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
        if app_user is not None and app_user.has_admin_access:
            menu_button = {
                "type": "web_app",
                "text": "Открыть приложение",
                "web_app": {"url": self.settings.mini_app_url},
            }
        else:
            menu_button = {
                "type": "commands",
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
        if app_user.has_admin_access:
            return "Mini App: откройте через кнопку меню «Открыть приложение»."
        return "Mini App: доступен только администратору."

    @classmethod
    def _normalize_command_text(cls, text: str) -> str:
        return cls.BUTTON_TO_COMMAND.get(text, text)

    def _reply_markup_for_response(
        self,
        text: str,
        original_app_user: Optional[AppUser],
        reply_user: Optional[AppUser],
    ) -> Optional[str]:
        if text == "/start" and original_app_user is None and reply_user is not None:
            return self._welcome_reply_markup()
        if reply_user is None:
            return None
        if text in {"/add_water", "/water_custom"}:
            return self._water_prompt_reply_markup()
        if text.startswith("/water "):
            return self._water_result_reply_markup()
        if text == "/history":
            return self._history_reply_markup()
        if text == "/history_meals":
            return self._meal_history_reply_markup(reply_user)
        if text == "/history_recognitions":
            return self._recognition_history_reply_markup(reply_user)
        if text in {
            "/start",
            "/help",
            "/menu",
            "/user_mode",
            "/admin_mode",
            "/add_food",
            "/progress",
            "/profile",
            "/how_it_works",
        }:
            return self._menu_reply_markup(reply_user)
        return None

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
                    [{"text": "Приемы пищи"}, {"text": "Распознавания"}],
                    [{"text": "Назад"}],
                ],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _history_inline_home_reply_markup() -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Приемы пищи", "callback_data": "history_meals:0"},
                        {"text": "Распознавания", "callback_data": "history_recognitions:0"},
                    ]
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

    def _meal_history_reply_markup(self, app_user: AppUser, offset: int = 0) -> str:
        meals = self.service.list_recent_meals(
            app_user.user_id,
            limit=self.HISTORY_PAGE_SIZE,
            offset=offset,
        )
        if not meals:
            return self._history_inline_home_reply_markup()
        inline_keyboard = []
        for index, meal in enumerate(meals, start=offset + 1):
            inline_keyboard.append(
                [
                    {
                        "text": "Открыть #%s" % index,
                        "callback_data": "history_meal_open:%s:%s" % (meal.entry_id, offset),
                    },
                    {
                        "text": "Изменить #%s" % index,
                        "callback_data": "history_meal_edit:%s:%s" % (meal.entry_id, offset),
                    },
                ]
            )
        if len(meals) == self.HISTORY_PAGE_SIZE:
            inline_keyboard.append(
                [
                    {
                        "text": "Показать еще",
                        "callback_data": "history_meals:%s" % (offset + self.HISTORY_PAGE_SIZE),
                    }
                ]
            )
        return json.dumps({"inline_keyboard": inline_keyboard}, ensure_ascii=False)

    def _recognition_history_reply_markup(self, app_user: AppUser, offset: int = 0) -> str:
        drafts = self.service.list_recent_food_draft_history(
            app_user.user_id,
            limit=self.HISTORY_PAGE_SIZE,
            offset=offset,
        )
        if not drafts:
            return self._history_inline_home_reply_markup()
        inline_keyboard = []
        for index, draft in enumerate(drafts, start=offset + 1):
            row = [
                {
                    "text": "Открыть #%s" % index,
                    "callback_data": "history_draft_open:%s:%s" % (draft.draft_id, offset),
                }
            ]
            if draft.status == MealDraftStatus.PENDING:
                row.extend(
                    [
                        {
                            "text": "Сохранить #%s" % index,
                            "callback_data": "meal_confirm:%s" % draft.draft_id,
                        },
                        {
                            "text": "Изменить #%s" % index,
                            "callback_data": "history_draft_edit:%s:%s" % (draft.draft_id, offset),
                        },
                    ]
                )
            elif draft.status == MealDraftStatus.REJECTED:
                row.append(
                    {
                        "text": "Распознать заново #%s" % index,
                        "callback_data": "history_draft_retry:%s:%s" % (draft.draft_id, offset),
                    }
                )
            inline_keyboard.append(row)
            if draft.status == MealDraftStatus.PENDING:
                inline_keyboard.append(
                    [
                        {
                            "text": "Отклонить #%s" % index,
                            "callback_data": "meal_reject:%s" % draft.draft_id,
                        }
                    ]
                )
        if len(drafts) == self.HISTORY_PAGE_SIZE:
            inline_keyboard.append(
                [
                    {
                        "text": "Показать еще",
                        "callback_data": "history_recognitions:%s" % (offset + self.HISTORY_PAGE_SIZE),
                    }
                ]
            )
        return json.dumps({"inline_keyboard": inline_keyboard}, ensure_ascii=False)

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
            "/history_meals",
            "/history_recognitions",
            "/progress",
            "/profile",
            "/how_it_works",
        }:
            self._clear_pending_draft_edit_state(app_user.user_id)
            return None
        draft_id = state["draft_id"]
        field = state["field"]
        try:
            if field == "title":
                draft = self.service.update_meal_draft(app_user.user_id, draft_id, title=raw_text.strip())
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
            return str(exc), self._draft_edit_prompt_reply_markup(draft_id)
        self._clear_pending_draft_edit_state(app_user.user_id, draft_id=draft_id)
        return self._format_meal_draft_card_text(draft), self._meal_draft_card_reply_markup(draft)

    def _handle_pending_meal_edit_input(
        self,
        app_user: Optional[AppUser],
        raw_text: str,
        normalized_text: str,
    ) -> Optional[tuple[str, str]]:
        if app_user is None:
            return None
        state = self._pending_meal_edit_states.get(app_user.user_id)
        if state is None:
            return None
        if normalized_text in {
            "/menu",
            "/help",
            "/start",
            "/add_food",
            "/add_water",
            "/history",
            "/history_meals",
            "/history_recognitions",
            "/progress",
            "/profile",
            "/how_it_works",
        }:
            self._clear_pending_meal_edit_state(app_user.user_id)
            return None
        entry_id = state["entry_id"]
        offset = int(state["offset"])
        field = state["field"]
        old_meal = self.service.get_meal_entry(app_user.user_id, entry_id)
        try:
            if field == "title":
                meal = self.service.update_meal_entry(app_user.user_id, entry_id, title=raw_text.strip())
            elif field == "time":
                meal = self.service.update_meal_entry(
                    app_user.user_id,
                    entry_id,
                    occurred_at=self._parse_meal_entry_time(raw_text, old_meal),
                )
            elif field == "macros":
                calories, protein_g, fat_g, carbs_g = self._parse_draft_macros(raw_text)
                meal = self.service.update_meal_entry(
                    app_user.user_id,
                    entry_id,
                    calories=calories,
                    protein_g=protein_g,
                    fat_g=fat_g,
                    carbs_g=carbs_g,
                )
            else:
                return None
        except ValueError as exc:
            return str(exc), self._meal_entry_prompt_reply_markup(entry_id, offset=offset)
        self._refresh_day_after_saved_meal_change(app_user, old_date=old_meal.occurred_at.date(), new_date=meal.occurred_at.date())
        self._clear_pending_meal_edit_state(app_user.user_id, entry_id=entry_id)
        return self._format_saved_meal_updated_text(app_user, meal, old_date=old_meal.occurred_at.date()), self._saved_meal_result_reply_markup(offset=offset)

    def _set_pending_draft_edit_state(self, user_id: int, draft_id: str, field: str) -> None:
        self._pending_draft_edit_states[user_id] = {"draft_id": draft_id, "field": field}

    def _clear_pending_draft_edit_state(self, user_id: int, draft_id: Optional[str] = None) -> None:
        state = self._pending_draft_edit_states.get(user_id)
        if state is None:
            return
        if draft_id is not None and state.get("draft_id") != draft_id:
            return
        self._pending_draft_edit_states.pop(user_id, None)

    def _set_pending_meal_edit_state(self, user_id: int, entry_id: str, field: str, offset: int) -> None:
        self._pending_meal_edit_states[user_id] = {
            "entry_id": entry_id,
            "field": field,
            "offset": str(offset),
        }

    def _clear_pending_meal_edit_state(self, user_id: int, entry_id: Optional[str] = None) -> None:
        state = self._pending_meal_edit_states.get(user_id)
        if state is None:
            return
        if entry_id is not None and state.get("entry_id") != entry_id:
            return
        self._pending_meal_edit_states.pop(user_id, None)

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
    def _meal_draft_card_reply_markup(draft: MealPhotoDraft) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Сохранить", "callback_data": "meal_confirm:%s" % draft.draft_id},
                        {"text": "Изменить", "callback_data": "meal_edit_menu:%s" % draft.draft_id},
                    ],
                    [
                        {"text": "Не то блюдо", "callback_data": "meal_rewrite_prompt:%s" % draft.draft_id},
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
                        {"text": "Назад к черновику", "callback_data": "meal_edit_back:%s" % draft.draft_id},
                    ],
                ]
            },
            ensure_ascii=False,
        )

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

    def _format_saved_meal_text(self, app_user: AppUser, title: str, target_date: date) -> str:
        summary = self.service.get_daily_summary(app_user.user_id, target_date)
        return (
            "Сохранено: %s.\n\n"
            "Сегодня:\n"
            "%s приема пищи\n"
            "Белок: %s / %s г\n"
            "Вода: %s / %s л"
        ) % (
            title,
            summary.meals_count,
            self._format_decimal(summary.protein_g),
            self._format_decimal(summary.goals.protein_g),
            self._format_liters_fixed(summary.water_ml),
            self._format_liters_fixed(summary.goals.water_ml),
        )

    @classmethod
    def _post_save_meal_reply_markup(cls) -> str:
        return json.dumps(
            {
                "resize_keyboard": True,
                "keyboard": [
                    [{"text": "Добавить воду"}],
                    [{"text": "История"}, {"text": "Прогресс"}],
                ],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_history_detail_reply_markup(entry_id: str, offset: int) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Изменить", "callback_data": "history_meal_edit:%s:%s" % (entry_id, offset)},
                    ],
                    [
                        {"text": "Назад к приемам пищи", "callback_data": "history_meals:%s" % offset},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _saved_meal_edit_menu_reply_markup(meal: MealEntry, offset: int) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Изменить название", "callback_data": "meal_entry_edit_title:%s:%s" % (meal.entry_id, offset)},
                    ],
                    [
                        {"text": "Изменить порцию", "callback_data": "meal_entry_edit_portion_menu:%s:%s" % (meal.entry_id, offset)},
                    ],
                    [
                        {"text": "Изменить время", "callback_data": "meal_entry_edit_time:%s:%s" % (meal.entry_id, offset)},
                    ],
                    [
                        {"text": "Изменить БЖУ", "callback_data": "meal_entry_edit_macros:%s:%s" % (meal.entry_id, offset)},
                    ],
                    [
                        {"text": "Удалить запись", "callback_data": "meal_entry_delete_confirm:%s:%s" % (meal.entry_id, offset)},
                    ],
                    [
                        {"text": "Назад", "callback_data": "meal_entry_edit_back:%s:%s" % (meal.entry_id, offset)},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _saved_meal_portion_reply_markup(meal: MealEntry, offset: int) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Меньше", "callback_data": "meal_entry_edit_portion:%s:%s:smaller" % (meal.entry_id, offset)},
                        {"text": "Стандарт", "callback_data": "meal_entry_edit_portion:%s:%s:standard" % (meal.entry_id, offset)},
                        {"text": "Больше", "callback_data": "meal_entry_edit_portion:%s:%s:bigger" % (meal.entry_id, offset)},
                    ],
                    [
                        {"text": "Назад", "callback_data": "history_meal_edit:%s:%s" % (meal.entry_id, offset)},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_entry_prompt_reply_markup(entry_id: str, offset: int) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Назад", "callback_data": "history_meal_edit:%s:%s" % (entry_id, offset)},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _meal_entry_time_prompt_reply_markup(entry_id: str, offset: int) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Сейчас", "callback_data": "meal_entry_edit_time_now:%s:%s" % (entry_id, offset)},
                        {"text": "Назад", "callback_data": "history_meal_edit:%s:%s" % (entry_id, offset)},
                    ]
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _saved_meal_delete_confirm_reply_markup(entry_id: str, offset: int) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Да, удалить", "callback_data": "meal_entry_delete:%s:%s" % (entry_id, offset)},
                        {"text": "Отмена", "callback_data": "meal_entry_delete_cancel:%s:%s" % (entry_id, offset)},
                    ]
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _saved_meal_result_reply_markup(offset: int) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Назад к приемам пищи", "callback_data": "history_meals:%s" % offset},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _history_pending_draft_reply_markup(draft: MealPhotoDraft, offset: int) -> str:
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": "Сохранить", "callback_data": "meal_confirm:%s" % draft.draft_id},
                        {"text": "Изменить", "callback_data": "history_draft_edit:%s:%s" % (draft.draft_id, offset)},
                    ],
                    [
                        {"text": "Отклонить", "callback_data": "meal_reject:%s" % draft.draft_id},
                    ],
                    [
                        {"text": "Назад к распознаваниям", "callback_data": "history_recognitions:%s" % offset},
                    ],
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _history_draft_detail_reply_markup(draft: MealPhotoDraft, offset: int) -> str:
        rows = []
        if draft.status == MealDraftStatus.REJECTED:
            rows.append(
                [
                    {
                        "text": "Распознать заново",
                        "callback_data": "history_draft_retry:%s:%s" % (draft.draft_id, offset),
                    }
                ]
            )
        rows.append(
            [
                {"text": "Назад к распознаваниям", "callback_data": "history_recognitions:%s" % offset},
            ]
        )
        return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)

    @staticmethod
    def _meal_draft_status_text(status: MealDraftStatus) -> str:
        return {
            MealDraftStatus.PENDING: "Ожидает решения",
            MealDraftStatus.CONFIRMED: "Сохранено",
            MealDraftStatus.REJECTED: "Отклонено",
        }[status]

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
    def _parse_draft_time(raw_text: str, draft: MealPhotoDraft) -> datetime:
        try:
            parsed_time = datetime.strptime(raw_text.strip(), "%H:%M").time()
        except ValueError as exc:
            raise ValueError("Введите время в формате HH:MM, например: 13:45") from exc
        return datetime.combine(draft.occurred_at.date(), parsed_time)

    @staticmethod
    def _parse_meal_entry_time(raw_text: str, meal: MealEntry) -> datetime:
        try:
            parsed_time = datetime.strptime(raw_text.strip(), "%H:%M").time()
        except ValueError as exc:
            raise ValueError("Введите время в формате HH:MM, например: 13:45") from exc
        return datetime.combine(meal.occurred_at.date(), parsed_time)

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
            "/history_meals",
            "/history_recognitions",
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
