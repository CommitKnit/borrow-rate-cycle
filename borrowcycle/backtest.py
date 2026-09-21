"""
The calendar-spread backtest, ported from the original research.

Three constructions are evaluated at the same entry and exit:

  S1  Futures calendar    SHORT F1 + LONG F2      (no options, no vega)
  S2  Synthetic calendar  SSF1 + SLF2 via options
  S3  Asymmetric          BUY call F2 + SELL put F1

Two deliberate changes from the original are marked CHANGED below. Both are
documented in docs/03_strategy_results.md and docs/05_limitations.md.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import data as bd
from .cycle import SMOOTH, smooth, spread
from .options import find_atm, get_opts_bar, px_fb

SLIP_OPT = 0.005   # 0.5% per options leg
SLIP_FUT = 0.001   # 0.1% per futures leg


def detect_peak(cyc: pd.DataFrame, center: bool = True) -> tuple[int, float]:
    """Index and level of the smoothed spread peak."""
    sm = smooth(spread(cyc), SMOOTH, center=center)
    return int(np.nanargmax(sm)), float(np.nanmax(sm))


def find_inflection_entry(cyc: pd.DataFrame, peak_idx: int,
                          center: bool = True) -> pd.Timestamp | None:
    """First bar at or after the peak where slope < 0 and acceleration < 0.

    With ``center=True`` this reproduces the original signal, which peeks
    (SMOOTH-1)//2 = 5 bars (~75 minutes) ahead. ``center=False`` is the
    implementable version.
    """
    sm = pd.Series(smooth(spread(cyc), SMOOTH, center=center))
    slopes = sm.diff().to_numpy(float)
    accels = sm.diff().diff().to_numpy(float)
    for i in range(peak_idx, len(cyc)):
        s, a = slopes[i], accels[i]
        if np.isfinite(s) and np.isfinite(a) and s < 0 and a < 0:
            return cyc.index[i]
    return None


def find_exit_ts(cyc: pd.DataFrame) -> pd.Timestamp | None:
    """Last bar at or before 12:00 on F1 expiry day."""
    if "is_expiry_day" in cyc.columns:
        exp_rows = cyc[cyc["is_expiry_day"] == 1]
        exp_d = exp_rows.index[0].date() if not exp_rows.empty else cyc.index.max().date()
    else:
        exp_d = cyc.index.max().date()
    noon = pd.Timestamp(f"{exp_d} 12:00:00")
    if cyc.index.tz is not None:
        noon = noon.tz_localize(cyc.index.tz)
    cands = cyc[cyc.index <= noon]
    return cands.index[-1] if not cands.empty else None


def find_f1_expiry(cyc: pd.DataFrame, expiries: list[str]) -> str | None:
    last_d = cyc.index.max().date()
    cands = [e for e in expiries if abs((date.fromisoformat(e) - last_d).days) <= 5]
    if not cands:
        return None
    return min(cands, key=lambda e: abs((date.fromisoformat(e) - last_d).days))


def find_f2_expiry(f1_exp: str, expiries: list[str]) -> str | None:
    f1_dt = date.fromisoformat(f1_exp)
    later = [e for e in sorted(expiries) if date.fromisoformat(e) > f1_dt]
    return later[0] if later else None


def slip(p: float, buy: bool, fut: bool = False) -> float:
    pct = SLIP_FUT if fut else SLIP_OPT
    return p * (1 + pct) if buy else p * (1 - pct)


def run_cycle(ticker, cid, cyc, f1_opts, f2_opts, entry_ts, exit_ts,
              peak_b12, peak_spread, use_slip=True, legacy_slip=False) -> dict:
    """Simulate S1/S2/S3 for one cycle.

    S1 is reported per share and in basis points of F1 for every ticker, and
    additionally in rupees per lot where the lot size is known. S1 is always
    computed when the futures prices are present, regardless of whether
    options are available.

    ``legacy_slip=True`` reproduces the original slippage arithmetic, which
    had an inverted sign on every futures leg. It exists only so the published
    numbers can be reproduced and the size of the error measured.
    """
    lot = bd.LOT_SIZES.get(ticker)
    res = {
        "ticker": ticker, "cid": int(cid), "lot": lot if lot else np.nan,
        "entry_ts": entry_ts, "exit_ts": exit_ts,
        "peak_b12": peak_b12, "peak_spread": peak_spread,
        "skip_reason": None, "opt_status": "ok",
        "S1": np.nan, "S2": np.nan, "S3": np.nan,
        "S1_per_share": np.nan, "S1_bps": np.nan,
        "SLF2": np.nan, "SSF1": np.nan,
        "entry_dte": np.nan, "spread_entry": np.nan, "spread_exit": np.nan,
        "direction": "unknown", "K_f1": np.nan, "K_f2": np.nan,
    }

    ep = cyc[cyc.index <= entry_ts]
    ex = cyc[cyc.index <= exit_ts]
    if ep.empty or ex.empty:
        res["skip_reason"] = "missing bars"
        return res

    er, xr = ep.iloc[-1], ex.iloc[-1]
    F1e, F2e = float(er.get("F1_close", np.nan)), float(er.get("F2_close", np.nan))
    F1x, F2x = float(xr.get("F1_close", np.nan)), float(xr.get("F2_close", np.nan))
    res["entry_dte"] = float(er.get("F1_dte", np.nan))
    res.update({"F1_entry": F1e, "F2_entry": F2e, "F1_exit": F1x, "F2_exit": F2x})

    if not all(np.isfinite(v) for v in (F1e, F2e, F1x, F2x)):
        res["skip_reason"] = "missing futures price"
        return res

    res["spread_entry"] = F1e - F2e
    res["spread_exit"] = F1x - F2x

    # Which leg did the convergence come from?
    sp_e, sp_x = float(er.get("SPOT_close", np.nan)), float(xr.get("SPOT_close", np.nan))
    if all(np.isfinite(v) and v > 0 for v in (sp_e, sp_x)):
        db1 = (F1x - sp_x) - (F1e - sp_e)
        db2 = (F2x - sp_x) - (F2e - sp_e)
        denom = abs(-db1) + abs(db2)
        if denom > 1e-6:
            res["direction"] = "F1->F2" if (-db1) / denom > 0.5 else "F2->F1"

    # ---- S1: futures calendar (needs no options) -----------------------------
    # CHANGED (2): slippage sign. The original adjusted pnl by deltas whose
    # sign was inverted on all four legs, so it CREDITED transaction costs
    # instead of charging them -- worth about +80 bps per cycle, comparable to
    # the entire claimed edge. Here the legs are filled at slipped prices
    # directly, which cannot get the sign wrong. See docs/05_limitations.md.
    if use_slip:
        if legacy_slip:
            pnl = (F1e - F1x) + (F2x - F2e)
            pnl -= slip(F1e, False, True) - F1e   # sell F1      (sign inverted)
            pnl -= F2e - slip(F2e, True, True)    # buy  F2      (sign inverted)
            pnl -= F1x - slip(F1x, True, True)    # buy back F1  (sign inverted)
            pnl -= slip(F2x, False, True) - F2x   # sell F2      (sign inverted)
        else:
            sell_f1_in = slip(F1e, False, True)   # short F1: receive less
            buy_f2_in = slip(F2e, True, True)     # long  F2: pay more
            buy_f1_out = slip(F1x, True, True)    # cover F1: pay more
            sell_f2_out = slip(F2x, False, True)  # sell  F2: receive less
            pnl = (sell_f1_in - buy_f1_out) + (sell_f2_out - buy_f2_in)
    else:
        pnl = (F1e - F1x) + (F2x - F2e)
    res["S1_per_share"] = round(pnl, 4)
    res["S1_bps"] = round(1e4 * pnl / F1e, 2) if F1e else np.nan
    if lot:
        res["S1"] = round(pnl * lot, 2)

    # CHANGED (1): options are optional. The original gated the whole cycle on
    # option availability -- including S1, which uses no options -- silently
    # dropping 11 of 33 qualifying SBICARD/RVNL cycles.
    if f1_opts is None or f2_opts is None or f1_opts.empty or f2_opts.empty:
        res["opt_status"] = "unavailable"
        return res

    f1_e_bar, f1_x_bar = get_opts_bar(f1_opts, entry_ts), get_opts_bar(f1_opts, exit_ts)
    f2_e_bar, f2_x_bar = get_opts_bar(f2_opts, entry_ts), get_opts_bar(f2_opts, exit_ts)

    def _ref(bar, fallback):
        if not bar.empty and "futures_ref_price" in bar.columns:
            return float(bar["futures_ref_price"].iloc[0])
        return fallback

    K_f1 = find_atm(f1_e_bar, _ref(f1_e_bar, F1e)) if not f1_e_bar.empty else None
    K_f2 = find_atm(f2_e_bar, _ref(f2_e_bar, F2e)) if not f2_e_bar.empty else None
    res.update({"K_f1": K_f1 if K_f1 else np.nan, "K_f2": K_f2 if K_f2 else np.nan})

    if lot is None:
        res["opt_status"] = "no lot size"
        return res

    f1_dte = res["entry_dte"] if np.isfinite(res["entry_dte"]) else 3.0
    f2_dte_e = 45.0
    if not f2_e_bar.empty and "dte" in f2_e_bar.columns:
        f2_dte_e = float(f2_e_bar["dte"].iloc[0])
    f2_dte_x = 30.0
    if not f2_x_bar.empty and "dte" in f2_x_bar.columns:
        f2_dte_x = float(f2_x_bar["dte"].iloc[0])
    F2_ref_e, F2_ref_x = _ref(f2_e_bar, F2e), _ref(f2_x_bar, F2x)
    F1_ref_x = _ref(f1_x_bar, F1x)

    # ---- S2 leg: synthetic short F1 (F1-expiry options) ----------------------
    ssf1 = np.nan
    if K_f1 and not f1_e_bar.empty and not f1_x_bar.empty:
        p_e = px_fb(f1_e_bar, K_f1, "PE", F1e, f1_dte)
        c_e = px_fb(f1_e_bar, K_f1, "CE", F1e, f1_dte)
        if p_e is not None and c_e is not None:
            p_x = px_fb(f1_x_bar, K_f1, "PE", F1_ref_x, 0.0, True) or max(0., K_f1 - F1_ref_x)
            c_x = px_fb(f1_x_bar, K_f1, "CE", F1_ref_x, 0.0, True) or max(0., F1_ref_x - K_f1)
            if use_slip:
                p_e, c_e = slip(p_e, True), slip(c_e, False)
                p_x, c_x = slip(p_x, False), slip(c_x, True)
            ssf1 = (p_x - p_e) - (c_x - c_e)
            res["SSF1"] = round(ssf1 * lot, 2)

    # ---- S2 leg: synthetic long F2 (F2-expiry options) -----------------------
    slf2 = np.nan
    if K_f2 and not f2_e_bar.empty and not f2_x_bar.empty:
        ce_e = px_fb(f2_e_bar, K_f2, "CE", F2_ref_e, f2_dte_e)
        pe_e = px_fb(f2_e_bar, K_f2, "PE", F2_ref_e, f2_dte_e)
        ce_x = px_fb(f2_x_bar, K_f2, "CE", F2_ref_x, f2_dte_x)
        pe_x = px_fb(f2_x_bar, K_f2, "PE", F2_ref_x, f2_dte_x)
        if all(v is not None for v in (ce_e, pe_e, ce_x, pe_x)):
            if use_slip:
                ce_e, pe_e = slip(ce_e, True), slip(pe_e, False)
                ce_x, pe_x = slip(ce_x, False), slip(pe_x, True)
            slf2 = (ce_x - ce_e) - (pe_x - pe_e)
            res["SLF2"] = round(slf2 * lot, 2)

    if np.isfinite(ssf1) and np.isfinite(slf2):
        res["S2"] = round((ssf1 + slf2) * lot, 2)

    # ---- S3: buy call F2 + sell put F1 ---------------------------------------
    bars_ok = not any(b.empty for b in (f1_e_bar, f1_x_bar, f2_e_bar, f2_x_bar))
    if K_f1 and K_f2 and bars_ok:
        c2e = px_fb(f2_e_bar, K_f2, "CE", F2_ref_e, f2_dte_e)
        c2x = px_fb(f2_x_bar, K_f2, "CE", F2_ref_x, f2_dte_x)
        p1e = px_fb(f1_e_bar, K_f1, "PE", F1e, f1_dte)
        p1x = px_fb(f1_x_bar, K_f1, "PE", F1_ref_x, 0.0, True) or max(0., K_f1 - F1_ref_x)
        if all(v is not None for v in (c2e, c2x, p1e, p1x)):
            if use_slip:
                c2e, c2x = slip(c2e, True), slip(c2x, False)
                p1e, p1x = slip(p1e, False), slip(p1x, True)
            res["S3"] = round(((c2x - c2e) + (p1e - p1x)) * lot, 2)

    return res
