from yuge_finance.api import beds24_client


def test_normalize_booking_basic():
    raw = {
        "id": 12345, "propertyId": 7, "roomId": 3, "roomName": "和室A",
        "referer": "Booking.com", "firstName": "太郎", "lastName": "山田",
        "arrival": "2026-07-10", "departure": "2026-07-12",
        "numAdult": 2, "numChild": 1, "price": 30000, "commission": 4500,
        "status": "confirmed", "bookingTime": "2026-06-01 10:00:00",
    }
    rec = beds24_client.normalize_booking(raw, "喜らく")
    assert rec.booking_id == "12345"
    assert rec.channel == "Booking.com"
    assert rec.guest_name == "太郎 山田"
    assert rec.checkin_date == "2026-07-10"
    assert rec.stay_nights == 2
    assert rec.guests == 3
    assert rec.gross_revenue == 30000
    assert rec.ota_commission == 4500
    assert rec.net_revenue == 25500          # gross - commission
    assert rec.stay_month == "2026-07"
    assert rec.import_hash                     # 付与されている


def test_cancelled_detection():
    raw = {"id": "1", "arrival": "2026-07-01", "departure": "2026-07-02",
           "price": 10000, "status": "cancelled"}
    rec = beds24_client.normalize_booking(raw)
    assert rec.is_cancelled(["cancelled"]) is True
    assert rec.is_cancelled(["black"]) is False


def test_normalize_booking_price_zero_falls_back_to_charge_total():
    """実データ実証済み: 手動作成予約(apiSource=Direct, rateDescription='オファー1')は
    booking.price が0のまま室料chargeだけinvoiceItemsに計上されることがある
    (booking_id 89381508/89362589/89214149/89500049で確認)。price=0でも
    charge行の合計を売上として拾う必要がある。"""
    raw = {
        "id": "89381508", "apiSource": "Direct", "channel": "direct",
        "arrival": "2026-07-06", "departure": "2026-07-08", "price": 0,
        "status": "confirmed",
        "invoiceItems": [
            {"type": "charge", "description": "", "lineTotal": 11800, "amount": 11800},
        ],
    }
    rec = beds24_client.normalize_booking(raw)
    assert rec.gross_revenue == 11800
    assert rec.net_revenue == 11800


def test_normalize_booking_price_zero_and_no_charge_stays_zero():
    """price=0かつcharge行も無い(または0以下)場合はgrossを0のままにする
    (charge行が本当に無い予約まで誤って加算しないため)。"""
    raw = {
        "id": "2", "arrival": "2026-07-01", "departure": "2026-07-02",
        "price": 0, "status": "confirmed",
        "invoiceItems": [
            {"type": "payment", "description": "BankTransfer", "lineTotal": 0},
        ],
    }
    rec = beds24_client.normalize_booking(raw)
    assert rec.gross_revenue == 0


def test_normalize_booking_price_nonzero_ignores_charge_fallback():
    """price が既に正しく入っている通常予約では、charge合計と一致していれば
    フォールバックの有無に関わらず結果は変わらない(既存の主要ケースを壊さない)。"""
    raw = {
        "id": "3", "arrival": "2026-07-01", "departure": "2026-07-02",
        "price": 30000, "status": "confirmed",
        "invoiceItems": [
            {"type": "charge", "description": "", "lineTotal": 30000},
        ],
    }
    rec = beds24_client.normalize_booking(raw)
    assert rec.gross_revenue == 30000
