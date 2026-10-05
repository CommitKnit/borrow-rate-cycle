"""Features: look-ahead freedom, optional IV group, both options layouts, incremental writes."""
import numpy as np
import pandas as pd
import pytest

from borrowcycle.pipeline import features
from borrowcycle.pipeline.run import build_features
from borrowcycle.pipeline.store import LIB_BORROW, LIB_FUTURES, LIB_OPTIONS


def _joined(n_cycles=4, bars_per_dte=3, seed=0):
    rng = np.random.default_rng(seed)
    rows, ts = [], pd.Timestamp("2025-01-01 09:15", tz="Asia/Kolkata")
    for cid in range(1, n_cycles + 1):
        for dte in (3, 2, 1, 0):
            for _ in range(bars_per_dte):
                rows.append({"timestamp": ts, "cycle_id": cid, "F1_dte": dte, "F1_dte_star": dte + 0.5,
                             "F1_oi": float(rng.integers(100, 1000)), "F2_oi": float(rng.integers(100, 1000)),
                             "F1_volume": 10.0, "F2_volume": 5.0, "F1_close": 100 + rng.normal(),
                             "b12": float(rng.normal())})
                ts += pd.Timedelta(minutes=15)
    return pd.DataFrame(rows).set_index("timestamp")


class _NoOptions:
    def option_expiries(self, *a):
        return []


def _compute(df):
    return features.compute(_NoOptions(), "T", "15minute", df, pd.Series(dtype=object))


@pytest.mark.parametrize("col", ["feat_roll_deviation", "feat_b12_percentile"])
def test_earlier_cycles_only(col):
    base = _joined()
    shocked = base.copy()
    last = shocked["cycle_id"] == shocked["cycle_id"].max()
    shocked.loc[last, ["F1_oi", "b12"]] = [1e9, 1e6]
    a, b = _compute(base), _compute(shocked)
    earlier = base["cycle_id"] < base["cycle_id"].max()
    pd.testing.assert_series_equal(a.loc[earlier, col], b.loc[earlier, col])
    assert a.loc[base["cycle_id"] == 1, col].isna().all()


def test_all_features_past_only():
    """Truncating the input never changes the value at an earlier bar."""
    df = _joined()
    full, part = _compute(df), _compute(df.iloc[:30])
    pd.testing.assert_frame_equal(full.iloc[:30], part, check_freq=False)


def test_columns_and_no_iv_without_options():
    out = _compute(_joined())
    assert list(out.columns) == features.FEATURES and len(features.FEATURES) == 25
    assert "feat_roll_fraction" not in out.columns
    assert out[features.IV_DEPENDENT].isna().all().all()


@pytest.mark.parametrize("layout", ["src", "fetch_options"])
def test_iv_from_both_options_layouts(store, layout):
    j = _joined(n_cycles=1)
    exp = "2025-01-30"
    rows = []
    for ts in j.index:
        for strike, typ, delta in [(100, "CE", 0.5), (100, "PE", -0.5), (110, "CE", 0.25), (90, "PE", -0.25)]:
            rows.append({"timestamp": ts, "strike": float(strike), "opt_type": typ, "delta": delta,
                         "iv": 0.3 + 0.01 * (strike - 100) / 10, "futures_ref_price": 100.0})
    opt = pd.DataFrame(rows).set_index("timestamp")
    if layout == "fetch_options":
        opt = opt.rename(columns={"strike": "strike_price", "opt_type": "instrument_type",
                                  "futures_ref_price": "futures_price"})
    store.write(LIB_OPTIONS, f"T/15minute/{exp}", opt)
    out = features.compute(store, "T", "15minute", j, pd.Series({1: exp}))
    assert out["feat_atm_iv"].round(6).eq(0.3).all()
    assert out["feat_otm_25d_call_iv"].round(6).eq(0.31).all()
    assert out["feat_25d_risk_reversal"].round(6).eq(0.02).all()


def test_build_features_writes_only_changed_rows(store):
    j = _joined()
    panel = j[["F1_oi", "F2_oi", "F1_volume", "F2_volume", "F1_close", "F1_dte", "cycle_id"]].assign(
        F1_expiry="2025-01-30")
    store.write(LIB_FUTURES, "T/15minute", panel.iloc[:36])
    store.write(LIB_BORROW, "T/15minute", j[["F1_dte_star", "b12"]].iloc[:36])
    assert "wrote" in build_features(store, "T", "15minute")[0]
    first = store.read(LIB_BORROW, "T/15minute")
    assert "up to date" in build_features(store, "T", "15minute")[0]
    store.upsert(LIB_FUTURES, "T/15minute", panel.iloc[36:])
    store.upsert(LIB_BORROW, "T/15minute", j[["F1_dte_star", "b12"]].iloc[36:])
    msg = build_features(store, "T", "15minute")[0]
    assert "updated 12 rows" in msg
    after = store.read(LIB_BORROW, "T/15minute")
    pd.testing.assert_frame_equal(after.iloc[:36], first, check_freq=False)
    assert after[features.FEATURES].iloc[36:].notna().any().any()
    assert store.versions(LIB_BORROW, "T/15minute") == 1
