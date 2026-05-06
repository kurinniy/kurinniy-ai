import json
import logging
import shlex
import time
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional
from urllib import parse, request
from uuid import uuid4
from zoneinfo import ZoneInfo

from ai_me.domain.food import MealDraftStatus, MealPhotoDraft
from ai_me.config import TelegramSettings
from ai_me.domain.decision_log import DecisionStatus
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    MealEntry,
    SleepEntry,
    WaterEntry,
    WeightEntry,
)
from ai_me.services.health_service import HealthService


logger = logging.getLogger(__name__)


class TelegramHealthBot:
    BUTTON_TO_COMMAND = {
        "Сводка за сегодня": "/summary",
        "Открытые решения": "/decisions",
        "Черновики еды": "/drafts",
        "Кто я": "/whoami",
        "Помощь": "/help",
    }

    def __init__(self, service: HealthService, settings: TelegramSettings) -> None:
        self.service = service
        self.settings = settings
        self.base_url = "https://api.telegram.org/bot%s/" % settings.bot_token
        self.timezone = ZoneInfo(settings.timezone_name)

    def run_forever(self) -> None:
        self._ensure_polling_mode()
        self._sync_bot_commands()
        logger.info("Telegram long polling started")
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

        text = message.get("text")
        caption = message.get("caption")
        photo = message.get("photo")
        chat = message.get("chat", {})
        user = message.get("from", {})
        chat_id = chat.get("id")
        user_id = user.get("id")
        if not isinstance(chat_id, int):
            logger.warning("Skipping update without valid chat_id: %s", update)
            return

        if self.settings.allowed_user_ids and user_id not in self.settings.allowed_user_ids:
            logger.warning("Rejected update from non-allowed user_id=%s chat_id=%s", user_id, chat_id)
            self._send_message(chat_id, "Бот доступен только для разрешенных пользователей Telegram.")
            return

        if isinstance(photo, list) and photo:
            logger.info("Received photo message chat_id=%s user_id=%s", chat_id, user_id)
            self._handle_photo_message(
                chat_id=chat_id,
                photo=photo,
                caption=caption if isinstance(caption, str) else "",
            )
            return

        if isinstance(text, str):
            logger.info("Received text command chat_id=%s user_id=%s text=%s", chat_id, user_id, text.strip())
            normalized_text = self._normalize_command_text(text.strip())
            response = self._route_command(text=normalized_text, chat_id=chat_id, user_id=user_id)
            if response:
                self._send_message(
                    chat_id,
                    response,
                    reply_markup=self._menu_reply_markup() if self._should_show_menu(normalized_text) else None,
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
        if not isinstance(data, str) or not isinstance(query_id, str) or not isinstance(chat_id, int):
            logger.warning("Skipping malformed callback query: %s", callback_query)
            return
        logger.info(
            "Received callback query_id=%s user_id=%s chat_id=%s message_id=%s data=%s",
            query_id,
            user_id,
            chat_id,
            message_id,
            data,
        )
        if self.settings.allowed_user_ids and user_id not in self.settings.allowed_user_ids:
            logger.warning("Rejected callback from non-allowed user_id=%s", user_id)
            self._answer_callback_query(query_id, "Доступ запрещен.")
            return

        if data.startswith("meal_confirm:"):
            draft_id = data.split(":", 1)[1]
            logger.info("Confirming meal draft_id=%s", draft_id)
            try:
                meal = self.service.confirm_meal_draft(draft_id)
            except ValueError as exc:
                logger.warning("Meal confirmation failed draft_id=%s error=%s", draft_id, exc)
                self._answer_callback_query(query_id, str(exc))
                return
            self._answer_callback_query(query_id, "Прием пищи сохранен.")
            if isinstance(message_id, int):
                self._try_edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        "Прием пищи сохранен\n"
                        "Блюдо: %s\n"
                        "Статус: подтверждено"
                    )
                    % meal.title,
                )
            decisions = self.service.evaluate_day(meal.occurred_at.date(), now=self._local_now())
            logger.info("Meal confirmed draft_id=%s title=%s", draft_id, meal.title)
            self._send_message(
                chat_id,
                "Прием пищи сохранен: %s.\n%s" % (meal.title, self._format_new_decisions(decisions)),
            )
            return

        if data.startswith("meal_reject:"):
            draft_id = data.split(":", 1)[1]
            logger.info("Rejecting meal draft_id=%s", draft_id)
            try:
                draft = self.service.reject_meal_draft(draft_id)
            except ValueError as exc:
                logger.warning("Meal rejection failed draft_id=%s error=%s", draft_id, exc)
                self._answer_callback_query(query_id, str(exc))
                return
            self._answer_callback_query(query_id, "Черновик отклонен.")
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

        logger.warning("Unknown callback action query_id=%s data=%s", query_id, data)
        self._answer_callback_query(query_id, "Неизвестное действие.")

    def _route_command(
        self,
        text: str,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
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
                return self._help_text()
            if command == "/help":
                return self._help_text()
            if command == "/menu":
                return self._help_text()
            if command == "/whoami":
                return self._handle_whoami(chat_id=chat_id, user_id=user_id)
            if command == "/confirm_meal":
                return self._handle_confirm_meal(args)
            if command == "/reject_meal":
                return self._handle_reject_meal(args)
            if command == "/drafts":
                return self._handle_drafts()
            if command == "/water":
                return self._handle_water(args)
            if command == "/meal":
                return self._handle_meal(args)
            if command == "/weight":
                return self._handle_weight(args)
            if command == "/sleep":
                return self._handle_sleep(args)
            if command == "/activity":
                return self._handle_activity(args)
            if command == "/goals":
                return self._handle_goals(args)
            if command == "/summary":
                return self._handle_summary(args)
            if command == "/decisions":
                return self._handle_decisions(args)
        except ValueError as exc:
            return "Некорректные аргументы команды: %s" % exc
        return "Неизвестная команда.\n\n%s" % self._help_text()

    def _handle_whoami(self, chat_id: Optional[int], user_id: Optional[int]) -> str:
        lines = [
            "Данные Telegram",
            "user_id=%s" % (user_id if user_id is not None else "неизвестно"),
            "chat_id=%s" % (chat_id if chat_id is not None else "неизвестно"),
        ]
        if self.settings.allowed_user_ids:
            lines.append("белый_список=включен")
        else:
            lines.append("белый_список=выключен")
        return "\n".join(lines)

    def _handle_confirm_meal(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Использование: /confirm_meal <draft_id>"
        meal = self.service.confirm_meal_draft(args[0])
        decisions = self.service.evaluate_day(meal.occurred_at.date(), now=self._local_now())
        return "Прием пищи сохранен: %s.\n%s" % (meal.title, self._format_new_decisions(decisions))

    def _handle_reject_meal(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Использование: /reject_meal <draft_id>"
        draft = self.service.reject_meal_draft(args[0])
        return "Черновик приема пищи отклонен: %s." % draft.title

    def _handle_drafts(self) -> str:
        drafts = self.service.list_meal_drafts(status=MealDraftStatus.PENDING)
        if not drafts:
            return "Нет ожидающих черновиков приема пищи."
        lines = ["Ожидающие черновики приема пищи:"]
        for draft in drafts[:10]:
            lines.append(
                "- %s | %s ккал | уверенность %.2f | id=%s"
                % (draft.title, draft.calories, draft.confidence, draft.draft_id)
            )
        return "\n".join(lines)

    def _handle_water(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Использование: /water <ml>"
        amount_ml = int(args[0])
        now = self._local_now()
        self.service.log_water(
            WaterEntry(
                entry_id=str(uuid4()),
                occurred_at=now,
                amount_ml=amount_ml,
            )
        )
        decisions = self.service.evaluate_day(now.date(), now=now)
        return "Записано воды: %s мл.\n%s" % (amount_ml, self._format_new_decisions(decisions))

    def _handle_meal(self, args: List[str]) -> str:
        if len(args) < 3:
            return 'Использование: /meal <calories> <protein_g> <title>. Пример: /meal 650 45 "Курица с рисом"'
        calories = int(args[0])
        protein_g = float(args[1])
        title = " ".join(args[2:])
        now = self._local_now()
        self.service.log_meal(
            MealEntry(
                entry_id=str(uuid4()),
                occurred_at=now,
                title=title,
                calories=calories,
                protein_g=protein_g,
            )
        )
        decisions = self.service.evaluate_day(now.date(), now=now)
        return "Прием пищи записан: %s.\n%s" % (title, self._format_new_decisions(decisions))

    def _handle_weight(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Использование: /weight <kg>"
        weight_kg = float(args[0])
        now = self._local_now()
        self.service.log_weight(
            WeightEntry(
                entry_id=str(uuid4()),
                occurred_at=now,
                weight_kg=weight_kg,
            )
        )
        return "Вес записан: %.1f кг." % weight_kg

    def _handle_sleep(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Использование: /sleep <hours>"
        duration_hours = float(args[0])
        end_at = self._local_now()
        start_at = end_at - timedelta(hours=duration_hours)
        self.service.log_sleep(
            SleepEntry(
                entry_id=str(uuid4()),
                start_at=start_at,
                end_at=end_at,
            )
        )
        decisions = self.service.evaluate_day(end_at.date(), now=end_at)
        return "Сон записан: %.2f ч.\n%s" % (duration_hours, self._format_new_decisions(decisions))

    def _handle_activity(self, args: List[str]) -> str:
        if len(args) < 3:
            return 'Использование: /activity <minutes> <steps> <title>. Пример: /activity 45 6000 "Вечерняя прогулка"'
        duration_minutes = int(args[0])
        steps = int(args[1])
        title = " ".join(args[2:])
        now = self._local_now()
        self.service.log_activity(
            ActivityEntry(
                entry_id=str(uuid4()),
                occurred_at=now,
                title=title,
                duration_minutes=duration_minutes,
                steps=steps,
            )
        )
        decisions = self.service.evaluate_day(now.date(), now=now)
        return "Активность записана: %s.\n%s" % (title, self._format_new_decisions(decisions))

    def _handle_goals(self, args: List[str]) -> str:
        if len(args) != 4:
            return "Использование: /goals <water_ml> <protein_g> <sleep_hours> <steps>"
        today = self._local_today()
        goals = DailyHealthGoals(
            target_date=today,
            water_ml=int(args[0]),
            protein_g=int(args[1]),
            sleep_hours=float(args[2]),
            steps=int(args[3]),
        )
        self.service.set_goals(goals)
        return (
            "Цели обновлены на %s:\nвода=%s мл, белок=%s г, сон=%.1f ч, шаги=%s"
            % (today.isoformat(), goals.water_ml, goals.protein_g, goals.sleep_hours, goals.steps)
        )

    def _handle_summary(self, args: List[str]) -> str:
        if args:
            target_date = date.fromisoformat(args[0])
        else:
            target_date = self._local_today()
        self.service.evaluate_day(target_date, now=self._local_now())
        summary = self.service.get_daily_summary(target_date)
        meals = self.service.list_meals(target_date)
        response = (
            "Сводка за %s\n"
            "Приемы пищи: %s\n"
            "Калории: %s\n"
            "Белок: %.1f / %s г\n"
            "Жиры: %.1f г\n"
            "Углеводы: %.1f г\n"
            "Вода: %s / %s мл\n"
            "Сон: %.2f / %.1f ч\n"
            "Шаги: %s / %s\n"
            "Активность: %s мин\n"
            "Вес: %s"
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
                summary.activity_minutes,
                "%.1f кг" % summary.latest_weight_kg if summary.latest_weight_kg is not None else "нет данных",
            )
        )
        if meals:
            meal_lines = [
                "- %s | %s | %s ккал | Б %.1f / Ж %.1f / У %.1f"
                % (
                    meal.occurred_at.strftime("%H:%M"),
                    meal.title,
                    meal.calories,
                    meal.protein_g,
                    meal.fat_g,
                    meal.carbs_g,
                )
                for meal in meals
            ]
            response += "\nЕда:\n%s" % "\n".join(meal_lines)
        else:
            response += "\nЕда:\n- Нет записанных приемов пищи"
        return response

    def _handle_decisions(self, args: List[str]) -> str:
        if args:
            target_date = date.fromisoformat(args[0])
        else:
            target_date = self._local_today()
        self.service.evaluate_day(target_date, now=self._local_now())
        decisions = self.service.list_decisions(
            status=DecisionStatus.OPEN,
            target_date=target_date,
        )
        if not decisions:
            return "Нет открытых решений на %s." % target_date.isoformat()

        lines = ["Открытые решения на %s:" % target_date.isoformat()]
        for decision in decisions:
            lines.append("- [%s] %s" % (decision.kind.value, decision.title))
        return "\n".join(lines)

    def _help_text(self) -> str:
        return (
            "Команды:\n"
            "/whoami\n"
            "Отправь фото еды, чтобы создать черновик приема пищи.\n"
            "/menu\n"
            "/confirm_meal <draft_id>\n"
            "/reject_meal <draft_id>\n"
            "/drafts\n"
            "/water <ml>\n"
            '/meal <calories> <protein_g> <title>  Пример: /meal 650 45 "Курица с рисом"\n'
            "/weight <kg>\n"
            "/sleep <hours>\n"
            '/activity <minutes> <steps> <title>  Пример: /activity 45 6000 "Вечерняя прогулка"\n'
            "/goals <water_ml> <protein_g> <sleep_hours> <steps>\n"
            "/summary [YYYY-MM-DD]\n"
            "/decisions [YYYY-MM-DD]"
        )

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

    def _send_message(self, chat_id: int, text: str, reply_markup: Optional[str] = None) -> None:
        params = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        self._telegram_api("sendMessage", params)

    def _edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        self._telegram_api(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
            },
        )

    def _try_edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            self._edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Callback message edit failed chat_id=%s message_id=%s error=%s",
                chat_id,
                message_id,
                exc,
            )

    def _send_meal_draft(self, chat_id: int, draft: MealPhotoDraft) -> None:
        items_text = "\n".join(
            "- %s (%s): %s ккал, Б %.1f / Ж %.1f / У %.1f"
            % (
                item.title,
                item.portion_text,
                item.calories,
                item.protein_g,
                item.fat_g,
                item.carbs_g,
            )
            for item in draft.items
        )
        text = (
            "Черновик приема пищи\n"
            "Блюдо: %s\n"
            "Состав: %s\n"
            "Калории: %s\n"
            "Белки: %.1f г\n"
            "Жиры: %.1f г\n"
            "Углеводы: %.1f г\n"
            "Уверенность: %.2f\n"
            "ID черновика: %s"
            % (
                draft.title,
                draft.summary,
                draft.calories,
                draft.protein_g,
                draft.fat_g,
                draft.carbs_g,
                draft.confidence,
                draft.draft_id,
            )
        )
        if items_text:
            text += "\nИнгредиенты:\n%s" % items_text

        self._telegram_api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
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
                    }
                ),
            },
        )

    def _answer_callback_query(self, callback_query_id: str, text: str) -> None:
        self._telegram_api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_query_id,
                "text": text,
            },
        )

    def _handle_photo_message(self, chat_id: int, photo: List[dict], caption: str) -> None:
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
            {"command": "menu", "description": "Показать кнопки и список команд"},
            {"command": "summary", "description": "Сводка за сегодня"},
            {"command": "decisions", "description": "Открытые решения"},
            {"command": "drafts", "description": "Черновики приема пищи"},
            {"command": "whoami", "description": "Мои Telegram ID"},
            {"command": "help", "description": "Справка по командам"},
        ]
        try:
            self._telegram_api(
                "setMyCommands",
                {
                    "commands": json.dumps(commands, ensure_ascii=False),
                },
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Bot command sync failed: %s", exc)

    @classmethod
    def _normalize_command_text(cls, text: str) -> str:
        return cls.BUTTON_TO_COMMAND.get(text, text)

    @staticmethod
    def _should_show_menu(text: str) -> bool:
        return text in {"/start", "/help", "/menu"}

    @classmethod
    def _menu_reply_markup(cls) -> str:
        return json.dumps(
            {
                "resize_keyboard": True,
                "keyboard": [
                    [{"text": "Сводка за сегодня"}, {"text": "Открытые решения"}],
                    [{"text": "Черновики еды"}, {"text": "Кто я"}],
                    [{"text": "Помощь"}],
                ],
            },
            ensure_ascii=False,
        )

    def _telegram_api(self, method: str, params: Dict[str, object]):
        encoded = parse.urlencode(params).encode()
        req = request.Request(self.base_url + method, data=encoded)
        with request.urlopen(req, timeout=self.settings.polling_timeout_seconds + 10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            logger.error("Telegram API error method=%s payload=%s", method, payload)
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
