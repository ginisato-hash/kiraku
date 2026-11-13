"""MFの公式応答形を使い、全件性・認証・安全な失敗をHTTP境界で検証する。"""
import traceback
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from yuge_finance.api.moneyforward_client import (
    API_BASE_URL,
    TOKEN_EXCHANGE_URL,
    MoneyForwardClient,
    MoneyForwardError,
)


API_KEY = "mf_api_prd_TEST_NOT_A_REAL_KEY"
JWT = "TEST_NOT_A_REAL_JWT"
OFFICE = "1234-5678"
ACCOUNT = "bank%2Bparent%3D%3D"
SUB_ACCOUNT = "%2Bbranch%2Bid%3D%3D"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class Response:
    def __init__(self, body=None, status=200, headers=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses, clock):
        self.responses = list(responses)
        self.calls = []
        self.clock = clock

    def request(self, method, url, **kwargs):
        prepared = requests.Request(method, url, params=kwargs.get("params")).prepare()
        self.calls.append({"method": method, "url": prepared.url, "at": self.clock(), **kwargs})
        if not self.responses:
            raise AssertionError("Unexpected HTTP request")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def token(value=JWT, expires=3600):
    return Response({"access_token": value, "token_type": "Bearer", "expires_in": expires})


def transaction(identity="t1", **overrides):
    return {"id": identity, "date": "2026-11-01", "value": 1200,
            "side": "INCOME", "content": "sample bank transaction", "memo": None,
            "connected_account_id": ACCOUNT, "connected_sub_account_id": SUB_ACCOUNT,
            "journalizing_status": "none", "voucher_file_ids": [], **overrides}


def page(rows, total=None, pages=1):
    return Response({"transactions": rows,
                     "metadata": {"total_count": len(rows) if total is None else total,
                                  "total_pages": pages}})


def client(responses, **kwargs):
    clock = FakeClock()
    session = Session(responses, clock)
    instance = MoneyForwardClient(API_KEY, OFFICE, session=session, clock=clock,
                                  sleep=clock.sleep, **kwargs)
    return instance, session, clock


def fetch(instance, **kwargs):
    return instance.list_transactions("2026-11-01", "2026-11-30", **kwargs)


@pytest.mark.parametrize("api_key,office", [("", OFFICE), (API_KEY, ""), (API_KEY, "12345678"), ("bad\nkey", OFFICE)])
def test_configuration_is_required_without_exposing_values(api_key, office):
    with pytest.raises(MoneyForwardError) as exc:
        MoneyForwardClient(api_key, office)
    assert API_KEY not in str(exc.value)
    assert "bad\nkey" not in str(exc.value)


def test_api_key_exchange_cached_jwt_and_office_scope():
    instance, session, _ = client([token(), Response({"code": OFFICE}),
                                   Response({"connected_accounts": []})])
    assert instance.get_office()["code"] == OFFICE
    assert instance.list_connected_accounts() == []
    assert len(session.calls) == 3
    exchange, first, second = session.calls
    assert exchange["method"] == "POST" and exchange["url"] == TOKEN_EXCHANGE_URL
    assert exchange["headers"]["Authorization"] == "Bearer " + API_KEY
    assert exchange["params"] is None and "json" not in exchange and "data" not in exchange
    for call in (first, second):
        assert call["method"] == "GET"
        assert call["url"].startswith(API_BASE_URL + "/")
        assert call["headers"]["Authorization"] == "Bearer " + JWT
        assert call["params"]["office_code"] == OFFICE
        assert call["allow_redirects"] is False


def test_expired_jwt_is_exchanged_before_next_request():
    instance, session, clock = client([token(), Response({"code": OFFICE}),
                                       token("RENEWED"), Response({"code": OFFICE})])
    instance.get_office()
    clock.now += 3600
    instance.get_office()
    assert [c["method"] for c in session.calls] == ["POST", "GET", "POST", "GET"]
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer RENEWED"


def test_all_pages_all_statuses_and_encoded_subaccount_id():
    statuses = ["excluded", "none", "registered", "modified", "new_voucher_attached"]
    rows = [transaction(f"t{i}", journalizing_status=statuses[i % 5], side="EXPENSE" if i % 2 else "INCOME")
            for i in range(11)]
    instance, session, _ = client([token(), page(rows[:10], total=11, pages=2),
                                   page(rows[10:], total=11, pages=2)])
    assert fetch(instance, connected_sub_account_id=SUB_ACCOUNT, per_page=10) == rows
    calls = session.calls[1:]
    assert [c["params"]["page"] for c in calls] == [1, 2]
    for call in calls:
        assert "journalizing_statuses" not in call["params"]
        assert call["params"]["order"] == "asc"
        assert "connected_account_id" not in call["params"]
        assert "connected_sub_account_id=%2Bbranch%2Bid%3D%3D" in call["url"]
        assert "%252B" not in call["url"] and "%253D" not in call["url"]
        assert parse_qs(urlsplit(call["url"]).query)["connected_sub_account_id"] == ["+branch+id=="]


def test_parent_filter_allows_null_subaccount_and_optional_text():
    row = transaction(connected_sub_account_id=None)
    row.pop("content")
    row.pop("memo")
    instance, _, _ = client([token(), page([row])])
    assert fetch(instance, connected_account_id=ACCOUNT) == [row]


@pytest.mark.parametrize("pages", [0, 1])
def test_empty_window_is_complete_without_requesting_nonexistent_page(pages):
    instance, session, _ = client([token(), page([], pages=pages)])
    assert fetch(instance) == []
    assert len(session.calls) == 2


def test_401_refreshes_once_and_replays_same_read():
    instance, session, _ = client([token(), Response(status=401), token("NEW"), page([transaction()])])
    assert len(fetch(instance, connected_sub_account_id=SUB_ACCOUNT)) == 1
    assert [c["method"] for c in session.calls] == ["POST", "GET", "POST", "GET"]
    assert session.calls[1]["params"] == session.calls[3]["params"]
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer NEW"


def test_second_401_stops_without_exposing_upstream_body():
    private = API_KEY + JWT + " private bank transaction"
    instance, session, _ = client([token(), Response(private, 401), token("NEW"), Response(private, 401)])
    with pytest.raises(MoneyForwardError) as exc:
        fetch(instance)
    assert exc.value.status_code == 401
    assert len(session.calls) == 4
    assert private not in str(exc.value) and API_KEY not in str(exc.value)


def test_429_honors_retry_after_and_500_retries_with_bounded_backoff():
    instance, session, clock = client([token(), Response(status=429, headers={"Retry-After": "2"}),
                                       Response(status=500), page([transaction()])])
    assert len(fetch(instance)) == 1
    reads = [c for c in session.calls if c["method"] == "GET"]
    assert len(reads) == 3
    assert reads[1]["at"] - reads[0]["at"] >= 2
    assert reads[2]["at"] - reads[1]["at"] >= 2
    assert len(clock.waits) == 2


def test_long_retry_after_does_not_retry_early_or_block_for_minutes():
    instance, session, clock = client([token(), Response(status=429, headers={"Retry-After": "120"})])
    with pytest.raises(MoneyForwardError) as exc:
        fetch(instance)
    assert exc.value.status_code == 429
    assert len(session.calls) == 2 and not clock.waits


def test_network_error_is_retried_but_never_leaks_exception_context():
    private = API_KEY + JWT + " account and transaction detail"
    instance, session, _ = client([token(), requests.Timeout(private), requests.Timeout(private)], max_retries=1)
    with pytest.raises(MoneyForwardError) as exc:
        fetch(instance)
    rendered = "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
    assert private not in rendered and API_KEY not in rendered
    assert exc.value.__context__ is None
    assert len(session.calls) == 3


@pytest.mark.parametrize("status", [400, 403, 302])
def test_nonretryable_errors_do_not_return_response_data(status):
    instance, session, _ = client([token(), Response({"errors": [{"message": API_KEY + JWT}]}, status)])
    with pytest.raises(MoneyForwardError) as exc:
        fetch(instance)
    assert exc.value.status_code == status
    assert len(session.calls) == 2 and API_KEY not in str(exc.value) and JWT not in str(exc.value)


def test_api_rate_limit_applies_across_all_read_methods():
    instance, session, _ = client([token()] + [Response({"code": OFFICE}) for _ in range(5)])
    for _ in range(5):
        instance.get_office()
    times = [c["at"] for c in session.calls if c["method"] == "GET"]
    assert all(b - a >= 0.349 for a, b in zip(times, times[1:]))
    assert all(times[i + 3] - times[i] > 1 for i in range(len(times) - 3))


@pytest.mark.parametrize("second", [
    page([transaction("last")], total=12, pages=2),
    page([], total=11, pages=2),
    page([transaction("t0")], total=11, pages=2),
])
def test_incomplete_or_changing_pagination_never_returns_partial_results(second):
    first = [transaction(f"t{i}") for i in range(10)]
    instance, _, _ = client([token(), page(first, total=11, pages=2), second])
    with pytest.raises(MoneyForwardError):
        fetch(instance, per_page=10)


def test_wrong_office_or_filtered_account_is_rejected():
    instance, _, _ = client([token(), Response({"code": "0000-0000"})])
    with pytest.raises(MoneyForwardError):
        instance.get_office()
    instance, _, _ = client([token(), page([transaction(connected_sub_account_id="another")])])
    with pytest.raises(MoneyForwardError):
        fetch(instance, connected_sub_account_id=SUB_ACCOUNT)


@pytest.mark.parametrize("overrides", [
    {"value": -1}, {"value": True}, {"side": "OTHER"}, {"date": "2026-10-31"},
    {"date": "2026-11-31"}, {"connected_sub_account_id": 1},
])
def test_invalid_transaction_fails_closed_without_echoing_bank_details(overrides):
    instance, _, _ = client([token(), page([transaction(content="PRIVATE BANK DETAILS", **overrides)])])
    with pytest.raises(MoneyForwardError) as exc:
        fetch(instance)
    assert "PRIVATE BANK DETAILS" not in str(exc.value)


def test_invalid_query_is_rejected_before_authentication():
    instance, session, _ = client([])
    bad = [lambda: instance.list_transactions("2025-01-01", "2026-11-01"),
           lambda: instance.list_transactions("2026-11-02", "2026-11-01"),
           lambda: fetch(instance, per_page=1),
           lambda: fetch(instance, connected_account_id=ACCOUNT, connected_sub_account_id=SUB_ACCOUNT)]
    for call in bad:
        with pytest.raises(MoneyForwardError):
            call()
    assert not session.calls


def test_bad_json_exception_has_no_payload_in_traceback():
    private = API_KEY + JWT + "PRIVATE BANK DETAILS"
    instance, _, _ = client([token(), Response(ValueError(private))])
    with pytest.raises(MoneyForwardError) as exc:
        fetch(instance)
    assert exc.value.__context__ is None
    assert private not in "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
