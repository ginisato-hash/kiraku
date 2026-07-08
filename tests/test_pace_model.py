"""予約ペース判定モデル（達成率とは別軸）。

「達成率」= 現在のBeds24速報売上 ÷ 月間Cash BEP。月初・月中は低く出て当然（大幅未達等の表示を維持）。
「予約ペース」= 月内経過率に対して、最終的にBEPを達成できそうかを見る別軸の指標。
両者は別の入力を受け取れるため、達成率が低くても予約ペースがgreen/yellowになり得る。
"""
import datetime as dt

from yuge_finance.accounting import pace_model


def test_past_month_elapsed_rate_is_one():
    r = pace_model.build("2026-05", cash_operating_breakeven_revenue=2000000,
                        beds24_month_on_the_books_revenue=1000000,
                        now_jst=dt.datetime(2026, 6, 10, tzinfo=pace_model.JST))
    assert r["month_elapsed_rate"] == 1.0
    assert r["days_elapsed_in_month"] == r["days_in_month"]
    assert r["days_remaining_in_month"] == 0


def test_future_month_pace_is_unknown():
    r = pace_model.build("2026-12", cash_operating_breakeven_revenue=2000000,
                        beds24_month_on_the_books_revenue=0,
                        now_jst=dt.datetime(2026, 6, 10, tzinfo=pace_model.JST))
    assert r["month_elapsed_rate"] == 0.0
    assert r["booking_pace_status"] == "unknown"
    assert r["day_of_month"] == 0


def test_current_month_days_elapsed_and_in_month_computed():
    r = pace_model.build("2026-07", cash_operating_breakeven_revenue=2000000,
                        beds24_month_on_the_books_revenue=500000,
                        now_jst=dt.datetime(2026, 7, 8, tzinfo=pace_model.JST))
    assert r["days_in_month"] == 31
    assert r["day_of_month"] == 8
    assert r["days_elapsed_in_month"] == 8
    assert r["days_remaining_in_month"] == 23
    assert abs(r["month_elapsed_rate"] - 8 / 31) < 1e-6


def test_projected_month_end_bep_achievement_rate_is_computed():
    r = pace_model.build("2026-07", cash_operating_breakeven_revenue=2000000,
                        beds24_month_on_the_books_revenue=1000000,
                        now_jst=dt.datetime(2026, 7, 8, tzinfo=pace_model.JST))
    assert r["projected_month_end_revenue"] == 1000000
    assert r["projected_month_end_bep_achievement_rate"] == 0.5


def test_booking_pace_status_is_one_of_allowed_values():
    for otb in (0, 500000, 2000000, 5000000):
        r = pace_model.build("2026-07", cash_operating_breakeven_revenue=2000000,
                            beds24_month_on_the_books_revenue=otb,
                            now_jst=dt.datetime(2026, 7, 8, tzinfo=pace_model.JST))
        assert r["booking_pace_status"] in {"green", "yellow", "red", "unknown"}


def test_cash_bep_zero_or_none_gives_unknown():
    r = pace_model.build("2026-07", cash_operating_breakeven_revenue=0,
                        beds24_month_on_the_books_revenue=1000000,
                        now_jst=dt.datetime(2026, 7, 8, tzinfo=pace_model.JST))
    assert r["booking_pace_status"] == "unknown"


def test_ahead_of_schedule_but_below_absolute_target_is_green():
    """本番回帰テスト: booking_pace_achievement_rate=2.0399 (>=1.15) は green でなければならない。

    達成率(52.6%)は大幅未達水準でも、booking_pace_achievement_rate>=1.15なら green。
    projected_month_end_bep_achievement_rate は主判定に使わない。
    """
    r = pace_model.build("2026-07", cash_operating_breakeven_revenue=2009657,
                        beds24_month_on_the_books_revenue=1057943,
                        now_jst=dt.datetime(2026, 7, 8, tzinfo=pace_model.JST))
    achievement_rate = 1057943 / 2009657
    assert achievement_rate < 0.8  # 大幅未達水準（達成率とは別指標であることの確認）
    assert round(r["booking_pace_achievement_rate"], 4) == 2.0399
    assert r["booking_pace_status"] == "green"
    assert r["booking_pace_label"] == "グリーン"
    assert r["booking_pace_reason"] == "月内経過率に対して予約売上が十分先行しています。"


def test_pace_thresholds_exact_boundaries():
    def rate_at(otb, cash_bep, elapsed_rate):
        return pace_model._pace_status(cash_bep, elapsed_rate, otb / (cash_bep * elapsed_rate))

    assert rate_at(115, 100, 1.0)[0] == "green"    # 1.15 -> green
    assert rate_at(114.9, 100, 1.0)[0] == "yellow"  # <1.15 -> yellow
    assert rate_at(90, 100, 1.0)[0] == "yellow"     # 0.90 -> yellow
    assert rate_at(89.9, 100, 1.0)[0] == "red"      # <0.90 -> red


def test_projected_month_end_rate_not_used_for_primary_status():
    """projected_month_end_bep_achievement_rateが低くても、booking_pace_achievement_rateが
    閾値を満たせばgreenになる（主判定に使わないことの確認）。"""
    r = pace_model.build("2026-07", cash_operating_breakeven_revenue=2009657,
                        beds24_month_on_the_books_revenue=1057943,
                        now_jst=dt.datetime(2026, 7, 8, tzinfo=pace_model.JST))
    assert r["projected_month_end_bep_achievement_rate"] < 1.0  # 月末着地見込みは未達水準
    assert r["booking_pace_status"] == "green"  # だが主判定はgreen（別指標として残すだけ）


def test_far_behind_schedule_and_below_target_is_red():
    r = pace_model.build("2026-07", cash_operating_breakeven_revenue=2000000,
                        beds24_month_on_the_books_revenue=100000,
                        now_jst=dt.datetime(2026, 7, 20, tzinfo=pace_model.JST))
    assert r["booking_pace_status"] == "red"


def test_achievement_status_far_can_coexist_with_pace_green():
    """達成率(=大幅未達水準)と予約ペース(green)は独立した入力を持てる別指標であることを確認する。

    実運用の monthly.py では両者に同じBeds24速報売上を渡すため、達成率と
    projected_month_end_bep_achievement_rate は同値になり、green は実質的に
    達成率>=100%のときのみ生じる（この場合は「大幅未達」ではなく「達成」表記になる）。
    その代わり、月内進捗比で先行しているケースは pace=yellow として現れる
    （test_ahead_of_schedule_but_below_absolute_target_is_not_red で確認済み）。
    本テストは、pace_model.build() 自体が achievement 判定と独立した入力
    （beds24_month_on_the_books_revenue）を受け取れる、関数として decoupled な
    設計であることを確認するもの。
    """
    cash_bep = 2000000
    # 別途の「達成率」計算（大幅未達水準の売上を使う）
    low_revenue_for_achievement = 300000  # achievement_rate = 0.15 → 大幅未達
    achievement_rate = low_revenue_for_achievement / cash_bep
    assert achievement_rate < 0.8

    # pace_model には独立した(より新しい/より高い) OTB revenue を渡す
    r = pace_model.build("2026-05", cash_operating_breakeven_revenue=cash_bep,
                        beds24_month_on_the_books_revenue=cash_bep * 1.2,
                        now_jst=dt.datetime(2026, 6, 10, tzinfo=pace_model.JST))
    assert r["booking_pace_status"] == "green"
