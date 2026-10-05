# build_features

## What it does
Builds 25 features per bar describing the roll, open interest, volume, the borrow
rate's dynamics and the implied-vol smile, and stores them in
`borrow_rates/{T}/{interval}` next to the borrow rates. Every feature uses only data
up to its own bar.

## Inputs
| Library / symbol | Used for |
|---|---|
| `borrow_rates/{T}/{interval}` (joined via `Store.read_borrow`) | `b12`, `F1_dte_star`, plus panel inputs `F1_oi F2_oi F1_volume F2_volume F1_close F1_dte cycle_id` |
| `futures/{T}/{interval}` | each cycle's `F1_expiry` |
| `options_enriched/{T}/{interval}/{expiry}` | optional: front-month chain for the IV group |

## Outputs
25 `feat_*` columns in `borrow_rates/{T}/{interval}`. `F1_volume`/`F2_volume` are
inputs, read from `futures`, and not stored again.

| Feature | Definition |
|---|---|
| `feat_oi_concentration` | C = F1_oi / (F1_oi + F2_oi), the share of open interest still in the front month |
| `feat_roll_velocity` | change in C from the previous bar, within the cycle |
| `feat_roll_acceleration` | change in roll velocity, within the cycle |
| `feat_relative_oi_change` | g2 − g1, where g = ΔOI / previous OI for each leg |
| `feat_net_roll_flow` | ΔF2_oi − ΔF1_oi |
| `feat_flow_intensity` | (net roll flow / (F1_oi + F2_oi)) / max(F1_dte_star, 0.04) |
| `feat_roll_deviation` | C − mean C at the same whole-day DTE over **earlier cycles** |
| `feat_Vshare` | F2_volume / (F1_volume + F2_volume), where both are positive |
| `feat_volflow` | net roll flow × Vshare |
| `feat_atm_iv` | mean IV of the at-the-money call and put (strike nearest the future) |
| `feat_otm_25d_call_iv` / `feat_otm_15d_call_iv` | IV of the call with delta nearest +0.25 / +0.15 |
| `feat_otm_25d_put_iv` / `feat_otm_15d_put_iv` | IV of the put with delta nearest −0.25 / −0.15 |
| `feat_25d_risk_reversal` | 25Δ call IV − 25Δ put IV |
| `feat_residual_atm_iv` | ATM IV − median ATM IV at the same DTE over **earlier cycles** |
| `feat_b12_percentile` | percentile of b12 among **earlier cycles'** b12 at the same DTE |
| `feat_b12_slope_1d` | slope of b12 over the last trading day, within the cycle |
| `feat_b12_accel` | change in that slope over ~75 minutes |
| `feat_beta_regression` | slope of log(F1_close) on F1_dte_star using every bar so far in the cycle |
| `feat_r2_regression` / `feat_beta_tsat` | R² / t-statistic of that slope |
| `feat_beta_rolling` / `feat_r2_rolling` | the same regression over the last 10 trading days (crosses cycles) |
| `feat_beta_reversal` | rolling slope − within-cycle slope |

**On the regression group:** `F1_dte_star` falls steadily through a cycle, so
regressing price on it is the same as fitting a trend line through time. In practice
these features measure **cycle-to-date price momentum** (rank correlation about −0.8
to −0.9 with the cycle's return so far), not the cost of carry. Carry is already in
`b1`/`b12` directly.

## How it works
1. Read the joined borrow frame and each cycle's front-month expiry.
2. Open-interest and volume features: differences are taken within each cycle, so
   nothing leaks across an expiry.
3. IV features: per cycle, take the enriched chain for its F1 expiry (rows tagged
   `slot == F1` when that column exists). Both stored column layouts are accepted:
   `strike`/`opt_type`/`futures_ref_price`, or `strike_price`/`instrument_type`/`futures_price`.
4. Earlier-cycle statistics (`roll_deviation`, `b12_percentile`, `residual_atm_iv`)
   pool only cycles that started before the bar's own cycle. The first cycle is NaN.
5. Windows are set in trading days and converted to bars for the interval:

| Window | Days | 15minute | 5minute | 1minute | 1day |
|---|---|---|---|---|---|
| rolling regression | 10 | 250 | 750 | 3,750 | 10 |
| b12 slope | 1 | 25 | 75 | 375 | 5 (minimum) |
| b12 acceleration lag | 0.2 | 5 | 15 | 75 | 1 |

## Usage
```bash
python pipeline/build_features/build_features.py --dry-run              # research tickers, 15minute
python pipeline/build_features/build_features.py KAYNES DIXON           # adds features (IV NaN: no chains)
python pipeline/build_features/build_features.py RVNL --intervals 1day
```

## Re-running / what gets skipped
* All features are recomputed in memory. Because none of them look ahead, stored rows
  don't change, and only rows that differ (normally just new bars) are written.
  On up-to-date data the run reports "up to date" and writes nothing.
* A series without features, or with an older feature set, gets one full rewrite.
  The previous version is pruned.
* `--force` takes a snapshot, then rewrites every row.

## Caveats
* The IV group (7 features) is NaN wherever no enriched option chain exists for the
  cycle's F1 expiry. Today that's everywhere except the seven research tickers at
  15minute, RVNL at 1day, and a few 1-minute chains.
* Earlier-cycle features need several cycles of history. On short series (a few
  days of 5-minute data, for example) they stay NaN.
