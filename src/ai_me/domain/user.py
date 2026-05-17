from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class UserStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"


class InviteStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXHAUSTED = "exhausted"
    EXPIRED = "expired"


class UserSex(str, Enum):
    MALE = "male"
    FEMALE = "female"


class UserGoal(str, Enum):
    MAINTENANCE = "maintenance"
    WEIGHT_LOSS = "weight_loss"
    MASS_GAIN = "mass_gain"


@dataclass(frozen=True)
class AppUser:
    user_id: int
    telegram_user_id: int
    chat_id: int
    username: str = ""
    first_name: str = ""
    status: UserStatus = UserStatus.ACTIVE
    is_admin: bool = False
    admin_mode_enabled: bool = True
    sex: Optional[UserSex] = None
    age_years: Optional[int] = None
    height_cm: Optional[int] = None
    profile_weight_kg: Optional[float] = None
    goal: Optional[UserGoal] = None
    target_water_ml: int = 2000
    target_protein_g: int = 120
    target_calories_min: Optional[int] = None
    target_calories_max: Optional[int] = None
    reminders_enabled: bool = False
    reminder_meal_logging: bool = False
    reminder_water: bool = False
    reminder_evening_summary: bool = False
    created_at: Optional[datetime] = None

    @property
    def has_admin_access(self) -> bool:
        return self.is_admin and self.admin_mode_enabled


@dataclass(frozen=True)
class InviteCode:
    code: str
    created_by_user_id: int
    created_at: datetime
    expires_at: Optional[datetime] = None
    max_uses: int = 1
    used_count: int = 0
    status: InviteStatus = InviteStatus.ACTIVE
