"""売上サマリ レポート（喜らく単体）。

A. 宿泊月ベース速報（Beds24・KPI）／ B. 入金月ベース実績（銀行/現金）／ C. 精算ラグ注記。
同月のA・Bは対象コホートが異なるため差分比較しない（OTA精算ラグ）。
本当のreconciliationはOTA精算明細(settlement table)導入時に実装。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from .. import csvio
from ..accounting import revenue_recon


def write(month: str, rec: Dict, out_dir: Path) -> Dict:
    lines = revenue_recon.display_lines(rec)
    csv_path = out_dir / "revenue_reconciliation_report.csv"
    csvio.write_rows(csv_path, lines, ["区分", "項目", "値"])

    md = [f"# 売上サマリ レポート｜喜らく単体 {month}", "",
          f"**revenue_data_status: {rec['revenue_data_status']}** / "
          f"**{rec['revenue_comparison_status']}**", "",
          f"> {rec['ota_settlement_lag_note']}", "",
          "## A. 宿泊月ベース速報（Beds24・宿泊KPI）", "",
          f"- 宿泊月売上(キャンセル除外): ¥{rec['beds24_stay_month_revenue_excluding_cancelled']:,}",
          f"- キャンセル保持額(速報): ¥{rec['beds24_stay_month_cancelled_revenue']:,}",
          f"- ADR: ¥{rec['adr']:,} / RevPAR: ¥{rec['revpar']:,} / 稼働率: {rec['occupancy']:.1%}",
          f"- 損益分岐達成率(速報): {rec['break_even_achievement_rate_sokuho']}", "",
          "## B. 入金月ベース会計/資金実績（銀行/現金）", "",
          f"- OTA入金売上(確定PL): ¥{rec['bank_deposit_month_ota_revenue']:,}",
          f"- 現金売上: ¥{rec['cash_in_basis_revenue']:,}",
          f"- 会計認識売上(入金ベース): ¥{rec['accounting_revenue_confirmed']:,}",
          f"- 総入金: ¥{rec['bank_deposit_month_total_inflow']:,} / "
          f"総出金: ¥{rec['bank_deposit_month_total_outflow']:,} / "
          f"現預金純増減: ¥{rec['net_cash_movement']:,}", "",
          "## C. 精算ラグ注記", "",
          f"- 同月比較適用: {rec['same_month_revenue_comparison_applicable']}（{rec['revenue_comparison_status']}）",
          f"- settlement_reconciliation_status: {rec['settlement_reconciliation_status']}",
          "- 将来: OTA精算明細(settlement_month/stay_month/OTA/gross/commission/net/deposit_date)導入時に"
          "宿泊月対応のreconciliationを実装。", "",
          "## legacy（参考値・判定には使わない）", "",
          f"- {rec['legacy_same_month_reference']['note']}",
          f"  - 同月差額(参考): ¥{rec['legacy_same_month_reference']['revenue_reconciliation_difference_same_month']:,}",
          ""]
    md_path = out_dir / "revenue_reconciliation_report.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(md), encoding="utf-8")
    return {"csv": str(csv_path), "md": str(md_path), "status": rec["revenue_data_status"]}
