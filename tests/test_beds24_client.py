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
