"""Money Forward クラウド会計 V3 の読取専用クライアント。

APIキーを短期JWTへ交換し、指定事業者の口座と明細を取得する。
銀行実残高・銀行の最終同期時刻は、このAPIのレスポンスには存在しない。
明細は全仕訳状態・全ページを検証して返し、部分的な取得結果は返さない。
業務上の対象口座、保存、消込、会計仕訳への反映は呼び出し側の責務。

仕様: https://developers.api-accounting.moneyforward.com/v3/openapi.yaml
認証: https://developers.biz.moneyforward.com/docs/common/api-keys/overview/
"""
from __future__ import annotations

import math
import re
import threading
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, List, Optional
from urllib.parse import unquote

import requests

from .. import config


API_BASE_URL = "https://api-accounting.moneyforward.com/api/v3"
TOKEN_EXCHANGE_URL = "https://api.biz.moneyforward.com/auth/exchange"
_STATUSES = {"excluded", "none", "registered", "modified", "new_voucher_attached"}


class MoneyForwardError(RuntimeError):
    """秘密値・URL・レスポンス本文を含めない連携エラー。"""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MoneyForwardClient:
    """APIキー認証による読取専用クライアント。トークンはメモリ内のみ。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        office_code: Optional[str] = None,
        *,
        session=None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        api_key = config.env("MF_API_KEY") if api_key is None else api_key
        office_code = config.env("MF_OFFICE_CODE") if office_code is None else office_code
        if not isinstance(api_key, str) or not api_key.strip() or any(c in api_key for c in "\r\n"):
            raise MoneyForwardError("MF_API_KEY が設定されていません、または形式が不正です。")
        if not isinstance(office_code, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{4}", office_code.strip()):
            raise MoneyForwardError("MF_OFFICE_CODE は XXXX-XXXX 形式で必須です。")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise MoneyForwardError("Money Forward timeout は正の有限数で指定してください。")
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise MoneyForwardError("Money Forward max_retries は 0〜10 で指定してください。")
        self._api_key = api_key.strip()
        self.office_code = office_code.strip()
        self.enabled = True
        self._session = session if session is not None else requests.Session()
        self._clock, self._sleep = clock, sleep
        self._timeout, self._max_retries = timeout, max_retries
        self._token = ""
        self._token_valid_until = 0.0
        self._last_request_at: Dict[str, float] = {}
        self._lock = threading.RLock()

    def get_office(self) -> Dict:
        """設定した事業者の情報。別事業者が返った場合は失敗とする。"""
        result = self._get("offices")
        if result.get("code") != self.office_code:
            raise MoneyForwardError("Money Forward の返却事業者が設定と一致しません。")
        return result

    def list_connected_accounts(self) -> List[Dict]:
        """連携サービスと配下口座。勘定科目IDを銀行口座IDとして扱わない。"""
        accounts = self._get("connected_accounts").get("connected_accounts")
        if not isinstance(accounts, list):
            raise MoneyForwardError("Money Forward 口座一覧の応答形式が不正です。")
        for account in accounts:
            if not isinstance(account, dict) or not self._nonempty_string(account.get("id")):
                raise MoneyForwardError("Money Forward 口座一覧の識別子が不正です。")
            children = account.get("connected_sub_accounts")
            if not isinstance(children, list):
                raise MoneyForwardError("Money Forward 配下口座一覧の応答形式が不正です。")
            if any(not isinstance(child, dict) or not self._nonempty_string(child.get("id")) for child in children):
                raise MoneyForwardError("Money Forward 配下口座の識別子が不正です。")
        return accounts

    def list_transactions(
        self,
        start_date: str,
        end_date: str,
        *,
        connected_sub_account_id: Optional[str] = None,
        connected_account_id: Optional[str] = None,
        per_page: int = 500,
    ) -> List[Dict]:
        """日付窓の全状態・全ページを取得。両口座フィルタの同時指定は禁止。

        IDはAPIが返したURLエンコード済みの値をそのまま渡せる。
        metadata不整合・重複・範囲外明細は、完全取得と誤認しないよう失敗する。
        """
        start, end = self._parse_date(start_date), self._parse_date(end_date)
        if not 0 <= (end - start).days <= 366:
            raise MoneyForwardError("Money Forward の取得期間は昇順で366日差以内にしてください。")
        if type(per_page) is not int or not 10 <= per_page <= 500:
            raise MoneyForwardError("Money Forward per_page は10〜500で指定してください。")
        if connected_account_id is not None and connected_sub_account_id is not None:
            raise MoneyForwardError("Money Forward の口座フィルタは同時指定できません。")
        params = {"start_date": start_date, "end_date": end_date, "per_page": per_page, "order": "asc"}
        for field, value in (("connected_account_id", connected_account_id),
                             ("connected_sub_account_id", connected_sub_account_id)):
            if value is not None:
                if not self._nonempty_string(value):
                    raise MoneyForwardError("Money Forward の口座フィルタが空です。")
                # requestsが再エンコードするため、一度だけ復号する。unquote_plusは使わない。
                params[field] = unquote(value)

        result: List[Dict] = []
        seen = set()
        expected = None
        page = 1
        while True:
            response = self._get("transactions", {**params, "page": page})
            rows, metadata = response.get("transactions"), response.get("metadata")
            if not isinstance(rows, list) or not isinstance(metadata, dict):
                raise MoneyForwardError("Money Forward 明細一覧の応答形式が不正です。")
            count, pages = metadata.get("total_count"), metadata.get("total_pages")
            if type(count) is not int or type(pages) is not int or count < 0 or pages < 0:
                raise MoneyForwardError("Money Forward ページ情報が不正です。")
            if (count > 0 and pages < 1) or (count == 0 and pages > 1) or len(rows) > per_page:
                raise MoneyForwardError("Money Forward ページ情報と件数が一致しません。")
            if expected is None:
                expected = (count, pages)
            elif expected != (count, pages):
                raise MoneyForwardError("Money Forward 取得中に件数が変わりました。対象期間を再取得してください。")
            for row in rows:
                self._validate_transaction(row, start, end, params)
                identity = unquote(row["id"])
                if identity in seen:
                    raise MoneyForwardError("Money Forward のページ間で明細IDが重複しました。対象期間を再取得してください。")
                seen.add(identity)
                result.append(row)
            if page >= pages:
                break
            page += 1
        if len(result) != expected[0]:
            raise MoneyForwardError("Money Forward の全ページ取得件数が一致しません。")
        return result

    def _get(self, resource: str, params: Optional[Dict] = None) -> Dict:
        # 同一clientの並行実行でも、JWT交換と3req/sの制限を共有する。
        with self._lock:
            query = {"office_code": self.office_code, **(params or {})}
            for refresh_attempt in range(2):
                token = self._access_token()
                response = self._send("GET", f"{API_BASE_URL}/{resource}", token, query, "api")
                if response.status_code == 401 and refresh_attempt == 0:
                    response.close()
                    self._token, self._token_valid_until = "", 0.0
                    continue
                return self._object_response(response)
        raise MoneyForwardError("Money Forward の認証に失敗しました。", status_code=401)

    def _access_token(self) -> str:
        if self._token and self._clock() < self._token_valid_until:
            return self._token
        response = self._send("POST", TOKEN_EXCHANGE_URL, self._api_key, None, "auth")
        body = self._object_response(response)
        token, expires = body.get("access_token"), body.get("expires_in")
        token_type = body.get("token_type")
        if (not self._nonempty_string(token) or any(c in token for c in "\r\n")
                or not isinstance(token_type, str) or token_type.lower() != "bearer"
                or type(expires) is not int or expires <= 0):
            raise MoneyForwardError("Money Forward のトークン応答形式が不正です。")
        self._token = token
        self._token_valid_until = self._clock() + expires - min(30.0, expires / 10)
        return token

    def _send(self, method: str, url: str, token: str, params, limiter: str):
        for attempt in range(self._max_retries + 1):
            self._pace(limiter)
            response = None
            try:
                response = self._session.request(
                    method, url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                    params=params, timeout=self._timeout, allow_redirects=False,
                )
            except requests.RequestException:
                # requestsの例外にはURL・ヘッダ等が含まれ得る。外へ持ち出さない。
                pass
            if response is None:
                if attempt < self._max_retries:
                    self._sleep(min(2.0 ** attempt, 60.0))
                    continue
                raise MoneyForwardError("Money Forward への通信に失敗しました。")
            status = response.status_code
            if type(status) is not int or not 100 <= status <= 599:
                response.close()
                raise MoneyForwardError("Money Forward のHTTP応答形式が不正です。")
            if status == 429 or 500 <= status <= 599:
                if attempt < self._max_retries:
                    delay = self._retry_delay(response, attempt)
                    response.close()
                    if delay <= 60:
                        self._sleep(delay)
                        continue
                    raise MoneyForwardError("Money Forward の再試行待ち時間が長いため、後で再取得してください。",
                                            status_code=status)
            return response
        raise MoneyForwardError("Money Forward への通信を完了できませんでした。")

    def _pace(self, limiter: str) -> None:
        # 会計API: 3req/s。APIキー交換: 100req/min。境界誤差に余裕を持たせる。
        interval = 0.35 if limiter == "api" else 0.61
        previous = self._last_request_at.get(limiter)
        if previous is not None:
            wait = interval - (self._clock() - previous)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at[limiter] = self._clock()

    @staticmethod
    def _retry_delay(response, attempt: int) -> float:
        delay = min(2.0 ** attempt, 60.0)
        value = response.headers.get("Retry-After")
        if value:
            try:
                requested = float(value)
            except (TypeError, ValueError):
                try:
                    until = parsedate_to_datetime(value)
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=timezone.utc)
                    requested = (until - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    requested = 0.0
            if math.isfinite(requested):
                delay = max(delay, requested)
        return delay

    @staticmethod
    def _object_response(response) -> Dict:
        try:
            if response.status_code != 200:
                # upstreamのerror.code/messageも未検証文字列として表示しない。
                raise MoneyForwardError(f"Money Forward API が HTTP {response.status_code} を返しました。",
                                        status_code=response.status_code)
            body = None
            try:
                body = response.json()
            except ValueError:
                pass
            if not isinstance(body, dict):
                raise MoneyForwardError("Money Forward のJSON応答形式が不正です。")
            return body
        finally:
            response.close()

    @staticmethod
    def _nonempty_string(value) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _parse_date(value: str) -> date:
        parsed = None
        if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                pass
        if parsed is None:
            raise MoneyForwardError("Money Forward の日付は有効な YYYY-MM-DD で指定してください。")
        return parsed

    def _validate_transaction(self, row, start: date, end: date, params: Dict) -> None:
        if not isinstance(row, dict):
            raise MoneyForwardError("Money Forward 明細の応答形式が不正です。")
        if not self._nonempty_string(row.get("id")) or not self._nonempty_string(row.get("connected_account_id")):
            raise MoneyForwardError("Money Forward 明細の識別子が不正です。")
        if "connected_sub_account_id" not in row or (row["connected_sub_account_id"] is not None
                                                      and not self._nonempty_string(row["connected_sub_account_id"])):
            raise MoneyForwardError("Money Forward 明細の口座識別子が不正です。")
        if (type(row.get("value")) is not int or row["value"] <= 0
                or not isinstance(row.get("side"), str) or row["side"] not in {"INCOME", "EXPENSE"}):
            raise MoneyForwardError("Money Forward 明細の金額または収支区分が不正です。")
        if not isinstance(row.get("journalizing_status"), str) or row["journalizing_status"] not in _STATUSES:
            raise MoneyForwardError("Money Forward 明細の仕訳状態が不正です。")
        if not isinstance(row.get("voucher_file_ids"), list) or any(not isinstance(v, str) for v in row["voucher_file_ids"]):
            raise MoneyForwardError("Money Forward 明細の証憑ID一覧が不正です。")
        if any(row.get(key) is not None and not isinstance(row[key], str) for key in ("content", "memo")):
            raise MoneyForwardError("Money Forward 明細のテキスト形式が不正です。")
        if not start <= self._parse_date(row.get("date")) <= end:
            raise MoneyForwardError("Money Forward の返却明細が取得期間外です。")
        for field in ("connected_account_id", "connected_sub_account_id"):
            if field in params and (row.get(field) is None or unquote(row[field]) != params[field]):
                raise MoneyForwardError("Money Forward の返却明細が指定口座と一致しません。")
