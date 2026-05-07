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


@dataclass(frozen=True)
class AppUser:
    user_id: int
    telegram_user_id: int
    chat_id: int
    username: str = ""
    first_name: str = ""
    status: UserStatus = UserStatus.ACTIVE
    is_admin: bool = False
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class InviteCode:
    code: str
    created_by_user_id: int
    created_at: datetime
    expires_at: Optional[datetime] = None
    max_uses: int = 1
    used_count: int = 0
    status: InviteStatus = InviteStatus.ACTIVE

