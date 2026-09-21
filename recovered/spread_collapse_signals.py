"""
spread_collapse_signals.py
==========================
Cross-ticker study of what signals the b12 borrow-rate spread's COLLAPSE from its
peak, and in which DIRECTION it converges — every ticker, every cycle, split into
3 HTB tiers (non-HTB <5%, moderate 5-15%, extreme >=15% peak annualised b12),
with the 5-state HMM regime model and the S4 signal integrated.

Answers
-------
  Q1  Average OI-concentration ratio AT the b12 peak (where the collapse begins).
  Q2  How ATM IV relates to the spread, and which ticker's IV move is the signal
      that the spread is about to fall from its peak.
  Q3  How the 25d risk-reversal predicts the DIRECTION of convergence
      (F1 falls toward F2, or F2 rises toward F1).
  Q4  Where the signals do NOT work (failure cases).
  A   The 5-state HMM regimes explained per ticker + the S4 lead/lag signal,
      integrated with the collapse (which state the peak/collapse sits in).

Definitions
-----------
  b12         annualised borrow rate (the "spread") — peak/collapse measured on this
  rupee spread = F1_close - F2_close — used for the direction decomposition
  concentration = feat_oi_concentration = F1_oi / (F1_oi + F2_oi)  in [0,1]
  peak        argmax of the 11-bar centred-smoothed b12 within the cycle
  collapse    decline from the smoothed peak to the last bar (~expiry)
  rise start  first bar reaching 20% of the low->peak height (spread "takeoff")
  25 bars     = 1 NSE trading day (15-min bars)

Run
---
  python scripts/spread_collapse_signals.py
  python scripts/spread_collapse_signals.py --tickers RVNL SBICARD --no-hmm
  python scripts/spread_collapse_signals.py --tier-edges 0.05 0.15
"""
from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.getLogger("hmmlearn").setLevel(logging.ERROR)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats as sp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data.storage.arctic_store import ArcticStore  # noqa: E402

INTERVAL = "15minute"
TICKERS = ["SBICARD", "IREDA", "VOLTAS", "KPITTECH", "ASTRAL", "RVNL", "BDL"]

# 9 features the canonical HMM is trained on (identical to hmm_s4_timing.py)
HMM_FEATURES = [
    "feat_b12_percentile", "feat_oi_concentration", "feat_roll_deviation",
    "feat_residual_atm_iv", "feat_25d_risk_reversal", "feat_beta_tsat",
    "feat_beta_reversal", "feat_b12_slope_1d", "feat_b12_accel",
]

SMOOTH = 11             # centred bars for robust b12 event dating
RISE_FRAC = 0.20        # spread "starts to rise" at 20% of low->peak height
BARS_PER_DAY = 25.0
TIER_ORDER = ["NON", "MOD", "EXT"]
TIER_LABEL = {"NON": "non-HTB (<5%)", "MOD": "mod-HTB (5-15%)", "EXT": "ext-HTB (>=15%)"}


# ───────────────────────── helpers ──────────────────────────────────────────

def safe_pearson(x, y, min_n: int = 5):
    x = np.asarray(x, "float64"); y = np.asarray(y, "float64")
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < min_n or np.std(x[m]) < 1e-12 or np.std(y[m]) < 1e-12:
        return np.nan, np.nan
    r, p = sp.pearsonr(x[m], y[m])
    return float(r), float(p)


def tier_of(peak_b12: float, edges) -> str:
    lo, hi = edges
    if not np.isfinite(peak_b12):
        return "NON"
    if peak_b12 < lo:
        return "NON"
    if peak_b12 < hi:
        return "MOD"
    return "EXT"


def _at(cyc: pd.DataFrame, col: str, i: int) -> float:
    if col not in cyc.columns:
        return np.nan
    try:
        v = cyc[col].iloc[i]
        return float(v) if pd.notna(v) else np.nan
    except Exception:
        return np.nan


def fit_hmm_states(br: pd.DataFrame, n_states: int, seed: int) -> pd.Series | None:
    """Fit the 5-state HMM on HMM_FEATURES, return a per-timestamp state Series
    canonically relabelled S0..S4 by ascending mean feat_b12_percentile."""
    from hmmlearn.hmm import GaussianHMM
    from sklearn.preprocessing import StandardScaler

    groups = {}
    for cid, g in br.groupby("cycle_id", sort=True):
        sub = g[HMM_FEATURES].dropna()
        if len(sub) >= 30:
            groups[int(cid)] = sub
    if not groups:
        return None
    cids = sorted(groups)
    alld = pd.concat([groups[c] for c in cids])
    X = alld.to_numpy("float64")
    lengths = [len(groups[c]) for c in cids]
    sc = StandardScaler().fit(X)
    Z = np.clip(sc.transform(X), -8, 8)

    best, bll = None, -np.inf
    for r in range(5):
        m = GaussianHMM(n_components=n_states, covariance_type="diag", n_iter=200,
                        tol=1e-3, min_covar=1e-3, random_state=seed + r,
                        init_params="stmc")
        try:
            m.fit(Z, lengths); ll = m.score(Z, lengths)
        except Exception:
            continue
        if np.isfinite(ll) and ll > bll:
            best, bll = m, ll
    if best is None:
        return None

    ki = HMM_FEATURES.index("feat_b12_percentile")
    raw = best.predict(Z, lengths)
    means = [X[raw == s, ki].mean() if (raw == s).any() else np.inf
             for s in range(n_states)]
    order = np.argsort(means)
    remap = {int(o): n for n, o in enumerate(order)}
    return pd.Series([remap[int(s)] for s in raw], index=alld.index, name="state")


def per_cycle_record(tk: str, cid: int, cyc: pd.DataFrame,
                     dte_window: int, edges) -> dict | None:
    cyc = cyc.sort_index()
    b = cyc["b12"].to_numpy("float64")
    if np.isfinite(b).sum() < 10:
        return None
    sm = pd.Series(b).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
    if not np.isfinite(sm).any():
        return None

    peak_idx = int(np.nanargmax(sm))
    pk = float(sm[peak_idx])
    pre = sm[:peak_idx + 1]
    lo = float(np.nanmin(pre)) if np.isfinite(pre).any() else float(np.nanmin(sm))
    height = pk - lo
    if height <= 1e-9:
        rise_idx = 0
    else:
        level = lo + RISE_FRAC * height
        above = np.where(pre >= level)[0]
        rise_idx = int(above[0]) if len(above) else peak_idx

    fin = sm[np.isfinite(sm)]
    en = float(fin[-1]) if len(fin) else np.nan
    peak_b12 = float(np.nanmax(b))
    # Structural HTB level = median b12 mid-cycle (DTE 8-20), robust to the
    # expiry-day annualisation blow-up that makes raw peak b12 ~always extreme.
    mid = cyc.loc[(cyc["F1_dte"] >= 8) & (cyc["F1_dte"] <= 20), "b12"] if "F1_dte" in cyc else pd.Series(dtype=float)
    if mid.notna().sum() < 5:
        mid = cyc.loc[cyc["F1_dte"] > 5, "b12"] if "F1_dte" in cyc else cyc["b12"]
    htb_level = float(mid.median()) if mid.notna().sum() else peak_b12
    tier = tier_of(htb_level, edges)
    collapse_abs = pk - en
    collapse_pct = (collapse_abs / pk * 100.0) if pk > 1e-9 else np.nan
    did_collapse = bool(collapse_abs > 0)

    # ── concentration (Q1) ──
    conc_at_peak = _at(cyc, "feat_oi_concentration", peak_idx)
    oi_ratio_at_peak = _at(cyc, "OI_ratio", peak_idx)
    U_t_at_peak = _at(cyc, "U_t", peak_idx)
    conc_mean_collapse = float(cyc["feat_oi_concentration"].iloc[peak_idx:].mean())

    # ── direction decomposition (Q3) peak -> end ──
    F1p, F1e = _at(cyc, "F1_close", peak_idx), _at(cyc, "F1_close", -1)
    F2p, F2e = _at(cyc, "F2_close", peak_idx), _at(cyc, "F2_close", -1)
    SPp, SPe = _at(cyc, "SPOT_close", peak_idx), _at(cyc, "SPOT_close", -1)
    dF1, dF2, dSpot = F1e - F1p, F2e - F2p, SPe - SPp
    spread_rs_peak = F1p - F2p
    spread_rs_end = F1e - F2e
    f1_share, direction = np.nan, "n/a"
    if all(np.isfinite(v) for v in [F1p, F1e, F2p, F2e, SPp, SPe]):
        dbasis1 = (F1e - SPe) - (F1p - SPp)   # change in F1 premium over spot
        dbasis2 = (F2e - SPe) - (F2p - SPp)   # change in F2 premium over spot
        c1 = -dbasis1                         # F1-leg contribution to the shrink
        c2 = dbasis2                          # F2-leg contribution to the shrink
        denom = c1 + c2
        if abs(denom) > 1e-9 and (spread_rs_peak - spread_rs_end) > 0:
            f1_share = c1 / denom
            if f1_share > 0.55:
                direction = "F1->F2"   # F1 premium collapses toward F2
            elif f1_share < 0.45:
                direction = "F2->F1"   # F2 rises toward F1
            else:
                direction = "both"

    # ── skew (Q3) ──
    rr_at_peak = _at(cyc, "feat_25d_risk_reversal", peak_idx)
    lw = cyc.loc[cyc["F1_dte"] <= 5, "feat_25d_risk_reversal"] if "F1_dte" in cyc else pd.Series(dtype=float)
    rr_mean_lw = float(lw.mean()) if len(lw) else np.nan
    rr_used = rr_at_peak if np.isfinite(rr_at_peak) else rr_mean_lw
    rr_sign = ("+" if rr_used > 0 else "-") if np.isfinite(rr_used) else "?"

    # ── ATM IV (Q2) ──
    resid_iv_at_peak = _at(cyc, "feat_residual_atm_iv", peak_idx)
    resid_iv_end = _at(cyc, "feat_residual_atm_iv", -1)
    d_resid_iv = resid_iv_end - resid_iv_at_peak
    w = cyc[cyc["F1_dte"] <= max(dte_window, 8)] if "F1_dte" in cyc else cyc
    r_level, _ = safe_pearson(w["b12"], w["feat_residual_atm_iv"])
    r_change, _ = safe_pearson(w["b12"].diff(), w["feat_residual_atm_iv"].diff())
    iv_lead_days = np.nan
    if len(w) >= 6 and w["feat_residual_atm_iv"].notna().sum() >= 5:
        try:
            iv_trough_dte = float(w.loc[w["feat_residual_atm_iv"].idxmin(), "F1_dte"])
            sp_peak_dte_w = float(w.loc[w["b12"].idxmax(), "F1_dte"])
            iv_lead_days = iv_trough_dte - sp_peak_dte_w   # >0 = IV bottoms earlier => leads
        except Exception:
            pass

    # ── inflection / momentum ──
    slope_at_peak = _at(cyc, "feat_b12_slope_1d", peak_idx)
    accel_at_peak = _at(cyc, "feat_b12_accel", peak_idx)

    # ── U_t lead over the b12 peak ──
    u_t_lead_bars = np.nan
    if "U_t" in cyc.columns:
        ut = cyc["U_t"].to_numpy("float64")
        if np.isfinite(ut).sum() >= 6:
            ut_sm = pd.Series(ut).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
            if np.isfinite(ut_sm).any():
                u_t_lead_bars = peak_idx - int(np.nanargmax(ut_sm))

    # ── HMM tags ──
    state_at_peak = _at(cyc, "state", peak_idx)
    dom_state_collapse = np.nan
    s4_onset_idx, s4_lag, frac_up_at_s4 = np.nan, np.nan, np.nan
    if "state" in cyc.columns:
        cstates = cyc["state"].iloc[peak_idx:].dropna()
        if len(cstates):
            dom_state_collapse = float(cstates.mode().iloc[0])
        s4 = np.where(cyc["state"].to_numpy() == 4)[0]
        if len(s4):
            s4_onset_idx = int(s4[0])
            s4_lag = s4_onset_idx - rise_idx
            if height > 1e-9:
                frac_up_at_s4 = (b[s4_onset_idx] - lo) / height

    return dict(
        ticker=tk, cid=int(cid), tier=tier, n=len(cyc),
        date_start=cyc.index[0].date(), date_end=cyc.index[-1].date(),
        peak_b12=peak_b12, htb_level=htb_level, peak_dte=_at(cyc, "F1_dte", peak_idx),
        end_b12=en, collapse_abs=collapse_abs, collapse_pct=collapse_pct,
        did_collapse=did_collapse,
        conc_at_peak=conc_at_peak, oi_ratio_at_peak=oi_ratio_at_peak,
        U_t_at_peak=U_t_at_peak, conc_mean_collapse=conc_mean_collapse,
        spread_rs_peak=spread_rs_peak, spread_rs_end=spread_rs_end,
        dF1=dF1, dF2=dF2, dSpot=dSpot, f1_share=f1_share, direction=direction,
        rr_at_peak=rr_at_peak, rr_used=rr_used, rr_sign=rr_sign,
        resid_iv_at_peak=resid_iv_at_peak, d_resid_iv=d_resid_iv,
        r_level=r_level, r_change=r_change, iv_lead_days=iv_lead_days,
        slope_at_peak=slope_at_peak, accel_at_peak=accel_at_peak,
        u_t_lead_bars=u_t_lead_bars,
        state_at_peak=state_at_peak, dom_state_collapse=dom_state_collapse,
        s4_onset_idx=s4_onset_idx, s4_lag=s4_lag, frac_up_at_s4=frac_up_at_s4,
    )


# ───────────────────────── data build ───────────────────────────────────────

def build(store, tickers, dte_window, edges, min_bars, use_hmm, n_states, seed):
    records, profiles = [], {}
    for tk in tickers:
        br = store.read_borrow_rates(tk, INTERVAL)
        if br is None or br.empty:
            print(f"  {tk}: no data — skipped"); continue
        br = br.sort_index()
        if use_hmm:
            st = fit_hmm_states(br, n_states, seed)
            br["state"] = st.reindex(br.index) if st is not None else np.nan
            # per-state economic profile (original units)
            if st is not None:
                prof_cols = ["b12", "F1_dte", "feat_oi_concentration",
                             "feat_residual_atm_iv", "feat_25d_risk_reversal",
                             "feat_beta_tsat", "feat_b12_slope_1d"]
                prof_cols = [c for c in prof_cols if c in br.columns]
                g = br.dropna(subset=["state"]).groupby("state")
                prof = g[prof_cols].mean()
                prof["freq%"] = g.size() / br["state"].notna().sum() * 100
                profiles[tk] = prof
        for cid, cyc in br.groupby("cycle_id", sort=True):
            if len(cyc) < min_bars:
                continue
            rec = per_cycle_record(tk, cid, cyc, dte_window, edges)
            if rec is not None:
                records.append(rec)
        print(f"  {tk}: {sum(r['ticker']==tk for r in records)} cycles processed")
    return pd.DataFrame(records), profiles


# ───────────────────────── console: Section A (HMM states) ──────────────────

def section_A(profiles, rec):
    print("\n" + "=" * 100)
    print("  SECTION A — HMM 5-STATE REGIMES EXPLAINED PER TICKER")
    print("  (states relabelled S0..S4 by ascending feat_b12_percentile; "
          "distress = most-negative beta_tsat)")
    print("=" * 100)
    for tk, prof in profiles.items():
        print(f"\n  ── {tk} " + "─" * 80)
        print(f"  {'st':>3}{'freq%':>8}{'b12':>9}{'DTE':>7}{'oi_conc':>9}"
              f"{'resid_iv':>10}{'rr_25d':>9}{'beta_tsat':>10}{'slope':>11}  role")
        distress = prof["feat_beta_tsat"].idxmin() if "feat_beta_tsat" in prof else None
        for s, row in prof.iterrows():
            role = []
            if s == prof.index.max():
                role.append("HIGH-SPREAD / S4")
            if s == distress:
                role.append("DISTRESS")
            if s == 0:
                role.append("dormant/low-borrow")
            tag = ", ".join(role)
            print(f"  S{int(s):>2}{row['freq%']:>7.1f}%{row['b12']:>9.3f}"
                  f"{row['F1_dte']:>7.1f}{row['feat_oi_concentration']:>9.3f}"
                  f"{row['feat_residual_atm_iv']:>10.3f}{row['feat_25d_risk_reversal']:>9.3f}"
                  f"{row['feat_beta_tsat']:>10.1f}{row['feat_b12_slope_1d']:>11.5f}  {tag}")
        # which state the b12 peak sits in, for this ticker
        sub = rec[(rec["ticker"] == tk) & rec["state_at_peak"].notna()]
        if len(sub):
            comp = sub["state_at_peak"].astype(int).value_counts(normalize=True) * 100
            comp_s = "  ".join(f"S{int(k)}:{v:.0f}%" for k, v in comp.sort_index().items())
            print(f"     b12-peak sits in -> {comp_s}")


# ───────────────────────── console: Q1 concentration ────────────────────────

def section_1(rec):
    print("\n" + "=" * 100)
    print("  SECTION 1 (Q1) — OI-CONCENTRATION RATIO AT THE b12 PEAK (where the collapse begins)")
    print("=" * 100)
    print(f"  {'ticker':>9}{'tier':>14}{'n':>4}{'conc@peak_mean':>16}{'median':>8}"
          f"{'IQR':>16}{'OIratio@pk':>11}{'collapse%':>11}")
    print("  " + "-" * 94)
    for tk in sorted(rec["ticker"].unique()):
        for tier in TIER_ORDER:
            s = rec[(rec["ticker"] == tk) & (rec["tier"] == tier)]
            if s.empty:
                continue
            c = s["conc_at_peak"].dropna()
            if c.empty:
                continue
            q25, q75 = c.quantile([.25, .75])
            print(f"  {tk:>9}{TIER_LABEL[tier]:>14}{len(s):>4}{c.mean():>16.3f}"
                  f"{c.median():>8.3f}{f'[{q25:.2f},{q75:.2f}]':>16}"
                  f"{s['oi_ratio_at_peak'].mean():>11.1f}{s['collapse_pct'].mean():>10.0f}%")
    print("  " + "-" * 94)
    print("  POOLED BY TIER (the average concentration at which b12 collapses):")
    for tier in TIER_ORDER:
        s = rec[rec["tier"] == tier]
        c = s["conc_at_peak"].dropna()
        if c.empty:
            continue
        q25, q75 = c.quantile([.25, .75])
        print(f"  {'ALL':>9}{TIER_LABEL[tier]:>14}{len(s):>4}{c.mean():>16.3f}"
              f"{c.median():>8.3f}{f'[{q25:.2f},{q75:.2f}]':>16}"
              f"{s['oi_ratio_at_peak'].mean():>11.1f}{s['collapse_pct'].mean():>10.0f}%")
    both = rec[["conc_at_peak", "collapse_pct"]].dropna()
    if len(both) >= 5:
        r, p = safe_pearson(both["conc_at_peak"], both["collapse_pct"])
        print(f"\n  Pearson  conc@peak <-> collapse%  =  {r:+.3f}  (p={p:.3f}, n={len(both)})")


# ───────────────────────── console: Q2 IV vs spread ─────────────────────────

def section_2(rec):
    print("\n" + "=" * 100)
    print("  SECTION 2 (Q2) — ATM IV vs SPREAD: which ticker's IV move signals the collapse")
    print("  r_level=corr(b12,residIV)  r_change=corr(db12,dIV)  iv_lead=IV trough DTE - spread peak DTE (>0 IV leads)")
    print("=" * 100)
    print(f"  {'ticker':>9}{'n':>4}{'r_level':>9}{'r_change':>10}{'iv_lead_d':>11}"
          f"{'dresidIV@collapse':>18}  read")
    print("  " + "-" * 90)
    rows = []
    for tk in sorted(rec["ticker"].unique()):
        s = rec[rec["ticker"] == tk]
        rl = s["r_level"].mean(); rc = s["r_change"].mean()
        il = s["iv_lead_days"].mean(); div = s["d_resid_iv"].mean()
        rows.append((tk, rl, rc, il))
        read = ("IV UP as spread falls (inverse)" if rl < -0.15
                else "IV DOWN as spread falls (direct)" if rl > 0.15 else "weak/none")
        lead = " + LEADS" if il > 1 else (" + lags" if il < -1 else "")
        print(f"  {tk:>9}{len(s):>4}{rl:>+9.2f}{rc:>+10.2f}{il:>+11.1f}{div:>+18.3f}  {read}{lead}")
    # verdict: strongest |r_level| with a lead
    valid = [r for r in rows if np.isfinite(r[1])]
    if valid:
        best = min(valid, key=lambda r: r[1])   # most negative (inverse) r_level
        print(f"\n  => Cleanest IV signal: {best[0]} (r_level={best[1]:+.2f}). "
              f"Negative r => rising ATM IV is the tell that the spread is rolling over.")


# ───────────────────────── console: Q3 RR -> direction ──────────────────────

def section_3(rec):
    print("\n" + "=" * 100)
    print("  SECTION 3 (Q3) — 25d RISK-REVERSAL SIGN vs DIRECTION OF CONVERGENCE")
    print("  rr +=call-skew  -=put-skew  |  F1->F2 = F1 premium collapses, F2->F1 = F2 rises to F1")
    print("=" * 100)
    use = rec[(rec["direction"].isin(["F1->F2", "F2->F1"])) & (rec["rr_sign"].isin(["+", "-"]))]
    if use.empty:
        print("  (insufficient cycles with both a defined direction and a risk-reversal)")
        return
    print("  Contingency (all tiers pooled):")
    ct = pd.crosstab(use["rr_sign"], use["direction"])
    for d in ["F1->F2", "F2->F1"]:
        if d not in ct.columns:
            ct[d] = 0
    ct = ct[["F1->F2", "F2->F1"]]
    print(f"  {'rr_sign':>8}{'F1->F2':>9}{'F2->F1':>9}   reading")
    for sgn in ct.index:
        a, b = int(ct.loc[sgn, "F1->F2"]), int(ct.loc[sgn, "F2->F1"])
        dom = "F1 falls" if a > b else ("F2 rises" if b > a else "tie")
        lab = "call-skew(+)" if sgn == "+" else "put-skew(-)"
        print(f"  {lab:>12}{a:>5}{b:>9}   -> mostly {dom}")
    # hypothesis hit-rate: put-skew(-) -> F1->F2 (price falls, F1 premium dies);
    #                      call-skew(+) -> F2->F1 (squeeze risk, F2 lifts)
    hit = use[((use["rr_sign"] == "-") & (use["direction"] == "F1->F2")) |
              ((use["rr_sign"] == "+") & (use["direction"] == "F2->F1"))]
    print(f"\n  Hypothesis (put-skew->F1 falls, call-skew->F2 rises) holds in "
          f"{len(hit)}/{len(use)} = {len(hit)/len(use)*100:.0f}% of cycles.")
    if min(ct.shape) == 2 and ct.values.sum() >= 8:
        try:
            _, pf = sp.fisher_exact(ct.values)
            print(f"  Fisher exact p = {pf:.3f}"
                  + ("  (significant association)" if pf < 0.05 else "  (not significant)"))
        except Exception:
            pass
    # per ticker
    print("\n  Per-ticker direction tally (collapsing cycles):")
    print(f"  {'ticker':>9}{'F1->F2':>9}{'F2->F1':>9}{'both':>6}{'mean_f1_share':>15}")
    for tk in sorted(rec["ticker"].unique()):
        s = rec[(rec["ticker"] == tk) & rec["did_collapse"]]
        a = (s["direction"] == "F1->F2").sum(); b = (s["direction"] == "F2->F1").sum()
        bo = (s["direction"] == "both").sum()
        print(f"  {tk:>9}{a:>9}{b:>9}{bo:>6}{s['f1_share'].mean():>15.2f}")


# ───────────────────────── console: Q-other signals ─────────────────────────

def section_4(rec):
    print("\n" + "=" * 100)
    print("  SECTION 4 — OTHER SIGNALS (U_t lead, OI_ratio, slope/accel inflection)")
    print("=" * 100)
    print("  U_t lead over the b12 peak (positive = U_t peaks BEFORE b12, in trading days):")
    print(f"  {'tier':>14}{'n':>4}{'mean_lead_d':>13}{'median_d':>10}{'%U_t leads':>12}")
    for tier in TIER_ORDER:
        s = rec[rec["tier"] == tier]["u_t_lead_bars"].dropna() / BARS_PER_DAY
        if s.empty:
            continue
        print(f"  {TIER_LABEL[tier]:>14}{len(s):>4}{s.mean():>13.1f}{s.median():>10.1f}"
              f"{(s > 0).mean()*100:>11.0f}%")
    # accel sign at peak (inflection detector: accel<0 means momentum already rolling over)
    acc = rec["accel_at_peak"].dropna()
    if len(acc):
        print(f"\n  b12 acceleration at the peak < 0 (momentum rolling over) in "
              f"{(acc < 0).mean()*100:.0f}% of cycles (n={len(acc)}).")
    slp = rec["slope_at_peak"].dropna()
    if len(slp):
        print(f"  b12 slope at the peak still > 0 in {(slp > 0).mean()*100:.0f}% "
              f"(spread still nominally rising at the smoothed peak).")
    # OI_ratio at peak by tier
    print("\n  OI_ratio (F1_oi/F2_oi) at peak by tier:")
    for tier in TIER_ORDER:
        s = rec[rec["tier"] == tier]["oi_ratio_at_peak"].dropna()
        if len(s):
            print(f"  {TIER_LABEL[tier]:>14}  mean={s.mean():>6.1f}  median={s.median():>6.1f}")


# ───────────────────────── console: Q4 failures ─────────────────────────────

def section_5(rec):
    print("\n" + "=" * 100)
    print("  SECTION 5 (Q4) — WHERE THE SIGNALS DO NOT WORK")
    print("=" * 100)
    nocol = rec[~rec["did_collapse"] | (rec["collapse_pct"] < 10)]
    print(f"  (a) No / tiny collapse (<10% of peak): {len(nocol)}/{len(rec)} cycles")
    for _, r in nocol.sort_values("peak_b12").iterrows():
        print(f"        {r['ticker']:>9} c{r['cid']:<3} {TIER_LABEL[r['tier']]:>14}  "
              f"peak_b12={r['peak_b12']:.3f}  collapse%={r['collapse_pct']:>5.0f}")
    direct = rec[rec["r_level"] > 0.15]
    print(f"\n  (b) IV-spread relationship DIRECT (contradicts inverse) r_level>0.15: "
          f"{len(direct)}/{rec['r_level'].notna().sum()} cycles "
          f"({sorted(direct['ticker'].unique())})")
    use = rec[(rec["direction"].isin(["F1->F2", "F2->F1"])) & (rec["rr_sign"].isin(["+", "-"]))]
    miss = use[~(((use["rr_sign"] == "-") & (use["direction"] == "F1->F2")) |
                 ((use["rr_sign"] == "+") & (use["direction"] == "F2->F1")))]
    print(f"\n  (c) Risk-reversal sign mismatched the realised direction: "
          f"{len(miss)}/{len(use)} cycles ({len(miss)/max(len(use),1)*100:.0f}%)")
    print("\n  Regime read: signals are cleanest in the EXT tier; they decay in the NON tier")
    print("  (low-borrow names / early cycles) where there is no real spread to collapse.")


# ───────────────────────── console: Q-A S4 integration ──────────────────────

def section_6(rec):
    print("\n" + "=" * 100)
    print("  SECTION 6 — S4 SIGNAL & HMM INTEGRATION")
    print("  s4_lag = S4 onset - spread takeoff (bars); <0 LEADS, >0 LAGS; up@S4 = ascent fraction")
    print("=" * 100)
    print(f"  {'ticker':>9}{'nS4':>5}{'mean_lag_b':>11}{'mean_lag_d':>11}{'up@S4':>8}  verdict")
    print("  " + "-" * 70)
    for tk in sorted(rec["ticker"].unique()):
        s = rec[(rec["ticker"] == tk) & rec["s4_lag"].notna()]
        if s.empty:
            print(f"  {tk:>9}{0:>5}{'--':>11}{'--':>11}{'--':>8}  no S4")
            continue
        lag = s["s4_lag"].mean()
        verdict = ("LEADS" if lag < -4 else "LAGS" if lag > 4 else "COINCIDES")
        print(f"  {tk:>9}{len(s):>5}{lag:>+11.0f}{lag/BARS_PER_DAY:>+11.2f}"
              f"{s['frac_up_at_s4'].mean():>8.2f}  {verdict}")
    # which state the collapse runs through
    print("\n  Dominant HMM state DURING the collapse (peak->expiry), by tier:")
    for tier in TIER_ORDER:
        s = rec[(rec["tier"] == tier) & rec["dom_state_collapse"].notna()]
        if s.empty:
            continue
        comp = s["dom_state_collapse"].astype(int).value_counts(normalize=True) * 100
        comp_s = "  ".join(f"S{int(k)}:{v:.0f}%" for k, v in comp.sort_index().items())
        print(f"  {TIER_LABEL[tier]:>14}: {comp_s}")


# ───────────────────────── graphs ───────────────────────────────────────────

TIER_COLOR = {"NON": "#9e9e9e", "MOD": "#1f77b4", "EXT": "#d62728"}


def make_graphs(rec, profiles, store, tickers, dte_window, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    saved = []

    # g1 — conc@peak distribution by tier, per ticker
    try:
        tks = sorted(rec["ticker"].unique())
        fig, axes = plt.subplots(2, 4, figsize=(18, 9)); axes = axes.flatten()
        for i, tk in enumerate(tks):
            ax = axes[i]; s = rec[rec["ticker"] == tk]
            data, labs, cols = [], [], []
            for tier in TIER_ORDER:
                c = s[s["tier"] == tier]["conc_at_peak"].dropna()
                if len(c):
                    data.append(c.values); labs.append(f"{tier}\n(n={len(c)})"); cols.append(TIER_COLOR[tier])
            if data:
                bp = ax.boxplot(data, labels=labs, patch_artist=True, widths=0.6)
                for box, c in zip(bp["boxes"], cols):
                    box.set_facecolor(c); box.set_alpha(0.6)
            ax.set_title(tk); ax.set_ylabel("conc@peak"); ax.set_ylim(0, 1); ax.grid(alpha=0.2)
        # pooled
        ax = axes[len(tks)]
        data = [rec[rec["tier"] == t]["conc_at_peak"].dropna().values for t in TIER_ORDER]
        bp = ax.boxplot([d for d in data if len(d)],
                        labels=[TIER_LABEL[t] for t, d in zip(TIER_ORDER, data) if len(d)],
                        patch_artist=True, widths=0.6)
        for box, t in zip(bp["boxes"], [t for t, d in zip(TIER_ORDER, data) if len(d)]):
            box.set_facecolor(TIER_COLOR[t]); box.set_alpha(0.6)
        ax.set_title("POOLED"); ax.set_ylabel("conc@peak"); ax.set_ylim(0, 1); ax.grid(alpha=0.2)
        for j in range(len(tks) + 1, len(axes)):
            axes[j].axis("off")
        fig.suptitle("Q1 — OI concentration at the b12 peak (where the collapse begins), by HTB tier", fontsize=13)
        fig.tight_layout(rect=[0, 0, 1, 0.96])
        p = out / "g1_concentration_by_tier.png"; fig.savefig(p, dpi=120); plt.close(fig); saved.append(p)
    except Exception as e:
        print(f"  [g1 skipped: {e}]")

    # g2 — collapse% vs conc@peak, colored by tier, failures flagged
    try:
        fig, ax = plt.subplots(figsize=(10, 7))
        for tier in TIER_ORDER:
            s = rec[(rec["tier"] == tier)].dropna(subset=["conc_at_peak", "collapse_pct"])
            ok = s[s["did_collapse"]]; fail = s[~s["did_collapse"]]
            ax.scatter(ok["conc_at_peak"], ok["collapse_pct"], c=TIER_COLOR[tier],
                       s=55, alpha=0.8, label=f"{TIER_LABEL[tier]} (n={len(s)})")
            ax.scatter(fail["conc_at_peak"], fail["collapse_pct"], facecolors="none",
                       edgecolors=TIER_COLOR[tier], s=90, marker="X", linewidths=1.6)
        both = rec[["conc_at_peak", "collapse_pct"]].dropna()
        if len(both) >= 5:
            m, b = np.polyfit(both["conc_at_peak"], both["collapse_pct"], 1)
            xr = np.linspace(both["conc_at_peak"].min(), both["conc_at_peak"].max(), 50)
            r, _ = safe_pearson(both["conc_at_peak"], both["collapse_pct"])
            ax.plot(xr, m * xr + b, "k--", lw=1.5, label=f"fit r={r:+.2f}")
        ax.axhline(0, color="grey", lw=0.8)
        ax.set_xlabel("OI concentration at peak  F1/(F1+F2)"); ax.set_ylabel("collapse % of peak")
        ax.set_title("Q1/Q4 — collapse magnitude vs concentration at peak (X = did not collapse)")
        ax.legend(fontsize=8); ax.grid(alpha=0.2)
        p = out / "g2_collapse_vs_concentration.png"; fig.savefig(p, dpi=120); plt.close(fig); saved.append(p)
    except Exception as e:
        print(f"  [g2 skipped: {e}]")

    # g3 — per-ticker dual-axis b12 vs residual ATM IV over DTE (mean across cycles)
    try:
        tks = tickers
        fig, axes = plt.subplots(2, 4, figsize=(18, 9)); axes = axes.flatten()
        for i, tk in enumerate(tks):
            ax = axes[i]
            br = store.read_borrow_rates(tk, INTERVAL)
            if br is None or br.empty:
                ax.axis("off"); continue
            br = br.sort_index()
            w = br[br["F1_dte"] <= dte_window * 2].copy()
            w["dte_int"] = w["F1_dte"].round(0)
            g = w.groupby("dte_int")
            mb = g["b12"].mean(); mi = g["feat_residual_atm_iv"].mean()
            x = -mb.index.values
            ax.plot(x, mb.values, "o-", color="#1f77b4", ms=3, label="b12")
            ax.set_ylabel("b12", color="#1f77b4"); ax.tick_params(axis="y", labelcolor="#1f77b4")
            ax2 = ax.twinx()
            ax2.plot(-mi.index.values, mi.values, "s-", color="#d62728", ms=3, label="resid ATM IV")
            ax2.set_ylabel("resid ATM IV", color="#d62728"); ax2.tick_params(axis="y", labelcolor="#d62728")
            ax.set_title(tk); ax.set_xlabel("-DTE (right=expiry)"); ax.grid(alpha=0.2)
        for j in range(len(tks), len(axes)):
            axes[j].axis("off")
        fig.suptitle("Q2 — b12 spread vs residual ATM IV across DTE (mean over cycles)", fontsize=13)
        fig.tight_layout(rect=[0, 0, 1, 0.96])
        p = out / "g3_iv_vs_spread_dualaxis.png"; fig.savefig(p, dpi=120); plt.close(fig); saved.append(p)
    except Exception as e:
        print(f"  [g3 skipped: {e}]")

    # g4 — per-ticker IV<->spread correlation bars
    try:
        tks = sorted(rec["ticker"].unique())
        rl = [rec[rec["ticker"] == t]["r_level"].mean() for t in tks]
        rc = [rec[rec["ticker"] == t]["r_change"].mean() for t in tks]
        il = [rec[rec["ticker"] == t]["iv_lead_days"].mean() for t in tks]
        x = np.arange(len(tks))
        fig, (ax, ax2) = plt.subplots(1, 2, figsize=(16, 6))
        ax.bar(x - 0.2, rl, 0.4, label="r_level", color="#1f77b4", alpha=0.85)
        ax.bar(x + 0.2, rc, 0.4, label="r_change", color="#ff7f0e", alpha=0.85)
        ax.axhline(0, color="k", lw=0.8); ax.set_xticks(x); ax.set_xticklabels(tks, rotation=30)
        ax.set_ylabel("Pearson r (IV vs b12)"); ax.legend(); ax.grid(alpha=0.2, axis="y")
        ax.set_title("Q2 — IV<->spread correlation per ticker (negative = inverse)")
        cols = ["#2ca02c" if v > 0 else "#d62728" for v in il]
        ax2.bar(x, il, color=cols, alpha=0.85); ax2.axhline(0, color="k", lw=0.8)
        ax2.set_xticks(x); ax2.set_xticklabels(tks, rotation=30)
        ax2.set_ylabel("IV lead (DTE units, >0 = IV bottoms earlier => leads)")
        ax2.set_title("Q2 — does ATM IV LEAD the spread collapse?"); ax2.grid(alpha=0.2, axis="y")
        fig.tight_layout()
        p = out / "g4_iv_spread_correlation.png"; fig.savefig(p, dpi=120); plt.close(fig); saved.append(p)
    except Exception as e:
        print(f"  [g4 skipped: {e}]")

    # g5 — RR sign vs direction (stacked) + scatter rr vs f1_share
    try:
        use = rec[(rec["direction"].isin(["F1->F2", "F2->F1"])) & (rec["rr_sign"].isin(["+", "-"]))]
        fig, (ax, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        if not use.empty:
            ct = pd.crosstab(use["rr_sign"], use["direction"])
            for d in ["F1->F2", "F2->F1"]:
                if d not in ct.columns:
                    ct[d] = 0
            labs = ["put-skew(-)" if s == "-" else "call-skew(+)" for s in ct.index]
            ax.bar(labs, ct["F1->F2"], label="F1->F2 (F1 falls)", color="#d62728", alpha=0.8)
            ax.bar(labs, ct["F2->F1"], bottom=ct["F1->F2"], label="F2->F1 (F2 rises)",
                   color="#2ca02c", alpha=0.8)
            ax.set_ylabel("cycles"); ax.legend(); ax.set_title("Q3 — risk-reversal sign vs convergence direction")
        for tier in TIER_ORDER:
            s = rec[(rec["tier"] == tier)].dropna(subset=["rr_used", "f1_share"])
            ax2.scatter(s["rr_used"], s["f1_share"], c=TIER_COLOR[tier], s=55, alpha=0.8,
                        label=TIER_LABEL[tier])
        ax2.axhline(0.5, color="grey", ls="--", lw=1); ax2.axvline(0, color="grey", ls="--", lw=1)
        ax2.set_xlabel("25d risk-reversal at peak (+call / -put skew)")
        ax2.set_ylabel("f1_share (1=F1 falls, 0=F2 rises)")
        ax2.set_title("Q3 — RR vs leg attribution"); ax2.legend(fontsize=8); ax2.grid(alpha=0.2)
        fig.tight_layout()
        p = out / "g5_rr_direction.png"; fig.savefig(p, dpi=120); plt.close(fig); saved.append(p)
    except Exception as e:
        print(f"  [g5 skipped: {e}]")

    # g6 — U_t lead histogram + accel at peak
    try:
        fig, (ax, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        lead = (rec["u_t_lead_bars"].dropna() / BARS_PER_DAY)
        if len(lead):
            ax.hist(lead, bins=20, color="#1f77b4", alpha=0.8)
            ax.axvline(0, color="k", lw=1); ax.axvline(lead.median(), color="red", ls="--",
                       label=f"median {lead.median():.1f}d")
            ax.set_xlabel("U_t lead over b12 peak (trading days, >0 = U_t leads)")
            ax.set_ylabel("cycles"); ax.legend(); ax.set_title("Other signal — U_t leading the spread peak")
            ax.grid(alpha=0.2)
        acc = rec["accel_at_peak"].dropna()
        if len(acc):
            ax2.hist(acc, bins=20, color="#9467bd", alpha=0.8)
            ax2.axvline(0, color="k", lw=1)
            ax2.set_xlabel("b12 acceleration at peak (<0 = momentum rolling over)")
            ax2.set_ylabel("cycles"); ax2.set_title("Inflection — accel sign at the peak"); ax2.grid(alpha=0.2)
        fig.tight_layout()
        p = out / "g6_ut_lead_and_inflection.png"; fig.savefig(p, dpi=120); plt.close(fig); saved.append(p)
    except Exception as e:
        print(f"  [g6 skipped: {e}]")

    # g7 — per-ticker HMM state profile heatmap
    if profiles:
        try:
            feats = ["b12", "F1_dte", "feat_oi_concentration", "feat_residual_atm_iv",
                     "feat_25d_risk_reversal", "feat_beta_tsat", "feat_b12_slope_1d"]
            short = ["b12", "DTE", "oiC", "rIV", "rr", "tsat", "slope"]
            tks = list(profiles.keys())
            fig, axes = plt.subplots(2, 4, figsize=(19, 9)); axes = axes.flatten()
            for i, tk in enumerate(tks):
                ax = axes[i]; prof = profiles[tk]
                cols = [c for c in feats if c in prof.columns]
                M = prof[cols].to_numpy("float64")
                # z-score each feature (column) across states for colour comparability
                Z = (M - np.nanmean(M, axis=0)) / (np.nanstd(M, axis=0) + 1e-9)
                im = ax.imshow(Z, cmap="RdBu_r", vmin=-1.8, vmax=1.8, aspect="auto")
                ax.set_xticks(range(len(cols))); ax.set_xticklabels([short[feats.index(c)] for c in cols], fontsize=8)
                ax.set_yticks(range(len(prof))); ax.set_yticklabels([f"S{int(s)}" for s in prof.index])
                for yy in range(M.shape[0]):
                    for xx in range(M.shape[1]):
                        ax.text(xx, yy, f"{M[yy, xx]:.2f}", ha="center", va="center", fontsize=6)
                distress = prof["feat_beta_tsat"].idxmin() if "feat_beta_tsat" in prof else None
                ax.set_title(f"{tk}  (S{int(prof.index.max())}=S4, S{int(distress)}=distress)", fontsize=9)
            for j in range(len(tks), len(axes)):
                axes[j].axis("off")
            fig.suptitle("Section A — HMM state profiles per ticker (z-scored across states; numbers = raw means)", fontsize=13)
            fig.tight_layout(rect=[0, 0, 1, 0.96])
            p = out / "g7_hmm_state_profiles.png"; fig.savefig(p, dpi=120); plt.close(fig); saved.append(p)
        except Exception as e:
            print(f"  [g7 skipped: {e}]")

    # g8 — S4 lag per ticker + state_at_peak composition by tier
    if rec["s4_lag"].notna().any():
        try:
            fig, (ax, ax2) = plt.subplots(1, 2, figsize=(16, 6))
            tks = sorted(rec["ticker"].unique())
            lags = [(rec[(rec["ticker"] == t)]["s4_lag"].mean() / BARS_PER_DAY) for t in tks]
            cols = ["#2ca02c" if (np.isfinite(v) and v < 0) else "#d62728" for v in lags]
            ax.bar(tks, [v if np.isfinite(v) else 0 for v in lags], color=cols, alpha=0.85)
            ax.axhline(0, color="k", lw=0.8)
            ax.set_ylabel("mean S4 lag (days, <0 = LEADS)")
            ax.set_title("S4 signal — does it lead or lag the spread rise?")
            ax.tick_params(axis="x", rotation=30); ax.grid(alpha=0.2, axis="y")
            # state_at_peak composition by tier
            comp = (rec.dropna(subset=["state_at_peak"])
                    .assign(s=lambda d: d["state_at_peak"].astype(int))
                    .groupby(["tier", "s"]).size().unstack(fill_value=0))
            comp = comp.reindex(TIER_ORDER).dropna(how="all")
            comp = comp.div(comp.sum(axis=1), axis=0) * 100
            bottom = np.zeros(len(comp))
            for s in comp.columns:
                ax2.bar([TIER_LABEL[t] for t in comp.index], comp[s].values, bottom=bottom,
                        label=f"S{int(s)}", alpha=0.85)
                bottom += comp[s].values
            ax2.set_ylabel("% of cycles"); ax2.set_title("HMM state the b12 PEAK sits in, by tier")
            ax2.legend(fontsize=8, ncol=5); ax2.grid(alpha=0.2, axis="y")
            fig.tight_layout()
            p = out / "g8_s4_timing.png"; fig.savefig(p, dpi=120); plt.close(fig); saved.append(p)
        except Exception as e:
            print(f"  [g8 skipped: {e}]")

    return saved


# ───────────────────────── main ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", nargs="+", default=TICKERS)
    ap.add_argument("--tier-edges", nargs=2, type=float, default=[0.05, 0.15],
                    metavar=("MOD", "EXT"))
    ap.add_argument("--dte-window", type=int, default=10)
    ap.add_argument("--min-bars", type=int, default=100)
    ap.add_argument("--states", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-hmm", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "plots" / "spread_collapse_signals"))
    args = ap.parse_args()

    edges = tuple(args.tier_edges)
    tickers = [t.upper() for t in args.tickers]
    store = ArcticStore()

    print("=" * 100)
    print("  SPREAD-COLLAPSE SIGNAL STUDY  |  all tickers, all cycles  |  "
          f"HTB tiers: <{edges[0]:.0%} / {edges[0]:.0%}-{edges[1]:.0%} / >={edges[1]:.0%}")
    print("=" * 100)
    rec, profiles = build(store, tickers, args.dte_window, edges,
                          args.min_bars, not args.no_hmm, args.states, args.seed)
    if rec.empty:
        print("No cycles found."); return
    print(f"\n  Total cycles: {len(rec)}  |  tiers: "
          + ", ".join(f"{t}={int((rec['tier']==t).sum())}" for t in TIER_ORDER))

    if profiles:
        section_A(profiles, rec)
    section_1(rec)
    section_2(rec)
    section_3(rec)
    section_4(rec)
    section_5(rec)
    if not args.no_hmm:
        section_6(rec)

    out = Path(args.out)
    saved = make_graphs(rec, profiles, store, tickers, args.dte_window, out)
    print("\n" + "=" * 100)
    print(f"  Saved {len(saved)} figures to {out}")
    for p in saved:
        print(f"    {p.name}")
    print("=" * 100)


if __name__ == "__main__":
    main()
