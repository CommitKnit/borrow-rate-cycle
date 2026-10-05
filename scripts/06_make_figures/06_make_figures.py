"""
06_make_figures.py — render every narrative figure from results/.

Runs in seconds and needs only the CSVs written by scripts 01-05 and 07, so the
figures can be rebuilt without re-running any analysis.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from borrowcycle import data as bd
from borrowcycle import plotting as P
from borrowcycle import roll
from borrowcycle.cycle import detect_moments, smooth, spread
from borrowcycle.stats import win_rate

RESULTS = bd.PKG_ROOT / "results"
P.use_style()


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(RESULTS / name)


# ---------------------------------------------------------------- figure 01
def fig01_mechanism(mom: pd.DataFrame):
    """One annotated cycle: prices, the spread, and the open interest moving across."""
    cand = mom[(mom.ticker == "RVNL") & mom.amplitude_bps.notna()]
    row = cand.loc[cand.amplitude_bps.idxmax()]
    t, cid = row.ticker, int(row.cid)
    cyc = next(c for i, c in bd.iter_cycles(t) if i == cid)
    m = detect_moments(cyc, t, cid)

    sbps = roll.spread_bps(cyc)
    sm = smooth(sbps)
    f1oi, f2oi = cyc["F1_oi"].astype(float) / 1e6, cyc["F2_oi"].astype(float) / 1e6

    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(12.5, 10.2), sharex=True,
                                     gridspec_kw={"height_ratios": [1.0, 1.25, 1.1], "hspace": 0.08})
    x = np.arange(len(cyc))
    a1.plot(x, cyc["F1_close"].ffill(), color=P.C_F1OI, lw=1.4, label="F1 (front month)")
    a1.plot(x, cyc["F2_close"].ffill(), color=P.C_F2OI, lw=1.4, label="F2 (next month)")
    a1.set_ylabel("futures price (Rs)")
    a1.legend(loc="upper left")
    a1.set_title(f"{t}, {pd.Timestamp(cyc.index[-1]).strftime('%b %Y')} expiry: the premium builds as the roll "
                 "starts, peaks as open interest crosses to the next month, and collapses at settlement")

    a2.plot(x, sbps.to_numpy(float), color=P.C_SPREAD, lw=0.7, alpha=0.3)
    a2.plot(x, sm, color=P.C_SPREAD, lw=2.3, label="F1 - F2 spread (smoothed)")
    a2.axhline(0, color="k", lw=0.7, ls=":", alpha=0.5)
    a2.set_ylabel("F1 - F2 spread (bps of F1)")

    a3.stackplot(x, f1oi, f2oi, colors=[P.C_F1OI, P.C_F2OI], alpha=0.55,
                 labels=["F1 open interest", "F2 open interest"])
    a3.set_ylabel("open interest (million)")
    a3.legend(loc="upper left")

    marks = [(m.i_build, "BUILD", P.C_BUILD, "^"), (m.i_peak, "PEAK", P.C_PEAK, "D")]
    if m.i_collapse is not None:
        marks.append((m.i_collapse, "COLLAPSE", P.C_COLL, "v"))
    items = []
    for i, lbl, col, mk in marks:
        for ax in (a1, a2, a3):
            ax.axvline(i, color=col, lw=1.3, ls="--", alpha=0.8)
        a2.scatter([i], [sm[i]], s=140, marker=mk, color=col, edgecolor="white", lw=1.2, zorder=6)
        ratio = f1oi.iloc[i] / f2oi.iloc[i] if f2oi.iloc[i] > 0 else np.nan
        items.append((i, sm[i], f"{lbl}  {sm[i]:+.0f} bps\nF1/F2 OI {ratio:.1f}x, "
                                f"{cyc['F1_dte'].iloc[i]:.0f}d to expiry", col))
    P.annotate_no_overlap(a2, items)
    a2.legend(loc="upper left")
    P.bar_axis(a3, cyc.index)
    a3.set_xlim(0, len(cyc) - 1)
    return P.save(fig, "01_mechanism_single_cycle")


# ---------------------------------------------------------------- figure 02
def fig02_roll_leads(mom: pd.DataFrame):
    """Does front-month OI turn down before the spread peaks?"""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.5, 5.6))

    rows = []
    for t in bd.TICKERS:
        s = mom.loc[mom.ticker == t, "oi_peak_leads_spread_peak"].dropna()
        if len(s):
            k, n = int(s.sum()), len(s)
            p = k / n
            se = np.sqrt(p * (1 - p) / n)
            rows.append((t, p * 100, 1.96 * se * 100, k, n))
    rows.sort(key=lambda r: r[1])
    y = np.arange(len(rows))
    a1.barh(y, [r[1] for r in rows], xerr=[r[2] for r in rows],
            color=[P.TICKER_COLORS[r[0]] for r in rows], alpha=0.85,
            error_kw=dict(lw=1.1, capsize=3, ecolor="#555"))
    a1.axvline(50, color="k", ls="--", lw=1.2, label="no lead/lag (50%)")
    a1.set_yticks(y)
    a1.set_yticklabels([f"{r[0]}  ({r[3]}/{r[4]})" for r in rows])
    a1.set_xlabel("% of cycles where F1 open interest peaks BEFORE the spread peak")
    a1.set_xlim(0, 100)
    a1.legend(loc="lower right")
    a1.set_title("The roll leads the peak in every name")

    lead = mom["lead_bars"].dropna() / bd.BARS_PER_DAY
    lead = lead[(lead > -20) & (lead < 40)]
    a2.hist(lead, bins=30, color=P.C_F1OI, alpha=0.8, edgecolor="white")
    a2.axvline(0, color="k", ls="--", lw=1.2)
    a2.axvline(lead.median(), color=P.C_B12, lw=1.8,
               label=f"median {lead.median():.1f} sessions")
    a2.set_xlabel("sessions by which F1 OI peak leads the spread peak")
    a2.set_ylabel("cycles")
    a2.legend()
    a2.set_title("Size of the lead")

    fig.suptitle("Open interest rolls out of the front month before the "
                 "borrow premium tops out", y=1.00, fontsize=12.5)
    return P.save(fig, "02_roll_leads_peak")


# ---------------------------------------------------------------- figure 03
def fig03_event_time(etc: pd.DataFrame):
    """The money figure: the average shape of a cycle, in event time."""
    fig, ax = plt.subplots(figsize=(12.5, 6.4))
    s = etc["session"]

    ax.fill_between(s, etc["ALL_q25"], etc["ALL_q75"], color="#95a5a6",
                    alpha=0.25, label="all cycles, interquartile range")
    for tier in ("EXT", "MOD", "NON"):
        col = f"{tier}_median"
        if col in etc:
            n = int(np.nanmax(etc[f"{tier}_n"]))
            ax.plot(s, etc[col], color=P.TIER_COLORS[tier], lw=2.4,
                    label=f"{P.TIER_LABELS[tier]}  (n={n})")
    ax.plot(s, etc["ALL_median"], color="#2c3e50", lw=1.6, ls="--",
            label="all cycles, median")

    ax.axvline(0, color="k", lw=1.3, ls="--", alpha=0.8)
    ax.axhline(1.0, color="#7f8c8d", lw=0.8, ls=":", alpha=0.8)
    ax.axhline(0.0, color="#7f8c8d", lw=0.8, ls=":", alpha=0.8)
    ax.text(0.3, 1.02, "peak", fontsize=9, color="#555")
    ax.text(-9.7, 0.02, "build trough", fontsize=9, color="#555")

    ax.set_xlabel("trading sessions relative to the spread peak "
                  "(0 = peak, right of 0 = into expiry)")
    ax.set_ylabel("spread, normalised  (0 = build trough, 1 = peak)")
    ax.set_xlim(-10, 4)
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title("The average cycle: each expiry's spread normalised by its own "
                 "amplitude and aligned on its peak\n"
                 "the premium grinds up for weeks, then gives most of it back "
                 "within days")
    return P.save(fig, "03_cycle_anatomy_eventtime")


# ---------------------------------------------------------------- figure 04
def fig04_phase_timing(mom: pd.DataFrame):
    """When each phase happens, in days to expiry."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 5.8),
                                 gridspec_kw={"width_ratios": [1.25, 1]})

    cols = [("dte_build", "BUILD", P.C_BUILD),
            ("dte_peak", "PEAK", P.C_PEAK),
            ("dte_collapse", "COLLAPSE", P.C_COLL)]
    data = [mom[c].dropna().to_numpy() for c, _, _ in cols]
    parts = a1.violinplot(data, positions=[2, 1, 0], vert=False,
                          showmedians=True, widths=0.8)
    for pc, (_, _, col) in zip(parts["bodies"], cols):
        pc.set_facecolor(col); pc.set_alpha(0.55)
    for key in ("cmedians", "cbars", "cmins", "cmaxes"):
        if key in parts:
            parts[key].set_color("#2c3e50"); parts[key].set_linewidth(1.2)
    a1.set_yticks([2, 1, 0])
    a1.set_yticklabels([f"{l}\n(median {np.median(d):.0f}d)"
                        for (_, l, _), d in zip(cols, data)])
    a1.axvline(7, color="#e67e22", ls="--", lw=1.4, label="expiry week (DTE=7)")
    a1.invert_xaxis()
    a1.set_xlabel("days to F1 expiry  (right to left = approaching settlement)")
    a1.legend(loc="lower left")
    a1.set_title("Each phase, across 126 cycles")

    for t in bd.TICKERS:
        s = mom[mom.ticker == t]
        a2.scatter(s["dte_build"], s["dte_peak"], s=46, alpha=0.8,
                   color=P.TICKER_COLORS[t], label=t, edgecolor="white", lw=0.6)
    a2.axhline(7, color="#e67e22", ls="--", lw=1.2, alpha=0.8)
    a2.set_xlabel("DTE at build start")
    a2.set_ylabel("DTE at peak")
    a2.legend(fontsize=8, ncol=2)
    a2.set_title("Build is early in the cycle, the peak is late")

    fig.suptitle("The premium starts building a median of 26 days out and "
                 "tops out 4 days before expiry", y=1.00, fontsize=12.5)
    return P.save(fig, "04_phase_timing_distributions")


# ---------------------------------------------------------------- figure 05
def fig05_oi_ratio(mom: pd.DataFrame):
    """Paired slope chart of the F1/F2 open-interest ratio at each moment."""
    d = mom[["oi_ratio_build", "oi_ratio_peak", "oi_ratio_collapse", "htb_tier"]]
    d = d.replace([np.inf, -np.inf], np.nan)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.5, 6.0),
                                 gridspec_kw={"width_ratios": [1.15, 1]})

    pos = [0, 1, 2]
    for _, r in d.iterrows():
        ys = [r.oi_ratio_build, r.oi_ratio_peak, r.oi_ratio_collapse]
        xs = [p for p, y in zip(pos, ys) if np.isfinite(y) and y > 0]
        vs = [y for y in ys if np.isfinite(y) and y > 0]
        if len(vs) >= 2:
            a1.plot(xs, vs, color=P.TIER_COLORS.get(r.htb_tier, "#999"),
                    lw=0.7, alpha=0.22)
    med = [d[c].replace([np.inf, -np.inf], np.nan).dropna().median()
           for c in ("oi_ratio_build", "oi_ratio_peak", "oi_ratio_collapse")]
    a1.plot(pos, med, color="#2c3e50", lw=3.0, marker="o", ms=9,
            zorder=5, label="median across cycles")
    for p, v in zip(pos, med):
        a1.annotate(f"{v:.1f}x", (p, v), textcoords="offset points",
                    xytext=(0, 16), ha="center", fontsize=10.5,
                    fontweight="bold", color="#2c3e50")
    a1.axhline(1.0, color="#c0392b", ls=":", lw=1.3, label="F1 OI = F2 OI")
    a1.set_yscale("log")
    a1.set_xticks(pos)
    a1.set_xticklabels(["BUILD\n(spread trough)", "PEAK\n(spread top)",
                        "COLLAPSE\n(trigger)"])
    a1.set_ylabel("F1 open interest / F2 open interest  (log scale)")
    a1.legend(loc="upper right")
    a1.set_title("The spread peaks as open interest crosses parity")

    for lbl, col, c in (("at BUILD", "oi_ratio_build", P.C_BUILD),
                        ("at PEAK", "oi_ratio_peak", P.C_PEAK)):
        v = d[col].replace([np.inf, -np.inf], np.nan).dropna()
        v = np.log10(v[v > 0])
        a2.hist(v, bins=28, alpha=0.62, color=c, label=lbl, edgecolor="white")
    a2.axvline(0, color="#c0392b", ls=":", lw=1.3, label="ratio = 1")
    a2.set_xlabel("log10(F1 OI / F2 OI)")
    a2.set_ylabel("cycles")
    a2.legend()
    a2.set_title("Distribution shifts by more than an order of magnitude")

    fig.suptitle(f"Front-month open interest falls from ~{med[0]:.0f}x the next month at the spread "
                 f"trough to ~{med[1]:.1f}x at the spread peak",
                 y=1.00, fontsize=12.5)
    return P.save(fig, "05_oi_ratio_three_moments")


# ---------------------------------------------------------------- figure 06
def fig06_collapse(mom: pd.DataFrame):
    """How much of the build is given back, and does it scale with the premium?"""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.5, 5.8))

    r = mom["retracement"].dropna()
    r = r[(r > -1) & (r < 2.5)]
    a1.hist(r, bins=34, color=P.C_SPREAD, alpha=0.82, edgecolor="white")
    a1.axvline(1.0, color="#2c3e50", ls="--", lw=1.5,
               label="fully retraced")
    a1.axvline(r.median(), color=P.C_COLL, lw=2.0,
               label=f"median {r.median():.2f}")
    a1.set_xlabel("retracement  =  (peak - expiry) / (peak - build)")
    a1.set_ylabel("cycles")
    a1.legend()
    a1.set_title(f"{100*(r > 0.8).mean():.0f}% of cycles give back "
                 f"more than 80% of the build")

    for tier in ("NON", "MOD", "EXT"):
        s = mom[mom.htb_tier == tier]
        a2.scatter(s["peak_b12"], s["amplitude_bps"], s=48, alpha=0.8,
                   color=P.TIER_COLORS[tier], label=P.TIER_LABELS[tier],
                   edgecolor="white", lw=0.6)
    a2.set_xscale("log")
    a2.set_yscale("symlog", linthresh=10)
    a2.set_xlabel("peak b12 borrow rate (annualised, log scale)")
    a2.set_ylabel("cycle amplitude (bps of F1)")
    a2.legend(loc="upper left", fontsize=8.5)
    a2.set_title("Richer borrow, bigger cycle")

    fig.suptitle("The collapse is near-total, and its size scales with the "
                 "borrow premium", y=1.00, fontsize=12.5)
    return P.save(fig, "06_collapse_magnitude")


# ---------------------------------------------------------------- figure 07
def fig07_equity(pnl: pd.DataFrame):
    """Cumulative result, both smoother variants."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 5.8))

    for variant, style, lbl in (("centred", "--", "centred smoother (look-ahead)"),
                                ("trailing", "-", "trailing smoother (implementable)")):
        d = pnl[(pnl.variant == variant) & pnl.S1_bps.notna()]
        d = d.sort_values("entry_ts")
        a1.plot(np.arange(1, len(d) + 1), np.cumsum(d["S1_bps"]),
                ls=style, lw=2.3,
                color="#95a5a6" if variant == "centred" else "#2c3e50",
                label=f"{lbl}  (n={len(d)})")
    a1.axhline(0, color="k", lw=0.8)
    a1.set_xlabel("cycle (chronological)")
    a1.set_ylabel("cumulative S1 result (bps of F1)")
    a1.legend(loc="upper left")
    a1.set_title("Removing the look-ahead removes about a third of the edge")

    d = pnl[(pnl.variant == "trailing") & pnl.S1_bps.notna()]
    for t in bd.TICKERS:
        s = d[d.ticker == t].sort_values("entry_ts")
        if len(s):
            a2.plot(np.arange(1, len(s) + 1), np.cumsum(s["S1_bps"]),
                    lw=1.9, color=P.TICKER_COLORS[t], marker="o", ms=3.4,
                    label=f"{t} ({len(s)})")
    a2.axhline(0, color="k", lw=0.8)
    a2.set_xlabel("cycle (chronological)")
    a2.set_ylabel("cumulative S1 result (bps of F1)")
    a2.legend(fontsize=8, ncol=2)
    a2.set_title("Per name, trailing signal")

    fig.suptitle("Futures calendar spread, costs charged", y=1.00, fontsize=12.5)
    return P.save(fig, "07_equity_curve")


# ---------------------------------------------------------------- figure 08
def fig08_per_cycle(pnl: pd.DataFrame):
    """Every cycle's result, coloured by borrow tier."""
    d = pnl[(pnl.variant == "trailing") & pnl.S1_bps.notna()].copy()
    d = d.sort_values("peak_b12").reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(14, 5.8))
    ax.bar(np.arange(len(d)), d["S1_bps"],
           color=[P.TIER_COLORS[t] for t in d["htb_tier"]], alpha=0.88)
    ax.axhline(0, color="k", lw=0.9)
    ax.set_xlabel("cycles, sorted by peak borrow rate (low to high)")
    ax.set_ylabel("S1 result (bps of F1)")
    handles = [Line2D([], [], color=P.TIER_COLORS[t], lw=7,
                      label=f"{P.TIER_LABELS[t]}  "
                            f"win {win_rate(d.loc[d.htb_tier == t, 'S1_bps']):.0f}%")
               for t in ("NON", "MOD", "EXT") if (d.htb_tier == t).any()]
    ax.legend(handles=handles, loc="upper left")
    ax.set_title("Per-cycle result, trailing signal — losses concentrate where "
                 "the borrow premium is thin")
    return P.save(fig, "08_per_cycle_pnl")


# ---------------------------------------------------------------- figure 09
def fig09_pnl_vs_collapse(pnl: pd.DataFrame):
    """The result is just the spread collapse, as the thesis says it should be."""
    d = pnl[(pnl.variant == "trailing") & pnl.S1_bps.notna()].copy()
    d["reduction"] = d["spread_entry"] - d["spread_exit"]
    d = d[np.isfinite(d["reduction"])]
    fig, ax = plt.subplots(figsize=(11.5, 6.2))
    for t in bd.TICKERS:
        s = d[d.ticker == t]
        if len(s):
            ax.scatter(s["reduction"], s["S1_per_share"], s=52, alpha=0.82,
                       color=P.TICKER_COLORS[t], label=t,
                       edgecolor="white", lw=0.6)
    x, y = d["reduction"].to_numpy(), d["S1_per_share"].to_numpy()
    b, a = np.polyfit(x, y, 1)
    xs = np.linspace(x.min(), x.max(), 100)
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    ax.plot(xs, a + b * xs, color="#2c3e50", lw=2.0, ls="--",
            label=f"fit: slope {b:.2f}, R2 = {r2:.3f}")
    ax.axhline(0, color="k", lw=0.8); ax.axvline(0, color="k", lw=0.8)
    ax.set_xlabel("spread reduction, entry to exit (Rs / share)")
    ax.set_ylabel("S1 result (Rs / share, costs charged)")
    ax.legend(fontsize=8.5, ncol=2)
    ax.set_title("The result is the spread collapse and nothing else\n"
                 "no directional exposure to the underlying")
    return P.save(fig, "09_pnl_vs_collapse")


# ---------------------------------------------------------------- figure 10
def fig10_negative(head: pd.DataFrame):
    """Where the thesis fails: the options expression of the same view."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.5, 5.8))

    d = head[head.S3.notna()]
    a1.scatter(d["spread_entry"] - d["spread_exit"], d["S3"], s=64,
               color="#c0392b", alpha=0.82, edgecolor="white", lw=0.6,
               label="S3 (buy call F2 + sell put F1)")
    if "S1" in d:
        a1.scatter(d["spread_entry"] - d["spread_exit"], d["S1"], s=64,
                   color="#2c3e50", alpha=0.82, edgecolor="white", lw=0.6,
                   label="S1 (futures calendar)")
    a1.axhline(0, color="k", lw=0.9)
    a1.set_xlabel("spread reduction (Rs / share)")
    a1.set_ylabel("result (Rs / lot)")
    a1.legend(fontsize=8.5)
    a1.set_title("Same view, same cycles, opposite outcome")

    names, vals, cols = [], [], []
    for s, c in (("S1", "#2c3e50"), ("S2", "#8e44ad"), ("S3", "#c0392b")):
        v = head[s].dropna()
        if len(v):
            names.append(f"{s}\nn={len(v)}"); vals.append(win_rate(v)); cols.append(c)
    a2.bar(names, vals, color=cols, alpha=0.88)
    a2.axhline(50, color="k", ls="--", lw=1.1, label="coin flip")
    a2.set_ylabel("win rate (%)")
    a2.set_ylim(0, 105)
    a2.legend()
    a2.set_title("S3 is long F2 vega, and the collapse crushes F2 implied vol")

    fig.suptitle("Negative result: the spread thesis is right, but the options "
                 "expression of it loses money", y=1.00, fontsize=12.5)
    return P.save(fig, "10_negative_results")


# ------------------------------------------------- roll mechanics (script 07)
SESSIONS = 20


def _profile(prof: pd.DataFrame, by: str, group: str) -> pd.DataFrame:
    d = prof[(prof.by == by) & (prof.group == group)].set_index("sessions_left").sort_index()
    return d.loc[d.index <= SESSIONS]


def _session_axis(ax, step: int = 2):
    """x = -sessions_left so time runs left to right; label as sessions to expiry."""
    ax.set_xlim(-SESSIONS - 0.3, 0.3)
    ticks = list(range(-SESSIONS, 1, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(-t) for t in ticks])


def _roll_milestones(h: pd.DataFrame) -> dict:
    """Sessions-left where the median F1 share / ratio first crosses each milestone."""
    out = {}
    s = h.sort_index(ascending=False)
    for name, cond in (("roll starts (F1 share < 90%)", s.oi_share_f1_median < 0.90),
                       ("parity (F1/F2 < 1x)", s.oi_ratio_median < 1.0),
                       ("roll ~done (F1 share <= 25%)", s.oi_share_f1_median <= 0.25)):
        hit = s.index[cond.to_numpy()]
        if len(hit):
            out[name] = int(hit[0])
    return out


def fig00_hero(prof: pd.DataFrame):
    """The whole story in one chart: the spread against the open-interest transfer."""
    h, n = _profile(prof, "htb", "HTB"), _profile(prof, "htb", "non-HTB")
    x = -h.index.to_numpy(float)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12.5, 8.8), sharex=True,
                                 gridspec_kw={"height_ratios": [1.35, 1], "hspace": 0.07})
    a1.fill_between(x, h.spread_bps_p25, h.spread_bps_p75, color=P.C_SPREAD, alpha=0.16, lw=0,
                    label="hard-to-borrow cycles, middle 50%")
    a1.plot(x, h.spread_bps_median, color=P.C_SPREAD, lw=3, marker="o", ms=4,
            label=f"hard-to-borrow cycles, median (n={int(h.n.max())})")
    a1.plot(-n.index.to_numpy(float), n.spread_bps_median, color="#7f8c8d", lw=1.8, ls="--",
            label=f"no borrow premium (control), median (n={int(n.n.max())})")
    a1.axhline(0, color="k", lw=0.7, ls=":", alpha=0.5)
    a1.set_ylabel("F1 - F2 spread  (bps of F1)")
    a1.legend(loc="lower left")

    share = h.oi_share_f1_median.to_numpy(float) * 100
    a2.stackplot(x, share, 100 - share, colors=[P.C_F1OI, P.C_F2OI], alpha=0.55,
                 labels=["front month (F1)", "next month (F2)"])
    a2.axhline(50, color="k", lw=0.8, ls=":", alpha=0.6)
    a2.set_ylim(0, 100)
    a2.set_ylabel("share of open interest  (%)")
    a2.set_xlabel("trading sessions to front-month expiry")
    a2.legend(loc="lower left")

    ms = _roll_milestones(h)
    start = ms.get("roll starts (F1 share < 90%)", 9)
    zones = [(-SESSIONS - 0.3, -start, "premium builds,\nroll not yet started", "#27ae60"),
             (-start, -1, "roll underway:\npremium peaks", "#e67e22"),
             (-1, 0.3, "settlement:\ncollapse", "#c0392b")]
    top = a1.get_ylim()[1]
    for lo, hi, lab, col in zones:
        for ax in (a1, a2):
            ax.axvspan(lo, hi, color=col, alpha=0.06, lw=0)
        a1.text((lo + hi) / 2, top * 0.97, lab, ha="center", va="top", fontsize=9, color=col,
                fontweight="semibold")
    ypos = {"roll starts": 85, "parity": 62, "roll ~done": 38}
    for name, sl in ms.items():
        r = h.loc[sl]
        a2.axvline(-sl, color="#2c3e50", lw=1, ls="--", alpha=0.7)
        y = next(v for k, v in ypos.items() if name.startswith(k))
        a2.annotate(f"{name}\n{sl} sessions out, F1/F2 {r.oi_ratio_median:.1f}x",
                    (-sl, y), xytext=(-sl - 0.3, y), fontsize=8, ha="right", va="center", color="#2c3e50",
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#2c3e50", alpha=0.88))
    _session_axis(a2)
    pk = int(h.spread_bps_median.idxmax())
    fig.suptitle("The futures borrow premium builds as the roll begins, peaks while open interest moves "
                 "to the next month,\nand collapses once the roll is done "
                 f"(median peak {h.spread_bps_median.max():.0f} bps of F1, {pk} sessions before expiry)",
                 y=0.995, fontsize=12.5, fontweight="semibold")
    return P.save(fig, "00_roll_drives_the_premium")


def fig14_phase_portrait(sess: pd.DataFrame):
    """Spread against the F1/F2 open-interest ratio: the roll as the clock."""
    d = sess[(sess.oi_ratio > 0.15) & (sess.oi_ratio < 60)].copy()
    edges = np.geomspace(0.15, 60, 19)
    d["b"] = pd.cut(d.oi_ratio, edges)
    fig, ax = plt.subplots(figsize=(12.5, 6.8))
    for _, g in d[d.htb == "HTB"].groupby(["ticker", "cid"]):
        ax.plot(g.oi_ratio, g.spread_bps, color=P.C_SPREAD, lw=0.5, alpha=0.10)
    for grp, col, lw, ls in (("HTB", "#2c3e50", 3.0, "-"), ("non-HTB", "#7f8c8d", 1.8, "--")):
        sub = d[d.htb == grp]
        agg = sub.groupby("b", observed=True).spread_bps.agg(
            med="median", p25=lambda v: v.quantile(.25), p75=lambda v: v.quantile(.75), n="size")
        agg = agg[agg.n >= (40 if grp == "HTB" else 10)]
        mid = np.array([np.sqrt(i.left * i.right) for i in agg.index])
        if grp == "HTB":
            ax.fill_between(mid, agg.p25, agg.p75, color="#2c3e50", alpha=0.12, lw=0,
                            label="hard-to-borrow, middle 50%")
        ax.plot(mid, agg.med, color=col, lw=lw, ls=ls, marker="o" if grp == "HTB" else None, ms=4,
                label=f"{'hard-to-borrow' if grp == 'HTB' else 'no borrow premium (control)'}, median")
    for lvl in (4, 2, 1, 0.5):
        c = "#c0392b" if lvl == 1 else "#7f8c8d"
        ax.axvline(lvl, color=c, lw=1.1, ls=":")
        ax.text(lvl, 0.995, f"{lvl:g}x", transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=9, color=c)
    ax.set_xscale("log")
    ax.set_xlim(60, 0.15)                        # reversed: the roll progresses left to right
    lo, hi = d.spread_bps.quantile([0.03, 0.97])
    ax.set_ylim(lo, hi)
    ax.axhline(0, color="k", lw=0.7, ls=":", alpha=0.5)
    ax.set_xlabel("F1 open interest / F2 open interest  (log scale; the roll progresses left to right)")
    ax.set_ylabel("F1 - F2 spread  (bps of F1, end of session)")
    ax.legend(loc="upper left")
    ax.set_title("Measured on the roll itself: the premium rises as front-month open interest falls from ~16x "
                 "to ~2x the next month,\ntops out between 4x and 1x, and gives back once the front month is "
                 "the minority  (each faint line is one cycle)")
    return P.save(fig, "14_phase_portrait")


def fig15_spread_by_oi(sess: pd.DataFrame, bins: pd.DataFrame):
    """Box plots by OI-ratio bin, and a ticker x bin heatmap."""
    order = list(reversed(roll.OI_BIN_LABELS))       # >16 ... <0.25: roll progresses left to right
    d = sess[sess.htb == "HTB"].dropna(subset=["oi_ratio"]).copy()
    d["bin"] = roll.oi_bin(d.oi_ratio).astype(str)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 6.2), gridspec_kw={"width_ratios": [1.15, 1]})
    data = [d.loc[d.bin == b, "spread_bps"].dropna() for b in order]
    bp = a1.boxplot(data, tick_labels=[f"{b}x" for b in order], showfliers=False, patch_artist=True,
                    widths=0.6, medianprops=dict(color="#2c3e50", lw=2))
    meds = [v.median() for v in data]
    peak = int(np.nanargmax(meds))
    for i, box in enumerate(bp["boxes"]):
        box.set_facecolor(P.C_SPREAD if i == peak else "#f5cba7")
        box.set_alpha(0.85)
    ymin = a1.get_ylim()[0]
    for i, v in enumerate(data):
        a1.text(i + 1, ymin, f"n={len(v)}", ha="center", va="bottom", fontsize=8, color="#555")
    a1.axhline(0, color="k", lw=0.7, ls=":", alpha=0.5)
    a1.set_xlabel("F1 / F2 open-interest ratio  (roll progresses left to right)")
    a1.set_ylabel("F1 - F2 spread  (bps of F1, end of session)")
    a1.set_title(f"Hard-to-borrow cycles: widest at {order[peak]}x (median {meds[peak]:.0f} bps)")

    t = bins[bins.by == "ticker"].pivot(index="group", columns="oi_bin", values="spread_bps_median")
    t = t.reindex(index=bd.TICKERS, columns=order)
    vmax = np.nanpercentile(np.abs(t.to_numpy(float)), 95)
    im = a2.imshow(t.to_numpy(float), cmap="RdBu_r", vmin=-vmax, vmax=vmax, aspect="auto")
    a2.set_xticks(range(len(order)), [f"{b}x" for b in order], rotation=30, ha="right")
    a2.set_yticks(range(len(t.index)), t.index)
    for i in range(t.shape[0]):
        for j in range(t.shape[1]):
            v = t.iat[i, j]
            if np.isfinite(v):
                a2.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=8,
                        color="white" if abs(v) > 0.6 * vmax else "#222")
    a2.grid(False)
    fig.colorbar(im, ax=a2, fraction=0.04, label="median spread (bps)")
    a2.set_title("Median spread by ticker (all cycles)")
    fig.suptitle("The premium is a function of how far the roll has progressed", y=1.01, fontsize=12.5,
                 fontweight="semibold")
    return P.save(fig, "15_spread_by_oi_ratio")


def fig16_midpoint(ev: pd.DataFrame):
    """Cycles aligned on the session the front month becomes the minority."""
    d = ev[(ev.by == "htb") & (ev.group == "HTB")].set_index("rel_mid").sort_index()
    d = d.loc[(d.index >= -12) & (d.index <= 2)]
    fig, ax = plt.subplots(figsize=(12.5, 6.2))
    x = d.index.to_numpy(float)
    ax.fill_between(x, d.spread_bps_p25, d.spread_bps_p75, color=P.C_SPREAD, alpha=0.16, lw=0)
    ax.plot(x, d.spread_bps_median, color=P.C_SPREAD, lw=3, marker="o", ms=4,
            label="F1 - F2 spread, median (hard-to-borrow cycles)")
    ax.set_ylabel("F1 - F2 spread  (bps of F1)", color=P.C_SPREAD)
    ax.axvline(0, color="#c0392b", lw=1.6, ls="--")
    ax.text(0.12, 0.97, "front month becomes\nthe minority (F1/F2 < 1x)", transform=ax.get_xaxis_transform(),
            va="top", fontsize=9, color="#c0392b")
    ax2 = ax.twinx()
    ax2.spines["right"].set_visible(True)
    ax2.plot(x, d.oi_share_f1_median * 100, color=P.C_F1OI, lw=2, label="front-month share of OI, median")
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("front-month share of open interest (%)", color=P.C_F1OI)
    ax2.grid(False)
    ax.set_xlabel("sessions relative to the roll midpoint (0 = first session with F1/F2 OI < 1)")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="lower left")
    ax.set_title("Aligned on the roll midpoint: the spread holds near its peak while open interest crosses "
                 "over,\nthen converges in the sessions after the front month becomes the minority")
    return P.save(fig, "16_roll_midpoint_event")


def fig17_by_ticker(prof: pd.DataFrame):
    """The hero chart for every ticker."""
    fig, axes = plt.subplots(2, 4, figsize=(16, 7.6), sharex=True)
    for ax, t in zip(axes.flat, bd.TICKERS):
        d = _profile(prof, "ticker", t)
        x = -d.index.to_numpy(float)
        ax2 = ax.twinx()
        ax2.fill_between(x, 0, d.oi_share_f1_median * 100, color=P.C_F1OI, alpha=0.12, lw=0)
        ax2.set_ylim(0, 100)
        ax2.set_yticks([0, 50, 100])
        ax2.tick_params(labelsize=7, colors=P.C_F1OI)
        ax2.grid(False)
        ax.fill_between(x, d.spread_bps_p25, d.spread_bps_p75, color=P.C_SPREAD, alpha=0.15, lw=0)
        ax.plot(x, d.spread_bps_median, color=P.C_SPREAD, lw=2.2)
        ax.axhline(0, color="k", lw=0.6, ls=":", alpha=0.5)
        ax.set_title(f"{t}  (n={int(d.n.max())} cycles)", fontsize=10.5)
        ax.set_zorder(ax2.get_zorder() + 1)
        ax.patch.set_visible(False)
        _session_axis(ax, step=5)
    last = axes.flat[-1]
    last.axis("off")
    last.text(0.05, 0.55, "orange: F1 - F2 spread, bps of F1\n(median; middle 50% shaded)\n\n"
                          "red area: front-month share of\nopen interest (right axis, %)\n\n"
                          "x: trading sessions to expiry", fontsize=10, va="center")
    for ax in axes[1]:
        ax.set_xlabel("sessions to expiry")
    for ax in axes[:, 0]:
        ax.set_ylabel("spread (bps)")
    build = {}
    for t in bd.TICKERS:
        d = _profile(prof, "ticker", t).spread_bps_median
        build[t] = d.loc[1:5].median() - d.loc[15:20].median()
    strong = [t for t, v in sorted(build.items(), key=lambda kv: -kv[1]) if v >= 20]
    weak = [t for t in bd.TICKERS if t not in strong]
    fig.suptitle(f"By ticker: the spread builds into the roll in {', '.join(strong)} "
                 f"(+20 bps or more from 15-20 to 1-5 sessions out);\n{', '.join(weak)} build little "
                 "and mostly just converge at expiry  (all cycles)", y=1.02, fontsize=12.5, fontweight="semibold")
    fig.tight_layout()
    return P.save(fig, "17_roll_profile_by_ticker")


def main() -> int:
    mom = load("cycle_moments.csv")
    etc = load("event_time_curve.csv")
    pnl = load("pnl_per_cycle.csv")
    head = load("pnl_headline_22.csv")
    prof = load("roll_profile_by_session.csv")
    sess = load("roll_sessions.csv")

    made = [
        fig00_hero(prof),
        fig01_mechanism(mom), fig02_roll_leads(mom), fig03_event_time(etc),
        fig04_phase_timing(mom), fig05_oi_ratio(mom), fig06_collapse(mom),
        fig07_equity(pnl), fig08_per_cycle(pnl), fig09_pnl_vs_collapse(pnl),
        fig10_negative(head),
        fig14_phase_portrait(sess), fig15_spread_by_oi(sess, load("spread_by_oi_ratio.csv")),
        fig16_midpoint(load("roll_midpoint_event.csv")), fig17_by_ticker(prof),
    ]
    for p in made:
        print(f"  {p.name}  ({p.stat().st_size/1e3:.0f} kB)")
    print(f"{len(made)} figures written to figures/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
