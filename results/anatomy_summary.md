# Cycle anatomy — measured across every expiry cycle

135 expiry cycles, 7 NSE single-stock futures, 15-minute bars, Jul 2024 to Oct 2026.

Three moments are located on the 11-bar centred smoothed F1-F2 spread over the **whole cycle**:

- **BUILD** — the last trough before the peak (argmin of the smoothed spread before the peak).
- **PEAK** — the cycle maximum of the smoothed spread.
- **COLLAPSE** — the first bar after the peak where slope and acceleration are both negative for 4 consecutive bars (~1 hour).


## 1. When do the three moments happen?

Days to F1 expiry. Lower = closer to settlement.

| Moment | p10 | p25 | median | p75 | p90 |
|---|---|---|---|---|---|
| BUILD  8.8 | 20.0 | 26.0 | 27.0 | 34.0 |
| PEAK  0.0 | 1.0 | 4.0 | 14.0 | 26.0 |
| COLLAPSE  0.0 | 1.0 | 3.0 | 7.0 | 22.0 |

The premium starts building a median of **26 days** before expiry and tops out **4 days** before it.

The peak falls inside the final expiry week in **68%** of cycles — often, but far from always. 13% of cycles have their build trough at the very start of the cycle, where it cannot be distinguished from the cycle boundary; those are flagged `build_censored`.


## 2. How big is the move, and how much of it reverses?

| Measure | p25 | median | p75 |
|---|---|---|---|
| Amplitude, build to peak (bps of F1) | 63.17 | 120.71 | 221.87 |
| Collapse, peak to expiry (bps of F1) | 34.55 | 83.45 | 167.34 |
| Spread at expiry (bps of F1) | -52.61 | -17.19 | 147.60 |
| Retracement (collapse / amplitude) | 0.38 | 0.83 | 1.24 |

The median cycle gives back **83%** of everything it built, and **53%** of cycles retrace more than 80%. Retracement is used rather than collapse/peak because peak spreads near zero make that ratio explode.


## 3. The roll: open interest has already moved by the peak

Ratio of front-month to next-month open interest at each moment.

| Moment | p25 | median | p75 | n |
|---|---|---|---|---|
| BUILD | 9.32 | 18.88 | 30.24 | 122 |
| PEAK | 0.34 | 0.98 | 5.87 | 124 |
| COLLAPSE | 0.26 | 0.51 | 2.98 | 78 |

At the build trough the front month carries a median **18.6x** the open interest of the next month. By the spread peak that has fallen to **1.0x**. Paired Wilcoxon on log(ratio), n=118: p = 1.76e-20.

The roll is therefore already well advanced by the time the spread tops out — consistent with the roll causing the collapse rather than following it.


Front-month open interest peaks **before** the spread peak in **106/135 (79%)** of cycles (binomial vs 50%: p = 1.66e-11).

| Ticker | OI peaks first | n | rate |
|---|---|---|---|
| SBICARD | 17 | 22 | 77% |
| RVNL | 11 | 13 | 85% |
| KPITTECH | 12 | 19 | 63% |
| ASTRAL | 21 | 24 | 88% |
| BDL | 11 | 17 | 65% |
| IREDA | 17 | 20 | 85% |
| VOLTAS | 17 | 20 | 85% |

## 4. Does the borrow premium matter? (HTB vs control)

| Tier | n | median amplitude (bps) | median retracement |
|---|---|---|---|
| NON | 13 | 38.1 | 0.83 |
| MOD | 29 | 62.7 | 1.08 |
| EXT | 93 | 167.8 | 0.70 |

Mann-Whitney, amplitude of extreme-HTB vs non-HTB cycles (n=93 vs 13): p = 1.42e-07. The control group is small — treat it as a sanity check, not a test.


## 5. Is the collapse special to expiry? (placebo test)

`build < peak` holds by construction, so ordering alone proves nothing. The real question is whether a spread peak reverts *more* into expiry than an arbitrary local maximum reverts mid-cycle. Placebo windows are 7 sessions long and never touch the expiry week.

- observed retracement, median: **0.83** (n=133)
- placebo retracement, median: **0.35** (n=1215)
- Mann-Whitney (observed > placebo): p = 3.01e-08


## 6. Detector robustness

The original detector searched only inside the expiry week (`DTE <= 7`). That is censored: it reports the window edge as the build moment, and widening the window just moves the artefact. Compare the `dte_build` quartiles of the two detectors below — under the legacy rule p25, median and p75 collapse onto the window edge itself.

| Detector | smooth | min_sustain | n | dte_build p25/med/p75 | dte_peak med | retracement med | censored % |
|---|---|---|---|---|---|---|---|
| legacy (DTE<=7 window) | 5 | 2 | 129 | 7.0 / 7.0 / 7.0 | 3.0 | 1.23 | 0% |
| legacy (DTE<=7 window) | 5 | 4 | 129 | 7.0 / 7.0 / 7.0 | 3.0 | 1.23 | 0% |
| legacy (DTE<=7 window) | 5 | 8 | 129 | 7.0 / 7.0 / 7.0 | 3.0 | 1.23 | 0% |
| legacy (DTE<=7 window) | 11 | 2 | 129 | 7.0 / 7.0 / 7.0 | 3.0 | 1.27 | 0% |
| legacy (DTE<=7 window) | 11 | 4 | 129 | 7.0 / 7.0 / 7.0 | 3.0 | 1.27 | 0% |
| legacy (DTE<=7 window) | 11 | 8 | 129 | 7.0 / 7.0 / 7.0 | 3.0 | 1.27 | 0% |
| legacy (DTE<=7 window) | 21 | 2 | 129 | 7.0 / 7.0 / 7.0 | 3.0 | 1.17 | 0% |
| legacy (DTE<=7 window) | 21 | 4 | 129 | 7.0 / 7.0 / 7.0 | 3.0 | 1.17 | 0% |
| legacy (DTE<=7 window) | 21 | 8 | 129 | 7.0 / 7.0 / 7.0 | 3.0 | 1.17 | 0% |
| window-free | 5 | 2 | 135 | 20.5 / 26.0 / 27.0 | 4.0 | 0.84 | 13% |
| window-free | 5 | 4 | 135 | 20.5 / 26.0 / 27.0 | 4.0 | 0.84 | 13% |
| window-free | 5 | 8 | 135 | 20.5 / 26.0 / 27.0 | 4.0 | 0.84 | 13% |
| window-free | 11 | 2 | 135 | 20.0 / 26.0 / 27.0 | 4.0 | 0.83 | 13% |
| window-free | 11 | 4 | 135 | 20.0 / 26.0 / 27.0 | 4.0 | 0.83 | 13% |
| window-free | 11 | 8 | 135 | 20.0 / 26.0 / 27.0 | 4.0 | 0.83 | 13% |
| window-free | 21 | 2 | 135 | 20.0 / 26.0 / 27.0 | 4.0 | 0.83 | 17% |
| window-free | 21 | 4 | 135 | 20.0 / 26.0 / 27.0 | 4.0 | 0.83 | 17% |
| window-free | 21 | 8 | 135 | 20.0 / 26.0 / 27.0 | 4.0 | 0.83 | 17% |

The slope/acceleration collapse trigger and an independent amplitude-based one (first bar giving back 25% of the move) land within one session of each other in **63%** of cycles.


## 7. Data caveats

- 6 cycles have fewer than 5 bars inside the expiry week and are flagged `gap_flag`.
- 17 cycles have a censored build trough.
- IV features are NaN on cycles whose front-month expiry has no enriched option chain; those cycles still count in the spread and open-interest statistics.