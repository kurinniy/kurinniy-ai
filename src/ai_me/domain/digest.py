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
class DigestComparison:
    days: int
    calories_delta_pct: Optional[float] = None
    protein_delta_pct: Optional[float] = None
    fat_delta_pct: Optional[float] = None
    carbs_delta_pct: Optional[float] = None


@dataclass(frozen=True)
class DigestMealFeature:
    title: str
    time_text: str
    calories: int = 0
    protein_g: float = 0.0
    share_of_day_pct: float = 0.0


@dataclass(frozen=True)
class DailyDigestMealPattern:
    first_meal_time: str = ""
    last_meal_time: str = ""
    eating_window_hours: float = 0.0
    largest_meal: Optional[DigestMealFeature] = None
    protein_leader: Optional[DigestMealFeature] = None


@dataclass(frozen=True)
class DailyDigestMacroBalance:
    protein_share_pct: float = 0.0
    fat_share_pct: float = 0.0
    carbs_share_pct: float = 0.0
    protein_density_g_per_1000kcal: float = 0.0


@dataclass(frozen=True)
class DigestStreak:
    direction: str = "flat"
    days: int = 0


@dataclass(frozen=True)
class DailyDigestStability:
    calories_streak: DigestStreak = field(default_factory=DigestStreak)
    protein_streak: DigestStreak = field(default_factory=DigestStreak)


@dataclass(frozen=True)
class DailyDigestFlags:
    late_heavy_meal: bool = False
    high_fat_day: bool = False
    low_meal_count: bool = False
    protein_good: bool = False


@dataclass(frozen=True)
class DailyDigestCommentaryData:
    comparisons: List[DigestComparison] = field(default_factory=list)
    meal_pattern: DailyDigestMealPattern = field(default_factory=DailyDigestMealPattern)
    macro_balance: DailyDigestMacroBalance = field(default_factory=DailyDigestMacroBalance)
    stability: DailyDigestStability = field(default_factory=DailyDigestStability)
    flags: DailyDigestFlags = field(default_factory=DailyDigestFlags)


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
    commentary_data: DailyDigestCommentaryData = field(default_factory=DailyDigestCommentaryData)
    commentary: str = ""


@dataclass(frozen=True)
class WeeklyDigestHighlight:
    digest_date: date
    meal: Optional[DigestMealSnapshot]
    score: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class WeeklyDigestDayMetric:
    date_text: str
    calories: int = 0
    protein_g: float = 0.0


@dataclass(frozen=True)
class WeeklyDigestHighlightSummary:
    highest_calorie_day: Optional[WeeklyDigestDayMetric] = None
    highest_protein_day: Optional[WeeklyDigestDayMetric] = None
    most_distinct_meal_title: str = ""
    most_distinct_meal_date: str = ""
    most_distinct_meal_reason: str = ""


@dataclass(frozen=True)
class WeeklyDigestPatterns:
    late_heavy_dinners_days: int = 0
    high_protein_days: int = 0
    low_calorie_days: int = 0
    most_variable_macro: str = ""


@dataclass(frozen=True)
class WeeklyDigestConsistency:
    daily_calorie_cv: float = 0.0
    is_more_stable_than_prev_week: bool = False


@dataclass(frozen=True)
class WeeklyDigestFlags:
    week_heavier_than_usual: bool = False
    protein_stable: bool = False
    evening_overload_pattern: bool = False


@dataclass(frozen=True)
class WeeklyDigestCommentaryData:
    days_with_meals: int = 0
    average_daily_calories: float = 0.0
    average_daily_protein_g: float = 0.0
    comparisons: List[DigestComparison] = field(default_factory=list)
    patterns: WeeklyDigestPatterns = field(default_factory=WeeklyDigestPatterns)
    highlights: WeeklyDigestHighlightSummary = field(default_factory=WeeklyDigestHighlightSummary)
    consistency: WeeklyDigestConsistency = field(default_factory=WeeklyDigestConsistency)
    flags: WeeklyDigestFlags = field(default_factory=WeeklyDigestFlags)


@dataclass(frozen=True)
class WeeklyFoodDigest:
    user_id: int
    week_start: date
    week_end: date
    highlights: List[WeeklyDigestHighlight]
    total_meals: int
    total_calories: int
    commentary_data: WeeklyDigestCommentaryData = field(default_factory=WeeklyDigestCommentaryData)
    commentary: str = ""
