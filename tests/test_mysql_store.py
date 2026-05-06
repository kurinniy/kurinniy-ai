import unittest

from ai_me.storage.mysql import MySQLStore


class MigrationAwareMySQLStore(MySQLStore):
    def __init__(self) -> None:
        self._connect_kwargs = {"database": "ai_me"}
        self.present_columns = {
            ("meals", "fat_g"),
        }
        self.executed = []

    def _fetchone(self, query: str, params: tuple):
        _, table_name, column_name = params
        if (table_name, column_name) in self.present_columns:
            return {"exists": 1}
        return None

    def _execute(self, query: str, params: tuple) -> int:
        self.executed.append((query.strip(), params))
        return 1


class MySQLStoreMigrationTest(unittest.TestCase):
    def test_apply_schema_migrations_adds_only_missing_columns(self) -> None:
        store = MigrationAwareMySQLStore()

        store._apply_schema_migrations()

        executed_sql = [query for query, _ in store.executed]
        self.assertEqual(
            executed_sql,
            [
                "ALTER TABLE meals ADD COLUMN carbs_g DOUBLE NOT NULL DEFAULT 0 AFTER fat_g",
                "ALTER TABLE meal_photo_drafts ADD COLUMN fat_g DOUBLE NOT NULL DEFAULT 0 AFTER protein_g",
                "ALTER TABLE meal_photo_drafts ADD COLUMN carbs_g DOUBLE NOT NULL DEFAULT 0 AFTER fat_g",
            ],
        )


if __name__ == "__main__":
    unittest.main()
