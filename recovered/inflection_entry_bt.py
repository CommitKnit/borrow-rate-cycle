"""
inflection_entry_bt.py
======================
Entry signal: computed on the raw F1-F2 futures spread (NOT b12).
  smooth_spread = 11-bar centred rolling mean of (F1_close - F2_close)
  slope  = diff(smooth_spread)
  accel  = diff(slope)
First bar after the spread cycle peak where slope < 0 AND accel < 0 is the entry.

HTB cycle filter still uses b12 (peak_b12 >= 0.05) — this is unchanged.

Three strategies — RVNL and SBICARD, HTB cycles only:

  S1  — Futures Calendar      : SHORT F1 + LONG F2
  S2  — Synthetic Calendar    : SSF1 (F1-expiry opts) + SLF2 (F2-expiry opts)
  S3  — Asymmetric Options    : BUY call F2 (F2-expiry opts) + SELL put F1 (F1-expiry opts)

Exit: last 15-min bar at or before 12:00 PM on F1 expiry day.
"""
from __future__ import annotations
import sys, warnings
from pathlib import Path
from datetime import date
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data.storage.arctic_store import ArcticStore
from options.pricing.black76 import black76_price

warnings.filterwarnings("ignore")

# ── constants ─────────────────────────────────────────────────────────────────
TICKERS    = ["SBICARD", "RVNL"]
LOT_SIZES  = {"SBICARD": 800, "RVNL": 1525}
INTERVAL   = "15minute"
SMOOTH     = 11
HTB_THRESH = 0.05
SLIP_OPT   = 0.005
SLIP_FUT   = 0.001
RISK_FREE  = 0.065
OUT_DIR    = ROOT / "plots" / "inflection_entry_bt"

COLORS = {"SBICARD": "#e74c3c", "RVNL": "#3498db"}
S_COLORS = {"S1": "#2c3e50", "S2": "#8e44ad", "S3": "#e67e22"}


# ── helpers ───────────────────────────────────────────────────────────────────

def smooth_series(s: pd.Series, w: int = SMOOTH) -> np.ndarray:
    return s.rolling(w, center=True, min_periods=1).mean().to_numpy(float)


def htb_peak_b12(cyc: pd.DataFrame) -> float:
    """Peak of smoothed b12 — used only for the HTB cycle filter."""
    b  = cyc["b12"].ffill().to_numpy(float)
    sm = pd.Series(b).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
    return float(np.nanmax(sm))


def detect_peak(cyc: pd.DataFrame) -> tuple[int, float]:
    """Peak of the smoothed F1-F2 spread — used for signal timing."""
    spread = (cyc["F1_close"].ffill() - cyc["F2_close"].ffill()).to_numpy(float)
    sm     = pd.Series(spread).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
    idx    = int(np.nanargmax(sm))
    return idx, float(np.nanmax(sm))


def find_inflection_entry(cyc: pd.DataFrame, peak_idx: int) -> pd.Timestamp | None:
    """
    Compute slope and accel from the smoothed F1-F2 spread on the full cycle,
    then find the first bar at or after peak_idx where slope < 0 AND accel < 0.
    """
    raw_spread = cyc["F1_close"].ffill() - cyc["F2_close"].ffill()
    smoothed   = raw_spread.rolling(SMOOTH, center=True, min_periods=1).mean()
    slopes     = smoothed.diff().to_numpy(float)
    accels     = smoothed.diff().diff().to_numpy(float)

    for i in range(peak_idx, len(cyc)):
        s, a = slopes[i], accels[i]
        if np.isfinite(s) and np.isfinite(a) and s < 0 and a < 0:
            return cyc.index[i]
    return None


def get_opts_bar(o: pd.DataFrame, ts: pd.Timestamp,
                 window: pd.Timedelta = pd.Timedelta("45min")) -> pd.DataFrame:
    if o.empty:
        return pd.DataFrame()
    ts_c = ts
    if o.index.tz is not None and ts.tzinfo is None:
        ts_c = ts.tz_localize(o.index.tz)
    elif o.index.tz is not None and ts.tzinfo is not None:
        ts_c = ts.tz_convert(o.index.tz)
    lo, hi = ts_c - window, ts_c + window
    sub = o[(o.index >= lo) & (o.index <= hi)]
    if sub.empty:
        return pd.DataFrame()
    nearest = sub.index[np.argmin(np.abs((sub.index - ts_c).total_seconds()))]
    return sub[sub.index == nearest]


def px(bar: pd.DataFrame, K: float, otype: str) -> float:
    r = bar[(bar["strike"] == K) & (bar["opt_type"] == otype)]
    if r.empty: return np.nan
    v = float(r["close"].iloc[0])
    return max(v, 0.05) if v > 0 else np.nan


def px_fb(bar: pd.DataFrame, K: float, otype: str, F: float,
          dte: float, is_exit: bool = False) -> float | None:
    v = px(bar, K, otype)
    if np.isfinite(v): return v
    if is_exit or dte <= 0:
        return max(0.0, F - K) if otype == "CE" else max(0.0, K - F)
    r = bar[(bar["strike"] == K) & (bar["opt_type"] == otype)]
    if not r.empty:
        iv = float(r["iv"].iloc[0]) if "iv" in r.columns else np.nan
        if np.isfinite(iv) and iv > 0 and F > 0:
            try:
                return black76_price(F, K, max(dte / 365, 1e-6), iv, RISK_FREE, otype == "CE")
            except Exception:
                pass
    return None


def find_atm(bar: pd.DataFrame, F: float) -> float | None:
    if bar.empty: return None
    strikes = bar["strike"].unique()
    return float(strikes[np.argmin(np.abs(strikes - F))]) if len(strikes) else None


def slip(p: float, buy: bool, fut: bool = False) -> float:
    pct = SLIP_FUT if fut else SLIP_OPT
    return p * (1 + pct) if buy else p * (1 - pct)


def find_exit_ts(cyc: pd.DataFrame) -> pd.Timestamp | None:
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
    return min(cands, key=lambda e: abs((date.fromisoformat(e) - last_d).days)) if cands else None


def find_f2_expiry(f1_exp: str, expiries: list[str]) -> str | None:
    f1_dt = date.fromisoformat(f1_exp)
    later = [e for e in sorted(expiries) if date.fromisoformat(e) > f1_dt]
    return later[0] if later else None


# ── per-cycle simulation ──────────────────────────────────────────────────────

def run_cycle(ticker, cid, cyc, f1_opts, f2_opts,
              entry_ts, exit_ts, peak_b12, peak_spread, use_slip) -> dict:
    lot = LOT_SIZES[ticker]
    res = {
        "ticker": ticker, "cid": cid, "lot": lot,
        "entry_ts": entry_ts, "exit_ts": exit_ts,
        "peak_b12": peak_b12, "peak_spread": peak_spread, "skip": None,
        "S1": np.nan, "S2": np.nan, "S3": np.nan,
        "SLF2": np.nan, "SSF1": np.nan,
        "entry_dte": np.nan, "spread_entry": np.nan, "spread_exit": np.nan,
        "direction": "unknown", "K_f1": np.nan, "K_f2": np.nan,
        "call_f2_entry": np.nan, "put_f1_entry": np.nan,
    }

    ep = cyc[cyc.index <= entry_ts]
    ex = cyc[cyc.index <= exit_ts]
    if ep.empty or ex.empty:
        res["skip"] = "missing br rows"; return res

    er, xr = ep.iloc[-1], ex.iloc[-1]
    F1e = float(er.get("F1_close", np.nan));  F2e = float(er.get("F2_close", np.nan))
    F1x = float(xr.get("F1_close", np.nan));  F2x = float(xr.get("F2_close", np.nan))
    dte_e = float(er.get("F1_dte", np.nan))
    res.update({"F1_entry": F1e, "F2_entry": F2e, "F1_exit": F1x, "F2_exit": F2x,
                "entry_dte": dte_e})

    if all(np.isfinite(v) for v in [F1e, F2e, F1x, F2x]):
        res["spread_entry"] = F1e - F2e
        res["spread_exit"]  = F1x - F2x

    # direction
    try:
        sp_e = float(er.get("SPOT_close", np.nan)); sp_x = float(xr.get("SPOT_close", np.nan))
        if all(np.isfinite(v) and v > 0 for v in [F1e, F2e, sp_e, F1x, F2x, sp_x]):
            db1 = (F1x - sp_x) - (F1e - sp_e);  db2 = (F2x - sp_x) - (F2e - sp_e)
            denom = abs(-db1) + abs(db2)
            res["direction"] = "F1→F2" if denom > 1e-6 and (-db1) / denom > 0.5 else "F2→F1"
    except Exception:
        pass

    # ── S1: Futures Calendar SHORT F1 + LONG F2 ──────────────────────────────
    if all(np.isfinite(v) for v in [F1e, F2e, F1x, F2x]):
        pnl = (F1e - F1x) + (F2x - F2e)
        if use_slip:
            pnl -= slip(F1e, False, True) - F1e   # sell F1 (receive less)
            pnl -= F2e - slip(F2e, True,  True)   # buy  F2 (pay more)
            pnl -= F1x - slip(F1x, True,  True)   # buy back F1
            pnl -= slip(F2x, False, True) - F2x   # sell F2
        res["S1"] = round(pnl * lot, 2)

    # ── Options setup ─────────────────────────────────────────────────────────
    # F1-expiry options (for SSF1 leg and S3 put)
    f1_e_bar = get_opts_bar(f1_opts, entry_ts)
    f1_x_bar = get_opts_bar(f1_opts, exit_ts)
    # F2-expiry options (for SLF2 leg and S3 call)
    f2_e_bar = get_opts_bar(f2_opts, entry_ts)
    f2_x_bar = get_opts_bar(f2_opts, exit_ts)

    # ATM strikes
    K_f1 = find_atm(f1_e_bar, float(f1_e_bar["futures_ref_price"].iloc[0])
                    if not f1_e_bar.empty and "futures_ref_price" in f1_e_bar.columns
                    else F1e) if not f1_e_bar.empty else None

    K_f2 = find_atm(f2_e_bar, float(f2_e_bar["futures_ref_price"].iloc[0])
                    if not f2_e_bar.empty and "futures_ref_price" in f2_e_bar.columns
                    else F2e) if not f2_e_bar.empty else None

    res.update({"K_f1": K_f1 or np.nan, "K_f2": K_f2 or np.nan})

    f1_dte = dte_e if np.isfinite(dte_e) else 3.0
    f2_dte_e = (float(f2_e_bar["dte"].iloc[0]) if not f2_e_bar.empty and "dte" in f2_e_bar.columns
                else 45.0)
    f2_dte_x = (float(f2_x_bar["dte"].iloc[0]) if not f2_x_bar.empty and "dte" in f2_x_bar.columns
                else 30.0)
    F2_ref_e = (float(f2_e_bar["futures_ref_price"].iloc[0])
                if not f2_e_bar.empty and "futures_ref_price" in f2_e_bar.columns else F2e)
    F2_ref_x = (float(f2_x_bar["futures_ref_price"].iloc[0])
                if not f2_x_bar.empty and "futures_ref_price" in f2_x_bar.columns else F2x)
    F1_ref_x = (float(f1_x_bar["futures_ref_price"].iloc[0])
                if not f1_x_bar.empty and "futures_ref_price" in f1_x_bar.columns else F1x)

    # ── S2 leg: SSF1 (synthetic short F1) — F1-expiry options ────────────────
    ssf1_pnl = np.nan
    if K_f1 is not None and not f1_e_bar.empty and not f1_x_bar.empty:
        put_f1_e  = px_fb(f1_e_bar, K_f1, "PE", F1e, f1_dte)
        call_f1_e = px_fb(f1_e_bar, K_f1, "CE", F1e, f1_dte)
        if put_f1_e is not None and call_f1_e is not None:
            put_f1_x  = px_fb(f1_x_bar, K_f1, "PE", F1_ref_x, 0.0, is_exit=True) or max(0., K_f1 - F1_ref_x)
            call_f1_x = px_fb(f1_x_bar, K_f1, "CE", F1_ref_x, 0.0, is_exit=True) or max(0., F1_ref_x - K_f1)
            if use_slip:
                p_e = slip(put_f1_e, True);   c_e = slip(call_f1_e, False)
                p_x = slip(put_f1_x, False);  c_x = slip(call_f1_x, True)
            else:
                p_e, c_e = put_f1_e, call_f1_e
                p_x, c_x = put_f1_x, call_f1_x
            ssf1_pnl = (p_x - p_e) - (c_x - c_e)
            res["SSF1"] = round(ssf1_pnl * lot, 2)
            res["put_f1_entry"] = put_f1_e

    # ── S2 leg: SLF2 (synthetic long F2) — F2-expiry options ─────────────────
    slf2_pnl = np.nan
    if K_f2 is not None and not f2_e_bar.empty and not f2_x_bar.empty:
        call_f2_e = px_fb(f2_e_bar, K_f2, "CE", F2_ref_e, f2_dte_e)
        put_f2_e  = px_fb(f2_e_bar, K_f2, "PE", F2_ref_e, f2_dte_e)
        if call_f2_e is not None and put_f2_e is not None:
            call_f2_x = px_fb(f2_x_bar, K_f2, "CE", F2_ref_x, f2_dte_x)
            put_f2_x  = px_fb(f2_x_bar, K_f2, "PE", F2_ref_x, f2_dte_x)
            if call_f2_x is not None and put_f2_x is not None:
                if use_slip:
                    ce_e = slip(call_f2_e, True);   pe_e = slip(put_f2_e,  False)
                    ce_x = slip(call_f2_x, False);  pe_x = slip(put_f2_x,  True)
                else:
                    ce_e, pe_e = call_f2_e, put_f2_e
                    ce_x, pe_x = call_f2_x, put_f2_x
                slf2_pnl = (ce_x - ce_e) - (pe_x - pe_e)
                res["SLF2"] = round(slf2_pnl * lot, 2)
                res["call_f2_entry"] = call_f2_e

    # S2 combined
    if np.isfinite(ssf1_pnl) and np.isfinite(slf2_pnl):
        res["S2"] = round((ssf1_pnl + slf2_pnl) * lot, 2)

    # ── S3: BUY call F2 (F2-expiry) + SELL put F1 (F1-expiry) ────────────────
    if K_f2 is not None and K_f1 is not None and \
       not f2_e_bar.empty and not f2_x_bar.empty and \
       not f1_e_bar.empty and not f1_x_bar.empty:

        call_f2_e = px_fb(f2_e_bar, K_f2, "CE", F2_ref_e, f2_dte_e)
        call_f2_x = px_fb(f2_x_bar, K_f2, "CE", F2_ref_x, f2_dte_x)
        put_f1_e  = px_fb(f1_e_bar, K_f1, "PE", F1e, f1_dte)
        put_f1_x  = px_fb(f1_x_bar, K_f1, "PE", F1_ref_x, 0.0, is_exit=True) or max(0., K_f1 - F1_ref_x)

        if all(v is not None for v in [call_f2_e, call_f2_x, put_f1_e, put_f1_x]):
            if use_slip:
                cf2_e = slip(call_f2_e, True);   cf2_x = slip(call_f2_x, False)
                pf1_e = slip(put_f1_e,  False);  pf1_x = slip(put_f1_x,  True)
            else:
                cf2_e, cf2_x = call_f2_e, call_f2_x
                pf1_e, pf1_x = put_f1_e,  put_f1_x
            # BUY call F2: gain = cf2_x - cf2_e
            # SELL put F1: gain = pf1_e - pf1_x
            s3_pnl = (cf2_x - cf2_e) + (pf1_e - pf1_x)
            res["S3"] = round(s3_pnl * lot, 2)

    return res


# ── statistics ────────────────────────────────────────────────────────────────

def wr(a): a=a[np.isfinite(a)]; return float((a>0).mean()*100) if len(a) else np.nan
def sh(a): a=a[np.isfinite(a)]; return float(a.mean()/a.std()) if len(a)>=2 and a.std()>0 else np.nan
def mdd(a):
    a=a[np.isfinite(a)]
    if not len(a): return np.nan
    c=np.cumsum(a); return float(np.max(np.maximum.accumulate(c)-c))


# ── console output ────────────────────────────────────────────────────────────

def print_results(rows):
    print("\n" + "="*140)
    print("  INFLECTION-ENTRY BACKTEST  —  SBICARD & RVNL  —  HTB cycles only")
    print("  Signal: slope & accel computed on 11-bar smoothed F1-F2 spread (NOT b12)")
    print("  Entry: first bar after spread peak where spread_slope < 0 AND spread_accel < 0")
    print("  S1=futures SHORT F1+LONG F2  |  S2=synthetic calendar (F1+F2 opts)  |  S3=BUY call F2 + SELL put F1")
    print("="*140)
    hdr = (f"  {'ticker':<9}{'cid':>4}  {'entry_ts':<19}  {'dte':>4}  {'spread_e':>9}  "
           f"{'spread_x':>9}  {'dir':>8}  {'S1(₹)':>10}  {'S2(₹)':>10}  {'S3(₹)':>10}  note")
    print(hdr); print("  "+"-"*136)
    for r in rows:
        ts  = str(r["entry_ts"])[:19] if pd.notna(r.get("entry_ts")) else "n/a"
        dte = f"{r['entry_dte']:.0f}" if np.isfinite(r.get("entry_dte",np.nan)) else "?"
        def fs(k): return f"{r[k]:>+10.0f}" if np.isfinite(r.get(k,np.nan)) else "       NaN"
        se = f"{r['spread_entry']:>+9.2f}" if np.isfinite(r.get("spread_entry",np.nan)) else "      n/a"
        sx = f"{r['spread_exit']:>+9.2f}"  if np.isfinite(r.get("spread_exit",np.nan))  else "      n/a"
        note = f"*{r['skip'][:30]}" if r.get("skip") else ""
        print(f"  {r['ticker']:<9}{r['cid']:>4}  {ts}  {dte:>4}  {se}  {sx}  "
              f"{r['direction']:>8}  {fs('S1')}  {fs('S2')}  {fs('S3')}  {note}")
    print("  "+"-"*136)

    for ticker in TICKERS:
        sub = [r for r in rows if r["ticker"]==ticker]
        if not sub: continue
        print(f"\n  ── {ticker} ({'n='+str(len(sub))}) ──")
        for name, key in [("S1 (Futures calendar)","S1"),
                          ("S2 (Synthetic calendar)","S2"),
                          ("S3 (Call F2 + Put F1)","S3")]:
            arr = np.array([r[key] for r in sub], float)
            v = arr[np.isfinite(arr)]
            if not len(v): print(f"    {name:<28}  n=0"); continue
            print(f"    {name:<28}  n={len(v):>2}  win={wr(arr):>5.1f}%  "
                  f"mean={np.nanmean(arr):>+9.0f}₹  std={np.nanstd(arr):>+8.0f}  "
                  f"Sharpe={sh(arr):>+5.2f}  cum={np.nansum(arr):>+10.0f}₹  "
                  f"maxDD={-mdd(arr):>+9.0f}₹  "
                  f"best={np.nanmax(arr):>+9.0f}₹  worst={np.nanmin(arr):>+9.0f}₹")

    print(f"\n  ── POOLED (both tickers) ──")
    for name, key in [("S1","S1"),("S2","S2"),("S3","S3")]:
        arr = np.array([r[key] for r in rows], float)
        v = arr[np.isfinite(arr)]
        if not len(v): continue
        print(f"    {name:<6}  n={len(v):>2}  win={wr(arr):>5.1f}%  "
              f"mean={np.nanmean(arr):>+9.0f}₹  Sharpe={sh(arr):>+5.2f}  "
              f"cum={np.nansum(arr):>+10.0f}₹  maxDD={-mdd(arr):>+9.0f}₹")

    # direction split
    print(f"\n  ── Direction × Strategy ──")
    for d in ["F2→F1","F1→F2"]:
        grp = [r for r in rows if r["direction"]==d]
        if not grp: continue
        print(f"  {d} (n={len(grp)})")
        for name, key in [("S1","S1"),("S2","S2"),("S3","S3")]:
            arr = np.array([r[key] for r in grp], float)
            v = arr[np.isfinite(arr)]
            if not len(v): continue
            print(f"    {name}  win={wr(arr):>5.1f}%  mean={np.nanmean(arr):>+9.0f}₹")

    # entry timing analysis
    print(f"\n  ── Entry DTE distribution ──")
    for ticker in TICKERS:
        sub = [r for r in rows if r["ticker"]==ticker and np.isfinite(r.get("entry_dte",np.nan))]
        if not sub: continue
        dtes = np.array([r["entry_dte"] for r in sub])
        print(f"  {ticker}: median={np.median(dtes):.0f}  mean={np.mean(dtes):.1f}  "
              f"min={np.min(dtes):.0f}  max={np.max(dtes):.0f}  "
              f"expiry-week(≤5)={int((dtes<=5).sum())}/{len(dtes)}")


# ── plots ─────────────────────────────────────────────────────────────────────

def plot_all(rows, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    valid = sorted([r for r in rows if pd.notna(r.get("entry_ts"))], key=lambda r: r["entry_ts"])

    # ── Figure 1: per-cycle grouped bars ─────────────────────────────────────
    fig, axes = plt.subplots(2, 1, figsize=(max(16, len(valid)*0.8), 12), sharex=False)
    for ax, ticker in zip(axes, TICKERS):
        sub = [r for r in valid if r["ticker"]==ticker]
        if not sub: continue
        x = np.arange(len(sub)); w = 0.25
        labels = [f"c{r['cid']}\nDTE={r['entry_dte']:.0f}" if np.isfinite(r.get("entry_dte",np.nan))
                  else f"c{r['cid']}" for r in sub]
        for j, (key, name, col) in enumerate([("S1","Futures cal",S_COLORS["S1"]),
                                               ("S2","Synth cal",S_COLORS["S2"]),
                                               ("S3","Call F2+Put F1",S_COLORS["S3"])]):
            vals = [r[key] if np.isfinite(r.get(key,np.nan)) else 0 for r in sub]
            bars = ax.bar(x + (j-1)*w, vals, w, label=name, color=col, alpha=0.8)
            for bar, v in zip(bars, vals):
                if abs(v) > 2000:
                    ax.text(bar.get_x()+bar.get_width()/2, v+(300 if v>=0 else -600),
                            f"{v:+.0f}", ha="center", fontsize=7)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=8, rotation=30, ha="right")
        ax.set_title(f"{ticker} — per-cycle PnL (inflection entry)", fontsize=11)
        ax.set_ylabel("PnL ₹/lot"); ax.legend(fontsize=9); ax.grid(alpha=0.2, axis="y")
    fig.suptitle("Inflection-entry strategy PnL per cycle", fontsize=12)
    fig.tight_layout(); fig.savefig(out/"g1_per_cycle.png", dpi=120); plt.close(fig)
    print(f"  Saved g1_per_cycle.png")

    # ── Figure 2: cumulative equity curves per ticker ─────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    for ax, ticker in zip(axes, TICKERS):
        sub = sorted([r for r in rows if r["ticker"]==ticker], key=lambda r: r["entry_ts"])
        if not sub: continue
        for key, name, col, ls in [("S1","Futures cal",S_COLORS["S1"],"-"),
                                    ("S2","Synth cal",S_COLORS["S2"],"--"),
                                    ("S3","Call F2+Put F1",S_COLORS["S3"],"-.")]:
            arr = np.array([r[key] for r in sub], float)
            cum = np.nancumsum(np.where(np.isfinite(arr), arr, 0))
            ax.plot(range(len(cum)), cum, color=col, ls=ls, lw=2.2, label=name, marker="o", ms=5)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_title(f"{ticker} — cumulative PnL (inflection entry)")
        ax.set_xlabel("cycle #"); ax.set_ylabel("cumulative PnL ₹")
        ax.legend(fontsize=9); ax.grid(alpha=0.2)
    fig.suptitle("Equity curves — inflection-point entry signal", fontsize=12)
    fig.tight_layout(); fig.savefig(out/"g2_cumulative.png", dpi=120); plt.close(fig)
    print(f"  Saved g2_cumulative.png")

    # ── Figure 3: spread reduction vs PnL scatter ─────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), sharey=False)
    for ax, key, name in zip(axes, ["S1","S2","S3"],
                              ["S1: Futures Calendar","S2: Synthetic Calendar","S3: Call F2 + Put F1"]):
        sub = [r for r in rows if np.isfinite(r.get("spread_entry",np.nan)) and
               np.isfinite(r.get("spread_exit",np.nan)) and np.isfinite(r.get(key,np.nan))]
        if not sub: continue
        for ticker in TICKERS:
            ts = [r for r in sub if r["ticker"]==ticker]
            if not ts: continue
            ax.scatter([r["spread_entry"]-r["spread_exit"] for r in ts],
                       [r[key] for r in ts], color=COLORS[ticker], label=ticker, alpha=0.8, s=70)
        ax.axhline(0, color="k", lw=0.8, ls="--"); ax.axvline(0, color="gray", lw=0.6, ls=":")
        ax.set_xlabel("Spread reduction ₹ (entry − exit)"); ax.set_ylabel("PnL ₹/lot")
        ax.set_title(name); ax.legend(fontsize=9); ax.grid(alpha=0.2)
        if len(sub) >= 4:
            from numpy.polynomial import polynomial as P
            xs = [r["spread_entry"]-r["spread_exit"] for r in sub]
            ys = [r[key] for r in sub]
            c = P.polyfit(xs, ys, 1)
            xl = np.linspace(min(xs), max(xs), 50)
            ax.plot(xl, P.polyval(xl, c), "k--", lw=1, alpha=0.5)
    fig.suptitle("PnL vs spread reduction — inflection entry", fontsize=12)
    fig.tight_layout(); fig.savefig(out/"g3_spread_vs_pnl.png", dpi=120); plt.close(fig)
    print(f"  Saved g3_spread_vs_pnl.png")

    # ── Figure 4: DTE at entry distribution + win rate by DTE bucket ──────────
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    for row_ax, ticker in zip([[axes[0][0], axes[0][1]], [axes[1][0], axes[1][1]]], TICKERS):
        sub = [r for r in rows if r["ticker"]==ticker and np.isfinite(r.get("entry_dte",np.nan))]
        if not sub: continue
        dtes = np.array([r["entry_dte"] for r in sub])
        # histogram
        ax = row_ax[0]
        ax.hist(dtes, bins=range(0, int(dtes.max())+2), color=COLORS[ticker], alpha=0.75, edgecolor="white")
        ax.axvline(5, color="orange", lw=1.5, ls="--", label="DTE=5 (expiry week)")
        ax.set_title(f"{ticker} — DTE at inflection entry"); ax.set_xlabel("DTE"); ax.set_ylabel("count")
        ax.legend(fontsize=8); ax.grid(alpha=0.2, axis="y")
        # win rate by DTE bucket
        ax = row_ax[1]
        buckets = [(0,1,"0-1"),(2,5,"2-5"),(6,10,"6-10"),(11,30,"11-30"),(31,100,"31+")]
        for key, col, ls in [("S1",S_COLORS["S1"],"-"),("S2",S_COLORS["S2"],"--"),("S3",S_COLORS["S3"],"-.")]:
            wrs = []
            ns  = []
            for lo, hi, lab in buckets:
                grp = [r for r in sub if lo <= r["entry_dte"] <= hi and np.isfinite(r.get(key,np.nan))]
                arr = np.array([r[key] for r in grp], float)
                wrs.append(wr(arr) if len(arr) else np.nan)
                ns.append(len(arr))
            ax.plot([b[2] for b in buckets], wrs, marker="o", lw=2, color=col, ls=ls, label=key)
        ax.axhline(50, color="gray", lw=0.8, ls=":")
        ax.set_title(f"{ticker} — win% by entry DTE bucket")
        ax.set_xlabel("DTE at entry"); ax.set_ylabel("Win rate %")
        ax.legend(fontsize=9); ax.grid(alpha=0.2); ax.set_ylim(0, 105)
    fig.suptitle("Entry timing analysis — inflection-point signal", fontsize=12)
    fig.tight_layout(); fig.savefig(out/"g4_dte_analysis.png", dpi=120); plt.close(fig)
    print(f"  Saved g4_dte_analysis.png")

    # ── Figure 5: b12 signal visualisation for 4 representative cycles ────────
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    picked = []
    store = ArcticStore()
    for ticker in TICKERS:
        sub = [r for r in valid if r["ticker"]==ticker and not r.get("skip") and np.isfinite(r.get("S1",np.nan))]
        if sub:
            picked += [(ticker, sub[len(sub)//2], store)]
            if len(sub) > 1:
                picked += [(ticker, max(sub, key=lambda r: abs(r.get("S1",0))), store)]

    for ax, (ticker, r, st) in zip(axes.flat, picked[:4]):
        br = st.read_borrow_rates(ticker, INTERVAL)
        cyc = br[br["cycle_id"]==r["cid"]].sort_index()
        raw_sp = (cyc["F1_close"].ffill() - cyc["F2_close"].ffill()).values
        sm_sp  = pd.Series(raw_sp).rolling(SMOOTH, center=True, min_periods=1).mean().values
        ts = cyc.index
        # tz-normalise for plotting
        ts_plt = [t.tz_localize(None) if t.tzinfo is not None else t for t in ts]
        ax.plot(ts_plt, raw_sp, color="lightgrey", lw=1, label="raw F1-F2 spread")
        ax.plot(ts_plt, sm_sp,  color=COLORS[ticker], lw=2, label="smoothed spread")
        # mark entry
        e_ts = r["entry_ts"]
        if e_ts.tzinfo is not None: e_ts = e_ts.tz_localize(None)
        ax.axvline(e_ts, color="green", lw=1.5, ls="--", label=f"inflection entry DTE={r['entry_dte']:.0f}")
        # mark exit
        x_ts = r["exit_ts"]
        if x_ts.tzinfo is not None: x_ts = x_ts.tz_localize(None)
        ax.axvline(x_ts, color="red", lw=1.5, ls=":", label="exit 12PM expiry")
        ax.set_title(f"{ticker} cid={r['cid']}  S1={r['S1']:+.0f}₹  S3={r.get('S3',np.nan):+.0f}₹" if np.isfinite(r.get('S3',np.nan)) else f"{ticker} cid={r['cid']}  S1={r['S1']:+.0f}₹")
        ax.set_ylabel("F1-F2 spread (₹)"); ax.legend(fontsize=7); ax.grid(alpha=0.2)
        ax.tick_params(axis='x', rotation=30)
    fig.suptitle("F1-F2 spread — inflection-entry timing on representative cycles", fontsize=12)
    fig.tight_layout(); fig.savefig(out/"g5_signal_viz.png", dpi=120); plt.close(fig)
    print(f"  Saved g5_signal_viz.png")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-slippage", action="store_true")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()
    use_slip = not args.no_slippage
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    store = ArcticStore()
    rows: list[dict] = []

    print(f"\nINFLECTION-ENTRY BACKTEST")
    print(f"  Signal: spread slope & accel from 11-bar smoothed F1-F2 spread (NOT b12)")
    print(f"  Slippage: {'OFF' if args.no_slippage else f'OPT={SLIP_OPT*100:.1f}% FUT={SLIP_FUT*100:.1f}%'}")

    for ticker in TICKERS:
        print(f"\n[{ticker}]")
        br = store.read_borrow_rates(ticker, INTERVAL)
        if br is None or br.empty: continue
        br = br.copy()
        expiries = store.list_option_expiries(ticker, INTERVAL)

        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index()

            # HTB filter: still uses b12
            peak_b12 = htb_peak_b12(cyc)
            if peak_b12 < HTB_THRESH: continue

            # Peak and entry signal: uses F1-F2 spread
            peak_idx, peak_spread = detect_peak(cyc)

            entry_ts = find_inflection_entry(cyc, peak_idx)
            if entry_ts is None:
                print(f"  cid={cid:>3}  SKIP: no inflection entry found"); continue

            exit_ts = find_exit_ts(cyc)
            if exit_ts is None or exit_ts <= entry_ts:
                print(f"  cid={cid:>3}  SKIP: exit <= entry"); continue

            f1_exp = find_f1_expiry(cyc, expiries)
            f2_exp = find_f2_expiry(f1_exp, expiries) if f1_exp else None
            if not f1_exp or not f2_exp:
                print(f"  cid={cid:>3}  SKIP: missing expiry"); continue

            try: f1_opts = store.read_options(ticker, INTERVAL, f1_exp)
            except Exception: f1_opts = pd.DataFrame()
            try: f2_opts = store.read_options(ticker, INTERVAL, f2_exp)
            except Exception: f2_opts = pd.DataFrame()

            r = run_cycle(ticker, int(cid), cyc, f1_opts, f2_opts,
                          entry_ts, exit_ts, peak_b12, peak_spread, use_slip)
            rows.append(r)

            s1s = f"S1={r['S1']:+.0f}₹" if np.isfinite(r["S1"]) else "S1=n/a"
            s2s = f"S2={r['S2']:+.0f}₹" if np.isfinite(r["S2"]) else "S2=n/a"
            s3s = f"S3={r['S3']:+.0f}₹" if np.isfinite(r["S3"]) else "S3=n/a"
            dte_s = f"{r.get('entry_dte',np.nan):.0f}" if np.isfinite(r.get("entry_dte",np.nan)) else "?"
            se_s  = f"{r.get('spread_entry',np.nan):+.2f}" if np.isfinite(r.get("spread_entry",np.nan)) else "?"
            print(f"  cid={cid:>3}  b12={peak_b12:.4f}  spread_peak={peak_spread:+.2f}  "
                  f"dte={dte_s:>3}  spread_entry={se_s:>7}  {s1s}  {s2s}  {s3s}  [{r.get('skip') or 'OK'}]")

    if not rows:
        print("\nNo qualifying cycles."); return

    rows.sort(key=lambda r: r["entry_ts"] if pd.notna(r.get("entry_ts")) else pd.Timestamp.max)
    print_results(rows)

    print(f"\nGenerating figures …")
    plot_all(rows, out)

    n_ok = sum(1 for r in rows if not r.get("skip"))
    print(f"\nDone. {n_ok}/{len(rows)} clean cycles.  Plots: {out}")


if __name__ == "__main__":
    main()
