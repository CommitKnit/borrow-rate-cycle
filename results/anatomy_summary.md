# Cycle anatomy — measured across every expiry cycle

126 expiry cycles, 7 NSE single-stock futures, 15-minute bars, Jul 2024 to Aug 2026.

Three moments are located on the 11-bar centred smoothed F1-F2 spread over the **whole cycle**:

- **BUILD** — the last trough before the peak (argmin of the smoothed spread before the peak).
- **PEAK** — the cycle maximum of the smoothed spread.
- **COLLAPSE** — the first bar after the peak where slope and acceleration are both negative for 4 consecutive bars (~1 hour).


## 1. When do the three moments happen?

Days to F1 expiry. Lower = closer to settlement.

| Moment | p10 | p25 | median | p75 | p90 |
|---|---|---|---|---|---|
| BUILD  9.0 | 20.0 | 26.0 | 27.0 | 34.0 |
| PEAK  0.0 | 1.0 | 4.0 | 14.5 | 26.0 |
| COLLAPSE  0.0 | 1.0 | 3.0 | 8.0 | 22.0 |

The premium starts building a median of **26 days** before expiry and tops out **4 days** before it.

The peak falls inside the final expiry week in **67%** of cycles — often, but far from always. 11% of cycles have their build trough at the very start of the cycle, where it cannot be distinguished from the cycle boundary; those are flagged `build_censored`.


## 2. How big is the move, and how much of it reverses?

| Measure | p25 | median | p75 |
|---|---|---|---|
| Amplitude, build to peak (bps of F1) | 63.55 | 116.32 | 216.93 |
| Collapse, peak to expiry (bps of F1) | 36.96 | 80.79 | 158.48 |
| Spread at expiry (bps of F1) | -52.87 | -10.02 | 143.61 |
| Retracement (collapse / amplitude) | 0.44 | 0.87 | 1.24 |

The median cycle gives back **87%** of everything it built, and **54%** of cycles retrace more than 80%. Retracement is used rather than collapse/peak because peak spreads near zero make that ratio explode.


## 3. The roll: open interest has already moved by the peak

Ratio of front-month to next-month open interest at each moment.

| Moment | p25 | median | p75 | n |
|---|---|---|---|---|
| BUILD | 9.26 | 20.42 | 31.13 | 113 |
| PEAK | 0.35 | 0.98 | 6.26 | 115 |
| COLLAPSE | 0.27 | 0.52 | 3.43 | 73 |

At the build trough the front month carries a median **20.2x** the open interest of the next month. By the spread peak that has fallen to **1.0x**. Paired Wilcoxon on log(ratio), n=109: p = 3.26e-19.

The roll is therefore already well advanced by the time the spread tops out — consistent with the roll causing the collapse rather than following it.


Front-month open interest peaks **before** the spread peak in **98/126 (78%)** of cycles (binomial vs 50%: p = 2.69e-10).

| Ticker | OI peaks first | n | rate |
|---|---|---|---|
| SBICARD | 15 | 20 | 75% |
| RVNL | 11 | 13 | 85% |
| KPITTECH | 11 | 18 | 61% |
| ASTRAL | 19 | 22 | 86% |
| BDL | 10 | 15 | 67% |
| IREDA | 15 | 18 | 83% |
| VOLTAS | 17 | 20 | 85% |

## 4. Does the borrow premium matter? (HTB vs control)

| Tier | n | median amplitude (bps) | median retracement |
|---|---|---|---|
| NON | 12 | 39.1 | 0.85 |
| MOD | 29 | 62.7 | 1.08 |
| EXT | 85 | 159.9 | 0.73 |

Mann-Whitney, amplitude of extreme-HTB vs non-HTB cycles (n=85 vs 12): p = 2.39e-07. The control group is small — treat it as a sanity check, not a test.


## 5. Is the collapse special to expiry? (placebo test)

`build < peak` holds by construction, so ordering alone proves nothing. The real question is whether a spread peak reverts *more* into expiry than an arbitrary local maximum reverts mid-cycle. Placebo windows are 7 sessions long and never touch the expiry week.

- observed retracement, median: **0.87** (n=125)
- placebo retracement, median: **0.41** (n=1178)
- Mann-Whitney (observed > placebo): p = 3.19e-07


## 6. Detector robustness

The original detector searched only inside the expiry week (`DTE <= 7`). That is censored: it reports the window edge as the build moment, and widening the window just moves the artefact. Compare the `dte_build` quartiles of the two detectors below — under the legacy rule p25, median and p75 collapse onto the window edge itself.

| Detector | smooth | min_sustain | n | dte_build p25/med/p75 | dte_peak med | retracement med | censored % |
|---|---|---|---|---|---|---|---|
| legacy (DTE<=7 window) | 5 | 2 | 119 | 7.0 / 7.0 / 7.0 | 3.0 | 1.22 | 0% |
| legacy (DTE<=7 window) | 5 | 4 | 119 | 7.0 / 7.0 / 7.0 | 3.0 | 1.22 | 0% |
| legacy (DTE<=7 window) | 5 | 8 | 119 | 7.0 / 7.0 / 7.0 | 3.0 | 1.22 | 0% |
| legacy (DTE<=7 window) | 11 | 2 | 119 | 7.0 / 7.0 / 7.0 | 3.0 | 1.27 | 0% |
| legacy (DTE<=7 window) | 11 | 4 | 119 | 7.0 / 7.0 / 7.0 | 3.0 | 1.27 | 0% |
| legacy (DTE<=7 window) | 11 | 8 | 119 | 7.0 / 7.0 / 7.0 | 3.0 | 1.27 | 0% |
| legacy (DTE<=7 window) | 21 | 2 | 119 | 7.0 / 7.0 / 7.0 | 3.0 | 1.17 | 0% |
| legacy (DTE<=7 window) | 21 | 4 | 119 | 7.0 / 7.0 / 7.0 | 3.0 | 1.17 | 0% |
| legacy (DTE<=7 window) | 21 | 8 | 119 | 7.0 / 7.0 / 7.0 | 3.0 | 1.17 | 0% |
| window-free | 5 | 2 | 126 | 20.2 / 26.0 / 27.0 | 4.0 | 0.84 | 13% |
| window-free | 5 | 4 | 126 | 20.2 / 26.0 / 27.0 | 4.0 | 0.84 | 13% |
| window-free | 5 | 8 | 126 | 20.2 / 26.0 / 27.0 | 4.0 | 0.84 | 13% |
| window-free | 11 | 2 | 126 | 20.0 / 26.0 / 27.0 | 4.0 | 0.87 | 11% |
| window-free | 11 | 4 | 126 | 20.0 / 26.0 / 27.0 | 4.0 | 0.87 | 11% |
| window-free | 11 | 8 | 126 | 20.0 / 26.0 / 27.0 | 4.0 | 0.87 | 11% |
| window-free | 21 | 2 | 126 | 20.0 / 26.0 / 27.0 | 4.0 | 0.87 | 16% |
| window-free | 21 | 4 | 126 | 20.0 / 26.0 / 27.0 | 4.0 | 0.87 | 16% |
| window-free | 21 | 8 | 126 | 20.0 / 26.0 / 27.0 | 4.0 | 0.87 | 16% |

The slope/acceleration collapse trigger and an independent amplitude-based one (first bar giving back 25% of the move) land within one session of each other in **62%** of cycles.


## 7. Data caveats

- 7 cycles have fewer than 5 bars inside the expiry week and are flagged `gap_flag`.
- 14 cycles have a censored build trough.
- BDL ships without the `feat_*` columns, so it contributes to the spread and open-interest statistics but not the IV ones.