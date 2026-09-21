"""
oi_feature_importance.py
========================
Determines which OI/roll features best:
  1. PREDICT THE DIRECTION of b12 spread collapse in the last 10 DTE
     (F1 premium collapses toward F2  vs  F2 rises toward F1)
  2. LEAD THE COLLAPSE ONSET — which feature turns/peaks earliest before
     the b12 peak, giving the best early-warning signal

Scope: HTB cycles only (peak_b12 >= HTB_THRESHOLD), across all 4 tickers,
       within the last LAST_DTE days of each expiry.

Methodology
-----------
Direction prediction (Q1):
  - Target: f1_share = (−ΔF1_basis) / (|−ΔF1_basis| + |ΔF2_basis|)
    > 0.5  → F1 collapses (F1→F2); < 0.5 → F2 rises (F2→F1)
    Binary target: dir = 1 if f1_share > 0.5 else 0
  - Features measured AT the peak bar (or mean in DTE 8-20 for stability)
  - Methods: point-biserial correlation, logistic regression coefficients,
    random-forest permutation importance (when n≥20)

Lead-indicator analysis (Q2):
  - For each OI feature, compute bar-by-bar cross-correlation
    corr(Δfeature[t], Δb12[t+L]) for L in -5..+5
    Most-negative cross-lag = how many bars BEFORE the spread peak does
    the feature start turning (L>0 = feature leads)
  - Turning-point timing: feature's own turning point vs b12 peak within
    the last LAST_DTE window — median lead across cycles

Output: console tables + plots/oi_feature_importance/*.png (4 figures)
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy import stats as sp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data.storage.arctic_store import ArcticStore

warnings.filterwarnings("ignore")

# ─── config ───────────────────────────────────────────────────────────────────
TICKERS      = ["SBICARD", "RVNL", "BDL", "IREDA", "KPITTECH"]
INTERVAL     = "15minute"
HTB_THRESH   = 0.05          # min peak_b12 to be called HTB
LAST_DTE     = 10            # "last expiry week" window
SMOOTH       = 7             # bars for b12 rolling smoothing before peak detection
LAGS         = list(range(-5, 6))   # cross-correlation lags

OI_FEATURES = [
    "feat_oi_concentration",
    "OI_ratio",
    "U_t",
    "feat_roll_velocity",
    "feat_roll_acceleration",
    "feat_relative_oi_change",
    "feat_net_roll_flow",
    "feat_roll_fraction",
    "feat_flow_intensity",
    "feat_roll_deviation",
]

OUT = ROOT / "plots" / "oi_feature_importance"
OUT.mkdir(parents=True, exist_ok=True)

NICE = {
    "feat_oi_concentration":   "OI Conc\n(F1/total)",
    "OI_ratio":                "OI Ratio\n(F1/F2)",
    "U_t":                     "U_t\n(roll proxy)",
    "feat_roll_velocity":      "Roll Vel",
    "feat_roll_acceleration":  "Roll Accel",
    "feat_relative_oi_change": "Rel OI\nChange",
    "feat_net_roll_flow":      "Net Roll\nFlow",
    "feat_roll_fraction":      "Roll\nFraction",
    "feat_flow_intensity":     "Flow\nIntensity",
    "feat_roll_deviation":     "Roll\nDeviation",
}


# ─── helpers ──────────────────────────────────────────────────────────────────

def smooth_series(s: pd.Series, w: int) -> pd.Series:
    return s.rolling(w, center=True, min_periods=1).mean()


def detect_peak(cyc: pd.DataFrame, col: str = "b12") -> int | None:
    """Returns iloc index of b12 peak within the last LAST_DTE rows."""
    lw = cyc[cyc["F1_dte"] <= LAST_DTE].copy()
    if lw.empty:
        return None
    sm = smooth_series(lw[col].fillna(method="ffill"), SMOOTH)
    if sm.isna().all():
        return None
    return int(sm.values.argmax())    # iloc within lw


def direction_decompose(cyc: pd.DataFrame, peak_iloc: int) -> dict | None:
    """F1/F2/spot direction from peak to end, within the last-DTE window."""
    lw = cyc[cyc["F1_dte"] <= LAST_DTE].reset_index(drop=True)
    if peak_iloc >= len(lw) - 1:
        return None
    peak_row = lw.iloc[peak_iloc]
    end_row  = lw.iloc[-1]

    f1p, f2p, sp_ = peak_row["F1_close"], peak_row["F2_close"], peak_row["SPOT_close"]
    f1e, f2e, se  = end_row["F1_close"],  end_row["F2_close"],  end_row["SPOT_close"]

    if any(pd.isna(v) or v <= 0 for v in [f1p, f2p, sp_, f1e, f2e, se]):
        return None

    b1p = f1p - sp_; b2p = f2p - sp_
    b1e = f1e - se;  b2e = f2e - se

    db1 = b1e - b1p   # change in F1 basis  (negative = F1 fell relative to spot)
    db2 = b2e - b2p   # change in F2 basis  (positive = F2 rose relative to spot)

    neg_db1 = -db1    # contribution of F1 falling (positive is good)
    pos_db2 =  db2    # contribution of F2 rising  (positive is good)

    denom = abs(neg_db1) + abs(pos_db2)
    if denom < 1e-6:
        return None

    f1_share = neg_db1 / denom  # >0.5 = F1 collapses dominates
    direction = "F1→F2" if f1_share > 0.5 else "F2→F1"

    return {
        "f1_share": float(f1_share),
        "direction": direction,
        "dir_binary": 1 if f1_share > 0.5 else 0,
        "db1": float(db1),
        "db2": float(db2),
    }


def build_records() -> pd.DataFrame:
    store = ArcticStore()
    rows = []

    for ticker in TICKERS:
        try:
            br = store.read_borrow_rates(ticker, INTERVAL)
        except Exception:
            continue
        if br is None or br.empty:
            continue

        avail_feats = [f for f in OI_FEATURES if f in br.columns]
        br = br.copy()
        br["b12_smooth"] = smooth_series(br["b12"].fillna(method="ffill"), SMOOTH)

        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index().reset_index(drop=True)
            lw  = cyc[cyc["F1_dte"] <= LAST_DTE].reset_index(drop=True)
            if lw.empty or len(lw) < 4:
                continue

            # Peak detection within last-DTE window
            sm = smooth_series(lw["b12"].fillna(method="ffill"), SMOOTH)
            if sm.isna().all():
                continue
            peak_iloc = int(sm.values.argmax())
            peak_b12  = float(lw["b12"].iloc[peak_iloc])

            # HTB filter
            if peak_b12 < HTB_THRESH:
                continue

            # Collapse check: b12 must fall after peak
            end_b12 = float(lw["b12"].iloc[-1])
            collapse_abs = peak_b12 - end_b12
            if collapse_abs <= 0:
                continue  # no collapse
            collapse_pct = collapse_abs / abs(peak_b12) * 100

            # Direction decomposition
            dir_info = direction_decompose(cyc, peak_iloc)
            if dir_info is None:
                continue

            # Features at peak bar
            feat_vals = {}
            for f in avail_feats:
                v = lw[f].iloc[peak_iloc] if f in lw.columns else np.nan
                feat_vals[f] = float(v) if pd.notna(v) else np.nan

            # Features: mean over DTE 8-20 window (structural mid-cycle signal)
            mid = lw[(lw["F1_dte"] >= 8) & (lw["F1_dte"] <= 20)]
            feat_mid = {}
            for f in avail_feats:
                if f in mid.columns and not mid[f].isna().all():
                    feat_mid[f + "_mid"] = float(mid[f].median())
                else:
                    feat_mid[f + "_mid"] = np.nan

            # U_t peak timing (lead vs b12 peak)
            ut_lead = np.nan
            if "U_t" in lw.columns and not lw["U_t"].isna().all():
                ut_sm = smooth_series(lw["U_t"].fillna(method="ffill"), 3)
                ut_peak_iloc = int(ut_sm.values.argmax())
                ut_lead = peak_iloc - ut_peak_iloc  # > 0 = U_t peaks BEFORE b12

            # OI concentration turning point timing
            conc_lead = np.nan
            if "feat_oi_concentration" in lw.columns:
                conc = lw["feat_oi_concentration"].fillna(method="ffill")
                if not conc.isna().all():
                    # Find the local max of concentration before the b12 peak
                    pre_peak = conc.iloc[:peak_iloc + 1]
                    if len(pre_peak) > 0:
                        conc_pk = int(pre_peak.values.argmax())
                        conc_lead = peak_iloc - conc_pk  # > 0 = conc peaked before b12

            row = {
                "ticker":        ticker,
                "cycle_id":      int(cid),
                "peak_b12":      peak_b12,
                "end_b12":       end_b12,
                "collapse_abs":  collapse_abs,
                "collapse_pct":  collapse_pct,
                "peak_dte":      float(lw["F1_dte"].iloc[peak_iloc]),
                "peak_iloc":     peak_iloc,
                "n_lw_bars":     len(lw),
                "ut_lead_bars":  ut_lead,
                "conc_lead_bars": conc_lead,
                **dir_info,
                **feat_vals,
                **feat_mid,
            }
            rows.append(row)

    return pd.DataFrame(rows)


# ─── cross-correlation: which OI feature leads the b12 collapse ──────────────

def compute_lead_lag(store: ArcticStore) -> pd.DataFrame:
    """
    For each feature × lag L, compute pooled corr(Δfeature[t], Δb12[t+L])
    across all HTB cycles, within last LAST_DTE bars.
    Returns DataFrame: index=feature, columns=lag L values.
    """
    records_by_feat: dict[str, dict[int, list[tuple[float, float]]]] = {
        f: {L: [] for L in LAGS} for f in OI_FEATURES
    }

    for ticker in TICKERS:
        try:
            br = store.read_borrow_rates(ticker, INTERVAL)
        except Exception:
            continue
        if br is None or br.empty:
            continue

        avail = [f for f in OI_FEATURES if f in br.columns]
        br = br.copy()

        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index().reset_index(drop=True)
            lw = cyc[cyc["F1_dte"] <= LAST_DTE].reset_index(drop=True)
            if len(lw) < 8:
                continue

            # HTB filter
            peak_b12 = float(lw["b12"].max()) if not lw["b12"].isna().all() else 0
            if peak_b12 < HTB_THRESH:
                continue

            db12 = lw["b12"].diff().values

            for f in avail:
                dfeat = lw[f].diff().values
                for L in LAGS:
                    for t in range(len(lw)):
                        tL = t + L
                        if 0 <= tL < len(lw):
                            if np.isfinite(dfeat[t]) and np.isfinite(db12[tL]):
                                records_by_feat[f][L].append((dfeat[t], db12[tL]))

    results = {}
    for f in OI_FEATURES:
        row = {}
        for L in LAGS:
            pairs = records_by_feat[f][L]
            if len(pairs) >= 10:
                xs = [p[0] for p in pairs]
                ys = [p[1] for p in pairs]
                r, _ = sp.pearsonr(xs, ys)
                row[L] = r
            else:
                row[L] = np.nan
        results[f] = row

    return pd.DataFrame(results).T  # index=feature, cols=lags


# ─── direction prediction: per-feature stats ─────────────────────────────────

def direction_stats(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each OI feature, compute:
    - point-biserial corr with dir_binary (1=F1→F2, 0=F2→F1)
    - mean feature value in F1→F2 group vs F2→F1 group
    - Mann-Whitney U p-value
    """
    avail = [f for f in OI_FEATURES if f in df.columns]
    rows = []
    for f in avail:
        sub = df[["dir_binary", "f1_share", f]].dropna()
        if len(sub) < 4:
            continue
        d0 = sub[sub["dir_binary"] == 0][f]
        d1 = sub[sub["dir_binary"] == 1][f]

        r_pb, p_pb = sp.pointbiserialr(sub["dir_binary"], sub[f])
        r_sp, p_sp = sp.spearmanr(sub["f1_share"], sub[f])

        if len(d0) >= 3 and len(d1) >= 3:
            _, p_mw = sp.mannwhitneyu(d0, d1, alternative="two-sided")
        else:
            p_mw = np.nan

        rows.append({
            "feature":       f,
            "n":             len(sub),
            "r_pb":          r_pb,
            "p_pb":          p_pb,
            "r_spearman":    r_sp,
            "p_spearman":    p_sp,
            "p_mannwhitney": p_mw,
            "mean_F1toF2":   float(d1.mean()),
            "mean_F2toF1":   float(d0.mean()),
            "diff":          float(d1.mean() - d0.mean()),
        })
    return pd.DataFrame(rows).sort_values("r_pb", key=abs, ascending=False)


# ─── leading indicator: turning-point timing per feature ─────────────────────

def turning_point_timing(store: ArcticStore) -> pd.DataFrame:
    """
    Per feature × cycle: how many bars before the b12 peak does
    the feature reach its own local max (within the last LAST_DTE window)?
    lead = peak_b12_iloc - feature_peak_iloc   (>0 = feature peaks first)
    """
    all_rows = []

    for ticker in TICKERS:
        try:
            br = store.read_borrow_rates(ticker, INTERVAL)
        except Exception:
            continue
        if br is None or br.empty:
            continue

        avail = [f for f in OI_FEATURES if f in br.columns]
        br = br.copy()

        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index().reset_index(drop=True)
            lw = cyc[cyc["F1_dte"] <= LAST_DTE].reset_index(drop=True)
            if len(lw) < 6:
                continue

            sm = smooth_series(lw["b12"].fillna(method="ffill"), SMOOTH)
            if sm.isna().all():
                continue
            peak_b12 = float(sm.max())
            if peak_b12 < HTB_THRESH:
                continue

            # Check collapse
            b12_vals = lw["b12"].fillna(method="ffill")
            b12_peak_iloc = int(sm.values.argmax())
            if b12_peak_iloc >= len(lw) - 1:
                continue
            collapse = float(b12_vals.iloc[b12_peak_iloc]) - float(b12_vals.iloc[-1])
            if collapse <= 0:
                continue

            for f in avail:
                if f not in lw.columns:
                    continue
                fs = lw[f].fillna(method="ffill")
                if fs.isna().all():
                    continue
                sm_f = smooth_series(fs, 3)
                # Feature's turning point = last local max before or at b12 peak
                pre = sm_f.iloc[:b12_peak_iloc + 1]
                if pre.empty or pre.isna().all():
                    continue
                feat_peak_iloc = int(pre.values.argmax())
                lead = b12_peak_iloc - feat_peak_iloc  # >0 = feature peaked earlier (leads)
                all_rows.append({
                    "ticker":   ticker,
                    "cycle_id": cid,
                    "feature":  f,
                    "lead_bars": lead,
                    "peak_b12": peak_b12,
                })

    return pd.DataFrame(all_rows)


# ─── plots ────────────────────────────────────────────────────────────────────

def plot_direction_importance(dir_df: pd.DataFrame, out: Path):
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))

    # Left: point-biserial correlation bar
    feats = dir_df["feature"].tolist()
    rs    = dir_df["r_pb"].tolist()
    cols  = ["#1a7abf" if r > 0 else "#c0392b" for r in rs]
    axes[0].barh([NICE.get(f, f) for f in feats], rs, color=cols, alpha=0.85)
    axes[0].axvline(0, color="k", lw=0.8)
    for i, (r, p) in enumerate(zip(dir_df["r_pb"], dir_df["p_pb"])):
        sig = "**" if p < 0.01 else ("*" if p < 0.05 else "")
        axes[0].text(r + (0.01 if r >= 0 else -0.01),
                     i, f"{r:+.2f}{sig}", va="center",
                     ha="left" if r >= 0 else "right", fontsize=8)
    axes[0].set_xlabel("Point-biserial r with direction (F1→F2=1, F2→F1=0)")
    axes[0].set_title("OI Feature vs Collapse Direction\n"
                      "r>0: feature HIGH → F1 collapses; r<0: feature HIGH → F2 rises\n"
                      "* p<0.05  ** p<0.01")
    axes[0].grid(alpha=0.2, axis="x")

    # Right: mean values by direction group
    x = np.arange(len(feats))
    w = 0.35
    m1 = dir_df["mean_F1toF2"].tolist()
    m0 = dir_df["mean_F2toF1"].tolist()
    ax2 = axes[1]
    b1 = ax2.bar(x - w/2, m1, w, label="F1→F2", color="#e74c3c", alpha=0.8)
    b2 = ax2.bar(x + w/2, m0, w, label="F2→F1", color="#3498db", alpha=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels([NICE.get(f, f) for f in feats], fontsize=8, rotation=15, ha="right")
    ax2.set_ylabel("Mean feature value at peak")
    ax2.set_title("Feature value at peak by collapse direction\n"
                  "Large separation → stronger directional signal")
    ax2.legend()
    ax2.grid(alpha=0.2, axis="y")

    fig.suptitle("Which OI feature predicts the DIRECTION of spread collapse? (HTB cycles only)",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    p = out / "g1_direction_importance.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    print(f"  Saved: {p}")


def plot_lead_lag(ll_df: pd.DataFrame, out: Path):
    """Heat-map of cross-correlation r(Δfeature[t], Δb12[t+L]) for all features × lags."""
    feats = [f for f in OI_FEATURES if f in ll_df.index]
    lags  = [L for L in LAGS if L in ll_df.columns]
    data  = ll_df.loc[feats, lags].values.astype(float)

    fig, ax = plt.subplots(figsize=(13, 6))
    im = ax.imshow(data, aspect="auto", cmap="RdBu_r", vmin=-0.4, vmax=0.4)
    ax.set_xticks(range(len(lags)))
    ax.set_xticklabels([str(L) for L in lags])
    ax.set_yticks(range(len(feats)))
    ax.set_yticklabels([NICE.get(f, f) for f in feats], fontsize=9)
    ax.set_xlabel("Lag L  (L>0: feature change leads b12 change by L bars)\n"
                  "Most-negative (red) cell = feature DRIVES spread lower")
    ax.set_title("Cross-correlation  r(Δfeature[t], Δb12[t+L])  — HTB cycles, last 10 DTE\n"
                 "Column L>0: feature changes today → b12 changes L bars later\n"
                 "Strong red in L>0 columns: OI feature LEADS the collapse", fontsize=11)
    plt.colorbar(im, ax=ax, fraction=0.03, pad=0.02, label="Pearson r")

    # Annotate cells
    for i in range(len(feats)):
        for j in range(len(lags)):
            v = data[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                        fontsize=6.5, color="k" if abs(v) < 0.3 else "w")

    # Mark best lead lag per feature
    for i, f in enumerate(feats):
        row = data[i, :]
        if np.isfinite(row).any():
            best_j = int(np.nanargmin(row))
            best_L = lags[best_j]
            if best_L > 0:  # feature leads
                ax.add_patch(plt.Rectangle(
                    (best_j - 0.5, i - 0.5), 1, 1,
                    fill=False, edgecolor="#00aa00", lw=2.5
                ))

    fig.tight_layout()
    p = out / "g2_cross_correlation.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    print(f"  Saved: {p}")


def plot_turning_point(tp_df: pd.DataFrame, out: Path):
    """Box plot: lead_bars distribution per feature, sorted by median lead."""
    feats = [f for f in OI_FEATURES if f in tp_df["feature"].unique()]
    medians = {f: tp_df[tp_df["feature"] == f]["lead_bars"].median() for f in feats}
    feats_sorted = sorted(feats, key=lambda f: medians[f], reverse=True)

    fig, ax = plt.subplots(figsize=(13, 6))
    data = [tp_df[tp_df["feature"] == f]["lead_bars"].dropna().values for f in feats_sorted]
    bp = ax.boxplot(data, vert=False, patch_artist=True, widths=0.55)
    for patch, f in zip(bp["boxes"], feats_sorted):
        med = medians[f]
        patch.set_facecolor("#1a7abf" if med > 0 else "#c0392b")
        patch.set_alpha(0.7)
    ax.set_yticks(range(1, len(feats_sorted) + 1))
    ax.set_yticklabels([NICE.get(f, f) for f in feats_sorted], fontsize=9)
    ax.axvline(0, color="k", lw=1, ls="--", label="b12 peak (day 0)")
    ax.set_xlabel("Bars before b12 peak that feature peaks\n"
                  ">0 (blue) = feature peaks BEFORE b12 → leading indicator")
    ax.set_title("Turning-point timing: how many bars BEFORE the b12 peak does each OI feature turn?\n"
                 "HTB cycles with collapse in last 10 DTE — blue=feature leads, red=feature lags",
                 fontsize=11)
    ax.legend(loc="lower right")
    ax.grid(alpha=0.2, axis="x")

    # Add median labels
    for i, f in enumerate(feats_sorted):
        m = medians[f]
        ax.text(m + 0.15, i + 1, f"med={m:.1f}", va="center", fontsize=8)

    fig.tight_layout()
    p = out / "g3_turning_point_timing.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    print(f"  Saved: {p}")


def plot_scatter_matrix(df: pd.DataFrame, out: Path):
    """2x2 scatter: top 2 direction features vs f1_share, top 2 leading features vs collapse%."""
    avail = [f for f in OI_FEATURES if f in df.columns]
    if len(avail) < 2:
        return

    dir_corrs = [(f, abs(df[[f, "f1_share"]].dropna().corr().iloc[0, 1])) for f in avail]
    dir_corrs = sorted(dir_corrs, key=lambda x: x[1], reverse=True)
    top_dir = [f for f, _ in dir_corrs[:2]]

    lead_corrs = [(f, abs(df[[f, "collapse_pct"]].dropna().corr().iloc[0, 1])) for f in avail]
    lead_corrs = sorted(lead_corrs, key=lambda x: x[1], reverse=True)
    top_lead = [f for f, _ in lead_corrs[:2]]

    colors = {"SBICARD": "#e74c3c", "RVNL": "#3498db", "BDL": "#2ecc71",
              "IREDA": "#f39c12", "KPITTECH": "#9b59b6"}

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.ravel()

    plot_idx = 0
    for f in top_dir[:2]:
        sub = df[["ticker", f, "f1_share"]].dropna()
        ax = axes[plot_idx]
        for tkr, grp in sub.groupby("ticker"):
            ax.scatter(grp[f], grp["f1_share"],
                       color=colors.get(tkr, "grey"), label=tkr, alpha=0.7, s=50)
        ax.axhline(0.5, color="k", ls="--", lw=0.8, label="50% (equal F1/F2)")
        r, p = sp.spearmanr(sub[f], sub["f1_share"])
        ax.set_xlabel(NICE.get(f, f).replace("\n", " "))
        ax.set_ylabel("f1_share (>0.5 = F1 collapses)")
        ax.set_title(f"Direction predictor: {NICE.get(f,f).replace(chr(10),' ')}\n"
                     f"Spearman r={r:+.2f} p={p:.3f}")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.2)
        plot_idx += 1

    for f in top_lead[:2]:
        sub = df[["ticker", f, "collapse_pct"]].dropna()
        ax = axes[plot_idx]
        for tkr, grp in sub.groupby("ticker"):
            ax.scatter(grp[f], grp["collapse_pct"],
                       color=colors.get(tkr, "grey"), label=tkr, alpha=0.7, s=50)
        r, p = sp.spearmanr(sub[f], sub["collapse_pct"])
        ax.set_xlabel(NICE.get(f, f).replace("\n", " "))
        ax.set_ylabel("Collapse % (peak→expiry)")
        ax.set_title(f"Collapse magnitude predictor: {NICE.get(f,f).replace(chr(10),' ')}\n"
                     f"Spearman r={r:+.2f} p={p:.3f}")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.2)
        plot_idx += 1

    fig.suptitle("Top OI features: direction predictors (top) vs collapse-magnitude predictors (bottom)\n"
                 "All HTB cycles with collapse in last 10 DTE", fontsize=11, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    p = out / "g4_scatter_top_features.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    print(f"  Saved: {p}")


# ─── console print helpers ────────────────────────────────────────────────────

def print_direction_table(dir_df: pd.DataFrame):
    print("\n" + "=" * 90)
    print("  SECTION 1 — OI FEATURE IMPORTANCE FOR COLLAPSE DIRECTION")
    print("  Target: which leg converges — F1→F2 (F1 falls) or F2→F1 (F2 rises)?")
    print("=" * 90)
    print(f"  {'Feature':<28}{'n':>4}  {'r_pb':>7}  {'p_pb':>7}  "
          f"{'r_sp':>7}  {'p_sp':>7}  {'mean(F1→F2)':>12}  {'mean(F2→F1)':>12}  {'diff':>8}")
    print("  " + "─" * 88)
    for _, r in dir_df.iterrows():
        sig_pb = "**" if r["p_pb"] < 0.01 else ("*" if r["p_pb"] < 0.05 else "  ")
        sig_sp = "**" if r["p_spearman"] < 0.01 else ("*" if r["p_spearman"] < 0.05 else "  ")
        print(f"  {r['feature']:<28}{int(r['n']):>4}  "
              f"{r['r_pb']:>+7.3f}{sig_pb}  {r['p_pb']:>6.3f}  "
              f"{r['r_spearman']:>+7.3f}{sig_sp}  {r['p_spearman']:>6.3f}  "
              f"{r['mean_F1toF2']:>12.4f}  {r['mean_F2toF1']:>12.4f}  "
              f"{r['diff']:>+8.4f}")
    print("  " + "─" * 88)
    print("  r_pb = point-biserial (direction 0/1); r_sp = Spearman with f1_share continuous")
    print("  * p<0.05  ** p<0.01")


def print_lead_lag_table(ll_df: pd.DataFrame):
    print("\n" + "=" * 90)
    print("  SECTION 2 — CROSS-CORRELATION: corr(Δfeature[t], Δb12[t+L])")
    print("  L>0 = feature changes LEAD b12 changes by L bars")
    print("  Most-negative at L>0 = feature DRIVES spread collapse (leading indicator)")
    print("=" * 90)

    feats = [f for f in OI_FEATURES if f in ll_df.index]
    # Print lag header
    lag_str = "".join(f"{'L='+str(L):>8}" for L in LAGS)
    print(f"  {'Feature':<28}{lag_str}  {'Best L':>7}  {'Signal'}")
    print("  " + "─" * 85)
    for f in feats:
        row = ll_df.loc[f]
        vals = [row.get(L, np.nan) for L in LAGS]
        val_str = "".join(f"{v:>8.3f}" if np.isfinite(v) else "     NaN" for v in vals)
        # Best lead lag = most negative r at L > 0
        pos_vals = [(L, v) for L, v in zip(LAGS, vals) if L > 0 and np.isfinite(v)]
        if pos_vals:
            best_L, best_r = min(pos_vals, key=lambda x: x[1])
            signal = "LEADS" if best_r < -0.1 else "weak"
        else:
            best_L, best_r, signal = 0, np.nan, "n/a"
        print(f"  {f:<28}{val_str}  {best_L:>5}  {signal}  r={best_r:+.3f}" if np.isfinite(best_r)
              else f"  {f:<28}{val_str}  {best_L:>5}  {signal}")
    print("  " + "─" * 85)


def print_turning_point_table(tp_df: pd.DataFrame):
    print("\n" + "=" * 90)
    print("  SECTION 3 — TURNING-POINT TIMING (feature peak vs b12 peak)")
    print("  lead_bars > 0 = feature peaks BEFORE b12 → early-warning signal")
    print("=" * 90)

    feats = [f for f in OI_FEATURES if f in tp_df["feature"].unique()]
    summary = tp_df.groupby("feature")["lead_bars"].agg(
        n="count", median="median", mean="mean", std="std",
        pct_lead=lambda x: (x > 0).mean() * 100
    ).reset_index()
    summary = summary[summary["feature"].isin(feats)].sort_values("median", ascending=False)

    print(f"  {'Feature':<28}{'n':>4}  {'median_lead':>12}  {'mean_lead':>10}  "
          f"{'std':>7}  {'%_cycles_lead':>14}")
    print("  " + "─" * 80)
    for _, r in summary.iterrows():
        flag = " <-- LEADS" if r["median"] > 1 else ""
        print(f"  {r['feature']:<28}{int(r['n']):>4}  "
              f"{r['median']:>12.1f}  {r['mean']:>10.1f}  "
              f"{r['std']:>7.1f}  {r['pct_lead']:>13.0f}%{flag}")
    print("  " + "─" * 80)
    print("  lead_bars > 0: feature local max precedes b12 peak in the last 10 DTE window")


def print_per_ticker(df: pd.DataFrame, dir_df: pd.DataFrame):
    print("\n" + "=" * 90)
    print("  SECTION 4 — PER-TICKER BREAKDOWN (HTB collapse cycles)")
    print("=" * 90)

    for ticker in TICKERS:
        sub = df[df["ticker"] == ticker]
        if sub.empty:
            continue
        n_total   = len(sub)
        n_f1      = (sub["dir_binary"] == 1).sum()
        n_f2      = (sub["dir_binary"] == 0).sum()
        print(f"\n  [{ticker}]  n={n_total}  "
              f"F1→F2={n_f1} ({n_f1/n_total*100:.0f}%)  "
              f"F2→F1={n_f2} ({n_f2/n_total*100:.0f}%)")
        print(f"  {'cycle':>6}  {'peak_b12':>9}  {'collapse%':>10}  "
              f"{'direction':>8}  {'conc@peak':>10}  {'OI_ratio@pk':>12}  {'U_t@pk':>8}")
        print("  " + "─" * 78)
        for _, r in sub.iterrows():
            conc_s = f"{r['feat_oi_concentration']:.4f}" if "feat_oi_concentration" in r and pd.notna(r["feat_oi_concentration"]) else "  NaN"
            oir_s  = f"{r['OI_ratio']:.3f}"              if "OI_ratio" in r and pd.notna(r["OI_ratio"])               else "  NaN"
            ut_s   = f"{r['U_t']:.4f}"                   if "U_t" in r and pd.notna(r["U_t"])                         else "  NaN"
            print(f"  {int(r['cycle_id']):>6}  {r['peak_b12']:>9.4f}  "
                  f"{r['collapse_pct']:>9.1f}%  {r['direction']:>8}  "
                  f"{conc_s:>10}  {oir_s:>12}  {ut_s:>8}")


def print_verdict(dir_df: pd.DataFrame, tp_df: pd.DataFrame, ll_df: pd.DataFrame):
    print("\n" + "=" * 90)
    print("  VERDICT")
    print("=" * 90)

    # Best direction predictor
    best_dir = dir_df.iloc[0] if not dir_df.empty else None
    if best_dir is not None:
        sig = " (significant)" if best_dir["p_pb"] < 0.05 else " (not significant)"
        print(f"\n  DIRECTION PREDICTOR:")
        print(f"  Best feature = {best_dir['feature']}  r_pb={best_dir['r_pb']:+.3f}{sig}")
        if best_dir["r_pb"] > 0:
            print(f"  → When {best_dir['feature']} is HIGH at peak → F1 collapses (F1→F2)")
            print(f"  → When {best_dir['feature']} is LOW at peak  → F2 rises (F2→F1)")
        else:
            print(f"  → When {best_dir['feature']} is LOW at peak  → F1 collapses (F1→F2)")
            print(f"  → When {best_dir['feature']} is HIGH at peak → F2 rises (F2→F1)")

    # Best leading indicator
    feats_in_tp = tp_df["feature"].unique()
    tp_med = tp_df.groupby("feature")["lead_bars"].median()
    leading = tp_med[tp_med > 1].sort_values(ascending=False)
    if not leading.empty:
        best_lead_feat = leading.index[0]
        best_lead_med  = leading.iloc[0]
        pct_lead = (tp_df[tp_df["feature"] == best_lead_feat]["lead_bars"] > 0).mean() * 100
        print(f"\n  LEADING INDICATOR FOR COLLAPSE ONSET:")
        print(f"  Best feature = {best_lead_feat}")
        print(f"  → Peaks {best_lead_med:.1f} bars BEFORE the b12 peak on median")
        print(f"  → Leads b12 in {pct_lead:.0f}% of HTB collapse cycles")
    else:
        print(f"\n  LEADING INDICATOR: No feature consistently leads the b12 peak by >1 bar")

    # Cross-correlation verdict
    if not ll_df.empty:
        feats_in_ll = [f for f in OI_FEATURES if f in ll_df.index]
        best_r_lead = {}
        for f in feats_in_ll:
            pos_lags = [(L, ll_df.loc[f, L]) for L in LAGS if L > 0
                        and L in ll_df.columns and np.isfinite(ll_df.loc[f, L])]
            if pos_lags:
                best_L, best_r = min(pos_lags, key=lambda x: x[1])
                best_r_lead[f] = (best_L, best_r)
        if best_r_lead:
            best_xcorr = min(best_r_lead.items(), key=lambda x: x[1][1])
            fname, (lag, r) = best_xcorr
            if r < -0.1:
                print(f"\n  CROSS-CORRELATION LEADER:")
                print(f"  {fname}: r={r:+.3f} at L={lag}")
                print(f"  → Daily Δ{fname} predicts Δb12 {lag} bar(s) later")

    print("\n" + "=" * 90)


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    store = ArcticStore()

    print("Building per-cycle records (HTB collapse cycles only) …")
    df = build_records()
    print(f"  {len(df)} qualifying cycles across tickers: "
          f"{df.groupby('ticker').size().to_dict()}")

    if df.empty:
        print("No qualifying cycles found. Exiting.")
        return

    print("\nComputing cross-correlation lead-lag matrix …")
    ll_df = compute_lead_lag(store)

    print("Computing turning-point timing …")
    tp_df = turning_point_timing(store)

    print("Computing direction statistics …")
    dir_df = direction_stats(df)

    # ── console output ─────────────────────────────────────────────────────
    print_direction_table(dir_df)
    print_lead_lag_table(ll_df)
    print_turning_point_table(tp_df)
    print_per_ticker(df, dir_df)
    print_verdict(dir_df, tp_df, ll_df)

    # ── plots ──────────────────────────────────────────────────────────────
    print(f"\nSaving figures to {OUT} …")
    plot_direction_importance(dir_df, OUT)
    plot_lead_lag(ll_df, OUT)
    plot_turning_point(tp_df, OUT)
    plot_scatter_matrix(df, OUT)

    print(f"\nDone. Saved 4 figures to:\n  {OUT}")


if __name__ == "__main__":
    main()
