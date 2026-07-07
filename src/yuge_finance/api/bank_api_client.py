"""銀行API クライアント（将来対応・現在はstub）。

当面は imports/bank/ へのCSVドロップ運用。
将来、銀行API直連携に差し替える際の窓口。
"""
from __future__ import annotations

from typing import List

from .. import config
from ..normalize.schema import BankTransaction


class BankApiClient:
    def __init__(self) -> None:
        self.token = config.env("BANK_API_TOKEN")
        self.enabled = bool(self.token)

    def fetch_transactions(self, month: str) -> List[BankTransaction]:
        raise NotImplementedError(
            "銀行API直連携は未実装です。現在は imports/bank/ のCSVドロップ運用です。"
        )
