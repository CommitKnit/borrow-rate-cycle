"""Option helpers — Black-76 pricing and ATM chain selection.

``black76_price`` is vendored from the source research repo's
options/pricing/black76.py so this package has no dependency on it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

#: Annualised risk-free rate used for the Black-76 fallback repricing.
RISK_FREE = 0.065


def black76_price(F: float, K: float, T: float, sigma: float,
                  r: float = RISK_FREE, is_call: bool = True) -> float:
    """Black-76 price of a European option on a futures contract."""
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        return max(0.0, (F - K) if is_call else (K - F))
    d1 = (np.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    disc = np.exp(-r * T)
    if is_call:
        return float(disc * (F * norm.cdf(d1) - K * norm.cdf(d2)))
    return float(disc * (K * norm.cdf(-d2) - F * norm.cdf(-d1)))


def get_opts_bar(o: pd.DataFrame, ts: pd.Timestamp,
                 window: pd.Timedelta = pd.Timedelta("45min")) -> pd.DataFrame:
    """The option chain snapshot nearest to ``ts``, within +/- ``window``."""
    if o.empty:
        return pd.DataFrame()
    ts_c = ts
    if o.index.tz is not None and ts.tzinfo is None:
        ts_c = ts.tz_localize(o.index.tz)
    elif o.index.tz is not None and ts.tzinfo is not None:
        ts_c = ts.tz_convert(o.index.tz)
    sub = o[(o.index >= ts_c - window) & (o.index <= ts_c + window)]
    if sub.empty:
        return pd.DataFrame()
    nearest = sub.index[np.argmin(np.abs((sub.index - ts_c).total_seconds()))]
    return sub[sub.index == nearest]


def px(bar: pd.DataFrame, K: float, otype: str) -> float:
    """Traded close for one strike/type, floored at the tick size."""
    r = bar[(bar["strike"] == K) & (bar["opt_type"] == otype)]
    if r.empty:
        return np.nan
    v = float(r["close"].iloc[0])
    return max(v, 0.05) if v > 0 else np.nan


def px_fb(bar: pd.DataFrame, K: float, otype: str, F: float,
          dte: float, is_exit: bool = False) -> float | None:
    """Option price with fallbacks: traded close, then Black-76, then intrinsic."""
    v = px(bar, K, otype)
    if np.isfinite(v):
        return v
    if is_exit or dte <= 0:
        return max(0.0, F - K) if otype == "CE" else max(0.0, K - F)
    r = bar[(bar["strike"] == K) & (bar["opt_type"] == otype)]
    if not r.empty and "iv" in r.columns:
        iv = float(r["iv"].iloc[0])
        if np.isfinite(iv) and iv > 0 and F > 0:
            return black76_price(F, K, max(dte / 365, 1e-6), iv, RISK_FREE, otype == "CE")
    return None


def find_atm(bar: pd.DataFrame, F: float) -> float | None:
    """Strike closest to the futures reference price."""
    if bar.empty:
        return None
    strikes = bar["strike"].unique()
    return float(strikes[np.argmin(np.abs(strikes - F))]) if len(strikes) else None
