# Backtest results

Entry: first bar at or after the smoothed F1-F2 spread peak where slope < 0 and acceleration < 0.  
Exit: last 15-minute bar at or before 12:00 on F1 expiry day.  
Costs: 0.1% slippage per futures leg, 0.5% per options leg, charged on entry and exit.

> The Sharpe below is a **per-cycle PnL Sharpe** (mean/std across cycles). It is not annualised and not capital-adjusted -- this study has no capital base. See `docs/05_limitations.md`.


## 1. Original published result (reproduction)

SBICARD + RVNL, options required, centred smoother. Rupees per lot.

| Strategy | n | Win rate | Mean PnL | Sharpe | Cumulative |
|---|---|---|---|---|---|
| S1 - Futures calendar (SHORT F1 + LONG F2) | 22 | 100% | Rs+13,429 | +1.83 | Rs+295,447 |
| S2 - Synthetic calendar (options) | 9 | 100% | Rs+9,615 | +1.28 | Rs+86,535 |
| S3 - Call F2 + Put F1 (options) | 10 | 40% | Rs-2,975 | -0.19 | Rs-29,750 |

2 further qualifying cycles were dropped by the original options-availability gate even though S1 uses no options. They are restored below.


## 2. Corrected scope (S1, basis points of F1)

All 7 tickers, no options gate. Reported in bps because lot sizes are only known for SBICARD and RVNL.

| Variant | n | Win rate | Mean bps | Median bps | Sharpe | 95% CI of mean | sign p |
|---|---|---|---|---|---|---|---|
| centred (look-ahead) | 105 | 77.1% | 76.99 | 52.9 | 0.75 | [58.3, 97.6] | 0.0 |
| trailing (implementable) | 107 | 64.5% | 50.29 | 23.76 | 0.49 | [31.7, 71.1] | 0.0035 |

## 3. Result by borrow-premium tier

The mechanism predicts that the edge should scale with the borrow premium. It does, under both variants -- and the ordering survives removal of the look-ahead.


**Centred smoother**

| Tier | n | Win rate | Mean bps | Median bps | Sharpe |
|---|---|---|---|---|---|
| NON | 12 | 33.3% | -3.5 | -7.1 | -0.17 |
| MOD | 28 | 67.9% | 25.9 | 19.3 | 0.59 |
| EXT | 65 | 89.2% | 113.9 | 94.1 | 1.02 |

**Trailing smoother**

| Tier | n | Win rate | Mean bps | Median bps | Sharpe |
|---|---|---|---|---|---|
| NON | 12 | 0.0% | -36.9 | -32.5 | -2.2 |
| MOD | 28 | 60.7% | 10.3 | 4.7 | 0.22 |
| EXT | 67 | 77.6% | 82.6 | 65.8 | 0.73 |

## 4. Per-ticker (trailing smoother)

| Ticker | n | Win rate | Mean bps | Sharpe |
|---|---|---|---|---|
| SBICARD | 16 | 88% | +103.6 | +0.88 |
| RVNL | 9 | 89% | +154.0 | +0.86 |
| KPITTECH | 17 | 65% | +21.0 | +0.48 |
| ASTRAL | 20 | 40% | +17.6 | +0.24 |
| BDL | 14 | 43% | +4.7 | +0.08 |
| IREDA | 15 | 73% | +68.5 | +0.71 |
| VOLTAS | 16 | 69% | +33.4 | +0.50 |