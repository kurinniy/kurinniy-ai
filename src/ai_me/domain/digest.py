from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional

from ai_me.domain.food import MealMedia


class DigestType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


class DigestStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class UserDigestSettings:
    user_id: int
    timezone_name: str = "Europe/Moscow"
    daily_digest_enabled: bool = True
    daily_digest_time: str = "08:00"
    weekly_digest_enabled: bool = True
    weekly_digest_time: str = "08:00"
    weekly_digest_weekday: int = 0


@dataclass(frozen=True)
class DigestRun:
    run_id: str
    user_id: int
    digest_type: DigestType
    digest_date: date
    status: DigestStatus
    created_at: datetime
    scheduled_for: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    error_message: str = ""
    payload: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DigestMealSnapshot:
    meal_entry_id: str
    occurred_at: datetime
    title: str
    calories: int
    protein_g: float
    fat_g: float
    carbs_g: float
    media_items: List[MealMedia] = field(default_factory=list)


@dataclass(frozen=True)
class DigestTrendWindow:
    days: int
    average_calories: float
    average_protein_g: float
    average_fat_g: float
    average_carbs_g: float
    average_meals_count: float
    days_with_meals: int


@dataclass(frozen=True)
class DailyFoodDigest:
    user_id: int
    digest_date: date
    meals: List[DigestMealSnapshot]
    total_calories: int
    total_protein_g: float
    total_fat_g: float
    total_carbs_g: float
    trend_windows: List[DigestTrendWindow] = field(default_factory=list)
    commentary: str = ""


@dataclass(frozen=True)
class WeeklyDigestHighlight:
    digest_date: date
    meal: Optional[DigestMealSnapshot]
    score: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class WeeklyFoodDigest:
    user_id: int
    week_start: date
    week_end: date
    highlights: List[WeeklyDigestHighlight]
    total_meals: int
    total_calories: int
    commentary: str = ""
