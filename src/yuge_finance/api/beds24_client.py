"""Beds24 API v2 クライアント（喜らく単体・売上の唯一の正規ソース）。

認証: refresh token から access token を取得。
取得: 対象月の予約を arrival(チェックイン)日でフィルタして取得。
正規化: BookingRecord へ変換（normalize_booking は純粋関数でテスト可能）。
"""
from __future__ import annotations

import calendar
import json
from pathlib import Path
from typing import Dict, List, Optional

import requests

from .. import config
from ..normalize.schema import BookingRecord


class Beds24Error(RuntimeError):
    pass


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def normalize_booking(raw: dict, property_name_default: str = "喜らく") -> BookingRecord:
    """Beds24 の生予約JSON を BookingRecord へ正規化（純粋関数）。"""
    arrival = str(_first(raw, "arrival", "checkin_date", "checkIn", default=""))
    departure = str(_first(raw, "departure", "checkout_date", "checkOut", default=""))
    nights = 0
    try:
        if arrival and departure:
            from datetime import date
            a = date.fromisoformat(arrival[:10])
            d = date.fromisoformat(departure[:10])
            nights = max((d - a).days, 0)
    except ValueError:
        nights = int(_first(raw, "stay_nights", "numNights", default=0) or 0)

    guest = _first(raw, "guest_name", default=None)
    if not guest:
        guest = " ".join(
            str(x) for x in [raw.get("firstName"), raw.get("lastName")] if x
        ).strip()

    gross = float(_first(raw, "gross_revenue", "price", "totalPrice", default=0) or 0)
    commission = float(_first(raw, "ota_commission", "commission", default=0) or 0)
    net = _first(raw, "net_revenue", default=None)
    net = float(net) if net not in (None, "") else gross - commission

    rec = BookingRecord(
        booking_id=str(_first(raw, "booking_id", "id", "bookId", default="")),
        property_id=str(_first(raw, "property_id", "propertyId", default="")),
        property_name=str(_first(raw, "property_name", "propertyName",
                                 default=property_name_default)),
        room_id=str(_first(raw, "room_id", "roomId", default="")),
        room_name=str(_first(raw, "room_name", "roomName", default="")),
        # 実OTA名は refererEditable に入る（channelフィールドは direct/booking で不正確）
        channel=str(_first(raw, "refererEditable", "channel", "apiSource",
                           "referer", "source", default="直販")),
        guest_name=guest,
        booking_date=str(_first(raw, "booking_date", "bookingTime", "bookingDate", default=""))[:10],
        checkin_date=arrival[:10],
        checkout_date=departure[:10],
        stay_nights=nights,
        rooms=int(_first(raw, "rooms", "numRooms", default=1) or 1),
        guests=int(float(_first(raw, "guests", "numAdult", default=0) or 0))
        + int(float(raw.get("numChild", 0) or 0)),
        gross_revenue=gross,
        ota_commission=commission,
        net_revenue=net,
        tax_amount=float(_first(raw, "tax_amount", "tax", default=0) or 0),
        status=str(_first(raw, "status", default="")).lower(),
        payment_status=str(_first(raw, "payment_status", "paymentStatus", default="")),
        invoice_status=str(_first(raw, "invoice_status", "invoiceStatus", default="")),
    )
    return rec.finalize()


class Beds24Client:
    def __init__(self) -> None:
        self.cfg = config.load_yaml("beds24.yml")["api"]
        self.base = config.env("BEDS24_API_BASE", "https://beds24.com/api/v2").rstrip("/")
        self.long_life_token = config.env("BEDS24_LONG_LIFE_TOKEN")
        self.refresh_token = config.env("BEDS24_REFRESH_TOKEN")
        self.access_token = config.env("BEDS24_ACCESS_TOKEN")
        ids = config.env("BEDS24_PROPERTY_IDS")
        self.property_ids = [p.strip() for p in ids.split(",") if p.strip()]
        self.timeout = self.cfg.get("timeout_seconds", 30)

    def _ensure_token(self) -> str:
        # Long Life Token を最優先で使用（refresh不要）
        if self.long_life_token:
            return self.long_life_token
        if self.access_token:
            return self.access_token
        if not self.refresh_token:
            raise Beds24Error(
                "Beds24 認証情報が未設定です。.env に BEDS24_LONG_LIFE_TOKEN "
                "（推奨）または BEDS24_REFRESH_TOKEN / BEDS24_ACCESS_TOKEN を設定してください。"
            )
        # フォールバック: refresh token から access token を取得
        resp = requests.get(
            f"{self.base}/authentication/token",
            headers={"refreshToken": self.refresh_token},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise Beds24Error(f"Beds24トークン取得失敗: {resp.status_code} {resp.text[:200]}")
        self.access_token = resp.json().get("token", "")
        if not self.access_token:
            raise Beds24Error("Beds24 access token を取得できませんでした。")
        return self.access_token

    def _headers(self) -> dict:
        """すべての v2 リクエストに付与する認証ヘッダー（token: <token>）。"""
        return {"token": self._ensure_token()}

    def fetch_properties(self) -> List[dict]:
        """/properties を取得し、id/name の一覧を返す（認証確認用）。"""
        resp = requests.get(
            f"{self.base}/properties",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise Beds24Error(f"Beds24 properties取得失敗: {resp.status_code} {resp.text[:200]}")
        body = resp.json()
        data = body.get("data", body if isinstance(body, list) else [])
        out = []
        for p in data:
            out.append({"id": str(_first(p, "id", "propertyId", default="")),
                        "name": str(_first(p, "name", "propertyName", default=""))})
        return out

    def test_auth(self) -> dict:
        """認証テスト。token本体は一切返さない・表示しない。"""
        props = self.fetch_properties()
        method = ("long_life_token" if self.long_life_token else
                  "access_token" if self.access_token else "refresh_token")
        return {"success": True, "auth_method": method,
                "property_count": len(props), "properties": props}

    def fetch_raw(self, month: str) -> List[dict]:
        """対象月(YYYY-MM)のチェックイン予約 生JSON を取得（ページング対応）。"""
        headers = self._headers()
        year, mon = (int(x) for x in month.split("-"))
        last = calendar.monthrange(year, mon)[1]
        params = {
            "arrivalFrom": f"{month}-01",
            "arrivalTo": f"{month}-{last:02d}",
            "includeInvoiceItems": "true",
        }
        # status を明示しないと cancelled が返らない。分析・照合用に全status取得する。
        statuses = config.load_yaml("beds24.yml").get("filter", {}).get(
            "fetch_statuses", ["new", "request", "confirmed", "cancelled", "black"])
        if statuses:
            params["status"] = statuses
        if self.property_ids:
            params["propertyId"] = self.property_ids
        out: List[dict] = []
        page = 1
        while True:
            params["page"] = page
            resp = requests.get(
                f"{self.base}/bookings",
                headers=headers,
                params=params,
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                raise Beds24Error(f"Beds24予約取得失敗: {resp.status_code} {resp.text[:200]}")
            body = resp.json()
            data = body.get("data", body if isinstance(body, list) else [])
            if not data:
                break
            out.extend(data)
            if len(data) < self.cfg.get("page_size", 100):
                break
            page += 1
        return out

    def fetch_month(self, month: str, raw_dir: Path) -> List[BookingRecord]:
        """対象月の予約を取得し、JSON原本保存 + BookingRecord正規化。"""
        raw = self.fetch_raw(month)
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{month}.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        records = []
        for item in raw:
            rec = normalize_booking(item, config.property_name())
            rec.raw_json_path = str(raw_path)
            rec.finalize()
            records.append(rec)
        return records
