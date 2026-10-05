# Data pipeline

Three steps take a ticker from the broker APIs to the features the research uses.
All data lives in one place: the backtesting engine's **ArcticDB** store. Nothing is
copied into this repository, and the analysis scripts in `scripts/` read the same
store.

```
 Upstox API (futures)        Kite API (spot)
        │                          │
        ▼                          ▼
 ┌──────────────────┐     ┌──────────────────┐
 │ futures_contracts│     │ spot  (Kite)     │   only fetched if missing
 │ one per expiry   │     │ Nifty-500 OHLCV  │
 └────────┬─────────┘     └────────┬─────────┘
          │ 1. fetch_ticker        │
          ▼                        │
 ┌──────────────────┐              │
 │ futures          │  F1/F2/F3 panel, futures columns only
 └────────┬─────────┘              │
          │ 2. build_borrow_rates  │ (spot joined at read time)
          ▼                        ▼
 ┌─────────────────────────────────────────┐
 │ borrow_rates   b1..b13, dte_star, ...   │
 │                3. build_features → feat_*│
 └─────────────────────────────────────────┘
```

| Step | Script | Writes |
|---|---|---|
| 1 | [`fetch_ticker/`](fetch_ticker/) | `futures_contracts/{T}/{interval}/{expiry}`, `futures/{T}/{interval}`, and `spot/{T}/{kite interval}` only if missing |
| 2 | [`build_borrow_rates/`](build_borrow_rates/) | `borrow_rates/{T}/{interval}` (computed columns) + `…/cycle_summary` |
| 3 | [`build_features/`](build_features/) | the 25 `feat_*` columns in `borrow_rates/{T}/{interval}` |

## How the store is laid out

Each value is stored **exactly once**:

| Library | Store | Symbol | Holds |
|---|---|---|---|
| `spot` | `data_cache/arcticdb_spot` | `{T}/{day,60minute,30minute,15minute,5minute,minute}` | Kite spot OHLCV (the only spot source) |
| `futures_contracts` | `data_cache/arcticdb` | `{T}/{interval}/{expiry}` | raw candles of each futures contract |
| `futures` | `data_cache/arcticdb` | `{T}/{interval}` | F1/F2/F3 panel: OHLCV, OI, DTE, expiry, ticker per slot, `cycle_id`, `is_expiry_day` (31 columns, no spot) |
| `borrow_rates` | `data_cache/arcticdb` | `{T}/{interval}` | only what is computed: `F*_dte_star`, `b1…b13`, `in_backwardation`, spreads, `OI_ratio`, `U_t`, `feat_*` |
| `borrow_rates` | `data_cache/arcticdb` | `{T}/{interval}/cycle_summary` | one row per cycle |
| `options_enriched` | `data_cache/arcticdb` | `{T}/{interval}/{expiry}` | option chains with IV/Greeks (read by the IV features) |

Readers get the familiar wide frame by joining on timestamp
(`Store.read_panel` adds Kite spot as `SPOT_*`; `Store.read_borrow` adds the panel
inputs and `SPOT_close`). Writes strip any column that belongs to another library,
so a copy can never creep back in. These rules are identical to the engine's own
`data/storage/arctic_store.py`; `tests/test_store_slim.py` checks they stay in sync.

## Write rules

* **Default:** write only what is missing. Anything already stored is skipped with
  zero API calls.
* **`--update`:** extend existing series with new rows only (ArcticDB `update`; the
  previous version is pruned, so no second copy is kept).
* **`--force`:** take an ArcticDB snapshot `borrowcycle_pre_<script>_<date>`, then
  rebuild. The run prints the one-line command that deletes the snapshot once you
  have checked the result.
* **`--dry-run`:** report what would happen; no writes, no API calls.

## Setup

```bash
pip install -e .
```

| Variable | Needed for |
|---|---|
| `BORROWCYCLE_ENGINE_ROOT` | folder whose `data_cache/` holds the store. Default: a sibling `../backtesting-engine/backtesting-engine` checkout if one exists, otherwise this repo (a fresh clone builds its store in `./data_cache/`, which is git-ignored) |
| `UPSTOX_ACCESS_TOKEN` | fetching futures contracts |
| `KITE_API_KEY`, `KITE_ACCESS_TOKEN` | fetching spot that is not in the store yet |

No token is needed when everything requested is already stored.

## Typical runs

```bash
python pipeline/fetch_ticker/fetch_ticker.py RVNL --intervals 15minute 1day --dry-run
python pipeline/fetch_ticker/fetch_ticker.py SWIGGY --update
python pipeline/build_borrow_rates/build_borrow_rates.py SWIGGY --update
python pipeline/build_features/build_features.py
```
