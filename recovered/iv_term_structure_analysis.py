"""
iv_term_structure_analysis.py
==============================
Is there a pattern in the front-month (F1) vs back-month (F2) ATM IV term
structure during HTB cycles, across all tickers? This is the IV analogue of
the futures-spread research: instead of (F1_close - F2_close), we look at
(F1_ATM_IV - F2_ATM_IV) and ask whether it behaves like a calendar-spread
signal (peaks and collapses with the cycle) or is unrelated noise.

Sampled once per trading day (last bar at/before 14:45) per HTB cycle, for
all 6 tickers with HTB cycles (peak_b12 >= 0.05).
"""
from __future__ import annotations
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")

from data.storage.arctic_store import ArcticStore
import inflection_entry_bt as ieb

SMOOTH  = 11
HTB     = 0.05
TICKERS = ["SBICARD", "RVNL", "ASTRAL", "KPITTECH", "BDL", "IREDA"]
OUT_CSV = ROOT / "scripts" / "_iv_term_structure_cache.csv"


def atm_iv(opts_bar: pd.DataFrame, K: float | None) -> float:
    if opts_bar.empty or K is None:
        return np.nan
    ce = opts_bar[(opts_bar["strike"] == K) & (opts_bar["opt_type"] == "CE")]
    pe = opts_bar[(opts_bar["strike"] == K) & (opts_bar["opt_type"] == "PE")]
    ivs = []
    if not ce.empty and np.isfinite(ce["iv"].iloc[0]) and ce["iv"].iloc[0] > 0:
        ivs.append(float(ce["iv"].iloc[0]))
    if not pe.empty and np.isfinite(pe["iv"].iloc[0]) and pe["iv"].iloc[0] > 0:
        ivs.append(float(pe["iv"].iloc[0]))
    return float(np.mean(ivs)) if ivs else np.nan


def build_dataset() -> pd.DataFrame:
    store = ArcticStore()
    recs = []

    for ticker in TICKERS:
        br = store.read_borrow_rates(ticker, "15minute")
        if br is None or br.empty:
            continue
        br = br.copy()
        expiries = store.list_option_expiries(ticker, "15minute")
        print(f"[{ticker}]  cycles={br['cycle_id'].nunique()}")

        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index()
            b   = cyc["b12"].ffill().to_numpy(float)
            bsm = pd.Series(b).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
            peak_b12 = float(np.nanmax(bsm))
            if peak_b12 < HTB:
                continue

            f1_exp = ieb.find_f1_expiry(cyc, expiries)
            f2_exp = ieb.find_f2_expiry(f1_exp, expiries) if f1_exp else None
            if not f1_exp or not f2_exp:
                continue

            try: f1_opts = store.read_options(ticker, "15minute", f1_exp)
            except Exception: f1_opts = pd.DataFrame()
            try: f2_opts = store.read_options(ticker, "15minute", f2_exp)
            except Exception: f2_opts = pd.DataFrame()
            if f1_opts.empty or f2_opts.empty:
                continue

            day_keys = pd.Series(cyc.index.date, index=cyc.index)
            for d in pd.Series(cyc.index.date).unique():
                day_rows = cyc[day_keys == d]
                if day_rows.empty:
                    continue
                target = pd.Timestamp(f"{d} 14:45:00")
                if cyc.index.tz is not None:
                    target = target.tz_localize(cyc.index.tz)
                cand = day_rows[day_rows.index <= target]
                row  = cand.iloc[-1] if not cand.empty else day_rows.iloc[-1]
                ts   = row.name

                F1c = float(row.get("F1_close", np.nan)); F2c = float(row.get("F2_close", np.nan))
                dte = float(row.get("F1_dte", np.nan))
                if not (np.isfinite(F1c) and np.isfinite(F2c)):
                    continue

                f1_bar = ieb.get_opts_bar(f1_opts, ts)
                f2_bar = ieb.get_opts_bar(f2_opts, ts)
                if f1_bar.empty or f2_bar.empty:
                    continue

                K1, K2 = ieb.find_atm(f1_bar, F1c), ieb.find_atm(f2_bar, F2c)
                iv1, iv2 = atm_iv(f1_bar, K1), atm_iv(f2_bar, K2)
                if not (np.isfinite(iv1) and np.isfinite(iv2)):
                    continue

                recs.append(dict(ticker=ticker, cid=int(cid), ts=ts, dte=dte,
                                  peak_b12=peak_b12, spread=F1c - F2c,
                                  iv_f1=iv1, iv_f2=iv2, iv_diff=iv1 - iv2))

    df = pd.DataFrame(recs)
    df.to_csv(OUT_CSV, index=False)
    return df


def safe_corr(a, b):
    d = pd.DataFrame({"a": a, "b": b}).dropna()
    return d["a"].corr(d["b"]) if len(d) >= 4 else np.nan


def analyse(df: pd.DataFrame) -> None:
    n_cycles = df[["ticker", "cid"]].drop_duplicates().shape[0]
    print(f"\n{'='*110}\nFRONT (F1) vs BACK (F2) ATM IV — HTB cycles only — {len(df)} daily samples, {n_cycles} cycles\n{'='*110}")

    # 1) Is front-month IV systematically richer than back-month?
    pct_f1_gt_f2 = 100 * (df["iv_diff"] > 0).mean()
    print(f"\n1) F1 ATM IV > F2 ATM IV (front-month vol premium): {pct_f1_gt_f2:.1f}% of all daily samples")
    print(f"   mean iv_diff = {df['iv_diff'].mean():+.4f}   median = {df['iv_diff'].median():+.4f}")

    print(f"\n   Per ticker:")
    print(f"   {'ticker':<10}{'n':>6}{'%F1>F2':>9}{'mean_diff':>11}{'median_diff':>13}")
    for t in TICKERS:
        sub = df[df.ticker == t]
        if sub.empty: continue
        print(f"   {t:<10}{len(sub):>6}{100*(sub.iv_diff>0).mean():>8.1f}%{sub.iv_diff.mean():>+11.4f}{sub.iv_diff.median():>+13.4f}")

    # 2) Does iv_diff correlate with the futures spread / b12 within cycles?
    print(f"\n2) Within-cycle correlation: iv_diff vs futures spread (F1_close - F2_close)")
    print(f"   {'ticker':<10}{'cid':>5}{'n':>5}{'corr':>9}{'iv_diff@dte0-3':>16}{'iv_diff@dte20+':>16}")
    corrs = []
    near_vals, far_vals = [], []
    for (t, cid), g in df.groupby(["ticker", "cid"]):
        g = g.sort_values("ts")
        if len(g) < 5: continue
        c = safe_corr(g["iv_diff"], g["spread"])
        near = g.loc[g.dte <= 3, "iv_diff"].mean()
        far  = g.loc[g.dte >= 20, "iv_diff"].mean()
        if np.isfinite(c): corrs.append(c)
        if np.isfinite(near): near_vals.append(near)
        if np.isfinite(far):  far_vals.append(far)
        print(f"   {t:<10}{cid:>5}{len(g):>5}{c:>+9.3f}{near:>+16.4f}{far:>+16.4f}")

    print(f"\n   POOLED: mean corr(iv_diff, spread) = {np.nanmean(corrs):+.3f}   median = {np.nanmedian(corrs):+.3f}  (n={len(corrs)} cycles)")
    print(f"   mean iv_diff in last 3 DTE   = {np.nanmean(near_vals):+.4f}")
    print(f"   mean iv_diff at DTE >= 20    = {np.nanmean(far_vals):+.4f}")

    # 3) Does iv_diff itself rise into expiry (DTE bucket pattern), like the futures spread does?
    print(f"\n3) iv_diff by DTE bucket (pooled, all tickers, all HTB cycles)")
    bins = [(0,2),(3,5),(6,10),(11,20),(21,40),(41,200)]
    print(f"   {'DTE bucket':<12}{'n':>6}{'mean iv_diff':>14}{'median':>10}{'%F1>F2':>9}")
    for lo, hi in bins:
        sub = df[(df.dte >= lo) & (df.dte <= hi)]
        if sub.empty: continue
        print(f"   {f'{lo}-{hi}':<12}{len(sub):>6}{sub.iv_diff.mean():>+14.4f}{sub.iv_diff.median():>+10.4f}{100*(sub.iv_diff>0).mean():>8.1f}%")

    # 4) Correlation with b12 itself (does iv_diff track the borrow premium magnitude across cycles?)
    cyc_level = df.groupby(["ticker","cid"]).agg(peak_b12=("peak_b12","first"),
                                                  mean_iv_diff=("iv_diff","mean"),
                                                  max_iv_diff=("iv_diff","max")).reset_index()
    c_mean = safe_corr(cyc_level["peak_b12"], cyc_level["mean_iv_diff"])
    c_max  = safe_corr(cyc_level["peak_b12"], cyc_level["max_iv_diff"])
    print(f"\n4) Across-cycle: corr(peak_b12, mean iv_diff) = {c_mean:+.3f}   corr(peak_b12, max iv_diff) = {c_max:+.3f}   (n={len(cyc_level)} cycles)")


if __name__ == "__main__":
    if OUT_CSV.exists():
        print(f"Loading cached dataset from {OUT_CSV}")
        df = pd.read_csv(OUT_CSV, parse_dates=["ts"])
    else:
        df = build_dataset()
    analyse(df)
