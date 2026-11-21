"""Issue #72 D2: 費用の実績（重複排除）と基準モデル予測。合成データのみ。"""
from types import SimpleNamespace as T

from yuge_finance.accounting.cost_view import actual_costs, baseline_forecast

CLS = {"TESTGAS": ("gas", False), "TESTPAY": ("payroll_or_staff_payment", False), "TESTSHAHO": ("social_insurance", False),
       "TESTLOAN": ("debt_service_bank", False), "TESTOTA": ("ota_receivable_collection", False), "TESTX": ("unknown", True)}


def classify(t):
    cat, review = CLS[t.counterparty_raw]
    return {"cost_model_category": cat, "requires_review": review}


def tx(date, who, amount):
    return T(transaction_date=date, counterparty_raw=who, signed_amount=amount)


def payroll(month, cost):
    return {"cost_status": "computed", "cost": cost, "cost_source": "MF給与 支給控除一覧表（確定済み支給月）"}


TX = [tx("2026-09-10", "TESTGAS", -30000), tx("2026-09-25", "TESTPAY", -900000), tx("2026-09-30", "TESTSHAHO", -200000),
      tx("2026-09-28", "TESTLOAN", -150000), tx("2026-09-15", "TESTOTA", 500000),
      tx("2026-10-10", "TESTGAS", -36000), tx("2026-10-25", "TESTPAY", -950000), tx("2026-10-31", "TESTSHAHO", -210000)]


def test_payroll_actual_replaces_bank_payroll_payments_and_loans_are_not_costs():
    a = actual_costs(TX, classify, {"2026-09": payroll("2026-09", 1_100_000), "2026-10": payroll("2026-10", 1_160_000)})
    sep = a["2026-09"]
    assert sep["categories"] == {"gas": 30000}, "loan, collection and payroll-paid lines are not added"
    assert sep["excluded_payroll_paid"] == 1_100_000
    assert sep["labor"] == {"amount": 1_100_000, "source": "MF給与 支給控除一覧表（確定済み支給月）", "basis": "mf_payroll_actual"}
    assert sep["total_known"] == 1_130_000 and sep["complete"] is True


def test_a_month_without_payroll_is_not_obtained_not_zero():
    a = actual_costs(TX, classify, {"2026-09": payroll("2026-09", 1_100_000)})
    assert a["2026-10"]["labor"]["basis"] == "not_obtained" and a["2026-10"]["labor"]["amount"] is None
    assert a["2026-10"]["complete"] is False
    assert a["2026-10"]["excluded_payroll_paid"] == 1_160_000, "still excluded: it pays a payroll counted in its own work month"


def test_no_payroll_anywhere_uses_bank_cash_basis():
    a = actual_costs(TX, classify, {})
    assert a["2026-09"]["labor"] == {"amount": 1_100_000, "source": "銀行出金（給与・社会保険・労働保険）", "basis": "bank_cash_payroll_category"}
    assert a["2026-09"]["categories"] == {"gas": 30000}


def test_unclassified_is_held_apart_and_keeps_the_month_open():
    a = actual_costs(TX + [tx("2026-10-05", "TESTX", -5000)], classify, {"2026-09": payroll("2026-09", 1), "2026-10": payroll("2026-10", 1)})
    assert (a["2026-10"]["unclassified"], a["2026-10"]["unclassified_count"], a["2026-10"]["complete"]) == (5000, 1, False)


def test_baseline_forecast_names_its_months_and_skips_actual_months():
    a = actual_costs(TX, classify, {"2026-09": payroll("2026-09", 1_100_000), "2026-10": payroll("2026-10", 1_160_000)})
    f = baseline_forecast(a, ["2026-10", "2026-11", "2026-12"])
    assert set(f) == {"2026-11", "2026-12"}
    nov = f["2026-11"]
    assert nov["kind"] == "forecast" and nov["source_months"] == ["2026-09", "2026-10"]
    assert nov["categories"] == {"gas": 33000} and nov["labor"]["amount"] == 1_130_000
    assert baseline_forecast(actual_costs(TX[:1], classify, {}), ["2026-11"]) == {}, "no complete month, no forecast"
