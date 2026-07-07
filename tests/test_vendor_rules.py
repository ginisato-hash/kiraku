"""きらやか銀行 総合振込先 取引先別ルールの検証。"""
from yuge_finance.accounting import journal_engine
from yuge_finance.normalize.schema import BankTransaction


def _bank(desc, amt):
    return BankTransaction(account_name="本店", transaction_date="2026-07-10",
                           description=desc, withdrawal_amount=amt, balance=0).finalize()


def _confirmed_bank(out):
    return [e for e in out["confirmed"] if e.source == "bank"]


def test_food_vendor_high_confirmed():
    out = journal_engine.build("2026-07", [], [_bank("ﾆﾎﾝｼﾖﾂｹﾝ", 50000)], [], [])
    e = _confirmed_bank(out)
    assert e and e[0].debit_account == "消耗品費" and e[0].debit_subaccount == "食材仕入"


def test_amenity_and_linen_high():
    out = journal_engine.build("2026-07", [], [
        _bank("ｱｽﾞﾏｼﾖｳｼﾞ", 20000), _bank("ｻﾞｵｳｻﾌﾟﾗｲｽﾞ", 30000)], [], [])
    accs = {(e.debit_account, e.debit_subaccount) for e in _confirmed_bank(out)}
    assert ("消耗品費", "アメニティ") in accs
    assert ("リネン費", "蔵王サプライズ") not in accs   # subaccountはcategory_map由来
    assert any(a == "リネン費" for a, _ in accs)


def test_hoshizaki_amount_cap_review():
    small = journal_engine.build("2026-07", [], [_bank("ﾎｼｻﾞｷﾄｳﾎｸ", 50000)], [], [])
    assert any(e.debit_account == "修繕費" for e in _confirmed_bank(small))
    big = journal_engine.build("2026-07", [], [_bank("ﾎｼｻﾞｷﾄｳﾎｸ", 150000)], [], [])
    assert _confirmed_bank(big) == []           # 10万以上は確定しない
    assert len(big["exceptions"]) >= 1


def test_haken_to_gaichu_subaccount():
    out = journal_engine.build("2026-07", [], [_bank("ﾋﾕ-ﾏﾆﾂｸ", 80000)], [], [])
    e = _confirmed_bank(out)
    assert e and e[0].debit_account == "固定費" and e[0].debit_subaccount == "人材派遣"


def test_medium_vendor_not_confirmed():
    out = journal_engine.build("2026-07", [], [_bank("ﾀﾏﾔｿｳﾎﾝﾃﾝ", 30000)], [], [])
    assert _confirmed_bank(out) == []
    assert any(e.rule_id == "bank_vendor_tamaya" for e in out["exceptions"])


def test_loan_vendor_never_auto_expensed():
    # 政策金融公庫は費用へ自動分類しない（仮勘定のままexception）
    out = journal_engine.build("2026-07", [], [_bank("ｾｲｻｸｺｳｺ(ｺｸﾐﾝ", 35243)], [], [])
    assert _confirmed_bank(out) == []
    assert any(e.rule_id == "unmatched" for e in out["exceptions"])


def test_ota_outflow_is_fee_not_revenue():
    # 楽天への出金は支払手数料（入金は宿泊売上で別処理）
    out = journal_engine.build("2026-07", [], [_bank("ﾗｸﾃﾝ", 10000)], [], [])
    e = _confirmed_bank(out)
    assert e and e[0].debit_account == "固定費" and e[0].debit_subaccount == "支払手数料"
