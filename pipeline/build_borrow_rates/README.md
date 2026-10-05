# build_borrow_rates

## What it does
Computes the implied borrow rates of each futures leg, and between legs, from the
stored F1/F2/F3 panel and Kite spot, and writes them to `borrow_rates` with a
per-cycle summary.

## Inputs
| Library / symbol | Used for |
|---|---|
| `futures/{T}/{interval}` | F1–F3 close, OI, DTE, `cycle_id`, `is_expiry_day`, `F1_expiry` |
| `spot/{T}/{kite interval}` | spot close (joined on timestamp) |

## Outputs
| Library / symbol | Columns |
|---|---|
| `borrow_rates/{T}/{interval}` | `F1_dte_star F2_dte_star F3_dte_star [OI_ratio U_t] spread_spot_f1 spread_spot_f2 spread_f1_f2 b1 b2 b3 b12 b23 b13 in_backwardation` (`OI_ratio`/`U_t` intraday only) |
| `borrow_rates/{T}/{interval}/cycle_summary` | per cycle: bars, bars in backwardation, `contango_dominant`, mean/max `b1`, max `b12`, `F1_expiry` |

The inputs are **not** stored again: read the full frame with `Store.read_borrow`,
which joins the panel columns and `SPOT_close` back on.

## How it works
1. **Fractional DTE:** `F{n}_dte_star = (F{n}_dte + 1) − i / bars_per_day`, where `i` is
   the bar's rank within its trading day (25 bars per day at 15 minutes, 1 at daily).
   Time to expiry then falls smoothly through each day.
2. **Cost of carry:** `F = S·exp((r − b)·τ)`, so with τ = dte_star / 365 and r = 6.25%:
   * `b1 = r − ln(F1/S)/τ1` (likewise `b2`, `b3`)
   * `b12 = r − ln(F2/F1)/(τ2 − τ1)` (likewise `b23`, `b13`)
   * all rates floored at 0.
3. `in_backwardation = F1 < S`. Spreads: `S − F1`, `S − F2`, `F1 − F2` (price points).
4. Intraday only: `OI_ratio = F1_oi / F2_oi`, and `U_t = OI_ratio / (F1_dte_star + 0.04
   on expiry-day bars)`, a measure of how urgently front-month holders still have to roll.
5. **Cycle summary.** At 15 minutes a cycle counts as hard-to-borrow if `b12` exceeds 15%
   p.a. at any bar. At other intervals it counts if at least half its bars are in
   backwardation.

## Usage
```bash
python pipeline/build_borrow_rates/build_borrow_rates.py --dry-run          # research tickers, 15minute
python pipeline/build_borrow_rates/build_borrow_rates.py KAYNES --intervals 15minute 1day
python pipeline/build_borrow_rates/build_borrow_rates.py SWIGGY --update
```

## Re-running / what gets skipped
* **Default:** series that already exist are skipped.
* **`--update`:** recomputes in memory and writes from the first row that differs.
  That covers new panel bars, and older bars whose spot was missing before but which
  Kite now covers. Stored `feat_*` values on those rows are kept until
  `build_features` refreshes them.
* **`--force`:** snapshot, then full rewrite. The feature columns are dropped by a full
  rewrite, so run `build_features` afterwards (the script says so).

## Caveats
* Spot-based columns (`b1–b3`, `in_backwardation`, spot spreads) are NaN where Kite spot
  doesn't reach yet. Intraday Kite spot currently ends on 2026-09-18.
* `b12` doesn't use spot. Near expiry, `τ2 − τ1` stays about a month, but `b1` blows up
  as `τ1 → 0`. The research classifies cycles with `b12` and times trades on the raw
  spread.
* No dividend adjustment: a dividend inside a contract's life lowers the futures
  price, which reads as a higher implied borrow.
