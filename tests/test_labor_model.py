"""Phase H-3: 人件費予測モデル（Beds24日別稼働ベース・速報）。"""
from yuge_finance.accounting import labor_model
from yuge_finance.normalize.schema import BookingRecord


def _uniform_occupancy(month, rooms, start="2026-06-01", end="2026-07-01"):
    return [BookingRecord(booking_id="B1", checkin_date=start, checkout_date=end,
                          rooms=rooms, status="confirmed", gross_revenue=1000000).finalize()]


def test_60pct_occupancy_golden_values():
    """18室x30日x60%稼働、均等稼働、70%超日0日。"""
    bookings = _uniform_occupancy("2026-06", rooms=11)  # 18*0.6≈10.8→11室で近似
    res = labor_model.build("2026-06", bookings)
    assert res["labor_occupied_days"] == 30
    assert res["labor_high_occupancy_days"] == 0
    assert res["labor_total_low_case"] == 791200     # 松元23日
    assert res["labor_total_base_case"] == 800800     # 松元22日
    assert res["labor_total_high_case"] == 810400     # 松元21日


def test_zero_occupancy_no_extra_costs_but_fixed_salary_remains():
    res = labor_model.build("2026-06", [])
    assert res["labor_occupied_days"] == 0
    assert res["labor_extra_front_cost"] == 0
    assert res["labor_cleaning_cost"] == 0
    assert res["labor_night_security_cost"] == 0
    assert res["labor_fixed_salary_cost"] == 334000   # 稼働比例しない固定給


def test_high_occupancy_day_doubles_cleaning_cost():
    # 18室全室稼働(100%>70%)の1日だけ作る
    bookings = [BookingRecord(booking_id="B1", checkin_date="2026-06-10",
                              checkout_date="2026-06-11", rooms=18,
                              status="confirmed", gross_revenue=100000).finalize()]
    daily = labor_model.daily_occupancy("2026-06", bookings)
    day10 = next(d for d in daily if d["date"] == "2026-06-10")
    assert day10["high_occupancy_day_flag"] is True
    res = labor_model.build("2026-06", bookings)
    # 70%超日のcleaning_costは base(5500) + additional(5500) = 11000 (通常日の2倍)
    assert res["labor_cleaning_cost"] == 1 * 5500 + 1 * 5500


def test_matsumoto_fixed_salary_not_proportional_to_occupancy():
    low = labor_model.build("2026-06", [])
    high = labor_model.build("2026-06", _uniform_occupancy("2026-06", rooms=18))
    assert low["labor_fixed_salary_cost"] == high["labor_fixed_salary_cost"] == 334000
