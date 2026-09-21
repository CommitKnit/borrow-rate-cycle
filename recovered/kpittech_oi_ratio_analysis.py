"""
kpittech_oi_ratio_analysis.py
==============================
For every KPITTECH HTB cycle, analyse the OI ratio (F1_oi / F2_oi) at three
key moments **inside the expiry week** (DTE <= 7):

  1. SPREAD BUILD START  — first bar in the expiry week where the smoothed
                           F1-F2 spread slope turns positive and stays
                           positive for >= 2 consecutive bars (trough → rise).
                           If no such bar exists, use argmin of the spread in
                           the expiry week as a proxy.

  2. SPREAD PEAK        — argmax of the smoothed F1-F2 spread inside the
                           expiry week.

  3. COLLAPSE TRIGGER   — first bar AFTER the spread peak where:
                           slope < 0 AND accel < 0 simultaneously.
                           If not found within the expiry week, use the bar
                           of maximum negative slope post-peak.

For each moment we record:
  • OI_ratio      = F1_oi / F2_oi
  • F1_oi, F2_oi (raw lots)
  • DTE
  • spread value (raw and smoothed)
  • feat_net_roll_flow (if available)
  • feat_roll_fraction (if available)

Output:
  console: full per-cycle table + summary statistics
  plots/kpittech_oi/ : per-cycle twin-axis chart + summary scatter
"""
from __future__ import annotations
import sys, warnings
from pathlib import Path
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
warnings.filterwarnings("ignore")

from data.storage.arctic_store import ArcticStore

import argparse as _argparse
_ap = _argparse.ArgumentParser(add_help=False)
_ap.add_argument("ticker", nargs="?", default="KPITTECH")
_ARGS, _ = _ap.parse_known_args()

TICKER     = _ARGS.ticker.upper()
INTERVAL   = "15minute"
SMOOTH     = 11
HTB_THRESH = 0.05
EXPIRY_DTE = 7          # "expiry week" = last 7 calendar days
MIN_SUSTAIN = 2         # bars slope must stay positive to confirm build start
OUT_DIR    = ROOT / "plots" / f"{TICKER.lower()}_oi"


# ── helpers ────────────────────────────────────────────────────────────────────

def smooth_s(s: pd.Series, w: int = SMOOTH) -> pd.Series:
    return s.rolling(w, center=True, min_periods=1).mean()


def oi_ratio(row: pd.Series) -> float:
    f1 = float(row.get("F1_oi", np.nan))
    f2 = float(row.get("F2_oi", np.nan))
    if np.isfinite(f1) and np.isfinite(f2) and f2 > 0:
        return f1 / f2
    return np.nan


def safe_get(row: pd.Series, col: str) -> float:
    v = row.get(col, np.nan)
    return float(v) if v is not None and np.isfinite(float(v)) else np.nan


def find_build_start(sm_spread: np.ndarray) -> int:
    """
    First index where slope turns positive AND stays positive for MIN_SUSTAIN bars.
    Falls back to argmin of spread if no such run is found.
    """
    slope = np.diff(sm_spread, prepend=np.nan)
    for i in range(len(sm_spread) - MIN_SUSTAIN):
        window = slope[i:i + MIN_SUSTAIN]
        if np.all(np.isfinite(window)) and np.all(window > 0):
            return i
    return int(np.nanargmin(sm_spread))


def find_collapse_trigger(sm_spread: np.ndarray, peak_idx: int) -> int:
    """
    First bar after peak_idx where slope < 0 AND accel < 0.
    Falls back to argmin-slope bar post-peak.
    """
    slope = np.diff(sm_spread, prepend=np.nan)
    accel = np.diff(slope, prepend=np.nan)
    for i in range(peak_idx + 1, len(sm_spread)):
        s, a = slope[i], accel[i]
        if np.isfinite(s) and np.isfinite(a) and s < 0 and a < 0:
            return i
    # fallback: steepest post-peak decline
    post = slope[peak_idx + 1:]
    if len(post) and np.any(np.isfinite(post)):
        return peak_idx + 1 + int(np.nanargmin(post))
    return min(peak_idx + 1, len(sm_spread) - 1)


def moment_record(label: str, idx: int, ew: pd.DataFrame,
                  sm_spread: np.ndarray) -> dict:
    row   = ew.iloc[idx]
    ts    = ew.index[idx]
    ratio = oi_ratio(row)
    return {
        "moment":         label,
        "ts":             ts,
        "DTE":            safe_get(row, "F1_dte"),
        "OI_ratio":       ratio,
        "F1_oi":          safe_get(row, "F1_oi"),
        "F2_oi":          safe_get(row, "F2_oi"),
        "spread_raw":     float((row.get("F1_close", np.nan) or np.nan)
                                - (row.get("F2_close", np.nan) or np.nan)),
        "spread_smooth":  float(sm_spread[idx]),
        "net_roll_flow":  safe_get(row, "feat_net_roll_flow"),
        "roll_fraction":  safe_get(row, "feat_roll_fraction"),
    }


# ── core analysis ──────────────────────────────────────────────────────────────

def analyse_cycle(cid: int, cyc: pd.DataFrame, peak_b12: float) -> list[dict]:
    """Return list of 3 moment dicts (build_start, peak, collapse) for one cycle."""
    # Expiry week subset
    ew = cyc[cyc["F1_dte"] <= EXPIRY_DTE].copy()
    if len(ew) < 5:
        return []

    raw_spread  = (ew["F1_close"].ffill() - ew["F2_close"].ffill()).to_numpy(float)
    sm_arr      = smooth_s(pd.Series(raw_spread), SMOOTH).to_numpy(float)

    build_idx   = find_build_start(sm_arr)
    peak_idx    = int(np.nanargmax(sm_arr))
    collapse_idx = find_collapse_trigger(sm_arr, peak_idx)

    records = []
    for label, idx in [("BUILD_START", build_idx),
                       ("PEAK",        peak_idx),
                       ("COLLAPSE",    collapse_idx)]:
        r = moment_record(label, idx, ew, sm_arr)
        r["cid"]       = int(cid)
        r["peak_b12"]  = peak_b12
        records.append(r)
    return records


# ── plotting ───────────────────────────────────────────────────────────────────

def plot_cycle(cid: int, cyc: pd.DataFrame, moments: list[dict],
               peak_b12: float, out: Path) -> None:
    ew = cyc[cyc["F1_dte"] <= EXPIRY_DTE].copy()
    if ew.empty:
        return

    raw_spread = (ew["F1_close"].ffill() - ew["F2_close"].ffill())
    sm_spread  = smooth_s(raw_spread, SMOOTH)
    f1_oi      = ew["F1_oi"].ffill()
    f2_oi      = ew["F2_oi"].ffill()
    oi_r       = (f1_oi / f2_oi.replace(0, np.nan))

    ts_plt = [t.tz_localize(None) if t.tzinfo is not None else t
              for t in ew.index]

    htb_label = f"HTB (b12={peak_b12:.3f})" if peak_b12 >= HTB_THRESH else f"non-HTB (b12={peak_b12:.3f})"

    fig = plt.figure(figsize=(14, 9))
    gs  = gridspec.GridSpec(3, 1, figure=fig, hspace=0.12, height_ratios=[2.5, 1.5, 1])
    ax_sp  = fig.add_subplot(gs[0])
    ax_oi  = fig.add_subplot(gs[1], sharex=ax_sp)
    ax_rat = fig.add_subplot(gs[2], sharex=ax_sp)

    # — spread panel —
    ax_sp.plot(ts_plt, raw_spread.values, color="#b0b0b0", lw=0.8, label="raw spread")
    ax_sp.plot(ts_plt, sm_spread.values,  color="#1a1a1a", lw=1.8, label=f"smooth({SMOOTH}) spread")
    ax_sp.axhline(0, color="#888", lw=0.6, ls=":")

    MSTYLE = {
        "BUILD_START": dict(color="#27ae60", marker="^", label="Build start"),
        "PEAK":        dict(color="#e67e22", marker="D", label="Spread peak"),
        "COLLAPSE":    dict(color="#e74c3c", marker="v", label="Collapse trigger"),
    }
    for m in moments:
        ts_m = m["ts"]
        ts_m = ts_m.tz_localize(None) if ts_m.tzinfo is not None else ts_m
        mst  = MSTYLE[m["moment"]]
        ax_sp.axvline(ts_m, color=mst["color"], lw=1.3, ls="--", alpha=0.7)
        ax_sp.scatter([ts_m], [m["spread_smooth"]], color=mst["color"],
                      marker=mst["marker"], s=120, zorder=5, label=mst["label"])
        # annotate OI_ratio
        ratio_lbl = f"OI_r={m['OI_ratio']:.2f}" if np.isfinite(m["OI_ratio"]) else "OI_r=n/a"
        ax_sp.annotate(
            f"{m['moment']}\nDTE={m['DTE']:.0f}\n{ratio_lbl}",
            xy=(ts_m, m["spread_smooth"]),
            xytext=(8, 12), textcoords="offset points",
            fontsize=7.5, color=mst["color"],
            bbox=dict(fc="white", ec=mst["color"], alpha=0.85, pad=2),
            arrowprops=dict(arrowstyle="-", color=mst["color"], lw=0.7),
        )

    ax_sp.set_ylabel("F1 - F2 spread (₹)", fontsize=9)
    ax_sp.legend(fontsize=8, loc="upper right")
    ax_sp.set_title(
        f"KPITTECH  cid={cid}  {htb_label}  |  Expiry week (DTE ≤ {EXPIRY_DTE})\n"
        f"OI ratio at BUILD={moments[0]['OI_ratio']:.2f}  "
        f"PEAK={moments[1]['OI_ratio']:.2f}  "
        f"COLLAPSE={moments[2]['OI_ratio']:.2f}",
        fontsize=9
    )
    ax_sp.tick_params(labelbottom=False)

    # — OI absolute panel —
    ax_oi.fill_between(ts_plt, f1_oi.values / 1e3, alpha=0.55,
                       color="#185FA5", label="F1 OI (k lots)")
    ax_oi.fill_between(ts_plt, f2_oi.values / 1e3, alpha=0.55,
                       color="#e74c3c",  label="F2 OI (k lots)")
    for m in moments:
        ts_m = m["ts"]
        ts_m = ts_m.tz_localize(None) if ts_m.tzinfo is not None else ts_m
        ax_oi.axvline(ts_m, color=MSTYLE[m["moment"]]["color"], lw=1.3, ls="--", alpha=0.6)
    ax_oi.set_ylabel("OI (k lots)", fontsize=9)
    ax_oi.legend(fontsize=8, loc="upper right")
    ax_oi.tick_params(labelbottom=False)

    # — OI ratio panel —
    ax_rat.plot(ts_plt, oi_r.values, color="#8e44ad", lw=1.6, label="OI ratio F1/F2")
    ax_rat.axhline(1.0, color="k", lw=0.8, ls=":", label="ratio=1 (crossover)")
    for m in moments:
        ts_m = m["ts"]
        ts_m = ts_m.tz_localize(None) if ts_m.tzinfo is not None else ts_m
        ax_rat.axvline(ts_m, color=MSTYLE[m["moment"]]["color"], lw=1.3, ls="--", alpha=0.6)
        if np.isfinite(m["OI_ratio"]):
            ax_rat.scatter([ts_m], [m["OI_ratio"]],
                           color=MSTYLE[m["moment"]]["color"],
                           marker=MSTYLE[m["moment"]]["marker"], s=80, zorder=5)
    ax_rat.set_ylabel("F1_oi / F2_oi", fontsize=9)
    ax_rat.legend(fontsize=8)
    ax_rat.tick_params(axis="x", rotation=30, labelsize=7)
    ax_rat.margins(x=0.01)

    fig.tight_layout()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"cycle_{cid:02d}.png"
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")


def plot_summary(all_moments: list[dict], out: Path) -> None:
    """Scatter: OI ratio at each moment across all HTB cycles."""
    df = pd.DataFrame(all_moments)
    htb = df[df["peak_b12"] >= HTB_THRESH]
    if htb.empty:
        print("  No HTB cycles for summary plot"); return

    cids = sorted(htb["cid"].unique())
    x = np.arange(len(cids))

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # Panel 1: OI ratio at each moment per cycle
    ax = axes[0]
    COLORS = {"BUILD_START": "#27ae60", "PEAK": "#e67e22", "COLLAPSE": "#e74c3c"}
    for moment, c in COLORS.items():
        sub = htb[htb["moment"] == moment].set_index("cid").reindex(cids)
        ys  = sub["OI_ratio"].values
        ax.plot(x, ys, color=c, lw=1.5, marker="o", ms=7, label=moment)
        for xi, yi, cid in zip(x, ys, cids):
            if np.isfinite(yi):
                ax.annotate(f"{yi:.2f}", (xi, yi), textcoords="offset points",
                            xytext=(0, 7), ha="center", fontsize=7, color=c)
    ax.axhline(1.0, color="k", lw=0.8, ls=":", label="ratio=1 (crossover)")
    ax.set_xticks(x); ax.set_xticklabels([f"c{c}" for c in cids], fontsize=8)
    ax.set_ylabel("F1_oi / F2_oi")
    ax.set_title("OI ratio at BUILD_START / PEAK / COLLAPSE\nper KPITTECH HTB cycle")
    ax.legend(fontsize=9); ax.grid(alpha=0.2)

    # Panel 2: DTE at each moment
    ax = axes[1]
    for moment, c in COLORS.items():
        sub = htb[htb["moment"] == moment].set_index("cid").reindex(cids)
        ys  = sub["DTE"].values
        ax.plot(x, ys, color=c, lw=1.5, marker="o", ms=7, label=moment)
        for xi, yi, cid in zip(x, ys, cids):
            if np.isfinite(yi):
                ax.annotate(f"{yi:.0f}", (xi, yi), textcoords="offset points",
                            xytext=(0, 7), ha="center", fontsize=7, color=c)
    ax.axhline(3, color="orange", lw=0.8, ls=":", label="DTE=3 (post-roll)")
    ax.set_xticks(x); ax.set_xticklabels([f"c{c}" for c in cids], fontsize=8)
    ax.set_ylabel("DTE at event")
    ax.set_title("DTE at BUILD_START / PEAK / COLLAPSE\nper KPITTECH HTB cycle")
    ax.legend(fontsize=9); ax.grid(alpha=0.2)

    fig.suptitle("KPITTECH — OI ratio & DTE analysis across HTB cycles", fontsize=12)
    fig.tight_layout()
    path = out / "summary_oi_ratio.png"
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")


# ── console output ─────────────────────────────────────────────────────────────

def print_table(all_moments: list[dict]) -> None:
    df = pd.DataFrame(all_moments)

    print(f"\n{'='*130}")
    print(f"  {TICKER} — OI RATIO ANALYSIS  (expiry week DTE ≤ {EXPIRY_DTE})")
    print(f"{'='*130}")
    print(f"  {'cid':>4} {'b12_pk':>7} {'HTB':>5}  {'moment':>12}  "
          f"{'DTE':>5} {'OI_ratio':>9} {'F1_oi(k)':>9} {'F2_oi(k)':>9} "
          f"{'sprd_sm':>8} {'net_roll_flow':>14} {'roll_frac':>10}")
    print(f"  {'-'*128}")

    for _, grp in df.groupby("cid"):
        for _, row in grp.iterrows():
            htb_flag = "YES" if row["peak_b12"] >= HTB_THRESH else "no"
            f1k = row["F1_oi"] / 1e3 if np.isfinite(row["F1_oi"]) else np.nan
            f2k = row["F2_oi"] / 1e3 if np.isfinite(row["F2_oi"]) else np.nan
            oi_str  = f"{row['OI_ratio']:9.3f}" if np.isfinite(row["OI_ratio"]) else f"{'n/a':>9}"
            f1_str  = f"{f1k:9.1f}" if np.isfinite(f1k) else f"{'n/a':>9}"
            f2_str  = f"{f2k:9.1f}" if np.isfinite(f2k) else f"{'n/a':>9}"
            nrf_str = f"{row['net_roll_flow']:>14.0f}" if np.isfinite(row["net_roll_flow"]) else f"{'n/a':>14}"
            rf_str  = f"{row['roll_fraction']:>10.4f}" if np.isfinite(row["roll_fraction"]) else f"{'n/a':>10}"
            sp_str  = f"{row['spread_smooth']:>8.2f}" if np.isfinite(row["spread_smooth"]) else f"{'n/a':>8}"
            print(f"  {row['cid']:>4} {row['peak_b12']:>7.4f} {htb_flag:>5}  "
                  f"{row['moment']:>12}  {row['DTE']:>5.0f} "
                  f"{oi_str} {f1_str} {f2_str} {sp_str} {nrf_str} {rf_str}")
        print()

    # Summary stats for HTB cycles only
    htb = df[df["peak_b12"] >= HTB_THRESH]
    if htb.empty:
        print("  No HTB cycles found (peak_b12 >= 0.05). Showing non-HTB stats instead.")
        htb = df
    n_cyc = htb["cid"].nunique()

    print(f"\n{'='*130}")
    print(f"  SUMMARY — HTB cycles only ({n_cyc} cycles, peak_b12 >= {HTB_THRESH})")
    print(f"{'='*130}")
    print(f"  {'moment':>12}  {'OI_ratio_mean':>14} {'OI_ratio_median':>16} "
          f"{'OI_ratio_min':>13} {'OI_ratio_max':>13}  {'DTE_mean':>9} {'DTE_median':>11}")
    print(f"  {'-'*95}")
    for moment in ["BUILD_START", "PEAK", "COLLAPSE"]:
        sub = htb[htb["moment"] == moment]["OI_ratio"].dropna()
        dte = htb[htb["moment"] == moment]["DTE"].dropna()
        if sub.empty:
            continue
        print(f"  {moment:>12}  {sub.mean():>14.3f} {sub.median():>16.3f} "
              f"{sub.min():>13.3f} {sub.max():>13.3f}  "
              f"{dte.mean():>9.1f} {dte.median():>11.0f}")

    # Delta analysis: OI ratio change peak→collapse
    print(f"\n  ΔRATIO (PEAK → COLLAPSE) per cycle:")
    for cid, grp in htb.groupby("cid"):
        peak_r = grp[grp["moment"] == "PEAK"]["OI_ratio"].values
        coll_r = grp[grp["moment"] == "COLLAPSE"]["OI_ratio"].values
        pk_b12 = grp["peak_b12"].iloc[0]
        if len(peak_r) and len(coll_r) and np.isfinite(peak_r[0]) and np.isfinite(coll_r[0]):
            delta = coll_r[0] - peak_r[0]
            print(f"    cid={cid:>3}  b12={pk_b12:.3f}  "
                  f"PEAK ratio={peak_r[0]:.3f}  COLLAPSE ratio={coll_r[0]:.3f}  "
                  f"Δ={delta:+.3f}  "
                  f"{'(F2 overtaking F1 OI at collapse)' if coll_r[0] < 1.0 else '(F1 still dominant at collapse)'}")

    # Key findings
    build_mean = htb[htb["moment"] == "BUILD_START"]["OI_ratio"].mean()
    peak_mean  = htb[htb["moment"] == "PEAK"]["OI_ratio"].mean()
    coll_mean  = htb[htb["moment"] == "COLLAPSE"]["OI_ratio"].mean()
    print(f"\n  KEY FINDINGS:")
    print(f"    BUILD_START : avg OI ratio = {build_mean:.3f}  → "
          f"{'F1 dominant (roll not started)' if build_mean > 1.5 else 'Roll already partially under way'}")
    print(f"    PEAK        : avg OI ratio = {peak_mean:.3f}   → "
          f"{'F1 still dominant at spread peak' if peak_mean > 1.0 else 'F2 already overtook F1 at spread peak'}")
    print(f"    COLLAPSE    : avg OI ratio = {coll_mean:.3f}   → "
          f"{'F1 still > F2 OI at collapse' if coll_mean > 1.0 else 'F2 overtook F1 OI — roll complete by collapse'}")
    print(f"{'='*130}\n")


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    store = ArcticStore()
    br    = store.read_borrow_rates(TICKER, INTERVAL)
    if br is None or br.empty:
        print(f"[{TICKER}] No borrow_rates data found."); return
    br = br.sort_index()

    print(f"\n{TICKER} OI RATIO ANALYSIS")
    print(f"Total timestamps: {len(br):,}")
    print(f"Expiry week definition: DTE <= {EXPIRY_DTE}")
    print(f"HTB filter: peak_b12 >= {HTB_THRESH}\n")

    all_moments: list[dict] = []
    n_htb = 0

    for cid, cyc in br.groupby("cycle_id", sort=True):
        cyc = cyc.sort_index()
        b   = cyc["b12"].ffill().to_numpy(float)
        bsm = pd.Series(b).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
        peak_b12 = float(np.nanmax(bsm))
        is_htb   = peak_b12 >= HTB_THRESH

        moments = analyse_cycle(int(cid), cyc, peak_b12)
        if not moments:
            print(f"  cid={cid:>3}  peak_b12={peak_b12:.4f}  {'HTB' if is_htb else '   '}  "
                  f"SKIP — too few bars in expiry week")
            continue

        htb_flag = "HTB" if is_htb else "   "
        spread_vals = [m["spread_smooth"] for m in moments]
        oi_vals     = [m["OI_ratio"] for m in moments]
        print(f"  cid={cid:>3}  peak_b12={peak_b12:.4f}  {htb_flag}  "
              f"BUILD_ratio={oi_vals[0]:.2f}  PEAK_ratio={oi_vals[1]:.2f}  "
              f"COLLAPSE_ratio={oi_vals[2]:.2f}  "
              f"spread[build={spread_vals[0]:.1f} peak={spread_vals[1]:.1f}]")

        if is_htb:
            n_htb += 1
            plot_cycle(int(cid), cyc, moments, peak_b12, OUT_DIR)
        all_moments.extend(moments)

    print(f"\nTotal cycles: {br['cycle_id'].nunique()}  |  HTB cycles: {n_htb}")
    print_table(all_moments)

    plot_summary(all_moments, OUT_DIR)
    print(f"\nPlots saved to {OUT_DIR}")


if __name__ == "__main__":
    main()
