"""Money Forward クラウド会計 連携クライアント（将来対応・現在はstub）。

現時点では銀行CSV・現金CSVを正規データソースとする。
将来 OAuth2.0 でトークン取得→事業者/会計期間選択→仕訳/明細取得 へ差し替える。
インターフェースのみ定義し、未実装メソッドは NotImplementedError を送出する。
"""
from __future__ import annotations

from typing import Dict, List

from .. import config


class MoneyForwardClient:
    """Money Forward API クライアント（stub）。"""

    def __init__(self) -> None:
        self.client_id = config.env("MF_CLIENT_ID")
        self.client_secret = config.env("MF_CLIENT_SECRET")
        self.redirect_uri = config.env("MF_REDIRECT_URI")
        self.company_id = config.env("MF_COMPANY_ID")
        self.enabled = bool(self.client_id and self.client_secret)

    # --- OAuth 2.0 ---
    def authorize_url(self) -> str:
        raise NotImplementedError("Money Forward OAuth は未実装です（将来対応）。")

    def exchange_code(self, code: str) -> Dict:
        raise NotImplementedError("Money Forward token交換は未実装です（将来対応）。")

    def save_token(self, token: Dict) -> None:
        raise NotImplementedError("Money Forward token保存は未実装です（将来対応）。")

    # --- マスタ・データ取得 ---
    def list_companies(self) -> List[Dict]:
        raise NotImplementedError("事業者一覧取得は未実装です（将来対応）。")

    def list_accounts(self) -> List[Dict]:
        raise NotImplementedError("勘定科目マスタ取得は未実装です（将来対応）。")

    def list_sub_accounts(self) -> List[Dict]:
        raise NotImplementedError("補助科目マスタ取得は未実装です（将来対応）。")

    def fetch_transactions(self, month: str) -> List[Dict]:
        raise NotImplementedError("取引明細取得は未実装です（将来対応）。")

    def create_journals(self, entries: List[Dict]) -> Dict:
        raise NotImplementedError("仕訳作成/同期は未実装です（将来対応）。")
