"""Full NaN diagnostic across futures, borrow_rates, and options_enriched panels.

Produces:
  1. Console table — per ticker × cycle × column NaN counts and DTE ranges
  2. plots/nan_diagnostic/nan_heatmap.png  — heatmap of NaN % per ticker × column
  3. plots/nan_diagnostic/nan_by_cycle.png — per-ticker cycle NaN profiles
"""

from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data.storage.arctic_store import ArcticStore

OUT = ROOT / "plots" / "nan_diagnostic"
OUT.mkdir(parents=True, exist_ok=True)

TICKERS   = ["SBICARD", "IREDA", "VOLTAS", "KPITTECH", "BDL", "ASTRAL", "RVNL"]
INTERVAL  = "15minute"

# Key columns to audit per panel
FUT_COLS  = ["F1_close", "F2_close", "F3_close",
             "SPOT_close", "F1_oi", "F2_oi", "F3_oi"]
BR_COLS   = ["b1", "b2", "b3", "b12", "b23", "b13",
             "SPOT_close", "F2_close", "F3_close", "OI_ratio", "U_t"]
OPT_COLS  = ["close", "iv", "delta", "gamma", "theta",
             "vega", "futures_ref_price", "oi", "volume"]

SEP = "=" * 120


# ── helpers ───────────────────────────────────────────────────────────────────

def nan_block(df: pd.DataFrame, cols: list[str], panel: str) -> pd.DataFrame:
    """Return per-column NaN summary for one ticker's full panel df."""
    rows = []
    for col in cols:
        if col not in df.columns:
            continue
        n = df[col].isna().sum()
        rows.append({
            "panel": panel, "column": col,
            "total": len(df), "nan": n,
            "pct": round(n / len(df) * 100, 1) if len(df) else 0.0,
        })
    return pd.DataFrame(rows)


def cycle_nan(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Per-cycle NaN breakdown for one column. df must have cycle_id + F1_dte."""
    rows = []
    if col not in df.columns or "cycle_id" not in df.columns:
        return pd.DataFrame()
    for cid, g in df.groupby("cycle_id"):
        g = g.sort_index()
        n_total = len(g)
        n_nan   = int(g[col].isna().sum())
        expiry  = str(g["F1_expiry"].iloc[0]) if "F1_expiry" in g.columns else "?"
        if n_nan == 0:
            dte_lo = dte_hi = first_d = last_d = "-"
        else:
            nr = g[g[col].isna()]
            dte_hi  = str(int(nr["F1_dte"].max())) if "F1_dte" in nr.columns else "?"
            dte_lo  = str(int(nr["F1_dte"].min())) if "F1_dte" in nr.columns else "?"
            first_d = str(nr.index[0].date())
            last_d  = str(nr.index[-1].date())
        rows.append({
            "cycle_id": int(cid), "expiry": expiry,
            "total_bars": n_total, "nan_bars": n_nan,
            "nan_pct": round(n_nan / n_total * 100, 1),
            "dte_hi": dte_hi, "dte_lo": dte_lo,
            "first_nan": first_d, "last_nan": last_d,
        })
    return pd.DataFrame(rows)


def print_cycle_table(df_cycle: pd.DataFrame, ticker: str, col: str):
    print(f"\n    Column: {col}")
    print(f"    {'cyc':>5}  {'expiry':>12}  {'bars':>7}  {'nans':>7}  {'nan%':>6}"
          f"  {'DTE hi->lo':>12}  {'first_nan':>12}  last_nan")
    print("    " + "-" * 90)
    for _, r in df_cycle.iterrows():
        bar = f"DTE{r['dte_hi']}->{r['dte_lo']}" if r["nan_bars"] > 0 else "  none  "
        print(f"    {r['cycle_id']:>5}  {r['expiry']:>12}  {r['total_bars']:>7}"
              f"  {r['nan_bars']:>7}  {r['nan_pct']:>5.1f}%  {bar:>12}"
              f"  {r['first_nan']:>12}  {r['last_nan']}")


# ── aggregate storage for heatmap ─────────────────────────────────────────────
heatmap_rows = []   # {ticker, panel, column, nan_pct}

# ── OPTIONS helper: per-expiry aggregate ──────────────────────────────────────
def options_nan_summary(store, ticker):
    exps = store.list_option_expiries(ticker, INTERVAL)
    rows = []
    for exp in exps:
        try:
            o = store.read_options(ticker, INTERVAL, exp)
        except Exception:
            continue
        n_total = len(o)
        for col in OPT_COLS:
            if col not in o.columns:
                continue
            n = int(o[col].isna().sum())
            rows.append({
                "expiry": exp, "column": col,
                "total": n_total, "nan": n,
                "pct": round(n / n_total * 100, 1) if n_total else 0.0,
            })
    return pd.DataFrame(rows)


def main():
    store = ArcticStore()

    # ══════════════════════════════════════════════════════════════════════════
    # 1. FUTURES PANEL
    # ══════════════════════════════════════════════════════════════════════════
    print(SEP)
    print("  PANEL 1 — FUTURES WIDE PANEL (15minute)  |  key columns: F1/F2/F3 close, SPOT_close")
    print(SEP)

    fut_summary = []
    for ticker in TICKERS:
        try:
            df = store.read_futures(ticker, INTERVAL)
        except Exception as e:
            print(f"\n  {ticker}: LOAD ERROR — {e}"); continue

        nb = nan_block(df, FUT_COLS, "futures")
        fut_summary.append(nb.assign(ticker=ticker))
        for r in nb.to_dict("records"):
            heatmap_rows.append({**r, "ticker": ticker})

        print(f"\n  {'─'*60}")
        print(f"  {ticker}  |  {len(df)} rows  |  "
              f"{df.index[0].date()} → {df.index[-1].date()}")
        any_nan = nb[nb["nan"] > 0]
        if any_nan.empty:
            print("    ✅ No NaNs in any key column")
        else:
            print(f"    {'column':<20} {'nan_bars':>9}  {'nan%':>6}  detail by cycle")
            print("    " + "-" * 80)
            for _, r in any_nan.iterrows():
                print(f"    {r['column']:<20} {r['nan']:>9,}  {r['pct']:>5.1f}%")
                cd = cycle_nan(df, r["column"])
                cd_with_nan = cd[cd["nan_bars"] > 0]
                if not cd_with_nan.empty:
                    print_cycle_table(cd_with_nan, ticker, r["column"])

    # ══════════════════════════════════════════════════════════════════════════
    # 2. BORROW RATES PANEL
    # ══════════════════════════════════════════════════════════════════════════
    print("\n\n" + SEP)
    print("  PANEL 2 — BORROW RATES (15minute)  |  key columns: b1/b2/b12, SPOT_close, OI_ratio")
    print(SEP)

    br_summary = []
    for ticker in TICKERS:
        try:
            df = store.read_borrow_rates(ticker, INTERVAL)
        except Exception as e:
            print(f"\n  {ticker}: LOAD ERROR — {e}"); continue

        nb = nan_block(df, BR_COLS, "borrow")
        br_summary.append(nb.assign(ticker=ticker))
        for r in nb.to_dict("records"):
            heatmap_rows.append({**r, "ticker": ticker})

        print(f"\n  {'─'*60}")
        print(f"  {ticker}  |  {len(df)} rows  |  "
              f"{df.index[0].date()} → {df.index[-1].date()}")
        any_nan = nb[nb["nan"] > 0]
        if any_nan.empty:
            print("    ✅ No NaNs in any key column")
        else:
            print(f"    {'column':<20} {'nan_bars':>9}  {'nan%':>6}")
            print("    " + "-" * 40)
            for _, r in any_nan.iterrows():
                # use the futures df for DTE/cycle if available; borrow may lack cycle_id
                flag = ""
                if r["pct"] > 90:
                    flag = "  ⚠ CRITICAL"
                elif r["pct"] > 30:
                    flag = "  ⚠ HIGH"
                print(f"    {r['column']:<20} {r['nan']:>9,}  {r['pct']:>5.1f}%{flag}")

    # ══════════════════════════════════════════════════════════════════════════
    # 3. OPTIONS ENRICHED PANEL
    # ══════════════════════════════════════════════════════════════════════════
    print("\n\n" + SEP)
    print("  PANEL 3 — OPTIONS ENRICHED (15minute)  |  key columns: close, iv, delta, greeks")
    print(SEP)

    for ticker in TICKERS:
        exps = store.list_option_expiries(ticker, INTERVAL)
        print(f"\n  {'─'*60}")
        print(f"  {ticker}  |  {len(exps)} expiries")
        if not exps:
            print("    No option expiries found"); continue

        opt_df = options_nan_summary(store, ticker)
        if opt_df.empty:
            print("    No data"); continue

        # Aggregate across expiries → per-column totals
        agg = opt_df.groupby("column").agg(
            total=("total", "sum"), nan=("nan", "sum")
        ).reset_index()
        agg["pct"] = (agg["nan"] / agg["total"] * 100).round(1)
        agg = agg.sort_values("nan", ascending=False)

        any_nan = agg[agg["nan"] > 0]
        if any_nan.empty:
            print("    ✅ No NaNs across all expiries")
        else:
            print(f"    Pooled across {len(exps)} expiries:")
            print(f"    {'column':<22} {'total_bars':>11}  {'nan_bars':>10}  {'nan%':>6}")
            print("    " + "-" * 60)
            for _, r in any_nan.iterrows():
                flag = "  ⚠ CRITICAL" if r["pct"] > 90 else (
                       "  ⚠ HIGH" if r["pct"] > 20 else "")
                print(f"    {r['column']:<22} {r['total']:>11,}  {r['nan']:>10,}  {r['pct']:>5.1f}%{flag}")

            # Per-expiry breakdown for columns with >5% NaN
            bad_cols = any_nan[any_nan["pct"] > 5]["column"].tolist()
            if bad_cols:
                print(f"\n    Per-expiry detail for columns with >5% NaN:")
                print(f"    {'expiry':>12}  {'total':>8}" +
                      "".join(f"  {c[:8]:>10}" for c in bad_cols))
                print("    " + "-" * (28 + 12 * len(bad_cols)))
                for exp in exps:
                    sub = opt_df[opt_df["expiry"] == exp]
                    if sub.empty: continue
                    tot = sub["total"].iloc[0]
                    vals = []
                    for c in bad_cols:
                        row = sub[sub["column"] == c]
                        if row.empty:
                            vals.append("  n/a")
                        else:
                            p = float(row["pct"].iloc[0])
                            vals.append(f"{p:>9.1f}%")
                    print(f"    {exp:>12}  {tot:>8,}" + "".join(vals))

        for r in agg.to_dict("records"):
            heatmap_rows.append({
                "ticker": ticker, "panel": "options",
                "column": r["column"], "nan": r["nan"],
                "total": r["total"], "pct": r["pct"]
            })

    # ══════════════════════════════════════════════════════════════════════════
    # 4. MASTER SUMMARY TABLE
    # ══════════════════════════════════════════════════════════════════════════
    print("\n\n" + SEP)
    print("  MASTER SUMMARY — worst NaN offenders (>5%) across all panels and tickers")
    print(SEP)
    if heatmap_rows:
        hdf = pd.DataFrame(heatmap_rows)
        bad = hdf[hdf["pct"] > 5].sort_values("pct", ascending=False)
        if not bad.empty:
            print(f"  {'ticker':<12}{'panel':<10}{'column':<25}{'nan%':>7}  {'nan_bars':>10}")
            print("  " + "-" * 68)
            for _, r in bad.iterrows():
                flag = " ⚠ CRITICAL" if r["pct"] > 90 else (
                       " ⚠ HIGH" if r["pct"] > 50 else "")
                print(f"  {r['ticker']:<12}{r['panel']:<10}{r['column']:<25}"
                      f"{r['pct']:>6.1f}%  {int(r['nan']):>10,}{flag}")

    # ══════════════════════════════════════════════════════════════════════════
    # 5. HEATMAP FIGURE
    # ══════════════════════════════════════════════════════════════════════════
    if not heatmap_rows:
        print("\nNo data for heatmap"); return

    hdf = pd.DataFrame(heatmap_rows)

    # One heatmap per panel
    fig, axes = plt.subplots(1, 3, figsize=(22, 7))
    panels = [("futures", FUT_COLS, axes[0]),
              ("borrow",  BR_COLS,  axes[1]),
              ("options", OPT_COLS, axes[2])]

    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nan_heat", ["#f7fbff", "#fdae6b", "#d62728"])

    for panel_name, col_list, ax in panels:
        sub = hdf[hdf["panel"] == panel_name]
        if sub.empty:
            ax.set_title(f"{panel_name}\n(no data)"); continue
        # pivot: rows=ticker, cols=column
        piv = sub.pivot_table(index="ticker", columns="column",
                              values="pct", aggfunc="mean")
        # keep only col_list order and fill missing
        cols_present = [c for c in col_list if c in piv.columns]
        piv = piv[cols_present].reindex(TICKERS).fillna(0)

        im = ax.imshow(piv.values, cmap=cmap, vmin=0, vmax=100,
                       aspect="auto")
        ax.set_xticks(range(len(cols_present)))
        ax.set_xticklabels(cols_present, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(TICKERS)))
        ax.set_yticklabels(TICKERS, fontsize=9)
        ax.set_title(f"{panel_name.upper()} — NaN %", fontsize=10)
        # annotate cells
        for i in range(len(TICKERS)):
            for j in range(len(cols_present)):
                v = piv.values[i, j]
                if v > 0:
                    ax.text(j, i, f"{v:.0f}", ha="center", va="center",
                            fontsize=7,
                            color="white" if v > 50 else "black")
        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04,
                     label="NaN %")

    fig.suptitle("NaN % heatmap — futures / borrow_rates / options_enriched\n"
                 "All tickers, 15minute interval, key columns",
                 fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    p = OUT / "nan_heatmap.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print(f"\nHeatmap saved: {p}")


if __name__ == "__main__":
    main()
