"""assert_no_forbidden_cleaning_keys() のテスト（清掃出力専用の禁止キー）。"""
import pytest

from yuge_finance.ops.schema import assert_no_forbidden_cleaning_keys


def test_clean_cleaning_payload_passes():
    payload = {
        "date": "2026-09-01", "room_number": "401", "status": "CHECKIN",
        "arriving_guest": {"booking_id": "1", "guest_name": "架空 太郎", "adults": 2,
                           "children": 0, "total_guests": 2, "check_in": "2026-09-01",
                           "arrival_time": "15:00", "source": "じゃらん"},
    }
    assert_no_forbidden_cleaning_keys(payload)  # 例外なし


@pytest.mark.parametrize("forbidden_key", [
    "phone", "mobile", "address", "postcode", "email", "passport", "nationality",
    "price", "revenue", "commission", "adr", "revpar", "rate", "amount", "notes",
])
def test_forbidden_key_anywhere_nested_raises(forbidden_key):
    payload = {"date": "2026-09-01", "arriving_guest": {"guest_name": "架空", forbidden_key: "x"}}
    with pytest.raises(AssertionError):
        assert_no_forbidden_cleaning_keys(payload)
