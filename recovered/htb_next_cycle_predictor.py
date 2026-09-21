"""htb_next_cycle_predictor.py — score every ticker's current cycle and predict
whether NEXT month's cycle will be HTB.

Empirical basis (121 cycles, 7 tickers): the borrow premium that has rolled into
the back months during THIS cycle's roll week (DTE<=7) is the strongest predictor
of next cycle's HTB intensity.  Next cycle's F1 == this cycle's F2, so:

    rw_b2   = mean b2 (F2-vs-spot borrow) over DTE<=7   -> corr +0.83 with next peak_b12
    end_b23 = b23 (F2-vs-F3 borrow) at the last bar      -> corr +0.79
    peak_b12 (own intensity, momentum)                   -> corr +0.75

We fit a pooled OLS  next_peak_b12 ~ rw_b2 + end_b23 + peak_b12  on all completed
cycle->next transitions, report fit quality, and apply it to each ticker's latest
cycle.  A non-parametric roll-week-b2 quartile lookup is shown alongside as a
sanity check.

Usage
-----
    python scripts/htb_next_cycle_predictor.py
    python scripts/htb_next_cycle_predictor.py --interval 15minute --strong 0.30 --htb 0.15
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

FEATURES = ["rw_b2", "end_b23", "peak_b12"]


def cycle_features(g: pd.DataFrame) -> dict:
    """Per-cycle predictive features from a single cycle's borrow_rates frame."""
    last = g.iloc[-1]
    rw = g[g["F1_dte"] <= 7]
    return dict(
        peak_b12=float(g["b12"].max()),
        rw_b2=float(rw["b2"].mean()) if len(rw) else np.nan,
        end_b23=float(last["b23"]) if "b23" in g.columns and pd.notna(last["b23"]) else np.nan,
        end_b2=float(last["b2"]) if pd.notna(last.get("b2", np.nan)) else np.nan,
        end_F2_disc=(float(last["SPOT_close"] - last["F2_close"])
                     if pd.notna(last.get("SPOT_close", np.nan)) else np.nan),
        last_dte=int(last["F1_dte"]),
        last_ts=g.index[-1],
        roll_week_seen=bool(len(rw) > 0),
        n_bars=len(g),
    )


def build_table(store: ArcticStore, tickers: list[str], interval: str) -> pd.DataFrame:
    rows = []
    for sym in tickers:
        try:
            m = store.read_borrow_rates(sym, interval)
        except Exception:
            continue
        if m.empty:
            continue
        for cid, g in m.groupby("cycle_id"):
            feat = cycle_features(g)
            feat.update(sym=sym, cid=int(cid))
            rows.append(feat)
    df = pd.DataFrame(rows).sort_values(["sym", "cid"]).reset_index(drop=True)
    # next-cycle target within each ticker
    df["next_peak_b12"] = df.groupby("sym")["peak_b12"].shift(-1)
    df["is_latest"] = df.groupby("sym")["cid"].transform("max") == df["cid"]
    return df


def fit_model(train: pd.DataFrame) -> tuple[np.ndarray, float]:
    """OLS next_peak_b12 ~ FEATURES (+intercept).  Returns (coef, R^2)."""
    t = train.dropna(subset=FEATURES + ["next_peak_b12"])
    X = np.column_stack([t[FEATURES].values, np.ones(len(t))])
    y = t["next_peak_b12"].values
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ coef
    ss_res = np.sum((y - pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return coef, r2


def quartile_lookup(train: pd.DataFrame) -> list[tuple[float, float, float]]:
    """Roll-week-b2 quartile -> mean next_peak_b12.  Returns (lo, hi, mean) bands."""
    t = train.dropna(subset=["rw_b2", "next_peak_b12"])
    qs = t["rw_b2"].quantile([0.25, 0.5, 0.75]).values
    edges = [t["rw_b2"].min(), *qs, t["rw_b2"].max() + 1e-9]
    bands = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sub = t[(t["rw_b2"] >= lo) & (t["rw_b2"] < hi)]
        bands.append((lo, hi, float(sub["next_peak_b12"].mean()) if len(sub) else np.nan))
    return bands


def quartile_predict(rw_b2: float, bands) -> float:
    if pd.isna(rw_b2):
        return np.nan
    for lo, hi, mean in bands:
        if lo <= rw_b2 < hi:
            return mean
    return bands[-1][2]


def label(pred: float, strong: float, htb: float) -> str:
    if pd.isna(pred):
        return "NO DATA"
    if pred >= strong:
        return "STRONG HTB"
    if pred >= htb:
        return "HTB"
    return "weak / fading"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Predict next-cycle HTB per ticker.")
    ap.add_argument("--interval", default="15minute")
    ap.add_argument("--strong", type=float, default=0.30, help="peak_b12 >= this = STRONG HTB")
    ap.add_argument("--htb", type=float, default=0.15, help="peak_b12 >= this = HTB")
    ap.add_argument("--tickers", nargs="*", default=None)
    args = ap.parse_args(argv)

    store = ArcticStore()
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        tickers = sorted({
            s.split("/")[0] for s in store.arctic.get_library("borrow_rates").list_symbols()
            if f"/{args.interval}" in s and "summary" not in s
        })

    df = build_table(store, tickers, args.interval)
    train = df[~df["is_latest"]].copy()  # only completed transitions train the model

    coef, r2 = fit_model(train)
    bands = quartile_lookup(train)

    print("=" * 78)
    print(f"HTB NEXT-CYCLE PREDICTOR   interval={args.interval}   "
          f"trained on {train.dropna(subset=FEATURES+['next_peak_b12']).shape[0]} transitions")
    print(f"Model: next_peak_b12 = "
          + " + ".join(f"{c:+.3f}*{n}" for c, n in zip(coef[:-1], FEATURES))
          + f" {coef[-1]:+.3f}   (R^2={r2:.2f})")
    print("Roll-week b2 quartile -> mean next peak_b12:")
    qn = ["Q1", "Q2", "Q3", "Q4"]
    for nm, (lo, hi, mean) in zip(qn, bands):
        print(f"    {nm}: b2 {lo:6.3f}-{hi:6.3f} -> {mean:.3f}")
    print("=" * 78)

    # Predict for each ticker's latest cycle
    latest = df[df["is_latest"]].copy()
    Xl = np.column_stack([latest[FEATURES].values, np.ones(len(latest))])
    latest["pred_model"] = Xl @ coef
    latest["pred_quart"] = latest["rw_b2"].apply(lambda v: quartile_predict(v, bands))
    latest["pred"] = latest[["pred_model", "pred_quart"]].mean(axis=1)
    latest["flag"] = latest["pred"].apply(lambda p: label(p, args.strong, args.htb))
    latest = latest.sort_values("pred", ascending=False)

    print(f"\n{'TICKER':<9}{'cid':>4}{'lastDTE':>8}{'rw_b2':>7}{'end_b23':>8}"
          f"{'peak_b12':>9}{'F2_disc':>8}{'->pred':>8}  {'FLAG':<13}{'asof':>12}")
    print("-" * 95)
    for _, r in latest.iterrows():
        note = "" if r["roll_week_seen"] else "  (roll wk not reached - preliminary)"
        print(f"{r['sym']:<9}{r['cid']:>4}{r['last_dte']:>8}"
              f"{r['rw_b2']:>7.3f}{r['end_b23']:>8.3f}{r['peak_b12']:>9.3f}"
              f"{r['end_F2_disc']:>8.1f}{r['pred']:>8.3f}  {r['flag']:<13}"
              f"{str(r['last_ts'].date()):>12}{note}")
    print("-" * 95)
    print("Flag thresholds: STRONG>=%.2f  HTB>=%.2f (predicted next-cycle peak b12)"
          % (args.strong, args.htb))
    print("NOTE: tickers whose 'asof' date is stale need a data refresh before trusting the call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
