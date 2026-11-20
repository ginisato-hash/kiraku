"""MF勤怠 打刻インポートCSV（確定分）→ 従業員番号×打刻所属日の実労働分。Issue #72 L 第1段。

入力は mf-attendance-automation が出す確定分CSV（UTF-8 BOM）:
    従業員番号,苗字,名前,打刻所属日,打刻日,打刻時間,打刻種別
打刻種別は 出勤 / 退勤 / 休憩開始 / 休憩終了。時刻は 打刻日+打刻時間 で絶対化し、
打刻所属日ごとに 出勤…退勤 の拘束から 休憩開始…休憩終了 を差し引く（日またぎ勤務も
所属日に寄せる）。対にならない日・順序が崩れた日・同時刻の打刻は推定で埋めず、
その日を丸ごと例外にして分数を出さない。

氏名列は読まない。出力の従業員識別子は従業員番号（文字列・先頭ゼロ保持）だけ。
給与額・時給・事業主負担はこのCSVに無い。ここでは時間だけを扱う。
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PUNCH_HEADER = ["従業員番号", "苗字", "名前", "打刻所属日", "打刻日", "打刻時間", "打刻種別"]
CLOCK_IN, CLOCK_OUT, BREAK_START, BREAK_END = "出勤", "退勤", "休憩開始", "休憩終了"
PUNCH_TYPES = {CLOCK_IN, CLOCK_OUT, BREAK_START, BREAK_END}
MAX_SHIFT_MINUTES = 24 * 60  # 所属日の拘束が24時間を超えるのは打刻誤りとして扱う


@dataclass(frozen=True)
class WorkedDay:
    employee_no: str
    work_date: str  # YYYY-MM-DD（打刻所属日）
    span_minutes: int  # 出勤→退勤
    break_minutes: int
    worked_minutes: int


@dataclass(frozen=True)
class AttendanceException:
    employee_no: str
    work_date: str
    reason: str
    detail: str = ""


@dataclass
class AttendanceMonth:
    month: Optional[str]
    days: List[WorkedDay] = field(default_factory=list)
    exceptions: List[AttendanceException] = field(default_factory=list)

    def per_employee(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for d in self.days:
            e = out.setdefault(d.employee_no, {"days": 0, "worked_minutes": 0, "break_minutes": 0})
            e["days"] += 1
            e["worked_minutes"] += d.worked_minutes
            e["break_minutes"] += d.break_minutes
        return dict(sorted(out.items(), key=lambda kv: _emp_sort_key(kv[0])))

    def total_worked_minutes(self) -> int:
        return sum(d.worked_minutes for d in self.days)

    def summary(self) -> Dict:
        """出力用（氏名なし）。例外がある月は complete=False — 下流は実績を全量とみなさない。"""
        return {"month": self.month, "complete": not self.exceptions,
                "employee_count": len({d.employee_no for d in self.days}),
                "worked_days": len(self.days), "worked_minutes": self.total_worked_minutes(),
                "worked_hours": round(self.total_worked_minutes() / 60, 2),
                "per_employee": self.per_employee(),
                "exceptions": [vars(x) for x in self.exceptions]}


def _emp_sort_key(no: str) -> Tuple[int, int, str]:
    return (0, int(no), no) if no.isdigit() else (1, 0, no)


def _decode(raw: bytes) -> str:
    # 黙って置換文字で読まない（csvio.read_dicts とは違い errors="replace" へ落ちない）
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("MF勤怠CSVの文字コードを判定できません（UTF-8 / Shift_JIS 以外）")


def read_punch_rows(path: Path) -> List[Dict[str, str]]:
    """確定分CSVを読む。ヘッダーは完全一致のみ受け付ける（シフトCSV等の取り違えを拒否）。"""
    rows = list(csv.reader(io.StringIO(_decode(Path(path).read_bytes()), newline="")))
    if not rows:
        raise ValueError(f"空のCSVです: {Path(path).name}")
    header = [h.strip() for h in rows[0]]
    if header != PUNCH_HEADER:
        raise ValueError(f"MF勤怠 打刻CSVのヘッダー不一致: {header}")
    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        if len(r) != len(PUNCH_HEADER):
            raise ValueError(f"MF勤怠 打刻CSVの列数不一致（{len(r)}列）")
        out.append(dict(zip(PUNCH_HEADER, (c.strip() for c in r))))
    return out


def _date(s: str) -> Optional[str]:
    try:
        return datetime.strptime(s, "%Y/%m/%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _stamp(day: str, hhmm: str) -> Optional[datetime]:
    try:
        return datetime.strptime(f"{day} {hhmm}", "%Y/%m/%d %H:%M")
    except ValueError:
        return None


def _pair_day(punches: List[Tuple[datetime, str]]) -> Tuple[Optional[Tuple[int, int]], str]:
    """1所属日の打刻列 → ((拘束分, 休憩分), "") か (None, 理由)。"""
    punches = sorted(punches)
    times = [t for t, _ in punches]
    if len(set(times)) != len(times):
        return None, "duplicate_timestamp"
    kinds = [k for _, k in punches]
    if kinds.count(CLOCK_IN) != 1 or kinds.count(CLOCK_OUT) != 1:
        return None, "unpaired_clock_in_out"
    if kinds[0] != CLOCK_IN or kinds[-1] != CLOCK_OUT:
        return None, "punch_outside_shift"
    breaks = kinds[1:-1]
    if len(breaks) % 2 or any(k != (BREAK_START if i % 2 == 0 else BREAK_END) for i, k in enumerate(breaks)):
        return None, "unpaired_break"
    span = int((times[-1] - times[0]).total_seconds() // 60)
    if span > MAX_SHIFT_MINUTES:
        return None, "shift_over_24h"
    brk = sum(int((times[i + 1] - times[i]).total_seconds() // 60) for i in range(1, len(times) - 1, 2))
    return (span, brk), ""


def parse_punches(rows: List[Dict[str, str]], month: Optional[str] = None) -> AttendanceMonth:
    """month（YYYY-MM）を渡すと所属日がその月以外の行は例外（集計しない）。"""
    result = AttendanceMonth(month=month)
    groups: Dict[Tuple[str, str], List[Tuple[datetime, str]]] = defaultdict(list)
    bad: Dict[Tuple[str, str], str] = {}
    for r in rows:
        emp = r["従業員番号"]
        work_date = _date(r["打刻所属日"])
        key = (emp, work_date or r["打刻所属日"])
        if not emp:
            result.exceptions.append(AttendanceException("", key[1], "missing_employee_no"))
            continue
        if work_date is None:
            bad.setdefault(key, "invalid_work_date")
            continue
        if month and not work_date.startswith(month + "-"):
            bad.setdefault(key, "outside_month")
            continue
        if r["打刻種別"] not in PUNCH_TYPES:
            bad.setdefault(key, "unknown_punch_type")
            continue
        ts = _stamp(r["打刻日"], r["打刻時間"])
        if ts is None:
            bad.setdefault(key, "invalid_punch_time")
            continue
        groups[key].append((ts, r["打刻種別"]))
    for key, reason in bad.items():
        groups.pop(key, None)  # 1行でも壊れていればその日は丸ごと例外（部分集計しない）
        result.exceptions.append(AttendanceException(key[0], key[1], reason))
    for (emp, work_date), punches in groups.items():
        paired, reason = _pair_day(punches)
        if paired is None:
            result.exceptions.append(AttendanceException(emp, work_date, reason, " ".join(k for _, k in sorted(punches))))
            continue
        span, brk = paired
        result.days.append(WorkedDay(emp, work_date, span, brk, span - brk))
    result.days.sort(key=lambda d: (_emp_sort_key(d.employee_no), d.work_date))
    result.exceptions.sort(key=lambda x: (_emp_sort_key(x.employee_no), x.work_date, x.reason))
    return result


def load(path: Path, month: Optional[str] = None) -> AttendanceMonth:
    return parse_punches(read_punch_rows(path), month)
