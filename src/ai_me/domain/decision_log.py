from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Dict


class DecisionKind(str, Enum):
    RECOMMENDATION = "recommendation"
    ALERT = "alert"
    CONFIRMATION_REQUIRED = "confirmation_required"


class DecisionStatus(str, Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXECUTED = "executed"
    DISMISSED = "dismissed"


@dataclass(frozen=True)
class DecisionLogEntry:
    decision_id: str
    decision_key: str
    created_at: datetime
    agent: str
    kind: DecisionKind
    title: str
    rationale: str
    context_date: date
    status: DecisionStatus = DecisionStatus.OPEN
    payload: Dict[str, str] = field(default_factory=dict)
