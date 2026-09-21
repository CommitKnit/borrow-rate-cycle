"""bdl_put_skew_htb.py — Verify put-IV skew in BDL HTB cycles where peak F1-F2 spread > 20.

Hypothesis:
    During the peak-spread window of high-HTB cycles, the market prices downside
    (ITM puts at strikes K < F1) at a higher IV than same-strike OTM calls — because
    the HTB premium in F1 creates crash risk: if the borrow premium collapses, F1/spot
    fall and those puts flip deeply ITM.

For each qualifying BDL cycle (peak spread > SPREAD_THRESH):
  1. Window: DTE 14 -> DTE 5 (build-up and peak zone, before the roll week blow-up).
  2. At each 15-min bar: find 5 strikes immediately BELOW F1 ref price (rank 1 = closest).
  3. For each rank, compare put IV vs call IV at that same strike.
  4. Report: mean skew (PE_IV - CE_IV) and % of bars where put > call, per strike rank.

Output: per-cycle table + pooled across all qualifying cycles.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.storage.arctic_store import ArcticStore  # noqa: E402

SYM = "BDL"
INTERVAL = "15minute"
SPREAD_THRESH = 20.0  # qualifying peak spread (pts)
ITM_STRIKES = 5       # number of strike ranks below ATM to check
DTE_HI = 14           # window: DTE <= this
DTE_LO = 5            # window: DTE >= this


def _build_expiry_map(br: pd.DataFrame, oe_lib) -> dict[int, str]:
    """Map cycle_id -> F1 options expiry symbol by date-range overlap."""
    opt_syms = sorted(
        s.split("/")[-1] for s in oe_lib.list_symbols()
        if s.startswith(f"{SYM}/{INTERVAL}/")
    )
    exp_map: dict[int, str] = {}
    for cid, g in br.groupby("cycle_id"):
        cid_start = g.index.min().date()
        for exp in opt_syms:
            if pd.Timestamp(exp).date() >= cid_start:
                exp_map[int(cid)] = exp
                break
    return exp_map


def load_options(oe_lib, expiry: str) -> pd.DataFrame:
    """Load options_enriched for a given expiry, keep F1 slot only."""
    try:
        df = oe_lib.read(f"{SYM}/{INTERVAL}/{expiry}").data
    except Exception:
        return pd.DataFrame()
    f1 = df[df["slot"] == "F1"].copy()
    needed = {"strike", "opt_type", "iv", "futures_ref_price"}
    if f1.empty or not needed.issubset(f1.columns):
        return pd.DataFrame()
    return f1


def skew_at_bar(group: pd.DataFrame, n_strikes: int = 5) -> pd.DataFrame:
    """For one timestamp's F1 options, compute per-rank skew for n strikes below ATM.

    rank 1 = immediately below ATM (ITM put, OTM call closest to money)
    rank 5 = furthest ITM put within the n_strikes window
    """
    ref = group["futures_ref_price"].iloc[0]
    if pd.isna(ref) or ref <= 0:
        return pd.DataFrame()

    valid = group[group["iv"].notna() & (group["iv"] > 0)].copy()
    if valid.empty:
        return pd.DataFrame()

    # strikes strictly below ATM (ITM puts / OTM calls)
    below = sorted(k for k in valid["strike"].unique() if k < ref)
    if not below:
        return pd.DataFrame()

    # take the n_strikes highest values (closest to ATM)
    picks = below[-n_strikes:][::-1]  # [closest, ..., furthest]

    rows = []
    for rank, k in enumerate(picks, start=1):
        bucket = valid[valid["strike"] == k]
        pe = bucket[bucket["opt_type"] == "PE"]
        ce = bucket[bucket["opt_type"] == "CE"]
        pe_iv = float(pe["iv"].iloc[0]) if len(pe) and pd.notna(pe["iv"].iloc[0]) else np.nan
        ce_iv = float(ce["iv"].iloc[0]) if len(ce) and pd.notna(ce["iv"].iloc[0]) else np.nan
        if pd.isna(pe_iv) or pd.isna(ce_iv):
            continue
        rows.append(dict(
            rank=rank,
            strike=k,
            dist_pts=int(round(ref - k)),
            pe_iv=pe_iv,
            ce_iv=ce_iv,
            skew=pe_iv - ce_iv,
            put_over_call=(pe_iv > ce_iv),
        ))
    return pd.DataFrame(rows)


def analyze_cycle(opt_f1: pd.DataFrame, window: pd.DataFrame, n_strikes: int) -> pd.DataFrame:
    """Aggregate per-rank skew over all bars in the window."""
    all_bars = []
    ts_index = set(window.index)
    for ts, g in opt_f1.groupby(level=0):
        if ts not in ts_index:
            continue
        bar = skew_at_bar(g, n_strikes)
        if not bar.empty:
            all_bars.append(bar)

    if not all_bars:
        return pd.DataFrame()

    df = pd.concat(all_bars, ignore_index=True)
    summary = (
        df.groupby("rank")
        .agg(
            n_bars=("skew", "count"),
            mean_pe_iv=("pe_iv", "mean"),
            mean_ce_iv=("ce_iv", "mean"),
            mean_skew=("skew", "mean"),
            pct_put_over=("put_over_call", "mean"),
        )
        .reset_index()
    )
    summary["skew_pct"] = (summary["mean_skew"] / summary["mean_ce_iv"] * 100).round(2)
    return summary


def print_summary(summary: pd.DataFrame) -> None:
    print(f"{'rank':>5}{'n_bars':>7}{'PE_IV':>8}{'CE_IV':>8}{'skew':>8}"
          f"{'skew%':>7}{'%PE>CE':>9}")
    for _, row in summary.iterrows():
        marker = " <-- PUT PREMIUM" if row.pct_put_over > 0.55 else ""
        print(f"{int(row['rank']):>5}{int(row.n_bars):>7}"
              f"{row.mean_pe_iv:>8.4f}{row.mean_ce_iv:>8.4f}{row.mean_skew:>8.4f}"
              f"{row.skew_pct:>7.1f}%{row.pct_put_over*100:>8.0f}%{marker}")


def main() -> int:
    store = ArcticStore()
    br = store.read_borrow_rates(SYM, INTERVAL)
    oe = store.arctic.get_library("options_enriched")

    exp_map = _build_expiry_map(br, oe)

    br["spread"] = br["F1_close"] - br["F2_close"]
    qualifying = []
    for cid, g in br.groupby("cycle_id"):
        peak_spread = g["spread"].max()
        if peak_spread >= SPREAD_THRESH:
            peak_ts = g["spread"].idxmax()
            qualifying.append(dict(
                cid=int(cid),
                expiry=exp_map.get(int(cid)),
                peak_spread=peak_spread,
                peak_b12=g["b12"].max(),
                peak_ts=peak_ts,
            ))

    print(f"\nBDL HTB cycles with peak F1-F2 spread >= {SPREAD_THRESH}pt:")
    for q in qualifying:
        print(f"  cid={q['cid']}  exp={q['expiry']}  "
              f"peak_spread={q['peak_spread']:.1f}  peak_b12={q['peak_b12']:.4f}")
    print()

    all_summaries = []
    for q in qualifying:
        cid, expiry = q["cid"], q["expiry"]
        if expiry is None:
            print(f"  cid={cid}: no expiry mapping, skip")
            continue

        cyc = br[br["cycle_id"] == cid]
        window = cyc[(cyc["F1_dte"] <= DTE_HI) & (cyc["F1_dte"] >= DTE_LO)]
        if window.empty:
            print(f"  cid={cid} exp={expiry}: no bars in DTE {DTE_HI}-{DTE_LO} window, skip")
            continue

        opt = load_options(oe, expiry)
        if opt.empty:
            print(f"  cid={cid} exp={expiry}: no options data, skip")
            continue

        summary = analyze_cycle(opt, window, ITM_STRIKES)
        if summary.empty:
            print(f"  cid={cid} exp={expiry}: no valid strike pairs found, skip")
            continue

        print(f"{'='*66}")
        print(f"cid={cid}  expiry={expiry}  peak_spread={q['peak_spread']:.1f}pt  "
              f"peak_b12={q['peak_b12']:.4f}  peak@{q['peak_ts'].date()}")
        print(f"Window DTE {DTE_HI}->{DTE_LO}  ({len(window)} bars  ~"
              f"{len(window)//26} trading days)")
        print(f"rank = strike rank below ATM (1 = immediately below F1 price, 5 = furthest ITM)")
        print_summary(summary)

        summary = summary.copy()
        summary["cid"] = cid
        summary["expiry"] = expiry
        all_summaries.append(summary)

    if not all_summaries:
        print("\nNo qualifying cycles had analyzable options data.")
        return 0

    # Pooled across qualifying cycles
    pool_df = pd.concat(all_summaries, ignore_index=True)
    pool = (
        pool_df.groupby("rank")
        .agg(
            n_cycles=("cid", "nunique"),
            total_bars=("n_bars", "sum"),
            pool_pe_iv=("mean_pe_iv", "mean"),
            pool_ce_iv=("mean_ce_iv", "mean"),
            pool_skew=("mean_skew", "mean"),
            pool_pct_put_over=("pct_put_over", "mean"),
        )
        .reset_index()
    )
    pool["skew_pct"] = (pool["pool_skew"] / pool["pool_ce_iv"] * 100).round(2)

    n_cyc = pool_df["cid"].nunique()
    print(f"\n{'='*66}")
    print(f"POOLED across {n_cyc} qualifying cycles  (peak spread >= {SPREAD_THRESH}pt)")
    print(f"rank = strike rank below ATM (1=nearest ITM put / nearest OTM call)")
    print(f"{'rank':>5}{'cycles':>7}{'obs':>6}{'PE_IV':>8}{'CE_IV':>8}{'skew':>8}"
          f"{'skew%':>7}{'%PE>CE':>9}")
    for _, row in pool.iterrows():
        marker = " <-- PUT PREMIUM" if row.pool_pct_put_over > 0.55 else ""
        print(f"{int(row['rank']):>5}{int(row.n_cycles):>7}{int(row.total_bars):>6}"
              f"{row.pool_pe_iv:>8.4f}{row.pool_ce_iv:>8.4f}{row.pool_skew:>8.4f}"
              f"{row.skew_pct:>7.1f}%{row.pool_pct_put_over*100:>8.0f}%{marker}")

    # Overall signal: across all ranks, does put > call dominate?
    overall_pct = pool["pool_pct_put_over"].mean()
    overall_skew = pool["pool_skew"].mean()
    print(f"\nOverall (mean across ranks 1-{ITM_STRIKES}):")
    print(f"  Mean skew (PE_IV - CE_IV) = {overall_skew:+.4f}")
    print(f"  % bars where PE_IV > CE_IV = {overall_pct*100:.0f}%")
    if overall_pct > 0.55:
        print("  --> HYPOTHESIS SUPPORTED: consistent ITM put premium during HTB peak window")
    elif overall_pct > 0.45:
        print("  --> MIXED: slight put premium but not decisive")
    else:
        print("  --> HYPOTHESIS REJECTED: calls price at parity or above puts in this window")

    print("\nInterpretation:")
    print("  Positive skew = market pays extra for downside protection (ITM puts bid up).")
    print("  Under Black-76 with the SAME F1 as forward, put-call parity implies skew=0.")
    print("  Persistent positive skew = market either (a) uses spot as effective forward")
    print("  for puts (HTB crash risk priced into puts), or (b) genuine demand for puts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
