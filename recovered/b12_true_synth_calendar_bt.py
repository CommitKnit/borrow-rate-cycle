"""
b12_true_synth_calendar_bt.py
==============================
Synthetic calendar spread using CORRECT option chains:

  SLF2  — Synthetic Long F2  : BUY call + SELL put at K_f2 (F2-ATM)
                                using F2-EXPIRY options
                                → tracks actual F2 price movement

  SSF1  — Synthetic Short F1 : BUY put  + SELL call at K_f1 (F1-ATM)
                                using F1-EXPIRY options
                                → tracks actual F1 price movement

  S_CAL — SLF2 + SSF1        : synthetic calendar = SHORT F1 + LONG F2
                                PnL ≈ initial_spread − final_spread

  B     — Futures calendar    : SHORT F1 + LONG F2 futures (benchmark)
                                PnL = (F1_entry−F1_exit) + (F2_exit−F2_entry)

Entry: at b12 PEAK bar (smoothed 11-bar argmax), HTB cycles only.
Exit:  12 PM on F1 expiry day (F2 options closed early — still have ~60 DTE).
Tickers: SBICARD, RVNL, KPITTECH, ASTRAL.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from datetime import date

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
TICKERS      = ["SBICARD", "RVNL", "KPITTECH", "ASTRAL"]
LOT_SIZES    = {"SBICARD": 800, "RVNL": 1525, "KPITTECH": 425, "ASTRAL": 425}
INTERVAL     = "15minute"
SMOOTH       = 11
HTB_THRESH   = 0.05
SLIPPAGE_OPT = 0.005
SLIPPAGE_FUT = 0.001
RISK_FREE    = 0.065
OUT_DIR      = ROOT / "plots" / "b12_true_synth_calendar"

TICKER_COLORS = {
    "SBICARD": "#e74c3c", "RVNL": "#3498db",
    "KPITTECH": "#2ecc71", "ASTRAL": "#f39c12",
}


# ─── data helpers ─────────────────────────────────────────────────────────────

def get_opts_bar(o: pd.DataFrame, ts: pd.Timestamp,
                 window: pd.Timedelta = pd.Timedelta("45min")) -> pd.DataFrame:
    """Return options bars at ts or nearest within ±window (handles tz)."""
    if o.empty:
        return pd.DataFrame()
    ts_c = ts.tz_convert(o.index.tz) if (o.index.tz and ts.tzinfo is None) else ts
    if ts_c.tzinfo is None and o.index.tz is not None:
        ts_c = ts_c.tz_localize(o.index.tz)
    lo, hi = ts_c - window, ts_c + window
    sub = o[(o.index >= lo) & (o.index <= hi)]
    if sub.empty:
        return pd.DataFrame()
    nearest = sub.index[np.argmin(np.abs((sub.index - ts_c).total_seconds()))]
    return sub[sub.index == nearest]


def px(bar: pd.DataFrame, strike: float, otype: str) -> float:
    r = bar[(bar["strike"] == strike) & (bar["opt_type"] == otype)]
    if r.empty:
        return np.nan
    v = float(r["close"].iloc[0])
    return max(v, 0.05) if v > 0 else np.nan


def px_fallback(bar: pd.DataFrame, K: float, otype: str,
                F: float, dte: float, is_exit: bool = False) -> float | None:
    v = px(bar, K, otype)
    if np.isfinite(v):
        return v
    # at exit, use intrinsic
    if is_exit or dte <= 0:
        return max(0.0, F - K) if otype == "CE" else max(0.0, K - F)
    # Black-76 fallback
    r = bar[(bar["strike"] == K) & (bar["opt_type"] == otype)]
    if not r.empty:
        iv = float(r["iv"].iloc[0]) if "iv" in r.columns else np.nan
        if np.isfinite(iv) and iv > 0 and F > 0:
            try:
                return black76_price(F, K, max(dte / 365.0, 1e-6), iv, RISK_FREE, otype == "CE")
            except Exception:
                pass
    return None


def find_atm(bar: pd.DataFrame, F: float) -> float | None:
    if bar.empty:
        return None
    strikes = bar["strike"].unique()
    return float(strikes[np.argmin(np.abs(strikes - F))]) if len(strikes) else None


def slip(price: float, is_buy: bool, is_fut: bool = False) -> float:
    pct = SLIPPAGE_FUT if is_fut else SLIPPAGE_OPT
    return price * (1 + pct) if is_buy else price * (1 - pct)


# ─── peak detection ───────────────────────────────────────────────────────────

def detect_peak(cyc: pd.DataFrame) -> tuple[int, float]:
    b  = cyc["b12"].ffill().to_numpy(float)
    sm = pd.Series(b).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
    idx = int(np.nanargmax(sm))
    return idx, float(np.nanmax(b))


# ─── expiry helpers ───────────────────────────────────────────────────────────

def find_f1_expiry(cyc: pd.DataFrame, expiries: list[str]) -> str | None:
    last_d = cyc.index.max().date()
    cands = [e for e in expiries if abs((date.fromisoformat(e) - last_d).days) <= 5]
    return min(cands, key=lambda e: abs((date.fromisoformat(e) - last_d).days)) if cands else None


def find_f2_expiry(f1_exp: str, expiries: list[str]) -> str | None:
    f1_dt = date.fromisoformat(f1_exp)
    later = [e for e in sorted(expiries) if date.fromisoformat(e) > f1_dt]
    return later[0] if later else None


def find_exit_ts(cyc: pd.DataFrame) -> pd.Timestamp | None:
    if "is_expiry_day" in cyc.columns:
        exp_rows = cyc[cyc["is_expiry_day"] == 1]
        exp_date = exp_rows.index[0].date() if not exp_rows.empty else cyc.index.max().date()
    else:
        exp_date = cyc.index.max().date()
    noon = pd.Timestamp(f"{exp_date} 12:00:00")
    if cyc.index.tz is not None:
        noon = noon.tz_localize(cyc.index.tz)
    cands = cyc[cyc.index <= noon]
    return cands.index[-1] if not cands.empty else None


# ─── direction decomposition ──────────────────────────────────────────────────

def compute_direction(cyc: pd.DataFrame, entry_ts, exit_ts) -> str:
    ep = cyc[cyc.index <= entry_ts]
    ex = cyc[cyc.index <= exit_ts]
    if ep.empty or ex.empty:
        return "unknown"
    er, xr = ep.iloc[-1], ex.iloc[-1]
    vals = [er.get(c) for c in ["F1_close","F2_close","SPOT_close"]] + \
           [xr.get(c) for c in ["F1_close","F2_close","SPOT_close"]]
    if any(v is None or not np.isfinite(float(v)) or float(v) <= 0 for v in vals):
        return "unknown"
    f1p,f2p,sp_ = float(er.F1_close), float(er.F2_close), float(er.SPOT_close)
    f1e,f2e,se  = float(xr.F1_close), float(xr.F2_close), float(xr.SPOT_close)
    db1 = (f1e - se) - (f1p - sp_)
    db2 = (f2e - se) - (f2p - sp_)
    denom = abs(-db1) + abs(db2)
    if denom < 1e-6:
        return "flat"
    return "F1→F2" if (-db1) / denom > 0.5 else "F2→F1"


# ─── per-cycle simulation ─────────────────────────────────────────────────────

def run_cycle(
    ticker: str, cid: int, cyc: pd.DataFrame,
    f1_opts: pd.DataFrame, f2_opts: pd.DataFrame,
    entry_ts: pd.Timestamp, exit_ts: pd.Timestamp,
    peak_b12: float, use_slip: bool,
) -> dict:

    lot = LOT_SIZES.get(ticker, 1)
    result = {
        "ticker": ticker, "cid": cid, "lot": lot,
        "entry_ts": entry_ts, "exit_ts": exit_ts, "peak_b12": peak_b12,
        "skip": None,
        "SLF2": np.nan, "SSF1": np.nan, "S_CAL": np.nan, "B": np.nan,
        "direction": "unknown",
        "K_f2": np.nan, "K_f1": np.nan,
        "F1_entry": np.nan, "F2_entry": np.nan,
        "F1_exit": np.nan,  "F2_exit": np.nan,
        "entry_dte": np.nan, "spread_entry": np.nan, "spread_exit": np.nan,
        "collapse_pct": np.nan,
    }

    # borrow rates rows
    ep_br = cyc[cyc.index <= entry_ts]
    ex_br = cyc[cyc.index <= exit_ts]
    if ep_br.empty or ex_br.empty:
        result["skip"] = "missing br rows"; return result

    er, xr = ep_br.iloc[-1], ex_br.iloc[-1]
    F1e = float(er.get("F1_close", np.nan))
    F2e = float(er.get("F2_close", np.nan))
    F1x = float(xr.get("F1_close", np.nan))
    F2x = float(xr.get("F2_close", np.nan))
    dte_e = float(er.get("F1_dte", np.nan))
    end_b12 = float(xr.get("b12", np.nan))

    result.update({"F1_entry": F1e, "F2_entry": F2e, "F1_exit": F1x, "F2_exit": F2x,
                   "entry_dte": dte_e})

    if all(np.isfinite(v) for v in [F1e, F2e, F1x, F2x]):
        result["spread_entry"] = F1e - F2e
        result["spread_exit"]  = F1x - F2x
        result["collapse_pct"] = ((peak_b12 - end_b12) / peak_b12 * 100
                                  if np.isfinite(peak_b12) and peak_b12 > 0 and np.isfinite(end_b12)
                                  else np.nan)
    result["direction"] = compute_direction(cyc, entry_ts, exit_ts)

    # ── B: futures calendar SHORT F1 + LONG F2 ──
    if all(np.isfinite(v) for v in [F1e, F2e, F1x, F2x]):
        b_pnl = (F1e - F1x) + (F2x - F2e)
        if use_slip:
            b_pnl -= slip(F1e, False, True) - F1e   # sell F1: receive less
            b_pnl -= F2e - slip(F2e, True,  True)   # buy F2:  pay more
            b_pnl -= F1x - slip(F1x, True,  True)   # buy back F1: pay more
            b_pnl -= slip(F2x, False, True) - F2x   # sell F2: receive less
        result["B"] = round(b_pnl * lot, 2)

    # ── SLF2: synthetic long F2 using F2-expiry options ──
    f2_entry_bar = get_opts_bar(f2_opts, entry_ts)
    f2_exit_bar  = get_opts_bar(f2_opts, exit_ts)

    if f2_entry_bar.empty:
        result["skip"] = (result["skip"] or "") + "; no F2-opts entry bar"
    else:
        # K_f2 = ATM relative to F2 price (futures_ref_price in F2 opts = F2)
        F2_ref_entry = float(f2_entry_bar["futures_ref_price"].iloc[0]) if "futures_ref_price" in f2_entry_bar.columns else F2e
        K_f2 = find_atm(f2_entry_bar, F2_ref_entry)
        result["K_f2"] = K_f2

        if K_f2 is None:
            result["skip"] = (result["skip"] or "") + "; no F2 ATM strike"
        else:
            f2_dte_entry = float(f2_entry_bar["dte"].iloc[0]) if "dte" in f2_entry_bar.columns else 60.0
            F2_ref_entry_val = F2_ref_entry if np.isfinite(F2_ref_entry) else F2e

            call_f2_e = px_fallback(f2_entry_bar, K_f2, "CE", F2_ref_entry_val, f2_dte_entry)
            put_f2_e  = px_fallback(f2_entry_bar, K_f2, "PE", F2_ref_entry_val, f2_dte_entry)

            if call_f2_e is None or put_f2_e is None:
                result["skip"] = (result["skip"] or "") + f"; no F2 price at K={K_f2}"
            else:
                if use_slip:
                    call_f2_e_paid = slip(call_f2_e, True)
                    put_f2_e_recv  = slip(put_f2_e,  False)
                else:
                    call_f2_e_paid = call_f2_e
                    put_f2_e_recv  = put_f2_e

                if f2_exit_bar.empty:
                    result["skip"] = (result["skip"] or "") + "; no F2-opts exit bar"
                else:
                    F2_ref_exit = float(f2_exit_bar["futures_ref_price"].iloc[0]) if "futures_ref_price" in f2_exit_bar.columns else F2x
                    f2_dte_exit = float(f2_exit_bar["dte"].iloc[0]) if "dte" in f2_exit_bar.columns else 30.0

                    call_f2_x = px_fallback(f2_exit_bar, K_f2, "CE", F2_ref_exit, f2_dte_exit)
                    put_f2_x  = px_fallback(f2_exit_bar, K_f2, "PE", F2_ref_exit, f2_dte_exit)

                    if call_f2_x is None or put_f2_x is None:
                        result["skip"] = (result["skip"] or "") + "; no F2 exit price"
                    else:
                        if use_slip:
                            call_f2_x_recv = slip(call_f2_x, False)
                            put_f2_x_paid  = slip(put_f2_x,  True)
                        else:
                            call_f2_x_recv = call_f2_x
                            put_f2_x_paid  = put_f2_x

                        # SLF2 PnL/share = Δcall − Δput = (call_exit − call_entry) − (put_exit − put_entry)
                        slf2 = (call_f2_x_recv - call_f2_e_paid) - (put_f2_x_paid - put_f2_e_recv)
                        result["SLF2"] = round(slf2 * lot, 2)

    # ── SSF1: synthetic short F1 using F1-expiry options ──
    f1_entry_bar = get_opts_bar(f1_opts, entry_ts)
    f1_exit_bar  = get_opts_bar(f1_opts, exit_ts)

    if f1_entry_bar.empty:
        result["skip"] = (result["skip"] or "") + "; no F1-opts entry bar"
    else:
        F1_ref_entry = float(f1_entry_bar["futures_ref_price"].iloc[0]) if "futures_ref_price" in f1_entry_bar.columns else F1e
        K_f1 = find_atm(f1_entry_bar, F1_ref_entry)
        result["K_f1"] = K_f1

        if K_f1 is not None:
            f1_dte_entry = dte_e if np.isfinite(dte_e) else 5.0
            F1_ref_val = F1_ref_entry if np.isfinite(F1_ref_entry) else F1e

            put_f1_e  = px_fallback(f1_entry_bar, K_f1, "PE", F1_ref_val, f1_dte_entry)
            call_f1_e = px_fallback(f1_entry_bar, K_f1, "CE", F1_ref_val, f1_dte_entry)

            if put_f1_e is not None and call_f1_e is not None:
                if use_slip:
                    put_f1_e_paid  = slip(put_f1_e,  True)
                    call_f1_e_recv = slip(call_f1_e, False)
                else:
                    put_f1_e_paid = put_f1_e; call_f1_e_recv = call_f1_e

                if not f1_exit_bar.empty:
                    F1_ref_exit = float(f1_exit_bar["futures_ref_price"].iloc[0]) if "futures_ref_price" in f1_exit_bar.columns else F1x
                    f1_dte_exit = 0.0  # at or near F1 expiry

                    put_f1_x  = px_fallback(f1_exit_bar, K_f1, "PE", F1_ref_exit, f1_dte_exit, is_exit=True)
                    call_f1_x = px_fallback(f1_exit_bar, K_f1, "CE", F1_ref_exit, f1_dte_exit, is_exit=True)
                    if put_f1_x  is None: put_f1_x  = max(0.0, K_f1 - F1_ref_exit)
                    if call_f1_x is None: call_f1_x = max(0.0, F1_ref_exit - K_f1)

                    if use_slip:
                        put_f1_x_recv  = slip(put_f1_x,  False)
                        call_f1_x_paid = slip(call_f1_x, True)
                    else:
                        put_f1_x_recv = put_f1_x; call_f1_x_paid = call_f1_x

                    # SSF1 PnL/share = Δput − Δcall = (put_exit − put_entry) − (call_exit − call_entry)
                    ssf1 = (put_f1_x_recv - put_f1_e_paid) - (call_f1_x_paid - call_f1_e_recv)
                    result["SSF1"] = round(ssf1 * lot, 2)

    # ── S_CAL = SLF2 + SSF1 ──
    if np.isfinite(result["SLF2"]) and np.isfinite(result["SSF1"]):
        result["S_CAL"] = round(float(result["SLF2"]) + float(result["SSF1"]), 2)

    return result


# ─── statistics ───────────────────────────────────────────────────────────────

def sharpe(a):
    a = a[np.isfinite(a)]
    return float(a.mean() / a.std()) if len(a) >= 2 and a.std() > 0 else np.nan

def win_rate(a):
    a = a[np.isfinite(a)]
    return float((a > 0).mean() * 100) if len(a) else np.nan

def max_dd(a):
    a = a[np.isfinite(a)]
    cum = np.cumsum(a)
    return float(np.max(np.maximum.accumulate(cum) - cum)) if len(a) else np.nan


# ─── console output ───────────────────────────────────────────────────────────

def print_per_cycle(rows):
    print("\n" + "=" * 150)
    print("  SECTION 1 — PER-CYCLE  (peak entry | SLF2=F2-expiry opts | SSF1=F1-expiry opts | S_CAL=combined | B=futures)")
    print("=" * 150)
    hdr = (f"  {'ticker':<9}{'cid':>4}  {'entry_ts':<19}  {'dte':>4}  {'peak_b12':>9}  "
           f"{'spread_e':>9}  {'spread_x':>9}  {'dir':>8}  "
           f"{'SLF2(₹)':>10}  {'SSF1(₹)':>10}  {'S_CAL(₹)':>10}  {'B(₹)':>10}  {'CAL-B':>8}  note")
    print(hdr)
    print("  " + "─" * 146)
    for r in rows:
        ts  = str(r["entry_ts"])[:19] if pd.notna(r.get("entry_ts")) else "n/a"
        dte = f"{r['entry_dte']:.0f}" if np.isfinite(r.get("entry_dte", np.nan)) else "n/a"
        se  = f"{r['spread_entry']:>+9.2f}" if np.isfinite(r.get("spread_entry", np.nan)) else "      n/a"
        sx  = f"{r['spread_exit']:>+9.2f}"  if np.isfinite(r.get("spread_exit",  np.nan)) else "      n/a"
        def fmt(k): return f"{r[k]:>+10.0f}" if np.isfinite(r.get(k, np.nan)) else "       NaN"
        cal_b = (f"{r['S_CAL'] - r['B']:>+8.0f}" if np.isfinite(r.get("S_CAL", np.nan)) and np.isfinite(r.get("B", np.nan)) else "     n/a")
        note = f"*{r['skip'][:35]}" if r.get("skip") else ""
        print(f"  {r['ticker']:<9}{r['cid']:>4}  {ts}  {dte:>4}  {r['peak_b12']:>9.4f}  "
              f"{se}  {sx}  {r['direction']:>8}  "
              f"{fmt('SLF2')}  {fmt('SSF1')}  {fmt('S_CAL')}  {fmt('B')}  {cal_b}  {note}")
    print("  " + "─" * 146)


def print_summary(rows):
    print("\n" + "=" * 100)
    print("  SECTION 2 — POOLED SUMMARY")
    print("=" * 100)
    for name, key in [("SLF2  (synth long F2)", "SLF2"),
                      ("SSF1  (synth short F1)", "SSF1"),
                      ("S_CAL (combined)", "S_CAL"),
                      ("B     (futures cal)", "B")]:
        arr = np.array([r[key] for r in rows], float)
        v = arr[np.isfinite(arr)]
        n = len(v)
        if n == 0:
            print(f"  {name:<25}  n=  0"); continue
        print(f"  {name:<25}  n={n:>3}  win%={win_rate(arr):>5.1f}%  "
              f"mean={np.nanmean(arr):>+10.0f}₹  std={np.nanstd(arr):>+9.0f}  "
              f"Sharpe={sharpe(arr):>+6.2f}  cum={np.nansum(arr):>+12.0f}₹  "
              f"maxDD={-max_dd(arr):>+10.0f}₹")
    print("  " + "─" * 96)

    # S_CAL vs B tracking
    both = [r for r in rows if np.isfinite(r["S_CAL"]) and np.isfinite(r["B"])]
    if both:
        diffs = np.array([r["S_CAL"] - r["B"] for r in both])
        print(f"\n  S_CAL vs B tracking error (should be ~0 if synthetic = futures):")
        print(f"    n={len(both)}  mean diff={diffs.mean():>+.0f}₹  std={diffs.std():>+.0f}₹  "
              f"max_diff={diffs.max():>+.0f}₹  min_diff={diffs.min():>+.0f}₹")
        print(f"    % cycles where S_CAL > B: {(diffs>0).mean()*100:.0f}%")

    # Per-ticker
    print(f"\n  Per-ticker S_CAL vs B:")
    print(f"  {'ticker':<10}{'n':>4}  {'S_CAL mean':>12}  {'B mean':>10}  {'S_CAL win%':>11}  {'B win%':>8}")
    print("  " + "─" * 60)
    for tk in TICKERS:
        sub = [r for r in rows if r["ticker"] == tk]
        if not sub: continue
        sc = np.array([r["S_CAL"] for r in sub], float)
        b  = np.array([r["B"]     for r in sub], float)
        print(f"  {tk:<10}{len(sub):>4}  {np.nanmean(sc):>+12.0f}  {np.nanmean(b):>+10.0f}  "
              f"{win_rate(sc):>10.1f}%  {win_rate(b):>7.1f}%")

    # Direction breakdown
    print(f"\n  S_CAL win% by collapse direction:")
    for d in ["F2→F1", "F1→F2"]:
        sub = [r for r in rows if r["direction"] == d and np.isfinite(r["S_CAL"])]
        if not sub: continue
        arr = np.array([r["S_CAL"] for r in sub], float)
        print(f"    {d}  n={len(sub):>3}  win%={win_rate(arr):>5.1f}%  mean={np.nanmean(arr):>+9.0f}₹")


def print_losses(rows):
    losers = [r for r in rows if np.isfinite(r["S_CAL"]) and r["S_CAL"] < 0]
    if not losers:
        print("\n  No S_CAL losses."); return
    print("\n" + "=" * 130)
    print("  SECTION 3 — S_CAL LOSS CASES")
    print("=" * 130)
    print(f"  {'ticker':<9}{'cid':>4}  {'dte':>4}  {'spread_e':>9}  {'spread_x':>9}  "
          f"{'dir':>8}  {'S_CAL':>9}  {'B':>9}  {'SLF2':>9}  {'SSF1':>9}  reason")
    print("  " + "─" * 126)
    for r in sorted(losers, key=lambda x: x["S_CAL"]):
        dte = f"{r['entry_dte']:.0f}" if np.isfinite(r.get("entry_dte", np.nan)) else "n/a"
        se  = f"{r['spread_entry']:>+9.2f}" if np.isfinite(r.get("spread_entry", np.nan)) else "      n/a"
        sx  = f"{r['spread_exit']:>+9.2f}"  if np.isfinite(r.get("spread_exit",  np.nan)) else "      n/a"
        def fmt(k): return f"{r[k]:>+9.0f}" if np.isfinite(r.get(k, np.nan)) else "      NaN"

        # classify loss
        reasons = []
        se_v = r.get("spread_entry", np.nan); sx_v = r.get("spread_exit", np.nan)
        if np.isfinite(se_v) and np.isfinite(sx_v) and sx_v > se_v:
            reasons.append(f"spread WIDENED after entry ({se_v:+.1f}→{sx_v:+.1f}): entered before true peak")
        if np.isfinite(se_v) and se_v <= 0:
            reasons.append("peak spread ≤0: b12 peak was DTE-formula artefact")
        if np.isfinite(r.get("entry_dte", np.nan)) and r["entry_dte"] > 10:
            reasons.append(f"early peak (DTE={r['entry_dte']:.0f}): large time-value drag on F2 options")
        slf2_v = r.get("SLF2", np.nan); ssf1_v = r.get("SSF1", np.nan)
        if np.isfinite(slf2_v) and np.isfinite(ssf1_v):
            if slf2_v < 0 and ssf1_v < 0:
                reasons.append("both legs lost: spread widened on both F1 and F2")
            elif slf2_v < 0:
                reasons.append("F2 leg lost: F2 fell (unexpected)")
            elif ssf1_v < 0:
                reasons.append("F1 leg lost: F1 rose instead of falling")
        if not reasons:
            reasons.append("small collapse vs option time-value change")

        print(f"  {r['ticker']:<9}{r['cid']:>4}  {dte:>4}  {se}  {sx}  "
              f"{r['direction']:>8}  {fmt('S_CAL')}  {fmt('B')}  {fmt('SLF2')}  {fmt('SSF1')}  "
              + " | ".join(reasons))
    print("  " + "─" * 126)
    print(f"\n  Total S_CAL losing cycles: {len(losers)}  loss={sum(r['S_CAL'] for r in losers):+,.0f}₹")


# ─── plots ────────────────────────────────────────────────────────────────────

def plot_all(rows, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    valid = sorted([r for r in rows if pd.notna(r.get("entry_ts"))], key=lambda r: r["entry_ts"])

    # 1. Per-cycle bar: S_CAL vs B
    fig, ax = plt.subplots(figsize=(max(14, len(valid)*0.75), 6))
    x = np.arange(len(valid)); w = 0.35
    scal = [r["S_CAL"] if np.isfinite(r.get("S_CAL", np.nan)) else 0 for r in valid]
    b    = [r["B"]     if np.isfinite(r.get("B",     np.nan)) else 0 for r in valid]
    ax.bar(x-w/2, scal, w, color=[TICKER_COLORS.get(r["ticker"],"grey") for r in valid],
           alpha=0.85, label="S_CAL (synth, coloured by ticker)")
    ax.bar(x+w/2, b,    w, color="#95a5a6", alpha=0.7, label="B (futures)")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r['ticker']}\nc{r['cid']}" for r in valid], fontsize=7, rotation=45, ha="right")
    ax.set_ylabel("PnL (₹ per lot)"); ax.set_title("Synthetic Calendar vs Futures Calendar — per cycle")
    ax.legend(); ax.grid(alpha=0.2, axis="y")
    fig.tight_layout(); fig.savefig(out/"g1_scal_vs_b_per_cycle.png", dpi=120); plt.close(fig)

    # 2. Cumulative equity curves
    fig, ax = plt.subplots(figsize=(13, 5))
    for name, key, col, ls in [
        ("S_CAL (synthetic)", "S_CAL", "#e74c3c", "-"),
        ("SLF2  (long F2)",   "SLF2",  "#3498db", "--"),
        ("SSF1  (short F1)",  "SSF1",  "#2ecc71", "-."),
        ("B     (futures)",   "B",     "#95a5a6", ":"),
    ]:
        arr = np.array([r[key] for r in valid], float)
        cum = np.nancumsum(np.where(np.isfinite(arr), arr, 0))
        ax.plot(range(len(cum)), cum, color=col, ls=ls, lw=2, label=name)
    ax.axhline(0, color="k", lw=0.8); ax.legend(); ax.grid(alpha=0.2)
    ax.set_xlabel("Cycle #"); ax.set_ylabel("Cumulative PnL (₹)")
    ax.set_title("Equity curves — S_CAL, SLF2, SSF1, B (peak entry)")
    fig.tight_layout(); fig.savefig(out/"g2_cumulative.png", dpi=120); plt.close(fig)

    # 3. S_CAL vs B scatter (tracking quality)
    both = [(r["B"], r["S_CAL"]) for r in rows if np.isfinite(r.get("S_CAL",np.nan)) and np.isfinite(r.get("B",np.nan))]
    if both:
        bx, sy = zip(*both)
        fig, ax = plt.subplots(figsize=(7, 7))
        ax.scatter(bx, sy, c=[TICKER_COLORS.get(r["ticker"],"grey")
                               for r in rows if np.isfinite(r.get("S_CAL",np.nan)) and np.isfinite(r.get("B",np.nan))],
                   alpha=0.8, s=70)
        lim = max(abs(min(bx+sy)), abs(max(bx+sy))) * 1.1
        ax.plot([-lim, lim], [-lim, lim], "k--", lw=1, label="perfect tracking")
        ax.set_xlabel("B PnL (futures, ₹)"); ax.set_ylabel("S_CAL PnL (synthetic, ₹)")
        ax.set_title("Synthetic calendar vs futures calendar tracking\nShould lie on y=x diagonal")
        ax.legend(); ax.grid(alpha=0.2)
        fig.tight_layout(); fig.savefig(out/"g3_tracking.png", dpi=120); plt.close(fig)

    # 4. Spread reduction vs S_CAL PnL
    sub = [r for r in rows if np.isfinite(r.get("spread_entry",np.nan)) and
           np.isfinite(r.get("spread_exit",np.nan)) and np.isfinite(r.get("S_CAL",np.nan))]
    if sub:
        reductions = [r["spread_entry"] - r["spread_exit"] for r in sub]
        scals = [r["S_CAL"] for r in sub]
        fig, ax = plt.subplots(figsize=(9, 6))
        for tk in TICKERS:
            ts = [(reductions[i], scals[i]) for i, r in enumerate(sub) if r["ticker"] == tk]
            if ts:
                xx, yy = zip(*ts)
                ax.scatter(xx, yy, color=TICKER_COLORS.get(tk,"grey"), label=tk, alpha=0.8, s=60)
        ax.axhline(0, color="k", lw=0.8, ls="--"); ax.axvline(0, color="k", lw=0.8, ls="--")
        ax.set_xlabel("Spread reduction at exit: (F1−F2)_entry − (F1−F2)_exit  (₹)")
        ax.set_ylabel("S_CAL PnL per lot (₹)")
        ax.set_title("S_CAL PnL vs actual spread reduction\nPerfect synthetic: slope ≈ lot_size")
        ax.legend(); ax.grid(alpha=0.2)
        fig.tight_layout(); fig.savefig(out/"g4_spread_vs_pnl.png", dpi=120); plt.close(fig)

    print(f"\n  Saved 4 plots to {out}")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers",     nargs="+", default=TICKERS)
    ap.add_argument("--no-slippage", action="store_true")
    ap.add_argument("--htb-thresh",  type=float, default=HTB_THRESH)
    ap.add_argument("--out",         default=str(OUT_DIR))
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    use_slip = not args.no_slippage
    store = ArcticStore()
    rows: list[dict] = []

    print(f"\nb12 TRUE SYNTHETIC CALENDAR BACKTEST")
    print(f"  Tickers: {args.tickers}  |  HTB >= {args.htb_thresh}")
    print(f"  SLF2 = BUY call + SELL put at K_f2 → F2-EXPIRY options (tracks F2 price)")
    print(f"  SSF1 = BUY put  + SELL call at K_f1 → F1-EXPIRY options (tracks F1 price)")
    print(f"  S_CAL = SLF2 + SSF1  ≈  futures SHORT F1 + LONG F2")
    print(f"  Slippage: {'OFF' if args.no_slippage else f'OPT={SLIPPAGE_OPT*100:.1f}% FUT={SLIPPAGE_FUT*100:.1f}%'}")

    for ticker in args.tickers:
        print(f"\n  [{ticker}] loading …")
        try:
            br = store.read_borrow_rates(ticker, INTERVAL)
        except Exception as e:
            print(f"    ERROR: {e}"); continue
        if br is None or br.empty:
            print("    empty"); continue

        br = br.copy()
        expiries = store.list_option_expiries(ticker, INTERVAL)

        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index()
            peak_idx, peak_b12 = detect_peak(cyc)
            if peak_b12 < args.htb_thresh:
                continue

            entry_ts = cyc.index[peak_idx]
            exit_ts  = find_exit_ts(cyc)
            if exit_ts is None or exit_ts <= entry_ts:
                print(f"    cid={cid:>3}  SKIP exit <= entry"); continue

            f1_exp = find_f1_expiry(cyc, expiries)
            f2_exp = find_f2_expiry(f1_exp, expiries) if f1_exp else None

            if f1_exp is None:
                print(f"    cid={cid:>3}  SKIP no F1 expiry"); continue
            if f2_exp is None:
                print(f"    cid={cid:>3}  SKIP no F2 expiry"); continue

            try:
                f1_opts = store.read_options(ticker, INTERVAL, f1_exp)
            except Exception:
                f1_opts = pd.DataFrame()
            try:
                f2_opts = store.read_options(ticker, INTERVAL, f2_exp)
            except Exception:
                f2_opts = pd.DataFrame()

            r = run_cycle(ticker=ticker, cid=int(cid), cyc=cyc,
                          f1_opts=f1_opts, f2_opts=f2_opts,
                          entry_ts=entry_ts, exit_ts=exit_ts,
                          peak_b12=peak_b12, use_slip=use_slip)
            rows.append(r)

            scal_s = f"S_CAL={r['S_CAL']:+.0f}₹" if np.isfinite(r["S_CAL"]) else "S_CAL=n/a"
            b_s    = f"B={r['B']:+.0f}₹"          if np.isfinite(r["B"])     else "B=n/a"
            dte_s  = f"{r['entry_dte']:.0f}" if np.isfinite(r.get("entry_dte", np.nan)) else "?"
            skip_s = r["skip"] or "OK"
            print(f"    cid={cid:>3}  b12={peak_b12:.4f}  dte={dte_s:>3}  "
                  f"F1-F2={r.get('spread_entry', np.nan):>+.2f}  {scal_s}  {b_s}  [{skip_s[:40]}]")

    if not rows:
        print("\nNo qualifying cycles."); return

    rows.sort(key=lambda r: r["entry_ts"] if pd.notna(r.get("entry_ts")) else pd.Timestamp.max)
    print_per_cycle(rows)
    print_summary(rows)
    print_losses(rows)
    plot_all(rows, out)

    n_ok = sum(1 for r in rows if not r.get("skip"))
    print(f"\nDone. {n_ok}/{len(rows)} clean cycles.  Plots: {out}")


if __name__ == "__main__":
    main()
