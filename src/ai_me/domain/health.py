from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass(frozen=True)
class DailyHealthGoals:
    target_date: date
    water_ml: int = 2000
    protein_g: int = 120
    sleep_hours: float = 8.0
    steps: int = 8000


@dataclass(frozen=True)
class MealEntry:
    entry_id: str
    occurred_at: datetime
    title: str
    calories: int
    protein_g: float
    fat_g: float = 0.0
    carbs_g: float = 0.0
    notes: str = ""


@dataclass(frozen=True)
class WaterEntry:
    entry_id: str
    occurred_at: datetime
    amount_ml: int


@dataclass(frozen=True)
class SleepEntry:
    entry_id: str
    start_at: datetime
    end_at: datetime
    quality_score: Optional[int] = None
    notes: str = ""

    @property
    def duration_hours(self) -> float:
        delta = self.end_at - self.start_at
        return round(delta.total_seconds() / 3600, 2)


@dataclass(frozen=True)
class WeightEntry:
    entry_id: str
    occurred_at: datetime
    weight_kg: float


@dataclass(frozen=True)
class ActivityEntry:
    entry_id: str
    occurred_at: datetime
    title: str
    duration_minutes: int
    steps: int = 0
    calories_burned: int = 0
    intensity: str = "moderate"


@dataclass(frozen=True)
class DailyHealthSummary:
    target_date: date
    meals_count: int
    calories: int
    protein_g: float
    fat_g: float
    carbs_g: float
    water_ml: int
    sleep_hours: float
    steps: int
    activity_minutes: int
    latest_weight_kg: Optional[float]
    goals: DailyHealthGoals
