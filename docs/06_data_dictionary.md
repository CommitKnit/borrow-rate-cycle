# Data dictionary

Everything the analysis reads is in `data/`. No database, no credentials,
25.8 MB total. Per-file row counts and sha256 checksums are in
[`data/MANIFEST.json`](../data/MANIFEST.json).

## Coverage

| Ticker | Rows | Cycles | Columns |
|---|---|---|---|
| SBICARD | 11,569 | 20 | 52 |
| ASTRAL | 12,515 | 22 | 52 |
| VOLTAS | 11,625 | 20 | 52 |
| IREDA | 8,937 | 19 | 52 |
| KPITTECH | 9,426 | 18 | 52 |
| BDL | 7,437 | 15 | **24** |
| RVNL | 6,329 | 13 | 52 |

67,838 bars in total, 15-minute frequency, 2024-07-03 to 2026-08-12.
**BDL ships without the `feat_*` columns** and is therefore excluded from every
implied-volatility and roll-feature statistic, while still contributing to the
spread and open-interest results.

Index: tz-aware `DatetimeIndex` in `Asia/Kolkata`, monotonically increasing.
`borrowcycle.data.load_panel` restores the timezone explicitly after reading
parquet — the exit rule compares against a localised noon timestamp and would
mis-time silently against a naive index.

## `data/borrow_panel/{TICKER}.parquet`

### Prices and contract state

| Column | Meaning |
|---|---|
| `F1_close`, `F2_close`, `F3_close` | Close of the 1st, 2nd and 3rd nearest futures contract |
| `SPOT_close` | Underlying spot close |
| `F1_oi`, `F2_oi`, `F3_oi` | Open interest per contract slot |
| `F1_volume`, `F2_volume` | Traded volume |
| `cycle_id` | Expiry cycle index, 1 = oldest. Contiguous per ticker |
| `is_expiry_day` | 1 on the front-month expiry date |
| `in_backwardation` | Spot above the front-month future |

The `Fn` slot is defined by expiry order at each timestamp: `F1` is the nearest
contract not yet expired, `F2` the next, and so on. A given physical contract
moves from `F3` to `F2` to `F1` as the calendar advances.

### Time to expiry — two conventions

| Column | Convention |
|---|---|
| `F1_dte`, `F2_dte`, `F3_dte` | **Calendar days** to expiry. Used by `b12` and everywhere in this repo |
| `F1_dte_star`, … | **Trading days**, decrementing 15/375 per 15-minute bar |

These are not interchangeable. See [05_limitations.md](05_limitations.md),
item 11.

### Borrow rates

| Column | Definition |
|---|---|
| `b1`, `b2`, `b3` | Annualised basis of each futures slot against spot |
| **`b12`** | `(F1 − F2)/F2 × 365/F1_dte` — the annualised borrow premium. **This repo's classifier, never its timer** |
| `b23`, `b13` | The same for other contract pairs |
| `OI_ratio` | `F1_oi / F2_oi` |
| `U_t` | Source-pipeline roll-pressure measure; not used here |

### Derived features (`feat_*`, absent for BDL)

**Roll flow** — all differences computed within `cycle_id`, so no value bleeds
across an expiry boundary:

| Column | Definition |
|---|---|
| `feat_oi_concentration` | `F1_oi / (F1_oi + F2_oi)` |
| `feat_roll_velocity` | First difference of the above |
| `feat_roll_acceleration` | Second difference |
| `feat_net_roll_flow` | `ΔF2_oi − ΔF1_oi` |
| `feat_roll_fraction` | Net roll flow ÷ total open interest |
| `feat_flow_intensity` | Roll fraction ÷ `F1_dte_star` |
| `feat_roll_deviation` | Concentration minus its mean at the same DTE |
| `feat_relative_oi_change` | Growth-rate difference between the two legs |
| `feat_Vshare`, `feat_volflow` | Volume-weighted equivalents |

**Options surface** — from the front-month chain:

| Column | Definition |
|---|---|
| `feat_atm_iv` | Mean implied vol of the ATM call and put |
| `feat_otm_25d_call_iv`, `feat_otm_15d_call_iv` | IV at the nearest 0.25 / 0.15 delta call |
| `feat_otm_25d_put_iv`, `feat_otm_15d_put_iv` | The put equivalents |
| `feat_25d_risk_reversal` | 25-delta call IV minus 25-delta put IV (put skew) |
| `feat_residual_atm_iv` | ATM IV net of its fitted relationship to the basis |

**Dynamics:** `feat_b12_slope_1d` (25-bar rolling OLS slope of `b12`),
`feat_b12_accel`, `feat_b12_percentile` (rank within the same integer-DTE
bucket), and `feat_beta_*` / `feat_r2_*` (within-cycle regressions of
log price on time to expiry).

## `data/options_atm/{TICKER}_{expiry}.parquet`

SBICARD and RVNL only, 33 expiries, ATM ± 3 strikes. The strike step is the
median gap between adjacent listed strikes for that expiry. The backtest only
ever selects the single ATM strike, so ±3 is a generous margin — but it means
these files **cannot** be used for wing or full-surface work.

Columns: `strike`, `opt_type` (`CE`/`PE`), `close`, `iv`, `delta`, `vega`,
`dte`, `futures_ref_price`, `expiry`, `oi`, `volume`.

`load_options` raises `OptionsUnavailable` when an expiry is not shipped.
Callers must degrade the options strategies to `NaN` and keep the futures
result — silently skipping the cycle is the selection bug documented in
[05_limitations.md](05_limitations.md), item 2.

## Lot sizes

Known only for **SBICARD (800)** and **RVNL (1525)**. The other five are not in
the source research and the panel carries no lot-size column, so they are
deliberately absent from `borrowcycle.data.LOT_SIZES` rather than guessed.

Consequence: wide-scope results are reported in **basis points of F1** and
never in rupees per lot. Basis points are the more comparable unit across names
in any case.

## Units used in results

| Unit | Where |
|---|---|
| Rupees per share | Raw spread levels |
| **Basis points of F1** | All normalised and wide-scope results |
| Rupees per lot | SBICARD/RVNL only |
| Annualised decimal | `b12` (0.15 = 15%) |
| 15-minute bars | Event time. 25 bars = 1 NSE session |
