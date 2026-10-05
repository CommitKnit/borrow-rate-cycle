# fetch_ticker

## What it does
Loads the futures contracts of a ticker into the engine ArcticDB, builds its F1/F2/F3
panel, and fetches Kite spot only if the store doesn't have it. Before fetching
anything it checks what is already stored for that interval, so a ticker that is
fully stored costs zero API calls.

## Inputs
| Source | Used for |
|---|---|
| ArcticDB `spot/{T}/{kite interval}` | is spot already there? |
| ArcticDB `futures/{T}/{interval}`, `futures_contracts/{T}/{interval}/*` | is the panel / each contract already there? |
| Upstox `/v2/instruments/search` | ticker → underlying key, live futures contracts |
| Upstox `/v2/expired-instruments/expiries`, `/future/contract`, `/historical-candle` | expired contracts and their candles |
| Upstox `/v3/historical-candle` | live contracts' candles |
| Kite historical API | spot, only when missing |

## Outputs
| Library / symbol | Content |
|---|---|
| `futures_contracts/{T}/{interval}/{expiry}` | raw candles of one contract: open, high, low, close, volume, oi, instrument_token, symbol, expiry, interval, dte |
| `futures/{T}/{interval}` | F1/F2/F3 panel, 31 columns, no spot |
| `spot/{T}/{day\|minute\|15minute…}` | Kite OHLCV (only written if it was missing) |

## How it works
1. **Spot.** Look up the Kite spot series (interval aliases below). If it exists, report
   it and move on. If not, resolve the equity's Kite token and fetch about 5 years in
   Kite's per-request chunks. Spot is never fetched from Upstox.
2. **Panel already stored?** In the default mode, stop here: nothing else is fetched.
3. **Contracts.** Resolve the underlying key through instrument search. List expired
   expiries (the listing endpoint, plus a probe of month-end dates for older years it
   omits). For each expired contract not yet stored, fetch expiry − 180 days → expiry
   from the expired-instruments endpoint, splitting the window in half on any error.
   Then fetch each live contract from v3, one month per request.
4. **Panel.** Rank the contracts by expiry on each trading day (F1 = nearest), pivot
   to one row per bar, and count `cycle_id` as the number of F1 changes. The panel is
   always built over every stored contract, so `cycle_id` doesn't depend on a start
   date.
5. Print a summary: what was skipped, fetched, the rows and span, and the number of
   API calls.

```
                   spot stored? ── yes ──► skip spot
                       │ no
                       ▼
                 fetch from Kite

  default mode:    panel stored? ── yes ──► skip (0 Upstox calls)
                       │ no
                       ▼
     fetch contracts not yet stored ──► build panel from all stored contracts

  --update:  fetch only bars after each live contract's last stored bar, plus newly
             expired contracts ──► extend the panel from its last stored day,
             continuing cycle_id
  --force:   snapshot futures + futures_contracts ──► refetch all ──► rebuild
```

## Usage
```bash
python pipeline/fetch_ticker/fetch_ticker.py RVNL --intervals 15minute 1day --dry-run
python pipeline/fetch_ticker/fetch_ticker.py NEWCO --intervals 15minute 1day
python pipeline/fetch_ticker/fetch_ticker.py SWIGGY BDL --update
```

## Re-running / what gets skipped
* Stored spot is never refetched (except `--update` with a stale tail, which appends).
* A stored expired contract that reaches its expiry date is never refetched.
* `--update` writes only new rows (ArcticDB `update`, previous version pruned).
* `--force` keeps a snapshot and prints how to delete it.

## Interval names

| Pipeline / futures | Kite spot |
|---|---|
| `1day` | `day` |
| `1minute` | `minute` |
| `60minute` (`1hour`) | `60minute` |
| `30minute`, `15minute`, `5minute` | same |

The Kite name is always tried first: a few tickers also hold short Upstox spot series
under `1minute`/`1hour` written by other tools, and those must not shadow the full Kite
history.

## Caveats
* Upstox serves expired contracts at `day` and 1/3/5/15/30-minute only, so a
  `60minute` panel can only cover live contracts.
* Upstox keeps expired contracts for a limited time. Contracts already in
  `futures_contracts` are the only copy of older history, so `--force` refetching
  them can lose bars Upstox no longer serves; the snapshot guards against that.
* NSE moved monthly expiry from the last Thursday to the last Tuesday for contracts
  expiring after 2025-09-01; the month-end probe accounts for this.
