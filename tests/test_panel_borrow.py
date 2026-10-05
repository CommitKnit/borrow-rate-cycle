"""Panel slotting, cycle_id continuity, and borrow-rate formulas."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from borrowcycle.pipeline import borrow
from borrowcycle.pipeline.panel import PANEL_COLUMNS, build_panel
from borrowcycle.pipeline.upstox import parse_candles

from conftest import daily_candles


def _contracts(expiries, start=date(2025, 9, 1)):
    frames = [parse_candles(daily_candles(start, date.fromisoformat(e), 100 + i), f"K{i}", "T", e, "1day")
              for i, e in enumerate(expiries)]
    return pd.concat(frames).sort_index(kind="stable")


EXP = ["2025-10-28", "2025-11-25", "2025-12-30", "2026-01-27"]


def test_slots_and_schema():
    p = build_panel(_contracts(EXP), "T", "1day")
    assert list(p.columns) == PANEL_COLUMNS and len(PANEL_COLUMNS) == 31
    assert not any(c.startswith("SPOT_") for c in p.columns)
    day = lambda d: p.loc[pd.Timestamp(d, tz="Asia/Kolkata")]
    assert day("2025-10-01")["F1_expiry"] == "2025-10-28" and day("2025-10-01")["F3_expiry"] == "2025-12-30"
    assert day("2025-11-03")["F1_expiry"] == "2025-11-25"     # rolled after the Oct expiry
    assert p["cycle_id"].is_monotonic_increasing and p["cycle_id"].iloc[0] == 1
    assert day("2025-10-28")["is_expiry_day"] and not day("2025-10-27")["is_expiry_day"]


def test_extension_continues_cycle_id():
    contracts = _contracts(EXP)
    full = build_panel(contracts, "T", "1day")
    cut = pd.Timestamp("2025-12-01", tz="Asia/Kolkata")
    head = full[full.index < cut]
    seg = build_panel(contracts[contracts.index >= cut], "T", "1day",
                      prev_f1_expiry=head["F1_expiry"].iloc[-1], prev_cycle_id=int(head["cycle_id"].iloc[-1]))
    pd.testing.assert_frame_equal(seg, full[full.index >= cut], check_freq=False)


def test_borrow_formulas_and_dte_star():
    idx = pd.date_range("2025-01-06 09:15", periods=25, freq="15min", tz="Asia/Kolkata")
    j = pd.DataFrame({"F1_close": 101.0, "F2_close": 102.0, "F3_close": 103.0, "SPOT_close": 100.0,
                      "F1_dte": 10, "F2_dte": 38, "F3_dte": 66, "F1_oi": 200, "F2_oi": 100,
                      "is_expiry_day": False}, index=idx)
    out = borrow.compute(j, "15minute", r=0.0625)
    assert list(out.columns) == borrow.INTRADAY_COLUMNS
    assert out["F1_dte_star"].iloc[0] == pytest.approx(11 - 1 / 25)
    assert out["F1_dte_star"].iloc[-1] == pytest.approx(10.0)
    t1, t2 = out["F1_dte_star"].iloc[-1] / 365, out["F2_dte_star"].iloc[-1] / 365
    assert out["b1"].iloc[-1] == pytest.approx(max(0.0, 0.0625 - np.log(101 / 100) / t1))
    assert out["b12"].iloc[-1] == pytest.approx(max(0.0, 0.0625 - np.log(102 / 101) / (t2 - t1)))
    assert out["OI_ratio"].iloc[0] == 2.0 and not out["in_backwardation"].any()


def test_daily_borrow_has_no_urgency():
    idx = pd.date_range("2025-01-06", periods=3, freq="B", tz="Asia/Kolkata")
    j = pd.DataFrame({"F1_close": 99.0, "F2_close": 100.0, "F3_close": 101.0, "SPOT_close": 100.0,
                      "F1_dte": 5, "F2_dte": 33, "F3_dte": 61, "F1_oi": 1, "F2_oi": 1,
                      "is_expiry_day": False}, index=idx)
    out = borrow.compute(j, "1day")
    assert list(out.columns) == borrow.DAILY_COLUMNS and out["in_backwardation"].all()
    assert (out["F1_dte_star"] == 5).all()
