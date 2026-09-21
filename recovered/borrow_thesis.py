"""Thesis test: Does the F1-F2 borrow spread "explode" in UP cycles?

For every ticker × cycle:
  - spot_return   = (spot_end - spot_start) / spot_start  × 100  (% over the cycle)
  - direction     = UP if spot_return > 0, else DOWN
  - spread_max    = max(F1_close - F2_close) in Rs over the cycle
  - spread_mean   = mean spread over the cycle
  - spread_end    = mean spread in last 5 trading days (convergence period)
  - b1_max        = peak annualised borrow rate (F1-Spot), from borrow_rates library

Then:
  1. Per-ticker table: all cycles with colour-coded UP/DOWN
  2. UP vs DOWN group means for spread_max and spread_mean (t-test + Mann-Whitney)
  3. Scatter: spot_return vs spread_max across all cycles (all tickers pooled)
  4. Correlation matrix per ticker
"""

from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data.storage.arctic_store import ArcticStore

OUT = ROOT / "plots" / "borrow_thesis"
OUT.mkdir(parents=True, exist_ok=True)

TICKERS = ["SBICARD", "IREDA", "VOLTAS", "KPITTECH", "BDL", "ASTRAL", "RVNL"]
INTERVAL = "15minute"
MIN_BARS = 200          # skip tiny partial cycles
LAST_N_BARS = 125       # ~5 trading days × 25 bars for "end spread"


def per_cycle_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Given a ticker's 15min wide-panel df, return one row per cycle."""
    df = df.copy()
    df["spread"] = df["F1_close"] - df["F2_close"]
    rows = []
    for cid, g in df.groupby("cycle_id"):
        g = g.sort_index()
        if len(g) < MIN_BARS:
            continue
        spot = g["SPOT_close"].dropna()
        if len(spot) < 20:
            continue

        spot_start = float(spot.iloc[:5].mean())    # first day avg
        spot_end   = float(spot.iloc[-5:].mean())   # last day avg
        spot_ret   = (spot_end - spot_start) / spot_start * 100
        direction  = "UP" if spot_ret > 0 else "DOWN"

        sp = g["spread"].dropna()
        if len(sp) < 10:
            continue
        spread_max  = float(sp.max())
        spread_mean = float(sp.mean())
        spread_start = float(sp.iloc[:LAST_N_BARS].mean())        # first 5 days
        spread_end   = float(sp.iloc[-LAST_N_BARS:].mean())       # last 5 days
        spread_range = spread_max - float(sp.min())               # total range

        # Day of max spread (as % through cycle)
        try:
            idx_max = sp.idxmax()
            pos_max = g.index.get_loc(idx_max) if idx_max in g.index else np.nan
            pct_through = float(pos_max) / len(g) * 100 if not np.isnan(pos_max) else np.nan
        except Exception:
            pct_through = np.nan

        expiry = str(g["F1_expiry"].iloc[0])

        rows.append({
            "cycle_id":     cid,
            "expiry":       expiry,
            "n_bars":       len(g),
            "spot_start":   round(spot_start, 1),
            "spot_end":     round(spot_end, 1),
            "spot_ret_pct": round(spot_ret, 2),
            "direction":    direction,
            "spread_max":   round(spread_max, 2),
            "spread_mean":  round(spread_mean, 2),
            "spread_start": round(spread_start, 2),
            "spread_end":   round(spread_end, 2),
            "spread_range": round(spread_range, 2),
            "pct_through":  round(pct_through, 1),
        })
    return pd.DataFrame(rows)


def stat_test(up_vals, dn_vals, metric_name):
    """t-test + Mann-Whitney U. Returns string summary."""
    if len(up_vals) < 2 or len(dn_vals) < 2:
        return "  (insufficient data)"
    t, pt = stats.ttest_ind(up_vals, dn_vals, equal_var=False)
    u, pu = stats.mannwhitneyu(up_vals, dn_vals, alternative="two-sided")
    return (f"  UP mean={np.mean(up_vals):.2f}  DOWN mean={np.mean(dn_vals):.2f} "
            f"  diff={np.mean(up_vals)-np.mean(dn_vals):+.2f}"
            f"  t-test p={pt:.3f}  MW p={pu:.3f}"
            + ("  ***SIGNIFICANT***" if min(pt, pu) < 0.05 else ""))


def main():
    store = ArcticStore()
    all_rows = []

    # ── Per-ticker table ─────────────────────────────────────────────────────
    print("\n" + "=" * 120)
    print("  THESIS: Does F1-F2 spread EXPLODE when spot rallies (UP cycle)?")
    print("=" * 120)
    print(f"  Spread = F1_close - F2_close (Rs). Positive = backwardation (HTB demand).\n")

    ticker_dfs = {}
    for ticker in TICKERS:
        try:
            df = store.read_futures(ticker, INTERVAL,
                                    columns=["F1_close", "F2_close",
                                             "SPOT_close", "cycle_id", "F1_expiry"])
        except Exception as e:
            print(f"  {ticker}: load error — {e}")
            continue

        cyc = per_cycle_metrics(df)
        if cyc.empty:
            continue
        ticker_dfs[ticker] = cyc

        # Print per-ticker cycles
        print(f"\n  ── {ticker} ({'─'*(50)})")
        print(f"  {'cyc':>4}  {'expiry':>12}  {'spot_start':>10}  {'spot_end':>9}  "
              f"{'ret%':>7}  {'dir':>5}  {'sprd_max':>9}  {'sprd_mean':>10}  "
              f"{'sprd_start':>11}  {'sprd_end':>9}")
        print("  " + "-" * 106)
        for _, r in cyc.iterrows():
            flag = "▲ UP  " if r["direction"] == "UP" else "▼ DOWN"
            print(f"  {int(r['cycle_id']):>4}  {r['expiry']:>12}  {r['spot_start']:>10.0f}  "
                  f"{r['spot_end']:>9.0f}  {r['spot_ret_pct']:>+7.1f}%  {flag}  "
                  f"{r['spread_max']:>9.2f}  {r['spread_mean']:>10.2f}  "
                  f"{r['spread_start']:>11.2f}  {r['spread_end']:>9.2f}")

        # Stats for this ticker
        up = cyc[cyc["direction"] == "UP"]
        dn = cyc[cyc["direction"] == "DOWN"]
        print(f"\n  {ticker}  UP cycles={len(up)}  DOWN cycles={len(dn)}")
        print(f"  spread_max :" + stat_test(up["spread_max"].values,
                                             dn["spread_max"].values, "spread_max"))
        print(f"  spread_mean:" + stat_test(up["spread_mean"].values,
                                             dn["spread_mean"].values, "spread_mean"))
        r_corr, p_corr = stats.pearsonr(cyc["spot_ret_pct"], cyc["spread_max"])
        print(f"  Pearson r(spot_ret, spread_max) = {r_corr:+.3f}  p={p_corr:.3f}"
              + ("  ***" if p_corr < 0.05 else ""))

        # Add ticker column for pooled analysis
        cyc["ticker"] = ticker
        all_rows.append(cyc)

    # ── POOLED analysis across all tickers ──────────────────────────────────
    all_cyc = pd.concat(all_rows, ignore_index=True)
    all_cyc = all_cyc.dropna(subset=["spot_ret_pct", "spread_max"])

    print("\n\n" + "=" * 80)
    print("  POOLED ANALYSIS — all tickers, all cycles")
    print("=" * 80)
    up_all = all_cyc[all_cyc["direction"] == "UP"]
    dn_all = all_cyc[all_cyc["direction"] == "DOWN"]
    print(f"  Total cycles: {len(all_cyc)}  |  UP: {len(up_all)}  DOWN: {len(dn_all)}")
    print(f"\n  spread_max :" + stat_test(up_all["spread_max"].values,
                                           dn_all["spread_max"].values, "spread_max"))
    print(f"  spread_mean:" + stat_test(up_all["spread_mean"].values,
                                         dn_all["spread_mean"].values, "spread_mean"))
    r_all, p_all = stats.pearsonr(all_cyc["spot_ret_pct"], all_cyc["spread_max"])
    print(f"\n  Pooled Pearson r(spot_ret%, spread_max) = {r_all:+.3f}  p={p_all:.4f}"
          + ("  ***SIGNIFICANT***" if p_all < 0.05 else ""))
    r_spear, p_spear = stats.spearmanr(all_cyc["spot_ret_pct"], all_cyc["spread_max"])
    print(f"  Pooled Spearman r(spot_ret%, spread_max) = {r_spear:+.3f}  p={p_spear:.4f}"
          + ("  ***SIGNIFICANT***" if p_spear < 0.05 else ""))

    # Quadrant analysis
    print(f"\n  QUADRANT ANALYSIS (high spread = spread_max > median):")
    med_sp = all_cyc["spread_max"].median()
    print(f"  Median spread_max across all cycles = {med_sp:.2f} Rs")
    q_uu = len(all_cyc[(all_cyc["direction"] == "UP")   & (all_cyc["spread_max"] > med_sp)])
    q_ud = len(all_cyc[(all_cyc["direction"] == "UP")   & (all_cyc["spread_max"] <= med_sp)])
    q_du = len(all_cyc[(all_cyc["direction"] == "DOWN") & (all_cyc["spread_max"] > med_sp)])
    q_dd = len(all_cyc[(all_cyc["direction"] == "DOWN") & (all_cyc["spread_max"] <= med_sp)])
    print(f"  UP   + high spread : {q_uu:>3}  ({q_uu/(q_uu+q_ud)*100:.0f}% of UP cycles have high spread)")
    print(f"  UP   + low  spread : {q_ud:>3}")
    print(f"  DOWN + high spread : {q_du:>3}  ({q_du/(q_du+q_dd)*100:.0f}% of DOWN cycles have high spread)")
    print(f"  DOWN + low  spread : {q_dd:>3}")

    # ── FIGURES ──────────────────────────────────────────────────────────────
    n_tickers = len(ticker_dfs)
    colors = plt.cm.tab10.colors

    # Fig 1: scatter spot_ret vs spread_max per ticker (pooled + per-ticker)
    fig, axes = plt.subplots(2, 4, figsize=(18, 10))
    axes = axes.flatten()

    # Pooled scatter
    ax = axes[0]
    for i, (tkr, cyc) in enumerate(ticker_dfs.items()):
        up_m = cyc[cyc["direction"] == "UP"]
        dn_m = cyc[cyc["direction"] == "DOWN"]
        ax.scatter(up_m["spot_ret_pct"], up_m["spread_max"],
                   color=colors[i], marker="^", s=60, alpha=0.8)
        ax.scatter(dn_m["spot_ret_pct"], dn_m["spread_max"],
                   color=colors[i], marker="v", s=60, alpha=0.8,
                   label=tkr)
    # regression line
    x = all_cyc["spot_ret_pct"].values; y = all_cyc["spread_max"].values
    m, b = np.polyfit(x, y, 1)
    xr = np.linspace(x.min(), x.max(), 100)
    ax.plot(xr, m * xr + b, "k--", lw=1.5,
            label=f"fit: r={r_all:+.2f} p={p_all:.3f}")
    ax.axvline(0, color="grey", lw=0.8, ls=":")
    ax.axhline(0, color="grey", lw=0.8, ls=":")
    ax.set_xlabel("Spot return % (cycle)"); ax.set_ylabel("Max F1-F2 spread (Rs)")
    ax.set_title("POOLED: spot return vs spread_max\n▲=UP ▼=DOWN")
    ax.legend(fontsize=7, ncol=2); ax.grid(alpha=0.2)

    # Per-ticker scatter
    for i, (tkr, cyc) in enumerate(ticker_dfs.items()):
        if i + 1 >= len(axes): break
        ax = axes[i + 1]
        up_m = cyc[cyc["direction"] == "UP"]
        dn_m = cyc[cyc["direction"] == "DOWN"]
        ax.scatter(up_m["spot_ret_pct"], up_m["spread_max"],
                   color="#2ca02c", marker="^", s=70, alpha=0.85, label="UP")
        ax.scatter(dn_m["spot_ret_pct"], dn_m["spread_max"],
                   color="#d62728", marker="v", s=70, alpha=0.85, label="DOWN")
        # annotate with cycle expiry (short)
        for _, r in cyc.iterrows():
            ax.annotate(str(r["expiry"])[5:],  # MM-DD
                        (r["spot_ret_pct"], r["spread_max"]),
                        fontsize=6, ha="center", va="bottom", alpha=0.7)
        if len(cyc) >= 4:
            xc = cyc["spot_ret_pct"].values; yc = cyc["spread_max"].values
            rc, pc = stats.pearsonr(xc, yc)
            mc, bc = np.polyfit(xc, yc, 1)
            xr2 = np.linspace(xc.min(), xc.max(), 50)
            ax.plot(xr2, mc * xr2 + bc, "k--", lw=1.2,
                    label=f"r={rc:+.2f} p={pc:.2f}")
        ax.axvline(0, color="grey", lw=0.8, ls=":")
        ax.axhline(0, color="grey", lw=0.8, ls=":")
        ax.set_xlabel("Spot return %"); ax.set_ylabel("Max spread (Rs)")
        ax.set_title(f"{tkr}")
        ax.legend(fontsize=7); ax.grid(alpha=0.2)

    for j in range(n_tickers + 1, len(axes)):
        axes[j].axis("off")

    fig.suptitle("Thesis: F1-F2 spread explosion vs spot direction per cycle\n"
                 "Positive spread = backwardation (F1>F2). Thesis holds if UP cycles → higher spread.",
                 fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    p1 = OUT / "borrow_vs_spot_scatter.png"
    fig.savefig(p1, dpi=120); plt.close(fig)

    # Fig 2: box/bar comparison UP vs DOWN for each ticker
    fig2, axes2 = plt.subplots(2, 4, figsize=(18, 10))
    axes2 = axes2.flatten()
    all_tickers_list = list(ticker_dfs.keys())

    # Pooled box
    ax = axes2[0]
    up_vals = up_all["spread_max"].values
    dn_vals = dn_all["spread_max"].values
    bp = ax.boxplot([up_vals, dn_vals], labels=["UP", "DOWN"],
                    patch_artist=True, widths=0.5)
    bp["boxes"][0].set_facecolor("#2ca02c"); bp["boxes"][0].set_alpha(0.6)
    bp["boxes"][1].set_facecolor("#d62728"); bp["boxes"][1].set_alpha(0.6)
    ax.set_ylabel("Max F1-F2 spread (Rs)")
    _, p_t = stats.ttest_ind(up_vals, dn_vals, equal_var=False)
    ax.set_title(f"POOLED spread_max UP vs DOWN\nt-test p={p_t:.3f}"
                 + (" ***" if p_t < 0.05 else ""))
    ax.grid(alpha=0.2, axis="y")

    for i, tkr in enumerate(all_tickers_list):
        if i + 1 >= len(axes2): break
        cyc = ticker_dfs[tkr]
        ax = axes2[i + 1]
        u = cyc[cyc["direction"] == "UP"]["spread_max"].values
        d = cyc[cyc["direction"] == "DOWN"]["spread_max"].values
        if len(u) == 0 or len(d) == 0:
            ax.set_title(f"{tkr} — insufficient data"); continue
        bp = ax.boxplot([u, d], labels=[f"UP\n(n={len(u)})", f"DOWN\n(n={len(d)})"],
                        patch_artist=True, widths=0.5)
        bp["boxes"][0].set_facecolor("#2ca02c"); bp["boxes"][0].set_alpha(0.6)
        bp["boxes"][1].set_facecolor("#d62728"); bp["boxes"][1].set_alpha(0.6)
        if len(u) >= 2 and len(d) >= 2:
            _, pt = stats.ttest_ind(u, d, equal_var=False)
            sig = " ***" if pt < 0.05 else ""
            ax.set_title(f"{tkr}  spread_max UP vs DOWN\nt-test p={pt:.3f}{sig}")
        else:
            ax.set_title(f"{tkr}  spread_max UP vs DOWN")
        ax.set_ylabel("Max spread (Rs)"); ax.grid(alpha=0.2, axis="y")

    for j in range(n_tickers + 1, len(axes2)):
        axes2[j].axis("off")

    fig2.suptitle("Max F1-F2 spread (Rs) — UP cycles vs DOWN cycles, per ticker\n"
                  "Thesis holds if green box (UP) is significantly higher than red box (DOWN)",
                  fontsize=11)
    fig2.tight_layout(rect=[0, 0, 1, 0.95])
    p2 = OUT / "borrow_vs_spot_boxplot.png"
    fig2.savefig(p2, dpi=120); plt.close(fig2)

    # Fig 3: heatmap — cycle direction × spread_max per ticker (sorted by spot_ret)
    fig3, axes3 = plt.subplots(2, 4, figsize=(18, 10))
    axes3 = axes3.flatten()

    # Build pooled direction-vs-spread heatmap (bar chart sorted)
    ax = axes3[0]
    pooled_sorted = all_cyc.sort_values("spot_ret_pct")
    bar_cols = ["#d62728" if d == "DOWN" else "#2ca02c"
                for d in pooled_sorted["direction"]]
    ax.bar(range(len(pooled_sorted)), pooled_sorted["spread_max"],
           color=bar_cols, alpha=0.8)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("Cycles sorted by spot return (left=most negative)")
    ax.set_ylabel("Max spread (Rs)")
    ax.set_title("POOLED — all cycles sorted by spot return\nGreen=UP  Red=DOWN")
    ax.grid(alpha=0.2, axis="y")
    up_p = mpatches.Patch(color="#2ca02c", alpha=0.7, label="UP cycle")
    dn_p = mpatches.Patch(color="#d62728", alpha=0.7, label="DOWN cycle")
    ax.legend(handles=[up_p, dn_p], fontsize=8)

    for i, tkr in enumerate(all_tickers_list):
        if i + 1 >= len(axes3): break
        cyc = ticker_dfs[tkr].sort_values("spot_ret_pct")
        ax = axes3[i + 1]
        bar_cols = ["#d62728" if d == "DOWN" else "#2ca02c"
                    for d in cyc["direction"]]
        ax.bar(range(len(cyc)), cyc["spread_max"], color=bar_cols, alpha=0.8)
        ax.axhline(0, color="k", lw=0.8)
        # label with expiry month
        ax.set_xticks(range(len(cyc)))
        ax.set_xticklabels([e[5:7] for e in cyc["expiry"]], fontsize=7)
        ax.set_xlabel("Expiry month (sorted by spot return)")
        ax.set_ylabel("Max spread (Rs)")
        ax.set_title(f"{tkr} — sorted by spot return"); ax.grid(alpha=0.2, axis="y")

    for j in range(n_tickers + 1, len(axes3)):
        axes3[j].axis("off")

    fig3.suptitle("Max F1-F2 spread per cycle — sorted by spot return (left=most bearish)\n"
                  "If thesis holds, green bars (UP) should cluster on the right with higher values",
                  fontsize=11)
    fig3.tight_layout(rect=[0, 0, 1, 0.95])
    p3 = OUT / "borrow_vs_spot_sorted_bar.png"
    fig3.savefig(p3, dpi=120); plt.close(fig3)

    print(f"\nSaved:\n  {p1}\n  {p2}\n  {p3}")


if __name__ == "__main__":
    main()
