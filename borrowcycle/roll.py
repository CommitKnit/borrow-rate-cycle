"""
Roll mechanics — the F1-F2 premium measured against the open-interest transfer.

Everything here works at end of session (the last 15-minute bar of each trading
day), on an axis of **trading sessions to F1 expiry** (0 = expiry day). Calendar
days to expiry are deliberately not used for event time: NSE monthly expiry moved
from the last Thursday to the last Tuesday in September 2025, so a given calendar
DTE lands on different weekdays in different cycles and pooling on it produces a
spurious weekly saw-tooth.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: F1/F2 open-interest ratio bins, from "roll not started" to "roll complete".
OI_BINS = [0, 0.25, 0.5, 1, 2, 4, 8, 16, np.inf]
OI_BIN_LABELS = ["<0.25", "0.25-0.5", "0.5-1", "1-2", "2-4", "4-8", "8-16", ">16"]

#: OI-ratio levels reported as roll milestones.
OI_LEVELS = (4.0, 2.0, 1.0, 0.5)


def spread_bps(cyc: pd.DataFrame) -> pd.Series:
    """F1 - F2 in basis points of F1 (forward-filled closes)."""
    f1, f2 = cyc["F1_close"].ffill(), cyc["F2_close"].ffill()
    return (f1 - f2) / f1 * 1e4


def f1_expiry_date(cyc: pd.DataFrame) -> pd.Timestamp:
    """The cycle's F1 expiry date: the most common (bar date + F1_dte)."""
    days = pd.to_timedelta(cyc["F1_dte"].astype(float).to_numpy(), unit="D")
    exp = pd.DatetimeIndex(cyc.index.normalize() + days).normalize()
    return pd.Series(exp).value_counts().idxmax()


def sessions_to_expiry(dates: pd.DatetimeIndex, expiry: pd.Timestamp) -> np.ndarray:
    """Trading sessions from each date to ``expiry`` (0 on expiry day).

    Uses the cycle's own trading dates when they reach the expiry (so exchange
    holidays are respected), and weekday counting for the part beyond the data.
    """
    dates = pd.DatetimeIndex(dates).normalize()
    known = dates[dates <= expiry].unique().sort_values()
    out = np.empty(len(dates), dtype=float)
    last_known = known[-1] if len(known) else None
    for i, d in enumerate(dates):
        if d > expiry:
            out[i] = np.nan
            continue
        after = int(((known > d) & (known <= expiry)).sum())
        gap = 0
        if last_known is not None and last_known < expiry:     # data stops before expiry
            gap = int(np.busday_count(last_known.date(), expiry.date()))
        out[i] = after + gap
    return out


def session_frame(cyc: pd.DataFrame) -> pd.DataFrame:
    """End-of-session view of one cycle with the roll measures."""
    bars = cyc.assign(spread_bps=spread_bps(cyc))
    eod = bars.groupby(bars.index.normalize()).tail(1).copy()
    f1, f2 = eod["F1_oi"].astype(float), eod["F2_oi"].astype(float)
    ok = (f1 > 0) & (f2 > 0)
    eod["oi_share_f1"] = (f1 / (f1 + f2)).where(ok)
    eod["oi_ratio"] = (f1 / f2).where(ok)
    eod["sessions_left"] = sessions_to_expiry(eod.index, f1_expiry_date(cyc))
    cols = ["sessions_left", "F1_dte", "spread_bps", "oi_share_f1", "oi_ratio"]
    if "SPOT_close" in eod.columns:      # where the borrow sits: spot vs F1 vs F2, in bps of spot
        s = eod["SPOT_close"].astype(float)
        eod["spot_f1_bps"] = (s - eod["F1_close"]) / s * 1e4
        eod["spot_f2_bps"] = (s - eod["F2_close"]) / s * 1e4
        cols += ["spot_f1_bps", "spot_f2_bps"]
    for b in ("b1", "b2", "b12"):        # annualised implied borrow, in %
        if b in eod.columns:
            eod[f"{b}_pct"] = eod[b].astype(float) * 100
            cols.append(f"{b}_pct")
    return eod[cols]


def first_below(sess: pd.DataFrame, level: float):
    """Index label of the first session whose OI ratio is below ``level`` (or None)."""
    hit = sess.index[sess["oi_ratio"] < level]
    return hit[0] if len(hit) else None


def roll_midpoint(sess: pd.DataFrame):
    """The first session where the front month holds less open interest than the next."""
    return first_below(sess, 1.0)


def oi_bin(ratio: pd.Series) -> pd.Series:
    return pd.cut(ratio, OI_BINS, labels=OI_BIN_LABELS, right=True)
