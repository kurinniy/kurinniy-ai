import unittest

from ai_me.services.health_service import HealthService
from ai_me.services.tbank_import import TBankCSVImporter
from ai_me.storage.memory import InMemoryStore


class TBankCSVImporterTest(unittest.TestCase):
    def test_parse_real_tbank_export_format(self) -> None:
        file_bytes = (
            '\ufeff"Дата операции";"Дата платежа";"Номер карты";"Статус";"Сумма операции";'
            '"Валюта операции";"Сумма платежа";"Валюта платежа";"Кэшбэк";"Категория";"MCC";'
            '"Описание";"Бонусы (включая кэшбэк)";"Округление на инвесткопилку";'
            '"Сумма операции с округлением"\n'
            '"06.05.2026 14:57:22";"06.05.2026";"";"OK";"-59,99";"RUB";"-59,99";"RUB";"";'
            '"Супермаркеты";"5411";"Перекрёсток";"0,00";"0,00";"-59,99"\n'
        ).encode("utf-8")

        transactions = TBankCSVImporter().parse(file_bytes=file_bytes, source_file_name="operations.csv")

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].occurred_at, __import__("datetime").datetime(2026, 5, 6, 14, 57, 22))
        self.assertEqual(transactions[0].currency, "RUB")
        self.assertEqual(transactions[0].account_name, "")
        self.assertEqual(transactions[0].title, "Перекрёсток")
        self.assertEqual(transactions[0].amount, -59.99)

    def test_parse_semicolon_csv(self) -> None:
        file_bytes = (
            "\ufeffДата операции;Время операции;Сумма платежа;Описание;Категория;MCC;Статус;Продукт\n"
            "01.05.2026;09:15;-1500,50;Перекресток;Продукты;5411;OK;Black\n"
            "02.05.2026;18:30;25000;Зарплата;Доход;0000;OK;Black\n"
        ).encode("utf-8")

        transactions = TBankCSVImporter().parse(file_bytes=file_bytes, source_file_name="tbank.csv")

        self.assertEqual(len(transactions), 2)
        self.assertEqual(transactions[0].title, "Перекресток")
        self.assertEqual(transactions[0].amount, -1500.5)
        self.assertEqual(transactions[0].category, "Продукты")
        self.assertEqual(transactions[0].mcc, "5411")
        self.assertEqual(transactions[1].amount, 25000.0)

    def test_parse_cp1251_csv(self) -> None:
        file_bytes = (
            "Дата операции;Сумма платежа;Описание\n"
            "03.05.2026;-990;Такси\n"
        ).encode("cp1251")

        transactions = TBankCSVImporter().parse(file_bytes=file_bytes, source_file_name="tbank.csv")

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].title, "Такси")


class TBankImportServiceTest(unittest.TestCase):
    def test_import_tbank_csv_deduplicates_repeated_import(self) -> None:
        store = InMemoryStore()
        service = HealthService(store=store)
        file_bytes = (
            "\ufeffДата операции;Время операции;Сумма платежа;Описание\n"
            "01.05.2026;09:15;-1500,50;Перекресток\n"
            "02.05.2026;18:30;25000;Зарплата\n"
        ).encode("utf-8")

        first = service.import_tbank_csv(file_bytes=file_bytes, source_file_name="tbank.csv")
        second = service.import_tbank_csv(file_bytes=file_bytes, source_file_name="tbank.csv")

        self.assertEqual(first.total_rows, 2)
        self.assertEqual(first.imported_rows, 2)
        self.assertEqual(first.skipped_rows, 0)
        self.assertEqual(second.imported_rows, 0)
        self.assertEqual(second.skipped_rows, 2)

    def test_finance_monthly_summary_aggregates_income_expenses_and_categories(self) -> None:
        store = InMemoryStore()
        service = HealthService(store=store)
        file_bytes = (
            "\ufeffДата операции;Время операции;Сумма платежа;Описание;Категория\n"
            "01.05.2026;09:15;-1500,50;Перекресток;Продукты\n"
            "02.05.2026;10:00;-700,00;Яндекс Go;Такси\n"
            "03.05.2026;18:30;25000;Зарплата;Доход\n"
            "04.05.2026;12:00;-800,00;Вкусвилл;Продукты\n"
        ).encode("utf-8")

        service.import_tbank_csv(file_bytes=file_bytes, source_file_name="tbank.csv")
        summary = service.get_finance_monthly_summary(__import__("datetime").date(2026, 5, 1))

        self.assertEqual(summary.transaction_count, 4)
        self.assertEqual(summary.income_total, 25000.0)
        self.assertEqual(summary.expense_total, 3000.5)
        self.assertEqual(summary.net_total, 21999.5)
        self.assertEqual(summary.top_expense_categories[0].category, "Продукты")
        self.assertEqual(summary.top_expense_categories[0].amount, 2300.5)
        self.assertEqual(summary.top_expense_categories[1].category, "Такси")


if __name__ == "__main__":
    unittest.main()
