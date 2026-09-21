"""
02_roll_evidence.py — does the futures roll cause the collapse?

The mechanism claims short sellers hold the front month because it is the
cheap way to be short a hard-to-borrow name, and that the premium dies when
they roll to the next month. If that is right, open interest should leave the
front month *before* the spread tops out, not after.

This script builds the event study: F1 and F2 open interest aligned on each
cycle's spread peak, normalised so cycles of different size are comparable.

Outputs
-------
  results/roll_lead_lag.csv       per-cycle lead/lag and OI levels
  results/roll_event_study.csv    normalised OI trajectory in event time
  figures/11_roll_event_study.png
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from borrowcycle import data as bd
from borrowcycle import plotting as P
from borrowcycle.cycle import detect_moments, htb_tier, peak_b12, smooth, spread
from borrowcycle.stats import binom_p

RESULTS = bd.PKG_ROOT / "results"
TAU_LO, TAU_HI = -250, 100
P.use_style()


def main() -> int:
    rows, f1_curves, f2_curves, sp_curves = [], {}, {}, {}

    for t in bd.TICKERS:
        for cid, cyc in bd.iter_cycles(t):
            m = detect_moments(cyc, t, cid)
            if m is None or "F1_oi" not in cyc.columns:
                continue
            f1 = cyc["F1_oi"].ffill().to_numpy(float)
            f2 = cyc["F2_oi"].ffill().to_numpy(float)
            sm = smooth(spread(cyc))
            if not np.isfinite(f1).any() or not np.isfinite(f2).any():
                continue

            i_oi = int(np.nanargmax(f1))
            lead = m.i_peak - i_oi
            rows.append({
                "ticker": t, "cid": cid, "htb_tier": htb_tier(peak_b12(cyc)),
                "i_f1_oi_peak": i_oi, "i_spread_peak": m.i_peak,
                "lead_bars": lead, "lead_sessions": lead / bd.BARS_PER_DAY,
                "oi_leads": bool(lead > 0),
                "f1_oi_at_own_peak": f1[i_oi],
                "f1_oi_at_spread_peak": f1[m.i_peak],
                "f1_oi_pct_of_max_at_spread_peak": (
                    100 * f1[m.i_peak] / f1[i_oi] if f1[i_oi] > 0 else np.nan),
            })

            tau = np.arange(len(cyc)) - m.i_peak
            keep = (tau >= TAU_LO) & (tau <= TAU_HI)
            mx1 = np.nanmax(f1)
            mx2 = np.nanmax(f2)
            if mx1 > 0:
                f1_curves[(t, cid)] = pd.Series(f1[keep] / mx1, index=tau[keep])
            if mx2 > 0:
                f2_curves[(t, cid)] = pd.Series(f2[keep] / mx2, index=tau[keep])
            amp = sm[m.i_peak] - sm[m.i_build]
            if amp > 1e-9:
                sp_curves[(t, cid)] = pd.Series(
                    (sm[keep] - sm[m.i_build]) / amp, index=tau[keep])

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "roll_lead_lag.csv", index=False)

    grid = np.arange(TAU_LO, TAU_HI + 1)
    ev = {"tau": grid, "session": grid / bd.BARS_PER_DAY}
    for name, curves in (("f1_oi", f1_curves), ("f2_oi", f2_curves),
                         ("spread", sp_curves)):
        mat = pd.DataFrame({i: s.reindex(grid) for i, s in enumerate(curves.values())})
        ev[f"{name}_median"] = mat.median(axis=1).to_numpy()
        ev[f"{name}_q25"] = mat.quantile(0.25, axis=1).to_numpy()
        ev[f"{name}_q75"] = mat.quantile(0.75, axis=1).to_numpy()
        ev[f"{name}_n"] = mat.notna().sum(axis=1).to_numpy()
    ev = pd.DataFrame(ev)
    ev.to_csv(RESULTS / "roll_event_study.csv", index=False)

    k, n = int(df["oi_leads"].sum()), len(df)
    print(f"F1 OI peaks before the spread peak in {k}/{n} ({100*k/n:.0f}%) "
          f"of cycles, binomial p={binom_p(k, n):.2e}")
    print(f"median lead: {df['lead_sessions'].median():.1f} sessions")
    print(f"at the spread peak, F1 OI has already fallen to "
          f"{df['f1_oi_pct_of_max_at_spread_peak'].median():.0f}% of its own maximum")

    # ---- figure ------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(12.5, 6.4))
    s = ev["session"]
    ax.plot(s, ev["f1_oi_median"], color=P.C_F1OI, lw=2.5,
            label="F1 open interest (front month)")
    ax.fill_between(s, ev["f1_oi_q25"], ev["f1_oi_q75"], color=P.C_F1OI, alpha=0.16)
    ax.plot(s, ev["f2_oi_median"], color=P.C_F2OI, lw=2.5,
            label="F2 open interest (next month)")
    ax.fill_between(s, ev["f2_oi_q25"], ev["f2_oi_q75"], color=P.C_F2OI, alpha=0.16)
    ax.set_ylabel("open interest, share of the cycle's own maximum")
    ax.set_xlabel("trading sessions relative to the spread peak")
    ax.axvline(0, color="k", lw=1.3, ls="--", alpha=0.8)
    ax.set_xlim(-12, 3)

    ax2 = ax.twinx()
    ax2.spines["right"].set_visible(True)
    ax2.plot(s, ev["spread_median"], color=P.C_SPREAD, lw=2.0, ls=":",
             label="F1 - F2 spread (normalised)")
    ax2.set_ylabel("spread, normalised", color=P.C_SPREAD)
    ax2.tick_params(axis="y", colors=P.C_SPREAD)
    ax2.grid(False)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=9)
    ax.set_title(f"Event study: the roll out of the front month is already "
                 f"underway while the spread is still rising\n"
                 f"F1 open interest peaks first in {k} of {n} cycles "
                 f"(median lead {df['lead_sessions'].median():.1f} sessions)")
    p = P.save(fig, "11_roll_event_study")
    print(f"wrote results/roll_lead_lag.csv, roll_event_study.csv, figures/{p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
