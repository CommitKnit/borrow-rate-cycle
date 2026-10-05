# 03_backtest

## What it does
The calendar-spread backtest: short F1 / long F2 after the spread peaks, out at noon on expiry day. Three scopes show what each correction to the original research does to the headline: the original published scope reproduced exactly, all tickers with the options gate removed, and all tickers with the look-ahead removed (the implementable result).

## Inputs
All reads go through `borrowcycle/data.py`, which reads the backtesting engine's ArcticDB: the joined 15-minute borrow panel (`borrow_rates/{T}/15minute` plus the `futures` panel inputs and Kite spot). The options strategies S2/S3 also read enriched option chains (`options_enriched/{T}/15minute/{expiry}`, ATM ± 3 strikes) for SBICARD and RVNL.

## Outputs
| File | Content |
|---|---|
| `results/pnl_headline_22.csv` | the original 22-cycle reproduction, S1/S2/S3 in rupees per lot |
| `results/pnl_per_cycle.csv` | every cycle under the wide (centred) and honest (trailing) variants, in bps of F1 |
| `results/summary_stats.md` | all scopes, tiers, per-ticker results, the slippage correction |

## How it works
1. Skip a cycle if its peak `b12` is below 5% (not hard to borrow).
2. **Entry:** first bar at or after the smoothed spread peak where slope < 0 and acceleration < 0. **Exit:** last bar at or before 12:00 on F1 expiry day.
3. **S1:** short F1 + long F2 futures. **S2:** the same through options. **S3:** long call on F2 + short put on F1.
4. **Costs:** 0.1% slippage per futures leg, 0.5% per options leg, charged on entry and exit. The original credited them, a sign error worth about ₹4,755 per cycle.
5. **Scopes:** headline (2 tickers, options required, centred smoother); wide (7 tickers, no options gate, centred); honest (7 tickers, no gate, **trailing** smoother, so no look-ahead).

## Usage
```bash
python scripts/03_backtest/03_backtest.py
```

## Re-running / what gets skipped
Rewrites its outputs; no ArcticDB writes.

## Caveats
* Parameters were chosen in-sample. The Sharpe is a per-cycle P&L ratio, not annualised and not capital-adjusted.
* Wide-scope results are in bps of F1 because lot sizes are known only for SBICARD and RVNL.
* Slippage only: no fees, taxes, margin financing or market impact.
