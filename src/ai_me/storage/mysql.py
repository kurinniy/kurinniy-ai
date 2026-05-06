import json
from datetime import date, datetime, time
from typing import Iterable, List, Optional

from ai_me.domain.decision_log import DecisionKind, DecisionLogEntry, DecisionStatus
from ai_me.domain.food import FoodItemEstimate, MealDraftStatus, MealPhotoDraft
from ai_me.domain.health import (
    ActivityEntry,
    DailyHealthGoals,
    DailyHealthSummary,
    MealEntry,
    SleepEntry,
    WaterEntry,
    WeightEntry,
)

try:
    import mysql.connector
except ImportError:  # pragma: no cover
    mysql = None
else:  # pragma: no cover
    mysql = mysql.connector


class MySQLStore:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        charset: str = "utf8mb4",
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
        self._init_schema()

    def close(self) -> None:
        return None

    def set_health_goals(self, goals: DailyHealthGoals) -> None:
        self._execute(
            """
            INSERT INTO health_goals (target_date, water_ml, protein_g, sleep_hours, steps)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                water_ml = VALUES(water_ml),
                protein_g = VALUES(protein_g),
                sleep_hours = VALUES(sleep_hours),
                steps = VALUES(steps)
            """,
            (
                goals.target_date,
                goals.water_ml,
                goals.protein_g,
                goals.sleep_hours,
                goals.steps,
            ),
        )

    def get_health_goals(self, target_date: date) -> DailyHealthGoals:
        row = self._fetchone(
            "SELECT * FROM health_goals WHERE target_date = %s",
            (target_date,),
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

    def add_meal(self, entry: MealEntry) -> None:
        self._execute(
            """
            INSERT INTO meals (entry_id, occurred_at, title, calories, protein_g, fat_g, carbs_g, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entry.entry_id,
                entry.occurred_at,
                entry.title,
                entry.calories,
                entry.protein_g,
                entry.fat_g,
                entry.carbs_g,
                entry.notes,
            ),
        )

    def create_meal_draft(self, draft: MealPhotoDraft) -> None:
        self._execute(
            """
            INSERT INTO meal_photo_drafts (
                draft_id,
                created_at,
                occurred_at,
                title,
                summary,
                calories,
                protein_g,
                fat_g,
                carbs_g,
                confidence,
                photo_file_id,
                photo_unique_id,
                status,
                source,
                items_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                draft.draft_id,
                draft.created_at,
                draft.occurred_at,
                draft.title,
                draft.summary,
                draft.calories,
                draft.protein_g,
                draft.fat_g,
                draft.carbs_g,
                draft.confidence,
                draft.photo_file_id,
                draft.photo_unique_id,
                draft.status.value,
                draft.source,
                json.dumps([item.__dict__ for item in draft.items], sort_keys=True),
            ),
        )

    def get_meal_draft(self, draft_id: str) -> Optional[MealPhotoDraft]:
        row = self._fetchone(
            "SELECT * FROM meal_photo_drafts WHERE draft_id = %s",
            (draft_id,),
        )
        return self._to_meal_draft(row) if row else None

    def list_meal_drafts(self, status: MealDraftStatus) -> List[MealPhotoDraft]:
        rows = self._fetchall(
            """
            SELECT *
            FROM meal_photo_drafts
            WHERE status = %s
            ORDER BY created_at ASC
            """,
            (status.value,),
        )
        return [self._to_meal_draft(row) for row in rows]

    def update_meal_draft_status(self, draft_id: str, status: MealDraftStatus) -> None:
        self._execute(
            """
            UPDATE meal_photo_drafts
            SET status = %s
            WHERE draft_id = %s
            """,
            (status.value, draft_id),
        )

    def add_water(self, entry: WaterEntry) -> None:
        self._execute(
            """
            INSERT INTO water_entries (entry_id, occurred_at, amount_ml)
            VALUES (%s, %s, %s)
            """,
            (entry.entry_id, entry.occurred_at, entry.amount_ml),
        )

    def add_sleep(self, entry: SleepEntry) -> None:
        self._execute(
            """
            INSERT INTO sleep_entries (entry_id, start_at, end_at, quality_score, notes)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                entry.entry_id,
                entry.start_at,
                entry.end_at,
                entry.quality_score,
                entry.notes,
            ),
        )

    def add_weight(self, entry: WeightEntry) -> None:
        self._execute(
            """
            INSERT INTO weight_entries (entry_id, occurred_at, weight_kg)
            VALUES (%s, %s, %s)
            """,
            (entry.entry_id, entry.occurred_at, entry.weight_kg),
        )

    def add_activity(self, entry: ActivityEntry) -> None:
        self._execute(
            """
            INSERT INTO activity_entries (
                entry_id,
                occurred_at,
                title,
                duration_minutes,
                steps,
                calories_burned,
                intensity
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entry.entry_id,
                entry.occurred_at,
                entry.title,
                entry.duration_minutes,
                entry.steps,
                entry.calories_burned,
                entry.intensity,
            ),
        )

    def build_health_summary(self, target_date: date) -> DailyHealthSummary:
        day_start = datetime.combine(target_date, time.min)
        day_end = datetime.combine(target_date, time.max)

        meals = self._fetchone(
            """
            SELECT COUNT(*) AS meals_count,
                   COALESCE(SUM(calories), 0) AS calories,
                   COALESCE(SUM(protein_g), 0) AS protein_g,
                   COALESCE(SUM(fat_g), 0) AS fat_g,
                   COALESCE(SUM(carbs_g), 0) AS carbs_g
            FROM meals
            WHERE occurred_at BETWEEN %s AND %s
            """,
            (day_start, day_end),
        )
        water = self._fetchone(
            """
            SELECT COALESCE(SUM(amount_ml), 0) AS water_ml
            FROM water_entries
            WHERE occurred_at BETWEEN %s AND %s
            """,
            (day_start, day_end),
        )
        activity = self._fetchone(
            """
            SELECT COALESCE(SUM(steps), 0) AS steps,
                   COALESCE(SUM(duration_minutes), 0) AS activity_minutes
            FROM activity_entries
            WHERE occurred_at BETWEEN %s AND %s
            """,
            (day_start, day_end),
        )
        latest_weight = self._fetchone(
            """
            SELECT weight_kg
            FROM weight_entries
            WHERE occurred_at BETWEEN %s AND %s
            ORDER BY occurred_at DESC
            LIMIT 1
            """,
            (day_start, day_end),
        )
        sleep_rows = self._fetchall(
            """
            SELECT start_at, end_at
            FROM sleep_entries
            WHERE end_at BETWEEN %s AND %s
            """,
            (day_start, day_end),
        )

        sleep_hours = 0.0
        for row in sleep_rows:
            sleep_hours += round((row["end_at"] - row["start_at"]).total_seconds() / 3600, 2)

        return DailyHealthSummary(
            target_date=target_date,
            meals_count=meals["meals_count"],
            calories=meals["calories"],
            protein_g=round(float(meals["protein_g"]), 2),
            fat_g=round(float(meals["fat_g"]), 2),
            carbs_g=round(float(meals["carbs_g"]), 2),
            water_ml=water["water_ml"],
            sleep_hours=round(sleep_hours, 2),
            steps=activity["steps"],
            activity_minutes=activity["activity_minutes"],
            latest_weight_kg=float(latest_weight["weight_kg"]) if latest_weight else None,
            goals=self.get_health_goals(target_date),
        )

    def upsert_decisions(self, decisions: Iterable[DecisionLogEntry]) -> List[DecisionLogEntry]:
        inserted = []
        for decision in decisions:
            rowcount = self._execute(
                """
                INSERT INTO decision_log (
                    decision_id,
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    decision_id = decision_id
                """,
                (
                    decision.decision_id,
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
        status: Optional[DecisionStatus] = None,
        context_date: Optional[date] = None,
    ) -> List[DecisionLogEntry]:
        query = """
            SELECT *
            FROM decision_log
            WHERE 1 = 1
        """
        params = []
        if status is not None:
            query += " AND status = %s"
            params.append(status.value)
        if context_date is not None:
            query += " AND context_date = %s"
            params.append(context_date)
        query += " ORDER BY created_at ASC"
        rows = self._fetchall(query, tuple(params))
        return [self._to_decision(row) for row in rows]

    def update_decision_status(self, decision_id: str, status: DecisionStatus) -> None:
        self._execute(
            "UPDATE decision_log SET status = %s WHERE decision_id = %s",
            (status.value, decision_id),
        )

    def _init_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS health_goals (
                target_date DATE PRIMARY KEY,
                water_ml INT NOT NULL,
                protein_g INT NOT NULL,
                sleep_hours DOUBLE NOT NULL,
                steps INT NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS meals (
                entry_id VARCHAR(64) PRIMARY KEY,
                occurred_at DATETIME(6) NOT NULL,
                title VARCHAR(255) NOT NULL,
                calories INT NOT NULL,
                protein_g DOUBLE NOT NULL,
                fat_g DOUBLE NOT NULL DEFAULT 0,
                carbs_g DOUBLE NOT NULL DEFAULT 0,
                notes TEXT NOT NULL,
                INDEX idx_meals_occurred_at (occurred_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS meal_photo_drafts (
                draft_id VARCHAR(64) PRIMARY KEY,
                created_at DATETIME(6) NOT NULL,
                occurred_at DATETIME(6) NOT NULL,
                title VARCHAR(255) NOT NULL,
                summary TEXT NOT NULL,
                calories INT NOT NULL,
                protein_g DOUBLE NOT NULL,
                fat_g DOUBLE NOT NULL,
                carbs_g DOUBLE NOT NULL,
                confidence DOUBLE NOT NULL,
                photo_file_id VARCHAR(255) NOT NULL,
                photo_unique_id VARCHAR(255) NOT NULL,
                status VARCHAR(64) NOT NULL,
                source VARCHAR(64) NOT NULL,
                items_json LONGTEXT NOT NULL,
                INDEX idx_meal_drafts_created_at (created_at),
                INDEX idx_meal_drafts_status (status)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS water_entries (
                entry_id VARCHAR(64) PRIMARY KEY,
                occurred_at DATETIME(6) NOT NULL,
                amount_ml INT NOT NULL,
                INDEX idx_water_occurred_at (occurred_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS sleep_entries (
                entry_id VARCHAR(64) PRIMARY KEY,
                start_at DATETIME(6) NOT NULL,
                end_at DATETIME(6) NOT NULL,
                quality_score INT NULL,
                notes TEXT NOT NULL,
                INDEX idx_sleep_end_at (end_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS weight_entries (
                entry_id VARCHAR(64) PRIMARY KEY,
                occurred_at DATETIME(6) NOT NULL,
                weight_kg DOUBLE NOT NULL,
                INDEX idx_weight_occurred_at (occurred_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS activity_entries (
                entry_id VARCHAR(64) PRIMARY KEY,
                occurred_at DATETIME(6) NOT NULL,
                title VARCHAR(255) NOT NULL,
                duration_minutes INT NOT NULL,
                steps INT NOT NULL,
                calories_burned INT NOT NULL,
                intensity VARCHAR(32) NOT NULL,
                INDEX idx_activity_occurred_at (occurred_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS decision_log (
                decision_id VARCHAR(64) PRIMARY KEY,
                decision_key VARCHAR(128) NOT NULL UNIQUE,
                created_at DATETIME(6) NOT NULL,
                agent VARCHAR(64) NOT NULL,
                kind VARCHAR(64) NOT NULL,
                title VARCHAR(255) NOT NULL,
                rationale TEXT NOT NULL,
                context_date DATE NOT NULL,
                status VARCHAR(64) NOT NULL,
                payload LONGTEXT NOT NULL,
                INDEX idx_decision_context_date (context_date),
                INDEX idx_decision_status (status)
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
    def _to_meal_draft(row: dict) -> MealPhotoDraft:
        items = [
            FoodItemEstimate(
                title=item["title"],
                portion_text=item["portion_text"],
                calories=item["calories"],
                protein_g=item["protein_g"],
                fat_g=item["fat_g"],
                carbs_g=item["carbs_g"],
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
            confidence=float(row["confidence"]),
            photo_file_id=row["photo_file_id"],
            photo_unique_id=row["photo_unique_id"],
            status=MealDraftStatus(row["status"]),
            source=row["source"],
            items=items,
        )
