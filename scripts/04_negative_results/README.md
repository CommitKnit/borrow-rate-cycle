# 04_negative_results

## What it does
Where the thesis fails, and the one place the original explanation of a failure was wrong. S3 (long call F2 + short put F1) loses money not because of vega, as originally claimed, but because it is a synthetic long: it tracks the underlying, not the spread. The strategy also loses where there's no borrow premium, which is the expected behaviour for a mechanism-specific trade.

## Inputs
All reads go through `borrowcycle/data.py`, which reads the backtesting engine's ArcticDB: the joined 15-minute borrow panel (`borrow_rates/{T}/15minute` plus the `futures` panel inputs and Kite spot). It also reads SBICARD and RVNL option chains (ATM ± 3 strikes) for F2 implied-vol changes and S3 legs, and `results/pnl_per_cycle.csv` from 03.

## Outputs
| File | Content |
|---|---|
| `results/s3_vega.csv` | F2 ATM IV at entry and exit for each S3 trade |
| `results/s3_directional.csv` | S3 and S1 P&L against the underlying move and the spread collapse |
| `results/negative_results.md` | the write-up (also copied to `docs/04_negative_results.md`) |
| `figures/12_s3_is_directional.png` | S3 vs underlying, S3 vs spread |

## How it works
1. For each S3 trade, measure the change in F2 ATM implied vol: the vega story needs it to fall.
2. Correlate S3 P&L with the underlying's move and with the spread collapse; do the same for S1.
3. Summarise S1 by borrow tier (the no-premium cycles are the control group) and by the direction of the underlying.

## Usage
```bash
python scripts/04_negative_results/04_negative_results.py   # after 03_backtest
```

## Re-running / what gets skipped
Rewrites its outputs and `docs/04_negative_results.md`; no ArcticDB writes.

## Caveats
* About 10 S3 trades: the correlations are indicative, not precise.
* Needs `results/pnl_per_cycle.csv`, so run 03 first.
