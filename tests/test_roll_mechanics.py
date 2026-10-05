"""Roll helpers: session counting across weekends/holidays and expiry weekdays, milestones, bins."""
import numpy as np
import pandas as pd

from borrowcycle import roll

TZ = "Asia/Kolkata"


def _cycle(dates, expiry, oi_f1, oi_f2, f1=101.0, f2=100.0):
    idx = pd.DatetimeIndex([pd.Timestamp(d + " 15:15", tz=TZ) for d in dates])
    exp = pd.Timestamp(expiry, tz=TZ)
    return pd.DataFrame({"F1_close": f1, "F2_close": f2, "F1_oi": oi_f1, "F2_oi": oi_f2,
                         "F1_dte": [(exp - ts.normalize()).days for ts in idx]}, index=idx)


def test_sessions_across_weekend_thursday_expiry():
    c = _cycle(["2025-08-22", "2025-08-25", "2025-08-26", "2025-08-27", "2025-08-28"],
               "2025-08-28", [9, 8, 4, 2, 1], [1, 1, 2, 3, 6])
    s = roll.session_frame(c)
    assert s["sessions_left"].tolist() == [4, 3, 2, 1, 0]       # Fri -> Thu, weekend skipped


def test_sessions_tuesday_expiry_and_holiday():
    # 2025-10-02 (Gandhi Jayanti) is missing from the data: it must not be counted
    c = _cycle(["2025-09-29", "2025-09-30", "2025-10-01", "2025-10-03", "2025-10-06", "2025-10-07"],
               "2025-10-07", 5, 1)
    assert roll.session_frame(c)["sessions_left"].tolist() == [5, 4, 3, 2, 1, 0]


def test_sessions_when_data_stops_before_expiry():
    c = _cycle(["2026-03-02", "2026-03-03"], "2026-03-10", 5, 1)
    # 03-03 -> 03-10 is 5 weekdays after the last known session
    assert roll.session_frame(c)["sessions_left"].tolist() == [6, 5]


def test_midpoint_and_milestones():
    c = _cycle(["2025-08-22", "2025-08-25", "2025-08-26", "2025-08-27", "2025-08-28"],
               "2025-08-28", [9, 8, 4, 2, 1], [1, 1, 2, 3, 6])
    s = roll.session_frame(c)
    assert s.loc[roll.roll_midpoint(s), "sessions_left"] == 1      # 2/3 < 1 first on 08-27
    assert s.loc[roll.first_below(s, 4.0), "sessions_left"] == 2   # 8 -> 2 on 08-26
    assert roll.first_below(s, 0.01) is None


def test_bins_and_spread():
    r = pd.Series([0.1, 0.25, 0.3, 1.0, 1.5, 3.9, 4.0, 20.0])
    assert roll.oi_bin(r).astype(str).tolist() == ["<0.25", "<0.25", "0.25-0.5", "0.5-1", "1-2",
                                                    "2-4", "2-4", ">16"]
    c = _cycle(["2025-08-27"], "2025-08-28", 2, 1, f1=101.0, f2=100.0)
    assert np.isclose(roll.spread_bps(c).iloc[0], 1 / 101 * 1e4)
