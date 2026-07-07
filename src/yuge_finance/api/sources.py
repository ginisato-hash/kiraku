"""PaymentSource 抽象（将来のデータソース差し替え用）。

現時点の正規ソース:
  - 売上     : Beds24Source
  - 入出金   : BankCsvSource (CSVドロップ)
  - 現金     : CashReceiptSource (CSVドロップ)
将来差し替え候補:
  - MoneyForwardSource / BankApiSource (現在stub)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class PaymentSource(ABC):
    """入出金/売上データソースの共通インターフェース。"""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """このソースが利用可能か（認証/ファイル有無）。"""

    @abstractmethod
    def fetch(self, month: str) -> List:
        """対象月の正規化済みレコードを返す。"""


class BankCsvSource(PaymentSource):
    name = "bank_csv"

    def is_available(self) -> bool:
        from .. import config
        return any((config.IMPORTS_DIR / "bank").glob("*.csv"))

    def fetch(self, month: str) -> List:
        from ..ingest import bank_csv
        return bank_csv.load(month)


class CashReceiptSource(PaymentSource):
    name = "cash_receipt"

    def is_available(self) -> bool:
        from .. import config
        base = config.IMPORTS_DIR / "cash_receipts"
        return any(base.glob("csv/*.csv")) or any(base.glob("reviewed/*.csv"))

    def fetch(self, month: str) -> List:
        from ..ingest import cash_receipt_csv
        return cash_receipt_csv.load(month)


class Beds24Source(PaymentSource):
    name = "beds24"

    def is_available(self) -> bool:
        from .. import config
        return bool(config.env("BEDS24_LONG_LIFE_TOKEN")
                    or config.env("BEDS24_REFRESH_TOKEN")
                    or config.env("BEDS24_ACCESS_TOKEN"))

    def fetch(self, month: str) -> List:
        from .beds24_client import Beds24Client
        from .. import config
        return Beds24Client().fetch_month(month, config.DATA_DIR / "raw" / "beds24" / month)


class MoneyForwardSource(PaymentSource):
    name = "money_forward"

    def is_available(self) -> bool:
        return False  # stub

    def fetch(self, month: str) -> List:
        raise NotImplementedError("MoneyForwardSource は未実装です（将来対応）。")


class BankApiSource(PaymentSource):
    name = "bank_api"

    def is_available(self) -> bool:
        return False  # stub

    def fetch(self, month: str) -> List:
        raise NotImplementedError("BankApiSource は未実装です（将来対応）。")
