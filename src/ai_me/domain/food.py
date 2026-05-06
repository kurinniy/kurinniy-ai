from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List


class MealDraftStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class FoodItemEstimate:
    title: str
    portion_text: str
    calories: int
    protein_g: float
    fat_g: float
    carbs_g: float


@dataclass(frozen=True)
class MealPhotoDraft:
    draft_id: str
    created_at: datetime
    occurred_at: datetime
    title: str
    summary: str
    calories: int
    protein_g: float
    fat_g: float
    carbs_g: float
    confidence: float
    photo_file_id: str
    photo_unique_id: str
    status: MealDraftStatus = MealDraftStatus.PENDING
    source: str = "telegram_photo"
    items: List[FoodItemEstimate] = field(default_factory=list)

