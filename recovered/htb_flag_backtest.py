"""htb_flag_backtest.py — out-of-sample validation of the next-cycle HTB flag.

For every completed cycle->next transition (pooled across tickers) we refit the
predictor on ALL OTHER transitions (leave-one-cycle-out), predict the held-out
cycle, classify the flag, and compare to the realised next-cycle peak_b12.  This
removes in-sample optimism, so the hit rates below are what you'd actually have
gotten predicting blind, one cycle at a time.

Usage
-----
    python scripts/htb_flag_backtest.py
    python scripts/htb_flag_backtest.py --strong 0.30 --htb 0.15
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.storage.arctic_store import ArcticStore  # noqa: E402
from scripts.htb_next_cycle_predictor import (  # noqa: E402
    FEATURES, build_table, fit_model, quartile_lookup, quartile_predict, label,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backtest the next-cycle HTB flag (leave-one-out).")
    ap.add_argument("--interval", default="15minute")
    ap.add_argument("--strong", type=float, default=0.30)
    ap.add_argument("--htb", type=float, default=0.15)
    args = ap.parse_args(argv)

    store = ArcticStore()
    tickers = sorted({
        s.split("/")[0] for s in store.arctic.get_library("borrow_rates").list_symbols()
        if f"/{args.interval}" in s and "summary" not in s
    })
    df = build_table(store, tickers, args.interval)

    # Universe of scorable transitions: features + outcome all present
    base = df.dropna(subset=FEATURES + ["next_peak_b12"]).reset_index(drop=True)
    n = len(base)

    preds, actuals, pflags, aflags, syms = [], [], [], [], []
    for i in range(n):
        train = base.drop(index=i)
        coef, _ = fit_model(train)
        bands = quartile_lookup(train)
        row = base.loc[i]
        x = np.array([row["rw_b2"], row["end_b23"], row["peak_b12"], 1.0])
        pm = float(x @ coef)
        pq = quartile_predict(row["rw_b2"], bands)
        pred = np.nanmean([pm, pq])
        act = row["next_peak_b12"]
        preds.append(pred); actuals.append(act)
        pflags.append(label(pred, args.strong, args.htb))
        aflags.append(label(act, args.strong, args.htb))
        syms.append(row["sym"])

    r = pd.DataFrame(dict(sym=syms, pred=preds, actual=actuals,
                          pflag=pflags, aflag=aflags))
    oos_r = np.corrcoef(r["pred"], r["actual"])[0, 1]

    print("=" * 74)
    print(f"HTB FLAG BACKTEST (leave-one-out)  interval={args.interval}  "
          f"transitions={n}")
    print(f"Out-of-sample corr(pred, actual next peak_b12) = {oos_r:+.2f}")
    print(f"Base rate  P(next is HTB,  actual>= {args.htb}) = "
          f"{(r['actual'] >= args.htb).mean():.2f}")
    print(f"Base rate  P(next is STRONG, actual>= {args.strong}) = "
          f"{(r['actual'] >= args.strong).mean():.2f}")
    print("=" * 74)

    # --- Precision of each predicted flag ---
    print("\nPredicted flag  ->  what actually happened next cycle")
    print(f"{'pred flag':<14}{'n':>4}{'%hit HTB':>10}{'%hit STRONG':>13}"
          f"{'mean actual':>13}{'median':>9}")
    for fl in ["STRONG HTB", "HTB", "weak / fading"]:
        sub = r[r["pflag"] == fl]
        if len(sub) == 0:
            continue
        print(f"{fl:<14}{len(sub):>4}"
              f"{(sub['actual'] >= args.htb).mean()*100:>9.0f}%"
              f"{(sub['actual'] >= args.strong).mean()*100:>12.0f}%"
              f"{sub['actual'].mean():>13.3f}{sub['actual'].median():>9.3f}")

    # --- Confusion matrix ---
    order = ["STRONG HTB", "HTB", "weak / fading"]
    cm = pd.crosstab(pd.Categorical(r["pflag"], order),
                     pd.Categorical(r["aflag"], order), dropna=False)
    print("\nConfusion matrix  (rows = predicted, cols = actual)")
    print(cm.to_string())

    # --- Per-ticker hit rate for STRONG flag ---
    print("\nPer-ticker: when flag=STRONG HTB, did next cycle clear HTB threshold?")
    strong = r[r["pflag"] == "STRONG HTB"]
    if len(strong):
        for sym, g in strong.groupby("sym"):
            print(f"  {sym:<9} n={len(g):>2}  hit HTB {(g['actual']>=args.htb).mean()*100:>3.0f}%"
                  f"  hit STRONG {(g['actual']>=args.strong).mean()*100:>3.0f}%"
                  f"  mean next peak_b12 {g['actual'].mean():.3f}")
    else:
        print("  (no STRONG predictions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
