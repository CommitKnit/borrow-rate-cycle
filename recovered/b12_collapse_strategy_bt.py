"""
b12_collapse_strategy_bt.py
===========================
Backtest three strategies triggered when the b12 borrow-rate spread peaks
and its slope flips from positive → negative (confirmed 3–4 consecutive bars).

Signal: peak_b12 >= HTB_THRESH and feat_b12_slope_1d crosses zero,
        stays negative for CONFIRM bars, within last LAST_DTE of F1 expiry.

S1 — F2 Synthetic Long (options):
  BUY call + SELL put at the F2-ATM strike (K closest to F2_close at entry).
  Uses F1-expiry monthly options.
  Thesis: F2 rises toward F1 (~90% of HTB collapse cycles).

S2 — Calendar in Options (S1 + F1 Synthetic Short):
  S1 PLUS: BUY put + SELL call at the F1-ATM strike.
  Adds value when F1 also falls (F1→F2 direction).

B  — Futures Calendar Benchmark:
  SHORT F2 futures + LONG F1 futures at entry; exit at expiry 12 PM.

Exit: last 15-min bar at or before 12:00 PM on F1 expiry day.
Tickers: SBICARD, RVNL, KPITTECH, ASTRAL, BDL (HTB cycles only).
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import date, timedelta
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
TICKERS    = ["SBICARD", "RVNL", "KPITTECH", "ASTRAL", "BDL"]
LOT_SIZES  = {"SBICARD": 800, "RVNL": 1525, "KPITTECH": 425, "ASTRAL": 425, "BDL": 750}
INTERVAL   = "15minute"
SMOOTH     = 11
CONFIRM    = 3        # confirm_bars (fallback: 4)
HTB_THRESH = 0.05
LAST_DTE   = 10
SLIPPAGE_OPT = 0.005
SLIPPAGE_FUT = 0.001
RISK_FREE    = 0.065
OUT_DIR      = ROOT / "plots" / "b12_collapse_bt"

TICKER_COLORS = {
    "SBICARD":  "#e74c3c",
    "RVNL":     "#3498db",
    "KPITTECH": "#2ecc71",
    "ASTRAL":   "#f39c12",
    "BDL":      "#9b59b6",
}


# ─── data helpers ─────────────────────────────────────────────────────────────

def smooth_b12(b12: pd.Series, w: int = SMOOTH) -> np.ndarray:
    return b12.rolling(w, center=True, min_periods=1).mean().to_numpy(dtype=float)


def get_opts_bar(o: pd.DataFrame, ts: pd.Timestamp, window: pd.Timedelta = pd.Timedelta("30min")) -> pd.DataFrame:
    """Return options bars at ts, or nearest bar within ±window."""
    if ts in o.index:
        return o.loc[[ts]]
    lo, hi = ts - window, ts + window
    sub = o[(o.index >= lo) & (o.index <= hi)]
    if sub.empty:
        return pd.DataFrame()
    nearest = sub.index[np.argmin(np.abs((sub.index - ts).total_seconds()))]
    return o.loc[[nearest]]


def px(bar: pd.DataFrame, strike: float, otype: str) -> float:
    """Close price for (strike, opt_type), floored at 0.05. Returns nan if missing."""
    r = bar[(bar["strike"] == strike) & (bar["opt_type"] == otype)]
    if r.empty:
        return np.nan
    v = float(r["close"].iloc[0])
    return max(v, 0.05) if v > 0 else np.nan


def px_with_fallback(
    bar: pd.DataFrame,
    K: float,
    otype: str,
    F: float,
    dte: float,
    is_exit: bool = False,
) -> float | None:
    """
    1. Try close price from options bar.
    2. Fallback: Black-76 using IV from the bar.
    3. At exit (dte <= 0): intrinsic value.
    """
    v = px(bar, K, otype)
    if np.isfinite(v):
        return v

    if is_exit or dte <= 0:
        return max(0.0, F - K) if otype == "CE" else max(0.0, K - F)

    # Black-76 fallback
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
    """Strike closest to F in the options bar."""
    if bar.empty:
        return None
    strikes = bar["strike"].unique()
    if len(strikes) == 0:
        return None
    return float(strikes[np.argmin(np.abs(strikes - F))])


def slippage(price: float, is_buy: bool, is_futures: bool = False) -> float:
    pct = SLIPPAGE_FUT if is_futures else SLIPPAGE_OPT
    return price * (1 + pct) if is_buy else price * (1 - pct)


# ─── peak and entry detection ─────────────────────────────────────────────────

def detect_peak(br_cycle: pd.DataFrame) -> tuple[int, float]:
    """11-bar centred smooth + argmax (matches hmm_s4_timing.py pattern)."""
    b = br_cycle["b12"].fillna(method="ffill").to_numpy(dtype=float)
    sm = pd.Series(b).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
    peak_idx = int(np.nanargmax(sm))
    peak_b12 = float(np.nanmax(b))
    return peak_idx, peak_b12


def find_entry(br_cycle: pd.DataFrame, peak_idx: int, confirm: int = CONFIRM) -> pd.Timestamp | None:
    """
    Starting from peak_idx, scan feat_b12_slope_1d for the first transition
    to negative and return the bar after `confirm` consecutive negative bars.
    Tries confirm=3 then confirm=4 if specified confirm=3.
    """
    sub = br_cycle.iloc[peak_idx:].copy()
    sub = sub[sub["F1_dte"] <= LAST_DTE]
    if len(sub) < confirm + 2:
        return None

    slopes = sub["feat_b12_slope_1d"].fillna(method="ffill").to_numpy(dtype=float)

    def _search(c: int) -> pd.Timestamp | None:
        streak = 0
        first_neg = None
        for i, s in enumerate(slopes):
            if not np.isfinite(s):
                streak = 0; first_neg = None; continue
            if s < 0:
                if streak == 0:
                    first_neg = i
                streak += 1
                if streak >= c:
                    entry_pos = first_neg + c  # bar AFTER the Nth confirmed negative
                    if entry_pos < len(sub):
                        return sub.index[entry_pos]
                    return None
            else:
                streak = 0; first_neg = None
        return None

    result = _search(confirm)
    if result is None and confirm == 3:
        result = _search(4)
    return result


# ─── expiry resolution ────────────────────────────────────────────────────────

def resolve_expiry(ticker: str, br_cycle: pd.DataFrame, store: ArcticStore) -> str | None:
    last_date = br_cycle.index.max().date()
    try:
        expiries = store.list_option_expiries(ticker, INTERVAL)
    except Exception:
        return None
    candidates = [e for e in expiries
                  if abs((date.fromisoformat(e) - last_date).days) <= 5]
    if not candidates:
        return None
    return min(candidates, key=lambda e: abs((date.fromisoformat(e) - last_date).days))


# ─── exit timestamp ───────────────────────────────────────────────────────────

def find_exit_ts(br_cycle: pd.DataFrame) -> pd.Timestamp | None:
    """Last borrow-rate bar at or before 12:00 PM on the expiry day."""
    # Try is_expiry_day column first
    if "is_expiry_day" in br_cycle.columns:
        exp_rows = br_cycle[br_cycle["is_expiry_day"] == 1]
        if not exp_rows.empty:
            exp_date = exp_rows.index[0].date()
        else:
            exp_date = br_cycle.index.max().date()
    else:
        exp_date = br_cycle.index.max().date()

    noon = pd.Timestamp(f"{exp_date} 12:00:00")
    # handle timezone-aware index
    if br_cycle.index.tz is not None:
        noon = noon.tz_localize(br_cycle.index.tz)

    candidates = br_cycle[br_cycle.index <= noon]
    if candidates.empty:
        return br_cycle.index.min()
    return candidates.index[-1]


def find_opts_exit_ts(o: pd.DataFrame, br_exit_ts: pd.Timestamp) -> pd.Timestamp | None:
    """Options bar closest to br_exit_ts."""
    if o.empty:
        return None
    exp_date = br_exit_ts.date()
    day_opts = o[o.index.date == exp_date]
    if day_opts.empty:
        day_opts = o
    diffs = np.abs((day_opts.index - br_exit_ts).total_seconds())
    return day_opts.index[int(np.argmin(diffs))]


# ─── direction decomposition (from spread_collapse_signals.py) ────────────────

def compute_direction(br_cycle: pd.DataFrame, entry_ts: pd.Timestamp, exit_ts: pd.Timestamp) -> str:
    def _row(ts: pd.Timestamp) -> pd.Series | None:
        candidates = br_cycle[br_cycle.index <= ts]
        if candidates.empty:
            return None
        return candidates.iloc[-1]

    ep = _row(entry_ts)
    ex = _row(exit_ts)
    if ep is None or ex is None:
        return "unknown"

    f1p, f2p, sp_ = ep.get("F1_close"), ep.get("F2_close"), ep.get("SPOT_close")
    f1e, f2e, se  = ex.get("F1_close"), ex.get("F2_close"), ex.get("SPOT_close")

    for v in [f1p, f2p, sp_, f1e, f2e, se]:
        if v is None or not np.isfinite(float(v)) or float(v) <= 0:
            return "unknown"

    db1 = (float(f1e) - float(se)) - (float(f1p) - float(sp_))
    db2 = (float(f2e) - float(se)) - (float(f2p) - float(sp_))
    denom = abs(-db1) + abs(db2)
    if denom < 1e-6:
        return "flat"
    f1_share = (-db1) / denom
    return "F1→F2" if f1_share > 0.5 else "F2→F1"


# ─── per-cycle simulation ─────────────────────────────────────────────────────

def run_cycle(
    ticker: str,
    cycle_id: int,
    br_cycle: pd.DataFrame,
    opts: pd.DataFrame | None,
    entry_ts: pd.Timestamp,
    br_exit_ts: pd.Timestamp,
    opts_exit_ts: pd.Timestamp | None,
    peak_b12: float,
    use_slippage: bool,
) -> dict:

    lot = LOT_SIZES.get(ticker, 1)
    result: dict = {
        "ticker": ticker, "cycle_id": cycle_id, "lot_size": lot,
        "entry_ts": entry_ts, "br_exit_ts": br_exit_ts,
        "peak_b12": peak_b12, "skip_reason": None,
        "S1_pnl": np.nan, "S2_pnl": np.nan, "B_pnl": np.nan,
        "S1_vs_B": np.nan, "direction": "unknown",
        "K_f2": np.nan, "K_f1": np.nan,
        "F2_entry": np.nan, "F1_entry": np.nan,
        "F2_exit": np.nan, "F1_exit": np.nan,
        "collapse_pct": np.nan, "entry_dte": np.nan,
    }

    # — entry bar from borrow rates —
    ep = br_cycle[br_cycle.index <= entry_ts]
    if ep.empty:
        result["skip_reason"] = "entry_ts before cycle start"
        return result
    ep_row = ep.iloc[-1]

    F1_entry = float(ep_row.get("F1_close", np.nan))
    F2_entry = float(ep_row.get("F2_close", np.nan))
    entry_dte = float(ep_row.get("F1_dte", np.nan))
    result.update({"F1_entry": F1_entry, "F2_entry": F2_entry, "entry_dte": entry_dte})

    # — exit bar from borrow rates —
    ex_row = br_cycle[br_cycle.index <= br_exit_ts].iloc[-1]
    F1_exit = float(ex_row.get("F1_close", np.nan))
    F2_exit = float(ex_row.get("F2_close", np.nan))
    end_b12  = float(ex_row.get("b12", np.nan))
    result.update({"F1_exit": F1_exit, "F2_exit": F2_exit})

    # — collapse % —
    if np.isfinite(peak_b12) and peak_b12 > 0 and np.isfinite(end_b12):
        result["collapse_pct"] = (peak_b12 - end_b12) / peak_b12 * 100

    # — direction decomposition —
    result["direction"] = compute_direction(br_cycle, entry_ts, br_exit_ts)

    # — futures benchmark (B) — always computed —
    if all(np.isfinite(v) for v in [F1_entry, F2_entry, F1_exit, F2_exit]):
        b_pnl_share = (F1_exit - F1_entry) + (F2_entry - F2_exit)
        if use_slippage:
            # Long F1 at entry: pay slippage; short F2 at entry: receive less
            b_pnl_share -= slippage(F1_entry, True, True)  - F1_entry
            b_pnl_share -= F2_entry - slippage(F2_entry, False, True)
            # Exit: sell F1, buy F2
            b_pnl_share -= F1_exit - slippage(F1_exit, False, True)
            b_pnl_share -= slippage(F2_exit, True, True) - F2_exit
        result["B_pnl"] = round(b_pnl_share * lot, 2)
    else:
        result["skip_reason"] = (result["skip_reason"] or "") + "; missing futures prices"

    # — options strategies S1 and S2 —
    if opts is None or opts.empty:
        result["skip_reason"] = (result["skip_reason"] or "") + "; no options data"
        result["S1_vs_B"] = np.nan if np.isnan(result["B_pnl"]) else float("nan")
        return result

    # entry options bar
    entry_opts = get_opts_bar(opts, entry_ts)
    if entry_opts.empty:
        result["skip_reason"] = (result["skip_reason"] or "") + "; no options bar at entry"
        return result

    F_ref_entry = float(entry_opts["futures_ref_price"].iloc[0]) if "futures_ref_price" in entry_opts.columns else F1_entry

    # ATM strikes
    K_f2 = find_atm(entry_opts, F2_entry) if np.isfinite(F2_entry) else find_atm(entry_opts, F_ref_entry)
    K_f1 = find_atm(entry_opts, F1_entry) if np.isfinite(F1_entry) else K_f2
    result.update({"K_f2": K_f2, "K_f1": K_f1})

    if K_f2 is None:
        result["skip_reason"] = (result["skip_reason"] or "") + "; no ATM strike found"
        return result

    dte_entry = entry_dte if np.isfinite(entry_dte) else 5.0

    # — entry prices —
    call_f2_e = px_with_fallback(entry_opts, K_f2, "CE", F2_entry if np.isfinite(F2_entry) else F_ref_entry, dte_entry)
    put_f2_e  = px_with_fallback(entry_opts, K_f2, "PE", F2_entry if np.isfinite(F2_entry) else F_ref_entry, dte_entry)

    call_f1_e = px_with_fallback(entry_opts, K_f1, "CE", F_ref_entry, dte_entry) if K_f1 is not None else None
    put_f1_e  = px_with_fallback(entry_opts, K_f1, "PE", F_ref_entry, dte_entry) if K_f1 is not None else None

    if call_f2_e is None or put_f2_e is None:
        result["skip_reason"] = (result["skip_reason"] or "") + f"; no F2-ATM price at K={K_f2}"
        return result

    if use_slippage:
        call_f2_e_paid = slippage(call_f2_e, True)     # buy call: pay more
        put_f2_e_recv  = slippage(put_f2_e,  False)    # sell put: receive less
    else:
        call_f2_e_paid = call_f2_e
        put_f2_e_recv  = put_f2_e

    if K_f1 is not None and call_f1_e is not None and put_f1_e is not None:
        if use_slippage:
            put_f1_e_paid  = slippage(put_f1_e,  True)   # buy put
            call_f1_e_recv = slippage(call_f1_e, False)  # sell call
        else:
            put_f1_e_paid  = put_f1_e
            call_f1_e_recv = call_f1_e
        has_f1 = True
    else:
        has_f1 = False

    # — exit options bar —
    if opts_exit_ts is None:
        result["skip_reason"] = (result["skip_reason"] or "") + "; no options exit bar"
        return result

    exit_opts = get_opts_bar(opts, opts_exit_ts)
    F_exit_ref = F1_exit if np.isfinite(F1_exit) else float(exit_opts["futures_ref_price"].iloc[0]) if (not exit_opts.empty and "futures_ref_price" in exit_opts.columns) else np.nan

    if not np.isfinite(F_exit_ref):
        result["skip_reason"] = (result["skip_reason"] or "") + "; no F at exit"
        return result

    # exit prices (DTE ≈ 0 at 12 PM on expiry day)
    call_f2_x = px_with_fallback(exit_opts, K_f2, "CE", F_exit_ref, 0.0, is_exit=True)
    put_f2_x  = px_with_fallback(exit_opts, K_f2, "PE", F_exit_ref, 0.0, is_exit=True)

    if call_f2_x is None or put_f2_x is None:
        # Use intrinsic directly (DTE ~ 0)
        call_f2_x = max(0.0, F_exit_ref - K_f2)
        put_f2_x  = max(0.0, K_f2 - F_exit_ref)

    if use_slippage:
        call_f2_x_recv = slippage(call_f2_x, False)   # sell call at exit
        put_f2_x_paid  = slippage(put_f2_x,  True)    # buy put at exit
    else:
        call_f2_x_recv = call_f2_x
        put_f2_x_paid  = put_f2_x

    # S1 PnL per share
    s1_pnl_share = (call_f2_x_recv - call_f2_e_paid) - (put_f2_x_paid - put_f2_e_recv)
    result["S1_pnl"] = round(s1_pnl_share * lot, 2)

    # S2 (S1 + F1 synthetic short)
    if has_f1 and K_f1 is not None:
        call_f1_x = px_with_fallback(exit_opts, K_f1, "CE", F_exit_ref, 0.0, is_exit=True)
        put_f1_x  = px_with_fallback(exit_opts, K_f1, "PE", F_exit_ref, 0.0, is_exit=True)
        if call_f1_x is None: call_f1_x = max(0.0, F_exit_ref - K_f1)
        if put_f1_x  is None: put_f1_x  = max(0.0, K_f1 - F_exit_ref)

        if use_slippage:
            put_f1_x_recv  = slippage(put_f1_x,  False)   # sell put at exit (close long put)
            call_f1_x_paid = slippage(call_f1_x, True)    # buy call at exit (close short call)
        else:
            put_f1_x_recv  = put_f1_x
            call_f1_x_paid = call_f1_x

        # F1 synthetic short: bought put + sold call at entry; unwind at exit
        s2_extra_share = (put_f1_x_recv - put_f1_e_paid) - (call_f1_x_paid - call_f1_e_recv)
        result["S2_pnl"] = round((s1_pnl_share + s2_extra_share) * lot, 2)
    else:
        result["S2_pnl"] = result["S1_pnl"]  # F1 leg unavailable; S2 = S1

    if np.isfinite(result["S1_pnl"]) and np.isfinite(result["B_pnl"]):
        result["S1_vs_B"] = round(float(result["S1_pnl"]) - float(result["B_pnl"]), 2)

    return result


# ─── aggregate statistics ─────────────────────────────────────────────────────

def sharpe(arr: np.ndarray) -> float:
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2 or arr.std() == 0:
        return np.nan
    return float(arr.mean() / arr.std())


def max_drawdown(arr: np.ndarray) -> float:
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return np.nan
    cum = np.cumsum(arr)
    running_max = np.maximum.accumulate(cum)
    return float(np.max(running_max - cum))


def win_rate(arr: np.ndarray) -> float:
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return np.nan
    return float((arr > 0).sum() / len(arr) * 100)


# ─── console printing ─────────────────────────────────────────────────────────

def print_per_cycle(rows: list[dict]):
    print("\n" + "=" * 120)
    print("  SECTION 1 — PER-CYCLE RESULTS (HTB collapse cycles, entry = slope-flip + confirmation)")
    print("=" * 120)
    hdr = (f"  {'ticker':<9}{'cid':>4}  {'entry_ts':<19}  {'dte':>4}  "
           f"{'peak_b12':>9}  {'direction':>8}  {'S1(₹)':>10}  {'S2(₹)':>10}  "
           f"{'B(₹)':>10}  {'S1vsB':>8}  note")
    print(hdr)
    print("  " + "─" * 116)
    for r in rows:
        ts_str  = str(r["entry_ts"])[:19] if pd.notna(r["entry_ts"]) else "  n/a"
        s1_str  = f"{r['S1_pnl']:>+10.0f}" if np.isfinite(r["S1_pnl"]) else "       NaN"
        s2_str  = f"{r['S2_pnl']:>+10.0f}" if np.isfinite(r["S2_pnl"]) else "       NaN"
        b_str   = f"{r['B_pnl']:>+10.0f}"  if np.isfinite(r["B_pnl"])  else "       NaN"
        vb_str  = f"{r['S1_vs_B']:>+8.0f}" if np.isfinite(r["S1_vs_B"]) else "     n/a"
        dte_str = f"{r['entry_dte']:>4.0f}" if np.isfinite(r["entry_dte"]) else " n/a"
        note    = f"*{r['skip_reason'][:30]}" if r["skip_reason"] else ""
        print(f"  {r['ticker']:<9}{r['cycle_id']:>4}  {ts_str}  {dte_str}  "
              f"{r['peak_b12']:>9.4f}  {r['direction']:>8}  {s1_str}  {s2_str}  "
              f"{b_str}  {vb_str}  {note}")
    print("  " + "─" * 116)
    print("  * = partial skip; NaN = prices unavailable for that strategy")


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
        wr = win_rate(s1)
        print(f"  {ticker:<9}{len(sub):>4}  {wr:>6.0f}%  "
              f"{np.nanmean(s1):>+12.0f}  {np.nanmean(s2):>+12.0f}  {np.nanmean(b):>+11.0f}  "
              f"{np.nanmax(s1):>+9.0f}  {np.nanmin(s1):>+9.0f}")
    print("  " + "─" * 96)


def print_pooled(rows: list[dict]):
    print("\n" + "=" * 90)
    print("  SECTION 3 — POOLED SUMMARY (all tickers, all qualifying cycles)")
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
            print(f"  {name:<12}{'0':>5}  {'n/a':>7}  {'n/a':>10}  {'n/a':>9}  {'n/a':>8}  {'n/a':>12}  {'n/a':>10}")
            continue
        sh = sharpe(arr)
        md = max_drawdown(arr)
        print(f"  {name:<12}{n:>5}  {win_rate(arr):>6.1f}%  "
              f"{np.nanmean(arr):>+10.0f}  {np.nanstd(arr):>+9.0f}  "
              f"{sh:>+8.2f}  {np.nansum(arr):>+12.0f}  {-md:>+10.0f}")
    print("  " + "─" * 84)

    # direction × outcome contingency
    f2f1 = [r for r in rows if r["direction"] == "F2→F1"]
    f1f2 = [r for r in rows if r["direction"] == "F1→F2"]
    print(f"\n  Direction × S1 outcome:")
    for label, grp in [("F2→F1 (dominant)", f2f1), ("F1→F2", f1f2)]:
        if not grp:
            continue
        arr = np.array([r["S1_pnl"] for r in grp], dtype=float)
        wr  = win_rate(arr)
        print(f"    {label:<20}  n={len(grp):>3}  S1 win%={wr:>5.1f}%  mean={np.nanmean(arr):>+9.0f}₹")


def print_failure_cases(rows: list[dict]):
    failures = [r for r in rows if np.isfinite(r["S1_pnl"]) and np.isfinite(r["B_pnl"])
                and ((r["S1_pnl"] < 0 and r["B_pnl"] > 0)
                     or (r["S1_pnl"] > 0 and r["B_pnl"] < 0 and abs(r["S1_pnl"]) < abs(r["B_pnl"])))]
    if not failures:
        print("\n  No clear failure cases (S1 lost when B won or vice versa).")
        return

    print("\n" + "=" * 100)
    print("  SECTION 4 — FAILURE CASES (S1 lost when B won, or S1 underperformed significantly)")
    print("=" * 100)
    print(f"  {'ticker':<9}{'cid':>4}  {'direction':>8}  "
          f"{'S1(₹)':>9}  {'B(₹)':>9}  {'S1vsB':>8}  {'collapse%':>10}  reason")
    print("  " + "─" * 95)
    for r in failures:
        # Rule-based reason
        if r["direction"] == "F1→F2":
            reason = "F1→F2 direction (S1 is long F2 which didn't rise)"
        elif np.isfinite(r["collapse_pct"]) and r["collapse_pct"] < 20:
            reason = f"small collapse ({r['collapse_pct']:.0f}%) — theta decay dominated"
        elif np.isfinite(r["entry_dte"]) and r["entry_dte"] <= 2:
            reason = "late entry (DTE≤2), insufficient time for move"
        else:
            reason = "other / insufficient move"
        col_str = f"{r['collapse_pct']:.0f}%" if np.isfinite(r["collapse_pct"]) else "n/a"
        print(f"  {r['ticker']:<9}{r['cycle_id']:>4}  {r['direction']:>8}  "
              f"{r['S1_pnl']:>+9.0f}  {r['B_pnl']:>+9.0f}  {r['S1_vs_B']:>+8.0f}  "
              f"{col_str:>10}  {reason}")
    print("  " + "─" * 95)


# ─── plots ────────────────────────────────────────────────────────────────────

def plot_pnl_by_cycle(rows: list[dict], out: Path):
    valid = [r for r in rows if np.isfinite(r["S1_pnl"]) or np.isfinite(r["B_pnl"])]
    if not valid:
        return
    valid.sort(key=lambda r: r["entry_ts"] if pd.notna(r["entry_ts"]) else pd.Timestamp.min)

    labels = [f"{r['ticker']}\n{r['cycle_id']}" for r in valid]
    s1 = [r["S1_pnl"] if np.isfinite(r["S1_pnl"]) else 0 for r in valid]
    b  = [r["B_pnl"]  if np.isfinite(r["B_pnl"])  else 0 for r in valid]
    x  = np.arange(len(valid))
    w  = 0.35

    fig, ax = plt.subplots(figsize=(max(14, len(valid) * 0.7), 6))
    colors_s1 = [TICKER_COLORS.get(r["ticker"], "grey") for r in valid]
    bars_s1 = ax.bar(x - w/2, s1, w, color=colors_s1, alpha=0.85, label="S1 (F2 synthetic)")
    bars_b  = ax.bar(x + w/2, b,  w, color="lightgrey",  alpha=0.85, label="B (futures cal)", edgecolor="grey", lw=0.5)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right")
    ax.set_ylabel("PnL (₹ per lot)")
    ax.set_title("Per-cycle PnL — S1 (F2 synthetic long, coloured by ticker) vs B (futures calendar, grey)\n"
                 "b12 slope-flip signal, HTB cycles only")
    ax.legend()
    ax.grid(alpha=0.2, axis="y")

    # value labels on large bars
    for rect in list(bars_s1) + list(bars_b):
        h = rect.get_height()
        if abs(h) > 2000:
            ax.text(rect.get_x() + rect.get_width() / 2, h + (200 if h >= 0 else -400),
                    f"{h:+.0f}", ha="center", fontsize=6.5)

    fig.tight_layout()
    p = out / "g1_pnl_by_cycle.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    print(f"  Saved: {p}")


def plot_cumulative(rows: list[dict], out: Path):
    valid = sorted([r for r in rows if r.get("entry_ts") is not None and pd.notna(r["entry_ts"])],
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
        valid_mask = np.isfinite(arr)
        cumsum = np.nancumsum(np.where(valid_mask, arr, 0))
        ax.plot(range(len(cumsum)), cumsum, color=col, ls=ls, lw=2, label=name)
        ax.plot(range(len(cumsum)), cumsum, ".", color=col, ms=5)

    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("Cycle number (sorted by entry date)")
    ax.set_ylabel("Cumulative PnL (₹)")
    ax.set_title("Equity curves — S1, S2, B (HTB cycles, all tickers)\n"
                 "Shaded area: positive cumulative PnL")
    ax.fill_between(range(len(s1)), np.nancumsum(np.where(np.isfinite(s1), s1, 0)),
                    0, where=np.nancumsum(np.where(np.isfinite(s1), s1, 0)) > 0,
                    alpha=0.08, color="#e74c3c")
    ax.legend()
    ax.grid(alpha=0.2)
    fig.tight_layout()
    p = out / "g2_cumulative_pnl.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    print(f"  Saved: {p}")


def plot_direction_vs_pnl(rows: list[dict], out: Path):
    f2f1_s1 = [r["S1_pnl"] for r in rows if r["direction"] == "F2→F1" and np.isfinite(r["S1_pnl"])]
    f1f2_s1 = [r["S1_pnl"] for r in rows if r["direction"] == "F1→F2" and np.isfinite(r["S1_pnl"])]
    f2f1_b  = [r["B_pnl"]  for r in rows if r["direction"] == "F2→F1" and np.isfinite(r["B_pnl"])]
    f1f2_b  = [r["B_pnl"]  for r in rows if r["direction"] == "F1→F2" and np.isfinite(r["B_pnl"])]

    fig, axes = plt.subplots(1, 2, figsize=(13, 6), sharey=False)
    for ax, name, d_f2f1, d_f1f2 in [
        (axes[0], "S1 (F2 Synthetic Long)", f2f1_s1, f1f2_s1),
        (axes[1], "B  (Futures Calendar)", f2f1_b, f1f2_b),
    ]:
        data = [d_f2f1, d_f1f2]
        bp = ax.boxplot(data, patch_artist=True, widths=0.4,
                        labels=[f"F2→F1\n(n={len(d_f2f1)})", f"F1→F2\n(n={len(d_f1f2)})"])
        bp["boxes"][0].set_facecolor("#3498db"); bp["boxes"][0].set_alpha(0.7)
        if len(bp["boxes"]) > 1:
            bp["boxes"][1].set_facecolor("#e74c3c"); bp["boxes"][1].set_alpha(0.7)
        ax.axhline(0, color="k", lw=0.8, ls="--")
        ax.set_ylabel("PnL per lot (₹)")
        ax.set_title(f"{name}\nPnL by collapse direction")
        ax.grid(alpha=0.2, axis="y")
        # Add mean markers
        for i, d in enumerate([d_f2f1, d_f1f2], 1):
            if d:
                ax.plot(i, np.mean(d), "D", color="k", ms=8, zorder=5, label=f"mean={np.mean(d):+.0f}")
        ax.legend(fontsize=8)

    fig.suptitle("PnL split by collapse direction (F2→F1 vs F1→F2)\n"
                 "S1 should profit in F2→F1 regime (dominant); B profits in opposite",
                 fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    p = out / "g3_direction_vs_pnl.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    print(f"  Saved: {p}")


def plot_pnl_vs_collapse(rows: list[dict], out: Path):
    valid = [r for r in rows if np.isfinite(r["collapse_pct"]) and
             (np.isfinite(r["S1_pnl"]) or np.isfinite(r["B_pnl"]))]
    if not valid:
        return

    fig, axes = plt.subplots(1, 2, figsize=(13, 6))
    for ax, col_key, name in [
        (axes[0], "S1_pnl", "S1 (F2 Synthetic Long)"),
        (axes[1], "B_pnl",  "B  (Futures Calendar)"),
    ]:
        sub = [r for r in valid if np.isfinite(r[col_key])]
        if not sub:
            continue
        xs = [r["collapse_pct"] for r in sub]
        ys = [r[col_key] for r in sub]
        cols = [TICKER_COLORS.get(r["ticker"], "grey") for r in sub]
        for ticker in TICKERS:
            tsub = [r for r in sub if r["ticker"] == ticker]
            if not tsub:
                continue
            ax.scatter([r["collapse_pct"] for r in tsub],
                       [r[col_key] for r in tsub],
                       color=TICKER_COLORS.get(ticker, "grey"),
                       label=ticker, alpha=0.8, s=60, zorder=3)
        ax.axhline(0, color="k", lw=0.8, ls="--")
        ax.set_xlabel("b12 collapse % (peak → expiry)")
        ax.set_ylabel("PnL per lot (₹)")
        ax.set_title(f"{name}\nvs collapse magnitude")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.2)
        # Trend line
        if len(xs) >= 4:
            from numpy.polynomial import polynomial as P
            c = P.polyfit(xs, ys, 1)
            xl = np.linspace(min(xs), max(xs), 50)
            ax.plot(xl, P.polyval(xl, c), "k--", lw=1.2, label="_trend")

    fig.suptitle("PnL vs b12 collapse magnitude — HTB cycles, all tickers\n"
                 "Larger collapse = stronger thesis for S1", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    p = out / "g4_pnl_vs_collapse.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    print(f"  Saved: {p}")


# ─── main ─────────────────────────────────────────────────────────────────────

def parse_args():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers",     nargs="+", default=TICKERS)
    ap.add_argument("--confirm",     type=int,  default=CONFIRM)
    ap.add_argument("--no-slippage", action="store_true")
    ap.add_argument("--dte-window",  type=int,  default=LAST_DTE)
    ap.add_argument("--htb-thresh",  type=float, default=HTB_THRESH)
    ap.add_argument("--out",         default=str(OUT_DIR))
    return ap.parse_args()


def main():
    args = parse_args()
    out  = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    use_slippage = not args.no_slippage

    store = ArcticStore()
    all_rows: list[dict] = []

    print(f"\nBacktesting b12 slope-flip strategies")
    print(f"  Tickers: {args.tickers}")
    print(f"  HTB threshold: peak_b12 >= {args.htb_thresh}")
    print(f"  Confirmation: {args.confirm} bars   |  DTE window: {args.dte_window}")
    print(f"  Slippage: {'OFF' if args.no_slippage else f'OPT={SLIPPAGE_OPT*100:.1f}% FUT={SLIPPAGE_FUT*100:.1f}%'}")

    for ticker in args.tickers:
        print(f"\n  [{ticker}] loading borrow rates …")
        try:
            br = store.read_borrow_rates(ticker, INTERVAL)
        except Exception as e:
            print(f"    ERROR: {e}"); continue
        if br is None or br.empty:
            print(f"    empty — skipping"); continue

        br = br.copy()
        all_expiries = store.list_option_expiries(ticker, INTERVAL)
        n_cycles = br["cycle_id"].nunique()
        print(f"    {len(br):,} bars, {n_cycles} cycles, {len(all_expiries)} option expiries")

        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index()

            # HTB filter
            peak_idx, peak_b12 = detect_peak(cyc)
            if peak_b12 < args.htb_thresh:
                continue

            # Entry signal
            entry_ts = find_entry(cyc, peak_idx, args.confirm)
            if entry_ts is None:
                continue

            # Exit timestamp (12 PM on expiry day)
            br_exit_ts = find_exit_ts(cyc)
            if br_exit_ts is None or br_exit_ts <= entry_ts:
                continue

            # Resolve options expiry
            expiry_str = resolve_expiry(ticker, cyc, store)
            if expiry_str is None:
                all_rows.append({
                    "ticker": ticker, "cycle_id": int(cid), "lot_size": LOT_SIZES.get(ticker, 1),
                    "entry_ts": entry_ts, "br_exit_ts": br_exit_ts, "peak_b12": peak_b12,
                    "skip_reason": "no matching options expiry",
                    "S1_pnl": np.nan, "S2_pnl": np.nan, "B_pnl": np.nan,
                    "S1_vs_B": np.nan, "direction": "unknown", "K_f2": np.nan, "K_f1": np.nan,
                    "F2_entry": np.nan, "F1_entry": np.nan, "F2_exit": np.nan, "F1_exit": np.nan,
                    "collapse_pct": np.nan, "entry_dte": np.nan,
                })
                continue

            # Load options data
            try:
                opts = store.read_options(ticker, INTERVAL, expiry_str)
            except Exception:
                opts = pd.DataFrame()

            opts_exit_ts = find_opts_exit_ts(opts, br_exit_ts) if (opts is not None and not opts.empty) else None

            result = run_cycle(
                ticker=ticker, cycle_id=int(cid),
                br_cycle=cyc, opts=opts,
                entry_ts=entry_ts, br_exit_ts=br_exit_ts,
                opts_exit_ts=opts_exit_ts,
                peak_b12=peak_b12,
                use_slippage=use_slippage,
            )
            all_rows.append(result)
            status = "OK" if result["skip_reason"] is None else f"PARTIAL({result['skip_reason'][:30]})"
            s1_str = f"S1={result['S1_pnl']:+.0f}₹" if np.isfinite(result["S1_pnl"]) else "S1=n/a"
            b_str  = f"B={result['B_pnl']:+.0f}₹"   if np.isfinite(result["B_pnl"])  else "B=n/a"
            print(f"    cid={cid:>3}  peak_b12={peak_b12:.4f}  entry_dte={result['entry_dte']:.0f}  "
                  f"{s1_str}  {b_str}  [{status}]")

    if not all_rows:
        print("\nNo qualifying cycles found.")
        return

    # Sort chronologically for equity curve
    all_rows.sort(key=lambda r: r["entry_ts"] if pd.notna(r["entry_ts"]) else pd.Timestamp.max)

    # Console output
    print_per_cycle(all_rows)
    print_per_ticker(all_rows)
    print_pooled(all_rows)
    print_failure_cases(all_rows)

    # Plots
    print(f"\nSaving figures to {out} …")
    plot_pnl_by_cycle(all_rows, out)
    plot_cumulative(all_rows, out)
    plot_direction_vs_pnl(all_rows, out)
    plot_pnl_vs_collapse(all_rows, out)

    n_ok  = sum(1 for r in all_rows if r["skip_reason"] is None)
    n_all = len(all_rows)
    print(f"\nDone. {n_ok}/{n_all} cycles clean.  Saved 4 figures to:\n  {out}")


if __name__ == "__main__":
    main()
