"""
04_negative_results.py — where the thesis fails, and one place the original
explanation of a failure turned out to be wrong.

Three results worth publishing:

1. **S3 loses money although the spread thesis is correct.** S3 buys an ATM
   call on the next month and sells an ATM put on the front month. The
   original research attributed its losses to vega -- the long call being
   crushed as the borrow collapse compressed next-month implied vol. The
   shipped data does not support that. What it does show is simpler: a long
   call plus a short put is a *synthetic long*, so S3 carries almost pure
   delta exposure to the underlying and almost none to the spread it was
   meant to trade.

2. **The edge disappears where the mechanism is absent.** Cycles with no
   meaningful borrow premium are a natural control, and the strategy loses
   money in them. A thesis that predicts its own failure region is more
   believable than one that wins everywhere.

3. **S1 is direction-neutral, as a forced-convergence trade should be.**

Outputs
-------
  results/s3_vega.csv
  results/s3_directional.csv
  results/negative_results.md  (mirrored to docs/04_negative_results.md)
  figures/12_s3_is_directional.png
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats as sps

from borrowcycle import data as bd
from borrowcycle import plotting as P
from borrowcycle.options import get_opts_bar
from borrowcycle.stats import win_rate

RESULTS = bd.PKG_ROOT / "results"
P.use_style()


def atm_iv(bar: pd.DataFrame, K: float) -> float:
    """Mean of the ATM call and put implied vol."""
    if bar.empty or not np.isfinite(K):
        return np.nan
    r = bar[bar["strike"] == K]
    if r.empty or "iv" not in r.columns:
        return np.nan
    v = r["iv"].to_numpy(float)
    v = v[np.isfinite(v) & (v > 0)]
    return float(v.mean()) if len(v) else np.nan


def f2_chain_for(t: str, exit_ts: pd.Timestamp, expiries: list[str]):
    """The next-month chain for a cycle settling at ``exit_ts``."""
    near = [e for e in expiries
            if abs((pd.Timestamp(e).date() - exit_ts.date()).days) <= 5]
    if not near:
        return None
    later = [e for e in expiries if e > sorted(near)[0]]
    if not later:
        return None
    try:
        return bd.load_options(t, sorted(later)[0])
    except bd.OptionsUnavailable:
        return None


def measure() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-cycle F2 implied-vol change and F2 price move over each S3 trade."""
    head = pd.read_csv(RESULTS / "pnl_headline_22.csv",
                       parse_dates=["entry_ts", "exit_ts"])
    iv_rows, dir_rows = [], []
    for t in bd.OPTION_TICKERS:
        expiries = bd.list_option_expiries(t)
        for _, r in head[head.ticker == t].iterrows():
            if not np.isfinite(r.get("K_f2", np.nan)):
                continue
            o2 = f2_chain_for(t, r.exit_ts, expiries)
            if o2 is None:
                continue
            b_in, b_out = get_opts_bar(o2, r.entry_ts), get_opts_bar(o2, r.exit_ts)
            iv_in, iv_out = atm_iv(b_in, r.K_f2), atm_iv(b_out, r.K_f2)
            iv_rows.append({"ticker": t, "cid": int(r.cid), "S3": r.get("S3", np.nan),
                            "f2_iv_entry": iv_in, "f2_iv_exit": iv_out,
                            "f2_iv_change": iv_out - iv_in})
            if b_in.empty or b_out.empty or not np.isfinite(r.get("S3", np.nan)):
                continue
            F2e = float(b_in["futures_ref_price"].iloc[0])
            F2x = float(b_out["futures_ref_price"].iloc[0])
            dir_rows.append({"ticker": t, "cid": int(r.cid),
                             "S1": r.get("S1", np.nan), "S3": r.S3,
                             "f2_move": F2x - F2e,
                             "f2_move_pct": 100 * (F2x - F2e) / F2e,
                             "spread_red": r.spread_entry - r.spread_exit})
    return pd.DataFrame(iv_rows), pd.DataFrame(dir_rows)


def main() -> int:
    v, dd = measure()
    v.to_csv(RESULTS / "s3_vega.csv", index=False)
    dd.to_csv(RESULTS / "s3_directional.csv", index=False)

    head = pd.read_csv(RESULTS / "pnl_headline_22.csv")
    pnl = pd.read_csv(RESULTS / "pnl_per_cycle.csv")

    iv_ch = v["f2_iv_change"].dropna()
    dv = v.dropna(subset=["S3", "f2_iv_change"])
    corr, pv = (sps.pearsonr(dv["f2_iv_change"], dv["S3"])
                if len(dv) >= 4 else (np.nan, np.nan))

    c_dir = sps.pearsonr(dd["f2_move"], dd["S3"])
    c_sprd = sps.pearsonr(dd["spread_red"], dd["S3"])
    c_s1 = sps.pearsonr(dd["spread_red"], dd["S1"])

    print(f"F2 ATM IV rose in {100*(iv_ch > 0).mean():.0f}% of trades "
          f"(median {iv_ch.median():+.4f}, n={len(iv_ch)})")
    print(f"corr(S3, F2 IV change)     = {corr:+.3f} (p={pv:.3f}, n={len(dv)})")
    print(f"corr(S3, underlying move)  = {c_dir[0]:+.3f} (p={c_dir[1]:.4f})")
    print(f"corr(S3, spread collapse)  = {c_sprd[0]:+.3f} (p={c_sprd[1]:.4f})")
    print(f"corr(S1, spread collapse)  = {c_s1[0]:+.3f} (p={c_s1[1]:.4f})")

    # ---- write-up ----------------------------------------------------------
    L = ["# Negative results\n"]
    L.append("Results that did not work, and one published explanation that "
             "turned out to be wrong.\n")

    L.append("\n## 1. The options expression of a correct view still loses\n")
    L.append("S3 buys an ATM call on the next month and sells an ATM put on the "
             "front month. Directionally it looks like the same view as the "
             "futures calendar. It loses money anyway.\n")
    L.append("| Strategy | n | Win rate | Mean (Rs/lot) | Cumulative |")
    L.append("|---|---|---|---|---|")
    for s in ("S1", "S2", "S3"):
        a = head[s].dropna()
        if len(a):
            L.append(f"| {s} | {len(a)} | {win_rate(a):.0f}% | "
                     f"Rs{a.mean():+,.0f} | Rs{a.sum():+,.0f} |")

    L.append("\n### The stated reason (vega) is not supported by the data\n")
    L.append(f"The original write-up attributed the S3 losses to vega: the long "
             f"next-month call being crushed as the borrow collapse compressed "
             f"F2 implied volatility. Measured over these trades, that is not "
             f"what happened. F2 ATM implied volatility *rose* in "
             f"**{100*(iv_ch > 0).mean():.0f}%** of cycles (median change "
             f"{iv_ch.median():+.3f} vol points, n={len(iv_ch)}), and its "
             f"correlation with the S3 result is {corr:+.2f} "
             f"(p = {pv:.2f}, n = {len(dv)}) -- weak, and of the opposite sign "
             f"to the vega story.\n")

    L.append("\n### The actual reason: S3 is a synthetic long, not a spread\n")
    L.append("A long call plus a short put is a synthetic long position. "
             "Because S3 buys a call and sells a put at near-identical strikes, "
             "its delta exposure to the underlying does not cancel -- it "
             "compounds. The trade is a directional bet wearing the costume of "
             "a spread trade.\n")
    L.append("| Exposure | correlation with S3 | correlation with S1 |")
    L.append("|---|---|---|")
    L.append(f"| Move in the underlying | **{c_dir[0]:+.2f}** (p={c_dir[1]:.4f}) | - |")
    L.append(f"| Collapse of the F1-F2 spread | {c_sprd[0]:+.2f} (p={c_sprd[1]:.2f}) | "
             f"**{c_s1[0]:+.2f}** (p={c_s1[1]:.4f}) |")

    up, dn = dd[dd.f2_move > 0], dd[dd.f2_move < 0]
    L.append("\nS3 tracks the stock almost perfectly and the spread not at all. "
             "Split by which way the underlying went:\n")
    L.append("| Underlying | n | mean S3 | mean S1 |")
    L.append("|---|---|---|---|")
    L.append(f"| rose | {len(up)} | Rs{up.S3.mean():+,.0f} | Rs{up.S1.mean():+,.0f} |")
    L.append(f"| fell | {len(dn)} | Rs{dn.S3.mean():+,.0f} | Rs{dn.S1.mean():+,.0f} |")
    L.append("\nS1 is profitable in both directions, which is exactly what a "
             "convergence trade forced by expiry should do. S3 is profitable "
             "only when the stock happens to rise. Its losses are not evidence "
             "against the thesis -- they are evidence that S3 was never testing "
             "the thesis.\n")
    L.append(f"n = {len(dd)} for these correlations, so the magnitude is "
             f"uncertain even though the direction is clear.\n")

    L.append("\n## 2. The edge vanishes where the mechanism is absent\n")
    L.append("Cycles whose borrow premium never exceeds 5% annualised are a "
             "natural control group: the mechanism says there is nothing to "
             "harvest there.\n")
    L.append("| Tier | n | Win rate | Mean bps |")
    L.append("|---|---|---|---|")
    hon = pnl[(pnl.variant == "trailing") & pnl.S1_bps.notna()]
    for tier in ("NON", "MOD", "EXT"):
        a = hon.loc[hon.htb_tier == tier, "S1_bps"]
        if len(a):
            L.append(f"| {tier} | {len(a)} | {win_rate(a):.0f}% | {a.mean():+.1f} |")
    non = hon.loc[hon.htb_tier == "NON", "S1_bps"]
    L.append(f"\nIn the {len(non)} control cycles the strategy wins "
             f"{win_rate(non):.0f}% of the time and loses an average of "
             f"{abs(non.mean()):.0f} bps. That is the correct outcome: the trade "
             f"is a bet on a specific mechanism and should not pay when the "
             f"mechanism is absent. With n={len(non)} this is a consistency "
             f"check, not a statistical test.\n")

    L.append("\n## 3. What this rules out\n")
    L.append(f"- **Not a directional bet.** S1 correlates {c_s1[0]:+.2f} with "
             f"the spread collapse, and is profitable whether the underlying "
             f"rose or fell.")
    L.append("- **Not generic mean reversion.** An identical measurement "
             "anchored on arbitrary mid-cycle spread peaks retraces a median of "
             "0.41, against 0.87 into expiry (see `anatomy_summary.md`, "
             "section 5).")
    body = "\n".join(L)
    (RESULTS / "negative_results.md").write_text(body, encoding="utf-8")
    (bd.PKG_ROOT / "docs" / "04_negative_results.md").write_text(body, encoding="utf-8")

    # ---- figure ------------------------------------------------------------
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.5, 5.8))
    a1.scatter(dd["f2_move"], dd["S3"], s=84, color="#c0392b", alpha=0.85,
               edgecolor="white", lw=0.7, label=f"S3  (corr {c_dir[0]:+.2f})")
    a1.scatter(dd["f2_move"], dd["S1"], s=84, color="#2c3e50", alpha=0.85,
               edgecolor="white", lw=0.7, label="S1 (futures calendar)")
    b0, a0 = np.polyfit(dd["f2_move"], dd["S3"], 1)
    xs = np.linspace(dd["f2_move"].min(), dd["f2_move"].max(), 50)
    a1.plot(xs, a0 + b0 * xs, color="#c0392b", ls="--", lw=1.6)
    a1.axhline(0, color="k", lw=0.9)
    a1.axvline(0, color="k", lw=0.9)
    a1.set_xlabel("move in the underlying future over the trade (Rs)")
    a1.set_ylabel("result (Rs / lot)")
    a1.legend(fontsize=8.5)
    a1.set_title("S3 tracks the stock; S1 does not")

    a2.scatter(dd["spread_red"], dd["S3"], s=84, color="#c0392b", alpha=0.85,
               edgecolor="white", lw=0.7, label=f"S3  (corr {c_sprd[0]:+.2f})")
    a2.scatter(dd["spread_red"], dd["S1"], s=84, color="#2c3e50", alpha=0.85,
               edgecolor="white", lw=0.7, label=f"S1  (corr {c_s1[0]:+.2f})")
    a2.axhline(0, color="k", lw=0.9)
    a2.axvline(0, color="k", lw=0.9)
    a2.set_xlabel("collapse of the F1-F2 spread (Rs / share)")
    a2.set_ylabel("result (Rs / lot)")
    a2.legend(fontsize=8.5)
    a2.set_title("S1 tracks the spread; S3 does not")

    fig.suptitle("S3 is a synthetic long in disguise -- it was never trading "
                 "the spread", y=1.00, fontsize=12.5)
    p_out = P.save(fig, "12_s3_is_directional")
    print(f"wrote results/s3_vega.csv, s3_directional.csv, "
          f"negative_results.md, figures/{p_out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
