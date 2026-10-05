# Roll mechanics: the premium against the open-interest transfer

The cycle-anatomy work (doc 02) locates *when* the F1 − F2 premium builds, peaks and
collapses in calendar time. This document measures it against the variable the
mechanism says drives it: **how far the roll from the front month to the next month
has progressed.** Numbers come from
[`scripts/07_roll_mechanics`](../scripts/07_roll_mechanics/README.md); the full tables
are in [`results/roll_mechanics.md`](../results/roll_mechanics.md).

## Method

* **Event time is trading sessions to F1 expiry** (0 = expiry day), counted on each
  cycle's own trading dates. Calendar days to expiry are not used: NSE moved monthly
  expiry from the last Thursday to the last Tuesday in September 2025, so a given
  calendar DTE lands on different weekdays in different cycles. Pooling on it produced
  a spurious weekly saw-tooth in the first look at this data.
* **End of session:** each trading day is represented by its last 15-minute bar.
* **Spread** = (F1 − F2)/F1 in bps. **Roll progress** = the front month's share of
  open interest, F1_oi/(F1_oi + F2_oi), or the F1/F2 open-interest ratio.
* **Roll midpoint:** the first session where F1/F2 < 1, i.e. the front month holds less
  open interest than the next.
* **Groups:** hard-to-borrow cycles (peak b12 ≥ 5%, tiers MOD + EXT, n = 120) against the
  control (peak b12 < 5%, n = 13), plus each tier and each ticker.

## Findings

**1. The roll happens late; the premium builds before it and while it starts.**
Median profile of hard-to-borrow cycles:

| sessions to expiry | 15 | 10 | 6 | 4 | 3 | 2 | 1 | 0 |
|---|---|---|---|---|---|---|---|---|
| front-month share of OI | 95% | 91% | 85% | 79% | 63% | 43% | 25% | 12% |
| F1/F2 OI ratio | 17.8× | 10.7× | 5.6× | 3.7× | 1.7× | 0.76× | 0.33× | 0.14× |
| F1 − F2 spread (bps) | 44 | 58 | 80 | 81 | **92** | 84 | 84 | −9 |

Most of the transfer happens in the final four sessions. The spread rises steadily
from about 44 to 80 bps while the front month still holds more than 80% of open
interest, peaks as the roll accelerates, stays elevated through the crossover, and
converges at the expiry close.

**2. The spread peaks as open interest crosses parity.** At each hard-to-borrow cycle's
own spread peak, the median F1/F2 ratio is **0.81×** (the front month holds 49%), two
sessions before expiry. Pooled by ratio, the spread is widest at 2–4×:

| F1/F2 OI ratio | >16× | 8–16× | 4–8× | 2–4× | 1–2× | 0.5–1× | 0.25–0.5× | <0.25× |
|---|---|---|---|---|---|---|---|---|
| median spread, hard-to-borrow (bps) | 4 | 52 | 88 | **151** | 103 | 86 | 70 | 63 |

The two views differ by design. The pooled bins weight every session; the per-cycle
statistic takes each cycle's single highest point, which in large-premium cycles
often comes later in the roll.

**3. The control shows none of it.** Cycles with no borrow premium sit at about −50 bps
(F1 below F2, i.e. normal cost of carry) at every stage of the roll. Their "peak" falls
at a median F1/F2 ratio of 16×, which is noise, not a cycle.

**4. Timing relative to the midpoint.** The roll midpoint is reached a median two
sessions before expiry (in 123 of 133 cycles). The peak comes at or before the
midpoint in 63% of cycles (mean lead 3.7 sessions, 95% CI 2.3–5.3). The median is 0,
and the sign test is not significant (p = 0.32), so "peak at the crossover" is the
fair summary rather than "peak strictly before it".

**5. By ticker, every name shows the cycle when borrow is extreme.** Restricted to
extreme cycles (EXT, peak b12 ≥ 15%, n = 92), all seven tickers build into the final
week and give the premium back at expiry. Median spread in bps:

| ticker (EXT cycles) | 10 | 6 | 4 | 3 | 2 | 1 | 0 | median per-cycle peak (sessions out) |
|---|---|---|---|---|---|---|---|---|
| IREDA (19) | 97 | 145 | 186 | 226 | **253** | 228 | 63 | 1 |
| SBICARD (17) | 85 | 114 | 122 | 119 | **135** | 106 | 81 | 2 |
| RVNL (12) | 202 | 217 | 222 | 217 | 284 | **306** | 211 | 1 |
| ASTRAL (11) | 66 | 74 | 66 | 59 | **124** | 114 | 35 | 2 |
| BDL (9) | 73 | 99 | 102 | 110 | 123 | **220** | −53 | 1 |
| VOLTAS (13) | 73 | 91 | **108** | 105 | 81 | 72 | 61 | 1 |
| KPITTECH (11) | 20 | **51** | 43 | 20 | 11 | 28 | −49 | 4 |

The names differ mainly in how *often* they are extreme. IREDA and RVNL are extreme in
every or nearly every cycle. ASTRAL and BDL each have six no-premium cycles, which
flatten their all-cycle profiles. Two qualifications apply. KPITTECH's premium is
smaller and its median profile peaks earlier, about 6 sessions out. RVNL's spread is
still elevated at the expiry close (median 211 bps). Full table:
[`results/roll_mechanics.md`](../results/roll_mechanics.md) §6.

## Reading the figures

| Figure | What it shows |
|---|---|
| [00](../figures/00_roll_drives_the_premium.png) | Spread (top) and the open-interest split (bottom) by sessions to expiry, with the control dashed and roll milestones marked |
| [14](../figures/14_phase_portrait.png) | Spread against the F1/F2 ratio (log, reversed so the roll runs left to right); faint lines are single cycles |
| [15](../figures/15_spread_by_oi_ratio.png) | Distribution of the spread in each ratio bin, and a ticker × bin heatmap |
| [16](../figures/16_roll_midpoint_event.png) | Every cycle aligned on its roll midpoint |
| [17](../figures/17_roll_profile_by_ticker.png) | Figure 00 for each ticker, extreme cycles only |
| [explorer](explorer/index.html) | Any single cycle against the population, interactively |

## Caveats

* Open interest is reported at bar end and the analysis is end-of-session, so moves
  within a day are not resolved.
* Ratio bins pool sessions from different cycles. The per-cycle statistics (finding 2)
  are the cycle-level check.
* The control group is small (13 cycles).
* The roll and the premium both run on the expiry calendar. Their alignment is
  consistent with the roll driving the premium, and with the mechanism's predictions
  (no premium, no cycle), but it is not a controlled experiment.
