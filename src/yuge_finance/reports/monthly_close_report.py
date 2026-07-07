"""月次締めレポート（monthly_close_report.md）。"""
from __future__ import annotations

from pathlib import Path
from typing import Dict


def write(month: str, summary: Dict, path: Path) -> Path:
    s = summary
    lines = [f"# 月次締めレポート｜喜らく単体 {month}", ""]
    lines += ["## 取込件数", "",
              f"- Beds24予約: {s.get('beds24_count', 0)} 件（うち売上計上対象 {s.get('revenue_bookings', 0)} 件）",
              f"- 銀行明細: {s.get('bank_count', 0)} 件",
              f"- 現金明細: {s.get('cash_count', 0)} 件（承認済 {s.get('cash_approved', 0)} 件）",
              f"- 手動補正: {s.get('manual_count', 0)} 件", ""]

    lines += ["## 仕訳", "",
              f"- 確定仕訳: {s.get('confirmed', 0)} 行",
              f"- 例外(medium/low/未承認): {s.get('exceptions', 0)} 行",
              f"- 借方合計: {s.get('debit_total', 0):,.0f} 円",
              f"- 貸方合計: {s.get('credit_total', 0):,.0f} 円", ""]

    pl = s.get("pl", {})
    lines += ["## PLサマリ（円）", ""]
    for ln in pl.get("lines", []):
        lines.append(f"- {ln['item']}: {ln['amount']:,.0f}")
    lines.append("")

    bs = s.get("bs", {})
    lines += ["## BSサマリ（円）", "",
              f"- 資産合計: {bs.get('assets', 0):,.0f}",
              f"- 負債・純資産合計: {bs.get('liabilities_equity', 0):,.0f}",
              f"- バランス: {'OK' if bs.get('balanced') else '要確認'}", ""]

    cf = s.get("cf", {})
    lines += ["## CFサマリ（円）", ""]
    for ln in cf.get("lines", []):
        lines.append(f"- {ln['item']}: {ln['amount']:,.0f}")
    lines.append("")

    lines += ["## 出力ファイル", "", f"- 出力先: `{s.get('output_dir', '')}`",
              f"- Excel: `{s.get('workbook', '')}`", ""]

    lines += ["## 総合判定", "",
              f"- 検証: {'✅ 重大エラーなし' if s.get('all_ok') else '❌ 要確認'}", ""]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
