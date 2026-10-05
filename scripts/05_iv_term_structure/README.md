# 05_iv_term_structure

## What it does
Checks whether the borrow pressure seen in the futures spread also shows in the options market: does front-month implied vol, or the 25-delta risk reversal (put skew), behave differently in hard-to-borrow cycles?

## Inputs
All reads go through `borrowcycle/data.py`, which reads the backtesting engine's ArcticDB: the joined 15-minute borrow panel (`borrow_rates/{T}/15minute` plus the `futures` panel inputs and Kite spot). It uses the stored `feat_atm_iv` and `feat_25d_risk_reversal` at each cycle's peak (via `results/cycle_moments.csv` from 01) and reads no option chains directly.

## Outputs
| File | Content |
|---|---|
| `results/iv_cross_section.csv` | per cycle: tier, peak `b12`, ATM IV and risk reversal at the peak |
| `results/iv_findings.md` | the tests and their results |
| `figures/13_iv_and_skew.png` | IV and skew by tier |

## How it works
1. Take each cycle's ATM IV and risk reversal at its spread peak; cycles with no IV value are excluded.
2. Mann-Whitney on ATM IV, extreme vs non-HTB cycles; Spearman between the peak borrow rate and the risk reversal.
3. For contrast, Spearman between the borrow rate and the spread amplitude.

## Usage
```bash
python scripts/05_iv_term_structure/05_iv_term_structure.py   # after 01_cycle_anatomy
```

## Re-running / what gets skipped
Rewrites its outputs; no ArcticDB writes.

## Caveats
* The non-HTB control group is small (about a dozen cycles).
* IV coverage depends on which expiries have enriched option chains.
