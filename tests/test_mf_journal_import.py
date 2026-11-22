"""Issue #72 A2: 確定仕訳 → MF仕訳帳インポートCSV と、エクスポートでの登録照合。合成データのみ。"""
import csv
import io

import pytest

from yuge_finance.accounting.mf_journal_import import HEADER, MfImportError, build_csv, registered_ids
from yuge_finance.normalize.schema import JournalEntry


def je(**kw):
    base = dict(journal_date="2026-10-05", description="TEST 電気代", debit_account="水道光熱費", credit_account="普通預金",
                debit_amount=11000, credit_amount=11000, tax_category="課税10%", source="bank", source_id="TEST-1", rule_id="r1")
    base.update(kw)
    return JournalEntry(**base).finalize()


def rows(result):
    return list(csv.reader(io.StringIO(result["bytes"].decode(result["encoding"]))))


def test_27_columns_dates_amounts_tax_and_memo_token():
    a, b = je(), je(source_id="TEST-2", debit_account="普通預金", credit_account="売上高", description="TEST 売上", debit_amount=22000, credit_amount=22000)
    r = build_csv([a, b])
    out = rows(r)
    assert out[0] == HEADER and len(HEADER) == 27
    assert out[1][:2] == ["1", "2026/10/05"] and out[2][0] == "2", "取引No is never blank: same-day rows would merge"
    assert (out[1][6], out[1][14]) == ("課税仕入 10%", "対象外"), "MF sample: full tax name on the P/L side, 対象外 on the balance side"
    assert (out[2][6], out[2][14]) == ("対象外", "課税売上 10%")
    assert (out[1][8], out[1][9], out[1][16], out[1][17]) == ("11000", "0", "11000", "0"), "MF sample puts 0 in the tax-amount columns"
    assert out[1][19] == f"GOS:{a.journal_id}"
    assert r["header_verified"] is True
    assert r["bytes"].endswith(b"\r\n")


def test_only_confirmed_and_not_yet_registered():
    a, b, c = je(), je(source_id="TEST-2"), je(source_id="TEST-3", confidence="medium")
    r = build_csv([a, b, c], registered={a.journal_id})
    assert r["written"] == [b.journal_id]
    assert r["skipped_registered"] == [a.journal_id] and r["skipped_unconfirmed"] == [c.journal_id]


def test_refuses_what_mf_cannot_read():
    for bad in (je(description='TEST "quoted"'), je(memo="line\nbreak"), je(debit_amount=100.5, credit_amount=100.5),
                je(debit_amount=100, credit_amount=200), je(journal_date="2026/10/05"), je(description="X" * 201)):
        with pytest.raises(MfImportError):
            build_csv([bad])


def test_registered_ids_from_an_mf_export_in_either_encoding():
    a = je()
    text = "取引No,取引日,仕訳メモ\r\n1,2026/10/05,GOS:" + a.journal_id + " TEST\r\n2,2026/10/06,手入力\r\n"
    for enc in ("utf-8-sig", "cp932"):
        assert registered_ids(text.encode(enc)) == {a.journal_id}
    with pytest.raises(MfImportError):
        registered_ids("a,b\r\n1,2\r\n".encode("utf-8"))
