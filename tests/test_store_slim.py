"""Slim layout: no copied columns are stored; joined reads put them back."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from borrowcycle.pipeline import config as cfg
from borrowcycle.pipeline import store as st
from borrowcycle.pipeline.store import LIB_BORROW, LIB_FUTURES, LIB_SPOT


def _idx(n, freq="15min"):
    return pd.date_range("2025-01-01 09:15", periods=n, freq=freq, tz="Asia/Kolkata")


def test_writes_strip_and_reads_join(store):
    idx = _idx(4)
    panel = pd.DataFrame({"F1_close": [10.0, 11, 12, 13], "F1_oi": 5, "cycle_id": 1,
                          "SPOT_close": 1.0, "spot_missing": False}, index=idx)
    store.write(LIB_FUTURES, "T/15minute", panel)
    store.write(LIB_SPOT, "T/15minute", pd.DataFrame(
        {"open": 9.0, "high": 9.0, "low": 9.0, "close": [9.0, 9.5, 9.7, 9.9], "volume": 1}, index=idx))
    store.write(LIB_BORROW, "T/15minute", panel.assign(b12=0.2, F1_expiry="2025-01-30"))
    assert set(store.columns(LIB_FUTURES, "T/15minute")) == {"F1_close", "F1_oi", "cycle_id"}
    assert store.columns(LIB_BORROW, "T/15minute") == ["b12"]
    full = store.read_borrow("T", "15minute")
    assert {"F1_close", "F1_oi", "cycle_id", "SPOT_close", "b12"} <= set(full.columns)
    assert full["SPOT_close"].tolist() == [9.0, 9.5, 9.7, 9.9]
    assert store.read_panel("T", "15minute")["spot_missing"].sum() == 0


def test_kite_minute_beats_upstox_1minute(store):
    idx = _idx(3, "1min")
    k = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [5.0, 6, 7], "volume": 1}, index=idx)
    store.write(LIB_SPOT, "T/minute", k)
    store.write(LIB_SPOT, "T/1minute", k.iloc[-1:].assign(close=0.1))
    assert store.spot_symbol_for("T", "1minute") == "T/minute"
    assert "log_return" in store.columns(LIB_SPOT, "T/minute")      # 1-minute spot carries log columns


def test_upsert_adds_no_full_copy_and_aligns_schema(store):
    idx = _idx(6)
    store.write(LIB_BORROW, "T/15minute", pd.DataFrame({"b12": np.arange(4.0), "feat_x": 1.0,
                                                        "in_backwardation": True}, index=idx[:4]))
    store.upsert(LIB_BORROW, "T/15minute", pd.DataFrame({"b12": [8.0, 9.0], "in_backwardation": False},
                                                        index=idx[4:]))
    df = store.read(LIB_BORROW, "T/15minute")
    assert len(df) == 6 and np.isnan(df["feat_x"].iloc[-1]) and df["feat_x"].iloc[0] == 1.0
    assert not df["in_backwardation"].iloc[-1] and df["in_backwardation"].iloc[0]
    assert store.versions(LIB_BORROW, "T/15minute") == 1         # no old version left behind
    with pytest.raises(ValueError):
        store.upsert(LIB_BORROW, "T/15minute", pd.DataFrame({"brand_new": [1.0]}, index=idx[5:]))


ENGINE_STORE = cfg.ENGINE_ROOT / "data" / "storage" / "arctic_store.py"


@pytest.mark.skipif(not ENGINE_STORE.exists(), reason="backtesting engine not present")
def test_slim_rules_match_engine():
    src = ENGINE_STORE.read_text(encoding="utf-8")
    ns: dict = {}
    for name in ("FUTURES_SPOT_COLUMNS", "BORROW_COPIED_COLUMNS", "SPOT_INTERVAL_ALIASES"):
        start = src.index(f"{name} = ")
        end = src.index(")\n", start) + 1 if src[start:].split("=", 1)[1].strip().startswith("(") \
            else src.index("\n", start)
        exec(src[start:end], ns)
    assert ns["FUTURES_SPOT_COLUMNS"] == st.FUTURES_SPOT_COLUMNS
    assert ns["BORROW_COPIED_COLUMNS"] == st.BORROW_COPIED_COLUMNS
    assert ns["SPOT_INTERVAL_ALIASES"] == cfg.SPOT_INTERVAL_ALIASES
    assert st.PANEL_FIELD.pattern in src
