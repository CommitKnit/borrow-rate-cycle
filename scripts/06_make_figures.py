"""
06_make_figures.py — render every narrative figure from results/.

Runs in seconds and needs only the CSVs written by scripts 01-05, so the
figures can be rebuilt without re-running any analysis.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from borrowcycle import data as bd
from borrowcycle import plotting as P
from borrowcycle.cycle import detect_moments, smooth, spread
from borrowcycle.stats import win_rate

RESULTS = bd.PKG_ROOT / "results"
P.use_style()


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(RESULTS / name)


# ---------------------------------------------------------------- figure 01
def fig01_mechanism(mom: pd.DataFrame):
    """One annotated cycle: the pattern the whole repo is about."""
    cand = mom[(mom.ticker == "RVNL") & mom.amplitude_bps.notna()]
    row = cand.loc[cand.amplitude_bps.idxmax()]
    t, cid = row.ticker, int(row.cid)
    cyc = next(c for i, c in bd.iter_cycles(t) if i == cid)
    m = detect_moments(cyc, t, cid)

    sp_raw = spread(cyc).to_numpy(float)
    sp_sm = smooth(spread(cyc))
    b12_sm = smooth(cyc["b12"])

    fig, ax = plt.subplots(figsize=(12.5, 6.4))
    x = P.bar_axis(ax, cyc.index)
    ax.plot(x, sp_raw, color=P.C_SPREAD, lw=0.7, alpha=0.30)
    ax.plot(x, sp_sm, color=P.C_SPREAD, lw=2.3, label="F1 - F2 spread (smoothed)")
    ax.axhline(0, color="k", lw=0.7, ls=":", alpha=0.5)
    ax.set_ylabel("F1 - F2 spread  (Rs / share)", color=P.C_SPREAD)
    ax.tick_params(axis="y", colors=P.C_SPREAD)

    ax2 = ax.twinx()
    ax2.spines["right"].set_visible(True)
    ax2.plot(x, b12_sm, color=P.C_B12, lw=1.7, alpha=0.85,
             label="b12 borrow rate (smoothed)")
    ax2.set_ylabel("b12 annualised borrow rate", color=P.C_B12)
    ax2.tick_params(axis="y", colors=P.C_B12)
    ax2.grid(False)

    marks = [(m.i_build, "BUILD", P.C_BUILD, "^"),
             (m.i_peak, "PEAK", P.C_PEAK, "D")]
    if m.i_collapse is not None:
        marks.append((m.i_collapse, "COLLAPSE", P.C_COLL, "v"))

    items = []
    for i, lbl, col, mk in marks:
        ax.axvline(i, color=col, lw=1.4, ls="--", alpha=0.85)
        ax.scatter([i], [sp_sm[i]], s=150, marker=mk, color=col,
                   edgecolor="white", linewidth=1.2, zorder=6)
        dte = cyc["F1_dte"].iloc[i]
        items.append((i, sp_sm[i],
                      f"{lbl}\nDTE {dte:.0f}   Rs{sp_sm[i]:+.1f}", col))

    handles = [Line2D([], [], color=P.C_SPREAD, lw=2.3, label="F1 - F2 spread"),
               Line2D([], [], color=P.C_B12, lw=1.7, label="b12 borrow rate")]
    handles += [Line2D([], [], color=c, marker=mk, ls="--", lw=1.4, label=l)
                for _, l, c, mk in marks]
    ax.legend(handles=handles, loc="upper left", fontsize=8.5)
    P.annotate_no_overlap(ax, items)

    ax.set_title(f"{t} cycle {cid} — the borrow premium builds, peaks, "
                 f"then collapses into expiry\n"
                 f"amplitude {row.amplitude_bps:.0f} bps of F1, "
                 f"{100*row.retracement:.0f}% retraced by settlement")
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
    a1.set_title("The roll is already done by the time the spread peaks")

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

    fig.suptitle("Front-month open interest falls from ~20x the next month to "
                 "parity between the spread trough and the spread peak",
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


def main() -> int:
    mom = load("cycle_moments.csv")
    etc = load("event_time_curve.csv")
    pnl = load("pnl_per_cycle.csv")
    head = load("pnl_headline_22.csv")

    made = [
        fig01_mechanism(mom), fig02_roll_leads(mom), fig03_event_time(etc),
        fig04_phase_timing(mom), fig05_oi_ratio(mom), fig06_collapse(mom),
        fig07_equity(pnl), fig08_per_cycle(pnl), fig09_pnl_vs_collapse(pnl),
        fig10_negative(head),
    ]
    for p in made:
        print(f"  {p.name}  ({p.stat().st_size/1e3:.0f} kB)")
    print(f"{len(made)} figures written to figures/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
