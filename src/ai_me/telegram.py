import json
import shlex
import sys
import time
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional
from urllib import parse, request
from uuid import uuid4
from zoneinfo import ZoneInfo

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


class TelegramHealthBot:
    def __init__(self, service: HealthService, settings: TelegramSettings) -> None:
        self.service = service
        self.settings = settings
        self.base_url = "https://api.telegram.org/bot%s/" % settings.bot_token
        self.timezone = ZoneInfo(settings.timezone_name)

    def run_forever(self) -> None:
        self._ensure_polling_mode()
        print("Telegram long polling started", file=sys.stderr)
        offset = None
        while True:
            try:
                updates = self._get_updates(offset=offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    self._handle_update(update)
            except Exception as exc:  # pragma: no cover
                print("Polling error: %s" % exc, file=sys.stderr)
                time.sleep(3)

    def _handle_update(self, update: Dict[str, object]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return

        text = message.get("text")
        chat = message.get("chat", {})
        user = message.get("from", {})
        chat_id = chat.get("id")
        user_id = user.get("id")
        if not isinstance(text, str) or not isinstance(chat_id, int):
            return

        if self.settings.allowed_user_ids and user_id not in self.settings.allowed_user_ids:
            self._send_message(chat_id, "This bot is restricted to approved Telegram users.")
            return

        response = self._route_command(text=text.strip(), chat_id=chat_id, user_id=user_id)
        if response:
            self._send_message(chat_id, response)

    def _route_command(
        self,
        text: str,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> str:
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return "Command parsing failed: %s" % exc
        if not parts:
            return ""

        command = parts[0].split("@", 1)[0].lower()
        args = parts[1:]
        try:
            if command == "/start":
                return self._help_text()
            if command == "/help":
                return self._help_text()
            if command == "/whoami":
                return self._handle_whoami(chat_id=chat_id, user_id=user_id)
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
            return "Invalid command arguments: %s" % exc
        return "Unknown command.\n\n%s" % self._help_text()

    def _handle_whoami(self, chat_id: Optional[int], user_id: Optional[int]) -> str:
        lines = [
            "Telegram identity",
            "user_id=%s" % (user_id if user_id is not None else "unknown"),
            "chat_id=%s" % (chat_id if chat_id is not None else "unknown"),
        ]
        if self.settings.allowed_user_ids:
            lines.append("allowlist=enabled")
        else:
            lines.append("allowlist=disabled")
        return "\n".join(lines)

    def _handle_water(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Usage: /water <ml>"
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
        return "Logged %s ml of water.\n%s" % (amount_ml, self._format_new_decisions(decisions))

    def _handle_meal(self, args: List[str]) -> str:
        if len(args) < 3:
            return 'Usage: /meal <calories> <protein_g> <title>. Example: /meal 650 45 "Chicken rice bowl"'
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
        return "Logged meal: %s.\n%s" % (title, self._format_new_decisions(decisions))

    def _handle_weight(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Usage: /weight <kg>"
        weight_kg = float(args[0])
        now = self._local_now()
        self.service.log_weight(
            WeightEntry(
                entry_id=str(uuid4()),
                occurred_at=now,
                weight_kg=weight_kg,
            )
        )
        return "Logged weight: %.1f kg." % weight_kg

    def _handle_sleep(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Usage: /sleep <hours>"
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
        return "Logged %.2f hours of sleep.\n%s" % (duration_hours, self._format_new_decisions(decisions))

    def _handle_activity(self, args: List[str]) -> str:
        if len(args) < 3:
            return 'Usage: /activity <minutes> <steps> <title>. Example: /activity 45 6000 "Evening walk"'
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
        return "Logged activity: %s.\n%s" % (title, self._format_new_decisions(decisions))

    def _handle_goals(self, args: List[str]) -> str:
        if len(args) != 4:
            return "Usage: /goals <water_ml> <protein_g> <sleep_hours> <steps>"
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
            "Updated goals for %s:\nwater=%s ml, protein=%s g, sleep=%.1f h, steps=%s"
            % (today.isoformat(), goals.water_ml, goals.protein_g, goals.sleep_hours, goals.steps)
        )

    def _handle_summary(self, args: List[str]) -> str:
        if args:
            target_date = date.fromisoformat(args[0])
        else:
            target_date = self._local_today()
        self.service.evaluate_day(target_date, now=self._local_now())
        summary = self.service.get_daily_summary(target_date)
        return (
            "Summary for %s\n"
            "Meals: %s\n"
            "Calories: %s\n"
            "Protein: %.1f / %s g\n"
            "Water: %s / %s ml\n"
            "Sleep: %.2f / %.1f h\n"
            "Steps: %s / %s\n"
            "Activity: %s min\n"
            "Weight: %s"
            % (
                target_date.isoformat(),
                summary.meals_count,
                summary.calories,
                summary.protein_g,
                summary.goals.protein_g,
                summary.water_ml,
                summary.goals.water_ml,
                summary.sleep_hours,
                summary.goals.sleep_hours,
                summary.steps,
                summary.goals.steps,
                summary.activity_minutes,
                "%.1f kg" % summary.latest_weight_kg if summary.latest_weight_kg is not None else "n/a",
            )
        )

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
            return "No open decisions for %s." % target_date.isoformat()

        lines = ["Open decisions for %s:" % target_date.isoformat()]
        for decision in decisions:
            lines.append("- [%s] %s" % (decision.kind.value, decision.title))
        return "\n".join(lines)

    def _help_text(self) -> str:
        return (
            "Commands:\n"
            "/whoami\n"
            "/water <ml>\n"
            '/meal <calories> <protein_g> <title>  Example: /meal 650 45 "Chicken rice bowl"\n'
            "/weight <kg>\n"
            "/sleep <hours>\n"
            '/activity <minutes> <steps> <title>  Example: /activity 45 6000 "Evening walk"\n'
            "/goals <water_ml> <protein_g> <sleep_hours> <steps>\n"
            "/summary [YYYY-MM-DD]\n"
            "/decisions [YYYY-MM-DD]"
        )

    def _format_new_decisions(self, decisions: Iterable) -> str:
        decision_list = list(decisions)
        if not decision_list:
            return "No new decisions."
        lines = ["New decisions:"]
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

    def _send_message(self, chat_id: int, text: str) -> None:
        self._telegram_api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
            },
        )

    def _ensure_polling_mode(self) -> None:
        try:
            self._telegram_api(
                "deleteWebhook",
                {
                    "drop_pending_updates": "false",
                },
            )
        except Exception as exc:  # pragma: no cover
            print("Webhook cleanup failed: %s" % exc, file=sys.stderr)

    def _telegram_api(self, method: str, params: Dict[str, object]):
        encoded = parse.urlencode(params).encode()
        req = request.Request(self.base_url + method, data=encoded)
        with request.urlopen(req, timeout=self.settings.polling_timeout_seconds + 10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError("Telegram API error for %s: %s" % (method, payload))
        return payload["result"]

    def _local_now(self) -> datetime:
        return datetime.now(self.timezone).replace(tzinfo=None)

    def _local_today(self) -> date:
        return self._local_now().date()
