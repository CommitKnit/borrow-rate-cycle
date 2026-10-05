"""
07_roll_mechanics.py — the F1-F2 premium measured against the open-interest roll.

The claim: the premium builds through the cycle, speeds up once short holders start
rolling out of the front month, tops out while open interest is crossing from F1 to
F2, and collapses once most of it has moved. This script turns that into numbers on
an axis of trading sessions to expiry, and on the F1/F2 open-interest ratio itself.

Outputs
-------
  results/roll_sessions.csv            one row per cycle-session (end of day)
  results/roll_cycles.csv              one row per cycle: peak vs roll milestones
  results/roll_profile_by_session.csv  medians/IQR by sessions-to-expiry (all, tier, ticker,
                                       ticker_ext = each ticker's EXT cycles only)
  results/spread_by_oi_ratio.csv       spread by F1/F2 OI-ratio bin (same groups)
  results/roll_midpoint_event.csv      profile aligned on the roll midpoint (ratio first < 1)
  results/roll_mechanics.md            the threshold statistics
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from borrowcycle import data as bd
from borrowcycle import roll
from borrowcycle.cycle import detect_moments, htb_tier, peak_b12, smooth
from borrowcycle.stats import bootstrap_ci, sign_test

RESULTS = bd.PKG_ROOT / "results"
MAX_SESSIONS = 25


def q(s: pd.Series, p: float) -> float:
    s = s.dropna()
    return float(s.quantile(p)) if len(s) else np.nan


def measure_cycle(t: str, cid: int, cyc: pd.DataFrame):
    m = detect_moments(cyc, t, cid)
    if m is None:
        return None, None
    sbps = roll.spread_bps(cyc)
    sm = smooth(sbps)
    build, peak = float(sm[m.i_build]), float(sm[m.i_peak])
    amp = peak - build
    if not np.isfinite(amp) or amp <= 0:
        return None, None

    sess = roll.session_frame(cyc)
    sess["spread_norm"] = (sess["spread_bps"] - build) / amp
    sess.insert(0, "cid", cid)
    sess.insert(0, "ticker", t)
    tier = htb_tier(peak_b12(cyc))
    sess["tier"] = tier
    sess["htb"] = "HTB" if tier in ("MOD", "EXT") else "non-HTB"

    peak_ts = cyc.index[m.i_peak]
    peak_day = peak_ts.normalize()
    f1, f2 = float(cyc["F1_oi"].iloc[m.i_peak]), float(cyc["F2_oi"].iloc[m.i_peak])
    rec = {"ticker": t, "cid": cid, "tier": tier, "htb": "HTB" if tier in ("MOD", "EXT") else "non-HTB",
           "amplitude_bps": amp,
           "peak_sessions_left": float(sess.loc[sess.index.normalize() == peak_day, "sessions_left"].iloc[0]),
           "oi_ratio_at_peak": f1 / f2 if f2 > 0 else np.nan,
           "oi_share_at_peak": f1 / (f1 + f2) if (f1 + f2) > 0 else np.nan}

    mid = roll.roll_midpoint(sess)
    rec["mid_sessions_left"] = float(sess.loc[mid, "sessions_left"]) if mid is not None else np.nan
    rec["peak_minus_mid"] = rec["peak_sessions_left"] - rec["mid_sessions_left"]
    for lvl in roll.OI_LEVELS:
        hit = roll.first_below(sess, lvl)
        rec[f"norm_at_ratio_{lvl:g}"] = float(sess.loc[hit, "spread_norm"]) if hit is not None else np.nan
        rec[f"sessions_left_at_ratio_{lvl:g}"] = float(sess.loc[hit, "sessions_left"]) if hit is not None else np.nan

    # share of the peak-to-expiry give-back that happens once the front month is the minority
    last = float(sess["spread_bps"].iloc[-1])
    if mid is not None and peak - last > 1e-9:
        before_mid = sess.loc[sess.index < mid, "spread_bps"]
        level = min(float(before_mid.iloc[-1]), peak) if len(before_mid) else peak
        rec["collapse_after_mid_frac"] = float(np.clip((level - last) / (peak - last), 0, 1))
    else:
        rec["collapse_after_mid_frac"] = np.nan
    return sess, rec


def profile(df: pd.DataFrame, key: str, cols: list[str], groups: list[str]) -> pd.DataFrame:
    out = []
    for g in groups:
        parts = [("all", df)] if g == "all" else list(df.groupby(g))
        for name, sub in parts:
            agg = sub.groupby(key).agg(**{f"{c}_{s}": (c, f) for c in cols for s, f in
                                          (("median", "median"), ("p25", lambda x: q(x, .25)),
                                           ("p75", lambda x: q(x, .75)))},
                                       n=(cols[0], "count"))
            agg.insert(0, "group", name)
            agg.insert(0, "by", g)
            out.append(agg.reset_index())
    return pd.concat(out, ignore_index=True)


def main() -> int:
    sessions, cycles = [], []
    for t in bd.TICKERS:
        for cid, cyc in bd.iter_cycles(t):
            sess, rec = measure_cycle(t, cid, cyc)
            if sess is not None:
                sessions.append(sess)
                cycles.append(rec)
    S = pd.concat(sessions)
    S.index.name = "date"
    C = pd.DataFrame(cycles)
    # per-ticker groups over extreme cycles only, so NON/MOD cycles don't dilute a name
    S["ticker_ext"] = S["ticker"].where(S["tier"] == "EXT")
    S.to_csv(RESULTS / "roll_sessions.csv")
    C.to_csv(RESULTS / "roll_cycles.csv", index=False)

    cols = ["spread_bps", "spread_norm", "oi_share_f1", "oi_ratio"]
    win = S[S["sessions_left"].between(0, MAX_SESSIONS)]
    prof = profile(win, "sessions_left", cols, ["all", "htb", "tier", "ticker", "ticker_ext"])
    prof.to_csv(RESULTS / "roll_profile_by_session.csv", index=False)

    S["oi_bin"] = roll.oi_bin(S["oi_ratio"])
    by_bin = profile(S.dropna(subset=["oi_bin"]).assign(oi_bin=lambda d: d["oi_bin"].astype(str)),
                     "oi_bin", ["spread_bps", "spread_norm", "sessions_left"], ["all", "htb", "tier", "ticker", "ticker_ext"])
    by_bin["order"] = by_bin["oi_bin"].map({b: i for i, b in enumerate(roll.OI_BIN_LABELS)})
    by_bin.sort_values(["by", "group", "order"]).drop(columns="order").to_csv(
        RESULTS / "spread_by_oi_ratio.csv", index=False)

    mids = C.set_index(["ticker", "cid"])["mid_sessions_left"]
    S["rel_mid"] = [m - s if np.isfinite(m) else np.nan for m, s in
                    zip(mids.reindex(pd.MultiIndex.from_arrays([S["ticker"], S["cid"]])).to_numpy(),
                        S["sessions_left"].to_numpy())]
    ev = S[S["rel_mid"].between(-15, 5)]
    profile(ev, "rel_mid", ["spread_bps", "spread_norm", "oi_share_f1"], ["all", "htb", "tier"]).to_csv(
        RESULTS / "roll_midpoint_event.csv", index=False)

    # ── threshold statistics ────────────────────────────────────────────────
    htbp = profile(win[win["htb"] == "HTB"], "sessions_left", cols, ["all"]).set_index("sessions_left")
    H = C[C["htb"] == "HTB"]
    peak_bin = by_bin[(by_bin.by == "all")].set_index("oi_bin")["spread_bps_median"].idxmax()
    lag = C["peak_minus_mid"].dropna()
    in_band = C["oi_ratio_at_peak"].between(0.5, 4)
    L = ["# Roll mechanics: the premium against the open-interest transfer\n",
         f"{len(C)} cycles, 7 tickers, end-of-session values on an axis of trading sessions to F1 "
         "expiry (0 = expiry day). Spread = (F1 − F2)/F1 in bps; *normalised* spread = 0 at the "
         "cycle's build trough, 1 at its peak (smoothed).\n",
         "## 1. The premium by open-interest ratio\n",
         "| F1/F2 OI ratio | median spread (bps) | IQR | median normalised | median sessions left | bars |",
         "|---|---|---|---|---|---|"]
    for _, r in by_bin[by_bin.by == "all"].iterrows():
        L.append(f"| {r.oi_bin} | {r.spread_bps_median:.0f} | {r.spread_bps_p25:.0f} – {r.spread_bps_p75:.0f} | "
                 f"{r.spread_norm_median:.2f} | {r.sessions_left_median:.0f} | {int(r.n)} |")
    L.append(f"\nThe median spread is highest in the **{peak_bin}×** bin and falls away on both sides.\n")

    L.append("## 2. Where each cycle peaks\n")
    L.append(f"- F1/F2 OI ratio at the spread peak: median **{C.oi_ratio_at_peak.median():.2f}×** "
             f"(IQR {q(C.oi_ratio_at_peak, .25):.2f} – {q(C.oi_ratio_at_peak, .75):.2f}); front-month share "
             f"of OI at the peak: median **{100 * C.oi_share_at_peak.median():.0f}%**.")
    L.append(f"- Hard-to-borrow cycles only (MOD + EXT, n={len(H)}): OI ratio at the peak median "
             f"**{H.oi_ratio_at_peak.median():.2f}×**, front-month share **{100 * H.oi_share_at_peak.median():.0f}%**, "
             f"peak **{H.peak_sessions_left.median():.0f}** sessions before expiry.")
    L.append(f"- {100 * in_band.mean():.0f}% of all cycles peak while the ratio is between 0.5× and 4× "
             f"(n={int(in_band.sum())}/{len(C)}); the rest mostly peak in the final session, after it.")
    L.append(f"- Peak: median **{C.peak_sessions_left.median():.0f}** sessions before expiry. Roll midpoint "
             f"(ratio first < 1): median **{C.mid_sessions_left.median():.0f}** sessions before expiry "
             f"(reached in {int(C.mid_sessions_left.notna().sum())}/{len(C)} cycles).")
    lo, hi = bootstrap_ci(lag)
    L.append(f"- Peak minus midpoint: median {lag.median():+.0f} sessions, mean {lag.mean():+.1f} "
             f"(95% CI {lo:+.1f} to {hi:+.1f}); the peak comes at or before the midpoint in "
             f"{100 * (lag >= 0).mean():.0f}% of cycles (sign test p = {sign_test(lag):.2g}).\n")

    L.append("## 3. How much of the premium is left as the roll progresses\n")
    L.append("All cycles. Normalised spread on the first session the OI ratio drops below each level "
             "(1.0 = the cycle's peak, 0 = its build trough).\n")
    L.append("| ratio first below | median normalised spread | IQR | median sessions left | cycles |")
    L.append("|---|---|---|---|---|")
    for lvl in roll.OI_LEVELS:
        c, s = C[f"norm_at_ratio_{lvl:g}"], C[f"sessions_left_at_ratio_{lvl:g}"]
        L.append(f"| {lvl:g}× | {c.median():.2f} | {q(c, .25):.2f} – {q(c, .75):.2f} | {s.median():.0f} | {int(c.notna().sum())} |")
    frac = C["collapse_after_mid_frac"].dropna()
    L.append(f"\nOf the give-back from the peak to the expiry close, a median **{100 * frac.median():.0f}%** "
             f"happens after the front month has become the minority (n={len(frac)}).\n")

    L.append("## 4. By borrow tier\n")
    L.append("| tier | cycles | median amplitude (bps) | OI ratio at peak | peak sessions left | midpoint sessions left |")
    L.append("|---|---|---|---|---|---|")
    for tier, g in C.groupby("tier"):
        L.append(f"| {tier} | {len(g)} | {g.amplitude_bps.median():.0f} | {g.oi_ratio_at_peak.median():.2f} | "
                 f"{g.peak_sessions_left.median():.0f} | {g.mid_sessions_left.median():.0f} |")

    L.append("\n## 5. Median profile of hard-to-borrow cycles (MOD + EXT), last 15 sessions\n")
    L.append("| sessions left | spread (bps) | normalised | F1 share of OI | F1/F2 ratio |")
    L.append("|---|---|---|---|---|")
    for s in range(15, -1, -1):
        if s in htbp.index:
            r = htbp.loc[s]
            L.append(f"| {s} | {r.spread_bps_median:.0f} | {r.spread_norm_median:.2f} | "
                     f"{100 * r.oi_share_f1_median:.0f}% | {r.oi_ratio_median:.2f} |")

    L.append("\n## 6. By ticker, extreme cycles only (EXT, b12 ≥ 15%)\n")
    L.append("Median spread (bps) by sessions to expiry. Restricting to EXT keeps a name's no-premium "
             "cycles from diluting its profile. Peaks are in sessions to expiry.\n")
    show = [10, 6, 4, 3, 2, 1, 0]
    L.append("| ticker | EXT cycles | " + " | ".join(str(s) for s in show) + " | peak of median profile | median per-cycle peak |")
    L.append("|---|---|" + "---|" * len(show) + "---|---|")
    ext = prof[prof.by == "ticker_ext"]
    for t in bd.TICKERS:
        d = ext[ext.group == t].set_index("sessions_left")["spread_bps_median"]
        last10 = d.loc[d.index <= 10]
        cyc_peak = C.loc[(C.ticker == t) & (C.tier == "EXT"), "peak_sessions_left"]
        L.append(f"| {t} | {len(cyc_peak)} | " + " | ".join(f"{d.get(s, np.nan):.0f}" for s in show)
                 + f" | {last10.idxmax():.0f} | {cyc_peak.median():.0f} |")
    (RESULTS / "roll_mechanics.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"{len(C)} cycles | spread peaks in the {peak_bin}x bin | OI ratio at peak "
          f"{C.oi_ratio_at_peak.median():.2f}x | peak {C.peak_sessions_left.median():.0f} sessions out, "
          f"midpoint {C.mid_sessions_left.median():.0f} | collapse after midpoint {100 * frac.median():.0f}%")
    print("wrote results/roll_sessions.csv, roll_cycles.csv, roll_profile_by_session.csv, "
          "spread_by_oi_ratio.csv, roll_midpoint_event.csv, roll_mechanics.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
