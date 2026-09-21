"""
b12_peak_entry_bt.py
====================
Same three strategies as b12_collapse_strategy_bt.py, but entry is placed
AT the b12 peak bar (smoothed 11-bar argmax) instead of waiting for the
slope-flip confirmation.

S1 — F2 Synthetic Long: BUY call + SELL put at F2-ATM strike.
S2 — S1 + F1 Synthetic Short: BUY put + SELL call at F1-ATM.
B  — Futures Calendar: SHORT F2 + LONG F1.

Exit: last 15-min bar at or before 12:00 PM on F1 expiry day.
Tickers: SBICARD, RVNL, KPITTECH, ASTRAL (BDL excluded).
Only HTB cycles (peak_b12 >= 0.05).
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data.storage.arctic_store import ArcticStore
from options.pricing.black76 import black76_price

warnings.filterwarnings("ignore")

# ─── constants ────────────────────────────────────────────────────────────────
TICKERS    = ["SBICARD", "RVNL", "KPITTECH", "ASTRAL"]
LOT_SIZES  = {"SBICARD": 800, "RVNL": 1525, "KPITTECH": 425, "ASTRAL": 425}
INTERVAL   = "15minute"
SMOOTH     = 11
HTB_THRESH = 0.05
SLIPPAGE_OPT = 0.005
SLIPPAGE_FUT = 0.001
RISK_FREE    = 0.065
OUT_DIR      = ROOT / "plots" / "b12_peak_entry_bt"

TICKER_COLORS = {
    "SBICARD":  "#e74c3c",
    "RVNL":     "#3498db",
    "KPITTECH": "#2ecc71",
    "ASTRAL":   "#f39c12",
}


# ─── data helpers ─────────────────────────────────────────────────────────────

def get_opts_bar(o: pd.DataFrame, ts: pd.Timestamp,
                 window: pd.Timedelta = pd.Timedelta("30min")) -> pd.DataFrame:
    if ts in o.index:
        return o.loc[[ts]]
    lo, hi = ts - window, ts + window
    sub = o[(o.index >= lo) & (o.index <= hi)]
    if sub.empty:
        return pd.DataFrame()
    nearest = sub.index[np.argmin(np.abs((sub.index - ts).total_seconds()))]
    return o.loc[[nearest]]


def px(bar: pd.DataFrame, strike: float, otype: str) -> float:
    r = bar[(bar["strike"] == strike) & (bar["opt_type"] == otype)]
    if r.empty:
        return np.nan
    v = float(r["close"].iloc[0])
    return max(v, 0.05) if v > 0 else np.nan


def px_with_fallback(bar: pd.DataFrame, K: float, otype: str,
                     F: float, dte: float, is_exit: bool = False) -> float | None:
    v = px(bar, K, otype)
    if np.isfinite(v):
        return v
    if is_exit or dte <= 0:
        return max(0.0, F - K) if otype == "CE" else max(0.0, K - F)
    r = bar[(bar["strike"] == K) & (bar["opt_type"] == otype)]
    if not r.empty:
        iv = float(r["iv"].iloc[0]) if "iv" in r.columns else np.nan
        if np.isfinite(iv) and iv > 0 and F > 0:
            T = max(dte / 365.0, 1e-6)
            try:
                return black76_price(F, K, T, iv, RISK_FREE, is_call=(otype == "CE"))
            except Exception:
                pass
    return None


def find_atm(bar: pd.DataFrame, F: float) -> float | None:
    if bar.empty:
        return None
    strikes = bar["strike"].unique()
    return float(strikes[np.argmin(np.abs(strikes - F))]) if len(strikes) else None


def slippage(price: float, is_buy: bool, is_futures: bool = False) -> float:
    pct = SLIPPAGE_FUT if is_futures else SLIPPAGE_OPT
    return price * (1 + pct) if is_buy else price * (1 - pct)


# ─── peak detection ───────────────────────────────────────────────────────────

def detect_peak(br_cycle: pd.DataFrame) -> tuple[int, float]:
    b  = br_cycle["b12"].ffill().to_numpy(dtype=float)
    sm = pd.Series(b).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
    peak_idx = int(np.nanargmax(sm))
    return peak_idx, float(np.nanmax(b))


# ─── expiry / exit helpers ────────────────────────────────────────────────────

def resolve_expiry(ticker: str, br_cycle: pd.DataFrame, store: ArcticStore) -> str | None:
    last_date = br_cycle.index.max().date()
    try:
        expiries = store.list_option_expiries(ticker, INTERVAL)
    except Exception:
        return None
    candidates = [e for e in expiries
                  if abs((pd.Timestamp(e).date() - last_date).days) <= 5]
    return min(candidates, key=lambda e: abs((pd.Timestamp(e).date() - last_date).days)) if candidates else None


def find_exit_ts(br_cycle: pd.DataFrame) -> pd.Timestamp | None:
    if "is_expiry_day" in br_cycle.columns:
        exp_rows = br_cycle[br_cycle["is_expiry_day"] == 1]
        exp_date = exp_rows.index[0].date() if not exp_rows.empty else br_cycle.index.max().date()
    else:
        exp_date = br_cycle.index.max().date()
    noon = pd.Timestamp(f"{exp_date} 12:00:00")
    if br_cycle.index.tz is not None:
        noon = noon.tz_localize(br_cycle.index.tz)
    cands = br_cycle[br_cycle.index <= noon]
    return cands.index[-1] if not cands.empty else br_cycle.index.min()


def find_opts_exit_ts(o: pd.DataFrame, br_exit_ts: pd.Timestamp) -> pd.Timestamp | None:
    if o.empty:
        return None
    exp_date = br_exit_ts.date()
    day_opts = o[o.index.date == exp_date]
    if day_opts.empty:
        day_opts = o
    return day_opts.index[int(np.argmin(np.abs((day_opts.index - br_exit_ts).total_seconds())))]


# ─── direction decomposition ──────────────────────────────────────────────────

def compute_direction(br_cycle: pd.DataFrame,
                      entry_ts: pd.Timestamp, exit_ts: pd.Timestamp) -> str:
    ep = br_cycle[br_cycle.index <= entry_ts]
    ex = br_cycle[br_cycle.index <= exit_ts]
    if ep.empty or ex.empty:
        return "unknown"
    ep_row = ep.iloc[-1]; ex_row = ex.iloc[-1]
    f1p = ep_row.get("F1_close"); f2p = ep_row.get("F2_close"); sp_ = ep_row.get("SPOT_close")
    f1e = ex_row.get("F1_close"); f2e = ex_row.get("F2_close"); se  = ex_row.get("SPOT_close")
    for v in [f1p, f2p, sp_, f1e, f2e, se]:
        if v is None or not np.isfinite(float(v)) or float(v) <= 0:
            return "unknown"
    db1 = (float(f1e) - float(se)) - (float(f1p) - float(sp_))
    db2 = (float(f2e) - float(se)) - (float(f2p) - float(sp_))
    denom = abs(-db1) + abs(db2)
    if denom < 1e-6:
        return "flat"
    return "F1→F2" if (-db1) / denom > 0.5 else "F2→F1"


# ─── per-cycle simulation ─────────────────────────────────────────────────────

def run_cycle(ticker: str, cycle_id: int, br_cycle: pd.DataFrame,
              opts: pd.DataFrame | None, entry_ts: pd.Timestamp,
              br_exit_ts: pd.Timestamp, opts_exit_ts: pd.Timestamp | None,
              peak_b12: float, use_slippage: bool) -> dict:

    lot = LOT_SIZES.get(ticker, 1)
    result: dict = {
        "ticker": ticker, "cycle_id": cycle_id, "lot_size": lot,
        "entry_ts": entry_ts, "br_exit_ts": br_exit_ts, "peak_b12": peak_b12,
        "skip_reason": None,
        "S1_pnl": np.nan, "S2_pnl": np.nan, "B_pnl": np.nan,
        "S1_vs_B": np.nan, "direction": "unknown",
        "K_f2": np.nan, "K_f1": np.nan,
        "F2_entry": np.nan, "F1_entry": np.nan,
        "F2_exit": np.nan, "F1_exit": np.nan,
        "collapse_pct": np.nan, "entry_dte": np.nan,
        "peak_raw_spread": np.nan,
    }

    ep = br_cycle[br_cycle.index <= entry_ts]
    if ep.empty:
        result["skip_reason"] = "entry_ts before cycle start"
        return result
    ep_row = ep.iloc[-1]
    F1_entry = float(ep_row.get("F1_close", np.nan))
    F2_entry = float(ep_row.get("F2_close", np.nan))
    entry_dte = float(ep_row.get("F1_dte", np.nan))
    result.update({"F1_entry": F1_entry, "F2_entry": F2_entry,
                   "entry_dte": entry_dte,
                   "peak_raw_spread": F1_entry - F2_entry if np.isfinite(F1_entry) and np.isfinite(F2_entry) else np.nan})

    ex_row = br_cycle[br_cycle.index <= br_exit_ts].iloc[-1]
    F1_exit = float(ex_row.get("F1_close", np.nan))
    F2_exit = float(ex_row.get("F2_close", np.nan))
    end_b12  = float(ex_row.get("b12", np.nan))
    result.update({"F1_exit": F1_exit, "F2_exit": F2_exit})

    if np.isfinite(peak_b12) and peak_b12 > 0 and np.isfinite(end_b12):
        result["collapse_pct"] = (peak_b12 - end_b12) / peak_b12 * 100

    result["direction"] = compute_direction(br_cycle, entry_ts, br_exit_ts)

    # — B (futures calendar) —
    if all(np.isfinite(v) for v in [F1_entry, F2_entry, F1_exit, F2_exit]):
        b_pnl = (F1_exit - F1_entry) + (F2_entry - F2_exit)
        if use_slippage:
            b_pnl -= slippage(F1_entry, True,  True) - F1_entry
            b_pnl -= F2_entry - slippage(F2_entry, False, True)
            b_pnl -= F1_exit  - slippage(F1_exit,  False, True)
            b_pnl -= slippage(F2_exit, True, True) - F2_exit
        result["B_pnl"] = round(b_pnl * lot, 2)

    # — options strategies —
    if opts is None or opts.empty:
        result["skip_reason"] = "no options data"
        return result

    entry_opts = get_opts_bar(opts, entry_ts)
    if entry_opts.empty:
        result["skip_reason"] = "no options bar at entry"
        return result

    F_ref_entry = float(entry_opts["futures_ref_price"].iloc[0]) if "futures_ref_price" in entry_opts.columns else F1_entry
    K_f2 = find_atm(entry_opts, F2_entry) if np.isfinite(F2_entry) else find_atm(entry_opts, F_ref_entry)
    K_f1 = find_atm(entry_opts, F1_entry) if np.isfinite(F1_entry) else K_f2
    result.update({"K_f2": K_f2, "K_f1": K_f1})

    if K_f2 is None:
        result["skip_reason"] = "no ATM strike found"
        return result

    dte_e = entry_dte if np.isfinite(entry_dte) else 5.0
    F_f2 = F2_entry if np.isfinite(F2_entry) else F_ref_entry

    call_f2_e = px_with_fallback(entry_opts, K_f2, "CE", F_f2, dte_e)
    put_f2_e  = px_with_fallback(entry_opts, K_f2, "PE", F_f2, dte_e)
    if call_f2_e is None or put_f2_e is None:
        result["skip_reason"] = f"no F2-ATM price at K={K_f2}"
        return result

    call_f1_e = px_with_fallback(entry_opts, K_f1, "CE", F_ref_entry, dte_e) if K_f1 else None
    put_f1_e  = px_with_fallback(entry_opts, K_f1, "PE", F_ref_entry, dte_e) if K_f1 else None
    has_f1    = K_f1 is not None and call_f1_e is not None and put_f1_e is not None

    if use_slippage:
        call_f2_e_paid = slippage(call_f2_e, True)
        put_f2_e_recv  = slippage(put_f2_e,  False)
        if has_f1:
            put_f1_e_paid  = slippage(put_f1_e,  True)
            call_f1_e_recv = slippage(call_f1_e, False)
    else:
        call_f2_e_paid = call_f2_e; put_f2_e_recv = put_f2_e
        if has_f1:
            put_f1_e_paid = put_f1_e; call_f1_e_recv = call_f1_e

    if opts_exit_ts is None:
        result["skip_reason"] = "no options exit bar"
        return result

    exit_opts = get_opts_bar(opts, opts_exit_ts)
    if exit_opts.empty:
        result["skip_reason"] = "empty options exit bar"
        return result

    F_exit_ref = F1_exit if np.isfinite(F1_exit) else (
        float(exit_opts["futures_ref_price"].iloc[0])
        if "futures_ref_price" in exit_opts.columns else np.nan)
    if not np.isfinite(F_exit_ref):
        result["skip_reason"] = "no F at exit"
        return result

    call_f2_x = px_with_fallback(exit_opts, K_f2, "CE", F_exit_ref, 0.0, is_exit=True) or max(0.0, F_exit_ref - K_f2)
    put_f2_x  = px_with_fallback(exit_opts, K_f2, "PE", F_exit_ref, 0.0, is_exit=True) or max(0.0, K_f2 - F_exit_ref)

    if use_slippage:
        call_f2_x_recv = slippage(call_f2_x, False)
        put_f2_x_paid  = slippage(put_f2_x,  True)
    else:
        call_f2_x_recv = call_f2_x; put_f2_x_paid = put_f2_x

    s1_pnl_share = (call_f2_x_recv - call_f2_e_paid) - (put_f2_x_paid - put_f2_e_recv)
    result["S1_pnl"] = round(s1_pnl_share * lot, 2)

    if has_f1:
        call_f1_x = px_with_fallback(exit_opts, K_f1, "CE", F_exit_ref, 0.0, is_exit=True) or max(0.0, F_exit_ref - K_f1)
        put_f1_x  = px_with_fallback(exit_opts, K_f1, "PE", F_exit_ref, 0.0, is_exit=True) or max(0.0, K_f1 - F_exit_ref)
        if use_slippage:
            put_f1_x_recv  = slippage(put_f1_x,  False)
            call_f1_x_paid = slippage(call_f1_x, True)
        else:
            put_f1_x_recv = put_f1_x; call_f1_x_paid = call_f1_x
        s2_extra = (put_f1_x_recv - put_f1_e_paid) - (call_f1_x_paid - call_f1_e_recv)
        result["S2_pnl"] = round((s1_pnl_share + s2_extra) * lot, 2)
    else:
        result["S2_pnl"] = result["S1_pnl"]

    if np.isfinite(result["S1_pnl"]) and np.isfinite(result["B_pnl"]):
        result["S1_vs_B"] = round(float(result["S1_pnl"]) - float(result["B_pnl"]), 2)

    return result


# ─── aggregate statistics ─────────────────────────────────────────────────────

def sharpe(arr: np.ndarray) -> float:
    arr = arr[np.isfinite(arr)]
    return float(arr.mean() / arr.std()) if len(arr) >= 2 and arr.std() > 0 else np.nan


def max_drawdown(arr: np.ndarray) -> float:
    arr = arr[np.isfinite(arr)]
    if not len(arr):
        return np.nan
    cum = np.cumsum(arr)
    return float(np.max(np.maximum.accumulate(cum) - cum))


def win_rate(arr: np.ndarray) -> float:
    arr = arr[np.isfinite(arr)]
    return float((arr > 0).sum() / len(arr) * 100) if len(arr) else np.nan


# ─── loss reason classifier ───────────────────────────────────────────────────

def loss_reason(r: dict) -> str:
    direction   = r.get("direction", "unknown")
    entry_dte   = r.get("entry_dte", np.nan)
    collapse_pct = r.get("collapse_pct", np.nan)
    peak_spread  = r.get("peak_raw_spread", np.nan)

    reasons = []

    # 1. Direction — S1 is long F2 (wants F2 to rise); if F1→F2 (F1 fell), F2 didn't rise
    if direction == "F1→F2":
        reasons.append("F1→F2 collapse: F2 didn't rise, S1 long was wrong direction")

    # 2. Early peak — lots of theta to overcome even though direction was right
    if np.isfinite(entry_dte) and entry_dte > 10:
        reasons.append(f"early peak (DTE={entry_dte:.0f}): {entry_dte:.0f} days of theta drag before expiry")

    # 3. Negative or near-zero peak spread — b12 peak driven by DTE denominator, not real F2 discount
    if np.isfinite(peak_spread) and peak_spread <= 0:
        reasons.append(f"peak spread was negative ({peak_spread:+.2f}₹): b12 peak was DTE-formula artifact, no actual F2 discount")

    # 4. Small absolute spread at peak — move too small to overcome slippage + bid-ask
    if np.isfinite(peak_spread) and 0 < peak_spread < 3:
        reasons.append(f"tiny peak spread ({peak_spread:.2f}₹): cost basis exceeded the move")

    # 5. Small b12 collapse — premium barely changed, theta dominated
    if np.isfinite(collapse_pct) and collapse_pct < 20:
        reasons.append(f"small b12 collapse ({collapse_pct:.0f}%): borrow premium didn't close, theta ate the position")

    return " | ".join(reasons) if reasons else "marginal move / insufficient directional edge"


# ─── console printing ─────────────────────────────────────────────────────────

def print_per_cycle(rows: list[dict]):
    print("\n" + "=" * 130)
    print("  SECTION 1 — PER-CYCLE RESULTS  (entry at b12 PEAK, HTB cycles, SBICARD/RVNL/KPITTECH/ASTRAL)")
    print("=" * 130)
    hdr = (f"  {'ticker':<9}{'cid':>4}  {'entry_ts':<19}  {'dte':>4}  {'peak_b12':>9}  "
           f"{'F1-F2(₹)':>9}  {'direction':>8}  {'S1(₹)':>10}  {'S2(₹)':>10}  "
           f"{'B(₹)':>10}  {'S1vsB':>8}  note")
    print(hdr)
    print("  " + "─" * 126)
    for r in rows:
        ts_str  = str(r["entry_ts"])[:19] if pd.notna(r.get("entry_ts")) else "  n/a"
        s1_str  = f"{r['S1_pnl']:>+10.0f}" if np.isfinite(r["S1_pnl"]) else "       NaN"
        s2_str  = f"{r['S2_pnl']:>+10.0f}" if np.isfinite(r["S2_pnl"]) else "       NaN"
        b_str   = f"{r['B_pnl']:>+10.0f}"  if np.isfinite(r["B_pnl"])  else "       NaN"
        vb_str  = f"{r['S1_vs_B']:>+8.0f}" if np.isfinite(r["S1_vs_B"]) else "     n/a"
        dte_str = f"{r['entry_dte']:>4.0f}" if np.isfinite(r.get("entry_dte", np.nan)) else " n/a"
        spr_str = f"{r['peak_raw_spread']:>+9.2f}" if np.isfinite(r.get("peak_raw_spread", np.nan)) else "      n/a"
        note    = f"*{r['skip_reason'][:35]}" if r.get("skip_reason") else ""
        print(f"  {r['ticker']:<9}{r['cycle_id']:>4}  {ts_str}  {dte_str}  "
              f"{r['peak_b12']:>9.4f}  {spr_str}  {r['direction']:>8}  "
              f"{s1_str}  {s2_str}  {b_str}  {vb_str}  {note}")
    print("  " + "─" * 126)
    print("  * = skip; NaN = prices unavailable")


def print_per_ticker(rows: list[dict]):
    print("\n" + "=" * 100)
    print("  SECTION 2 — PER-TICKER SUMMARY")
    print("=" * 100)
    hdr = (f"  {'ticker':<9}{'n':>4}  {'S1_wr':>7}  {'S1_mean(₹)':>12}  "
           f"{'S2_mean(₹)':>12}  {'B_mean(₹)':>11}  {'S1_best':>9}  {'S1_worst':>9}")
    print(hdr)
    print("  " + "─" * 96)
    for ticker in TICKERS:
        sub = [r for r in rows if r["ticker"] == ticker]
        if not sub:
            continue
        s1 = np.array([r["S1_pnl"] for r in sub], dtype=float)
        s2 = np.array([r["S2_pnl"] for r in sub], dtype=float)
        b  = np.array([r["B_pnl"]  for r in sub], dtype=float)
        print(f"  {ticker:<9}{len(sub):>4}  {win_rate(s1):>6.0f}%  "
              f"{np.nanmean(s1):>+12.0f}  {np.nanmean(s2):>+12.0f}  {np.nanmean(b):>+11.0f}  "
              f"{np.nanmax(s1):>+9.0f}  {np.nanmin(s1):>+9.0f}")
    print("  " + "─" * 96)


def print_pooled(rows: list[dict]):
    print("\n" + "=" * 90)
    print("  SECTION 3 — POOLED SUMMARY")
    print("=" * 90)
    s1 = np.array([r["S1_pnl"] for r in rows], dtype=float)
    s2 = np.array([r["S2_pnl"] for r in rows], dtype=float)
    b  = np.array([r["B_pnl"]  for r in rows], dtype=float)
    print(f"  {'Strategy':<12}{'n':>5}  {'win%':>7}  {'mean(₹)':>10}  "
          f"{'std(₹)':>9}  {'Sharpe':>8}  {'cumPnL(₹)':>12}  {'maxDD(₹)':>10}")
    print("  " + "─" * 84)
    for name, arr in [("S1 (synth)", s1), ("S2 (cal-opt)", s2), ("B (fut-cal)", b)]:
        valid = arr[np.isfinite(arr)]
        n = len(valid)
        if n == 0:
            print(f"  {name:<12}{'0':>5}  {'n/a':>7}")
            continue
        print(f"  {name:<12}{n:>5}  {win_rate(arr):>6.1f}%  "
              f"{np.nanmean(arr):>+10.0f}  {np.nanstd(arr):>+9.0f}  "
              f"{sharpe(arr):>+8.2f}  {np.nansum(arr):>+12.0f}  {-max_drawdown(arr):>+10.0f}")
    print("  " + "─" * 84)

    f2f1 = [r for r in rows if r["direction"] == "F2→F1"]
    f1f2 = [r for r in rows if r["direction"] == "F1→F2"]
    print(f"\n  Direction × S1 outcome:")
    for label, grp in [("F2→F1 (dominant)", f2f1), ("F1→F2", f1f2)]:
        if not grp:
            continue
        arr = np.array([r["S1_pnl"] for r in grp], dtype=float)
        print(f"    {label:<20}  n={len(grp):>3}  S1 win%={win_rate(arr):>5.1f}%  mean={np.nanmean(arr):>+9.0f}₹")


def print_loss_analysis(rows: list[dict]):
    losers = [r for r in rows if np.isfinite(r["S1_pnl"]) and r["S1_pnl"] < 0]
    if not losers:
        print("\n  No S1 losses.")
        return

    print("\n" + "=" * 140)
    print("  SECTION 4 — LOSS ANALYSIS  (all cycles where S1 < 0)")
    print("=" * 140)
    print(f"  {'ticker':<9}{'cid':>4}  {'dte':>4}  {'F1-F2(₹)':>9}  {'dir':>8}  "
          f"{'S1(₹)':>9}  {'B(₹)':>9}  {'col%':>6}  reason")
    print("  " + "─" * 136)
    for r in sorted(losers, key=lambda x: x["S1_pnl"]):
        col_str = f"{r['collapse_pct']:.0f}%" if np.isfinite(r.get("collapse_pct", np.nan)) else "n/a"
        dte_str = f"{r['entry_dte']:.0f}" if np.isfinite(r.get("entry_dte", np.nan)) else "n/a"
        spr_str = f"{r['peak_raw_spread']:+.2f}" if np.isfinite(r.get("peak_raw_spread", np.nan)) else "n/a"
        b_str   = f"{r['B_pnl']:>+9.0f}" if np.isfinite(r["B_pnl"]) else "      NaN"
        reason  = loss_reason(r)
        print(f"  {r['ticker']:<9}{r['cycle_id']:>4}  {dte_str:>4}  {spr_str:>9}  "
              f"{r['direction']:>8}  {r['S1_pnl']:>+9.0f}  {b_str}  {col_str:>6}  {reason}")
    print("  " + "─" * 136)

    # Loss summary by type
    by_dir   = {"F2→F1": [], "F1→F2": [], "other": []}
    by_dte   = {"high (>10)": [], "normal (≤10)": []}
    by_spr   = {"negative spread": [], "tiny (<3₹)": [], "ok (≥3₹)": []}
    for r in losers:
        d = r.get("direction", "unknown")
        by_dir[d if d in by_dir else "other"].append(r["S1_pnl"])
        dte_e = r.get("entry_dte", np.nan)
        by_dte["high (>10)" if np.isfinite(dte_e) and dte_e > 10 else "normal (≤10)"].append(r["S1_pnl"])
        ps = r.get("peak_raw_spread", np.nan)
        if np.isfinite(ps):
            bucket = "negative spread" if ps <= 0 else ("tiny (<3₹)" if ps < 3 else "ok (≥3₹)")
        else:
            bucket = "ok (≥3₹)"
        by_spr[bucket].append(r["S1_pnl"])

    print(f"\n  Loss breakdown:")
    print(f"  Total losing cycles: {len(losers)}  |  Total loss: ₹{sum(r['S1_pnl'] for r in losers):+,.0f}")
    print(f"\n  By direction at collapse:")
    for k, v in by_dir.items():
        if v:
            print(f"    {k:<22}  n={len(v):>3}  avg loss={np.mean(v):>+9.0f}₹  total={sum(v):>+10.0f}₹")
    print(f"\n  By DTE at peak entry:")
    for k, v in by_dte.items():
        if v:
            print(f"    {k:<22}  n={len(v):>3}  avg loss={np.mean(v):>+9.0f}₹  total={sum(v):>+10.0f}₹")
    print(f"\n  By peak spread size:")
    for k, v in by_spr.items():
        if v:
            print(f"    {k:<22}  n={len(v):>3}  avg loss={np.mean(v):>+9.0f}₹  total={sum(v):>+10.0f}₹")


# ─── plots ────────────────────────────────────────────────────────────────────

def plot_pnl_by_cycle(rows: list[dict], out: Path):
    valid = [r for r in rows if np.isfinite(r["S1_pnl"]) or np.isfinite(r["B_pnl"])]
    if not valid:
        return
    valid.sort(key=lambda r: r["entry_ts"] if pd.notna(r.get("entry_ts")) else pd.Timestamp.min)
    labels = [f"{r['ticker']}\nc{r['cycle_id']}" for r in valid]
    s1 = [r["S1_pnl"] if np.isfinite(r["S1_pnl"]) else 0 for r in valid]
    b  = [r["B_pnl"]  if np.isfinite(r["B_pnl"])  else 0 for r in valid]
    x, w = np.arange(len(valid)), 0.35

    fig, ax = plt.subplots(figsize=(max(14, len(valid) * 0.7), 6))
    ax.bar(x - w/2, s1, w, color=[TICKER_COLORS.get(r["ticker"], "grey") for r in valid],
           alpha=0.85, label="S1 (F2 synthetic)")
    ax.bar(x + w/2, b,  w, color="lightgrey", alpha=0.85,
           label="B (futures cal)", edgecolor="grey", lw=0.5)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right")
    ax.set_ylabel("PnL (₹ per lot)")
    ax.set_title("Per-cycle PnL — entry at b12 PEAK spread\nS1 (F2 synthetic, coloured by ticker) vs B (futures calendar, grey)")
    ax.legend(); ax.grid(alpha=0.2, axis="y")
    for bars in [ax.containers[0], ax.containers[1]]:
        for rect in bars:
            h = rect.get_height()
            if abs(h) > 2000:
                ax.text(rect.get_x() + rect.get_width()/2, h + (200 if h >= 0 else -400),
                        f"{h:+.0f}", ha="center", fontsize=6.5)
    fig.tight_layout()
    fig.savefig(out / "g1_pnl_by_cycle.png", dpi=120); plt.close(fig)
    print(f"  Saved: {out / 'g1_pnl_by_cycle.png'}")


def plot_cumulative(rows: list[dict], out: Path):
    valid = sorted([r for r in rows if pd.notna(r.get("entry_ts"))],
                   key=lambda r: r["entry_ts"])
    s1 = np.array([r["S1_pnl"] for r in valid], dtype=float)
    s2 = np.array([r["S2_pnl"] for r in valid], dtype=float)
    b  = np.array([r["B_pnl"]  for r in valid], dtype=float)

    fig, ax = plt.subplots(figsize=(13, 5))
    for name, arr, col, ls in [
        ("S1 (F2 synth)", s1, "#e74c3c", "-"),
        ("S2 (cal-opt)",  s2, "#3498db", "--"),
        ("B  (fut cal)",  b,  "#95a5a6", ":"),
    ]:
        cum = np.nancumsum(np.where(np.isfinite(arr), arr, 0))
        ax.plot(range(len(cum)), cum, color=col, ls=ls, lw=2, label=name)
        ax.plot(range(len(cum)), cum, ".", color=col, ms=5)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("Cycle # (sorted by entry date)")
    ax.set_ylabel("Cumulative PnL (₹)")
    ax.set_title("Equity curves — PEAK entry (S1, S2, B) — SBICARD/RVNL/KPITTECH/ASTRAL")
    ax.legend(); ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(out / "g2_cumulative_pnl.png", dpi=120); plt.close(fig)
    print(f"  Saved: {out / 'g2_cumulative_pnl.png'}")


def plot_direction_vs_pnl(rows: list[dict], out: Path):
    f2f1_s1 = [r["S1_pnl"] for r in rows if r["direction"] == "F2→F1" and np.isfinite(r["S1_pnl"])]
    f1f2_s1 = [r["S1_pnl"] for r in rows if r["direction"] == "F1→F2" and np.isfinite(r["S1_pnl"])]
    f2f1_b  = [r["B_pnl"]  for r in rows if r["direction"] == "F2→F1" and np.isfinite(r["B_pnl"])]
    f1f2_b  = [r["B_pnl"]  for r in rows if r["direction"] == "F1→F2" and np.isfinite(r["B_pnl"])]

    fig, axes = plt.subplots(1, 2, figsize=(13, 6), sharey=False)
    for ax, name, d1, d2 in [
        (axes[0], "S1 (F2 Synthetic Long)", f2f1_s1, f1f2_s1),
        (axes[1], "B  (Futures Calendar)",  f2f1_b,  f1f2_b),
    ]:
        if not d1 and not d2:
            continue
        bp = ax.boxplot([d1 or [0], d2 or [0]], patch_artist=True, widths=0.4,
                        labels=[f"F2→F1\n(n={len(d1)})", f"F1→F2\n(n={len(d2)})"])
        bp["boxes"][0].set_facecolor("#3498db"); bp["boxes"][0].set_alpha(0.7)
        if len(bp["boxes"]) > 1:
            bp["boxes"][1].set_facecolor("#e74c3c"); bp["boxes"][1].set_alpha(0.7)
        ax.axhline(0, color="k", lw=0.8, ls="--")
        ax.set_ylabel("PnL per lot (₹)")
        ax.set_title(f"{name}\nPnL by collapse direction")
        ax.grid(alpha=0.2, axis="y")
        for i, d in enumerate([d1, d2], 1):
            if d:
                ax.plot(i, np.mean(d), "D", color="k", ms=8, zorder=5, label=f"mean={np.mean(d):+.0f}")
        ax.legend(fontsize=8)
    fig.suptitle("PnL split by direction — PEAK entry", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out / "g3_direction_vs_pnl.png", dpi=120); plt.close(fig)
    print(f"  Saved: {out / 'g3_direction_vs_pnl.png'}")


def plot_dte_vs_pnl(rows: list[dict], out: Path):
    """DTE at peak entry vs S1 PnL — key diagnostic for early-peak bias."""
    sub = [r for r in rows if np.isfinite(r["S1_pnl"]) and np.isfinite(r.get("entry_dte", np.nan))]
    if not sub:
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    for ticker in TICKERS:
        ts = [r for r in sub if r["ticker"] == ticker]
        if not ts:
            continue
        ax.scatter([r["entry_dte"] for r in ts], [r["S1_pnl"] for r in ts],
                   color=TICKER_COLORS.get(ticker, "grey"), label=ticker, alpha=0.8, s=60, zorder=3)
    ax.axhline(0, color="k", lw=0.8, ls="--")
    ax.axvline(10, color="orange", lw=1, ls="--", label="DTE=10 threshold")
    ax.set_xlabel("DTE at peak entry")
    ax.set_ylabel("S1 PnL per lot (₹)")
    ax.set_title("S1 PnL vs DTE at peak — does entering early cost more?\n"
                 "High DTE = more theta drag before expiry")
    ax.legend(fontsize=9); ax.grid(alpha=0.2)
    if len(sub) >= 4:
        from numpy.polynomial import polynomial as P
        xs = [r["entry_dte"] for r in sub]; ys = [r["S1_pnl"] for r in sub]
        c = P.polyfit(xs, ys, 1)
        xl = np.linspace(min(xs), max(xs), 50)
        ax.plot(xl, P.polyval(xl, c), "k--", lw=1.2, alpha=0.6, label="_trend")
    fig.tight_layout()
    fig.savefig(out / "g4_dte_vs_pnl.png", dpi=120); plt.close(fig)
    print(f"  Saved: {out / 'g4_dte_vs_pnl.png'}")


# ─── main ─────────────────────────────────────────────────────────────────────

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers",     nargs="+", default=TICKERS)
    ap.add_argument("--no-slippage", action="store_true")
    ap.add_argument("--htb-thresh",  type=float, default=HTB_THRESH)
    ap.add_argument("--out",         default=str(OUT_DIR))
    return ap.parse_args()


def main():
    args = parse_args()
    out  = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    use_slippage = not args.no_slippage

    store = ArcticStore()
    all_rows: list[dict] = []

    print(f"\nb12 PEAK-ENTRY backtest")
    print(f"  Tickers: {args.tickers}  |  HTB >= {args.htb_thresh}")
    print(f"  Entry: AT the b12 peak bar (no slope-flip confirmation)")
    print(f"  Exit:  12 PM on expiry day")
    print(f"  Slippage: {'OFF' if args.no_slippage else f'OPT={SLIPPAGE_OPT*100:.1f}% FUT={SLIPPAGE_FUT*100:.1f}%'}")

    for ticker in args.tickers:
        print(f"\n  [{ticker}] loading …")
        try:
            br = store.read_borrow_rates(ticker, INTERVAL)
        except Exception as e:
            print(f"    ERROR: {e}"); continue
        if br is None or br.empty:
            print("    empty — skip"); continue

        br = br.copy()
        n_cycles = br["cycle_id"].nunique()
        print(f"    {len(br):,} bars, {n_cycles} cycles")

        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index()
            peak_idx, peak_b12 = detect_peak(cyc)
            if peak_b12 < args.htb_thresh:
                continue

            # ── ENTRY AT PEAK ──
            entry_ts   = cyc.index[peak_idx]
            br_exit_ts = find_exit_ts(cyc)
            if br_exit_ts is None or br_exit_ts <= entry_ts:
                print(f"    cid={cid:>3}  SKIP: exit <= entry"); continue

            expiry_str = resolve_expiry(ticker, cyc, store)
            if expiry_str is None:
                all_rows.append({"ticker": ticker, "cycle_id": int(cid),
                                  "lot_size": LOT_SIZES.get(ticker, 1),
                                  "entry_ts": entry_ts, "br_exit_ts": br_exit_ts,
                                  "peak_b12": peak_b12, "skip_reason": "no options expiry",
                                  "S1_pnl": np.nan, "S2_pnl": np.nan, "B_pnl": np.nan,
                                  "S1_vs_B": np.nan, "direction": "unknown",
                                  "K_f2": np.nan, "K_f1": np.nan,
                                  "F2_entry": np.nan, "F1_entry": np.nan,
                                  "F2_exit": np.nan, "F1_exit": np.nan,
                                  "collapse_pct": np.nan, "entry_dte": np.nan,
                                  "peak_raw_spread": np.nan})
                continue

            try:
                opts = store.read_options(ticker, INTERVAL, expiry_str)
            except Exception:
                opts = pd.DataFrame()

            opts_exit_ts = find_opts_exit_ts(opts, br_exit_ts) if (opts is not None and not opts.empty) else None

            result = run_cycle(ticker=ticker, cycle_id=int(cid),
                               br_cycle=cyc, opts=opts,
                               entry_ts=entry_ts, br_exit_ts=br_exit_ts,
                               opts_exit_ts=opts_exit_ts,
                               peak_b12=peak_b12, use_slippage=use_slippage)
            all_rows.append(result)
            s1s = f"S1={result['S1_pnl']:+.0f}₹" if np.isfinite(result["S1_pnl"]) else "S1=n/a"
            bs  = f"B={result['B_pnl']:+.0f}₹"   if np.isfinite(result["B_pnl"])  else "B=n/a"
            ste = result.get("skip_reason") or "OK"
            dte_s = f"{result.get('entry_dte', np.nan):.0f}" if np.isfinite(result.get("entry_dte", np.nan)) else "?"
            print(f"    cid={cid:>3}  peak_b12={peak_b12:.4f}  entry_dte={dte_s:>3}  {s1s}  {bs}  [{ste[:30]}]")

    if not all_rows:
        print("\nNo qualifying cycles found.")
        return

    all_rows.sort(key=lambda r: r["entry_ts"] if pd.notna(r.get("entry_ts")) else pd.Timestamp.max)

    print_per_cycle(all_rows)
    print_per_ticker(all_rows)
    print_pooled(all_rows)
    print_loss_analysis(all_rows)

    print(f"\nSaving figures …")
    plot_pnl_by_cycle(all_rows, out)
    plot_cumulative(all_rows, out)
    plot_direction_vs_pnl(all_rows, out)
    plot_dte_vs_pnl(all_rows, out)

    n_ok = sum(1 for r in all_rows if not r.get("skip_reason"))
    print(f"\nDone. {n_ok}/{len(all_rows)} cycles clean.  Figures: {out}")


if __name__ == "__main__":
    main()
