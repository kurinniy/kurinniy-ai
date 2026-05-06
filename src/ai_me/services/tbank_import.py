import csv
import hashlib
import json
from datetime import datetime
from io import StringIO
from typing import Dict, Iterable, List, Optional, Tuple

from ai_me.domain.finance import FinanceTransaction


class TBankCSVImporter:
    PROVIDER = "tbank"
    DATE_HEADERS = ("Дата операции", "Дата", "Дата платежа", "Дата авторизации")
    TIME_HEADERS = ("Время операции", "Время", "Время платежа")
    AMOUNT_HEADERS = ("Сумма платежа", "Сумма операции", "Сумма")
    INCOME_HEADERS = ("Пополнение", "Приход", "Сумма пополнения")
    OUTCOME_HEADERS = ("Списание", "Расход", "Сумма списания")
    TITLE_HEADERS = ("Описание", "Операция", "Детали", "Контрагент", "Название")
    CATEGORY_HEADERS = ("Категория",)
    MCC_HEADERS = ("MCC", "MCC-код", "MCC код")
    STATUS_HEADERS = ("Статус",)
    CURRENCY_HEADERS = ("Валюта платежа", "Валюта операции", "Валюта")
    ACCOUNT_HEADERS = ("Продукт", "Счет", "Карта", "Номер карты")
    DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d")
    DATETIME_FORMATS = (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%y %H:%M:%S",
        "%d.%m.%y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    )

    def parse(self, file_bytes: bytes, source_file_name: str = "") -> List[FinanceTransaction]:
        text = self._decode(file_bytes)
        delimiter = self._detect_delimiter(text)
        rows = list(csv.DictReader(StringIO(text), delimiter=delimiter))
        if not rows:
            return []

        transactions = []
        for row in rows:
            normalized = self._normalize_row(row)
            if self._is_empty_row(normalized):
                continue
            transactions.append(self._build_transaction(normalized, source_file_name=source_file_name))
        return transactions

    def _build_transaction(self, row: Dict[str, str], source_file_name: str) -> FinanceTransaction:
        occurred_at = self._parse_datetime(row)
        amount = self._parse_amount(row)
        title = self._get_value(row, self.TITLE_HEADERS) or "Операция Т-Банка"
        category = self._get_value(row, self.CATEGORY_HEADERS)
        mcc = self._get_value(row, self.MCC_HEADERS)
        status = self._get_value(row, self.STATUS_HEADERS)
        currency = self._get_value(row, self.CURRENCY_HEADERS) or "RUB"
        account_name = self._get_value(row, self.ACCOUNT_HEADERS)
        transaction_key = self._make_transaction_key(
            occurred_at=occurred_at,
            amount=amount,
            currency=currency,
            title=title,
            category=category,
            mcc=mcc,
            status=status,
            account_name=account_name,
        )
        return FinanceTransaction(
            transaction_key=transaction_key,
            provider=self.PROVIDER,
            occurred_at=occurred_at,
            amount=amount,
            currency=currency,
            title=title,
            category=category,
            mcc=mcc,
            status=status,
            account_name=account_name,
            source_file_name=source_file_name,
            raw_payload=json.dumps(row, ensure_ascii=False, sort_keys=True),
        )

    def _parse_datetime(self, row: Dict[str, str]) -> datetime:
        date_value = self._get_value(row, self.DATE_HEADERS)
        if not date_value:
            raise ValueError("Не найден столбец с датой операции")
        for fmt in self.DATETIME_FORMATS:
            try:
                return datetime.strptime(date_value, fmt)
            except ValueError:
                continue
        time_value = self._get_value(row, self.TIME_HEADERS)
        if time_value:
            candidate = "%s %s" % (date_value, time_value)
            for fmt in self.DATETIME_FORMATS:
                try:
                    return datetime.strptime(candidate, fmt)
                except ValueError:
                    continue
        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(date_value, fmt)
            except ValueError:
                continue
        raise ValueError("Не удалось разобрать дату операции: %s" % date_value)

    def _parse_amount(self, row: Dict[str, str]) -> float:
        direct_amount = self._get_value(row, self.AMOUNT_HEADERS)
        if direct_amount:
            return self._parse_amount_value(direct_amount)

        income_value = self._get_value(row, self.INCOME_HEADERS)
        outcome_value = self._get_value(row, self.OUTCOME_HEADERS)
        if income_value:
            return abs(self._parse_amount_value(income_value))
        if outcome_value:
            return -abs(self._parse_amount_value(outcome_value))
        raise ValueError("Не найден столбец с суммой операции")

    @staticmethod
    def _parse_amount_value(value: str) -> float:
        cleaned = (
            value.replace("\u00a0", "")
            .replace("\u202f", "")
            .replace("₽", "")
            .replace("руб.", "")
            .replace("RUB", "")
            .replace(" ", "")
            .strip()
        )
        if not cleaned:
            raise ValueError("Сумма операции пустая")
        normalized = cleaned.replace(",", ".")
        allowed = "".join(char for char in normalized if char.isdigit() or char in ".-")
        if not allowed or allowed in {"-", ".", "-."}:
            raise ValueError("Не удалось разобрать сумму операции: %s" % value)
        return round(float(allowed), 2)

    @classmethod
    def _make_transaction_key(
        cls,
        occurred_at: datetime,
        amount: float,
        currency: str,
        title: str,
        category: str,
        mcc: str,
        status: str,
        account_name: str,
    ) -> str:
        raw = json.dumps(
            {
                "provider": cls.PROVIDER,
                "occurred_at": occurred_at.isoformat(),
                "amount": amount,
                "currency": currency,
                "title": title,
                "category": category,
                "mcc": mcc,
                "status": status,
                "account_name": account_name,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _decode(file_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                return file_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("Не удалось декодировать файл. Ожидается CSV в UTF-8 или CP1251")

    @staticmethod
    def _detect_delimiter(text: str) -> str:
        sample = "\n".join(text.splitlines()[:5])
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
            return dialect.delimiter
        except csv.Error:
            return ";"

    @staticmethod
    def _normalize_row(row: Dict[str, object]) -> Dict[str, str]:
        normalized = {}
        for key, value in row.items():
            normalized_key = str(key).strip() if key is not None else ""
            normalized_value = str(value).strip() if value is not None else ""
            normalized[normalized_key] = normalized_value
        return normalized

    @classmethod
    def _get_value(cls, row: Dict[str, str], headers: Iterable[str]) -> str:
        lowered = {key.casefold(): value for key, value in row.items()}
        for header in headers:
            value = lowered.get(header.casefold(), "")
            if value:
                return value
        return ""

    @staticmethod
    def _is_empty_row(row: Dict[str, str]) -> bool:
        return not any(value for value in row.values())
