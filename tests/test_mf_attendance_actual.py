"""MF勤怠 打刻CSV → 実労働分、損益分岐v2の labor_source。Issue #72 L 第1段。

フィクスチャは合成（TESTGUEST系の架空氏名）。実ゴールデン（mf-attendance-automation の
確定分CSV、実名入り）はリポジトリへ複製しない — MF_ATTENDANCE_GOLDEN_CSV が指す時だけ読む。
"""
import json
import os
from pathlib import Path

import pytest

from yuge_finance.accounting import breakeven_v2 as v2
from yuge_finance.ingest import mf_attendance_csv as mfa

HEADER = "従業員番号,苗字,名前,打刻所属日,打刻日,打刻時間,打刻種別"
FAKE_NAMES = ("テスト", "太郎", "サンプル", "花子", "ダミー", "次郎")

ROWS = [
    # 3: 休憩1回 → 06:54–17:08 = 614分、休憩 51分 → 563分
    "3,テスト,太郎,2026/07/01,2026/07/01,06:54,出勤",
    "3,テスト,太郎,2026/07/01,2026/07/01,13:08,休憩開始",
    "3,テスト,太郎,2026/07/01,2026/07/01,13:59,休憩終了",
    "3,テスト,太郎,2026/07/01,2026/07/01,17:08,退勤",
    # 3: 休憩2回（行順が崩れていても時刻で並べる）08:00–17:00 = 540、休憩 30+15 → 495
    "3,テスト,太郎,2026/07/02,2026/07/02,17:00,退勤",
    "3,テスト,太郎,2026/07/02,2026/07/02,08:00,出勤",
    "3,テスト,太郎,2026/07/02,2026/07/02,10:00,休憩開始",
    "3,テスト,太郎,2026/07/02,2026/07/02,10:30,休憩終了",
    "3,テスト,太郎,2026/07/02,2026/07/02,15:00,休憩開始",
    "3,テスト,太郎,2026/07/02,2026/07/02,15:15,休憩終了",
    # 05（先頭ゼロ保持）: 日またぎ。所属日 07/03、退勤は 07/04 02:00 → 22:00–02:00 = 240
    "05,サンプル,花子,2026/07/03,2026/07/03,22:00,出勤",
    "05,サンプル,花子,2026/07/03,2026/07/04,02:00,退勤",
    # 05: 出勤のみ（ゴールデンにも実在する形）→ 例外、分数を出さない
    "05,サンプル,花子,2026/07/15,2026/07/15,07:00,出勤",
    # 8: 休憩終了が無い → 例外
    "8,ダミー,次郎,2026/07/10,2026/07/10,09:00,出勤",
    "8,ダミー,次郎,2026/07/10,2026/07/10,12:00,休憩開始",
    "8,ダミー,次郎,2026/07/10,2026/07/10,18:00,退勤",
    # 8: 正常 09:00–13:00 = 240
    "8,ダミー,次郎,2026/07/11,2026/07/11,09:00,出勤",
    "8,ダミー,次郎,2026/07/11,2026/07/11,13:00,退勤",
]


def write_csv(path: Path, rows, header=HEADER, encoding="utf-8-sig") -> Path:
    path.write_bytes(("\n".join([header, *rows]) + "\n").encode(encoding))
    return path


def test_pairs_punches_per_work_date_and_refuses_unpaired_days(tmp_path):
    month = mfa.load(write_csv(tmp_path / "punch.csv", ROWS), "2026-07")
    got = {(d.employee_no, d.work_date): (d.worked_minutes, d.break_minutes) for d in month.days}
    assert got == {("3", "2026-07-01"): (563, 51), ("3", "2026-07-02"): (495, 45),
                   ("05", "2026-07-03"): (240, 0), ("8", "2026-07-11"): (240, 0)}
    assert [(x.employee_no, x.work_date, x.reason) for x in month.exceptions] == [
        ("05", "2026-07-15", "unpaired_clock_in_out"), ("8", "2026-07-10", "unpaired_break")]
    s = month.summary()
    assert s["complete"] is False and s["worked_minutes"] == 563 + 495 + 240 + 240
    assert s["per_employee"] == {"3": {"days": 2, "worked_minutes": 1058, "break_minutes": 96},
                                 "05": {"days": 1, "worked_minutes": 240, "break_minutes": 0},
                                 "8": {"days": 1, "worked_minutes": 240, "break_minutes": 0}}


def test_names_never_reach_outputs(tmp_path):
    month = mfa.load(write_csv(tmp_path / "punch.csv", ROWS), "2026-07")
    blob = json.dumps([month.summary(), v2.labor_actual(month.summary())], ensure_ascii=False)
    assert not any(n in blob for n in FAKE_NAMES)


@pytest.mark.parametrize("rows,reason", [
    (["3,テスト,太郎,2026/07/05,2026/07/05,08:00,出勤", "3,テスト,太郎,2026/07/05,2026/07/05,08:00,退勤"], "duplicate_timestamp"),
    (["3,テスト,太郎,2026/07/05,2026/07/05,08:00,出勤", "3,テスト,太郎,2026/07/05,2026/07/05,12:00,出勤",
      "3,テスト,太郎,2026/07/05,2026/07/05,17:00,退勤"], "unpaired_clock_in_out"),
    (["3,テスト,太郎,2026/07/05,2026/07/05,07:00,休憩開始", "3,テスト,太郎,2026/07/05,2026/07/05,08:00,出勤",
      "3,テスト,太郎,2026/07/05,2026/07/05,17:00,退勤"], "punch_outside_shift"),
    (["3,テスト,太郎,2026/07/05,2026/07/05,08:00,出勤", "3,テスト,太郎,2026/07/05,2026/07/05,10:00,休憩終了",
      "3,テスト,太郎,2026/07/05,2026/07/05,11:00,休憩開始", "3,テスト,太郎,2026/07/05,2026/07/05,17:00,退勤"], "unpaired_break"),
    (["3,テスト,太郎,2026/07/05,2026/07/05,08:00,出勤", "3,テスト,太郎,2026/07/05,2026/07/06,09:00,退勤"], "shift_over_24h"),
    (["3,テスト,太郎,2026/07/05,2026/07/05,08:00,出勤", "3,テスト,太郎,2026/07/05,2026/07/05,17:00,直行"], "unknown_punch_type"),
    (["3,テスト,太郎,2026/07/05,2026/07/05,08:00,出勤", "3,テスト,太郎,2026/07/05,2026/07/05,25:00,退勤"], "invalid_punch_time"),
    (["3,テスト,太郎,2026/08/01,2026/08/01,08:00,出勤", "3,テスト,太郎,2026/08/01,2026/08/01,17:00,退勤"], "outside_month"),
])
def test_ambiguous_days_are_exceptions_not_guesses(tmp_path, rows, reason):
    month = mfa.load(write_csv(tmp_path / "p.csv", rows), "2026-07")
    assert month.days == [] and [x.reason for x in month.exceptions] == [reason]


def test_rejects_wrong_file_shapes(tmp_path):
    shift_header = "従業員番号,苗字,名前,日付,勤怠区分,勤務パターン,開始時刻,終了時刻"
    with pytest.raises(ValueError, match="ヘッダー不一致"):
        mfa.read_punch_rows(write_csv(tmp_path / "shift.csv", ["3,テスト,太郎,2026/07/01,平日,,00:00,23:59"],
                                      header=shift_header, encoding="cp932"))
    with pytest.raises(ValueError, match="列数"):
        mfa.read_punch_rows(write_csv(tmp_path / "short.csv", ["3,テスト,太郎,2026/07/01"]))
    # Shift_JIS 保存でも同じヘッダーなら読める
    assert len(mfa.read_punch_rows(write_csv(tmp_path / "sj.csv", ROWS[:4], encoding="cp932"))) == 4


def test_labor_actual_never_invents_yen():
    complete = {"month": "2026-07", "complete": True, "employee_count": 2, "worked_days": 3, "worked_hours": 20.0, "exceptions": []}
    assert v2.labor_actual(complete)["cost"] is None
    assert v2.labor_actual(complete)["cost_status"] == "needs_payroll_export"
    assert v2.labor_actual({**complete, "complete": False, "exceptions": [{}]}, payroll_cost_jpy=500_000)["cost"] is None
    got = v2.labor_actual(complete, payroll_cost_jpy=500_000)
    assert got["cost_status"] == "computed" and got["cost"] == 500_000 and got["labor_source"] == "mf_attendance_actual"


CAPS = [14] * 31
FLAT = {"fixed_staff": {"matsumoto": {"monthly_salary_gross": 334000, "expected_workdays_base": 22}},
        "front": {"cost_per_uncovered_occupied_day": 9600}, "cleaning": {"base_cost_per_occupied_day": 5500,
        "additional_high_occupancy_cost_per_day": 5500, "high_occupancy_threshold": 0.7}, "night_security": {"cost_per_occupied_day": 7500}}
ACTUAL = {"revenue": 3_000_000.0, "sold_room_nights": 200}


def test_default_build_is_assumption_and_numbers_unchanged():
    out = v2.build(CAPS, 15_000.0, 0.26875, 700_000.0, FLAT, ACTUAL)
    assert out["scenario"]["labor_source"] == "assumption" and out["actual"]["labor_source"] == "assumption"
    assert "labor_actual" not in out
    expected_labor = v2.labor_for_days(FLAT, v2.even_distribution(200, CAPS), CAPS)
    assert out["actual"]["labor"] == round(expected_labor)
    # 勤怠だけ（円なし）でも実績点の数字は仮定のまま
    hours_only = v2.labor_actual({"month": "2026-07", "complete": True, "worked_hours": 300.0, "exceptions": []})
    with_hours = v2.build(CAPS, 15_000.0, 0.26875, 700_000.0, FLAT, ACTUAL, hours_only)
    assert {k: v for k, v in with_hours.items() if k != "labor_actual"} == out
    assert with_hours["labor_actual"]["worked_hours"] == 300.0


def test_actual_point_uses_payroll_cost_but_bep_curve_stays_assumption():
    base = v2.build(CAPS, 15_000.0, 0.26875, 700_000.0, FLAT, ACTUAL)
    la = v2.labor_actual({"month": "2026-07", "complete": True, "worked_hours": 300.0, "exceptions": []}, payroll_cost_jpy=900_000)
    out = v2.build(CAPS, 15_000.0, 0.26875, 700_000.0, FLAT, ACTUAL, la)
    assert out["actual"]["labor"] == 900_000 and out["actual"]["labor_source"] == "mf_attendance_actual"
    assert out["actual"]["profit_after_mc"] != base["actual"]["profit_after_mc"]
    assert out["breakeven_room_nights"] == base["breakeven_room_nights"]
    assert out["at_breakeven"]["labor_source"] == "assumption"


GOLDEN = os.environ.get("MF_ATTENDANCE_GOLDEN_CSV")


@pytest.mark.skipif(not GOLDEN or not Path(GOLDEN).is_file(), reason="MF_ATTENDANCE_GOLDEN_CSV 未指定（実名入りのため非同梱）")
def test_real_golden_confirmed_export_parses_without_names():
    rows = mfa.read_punch_rows(Path(GOLDEN))
    month = mfa.parse_punches(rows, "2026-07")
    # 独立計算: 所属日ごとに 退勤−出勤 − Σ(休憩終了−休憩開始)、完全な日だけ
    from collections import defaultdict
    from datetime import datetime
    by_day = defaultdict(list)
    for r in rows:
        by_day[(r["従業員番号"], r["打刻所属日"])].append((datetime.strptime(r["打刻日"] + r["打刻時間"], "%Y/%m/%d%H:%M"), r["打刻種別"]))
    total, complete_days = 0, 0
    for punches in by_day.values():
        kinds = {k: t for t, k in punches}
        if "出勤" in kinds and "退勤" in kinds:
            complete_days += 1
            total += (kinds["退勤"] - kinds["出勤"]).seconds // 60
            if "休憩開始" in kinds:
                total -= (kinds["休憩終了"] - kinds["休憩開始"]).seconds // 60
    assert len(month.days) == complete_days and month.total_worked_minutes() == total
    assert [x.reason for x in month.exceptions] == ["unpaired_clock_in_out"]
    names = {r[c] for r in rows for c in ("苗字", "名前")}
    blob = json.dumps(month.summary(), ensure_ascii=False)
    assert not any(n in blob for n in names)
