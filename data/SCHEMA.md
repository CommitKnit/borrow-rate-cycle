# Shipped data

- `borrow_panel/{TICKER}.parquet` — 15-minute futures, open interest, borrow
  rates and derived features. 7 tickers, 67,838 rows, 126 expiry cycles,
  2024-07-03 to 2026-08-12.
- `options_atm/{TICKER}_{expiry}.parquet` — ATM ± 3 strike option chains for
  SBICARD and RVNL, 33 expiries.
- `MANIFEST.json` — row counts, date spans and sha256 per file.

Full column definitions, units and caveats:
[`../docs/06_data_dictionary.md`](../docs/06_data_dictionary.md).

Produced by `scripts/00_export_data.py` from an ArcticDB store; that script is
kept for provenance and cannot run from a clone.
