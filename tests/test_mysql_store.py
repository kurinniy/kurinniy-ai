import unittest

from ai_me.domain.user import AppUser, UserStatus
from ai_me.storage.mysql import MySQLStore


class MigrationAwareMySQLStore(MySQLStore):
    def __init__(self) -> None:
        self._connect_kwargs = {"database": "ai_me"}
        self.owner_telegram_user_id = 96445950
        self.present_columns = {
            ("meals", "fat_g"),
            ("health_goals", "target_date"),
            ("finance_transactions", "transaction_key"),
        }
        self.present_indexes = set()
        self.executed = []
        self.users = {}

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        return (table_name, column_name) in self.present_columns

    def _index_exists(self, table_name: str, index_name: str) -> bool:
        return (table_name, index_name) in self.present_indexes

    def _execute(self, query: str, params: tuple) -> int:
        normalized = " ".join(query.split())
        self.executed.append((normalized, params))
        return 1

    def get_user_by_telegram_user_id(self, telegram_user_id: int):
        return self.users.get(telegram_user_id)

    def create_user(
        self,
        telegram_user_id: int,
        chat_id: int,
        username: str,
        first_name: str,
        status: UserStatus,
        is_admin: bool,
    ) -> AppUser:
        user = AppUser(
            user_id=1,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            status=status,
            is_admin=is_admin,
        )
        self.users[telegram_user_id] = user
        return user


class MySQLStoreMigrationTest(unittest.TestCase):
    def test_apply_schema_migrations_adds_missing_user_columns_and_indexes(self) -> None:
        store = MigrationAwareMySQLStore()

        store._apply_schema_migrations()

        executed_sql = [query for query, _ in store.executed]
        self.assertIn("ALTER TABLE meals ADD COLUMN carbs_g DOUBLE NOT NULL DEFAULT 0 AFTER fat_g", executed_sql)
        self.assertIn("ALTER TABLE meals ADD COLUMN user_id BIGINT NULL AFTER entry_id", executed_sql)
        self.assertIn("ALTER TABLE finance_transactions ADD COLUMN user_id BIGINT NULL AFTER transaction_key", executed_sql)
        self.assertIn(
            "CREATE UNIQUE INDEX uk_decision_user_key ON decision_log (user_id, decision_key)",
            executed_sql,
        )
        self.assertIn(
            "CREATE UNIQUE INDEX uk_finance_user_key ON finance_transactions (user_id, transaction_key)",
            executed_sql,
        )

    def test_owner_user_is_created_and_legacy_rows_are_backfilled(self) -> None:
        store = MigrationAwareMySQLStore()
        store.present_columns.update(
            {
                ("health_goals", "user_id"),
                ("meals", "user_id"),
                ("meal_photo_drafts", "user_id"),
                ("water_entries", "user_id"),
                ("sleep_entries", "user_id"),
                ("weight_entries", "user_id"),
                ("activity_entries", "user_id"),
                ("decision_log", "user_id"),
                ("finance_transactions", "user_id"),
            }
        )

        store._ensure_owner_user_and_backfill()

        self.assertIn(96445950, store.users)
        executed_sql = [query for query, _ in store.executed]
        self.assertIn("UPDATE meals SET user_id = %s WHERE user_id IS NULL", executed_sql)
        self.assertIn("UPDATE finance_transactions SET user_id = %s WHERE user_id IS NULL", executed_sql)


if __name__ == "__main__":
    unittest.main()

