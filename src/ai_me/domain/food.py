from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from ai_me.domain.health import MealEntry


class MealDraftStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class PhotoLogKind(str, Enum):
    MEAL = "meal"
    WATER = "water"


MEAL_PHOTO_SOURCE = "telegram_photo"
WATER_PHOTO_SOURCE = "telegram_water_photo"


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
    source: str = MEAL_PHOTO_SOURCE
    items: List[FoodItemEstimate] = field(default_factory=list)
    water_ml: int = 0

    @property
    def is_water_only(self) -> bool:
        return self.source == WATER_PHOTO_SOURCE or (
            self.water_ml > 0
            and self.calories <= 0
            and self.protein_g <= 0
            and self.fat_g <= 0
            and self.carbs_g <= 0
        )


@dataclass(frozen=True)
class PhotoLogResult:
    entry_id: str
    kind: PhotoLogKind
    title: str
    occurred_at: datetime
    water_ml: int = 0
    meal_entry: Optional[MealEntry] = None


@dataclass(frozen=True)
class PhotoProcessingResult:
    draft: Optional[MealPhotoDraft] = None
    photo_log: Optional[PhotoLogResult] = None


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
    storage_kind: str = "railway_bucket"
    storage_key: str = ""
    bucket_name: str = ""
    width: int = 0
    height: int = 0
