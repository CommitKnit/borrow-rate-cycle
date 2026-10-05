"""
01_cycle_anatomy.py — measure the build/peak/collapse cycle across every expiry.

This is the core measurement of the repo. The claim under test is that the
hard-to-borrow futures premium is not noise but a repeating monthly shape:
it builds through the cycle, tops out near expiry, and collapses mechanically
into settlement. The script locates those three moments in all 127 expiry
cycles across 7 names and quantifies the shape.

Outputs
-------
  results/cycle_moments.csv       one row per cycle, ~40 columns
  results/anatomy_summary.md      the measured statistics
  results/event_time_curve.csv    amplitude-normalised mean trajectory
  results/robustness_grid.csv     detector sensitivity
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from borrowcycle import data as bd
from borrowcycle.cycle import (HTB_THRESH, MIN_SUSTAIN, SMOOTH, detect_moments,
                               htb_tier, peak_b12, smooth, spread)
from borrowcycle.stats import binom_p

RESULTS = bd.PKG_ROOT / "results"

#: Event-time window around the peak, in 15-minute bars (25 bars = 1 session).
TAU_LO, TAU_HI = -250, 100


def _oi_ratio(cyc: pd.DataFrame, i: int) -> float:
    if "F1_oi" not in cyc.columns or "F2_oi" not in cyc.columns:
        return np.nan
    f1, f2 = cyc["F1_oi"].iloc[i], cyc["F2_oi"].iloc[i]
    return float(f1 / f2) if np.isfinite(f1) and np.isfinite(f2) and f2 > 0 else np.nan


def _at(cyc: pd.DataFrame, col: str, i: int) -> float:
    if col not in cyc.columns:
        return np.nan
    v = cyc[col].iloc[i]
    return float(v) if np.isfinite(v) else np.nan


def build_records() -> tuple[pd.DataFrame, dict]:
    """Detect the three moments in every cycle and record the measurements."""
    rows, curves = [], {}
    for t in bd.TICKERS:
        for cid, cyc in bd.iter_cycles(t):
            m = detect_moments(cyc, t, cid)
            if m is None:
                continue
            sm = smooth(spread(cyc))
            f1 = cyc["F1_close"].ffill().to_numpy(float)
            f1_peak = f1[m.i_peak] if np.isfinite(f1[m.i_peak]) else np.nan

            s_build, s_peak = sm[m.i_build], sm[m.i_peak]
            s_expiry = sm[-1]
            s_coll = sm[m.i_collapse] if m.i_collapse is not None else np.nan
            amplitude = s_peak - s_build
            collapse_abs = s_peak - s_expiry
            retracement = collapse_abs / amplitude if amplitude > 1e-9 else np.nan

            pk = peak_b12(cyc)

            # Does F1 open interest turn down before the spread peaks?
            oi_peak_leads, lead_bars = np.nan, np.nan
            if "F1_oi" in cyc.columns:
                f1oi = cyc["F1_oi"].ffill().to_numpy(float)
                if np.isfinite(f1oi).any():
                    i_oi = int(np.nanargmax(f1oi))
                    oi_peak_leads = bool(i_oi < m.i_peak)
                    lead_bars = m.i_peak - i_oi

            def bps(x):
                return 1e4 * x / f1_peak if np.isfinite(f1_peak) and f1_peak else np.nan

            rows.append({
                "ticker": t, "cid": cid, "n_bars": m.n_bars,
                "cycle_start": cyc.index[0], "cycle_end": cyc.index[-1],
                "peak_b12": pk, "htb_tier": htb_tier(pk),
                "is_htb": pk >= HTB_THRESH,
                # timing
                "dte_build": _at(cyc, "F1_dte", m.i_build),
                "dte_peak": _at(cyc, "F1_dte", m.i_peak),
                "dte_collapse": (_at(cyc, "F1_dte", m.i_collapse)
                                 if m.i_collapse is not None else np.nan),
                "bars_build_to_peak": m.i_peak - m.i_build,
                "bars_peak_to_collapse": (m.i_collapse - m.i_peak
                                          if m.i_collapse is not None else np.nan),
                "hours_peak_to_collapse": ((m.i_collapse - m.i_peak) * 0.25
                                           if m.i_collapse is not None else np.nan),
                "collapse25_agrees": (
                    abs(m.i_collapse - m.i_collapse25) <= bd.BARS_PER_DAY
                    if m.i_collapse is not None and m.i_collapse25 is not None
                    else np.nan),
                # levels
                "spread_build": s_build, "spread_peak": s_peak,
                "spread_collapse": s_coll, "spread_expiry": s_expiry,
                "spread_build_bps": bps(s_build), "spread_peak_bps": bps(s_peak),
                "spread_expiry_bps": bps(s_expiry),
                "amplitude": amplitude, "amplitude_bps": bps(amplitude),
                "collapse_abs": collapse_abs, "collapse_abs_bps": bps(collapse_abs),
                "retracement": retracement,
                "b12_build": _at(cyc, "b12", m.i_build),
                "b12_peak": _at(cyc, "b12", m.i_peak),
                "b12_expiry": _at(cyc, "b12", len(cyc) - 1),
                # roll
                "oi_ratio_build": _oi_ratio(cyc, m.i_build),
                "oi_ratio_peak": _oi_ratio(cyc, m.i_peak),
                "oi_ratio_collapse": (_oi_ratio(cyc, m.i_collapse)
                                      if m.i_collapse is not None else np.nan),
                "f1_oi_build": _at(cyc, "F1_oi", m.i_build),
                "f1_oi_peak": _at(cyc, "F1_oi", m.i_peak),
                "f2_oi_build": _at(cyc, "F2_oi", m.i_build),
                "f2_oi_peak": _at(cyc, "F2_oi", m.i_peak),
                "oi_peak_leads_spread_peak": oi_peak_leads,
                "lead_bars": lead_bars,
                # features at the peak
                "atm_iv_peak": _at(cyc, "feat_atm_iv", m.i_peak),
                "rr25_peak": _at(cyc, "feat_25d_risk_reversal", m.i_peak),
                # flags
                "build_censored": m.build_censored,
                "build_confirmed": m.build_confirmed,
                "peak_in_expiry_week": m.peak_in_expiry_week,
                "phases_ordered": m.phases_ordered,
                "gap_flag": m.gap_flag,
            })

            # amplitude-normalised trajectory in event time
            if amplitude > 1e-9:
                tau = np.arange(len(sm)) - m.i_peak
                keep = (tau >= TAU_LO) & (tau <= TAU_HI)
                norm = (sm[keep] - s_build) / amplitude
                curves[(t, cid, htb_tier(pk))] = pd.Series(norm, index=tau[keep])

    return pd.DataFrame(rows), curves


def event_time_curve(curves: dict) -> pd.DataFrame:
    """Median and quartile trajectory across cycles, overall and per tier."""
    grid = np.arange(TAU_LO, TAU_HI + 1)
    out = {"tau": grid, "session": grid / bd.BARS_PER_DAY}
    for tier in ("ALL", "NON", "MOD", "EXT"):
        sel = [s for (_, _, tr), s in curves.items() if tier == "ALL" or tr == tier]
        if not sel:
            continue
        mat = pd.DataFrame({i: s.reindex(grid) for i, s in enumerate(sel)})
        out[f"{tier}_n"] = mat.notna().sum(axis=1).to_numpy()
        out[f"{tier}_median"] = mat.median(axis=1).to_numpy()
        out[f"{tier}_q25"] = mat.quantile(0.25, axis=1).to_numpy()
        out[f"{tier}_q75"] = mat.quantile(0.75, axis=1).to_numpy()
    return pd.DataFrame(out)


def placebo_test(n_draws: int = 1000, seed: int = 42) -> dict:
    """Is the collapse special to expiry, or does any local maximum revert?

    For each cycle, anchor the same retracement measurement on the argmax of a
    randomly chosen 7-session window that does NOT touch the expiry week, and
    compare the resulting distribution to the observed expiry-anchored value.
    """
    rng = np.random.default_rng(seed)
    obs, plac = [], []
    win = 7 * bd.BARS_PER_DAY
    for t in bd.TICKERS:
        for cid, cyc in bd.iter_cycles(t):
            m = detect_moments(cyc, t, cid)
            if m is None:
                continue
            sm = smooth(spread(cyc))
            amp = sm[m.i_peak] - sm[m.i_build]
            if amp <= 1e-9:
                continue
            obs.append((sm[m.i_peak] - sm[-1]) / amp)

            # placebo windows must end before the expiry week starts
            dte = cyc["F1_dte"].to_numpy(float)
            safe = np.where(np.isfinite(dte) & (dte > 7))[0]
            if len(safe) < win + bd.BARS_PER_DAY:
                continue
            hi = safe[-1] - win
            if hi <= safe[0]:
                continue
            for _ in range(max(1, n_draws // 100)):
                a = int(rng.integers(safe[0], hi))
                seg = sm[a:a + win]
                if not np.isfinite(seg).any():
                    continue
                ip = int(np.nanargmax(seg))
                ib = int(np.nanargmin(seg[:ip])) if ip > 0 else 0
                amp_p = seg[ip] - seg[ib]
                if amp_p > 1e-9:
                    plac.append((seg[ip] - seg[-1]) / amp_p)

    obs, plac = np.array(obs), np.array(plac)
    obs, plac = obs[np.isfinite(obs)], plac[np.isfinite(plac)]
    pct = float((plac < np.median(obs)).mean() * 100) if len(plac) else np.nan
    u = sps.mannwhitneyu(obs, plac, alternative="greater") if len(plac) else None
    return {"obs_n": len(obs), "obs_median": float(np.median(obs)),
            "placebo_n": len(plac), "placebo_median": float(np.median(plac)),
            "percentile_of_observed": pct,
            "mannwhitney_p": float(u.pvalue) if u else np.nan}


def robustness_grid() -> pd.DataFrame:
    """How sensitive are the measurements to the detector's free parameters?"""
    rows = []
    for legacy in (True, False):
        for w in (5, 11, 21):
            for ms in (2, 4, 8):
                dte_b, dte_p, retr, cens = [], [], [], []
                for t in bd.TICKERS:
                    for cid, cyc in bd.iter_cycles(t):
                        m = detect_moments(cyc, t, cid, w=w, min_sustain=ms,
                                           legacy=legacy)
                        if m is None:
                            continue
                        sm = smooth(spread(cyc), w)
                        amp = sm[m.i_peak] - sm[m.i_build]
                        dte_b.append(_at(cyc, "F1_dte", m.i_build))
                        dte_p.append(_at(cyc, "F1_dte", m.i_peak))
                        retr.append((sm[m.i_peak] - sm[-1]) / amp if amp > 1e-9 else np.nan)
                        cens.append(m.build_censored)
                rows.append({
                    "detector": "legacy (DTE<=7 window)" if legacy else "window-free",
                    "smooth": w, "min_sustain": ms, "n": len(dte_b),
                    "dte_build_p25": np.nanpercentile(dte_b, 25),
                    "dte_build_med": np.nanmedian(dte_b),
                    "dte_build_p75": np.nanpercentile(dte_b, 75),
                    "dte_peak_med": np.nanmedian(dte_p),
                    "retracement_med": np.nanmedian(retr),
                    "build_censored_pct": 100 * float(np.mean(cens)),
                })
    return pd.DataFrame(rows)


def pct_row(s: pd.Series) -> str:
    v = s.dropna()
    if not len(v):
        return "| - | - | - | - | - |"
    q = np.percentile(v, [10, 25, 50, 75, 90])
    return f"| {q[0]:.1f} | {q[1]:.1f} | {q[2]:.1f} | {q[3]:.1f} | {q[4]:.1f} |"


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    df, curves = build_records()
    df.to_csv(RESULTS / "cycle_moments.csv", index=False)
    print(f"measured {len(df)} cycles across {df['ticker'].nunique()} tickers")

    etc = event_time_curve(curves)
    etc.to_csv(RESULTS / "event_time_curve.csv", index=False)

    grid = robustness_grid()
    grid.to_csv(RESULTS / "robustness_grid.csv", index=False)

    plac = placebo_test()

    L = []
    L.append("# Cycle anatomy — measured across every expiry cycle\n")
    L.append(f"{len(df)} expiry cycles, {df['ticker'].nunique()} NSE single-stock "
             f"futures, 15-minute bars, "
             f"{df['cycle_start'].min():%b %Y} to {df['cycle_end'].max():%b %Y}.\n")
    L.append("Three moments are located on the 11-bar centred smoothed F1-F2 "
             "spread over the **whole cycle**:\n")
    L.append("- **BUILD** — the last trough before the peak (argmin of the "
             "smoothed spread before the peak).")
    L.append("- **PEAK** — the cycle maximum of the smoothed spread.")
    L.append("- **COLLAPSE** — the first bar after the peak where slope and "
             "acceleration are both negative for 4 consecutive bars (~1 hour).\n")

    L.append("\n## 1. When do the three moments happen?\n")
    L.append("Days to F1 expiry. Lower = closer to settlement.\n")
    L.append("| Moment | p10 | p25 | median | p75 | p90 |")
    L.append("|---|---|---|---|---|---|")
    for lbl, col in (("BUILD", "dte_build"), ("PEAK", "dte_peak"),
                     ("COLLAPSE", "dte_collapse")):
        L.append(f"| {lbl} {pct_row(df[col])[1:]}")
    L.append(f"\nThe premium starts building a median of "
             f"**{df['dte_build'].median():.0f} days** before expiry and tops out "
             f"**{df['dte_peak'].median():.0f} days** before it.\n")
    L.append(f"The peak falls inside the final expiry week in "
             f"**{100*df['peak_in_expiry_week'].mean():.0f}%** of cycles — often, "
             f"but far from always. {100*df['build_censored'].mean():.0f}% of "
             f"cycles have their build trough at the very start of the cycle, "
             f"where it cannot be distinguished from the cycle boundary; those "
             f"are flagged `build_censored`.\n")

    L.append("\n## 2. How big is the move, and how much of it reverses?\n")
    L.append("| Measure | p25 | median | p75 |")
    L.append("|---|---|---|---|")
    for lbl, col in (("Amplitude, build to peak (bps of F1)", "amplitude_bps"),
                     ("Collapse, peak to expiry (bps of F1)", "collapse_abs_bps"),
                     ("Spread at expiry (bps of F1)", "spread_expiry_bps"),
                     ("Retracement (collapse / amplitude)", "retracement")):
        v = df[col].dropna()
        L.append(f"| {lbl} | {np.percentile(v,25):.2f} | {np.median(v):.2f} | "
                 f"{np.percentile(v,75):.2f} |")
    r = df["retracement"].dropna()
    L.append(f"\nThe median cycle gives back **{100*np.median(r):.0f}%** of "
             f"everything it built, and **{100*(r>0.8).mean():.0f}%** of cycles "
             f"retrace more than 80%. Retracement is used rather than "
             f"collapse/peak because peak spreads near zero make that ratio "
             f"explode.\n")

    L.append("\n## 3. The roll: open interest has already moved by the peak\n")
    L.append("Ratio of front-month to next-month open interest at each moment.\n")
    L.append("| Moment | p25 | median | p75 | n |")
    L.append("|---|---|---|---|---|")
    for lbl, col in (("BUILD", "oi_ratio_build"), ("PEAK", "oi_ratio_peak"),
                     ("COLLAPSE", "oi_ratio_collapse")):
        v = df[col].replace([np.inf, -np.inf], np.nan).dropna()
        L.append(f"| {lbl} | {np.percentile(v,25):.2f} | {np.median(v):.2f} | "
                 f"{np.percentile(v,75):.2f} | {len(v)} |")
    pair = df[["oi_ratio_build", "oi_ratio_peak"]].replace(
        [np.inf, -np.inf], np.nan).dropna()
    if len(pair) > 5:
        w = sps.wilcoxon(np.log(pair["oi_ratio_peak"]), np.log(pair["oi_ratio_build"]))
        L.append(f"\nAt the build trough the front month carries a median "
                 f"**{np.median(pair['oi_ratio_build']):.1f}x** the open interest "
                 f"of the next month. By the spread peak that has fallen to "
                 f"**{np.median(pair['oi_ratio_peak']):.1f}x**. Paired Wilcoxon on "
                 f"log(ratio), n={len(pair)}: p = {w.pvalue:.2e}.\n")
        L.append("The roll is therefore already well advanced by the time the "
                 "spread tops out — consistent with the roll causing the "
                 "collapse rather than following it.\n")

    lead = df["oi_peak_leads_spread_peak"].dropna()
    if len(lead):
        k, n = int(lead.sum()), len(lead)
        L.append(f"\nFront-month open interest peaks **before** the spread peak in "
                 f"**{k}/{n} ({100*k/n:.0f}%)** of cycles "
                 f"(binomial vs 50%: p = {binom_p(k, n):.2e}).\n")
        L.append("| Ticker | OI peaks first | n | rate |")
        L.append("|---|---|---|---|")
        for t in bd.TICKERS:
            s = df.loc[df["ticker"] == t, "oi_peak_leads_spread_peak"].dropna()
            if len(s):
                L.append(f"| {t} | {int(s.sum())} | {len(s)} | {100*s.mean():.0f}% |")

    L.append("\n## 4. Does the borrow premium matter? (HTB vs control)\n")
    L.append("| Tier | n | median amplitude (bps) | median retracement |")
    L.append("|---|---|---|---|")
    for tier in ("NON", "MOD", "EXT"):
        s = df[df["htb_tier"] == tier]
        if len(s):
            L.append(f"| {tier} | {len(s)} | {s['amplitude_bps'].median():.1f} | "
                     f"{s['retracement'].median():.2f} |")
    non = df.loc[df["htb_tier"] == "NON", "amplitude_bps"].dropna()
    ext = df.loc[df["htb_tier"] == "EXT", "amplitude_bps"].dropna()
    if len(non) > 2 and len(ext) > 2:
        u = sps.mannwhitneyu(ext, non, alternative="greater")
        L.append(f"\nMann-Whitney, amplitude of extreme-HTB vs non-HTB cycles "
                 f"(n={len(ext)} vs {len(non)}): p = {u.pvalue:.2e}. The control "
                 f"group is small — treat it as a sanity check, not a test.\n")

    L.append("\n## 5. Is the collapse special to expiry? (placebo test)\n")
    L.append("`build < peak` holds by construction, so ordering alone proves "
             "nothing. The real question is whether a spread peak reverts *more* "
             "into expiry than an arbitrary local maximum reverts mid-cycle. "
             "Placebo windows are 7 sessions long and never touch the expiry "
             "week.\n")
    L.append(f"- observed retracement, median: **{plac['obs_median']:.2f}** "
             f"(n={plac['obs_n']})")
    L.append(f"- placebo retracement, median: **{plac['placebo_median']:.2f}** "
             f"(n={plac['placebo_n']})")
    L.append(f"- Mann-Whitney (observed > placebo): p = {plac['mannwhitney_p']:.2e}\n")

    L.append("\n## 6. Detector robustness\n")
    L.append("The original detector searched only inside the expiry week "
             "(`DTE <= 7`). That is censored: it reports the window edge as the "
             "build moment, and widening the window just moves the artefact. "
             "Compare the `dte_build` quartiles of the two detectors below — "
             "under the legacy rule p25, median and p75 collapse onto the window "
             "edge itself.\n")
    L.append("| Detector | smooth | min_sustain | n | dte_build p25/med/p75 | "
             "dte_peak med | retracement med | censored % |")
    L.append("|---|---|---|---|---|---|---|---|")
    for _, g in grid.iterrows():
        L.append(f"| {g['detector']} | {g['smooth']} | {g['min_sustain']} | "
                 f"{g['n']} | {g['dte_build_p25']:.1f} / {g['dte_build_med']:.1f} "
                 f"/ {g['dte_build_p75']:.1f} | {g['dte_peak_med']:.1f} | "
                 f"{g['retracement_med']:.2f} | {g['build_censored_pct']:.0f}% |")

    agree = df["collapse25_agrees"].dropna()
    if len(agree):
        L.append(f"\nThe slope/acceleration collapse trigger and an independent "
                 f"amplitude-based one (first bar giving back 25% of the move) "
                 f"land within one session of each other in "
                 f"**{100*agree.mean():.0f}%** of cycles.\n")

    L.append("\n## 7. Data caveats\n")
    L.append(f"- {int(df['gap_flag'].sum())} cycles have fewer than 5 bars inside "
             f"the expiry week and are flagged `gap_flag`.")
    L.append(f"- {int(df['build_censored'].sum())} cycles have a censored build "
             f"trough.")
    L.append("- IV features are NaN on cycles whose front-month expiry has no "
             "enriched option chain; those cycles still count in the spread and "
             "open-interest statistics.")

    (RESULTS / "anatomy_summary.md").write_text("\n".join(L), encoding="utf-8")

    print(f"  median dte_build={df['dte_build'].median():.0f} "
          f"dte_peak={df['dte_peak'].median():.0f}")
    print(f"  median amplitude={df['amplitude_bps'].median():.0f} bps  "
          f"retracement={df['retracement'].median():.2f}")
    print(f"  OI ratio build={df['oi_ratio_build'].median():.1f} -> "
          f"peak={df['oi_ratio_peak'].median():.1f}")
    print(f"  build_censored={100*df['build_censored'].mean():.0f}%  "
          f"peak_in_expiry_week={100*df['peak_in_expiry_week'].mean():.0f}%")
    print("wrote results/cycle_moments.csv, anatomy_summary.md, "
          "event_time_curve.csv, robustness_grid.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
