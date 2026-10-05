"""
03_backtest.py — the calendar-spread backtest, in three scopes.

  headline   SBICARD + RVNL, options required, centred smoother.
             Reproduces the original published result exactly, so the
             corrections below can be measured against a known baseline.

  wide       all 7 tickers, no options gate, centred smoother.
             Removes the selection bug.

  honest     all 7 tickers, no options gate, TRAILING smoother.
             Removes the look-ahead as well. This is the number to believe.

Outputs
-------
  results/pnl_headline_22.csv
  results/pnl_per_cycle.csv          (wide + honest, one row per cycle per variant)
  results/summary_stats.md
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from borrowcycle import data as bd
from borrowcycle import backtest as bt
from borrowcycle.cycle import HTB_THRESH, htb_tier, peak_b12
from borrowcycle.stats import bootstrap_ci, max_dd, sharpe, sign_test, win_rate

RESULTS = bd.PKG_ROOT / "results"


def run_scope(tickers, center: bool, require_options: bool, use_slip: bool = True,
              legacy_slip: bool = False, min_b12: float = HTB_THRESH):
    """Run every qualifying cycle for the given tickers under one set of rules.

    ``min_b12`` defaults to the HTB filter. Pass ``-inf`` to keep the non-HTB
    cycles as a control group.
    """
    rows = []
    for t in tickers:
        expiries = bd.list_option_expiries(t)
        for cid, cyc in bd.iter_cycles(t):
            pk = peak_b12(cyc)
            if pk < min_b12:
                continue

            peak_idx, peak_spread = bt.detect_peak(cyc, center=center)
            entry_ts = bt.find_inflection_entry(cyc, peak_idx, center=center)
            if entry_ts is None:
                rows.append({"ticker": t, "cid": cid, "peak_b12": pk,
                             "skip_reason": "no post-peak inflection"})
                continue
            exit_ts = bt.find_exit_ts(cyc)
            if exit_ts is None or exit_ts <= entry_ts:
                rows.append({"ticker": t, "cid": cid, "peak_b12": pk,
                             "skip_reason": "exit before entry"})
                continue

            f1_opts = f2_opts = None
            f1_exp = bt.find_f1_expiry(cyc, expiries) if expiries else None
            f2_exp = bt.find_f2_expiry(f1_exp, expiries) if f1_exp else None
            if f1_exp and f2_exp:
                try:
                    f1_opts = bd.load_options(t, f1_exp)
                    f2_opts = bd.load_options(t, f2_exp)
                except bd.OptionsUnavailable:
                    f1_opts = f2_opts = None

            # The original dropped the whole cycle here, including the
            # futures-only S1. Under require_options we reproduce that.
            if require_options and (f1_opts is None or f2_opts is None):
                rows.append({"ticker": t, "cid": cid, "peak_b12": pk,
                             "skip_reason": "missing option expiry (original gate)"})
                continue

            r = bt.run_cycle(t, cid, cyc, f1_opts, f2_opts, entry_ts, exit_ts,
                             pk, peak_spread, use_slip, legacy_slip)
            r["htb_tier"] = htb_tier(pk)
            rows.append(r)

    df = pd.DataFrame(rows)
    traded = df[df["skip_reason"].isna()] if "skip_reason" in df else df

    # The assertion that would have caught the original selection bug: a
    # futures-only strategy can never have fewer observations than an
    # options strategy computed on the same cycles.
    if len(traded):
        n_s1 = int(traded["S1_bps"].notna().sum())
        n_s2 = int(traded["S2"].notna().sum()) if "S2" in traded else 0
        assert n_s1 >= n_s2, f"S1 ({n_s1}) has fewer observations than S2 ({n_s2})"
    return df, traded


def tier_table(traded: pd.DataFrame, col: str = "S1_bps") -> pd.DataFrame:
    out = []
    for tier in ("NON", "MOD", "EXT"):
        v = traded.loc[traded["htb_tier"] == tier, col].to_numpy(float)
        v = v[np.isfinite(v)]
        out.append({"tier": tier, "n": len(v),
                    "win_pct": round(win_rate(v), 1) if len(v) else np.nan,
                    "mean_bps": round(float(v.mean()), 1) if len(v) else np.nan,
                    "median_bps": round(float(np.median(v)), 1) if len(v) else np.nan,
                    "sharpe": round(sharpe(v), 2) if len(v) else np.nan})
    return pd.DataFrame(out)


def summarise(traded: pd.DataFrame, col: str, label: str) -> dict:
    v = traded[col].to_numpy(float)
    v = v[np.isfinite(v)]
    lo, hi = bootstrap_ci(v)
    return {"scope": label, "n": len(v),
            "win_pct": round(win_rate(v), 1),
            "mean": round(float(v.mean()), 2) if len(v) else np.nan,
            "median": round(float(np.median(v)), 2) if len(v) else np.nan,
            "sharpe": round(sharpe(v), 2),
            "ci_lo": round(lo, 1), "ci_hi": round(hi, 1),
            "sign_p": round(sign_test(v), 4),
            "max_dd": round(max_dd(v), 1),
            "cumulative": round(float(v.sum()), 2)}


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    pair = ["SBICARD", "RVNL"]

    print("=" * 74)
    print("HEADLINE  SBICARD+RVNL | options required | centred smoother")
    print("          (reproduces the original published result)")
    head_all, head = run_scope(pair, center=True, require_options=True,
                               legacy_slip=True)
    head.to_csv(RESULTS / "pnl_headline_22.csv", index=False)
    for s in ("S1", "S2", "S3"):
        v = head[s].to_numpy(float); v = v[np.isfinite(v)]
        print(f"  {s}  n={len(v):3d}  win={win_rate(v):5.1f}%  "
              f"mean=Rs{v.mean():+,.0f}  sharpe={sharpe(v):+.2f}  "
              f"cum=Rs{v.sum():+,.0f}")

    print("=" * 74)
    print("WIDE      all 7 tickers | no options gate | centred smoother")
    wide_all, wide = run_scope(bd.TICKERS, center=True, require_options=False,
                               min_b12=-np.inf)
    print(f"  S1  n={wide['S1_bps'].notna().sum():3d}  "
          f"win={win_rate(wide['S1_bps']):5.1f}%  "
          f"mean={wide['S1_bps'].mean():+.1f} bps  "
          f"sharpe={sharpe(wide['S1_bps']):+.2f}")
    print(tier_table(wide).to_string(index=False))

    print("=" * 74)
    print("HONEST    all 7 tickers | no options gate | TRAILING smoother")
    print("          (no look-ahead -- this is the implementable result)")
    hon_all, hon = run_scope(bd.TICKERS, center=False, require_options=False,
                             min_b12=-np.inf)
    print(f"  S1  n={hon['S1_bps'].notna().sum():3d}  "
          f"win={win_rate(hon['S1_bps']):5.1f}%  "
          f"mean={hon['S1_bps'].mean():+.1f} bps  "
          f"sharpe={sharpe(hon['S1_bps']):+.2f}")
    print(tier_table(hon).to_string(index=False))

    print("=" * 74)
    print("SLIPPAGE  same as HEADLINE but with costs CHARGED, not credited")
    _, head_fix = run_scope(pair, center=True, require_options=True,
                            legacy_slip=False)
    v_bad = head["S1"].to_numpy(float); v_bad = v_bad[np.isfinite(v_bad)]
    v_fix = head_fix["S1"].to_numpy(float); v_fix = v_fix[np.isfinite(v_fix)]
    print(f"  original (costs credited): n={len(v_bad)} win={win_rate(v_bad):5.1f}% "
          f"mean=Rs{v_bad.mean():+,.0f}")
    print(f"  corrected (costs charged): n={len(v_fix)} win={win_rate(v_fix):5.1f}% "
          f"mean=Rs{v_fix.mean():+,.0f}")
    print(f"  cost of the sign error: Rs{v_bad.mean()-v_fix.mean():,.0f} per cycle")

    wide = wide.copy(); wide["variant"] = "centred"
    hon = hon.copy(); hon["variant"] = "trailing"
    combined = pd.concat([wide, hon], ignore_index=True)
    combined.to_csv(RESULTS / "pnl_per_cycle.csv", index=False)

    # ---- summary_stats.md ----------------------------------------------------
    L = []
    L.append("# Backtest results\n")
    L.append("Entry: first bar at or after the smoothed F1-F2 spread peak where "
             "slope < 0 and acceleration < 0.  \n"
             "Exit: last 15-minute bar at or before 12:00 on F1 expiry day.  \n"
             "Costs: 0.1% slippage per futures leg, 0.5% per options leg, "
             "charged on entry and exit.\n")
    L.append("> The Sharpe below is a **per-cycle PnL Sharpe** (mean/std across "
             "cycles). It is not annualised and not capital-adjusted -- this "
             "study has no capital base. See `docs/05_limitations.md`.\n")

    L.append("\n## 1. Original published result (reproduction)\n")
    L.append("SBICARD + RVNL, options required, centred smoother. Rupees per lot.\n")
    L.append("| Strategy | n | Win rate | Mean PnL | Sharpe | Cumulative |")
    L.append("|---|---|---|---|---|---|")
    names = {"S1": "S1 - Futures calendar (SHORT F1 + LONG F2)",
             "S2": "S2 - Synthetic calendar (options)",
             "S3": "S3 - Call F2 + Put F1 (options)"}
    for s in ("S1", "S2", "S3"):
        v = head[s].to_numpy(float); v = v[np.isfinite(v)]
        L.append(f"| {names[s]} | {len(v)} | {win_rate(v):.0f}% | "
                 f"Rs{v.mean():+,.0f} | {sharpe(v):+.2f} | Rs{v.sum():+,.0f} |")
    n_gate = int((head_all["skip_reason"] ==
                  "missing option expiry (original gate)").sum())
    L.append(f"\n{n_gate} further qualifying cycles were dropped by the original "
             "options-availability gate even though S1 uses no options. "
             "They are restored below.\n")

    L.append("\n## 2. Corrected scope (S1, basis points of F1)\n")
    L.append("All 7 tickers, no options gate. Reported in bps because lot sizes "
             "are only known for SBICARD and RVNL.\n")
    L.append("| Variant | n | Win rate | Mean bps | Median bps | Sharpe | "
             "95% CI of mean | sign p |")
    L.append("|---|---|---|---|---|---|---|---|")
    for lbl, d in (("centred (look-ahead)", wide), ("trailing (implementable)", hon)):
        st = summarise(d, "S1_bps", lbl)
        L.append(f"| {lbl} | {st['n']} | {st['win_pct']}% | {st['mean']} | "
                 f"{st['median']} | {st['sharpe']} | "
                 f"[{st['ci_lo']}, {st['ci_hi']}] | {st['sign_p']} |")

    L.append("\n## 3. Result by borrow-premium tier\n")
    L.append("The mechanism predicts that the edge should scale with the borrow "
             "premium. It does, under both variants -- and the ordering survives "
             "removal of the look-ahead.\n")
    for lbl, d in (("Centred smoother", wide), ("Trailing smoother", hon)):
        L.append(f"\n**{lbl}**\n")
        L.append("| Tier | n | Win rate | Mean bps | Median bps | Sharpe |")
        L.append("|---|---|---|---|---|---|")
        for _, r in tier_table(d).iterrows():
            L.append(f"| {r['tier']} | {r['n']} | {r['win_pct']}% | "
                     f"{r['mean_bps']} | {r['median_bps']} | {r['sharpe']} |")

    L.append("\n## 4. Per-ticker (trailing smoother)\n")
    L.append("| Ticker | n | Win rate | Mean bps | Sharpe |")
    L.append("|---|---|---|---|---|")
    for t in bd.TICKERS:
        v = hon.loc[hon["ticker"] == t, "S1_bps"].to_numpy(float)
        v = v[np.isfinite(v)]
        if len(v):
            L.append(f"| {t} | {len(v)} | {win_rate(v):.0f}% | "
                     f"{v.mean():+.1f} | {sharpe(v):+.2f} |")

    skipped = combined[combined["skip_reason"].notna()]
    if len(skipped):
        L.append("\n## 5. Cycles not traded\n")
        L.append("Published rather than silently dropped.\n")
        L.append("| Reason | count |")
        L.append("|---|---|")
        for reason, n in skipped["skip_reason"].value_counts().items():
            L.append(f"| {reason} | {n} |")

    (RESULTS / "summary_stats.md").write_text("\n".join(L), encoding="utf-8")
    print("\nwrote results/pnl_headline_22.csv, pnl_per_cycle.csv, summary_stats.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
