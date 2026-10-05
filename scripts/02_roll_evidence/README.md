# 02_roll_evidence

## What it does
Tests the mechanism: if short sellers rolling out of the front month cause the collapse, front-month open interest should start falling before the spread peaks. It measures that lead per cycle and builds an event study of open interest aligned on the spread peak.

## Inputs
All reads go through `borrowcycle/data.py`, which reads the backtesting engine's ArcticDB: the joined 15-minute borrow panel (`borrow_rates/{T}/15minute` plus the `futures` panel inputs and Kite spot); columns `F1_close F2_close F1_oi F2_oi cycle_id`.

## Outputs
| File | Content |
|---|---|
| `results/roll_lead_lag.csv` | per cycle: bars and sessions by which F1 OI peaks before the spread, OI levels |
| `results/roll_event_study.csv` | F1/F2 OI, normalised per cycle, in event time around the peak |
| `figures/11_roll_event_study.png` | the event-study chart |

## How it works
1. Per cycle, find the spread peak (as in 01) and the bar where F1 open interest peaks.
2. Lead = spread-peak bar − OI-peak bar; a binomial test checks whether OI leads more often than half the time.
3. Normalise F1 and F2 OI by each cycle's own maximum and average them on a common event-time grid around the peak.

## Usage
```bash
python scripts/02_roll_evidence/02_roll_evidence.py
```

## Re-running / what gets skipped
Rewrites its outputs; no ArcticDB writes.

## Caveats
* Open interest is end-of-bar, so a lead of under one bar isn't meaningful.
* This is consistent with the roll causing the collapse, but it isn't proof: both could follow the expiry calendar.
