from yuge_finance.accounting import pl_bs_cf, trial_balance
from yuge_finance.normalize.schema import JournalEntry


def _entries():
    return [
        JournalEntry(journal_date="2026-07-10", debit_account="売掛金",
                     debit_amount=30000, credit_account="宿泊売上",
                     credit_amount=30000).finalize(),
        JournalEntry(journal_date="2026-07-10", debit_account="水道光熱費",
                     debit_amount=32000, credit_account="現預金",
                     credit_amount=32000).finalize(),
    ]


def test_trial_balance_balanced():
    tb = trial_balance.build(_entries())
    tot = trial_balance.totals(tb)
    assert tot["balanced"]
    assert tot["debit"] == tot["credit"] == 62000


def test_bs_balances_with_net_income():
    tb = trial_balance.build(_entries())
    pl = pl_bs_cf.build_pl(tb)
    bs = pl_bs_cf.build_bs(tb, pl["net_income"])
    assert bs["balanced"]


def test_cf_reconciles():
    tb = trial_balance.build(_entries())
    pl = pl_bs_cf.build_pl(tb)
    cf = pl_bs_cf.build_cf(tb, pl["net_income"])
    assert cf["reconciles"]
