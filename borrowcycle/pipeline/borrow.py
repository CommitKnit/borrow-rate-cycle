"""Implied borrow rates from the F1/F2/F3 panel and Kite spot.

Cost-of-carry: F = S·exp((r − b)·τ), so the implied borrow rate on a leg is
b = r − ln(F/S)/τ, and between two futures b12 = r − ln(F2/F1)/(τ2 − τ1). Rates are
annualised (τ in years) and floored at 0. Formulas match src/update_intraday.py, the
version every stored series was built with.

τ uses a fractional DTE: F{n}_dte_star = (F{n}_dte + 1) − i/bars_per_day, where i is
the bar's 1-based rank within its trading day, so τ ticks down continuously intraday
(bar 1 ≈ dte + 24/25, last bar = dte at 15 minutes).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as cfg

#: Columns stored in borrow_rates (computed only; inputs are joined on read).
INTRADAY_COLUMNS = ["F1_dte_star", "F2_dte_star", "F3_dte_star", "OI_ratio", "U_t",
                    "spread_spot_f1", "spread_spot_f2", "spread_f1_f2",
                    "b1", "b2", "b3", "b12", "b23", "b13", "in_backwardation"]
DAILY_COLUMNS = [c for c in INTRADAY_COLUMNS if c not in ("OI_ratio", "U_t")]
HTB_B12_THRESHOLD = 0.15


def computed_columns(interval: str) -> list[str]:
    return DAILY_COLUMNS if interval in ("1day", "day") else INTRADAY_COLUMNS


def dte_star(panel: pd.DataFrame, bars_per_day: int) -> pd.DataFrame:
    rank = panel.groupby(panel.index.normalize()).cumcount() + 1
    out = pd.DataFrame(index=panel.index)
    for fx in ("F1", "F2", "F3"):
        out[f"{fx}_dte_star"] = (panel[f"{fx}_dte"] + 1) - rank / bars_per_day
    return out


def _log_ratio(num: pd.Series, den: pd.Series) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where((num > 0) & (den > 0), np.log(num / den), np.nan)


def compute(joined: pd.DataFrame, interval: str, r: float = cfg.RISK_FREE_RATE) -> pd.DataFrame:
    """``joined`` = panel + SPOT_close (Store.read_panel). Returns computed columns only."""
    out = dte_star(joined, cfg.BARS_PER_DAY[interval])
    S = joined["SPOT_close"]
    F = {n: joined[f"F{n}_close"] for n in (1, 2, 3)}
    t = {n: out[f"F{n}_dte_star"] / 365 for n in (1, 2, 3)}

    def rate(num, den, tau):
        return pd.Series(np.where(tau > 0, r - _log_ratio(num, den) / tau, np.nan), index=joined.index).clip(lower=0)

    for n in (1, 2, 3):
        out[f"b{n}"] = rate(F[n], S, t[n])
    out["b12"] = rate(F[2], F[1], t[2] - t[1])
    out["b23"] = rate(F[3], F[2], t[3] - t[2])
    out["b13"] = rate(F[3], F[1], t[3] - t[1])
    out["in_backwardation"] = F[1] < S
    out["spread_spot_f1"] = S - F[1]
    out["spread_spot_f2"] = S - F[2]
    out["spread_f1_f2"] = F[1] - F[2]
    if interval not in ("1day", "day"):
        out["OI_ratio"] = joined["F1_oi"] / joined["F2_oi"].replace(0, np.nan)
        # +0.04 day on expiry-day bars keeps U_t finite at the final bar (dte_star = 0)
        out["U_t"] = out["OI_ratio"] / (out["F1_dte_star"] + joined["is_expiry_day"].astype(float) * 0.04)
    return out[computed_columns(interval)]


def cycle_summary(df: pd.DataFrame, ticker: str, interval: str) -> pd.DataFrame:
    """Per-cycle classification (src populate_borrow_rates_htb._flag_contango_cycles,
    without the dividend columns). ``df`` needs cycle_id, F1_expiry, b1, b12,
    in_backwardation.

    15minute: HTB-dominant if b12 exceeds 15% p.a. at any bar. Other intervals:
    HTB-dominant if at least half the bars are in backwardation.
    """
    rows = []
    for cid, g in df.groupby("cycle_id"):
        total = len(g)
        bwd = int(g["in_backwardation"].sum())
        pct = bwd / total if total else 0.0
        max_b12, mean_b1, max_b1 = (float(g["b12"].max()), float(g["b1"].mean()), float(g["b1"].max()))
        contango = (np.isnan(max_b12) or max_b12 <= HTB_B12_THRESHOLD) if interval == "15minute" else pct < 0.5
        rd = lambda v, k: round(v, k) if not np.isnan(v) else np.nan
        rows.append({"symbol": ticker, "interval": interval, "cycle_id": int(cid), "total_bars": total,
                     "raw_bwd_bars": bwd, "adj_bwd_bars": bwd, "adj_bwd_pct": round(pct * 100, 2),
                     "contango_dominant": contango, "mean_b1": rd(mean_b1, 6), "max_b1": rd(max_b1, 6),
                     "max_b12": rd(max_b12, 6), "expected_div_mean": 0.0,
                     "F1_expiry": str(g["F1_expiry"].iloc[0])})
    return pd.DataFrame(rows).set_index("cycle_id")
