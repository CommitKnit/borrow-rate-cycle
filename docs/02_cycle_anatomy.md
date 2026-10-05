# Cycle anatomy

How the build/peak/collapse cycle is defined, measured, and tested against the
obvious alternative explanation.

The generated tables live in
[`results/anatomy_summary.md`](../results/anatomy_summary.md); the per-cycle
data is [`results/cycle_moments.csv`](../results/cycle_moments.csv), one row
per cycle. Everything below is produced by `scripts/01_cycle_anatomy/01_cycle_anatomy.py`.

---

## 1. Definitions

All three moments are located on the F1 − F2 spread, forward-filled and
smoothed with an 11-bar centred rolling mean, **over the whole cycle**:

| Moment | Rule |
|---|---|
| **PEAK** | `argmax` of the smoothed spread |
| **BUILD** | `argmin` of the smoothed spread *before* the peak — the trough the premium built from |
| **COLLAPSE** | first bar after the peak where slope < 0 **and** acceleration < 0, sustained for 4 consecutive bars (~1 hour) |

Two deliberate choices:

**The collapse trigger requires persistence.** The original rule fired on a
single bar, which on a smoothed series gave a median peak-to-collapse gap of
**1 bar** — the moment was indistinguishable from the peak itself. Requiring
4 consecutive bars makes it a real event. A second, independent definition is
recorded alongside it (`i_collapse25`: the first post-peak bar giving back 25%
of the amplitude); the two land within one session of each other in most
cycles, which is reported as a robustness check.

**The smoother is centred, and that is acceptable here but not for trading.**
A centred window looks 5 bars ahead. For *describing* a cycle after the fact
that is fine. For *timing an entry* it is look-ahead bias, which is why the
backtest publishes both a centred and a trailing variant. See
[05_limitations.md](05_limitations.md), item 1.

## 2. The censoring artefact, found and fixed

The original detector searched only inside the expiry week (`DTE <= 7`). Run
that way, the build moment has:

```
dte_build:   p25 = 7.0    median = 7.0    p75 = 7.0
```

Every quartile lands on the window edge. The detector was not finding a
trough — it was reporting the boundary of its own search window. Widening the
window does not fix it, it just moves the artefact: `DTE <= 12` gives a median
of 11.0, `DTE <= 21` gives 21.0.

Searching the whole cycle instead gives a real distribution:

| Detector | dte_build p25 / median / p75 | censored |
|---|---|---|
| Legacy (`DTE <= 7` window) | 7.0 / 7.0 / 7.0 | 0% |
| Window-free | 20.0 / 26.0 / 27.0 | 13% |

The window-free detector honestly reports that **13% of cycles** have their
trough at the start of the data, where it cannot be told apart from the cycle
boundary. Those are flagged `build_censored` and can be excluded. The legacy
detector reported 0% censoring only because it had already replaced every
trough with the window edge.

Both detectors remain available (`detect_moments(..., legacy=True)`) so the
comparison can be reproduced.

## 3. The robustness grid

`results/robustness_grid.csv` re-runs the detection over
`SMOOTH ∈ {5, 11, 21}` × `MIN_SUSTAIN ∈ {2, 4, 8}` × both detectors. The
window-free measurements are stable:

| smooth | dte_build median | dte_peak median | retracement median | censored |
|---|---|---|---|---|
| 5 | 26.0 | 4.0 | 0.84 | 13% |
| 11 | 26.0 | 4.0 | 0.83 | 13% |
| 21 | 26.0 | 4.0 | 0.83 | 17% |

`MIN_SUSTAIN` does not move the build or peak at all — it only affects where
the collapse trigger fires, which is its purpose.

## 4. What the measurements show

![anatomy](../figures/03_cycle_anatomy_eventtime.png)

Each cycle's spread is shifted so its build trough is 0 and its peak is 1, then
aligned on the peak. Normalising by each cycle's own amplitude is what makes a
₹2 cycle and a ₹38 cycle comparable.

The asymmetry is the finding: weeks of grind up, days of give-back.

**Timing.** Build starts a median of 26 days before expiry; the peak comes 4
days before. The peak falls inside the final expiry week in **68%** of cycles —
a tendency, not a rule, and worth stating because the original framing assumed
the expiry week was where everything happened.

**Magnitude.** Median amplitude is 121 bps of the front-month price; the median
cycle retraces 0.83 of it by settlement, and 53% retrace more than 80%. The
median spread at expiry is −17 bps: the front month closes slightly *below* the
next month, which is what convergence to spot plus a small carry differential
implies.

**The tiers share a shape but not a size.** The normalised curves for non-HTB,
moderate and extreme cycles lie almost on top of each other. The difference is
amplitude — 38, 63 and 168 bps respectively. The borrow premium sets how big
the cycle is, not what it looks like.

## 5. The roll

![oi](../figures/05_oi_ratio_three_moments.png)

| Moment | median F1 OI / F2 OI |
|---|---|
| BUILD (spread trough) | **18.6×** |
| PEAK (spread top) | **1.0×** |
| COLLAPSE (trigger) | **0.5×** |

Paired Wilcoxon on log(ratio), build vs peak, n = 118: **p = 2×10⁻²⁰**.

At the trough the front month carries roughly twenty times the next month's
open interest — normal for an active contract. By the time the spread peaks,
the two are at parity: the spread tops out as the roll crosses over, and gives
back once the front month is the minority. [08_roll_mechanics.md](08_roll_mechanics.md)
measures this on the open-interest ratio directly.

Front-month open interest turns down before the spread peaks in **106 of 135
cycles (79%)**, binomial p = 2×10⁻¹¹, and the pattern holds in every name
individually (63%–88%). Median lead: 4.7 sessions.

## 6. The placebo test — the part that carries the claim

BUILD is defined as the argmin before the peak, so `build < peak` is true **by
construction**. The 99% "phases ordered" statistic proves nothing on its own,
and it would be dishonest to present it as evidence.

The real question: does a spread peak revert *more* into expiry than an
arbitrary local maximum reverts mid-cycle? If not, this is just mean reversion
with extra steps.

The test anchors the identical measurement on the argmax of a randomly chosen
7-session window that never touches the expiry week, 1000 draws:

| | median retracement | n |
|---|---|---|
| Observed (anchored on expiry) | **0.83** | 133 |
| Placebo (arbitrary mid-cycle peaks) | **0.35** | 1215 |

Mann-Whitney, observed > placebo: **p = 3×10⁻⁸**.

An arbitrary spread peak gives back about 35% of its move. A peak that runs
into expiry gives back about 83%. The difference is the part expiry forces, and
it is what the strategy harvests.

## 7. Caveats specific to this measurement

- 13% of cycles have a censored build trough.
- 6 of 135 cycles have fewer than 5 bars in the expiry week (`gap_flag`).
- 32% of cycles peak outside the expiry week.
- `retracement` is used as the normalised measure rather than
  `collapse / peak`, because peak spreads near zero make the latter explode —
  its mean across cycles exceeds 600% and is meaningless.
