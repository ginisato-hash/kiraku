"""検証レポート（validation_report.md）。"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List


def write(month: str, checks: List[Dict], wb_checks: List[Dict],
          severity: Dict, path: Path) -> Path:
    lines = [f"# 検証レポート｜喜らく単体 {month}", ""]
    status = "✅ 重大エラーなし" if severity.get("all_ok") else "❌ 重大エラーあり"
    lines += [f"**総合判定: {status}**", ""]

    if severity.get("critical"):
        lines += ["## ❌ 重大エラー", ""]
        for c in severity["critical"]:
            lines.append(f"- **{c['check']}** ({c['target']}): 値={c['value']} / 許容={c['allow']} {c.get('detail','')}")
        lines.append("")
    if severity.get("warnings"):
        lines += ["## ⚠️ 警告（要手動確認）", ""]
        for c in severity["warnings"]:
            lines.append(f"- {c['check']}: 値={c['value']} {c.get('detail','')}")
        lines.append("")

    lines += ["## 整合性チェック一覧", "",
              "| チェック | 対象 | 値 | 許容 | 判定 | 備考 |",
              "|---|---|---|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['check']} | {c['target']} | {c['value']} | {c['allow']} | {c['status']} | {c.get('detail','')} |")
    lines.append("")

    lines += ["## Excel出力チェック", "",
              "| チェック | 判定 | 備考 |", "|---|---|---|"]
    for c in wb_checks:
        lines.append(f"| {c['check']} | {c['status']} | {c.get('detail','')} |")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
