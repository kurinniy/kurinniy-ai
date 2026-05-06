from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional


@dataclass(frozen=True)
class FinanceTransaction:
    transaction_key: str
    provider: str
    occurred_at: datetime
    amount: float
    currency: str
    title: str
    category: str = ""
    mcc: str = ""
    status: str = ""
    account_name: str = ""
    source_file_name: str = ""
    raw_payload: str = ""


@dataclass(frozen=True)
class FinanceImportResult:
    provider: str
    source_file_name: str
    total_rows: int
    imported_rows: int
    skipped_rows: int
    first_operation_at: Optional[datetime] = None
    last_operation_at: Optional[datetime] = None


@dataclass(frozen=True)
class FinanceCategoryTotal:
    category: str
    amount: float
    transaction_count: int


@dataclass(frozen=True)
class FinanceMonthlySummary:
    month_start: date
    month_end: date
    transaction_count: int
    income_total: float
    expense_total: float
    net_total: float
    top_expense_categories: List[FinanceCategoryTotal]
