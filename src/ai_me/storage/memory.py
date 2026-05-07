from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

from ai_me.domain.digest import DigestRun, DigestStatus, DigestType, UserDigestSettings
from ai_me.domain.decision_log import DecisionLogEntry, DecisionStatus
from ai_me.domain.finance import FinanceCategoryTotal, FinanceMonthlySummary, FinanceTransaction
from ai_me.domain.food import MealDraftStatus, MealMedia, MealPhotoDraft
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    DailyHealthSummary,
    MealEntry,
    SleepEntry,
    WaterEntry,
    WeightEntry,
)
from ai_me.domain.health_import import HealthImportFile, HealthImportProvider, UserGoogleDriveSettings
from ai_me.domain.user import AppUser, InviteCode, InviteStatus, UserStatus


class InMemoryStore:
    def __init__(self) -> None:
        self._next_user_id = 1
        self._users_by_id: Dict[int, AppUser] = {}
        self._user_ids_by_telegram_id: Dict[int, int] = {}
        self._invites_by_code: Dict[str, InviteCode] = {}
        self._digest_settings_by_user: Dict[int, UserDigestSettings] = {}
        self._google_drive_settings_by_user: Dict[int, UserGoogleDriveSettings] = {}
        self._health_import_files_by_user: Dict[int, Dict[str, HealthImportFile]] = {}
        self._digest_runs_by_id: Dict[str, DigestRun] = {}
        self._goals: Dict[int, Dict[date, DailyHealthGoals]] = {}
        self._meals: Dict[int, List[MealEntry]] = {}
        self._meal_drafts: Dict[int, Dict[str, MealPhotoDraft]] = {}
        self._meal_media_by_user: Dict[int, Dict[str, MealMedia]] = {}
        self._water_entries: Dict[int, List[WaterEntry]] = {}
        self._sleep_entries: Dict[int, List[SleepEntry]] = {}
        self._weight_entries: Dict[int, List[WeightEntry]] = {}
        self._activity_entries: Dict[int, List[ActivityEntry]] = {}
        self._finance_transactions_by_key: Dict[int, Dict[str, FinanceTransaction]] = {}
        self._decisions_by_id: Dict[int, Dict[str, DecisionLogEntry]] = {}
        self._decision_ids_by_key: Dict[int, Dict[str, str]] = {}

    def close(self) -> None:
        return None

    def list_users(self, status: Optional[UserStatus] = None) -> List[AppUser]:
        users = list(self._users_by_id.values())
        if status is not None:
            users = [user for user in users if user.status == status]
        return sorted(users, key=lambda item: item.user_id)

    def get_user_by_telegram_user_id(self, telegram_user_id: int) -> Optional[AppUser]:
        user_id = self._user_ids_by_telegram_id.get(telegram_user_id)
        return self._users_by_id.get(user_id) if user_id is not None else None

    def create_user(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: str,
        first_name: str,
        status: UserStatus,
        is_admin: bool,
    ) -> AppUser:
        existing = self.get_user_by_telegram_user_id(telegram_user_id)
        if existing is not None:
            updated = AppUser(
                user_id=existing.user_id,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                username=username,
                first_name=first_name,
                status=status,
                is_admin=is_admin,
                created_at=existing.created_at,
            )
            self._users_by_id[existing.user_id] = updated
            return updated
        user = AppUser(
            user_id=self._next_user_id,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            status=status,
            is_admin=is_admin,
            created_at=datetime.now(),
        )
        self._next_user_id += 1
        self._users_by_id[user.user_id] = user
        self._user_ids_by_telegram_id[telegram_user_id] = user.user_id
        return user

    def update_user_profile(self, user: AppUser, chat_id: int, username: str, first_name: str) -> AppUser:
        updated = AppUser(
            user_id=user.user_id,
            telegram_user_id=user.telegram_user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            status=user.status,
            is_admin=user.is_admin,
            created_at=user.created_at,
        )
        self._users_by_id[user.user_id] = updated
        return updated

    def get_user_google_drive_settings(self, user_id: int) -> Optional[UserGoogleDriveSettings]:
        return self._google_drive_settings_by_user.get(user_id)

    def upsert_user_google_drive_settings(self, settings: UserGoogleDriveSettings) -> UserGoogleDriveSettings:
        current = self._google_drive_settings_by_user.get(settings.user_id)
        saved = UserGoogleDriveSettings(
            user_id=settings.user_id,
            folder_id=settings.folder_id,
            folder_url=settings.folder_url,
            enabled=settings.enabled,
            created_at=current.created_at if current and current.created_at is not None else settings.created_at or datetime.now(),
            updated_at=settings.updated_at or datetime.now(),
        )
        self._google_drive_settings_by_user[settings.user_id] = saved
        return saved

    def list_users_with_google_drive_enabled(self) -> List[AppUser]:
        user_ids = [
            user_id
            for user_id, settings in self._google_drive_settings_by_user.items()
            if settings.enabled
        ]
        users = [self._users_by_id[user_id] for user_id in user_ids if user_id in self._users_by_id]
        return sorted(users, key=lambda item: item.user_id)

    def create_health_import_file(self, imported_file: HealthImportFile) -> HealthImportFile:
        key = "%s:%s" % (imported_file.provider.value, imported_file.external_file_id)
        self._health_import_files_by_user.setdefault(imported_file.user_id, {})[key] = imported_file
        return imported_file

    def get_health_import_file(
        self,
        user_id: int,
        provider: HealthImportProvider,
        external_file_id: str,
    ) -> Optional[HealthImportFile]:
        key = "%s:%s" % (provider.value, external_file_id)
        return self._health_import_files_by_user.get(user_id, {}).get(key)

    def list_health_import_files(
        self,
        user_id: int,
        provider: Optional[HealthImportProvider] = None,
    ) -> List[HealthImportFile]:
        files = list(self._health_import_files_by_user.get(user_id, {}).values())
        if provider is not None:
            files = [item for item in files if item.provider == provider]
        return sorted(files, key=lambda item: (item.imported_at, item.file_name))

    def create_invite(self, invite: InviteCode) -> InviteCode:
        self._invites_by_code[invite.code] = invite
        return invite

    def get_invite(self, code: str) -> Optional[InviteCode]:
        return self._invites_by_code.get(code)

    def list_invites(self, status: Optional[InviteStatus] = None) -> List[InviteCode]:
        invites = list(self._invites_by_code.values())
        if status is not None:
            invites = [invite for invite in invites if invite.status == status]
        return sorted(invites, key=lambda item: item.created_at, reverse=True)

    def increment_invite_usage(self, code: str, status: InviteStatus) -> None:
        invite = self._invites_by_code[code]
        self._invites_by_code[code] = InviteCode(
            code=invite.code,
            created_by_user_id=invite.created_by_user_id,
            created_at=invite.created_at,
            expires_at=invite.expires_at,
            max_uses=invite.max_uses,
            used_count=invite.used_count + 1,
            status=status,
        )

    def update_invite_status(self, code: str, status: InviteStatus) -> None:
        invite = self._invites_by_code[code]
        self._invites_by_code[code] = InviteCode(
            code=invite.code,
            created_by_user_id=invite.created_by_user_id,
            created_at=invite.created_at,
            expires_at=invite.expires_at,
            max_uses=invite.max_uses,
            used_count=invite.used_count,
            status=status,
        )

    def get_user_digest_settings(self, user_id: int) -> Optional[UserDigestSettings]:
        return self._digest_settings_by_user.get(user_id)

    def upsert_user_digest_settings(self, settings: UserDigestSettings) -> UserDigestSettings:
        self._digest_settings_by_user[settings.user_id] = settings
        return settings

    def create_digest_run(self, run: DigestRun) -> DigestRun:
        self._digest_runs_by_id[run.run_id] = run
        return run

    def list_digest_runs(
        self,
        user_id: int,
        digest_type: Optional[DigestType] = None,
        status: Optional[DigestStatus] = None,
    ) -> List[DigestRun]:
        runs = [run for run in self._digest_runs_by_id.values() if run.user_id == user_id]
        if digest_type is not None:
            runs = [run for run in runs if run.digest_type == digest_type]
        if status is not None:
            runs = [run for run in runs if run.status == status]
        return sorted(runs, key=lambda item: (item.digest_date, item.created_at))

    def update_digest_run(
        self,
        run_id: str,
        status: DigestStatus,
        sent_at: Optional[datetime] = None,
        error_message: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        current = self._digest_runs_by_id[run_id]
        self._digest_runs_by_id[run_id] = DigestRun(
            run_id=current.run_id,
            user_id=current.user_id,
            digest_type=current.digest_type,
            digest_date=current.digest_date,
            status=status,
            created_at=current.created_at,
            scheduled_for=current.scheduled_for,
            sent_at=sent_at if sent_at is not None else current.sent_at,
            error_message=error_message,
            payload=payload if payload is not None else current.payload,
        )

    def set_health_goals(self, user_id: int, goals: DailyHealthGoals) -> None:
        self._goals.setdefault(user_id, {})[goals.target_date] = goals

    def get_health_goals(self, user_id: int, target_date: date) -> DailyHealthGoals:
        return self._goals.get(user_id, {}).get(target_date, DailyHealthGoals(target_date=target_date))

    def add_meal(self, user_id: int, entry: MealEntry) -> None:
        self._meals.setdefault(user_id, []).append(entry)

    def list_meals(self, user_id: int, target_date: date) -> List[MealEntry]:
        meals = [entry for entry in self._meals.get(user_id, []) if entry.occurred_at.date() == target_date]
        return sorted(meals, key=lambda item: item.occurred_at)

    def create_meal_draft(self, user_id: int, draft: MealPhotoDraft) -> None:
        self._meal_drafts.setdefault(user_id, {})[draft.draft_id] = draft

    def create_meal_media(self, media: MealMedia) -> None:
        self._meal_media_by_user.setdefault(media.user_id, {})[media.media_id] = media

    def list_meal_media(self, user_id: int, target_date: Optional[date] = None) -> List[MealMedia]:
        media = list(self._meal_media_by_user.get(user_id, {}).values())
        if target_date is not None:
            media = [item for item in media if item.occurred_at.date() == target_date]
        return sorted(media, key=lambda item: item.occurred_at)

    def attach_meal_media_to_meal(self, user_id: int, draft_id: str, meal_entry_id: str) -> None:
        current_items = self._meal_media_by_user.get(user_id, {})
        for media_id, media in list(current_items.items()):
            if media.draft_id != draft_id:
                continue
            current_items[media_id] = MealMedia(
                media_id=media.media_id,
                user_id=media.user_id,
                draft_id=media.draft_id,
                occurred_at=media.occurred_at,
                created_at=media.created_at,
                mime_type=media.mime_type,
                telegram_file_id=media.telegram_file_id,
                telegram_unique_id=media.telegram_unique_id,
                byte_size=media.byte_size,
                sha256=media.sha256,
                image_bytes=media.image_bytes,
                meal_entry_id=meal_entry_id,
                storage_kind=media.storage_kind,
            )

    def get_meal_draft(self, user_id: int, draft_id: str) -> Optional[MealPhotoDraft]:
        return self._meal_drafts.get(user_id, {}).get(draft_id)

    def list_meal_drafts(self, user_id: int, status: MealDraftStatus) -> List[MealPhotoDraft]:
        drafts = [
            draft
            for draft in self._meal_drafts.get(user_id, {}).values()
            if draft.status == status
        ]
        return sorted(drafts, key=lambda item: item.created_at)

    def update_meal_draft_status(self, user_id: int, draft_id: str, status: MealDraftStatus) -> None:
        current = self._meal_drafts[user_id][draft_id]
        self._meal_drafts[user_id][draft_id] = MealPhotoDraft(
            draft_id=current.draft_id,
            created_at=current.created_at,
            occurred_at=current.occurred_at,
            title=current.title,
            summary=current.summary,
            calories=current.calories,
            protein_g=current.protein_g,
            fat_g=current.fat_g,
            carbs_g=current.carbs_g,
            confidence=current.confidence,
            photo_file_id=current.photo_file_id,
            photo_unique_id=current.photo_unique_id,
            status=status,
            source=current.source,
            items=current.items,
        )

    def add_water(self, user_id: int, entry: WaterEntry) -> None:
        self._water_entries.setdefault(user_id, []).append(entry)

    def add_sleep(self, user_id: int, entry: SleepEntry) -> None:
        self._sleep_entries.setdefault(user_id, []).append(entry)

    def add_weight(self, user_id: int, entry: WeightEntry) -> None:
        self._weight_entries.setdefault(user_id, []).append(entry)

    def add_activity(self, user_id: int, entry: ActivityEntry) -> None:
        entries = self._activity_entries.setdefault(user_id, [])
        for index, current in enumerate(entries):
            if current.entry_id == entry.entry_id:
                entries[index] = entry
                return
        entries.append(entry)

    def build_health_summary(self, user_id: int, target_date: date) -> DailyHealthSummary:
        meals = [entry for entry in self._meals.get(user_id, []) if entry.occurred_at.date() == target_date]
        water_entries = [
            entry for entry in self._water_entries.get(user_id, []) if entry.occurred_at.date() == target_date
        ]
        sleep_entries = [entry for entry in self._sleep_entries.get(user_id, []) if entry.end_at.date() == target_date]
        activity_entries = [
            entry for entry in self._activity_entries.get(user_id, []) if entry.occurred_at.date() == target_date
        ]
        weight_entries = [
            entry for entry in self._weight_entries.get(user_id, []) if entry.occurred_at.date() == target_date
        ]

        latest_weight = sorted(weight_entries, key=lambda item: item.occurred_at)[-1].weight_kg if weight_entries else None

        return DailyHealthSummary(
            target_date=target_date,
            meals_count=len(meals),
            calories=sum(entry.calories for entry in meals),
            protein_g=round(sum(entry.protein_g for entry in meals), 2),
            fat_g=round(sum(entry.fat_g for entry in meals), 2),
            carbs_g=round(sum(entry.carbs_g for entry in meals), 2),
            water_ml=sum(entry.amount_ml for entry in water_entries),
            sleep_hours=round(sum(entry.duration_hours for entry in sleep_entries), 2),
            steps=sum(entry.steps for entry in activity_entries),
            activity_minutes=sum(entry.duration_minutes for entry in activity_entries),
            latest_weight_kg=latest_weight,
            goals=self.get_health_goals(user_id, target_date),
        )

    def upsert_decisions(self, user_id: int, decisions: Iterable[DecisionLogEntry]) -> List[DecisionLogEntry]:
        decision_map = self._decisions_by_id.setdefault(user_id, {})
        decision_keys = self._decision_ids_by_key.setdefault(user_id, {})
        inserted = []
        for decision in decisions:
            if decision.decision_key in decision_keys:
                continue
            decision_map[decision.decision_id] = decision
            decision_keys[decision.decision_key] = decision.decision_id
            inserted.append(decision)
        return inserted

    def list_decisions(
        self,
        user_id: int,
        status: Optional[DecisionStatus] = None,
        context_date: Optional[date] = None,
    ) -> List[DecisionLogEntry]:
        decisions = list(self._decisions_by_id.get(user_id, {}).values())
        if status is not None:
            decisions = [item for item in decisions if item.status == status]
        if context_date is not None:
            decisions = [item for item in decisions if item.context_date == context_date]
        return sorted(decisions, key=lambda item: item.created_at)

    def update_decision_status(self, user_id: int, decision_id: str, status: DecisionStatus) -> None:
        current = self._decisions_by_id[user_id][decision_id]
        self._decisions_by_id[user_id][decision_id] = DecisionLogEntry(
            decision_id=current.decision_id,
            decision_key=current.decision_key,
            created_at=current.created_at,
            agent=current.agent,
            kind=current.kind,
            title=current.title,
            rationale=current.rationale,
            context_date=current.context_date,
            status=status,
            payload=current.payload,
        )

    def upsert_finance_transactions(self, user_id: int, transactions: Iterable[FinanceTransaction]) -> int:
        transactions_by_key = self._finance_transactions_by_key.setdefault(user_id, {})
        inserted = 0
        for transaction in transactions:
            if transaction.transaction_key in transactions_by_key:
                continue
            transactions_by_key[transaction.transaction_key] = transaction
            inserted += 1
        return inserted

    def build_finance_monthly_summary(self, user_id: int, month_start: date) -> FinanceMonthlySummary:
        if month_start.month == 12:
            next_month = date(month_start.year + 1, 1, 1)
        else:
            next_month = date(month_start.year, month_start.month + 1, 1)
        month_transactions = [
            item
            for item in self._finance_transactions_by_key.get(user_id, {}).values()
            if month_start <= item.occurred_at.date() < next_month
        ]
        income_total = round(sum(item.amount for item in month_transactions if item.amount > 0), 2)
        expense_total = round(abs(sum(item.amount for item in month_transactions if item.amount < 0)), 2)
        category_buckets: Dict[str, List[FinanceTransaction]] = {}
        for transaction in month_transactions:
            if transaction.amount >= 0:
                continue
            category = transaction.category or "Без категории"
            category_buckets.setdefault(category, []).append(transaction)
        top_categories = sorted(
            [
                FinanceCategoryTotal(
                    category=category,
                    amount=round(abs(sum(item.amount for item in items)), 2),
                    transaction_count=len(items),
                )
                for category, items in category_buckets.items()
            ],
            key=lambda item: item.amount,
            reverse=True,
        )[:5]
        return FinanceMonthlySummary(
            month_start=month_start,
            month_end=next_month,
            transaction_count=len(month_transactions),
            income_total=income_total,
            expense_total=expense_total,
            net_total=round(income_total - expense_total, 2),
            top_expense_categories=top_categories,
        )
