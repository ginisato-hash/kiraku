"""銀行CF summary sticky field引き継ぎのテスト（GitHub Actions実行時の欠落対策）。"""
from yuge_finance.reports import bank_sticky_fields as bsf

VALID_BANK = {
    "bank_actual_latest_balance": 3052421.0,
    "bank_actual_latest_balance_date": "2026-07-07",
    "bank_csv_import_status": "imported",
    "bank_csv_imported_rows": 89,
    "bank_total_deposits": 36144028.0,
    "bank_total_withdrawals": 44075154.0,
}

EMPTY_BANK = {
    "bank_actual_latest_balance": None,
    "bank_csv_import_status": "未取込",
    "bank_csv_imported_rows": 0,
}

NON_BANK_FIELDS = {
    "generated_at_jst": "2026-07-09T08:00:00+09:00",
    "today_jst": "2026-07-09",
    "target_month": "2026-07",
    "today_new_booking_count": 3,
    "today_new_booking_revenue": 84000,
    "today_new_booking_logic_status": "ok",
    "beds24_revenue_net_for_bi": 1072363,
    "booking_pace_status": "green",
    "cash_operating_breakeven_revenue": 2259367,
    "finance_breakeven_revenue": 2806375,
    "full_debt_reserve_breakeven_revenue": 3763640,
}


def test_is_valid_bank_snapshot_true_when_imported_status():
    assert bsf.is_valid_bank_snapshot(VALID_BANK) is True


def test_is_valid_bank_snapshot_false_when_not_imported():
    assert bsf.is_valid_bank_snapshot(EMPTY_BANK) is False


def test_is_valid_bank_snapshot_false_when_none():
    assert bsf.is_valid_bank_snapshot(None) is False
    assert bsf.is_valid_bank_snapshot({}) is False


def test_zero_balance_with_rows_is_still_valid():
    """0円残高は理論上あり得るため、単純に0を無効扱いしない。"""
    snap = {"bank_actual_latest_balance": 0.0, "bank_csv_imported_rows": 5,
           "bank_csv_import_status": "imported"}
    assert bsf.is_valid_bank_snapshot(snap) is True


def test_current_import_preferred_when_new_snapshot_has_valid_bank_data():
    new_snapshot = {**NON_BANK_FIELDS, **VALID_BANK}
    previous = {**NON_BANK_FIELDS, "bank_actual_latest_balance": 999999.0,
               "bank_csv_import_status": "imported", "bank_csv_imported_rows": 1}
    merged = bsf.merge_sticky_bank_fields(new_snapshot, previous)
    assert merged["bank_fields_source"] == "current_import"
    assert merged["bank_actual_latest_balance"] == 3052421.0  # newの値のまま


def test_previous_snapshot_used_when_new_is_empty():
    new_snapshot = {**NON_BANK_FIELDS, **EMPTY_BANK}
    previous_snapshot = {
        "generated_at_jst": "2026-07-09T07:00:00+09:00",
        **VALID_BANK,
    }
    merged = bsf.merge_sticky_bank_fields(new_snapshot, previous_snapshot)
    assert merged["bank_fields_source"] == "previous_r2_snapshot"
    assert merged["bank_actual_latest_balance"] == 3052421.0
    assert merged["bank_csv_import_status"] == "imported"
    assert merged["bank_csv_imported_rows"] == 89
    assert merged["bank_fields_preserved_from_generated_at_jst"] == "2026-07-09T07:00:00+09:00"
    assert "bank_fields_preserved_at_jst" in merged
    assert "直近公開snapshot" in merged["bank_fields_preserved_note"]


def test_not_available_when_neither_new_nor_previous_has_valid_bank_data():
    new_snapshot = {**NON_BANK_FIELDS, **EMPTY_BANK}
    merged = bsf.merge_sticky_bank_fields(new_snapshot, None)
    assert merged["bank_fields_source"] == "not_available"

    merged2 = bsf.merge_sticky_bank_fields(new_snapshot, {**NON_BANK_FIELDS, **EMPTY_BANK})
    assert merged2["bank_fields_source"] == "not_available"


def test_non_bank_fields_never_overwritten_by_previous():
    """generated_at_jst/today_jst/beds24系/BEP系はpreviousから絶対に引き継がない。"""
    new_snapshot = {**NON_BANK_FIELDS, **EMPTY_BANK}
    previous_snapshot = {
        "generated_at_jst": "2026-07-08T20:00:00+09:00",  # 古い値
        "today_jst": "2026-07-08",
        "target_month": "2026-06",
        "today_new_booking_count": 999,
        "beds24_revenue_net_for_bi": 1,
        "booking_pace_status": "red",
        "cash_operating_breakeven_revenue": 1,
        **VALID_BANK,
    }
    merged = bsf.merge_sticky_bank_fields(new_snapshot, previous_snapshot)
    assert merged["bank_fields_source"] == "previous_r2_snapshot"
    # 非bank系フィールドは今回値のまま(previousの汚染値が混入しない)
    assert merged["generated_at_jst"] == NON_BANK_FIELDS["generated_at_jst"]
    assert merged["today_jst"] == NON_BANK_FIELDS["today_jst"]
    assert merged["target_month"] == NON_BANK_FIELDS["target_month"]
    assert merged["today_new_booking_count"] == NON_BANK_FIELDS["today_new_booking_count"]
    assert merged["beds24_revenue_net_for_bi"] == NON_BANK_FIELDS["beds24_revenue_net_for_bi"]
    assert merged["booking_pace_status"] == NON_BANK_FIELDS["booking_pace_status"]
    assert merged["cash_operating_breakeven_revenue"] == NON_BANK_FIELDS["cash_operating_breakeven_revenue"]


def test_extra_bank_prefixed_fields_are_carried_generically():
    """明示リストに無いbank_接頭辞fieldも包括的に引き継ぐ。"""
    new_snapshot = {**NON_BANK_FIELDS, **EMPTY_BANK}
    previous_snapshot = {**NON_BANK_FIELDS, **VALID_BANK,
                         "bank_csv_observed_balance": 7749218.0,
                         "bank_csv_observed_date": "2026-05-29"}
    merged = bsf.merge_sticky_bank_fields(new_snapshot, previous_snapshot)
    assert merged["bank_csv_observed_balance"] == 7749218.0
    assert merged["bank_csv_observed_date"] == "2026-05-29"


def test_accountant_bs_cash_balance_not_treated_as_sticky_bank_field():
    """accountant_bs_cash_balanceは"bank_"接頭辞ではないため、会計データを推測で引き継がない。"""
    new_snapshot = {**NON_BANK_FIELDS, **EMPTY_BANK, "accountant_bs_cash_balance": None}
    previous_snapshot = {**NON_BANK_FIELDS, **VALID_BANK, "accountant_bs_cash_balance": 7950646.0}
    merged = bsf.merge_sticky_bank_fields(new_snapshot, previous_snapshot)
    assert merged["accountant_bs_cash_balance"] is None
