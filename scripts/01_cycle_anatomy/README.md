# 01_cycle_anatomy

## What it does
The core measurement. For every expiry cycle of the seven tickers it finds three moments in the F1 − F2 futures spread (BUILD: where the premium starts rising; PEAK; COLLAPSE: the sustained fall into expiry) and measures the cycle's shape: timing, size, how much of the rise is given back, how the roll lines up, and whether any of it is special to expiry.

## Inputs
All reads go through `borrowcycle/data.py`, which reads the backtesting engine's ArcticDB: the joined 15-minute borrow panel (`borrow_rates/{T}/15minute` plus the `futures` panel inputs and Kite spot); columns `F1_close F2_close F1_oi F2_oi F1_dte b12 cycle_id is_expiry_day`, plus `feat_atm_iv` and `feat_25d_risk_reversal` at the peak.

## Outputs
| File | Content |
|---|---|
| `results/cycle_moments.csv` | one row per cycle: moment timestamps, DTEs, spread levels, amplitude, retracement, OI ratios, borrow tier, IV at the peak |
| `results/anatomy_summary.md` | the statistics quoted in the README (§3) and docs/02 |
| `results/event_time_curve.csv` | amplitude-normalised mean spread path aligned on the peak |
| `results/robustness_grid.csv` | the statistics under 18 detector settings |

## How it works
1. Spread = F1_close − F2_close, smoothed with an 11-bar centred mean. This describes a finished cycle, so looking ahead is fine here; the backtest uses a trailing version.
2. **PEAK** = maximum of the smoothed spread over the whole cycle. **BUILD** = the trough before it. **COLLAPSE** = first bar after the peak where slope and acceleration are both negative for 4 bars (~1 hour).
3. Per cycle: amplitude (build → peak, bps of F1), collapse (peak → expiry), retracement = collapse / amplitude, F1/F2 open-interest ratio at each moment, peak `b12` → tier (NON < 5%, MOD 5–15%, EXT ≥ 15%).
4. Tests: paired Wilcoxon on the OI ratio (build vs peak), binomial test on OI peaking first, Mann-Whitney amplitude by tier, and a **placebo**: retracement after arbitrary mid-cycle local maxima versus into expiry.
5. Robustness: the old expiry-week-only detector against the whole-cycle detector, across smoothing and sustain settings.

## Usage
```bash
python scripts/01_cycle_anatomy/01_cycle_anatomy.py
```

## Re-running / what gets skipped
Deterministic: it rewrites its four outputs from whatever is in the store. Nothing is written to ArcticDB.

## Caveats
* `build < peak` holds by construction. The placebo test, not the ordering, is what carries the claim.
* Cycles whose build trough sits at the cycle's first bar are flagged `build_censored`.
* IV values at the peak are NaN when the cycle's expiry has no enriched option chain.
