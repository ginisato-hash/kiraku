from datetime import date

from yuge_finance.accounting import kpi
from yuge_finance.normalize.schema import JournalEntry


def _entries():
    return [
        JournalEntry(journal_date="2026-07-10", debit_account="売掛金",
                     debit_amount=1000000, credit_account="宿泊売上",
                     credit_amount=1000000).finalize(),
        JournalEntry(journal_date="2026-07-10", debit_account="リネン費",
                     debit_amount=100000, credit_account="現預金",
                     credit_amount=100000).finalize(),
        JournalEntry(journal_date="2026-07-31", debit_account="人件費",
                     debit_amount=200000, credit_account="買掛金・未払金",
                     credit_amount=200000).finalize(),
        JournalEntry(journal_date="2026-07-31", debit_account="MCコスト",
                     debit_subaccount="MC固定", debit_amount=150000,
                     credit_account="買掛金・未払金", credit_amount=150000).finalize(),
        JournalEntry(journal_date="2026-07-31", debit_account="MCコスト",
                     debit_subaccount="MC変動", debit_amount=50000,
                     credit_account="買掛金・未払金", credit_amount=50000).finalize(),
    ]


def test_split_costs_with_mc_subaccount():
    costs = kpi.split_costs(_entries())
    # 変動: リネン100k + MC変動50k = 150k / 固定: 人件費200k + MC固定150k = 350k
    assert costs["variable"] == 150000
    assert costs["fixed"] == 350000


def test_breakeven_computed():
    k = kpi.build("2026-07", _entries(), revenue=1000000, bookings=[],
                  as_of=date(2026, 7, 15))
    # v_ratio = 150000/1000000 = 0.15 ; be = 350000/0.85 ≈ 411765
    assert k["損益分岐売上"] is not None
    assert 400000 < k["損益分岐売上"] < 420000
    assert k["損益分岐達成率"] > 1            # 達成済み
    assert k["損益分岐まで残り売上"] == 0
    # 着地見込: 15日経過 → 1,000,000 * 31/15
    assert k["月末着地見込売上"] > 1000000


def test_breakeven_below_target():
    # 売上が損益分岐未達のケース
    entries = [
        JournalEntry(journal_date="2026-07-10", debit_account="売掛金",
                     debit_amount=300000, credit_account="宿泊売上",
                     credit_amount=300000).finalize(),
        JournalEntry(journal_date="2026-07-31", debit_account="人件費",
                     debit_amount=350000, credit_account="買掛金・未払金",
                     credit_amount=350000).finalize(),
    ]
    k = kpi.build("2026-07", entries, revenue=300000, bookings=[],
                  as_of=date(2026, 7, 31))
    assert k["損益分岐達成率"] < 1
    assert k["損益分岐まで残り売上"] > 0
    assert k["損益分岐まで残り販売室数"] > 0
