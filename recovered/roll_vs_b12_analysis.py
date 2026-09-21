"""
roll_vs_b12_analysis.py
========================
For each HTB cycle in RVNL and SBICARD, examine whether the b12 collapse
is mechanically caused by the futures roll (traders shifting OI from F1→F2).

Key question: Does F1 OI start declining BEFORE or AFTER the b12 peak?
If before → roll is the driver. If after → b12 peaks independently.

Outputs
-------
  plots/roll_vs_b12/
    g1_RVNL_cycles.png    — b12 + OI + roll_velocity per HTB cycle
    g2_SBICARD_cycles.png — same for SBICARD
    g3_lead_lag.png       — scatter: DTE_at_peak vs OI_ratio_at_peak (both tickers)
    g4_corr_heatmap.png   — correlation of b12_slope vs all roll features, per cycle
    g5_oi_timeline.png    — F1_oi / F2_oi aligned at b12 peak (event study)
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
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data.storage.arctic_store import ArcticStore
warnings.filterwarnings("ignore")

TICKERS    = ["RVNL", "SBICARD"]
INTERVAL   = "15minute"
SMOOTH     = 11
HTB_THRESH = 0.05
OUT_DIR    = ROOT / "plots" / "roll_vs_b12"

ROLL_FEATURES = [
    "feat_roll_velocity", "feat_roll_acceleration",
    "feat_net_roll_flow", "feat_roll_fraction",
    "feat_flow_intensity", "feat_relative_oi_change",
    "OI_ratio",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def detect_peak(cyc: pd.DataFrame) -> tuple[int, float]:
    b  = cyc["b12"].ffill().to_numpy(float)
    sm = pd.Series(b).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
    pi = int(np.nanargmax(sm))
    return pi, float(np.nanmax(sm))


def f1_oi_peak(cyc: pd.DataFrame) -> int:
    """Bar index where F1_oi is at its maximum (before roll has started)."""
    oi = cyc["F1_oi"].ffill().to_numpy(float)
    return int(np.nanargmax(oi))


def corr_safe(a: pd.Series, b: pd.Series) -> float:
    v = pd.concat([a.reset_index(drop=True), b.reset_index(drop=True)], axis=1).dropna()
    return float(v.iloc[:, 0].corr(v.iloc[:, 1])) if len(v) >= 4 else np.nan


# ── per-ticker cycle plots ────────────────────────────────────────────────────

def plot_ticker_cycles(ticker: str, htb_cycles: list[dict], out: Path) -> None:
    n = len(htb_cycles)
    if n == 0:
        return
    ncols = 2
    nrows = (n + 1) // 2
    fig = plt.figure(figsize=(18, 4.5 * nrows))
    gs  = gridspec.GridSpec(nrows, ncols, figure=fig, hspace=0.5, wspace=0.3)

    for idx, rec in enumerate(htb_cycles):
        row, col = divmod(idx, ncols)
        inner = gridspec.GridSpecFromSubplotSpec(
            3, 1, subplot_spec=gs[row, col], hspace=0.08, height_ratios=[2, 1, 1]
        )
        ax_b  = fig.add_subplot(inner[0])
        ax_oi = fig.add_subplot(inner[1], sharex=ax_b)
        ax_rv = fig.add_subplot(inner[2], sharex=ax_b)

        cyc    = rec["cyc"]
        pi     = rec["peak_i"]
        peak_b = rec["peak_b12"]
        cid    = rec["cid"]

        ts_plt = [t.tz_localize(None) if t.tzinfo is not None else t for t in cyc.index]
        pk_ts  = ts_plt[pi]

        b12_raw = cyc["b12"].ffill().values
        b12_sm  = pd.Series(b12_raw).rolling(SMOOTH, center=True, min_periods=1).mean().values
        f1_oi   = cyc["F1_oi"].ffill().values / 1e6
        f2_oi   = cyc["F2_oi"].ffill().values / 1e6
        rv      = cyc["feat_roll_velocity"].ffill().values

        # b12 panel
        ax_b.plot(ts_plt, b12_raw, color="lightgray", lw=0.8, label="raw b12")
        ax_b.plot(ts_plt, b12_sm,  color="#2c3e50",   lw=1.6, label="smooth b12")
        ax_b.axvline(pk_ts, color="red", lw=1.5, ls="--", label="b12 peak")
        ax_b.set_ylabel("b12", fontsize=8)
        ax_b.set_title(
            f"{ticker} cid={cid}  peak_b12={peak_b:.3f}  DTE@peak={rec['dte_at_peak']:.0f}  "
            f"OI_ratio@peak={rec['oi_ratio_at_peak']:.2f}  roll_frac@peak={rec['roll_frac_at_peak']:.4f}",
            fontsize=8
        )
        ax_b.legend(fontsize=7, loc="upper left")
        ax_b.tick_params(labelbottom=False)

        # OI panel
        ax_oi.fill_between(ts_plt, f1_oi, alpha=0.6, color="#185FA5", label="F1 OI (M)")
        ax_oi.fill_between(ts_plt, f2_oi, alpha=0.6, color="#e74c3c", label="F2 OI (M)")
        ax_oi.axvline(pk_ts, color="red", lw=1.5, ls="--")
        # mark F1_oi peak
        f1pi = rec["f1oi_peak_i"]
        ax_oi.axvline(ts_plt[f1pi], color="#185FA5", lw=1.2, ls=":", label="F1_oi peak")
        ax_oi.set_ylabel("OI (M lots)", fontsize=8)
        ax_oi.legend(fontsize=7, loc="upper right")
        ax_oi.tick_params(labelbottom=False)

        # Roll velocity panel
        ax_rv.plot(ts_plt, rv, color="#8e44ad", lw=1.2, label="roll_velocity")
        ax_rv.axvline(pk_ts, color="red", lw=1.5, ls="--")
        ax_rv.axhline(0, color="k", lw=0.6, ls=":")
        ax_rv.set_ylabel("roll_vel", fontsize=8)
        ax_rv.legend(fontsize=7)
        ax_rv.tick_params(axis="x", rotation=30, labelsize=7)

        # Annotate lead/lag
        lead_bars = rec["f1oi_peak_i"] - pi
        ann_txt = (f"F1_oi peak is {abs(lead_bars)} bars "
                   f"{'BEFORE' if lead_bars < 0 else 'AFTER'} b12 peak")
        ax_b.text(0.98, 0.05, ann_txt, transform=ax_b.transAxes,
                  fontsize=7, ha="right", color="#185FA5",
                  bbox=dict(fc="white", ec="none", alpha=0.7))

    fig.suptitle(
        f"{ticker} — b12 vs F1/F2 OI and roll_velocity per HTB cycle\n"
        "Red dashed = b12 peak | Blue dotted = F1_oi peak | "
        "If F1_oi peak is BEFORE b12 peak → roll precedes b12 collapse",
        fontsize=11
    )
    path = out / f"g1_{ticker}_cycles.png"
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")


# ── G3: DTE at peak vs OI_ratio at peak ───────────────────────────────────────

def plot_lead_lag(all_recs: list[dict], out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    colors    = {"RVNL": "#185FA5", "SBICARD": "#e74c3c"}

    # Panel 1: DTE at peak vs OI_ratio at peak
    ax = axes[0]
    for rec in all_recs:
        c  = colors[rec["ticker"]]
        r  = rec["oi_ratio_at_peak"]
        ax.scatter(rec["dte_at_peak"], r, color=c, s=80, alpha=0.8,
                   zorder=3, label=rec["ticker"])
        ax.text(rec["dte_at_peak"] + 0.3, r, f"c{rec['cid']}", fontsize=7, color=c)
    ax.axvline(7, color="orange", lw=1.2, ls=":", label="DTE=7 (roll starts)")
    ax.set_xlabel("DTE at b12 peak"); ax.set_ylabel("OI_ratio = F1_oi / F2_oi at peak")
    ax.set_title("DTE at b12 peak vs OI ratio\n(ratio<1 → roll mostly done; ratio>1 → F1 still dominant)")
    ax.grid(alpha=0.2)
    handles = [Line2D([0],[0], color=v, marker="o", ls="", label=k) for k,v in colors.items()]
    handles.append(Line2D([0],[0], color="orange", ls=":", label="DTE=7"))
    ax.legend(handles=handles, fontsize=8)

    # Panel 2: Lead/lag bars (F1_oi peak relative to b12 peak)
    ax = axes[1]
    for rec in all_recs:
        c  = colors[rec["ticker"]]
        ll = rec["f1oi_peak_i"] - rec["peak_i"]   # negative → F1_oi peaked before b12
        ax.bar(f"{rec['ticker']}_{rec['cid']}", ll, color=c, alpha=0.8)
    ax.axhline(0, color="k", lw=1)
    ax.set_xlabel("cycle"); ax.set_ylabel("bars (F1_oi peak − b12 peak)")
    ax.set_title("Lead/lag: F1_oi peak relative to b12 peak\n(negative = F1_oi peaked EARLIER = roll started before b12 peaked)")
    ax.tick_params(axis="x", rotation=90, labelsize=7)
    ax.grid(alpha=0.2, axis="y")

    # Panel 3: roll_fraction at b12 peak — how far into the roll is b12 peaking?
    ax = axes[2]
    for rec in all_recs:
        c = colors[rec["ticker"]]
        ax.scatter(rec["dte_at_peak"], rec["roll_frac_at_peak"],
                   color=c, s=80, alpha=0.8, zorder=3)
        ax.text(rec["dte_at_peak"] + 0.3, rec["roll_frac_at_peak"],
                f"c{rec['cid']}", fontsize=7, color=c)
    ax.axvline(7, color="orange", lw=1.2, ls=":")
    ax.set_xlabel("DTE at b12 peak"); ax.set_ylabel("feat_roll_fraction at b12 peak")
    ax.set_title("Roll fraction at b12 peak\n(how far the roll has progressed when b12 peaks)")
    ax.grid(alpha=0.2)
    ax.legend(handles=handles[:2], fontsize=8)

    fig.suptitle("Roll timing vs b12 peak — RVNL and SBICARD", fontsize=12)
    fig.tight_layout()
    path = out / "g3_lead_lag.png"
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")


# ── G4: correlation heatmap ────────────────────────────────────────────────────

def plot_corr_heatmap(all_recs: list[dict], out: Path) -> None:
    for ticker in TICKERS:
        recs = [r for r in all_recs if r["ticker"] == ticker]
        if not recs:
            continue
        cids   = [f"c{r['cid']}" for r in recs]
        feats  = ROLL_FEATURES
        matrix = np.full((len(feats), len(recs)), np.nan)

        for j, rec in enumerate(recs):
            cyc  = rec["cyc"]
            sl   = cyc["feat_b12_slope_1d"].ffill()
            for i, f in enumerate(feats):
                if f in cyc.columns:
                    matrix[i, j] = corr_safe(sl, cyc[f].ffill())

        fig, ax = plt.subplots(figsize=(max(8, len(recs) * 0.7), 6))
        im = ax.imshow(matrix, cmap="RdBu", vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(len(cids))); ax.set_xticklabels(cids, fontsize=8)
        ax.set_yticks(range(len(feats))); ax.set_yticklabels(feats, fontsize=9)
        ax.set_title(f"{ticker} — corr(b12_slope, roll_feature) per cycle\n"
                     "Red = positive (roll and b12 slope move together), "
                     "Blue = negative (anti-correlated)", fontsize=9)
        plt.colorbar(im, ax=ax, label="Pearson r")
        for i in range(len(feats)):
            for j in range(len(recs)):
                v = matrix[i, j]
                if np.isfinite(v):
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                            fontsize=6.5, color="white" if abs(v) > 0.5 else "black")
        fig.tight_layout()
        path = out / f"g4_corr_{ticker}.png"
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        print(f"  Saved {path.name}")


# ── G5: event study — OI and roll aligned at b12 peak ─────────────────────────

def plot_event_study(all_recs: list[dict], out: Path) -> None:
    """
    Align each HTB cycle at bar 0 = b12 peak.
    Plot mean ± std of: F1_oi (normalised), F2_oi (normalised), roll_velocity.
    Window: -20 bars to +20 bars around b12 peak.
    """
    WINDOW = 20
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    for ticker, ax_oi, ax_rv in zip(TICKERS,
                                     [axes[0], axes[1]],
                                     [axes[2], axes[2]]):
        recs   = [r for r in all_recs if r["ticker"] == ticker]
        if not recs:
            continue
        c      = {"RVNL": "#185FA5", "SBICARD": "#e74c3c"}[ticker]
        f1_mat = []
        f2_mat = []
        rv_mat = []

        for rec in recs:
            cyc = rec["cyc"]
            pi  = rec["peak_i"]
            n   = len(cyc)
            for bars, mat, col, norm in [
                (cyc["F1_oi"].ffill().values, f1_mat, None, True),
                (cyc["F2_oi"].ffill().values, f2_mat, None, True),
                (cyc["feat_roll_velocity"].ffill().values, rv_mat, None, False),
            ]:
                row = []
                for offset in range(-WINDOW, WINDOW + 1):
                    idx = pi + offset
                    if 0 <= idx < n:
                        v = bars[idx]
                    else:
                        v = np.nan
                    row.append(v)
                if norm and any(np.isfinite(row)):
                    peak_v = bars[pi] if np.isfinite(bars[pi]) else 1.0
                    row = [v / peak_v if (np.isfinite(v) and abs(peak_v) > 0) else np.nan
                           for v in row]
                mat.append(row)

        xs = np.arange(-WINDOW, WINDOW + 1)

        for ax, mat, lbl in [(ax_oi, f1_mat, "F1_oi / F1_oi@peak"),
                              (ax_oi, f2_mat, "F2_oi / F1_oi@peak")]:
            arr  = np.array(mat, float)
            mean = np.nanmean(arr, axis=0)
            std  = np.nanstd(arr, axis=0)
            clr  = c if "F1" in lbl else ("#e74c3c" if ticker == "RVNL" else "#27ae60")
            ax.plot(xs, mean, lw=2, color=clr, label=f"{ticker} {lbl}")
            ax.fill_between(xs, mean - std, mean + std, alpha=0.15, color=clr)

        for ax, mat, lbl in [(axes[2], rv_mat, "roll_velocity")]:
            arr  = np.array(mat, float)
            mean = np.nanmean(arr, axis=0)
            std  = np.nanstd(arr, axis=0)
            ax.plot(xs, mean, lw=2, color=c, label=f"{ticker} {lbl}")
            ax.fill_between(xs, mean - std, mean + std, alpha=0.15, color=c)

    axes[0].axvline(0, color="red", lw=1.5, ls="--", label="b12 peak (bar 0)")
    axes[0].axhline(1.0, color="k", lw=0.8, ls=":")
    axes[0].set_title("RVNL — F1/F2 OI normalised at b12 peak")
    axes[0].set_xlabel("bars relative to b12 peak")
    axes[0].set_ylabel("OI / F1_oi_at_peak")
    axes[0].legend(fontsize=8); axes[0].grid(alpha=0.2)

    axes[1].axvline(0, color="red", lw=1.5, ls="--", label="b12 peak (bar 0)")
    axes[1].axhline(1.0, color="k", lw=0.8, ls=":")
    axes[1].set_title("SBICARD — F1/F2 OI normalised at b12 peak")
    axes[1].set_xlabel("bars relative to b12 peak")
    axes[1].set_ylabel("OI / F1_oi_at_peak")
    axes[1].legend(fontsize=8); axes[1].grid(alpha=0.2)

    axes[2].axvline(0, color="red", lw=1.5, ls="--", label="b12 peak (bar 0)")
    axes[2].axhline(0, color="k", lw=0.8, ls=":")
    axes[2].set_title("roll_velocity — both tickers (mean ± 1σ)\nNegative = rolling FROM F1→F2")
    axes[2].set_xlabel("bars relative to b12 peak")
    axes[2].set_ylabel("feat_roll_velocity")
    axes[2].legend(fontsize=8); axes[2].grid(alpha=0.2)

    fig.suptitle(
        "Event study: OI and roll_velocity aligned at b12 peak (bar 0)\n"
        "Key question: Is F1_oi already falling BEFORE bar 0?",
        fontsize=11
    )
    fig.tight_layout()
    path = out / "g5_event_study.png"
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")


# ── summary table ──────────────────────────────────────────────────────────────

def print_summary(all_recs: list[dict]) -> None:
    print(f"\n{'='*120}")
    print("  ROLL vs b12 PEAK — SUMMARY")
    print(f"{'='*120}")
    print(f"  {'ticker':<8} {'cid':>4} {'peak_b12':>9} {'DTE@pk':>7} "
          f"{'OI_ratio@pk':>12} {'roll_frac@pk':>13} "
          f"{'F1oi_peak_lead':>15}  {'verdict'}")
    print(f"  {'-'*115}")
    for rec in all_recs:
        lead = rec["f1oi_peak_i"] - rec["peak_i"]
        if lead < -3:
            verdict = "ROLL PRECEDES b12 peak  (F1_oi declining before b12 peaks)"
        elif lead > 3:
            verdict = "b12 peaks BEFORE roll   (b12 peaks while F1 still dominant)"
        else:
            verdict = "simultaneous / unclear"
        print(f"  {rec['ticker']:<8} {rec['cid']:>4} {rec['peak_b12']:>9.4f} "
              f"{rec['dte_at_peak']:>7.0f} "
              f"{rec['oi_ratio_at_peak']:>12.3f} {rec['roll_frac_at_peak']:>13.5f} "
              f"{lead:>+15d}  {verdict}")

    # Summary stats
    for ticker in TICKERS:
        recs  = [r for r in all_recs if r["ticker"] == ticker]
        leads = np.array([r["f1oi_peak_i"] - r["peak_i"] for r in recs], float)
        oi_r  = np.array([r["oi_ratio_at_peak"] for r in recs if np.isfinite(r["oi_ratio_at_peak"])], float)
        dtes  = np.array([r["dte_at_peak"] for r in recs], float)
        roll_pre  = (leads < -3).sum()
        b12_first = (leads > 3).sum()
        simul     = len(recs) - roll_pre - b12_first

        print(f"\n  {ticker} ({len(recs)} HTB cycles):")
        print(f"    F1_oi peaked BEFORE b12:  {roll_pre}/{len(recs)} cycles "
              f"({100*roll_pre/len(recs):.0f}%) — roll drove b12 collapse")
        print(f"    b12 peaked BEFORE F1_oi:  {b12_first}/{len(recs)} cycles "
              f"({100*b12_first/len(recs):.0f}%) — b12 independent of roll")
        print(f"    simultaneous / unclear:   {simul}/{len(recs)} cycles")
        print(f"    DTE at b12 peak:  mean={np.nanmean(dtes):.1f}  median={np.nanmedian(dtes):.0f}  "
              f"range=[{dtes.min():.0f}, {dtes.max():.0f}]")
        print(f"    OI_ratio at peak: mean={np.nanmean(oi_r):.2f}  "
              f"(ratio<1 → F2>F1 OI → roll mostly done; >1 → F1 still dominant)")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    store    = ArcticStore()
    all_recs: list[dict] = []

    print("\nROLL vs B12 ANALYSIS — RVNL & SBICARD")
    print("Checking whether F1 futures rollover mechanically causes b12 to fall.\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for ticker in TICKERS:
        br = store.read_borrow_rates(ticker, INTERVAL)
        if br is None or br.empty:
            print(f"[{ticker}] no data"); continue
        br = br.copy()

        htb_cycles = []
        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index()
            pi, peak_b12 = detect_peak(cyc)
            if peak_b12 < HTB_THRESH:
                continue

            pk_row   = cyc.iloc[pi]
            f1oi_pi  = f1_oi_peak(cyc)
            oi_ratio = float(pk_row.get("OI_ratio", np.nan))
            rf       = float(pk_row.get("feat_roll_fraction", np.nan))
            if not np.isfinite(oi_ratio):
                f1v = float(pk_row.get("F1_oi", 1))
                f2v = float(pk_row.get("F2_oi", 0))
                oi_ratio = f1v / f2v if f2v > 0 else np.nan

            rec = {
                "ticker":            ticker,
                "cid":               int(cid),
                "cyc":               cyc,
                "peak_i":            pi,
                "peak_b12":          peak_b12,
                "dte_at_peak":       float(pk_row.get("F1_dte", np.nan)),
                "oi_ratio_at_peak":  oi_ratio,
                "roll_frac_at_peak": rf if np.isfinite(rf) else 0.0,
                "f1oi_peak_i":       f1oi_pi,
            }
            htb_cycles.append(rec)
            all_recs.append(rec)

        print(f"  {ticker}: {len(htb_cycles)} HTB cycles")
        plot_ticker_cycles(ticker, htb_cycles, OUT_DIR)

    print_summary(all_recs)

    print(f"\nGenerating summary plots → {OUT_DIR}")
    plot_lead_lag(all_recs, OUT_DIR)
    plot_corr_heatmap(all_recs, OUT_DIR)
    plot_event_study(all_recs, OUT_DIR)

    print(f"\nDone. Plots saved to {OUT_DIR}")


if __name__ == "__main__":
    main()
