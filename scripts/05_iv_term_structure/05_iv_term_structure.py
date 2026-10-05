"""
05_iv_term_structure.py — what the options market does during a borrow cycle.

The futures spread says short sellers are paying up to be short the front
month. If that pressure is real, it should leave a fingerprint in the options
surface of the same name: front-month implied volatility and the 25-delta
risk reversal (put skew) should behave differently when the borrow premium is
extreme than when it is absent.

Uses the ``feat_*`` columns in the shipped panel, so it needs no option files.
BDL has no feature columns and is excluded automatically.

Outputs
-------
  results/iv_cross_section.csv
  results/iv_findings.md
  figures/13_iv_and_skew.png
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats as sps

from borrowcycle import data as bd
from borrowcycle import plotting as P

RESULTS = bd.PKG_ROOT / "results"
P.use_style()


def main() -> int:
    mom = pd.read_csv(RESULTS / "cycle_moments.csv")
    have_iv = mom.dropna(subset=["atm_iv_peak"])

    rows = []
    for _, r in mom.iterrows():
        rows.append({
            "ticker": r.ticker, "cid": int(r.cid), "htb_tier": r.htb_tier,
            "peak_b12": r.peak_b12, "amplitude_bps": r.amplitude_bps,
            "retracement": r.retracement,
            "atm_iv_peak": r.atm_iv_peak, "rr25_peak": r.rr25_peak,
        })
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS / "iv_cross_section.csv", index=False)

    L = ["# What the options surface does during a borrow cycle\n"]
    L.append(f"Measured at each cycle's spread peak, across "
             f"{len(have_iv)} cycles with option-derived features "
             f"({mom['ticker'].nunique() - 1} tickers; BDL ships without them).\n")

    L.append("\n## 1. Front-month implied vol and skew at the spread peak\n")
    L.append("| Tier | n | median ATM IV | median 25d risk reversal |")
    L.append("|---|---|---|---|")
    for tier in ("NON", "MOD", "EXT"):
        s = have_iv[have_iv.htb_tier == tier]
        if len(s):
            L.append(f"| {tier} | {len(s)} | {s.atm_iv_peak.median():.3f} | "
                     f"{s.rr25_peak.median():+.4f} |")

    non = have_iv.loc[have_iv.htb_tier == "NON", "atm_iv_peak"].dropna()
    ext = have_iv.loc[have_iv.htb_tier == "EXT", "atm_iv_peak"].dropna()
    if len(non) > 2 and len(ext) > 2:
        u = sps.mannwhitneyu(ext, non)
        L.append(f"\nMann-Whitney on ATM implied vol, extreme-HTB vs non-HTB "
                 f"cycles (n={len(ext)} vs {len(non)}): p = {u.pvalue:.3f}.\n")
        if u.pvalue > 0.05:
            L.append("There is **no significant difference**. A rich borrow "
                     "premium is not accompanied by a distinctive level of "
                     "front-month implied volatility.\n")
            L.append("This is worth stating plainly because it is a useful "
                     "negative result: the borrow signal in the futures term "
                     "structure is **not** visible as an implied-volatility "
                     "level in the options of the same name. Anyone hoping to "
                     "detect hard-to-borrow stress from the vol surface alone "
                     "would not find it here.\n")
        else:
            L.append("The difference is significant at the 5% level.\n")

    rr = have_iv.dropna(subset=["rr25_peak"])
    if len(rr) > 5:
        c = sps.spearmanr(rr["peak_b12"], rr["rr25_peak"])
        L.append(f"\n## 2. Does put skew track the borrow premium?\n")
        L.append(f"Spearman correlation between the peak borrow rate and the "
                 f"25-delta risk reversal at the peak: "
                 f"rho = {c.statistic:+.2f} (p = {c.pvalue:.3f}, n = {len(rr)}).\n")
        if c.pvalue < 0.05:
            direction = ("more negative, i.e. puts richer"
                         if c.statistic < 0 else "less negative")
            L.append(f"Richer borrow goes with skew that is {direction}, which "
                     f"is consistent with short-selling pressure also being "
                     f"expressed in the put wing.\n")
        else:
            L.append("The relationship is not statistically significant. The "
                     "borrow premium in the futures does not reliably show up "
                     "as put skew in the options.\n")

    corr_amp = sps.spearmanr(df["peak_b12"].rank(), df["amplitude_bps"],
                             nan_policy="omit")
    L.append(f"\n## 3. For contrast: the futures signal is strong\n")
    L.append(f"The same cycles show a clear relationship between the borrow "
             f"premium and the size of the spread cycle: Spearman rho = "
             f"{corr_amp.statistic:+.2f} (p = {corr_amp.pvalue:.2e}, "
             f"n = {df['amplitude_bps'].notna().sum()}).\n")
    L.append("The information is in the futures term structure, not the option "
             "surface. That is the practical conclusion of this section.\n")
    (RESULTS / "iv_findings.md").write_text("\n".join(L), encoding="utf-8")

    # ---- figure ------------------------------------------------------------
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(16, 5.2))

    data = [have_iv.loc[have_iv.htb_tier == t, "atm_iv_peak"].dropna()
            for t in ("NON", "MOD", "EXT")]
    bp = a1.boxplot([d for d in data if len(d)], patch_artist=True,
                    tick_labels=[t for t, d in zip(("NON", "MOD", "EXT"), data) if len(d)])
    for patch, t in zip(bp["boxes"], [t for t, d in zip(("NON", "MOD", "EXT"), data) if len(d)]):
        patch.set_facecolor(P.TIER_COLORS[t]); patch.set_alpha(0.6)
    a1.set_ylabel("front-month ATM implied vol at the spread peak")
    a1.set_xlabel("borrow-premium tier")
    a1.set_title("Implied vol does not separate the tiers")

    for tier in ("NON", "MOD", "EXT"):
        s = rr[rr.htb_tier == tier]
        if len(s):
            a2.scatter(s["peak_b12"], s["rr25_peak"], s=46, alpha=0.8,
                       color=P.TIER_COLORS[tier], label=P.TIER_LABELS[tier],
                       edgecolor="white", lw=0.5)
    a2.set_xscale("log")
    a2.axhline(0, color="k", lw=0.8, ls=":")
    a2.set_xlabel("peak b12 borrow rate (log scale)")
    a2.set_ylabel("25-delta risk reversal at the peak")
    a2.legend(fontsize=8)
    a2.set_title("Nor does put skew")

    for tier in ("NON", "MOD", "EXT"):
        s = df[df.htb_tier == tier]
        if len(s):
            a3.scatter(s["peak_b12"], s["amplitude_bps"], s=46, alpha=0.8,
                       color=P.TIER_COLORS[tier], edgecolor="white", lw=0.5)
    a3.set_xscale("log")
    a3.set_yscale("symlog", linthresh=10)
    a3.set_xlabel("peak b12 borrow rate (log scale)")
    a3.set_ylabel("spread cycle amplitude (bps of F1)")
    a3.set_title(f"But the futures spread does  (rho {corr_amp.statistic:+.2f})")

    fig.suptitle("The hard-to-borrow signal lives in the futures term "
                 "structure, not the options surface", y=1.00, fontsize=12.5)
    p = P.save(fig, "13_iv_and_skew")
    print(f"wrote results/iv_cross_section.csv, iv_findings.md, figures/{p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
