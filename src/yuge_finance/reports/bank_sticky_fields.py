"""銀行CF summary の sticky field 引き継ぎ（GitHub Actions実行時の欠落対策）。

GitHub Actionsは毎回まっさらなcheckoutで動くため、ローカル銀行CSV(imports/bank/)や
会計士確定開始残高(imports/opening_balance/)にアクセスできず、今回生成したsnapshotの
bank_*フィールドは未取込/空になる。これをそのまま公開すると、Macで手動 ingest-bank-csv
した実績が次のGitHub Actions実行(最大15分後)で消えてしまう。

本モジュールは「bank_*フィールドのみ」を対象に、今回生成値が無効な場合に限り、
直近公開済みsnapshot(previous_snapshot)から引き継ぐ。会計opening balanceやraw明細を
推測で作ることは一切しない（そもそも引き継ぎ対象はbank_*フィールドの数値/ステータスのみ
であり、raw取引明細ではない）。

generated_at_jst / today_jst / target_month / today_new_booking_* / beds24_* /
booking_pace_status / 各種BEP等は "bank_" prefix を持たないため、本モジュールの
処理では一切変更されない（コピー対象をbank_* prefixのみに限定しているため構造的に安全）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

VALID_IMPORT_STATUSES = {"imported", "loaded", "available"}

JST = timezone(timedelta(hours=9))


def _jst_now_str() -> str:
    return datetime.now(timezone.utc).astimezone(JST).isoformat(timespec="seconds")


def is_valid_bank_snapshot(snapshot: Dict) -> bool:
    """snapshotのbank_*フィールドが「有効な取込済みデータ」を表しているか判定する。

    0円残高は理論上あり得るため、金額が0であること自体は無効の根拠にしない。
    """
    if not snapshot:
        return False
    status = snapshot.get("bank_csv_import_status")
    if status in VALID_IMPORT_STATUSES:
        return True
    if (snapshot.get("bank_actual_latest_balance") is not None
            and (snapshot.get("bank_csv_imported_rows") or 0) > 0):
        return True
    return False


def merge_sticky_bank_fields(new_snapshot: Dict, previous_snapshot: Optional[Dict]) -> Dict:
    """bank_*フィールドのみ、今回値が無効な場合に限りprevious_snapshotから引き継ぐ。

    今回値(new_snapshot)が有効な場合は常にそちらを優先する。
    generated_at_jst/today_jst/beds24系/booking_pace_status/BEP系等、
    "bank_" prefixを持たない全フィールドは一切変更しない。
    """
    merged = dict(new_snapshot)

    if is_valid_bank_snapshot(new_snapshot):
        merged["bank_fields_source"] = "current_import"
        return merged

    if previous_snapshot and is_valid_bank_snapshot(previous_snapshot):
        for key, value in previous_snapshot.items():
            if key.startswith("bank_"):
                merged[key] = value
        merged["bank_fields_source"] = "previous_r2_snapshot"
        merged["bank_fields_preserved_at_jst"] = _jst_now_str()
        merged["bank_fields_preserved_from_generated_at_jst"] = previous_snapshot.get("generated_at_jst")
        merged["bank_fields_preserved_note"] = (
            "GitHub Actions実行時にローカル銀行CSVが無いため、"
            "直近公開snapshotの銀行CF summaryを引き継ぎました。"
        )
        return merged

    merged["bank_fields_source"] = "not_available"
    return merged
