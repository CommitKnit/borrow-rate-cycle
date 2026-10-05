# Data dictionary

All data lives in the backtesting engine's **ArcticDB** store; nothing is copied into
this repository. [`pipeline/README.md`](../pipeline/README.md) describes how it is
fetched and built, and [`borrowcycle/data.py`](../borrowcycle/data.py) is the only
place the analysis reads it.

The store keeps each value **once** (slim layout). The analysis reads a joined frame
in which the computed borrow columns, the futures-panel inputs and Kite spot are
lined up on the same timestamps.

## Coverage (15-minute, the research universe)

| Ticker | Bars | Cycles in store | First bar | Last bar |
|---|---|---|---|---|
| SBICARD | 12,779 | 22 | 2024-07-03 | 2026-08-18 |
| ASTRAL | 13,425 | 24 | 2024-08-01 | 2026-10-01 |
| VOLTAS | 11,625 | 20 | 2024-07-03 | 2026-06-10 |
| IREDA | 9,847 | 21 | 2025-02-28 | 2026-10-01 |
| KPITTECH | 9,851 | 19 | 2024-11-29 | 2026-07-06 |
| BDL | 8,347 | 17 | 2025-06-02 | 2026-10-01 |
| RVNL | 6,329 | 13 | 2025-06-02 | 2026-06-10 |

Cycles with fewer than 40 bars are skipped by `iter_cycles`. All seven tickers carry
the full feature set.

Index: tz-aware `DatetimeIndex` in `Asia/Kolkata`, monotonically increasing. The
exit rule compares against a localised noon timestamp and would mis-time silently
against a naive index.

## Where each column comes from

### `futures/{T}/15minute` — the F1/F2/F3 panel (31 columns)

| Column | Meaning |
|---|---|
| `F1_open … F3_close` | OHLC of the 1st, 2nd and 3rd nearest futures contract |
| `F1_volume`, `F1_oi`, … | Traded volume and open interest per slot |
| `F1_dte`, `F2_dte`, `F3_dte` | **Calendar days** to that contract's expiry |
| `F1_expiry`, `F1_ticker`, … | The contract occupying each slot |
| `cycle_id` | Count of front-month changes, 1 = oldest; contiguous per ticker |
| `is_expiry_day` | True on bars of a contract's expiry date |

The `Fn` slot is assigned by expiry order on each trading day: `F1` is the nearest
contract not yet expired, `F2` the next. A physical contract moves F3 → F2 → F1 as
the calendar advances. The panel is built from the raw per-contract candles in
`futures_contracts/{T}/15minute/{expiry}`.

### `spot/{T}/15minute` (Kite) — joined as `SPOT_*`

`SPOT_close` (and `SPOT_open/high/low/volume`) are joined on timestamp at read time.
Kite back-adjusts splits and bonuses; none fell inside this window for the seven
names (Kite and Upstox daily closes agree on every day). Intraday Kite spot ends on
2026-09-18, so spot-based columns are NaN on the last bars of series that run later.

### `borrow_rates/{T}/15minute` — computed columns

τ is a **fractional trading-time DTE**: `F{n}_dte_star = (F{n}_dte + 1) − i/25`, with `i`
the bar's 1-based rank within its trading day. τ = dte_star / 365, r = 6.25%.

| Column | Definition |
|---|---|
| `F1_dte_star`, `F2_dte_star`, `F3_dte_star` | the fractional DTE above |
| `b1`, `b2`, `b3` | `r − ln(Fn / S) / τn`: implied borrow rate of each slot against spot, floored at 0 |
| **`b12`** | `r − ln(F2 / F1) / (τ2 − τ1)`: implied borrow rate between F1 and F2, floored at 0. **The research uses it to classify cycles, never to time trades** |
| `b23`, `b13` | the same for the other contract pairs |
| `in_backwardation` | `F1 < S` |
| `spread_spot_f1`, `spread_spot_f2`, `spread_f1_f2` | `S − F1`, `S − F2`, `F1 − F2` in price points |
| `OI_ratio` | `F1_oi / F2_oi` |
| `U_t` | `OI_ratio / (F1_dte_star + 0.04 on expiry-day bars)`: roll pressure; not used by the analysis |

`b12`'s denominator `τ2 − τ1` stays close to one month through the cycle. `b1` is the
measure whose denominator goes to zero at expiry.

### `borrow_rates/{T}/15minute` — features (`feat_*`)

25 columns built by [`pipeline/build_features`](../pipeline/build_features/README.md),
where each is defined. Groups: roll flow and open interest (within-cycle differences),
volume share, the front-month implied-vol smile, and b12 dynamics and trend
regressions. Features built from earlier cycles (`feat_roll_deviation`,
`feat_b12_percentile`, `feat_residual_atm_iv`) pool only cycles that started before
the bar's own, so nothing looks ahead. The `feat_beta_*` regressions of log price on
dte_star measure **cycle-to-date momentum**, not carry. `feat_roll_fraction` existed
in the September snapshot and has been removed.

## Option chains — `options_enriched/{T}/15minute/{expiry}`

`load_options` keeps ATM ± 3 strikes, where the strike step is the median gap
between adjacent listed strikes for that expiry, and these columns: `strike`,
`opt_type` (`CE`/`PE`), `close`, `iv`, `delta`, `vega`, `dte`, `futures_ref_price`,
`expiry`, `oi`, `volume`. Two stored column layouts exist (from two option
pipelines). The store wrapper renames `strike_price`/`instrument_type`/`futures_price`
to the names above.

Enriched chains exist for all seven tickers. The options strategies are evaluated on
SBICARD and RVNL only (`OPTION_TICKERS`), the two names with known lot sizes.

`load_options` raises `OptionsUnavailable` when an expiry has no chain. Callers must
degrade the options strategies to `NaN` and keep the futures result; silently
skipping the cycle is the selection bug documented in
[05_limitations.md](05_limitations.md), item 2.

## Lot sizes

Known only for **SBICARD (800)** and **RVNL (1525)**. The others are deliberately
absent from `borrowcycle.data.LOT_SIZES` rather than guessed, so wide-scope results
are reported in **basis points of F1**, never in rupees per lot.

## Units used in results

| Unit | Where |
|---|---|
| Rupees per share | Raw spread levels |
| **Basis points of F1** | All normalised and wide-scope results |
| Rupees per lot | SBICARD/RVNL only |
| Annualised decimal | `b1…b13` (0.15 = 15%) |
| 15-minute bars | Event time. 25 bars = 1 NSE session |
