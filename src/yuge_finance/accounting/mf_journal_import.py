"""確定仕訳 → マネーフォワード クラウド会計 仕訳帳インポートCSV。Issue #72 A2。

列は公式ガイド（仕訳帳のインポート ib01、2026-07-09更新）の項目表のとおり27列（A〜AA）。
2026-11-22、喜らくの事業者でMFの「サンプルフォーマットをダウンロード」（Shift-JIS/UTF-8の両方）を
取得し、1行目が HEADER と一字一句一致することを照合した（`HEADER_VERIFIED = True`）。
見本の書き方に合わせる：税区分は正式名（「課税売上 10%」「課税仕入 10%」）、貸借科目側は「対象外」、
税額列は 0。文字コードはMFの選択肢どおり Excel用（Shift-JIS = cp932）か その他（UTF-8）。

- 取引日は yyyy/MM/dd、金額は半角整数。税額はインポートで税額として取り込まれない（ガイド）ので 0。
- 1仕訳1行（借方・貸方が同額の単一仕訳）。取引Noは空にしない：同じ取引日の行が
  1つの複合仕訳にまとめられてしまうため（ie03 No.3）。
- 摘要・メモに改行や二重引用符があると読込失敗になるので、拒否する（切り詰めない）。
- 仕訳メモに `GOS:<journal_id>` を入れる。MFからエクスポートした仕訳帳CSVの仕訳メモで
  照合し、登録済みを再出力しない（再インポートは更新ではなく新規登録になるため）。
- 「確定」（confidence=high）だけを出力する。社内で確定しても MF登録済みではない。
"""
from __future__ import annotations

import csv
import io
import re
from typing import Dict, Iterable, List, Optional, Set

from ..normalize.schema import JournalEntry

HEADER = ["取引No", "取引日", "借方勘定科目", "借方補助科目", "借方部門", "借方取引先", "借方税区分", "借方インボイス",
          "借方金額(円)", "借方税額", "貸方勘定科目", "貸方補助科目", "貸方部門", "貸方取引先", "貸方税区分", "貸方インボイス",
          "貸方金額(円)", "貸方税額", "摘要", "仕訳メモ", "タグ", "MF仕訳タイプ", "決算整理仕訳", "作成日時", "作成者",
          "最終更新日時", "最終更新者"]
HEADER_VERIFIED = True
MEMO_TOKEN = re.compile(r"GOS:(JNL-[0-9a-f]{6,64})")
# 貸借対照表の科目（税区分を付けない側）。勘定科目名は喜らくの科目表の表記。
BALANCE_ACCOUNTS = {"現金", "普通預金", "当座預金", "売掛金", "未収入金", "買掛金", "未払金", "未払費用", "預り金", "前受金",
                    "仮払金", "仮受金", "短期借入金", "長期借入金", "立替金", "預金", "事業主貸", "事業主借"}
LIMITS = {"勘定科目": 30, "補助科目": 30, "取引先": 255, "摘要": 200, "仕訳メモ": 200}


class MfImportError(ValueError):
    pass


def _mf_tax(label: str, side: str) -> str:
    """社内の「課税10%」等をMFの見本と同じ正式名へ。売上側は課税売上、それ以外は課税仕入。不明はMFの科目既定に任せる（空）。"""
    m = re.fullmatch(r"課税(\d{1,2})%", label or "")
    if not m:
        return ""
    return f"{'課税売上' if side == 'sales' else '課税仕入'} {m.group(1)}%"


def _check(value: str, field: str, row: int) -> str:
    if '"' in value or "\n" in value or "\r" in value:
        raise MfImportError(f"row {row}: {field} contains a quote or a line break")
    for key, limit in LIMITS.items():
        if key in field and len(value) > limit:
            raise MfImportError(f"row {row}: {field} longer than {limit}")
    return value


def registered_ids(mf_export_csv: bytes) -> Set[str]:
    """MFの仕訳帳エクスポートCSVから、仕訳メモに入れた GOS:journal_id を集める（文字コードは自動判別）。"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = mf_export_csv.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise MfImportError("MF export is neither UTF-8 nor Shift_JIS")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or "仕訳メモ" not in rows[0]:
        raise MfImportError("not an MF 仕訳帳 export: no 仕訳メモ column")
    i = rows[0].index("仕訳メモ")
    return {m for r in rows[1:] if len(r) > i for m in MEMO_TOKEN.findall(r[i])}


def build_csv(entries: Iterable[JournalEntry], *, registered: Optional[Set[str]] = None,
              start_no: int = 1, encoding: str = "cp932") -> Dict:
    """confidence=high の仕訳だけ。registered に入っている journal_id は出さない（重複登録防止）。"""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(HEADER)
    skipped_registered, skipped_unconfirmed, written = [], [], []
    no = start_no
    for n, e in enumerate(entries, start=1):
        if e.confidence != "high":
            skipped_unconfirmed.append(e.journal_id)
            continue
        if registered and e.journal_id in registered:
            skipped_registered.append(e.journal_id)
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.journal_date or ""):
            raise MfImportError(f"row {n}: journal_date {e.journal_date!r}")
        d, c = float(e.debit_amount), float(e.credit_amount)
        if d != round(d) or c != round(c) or d <= 0 or round(d) != round(c):
            raise MfImportError(f"row {n}: amounts must be equal positive whole yen")
        if no > 999_999_999:
            raise MfImportError("取引No exceeds 9 digits")
        sales_side = "売上" in (e.credit_account or "")
        debit_tax = "対象外" if e.debit_account in BALANCE_ACCOUNTS else _mf_tax(e.tax_category, "purchase")
        credit_tax = "対象外" if e.credit_account in BALANCE_ACCOUNTS else _mf_tax(e.tax_category, "sales" if sales_side else "purchase")
        memo = _check(f"GOS:{e.journal_id}" + (f" {e.memo}" if e.memo else ""), "仕訳メモ", n)
        row = [str(no), e.journal_date.replace("-", "/"),
               _check(e.debit_account, "借方勘定科目", n), _check(e.debit_subaccount, "借方補助科目", n), "", _check(e.counterparty, "借方取引先", n),
               debit_tax, "", str(int(round(d))), "0",
               _check(e.credit_account, "貸方勘定科目", n), _check(e.credit_subaccount, "貸方補助科目", n), "", _check(e.counterparty, "貸方取引先", n),
               credit_tax, "", str(int(round(c))), "0",
               _check(e.description, "摘要", n), memo, "", "", "", "", "", "", ""]
        if not row[2] or not row[10]:
            raise MfImportError(f"row {n}: account required")
        w.writerow(row)
        written.append(e.journal_id)
        no += 1
    text = out.getvalue()
    return {"bytes": text.encode(encoding), "encoding": encoding, "written": written, "skipped_registered": skipped_registered,
            "skipped_unconfirmed": skipped_unconfirmed, "header_verified": HEADER_VERIFIED}
