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
    water_ml: int = 0


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
    water_ml: int = 0


@dataclass(frozen=True)
class MealMedia:
    media_id: str
    user_id: int
    draft_id: str
    occurred_at: datetime
    created_at: datetime
    mime_type: str
    telegram_file_id: str
    telegram_unique_id: str
    byte_size: int
    sha256: str
    image_bytes: bytes
    meal_entry_id: str = ""
    storage_kind: str = "db_blob"
