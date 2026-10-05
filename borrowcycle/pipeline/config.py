"""Paths, credentials and interval conventions for the pipeline.

The store is the backtesting engine's ArcticDB. The two URIs below are exactly the
engine's own (backtesting-engine/config/settings.py: ARCTICDB_URI / ARCTICDB_SPOT_URI);
LMDB must be opened with the same map_size the engine uses.
"""
from __future__ import annotations

import os
from pathlib import Path

ENGINE_ROOT = Path(os.environ.get(
    "BORROWCYCLE_ENGINE_ROOT", r"C:\Users\risha\backtesting-engine\backtesting-engine"))
MAIN_DIR = ENGINE_ROOT / "data_cache" / "arcticdb"
SPOT_DIR = ENGINE_ROOT / "data_cache" / "arcticdb_spot"
MAIN_URI = f"lmdb://{MAIN_DIR.resolve()}"
SPOT_URI = f"lmdb://{SPOT_DIR.resolve()}?map_size=8GB"

TZ = "Asia/Kolkata"
UPSTOX_BASE_URL = "https://api.upstox.com"

#: Risk-free rate in the borrow-rate formulas. 6.25% is the value every series
#: currently in the store was computed with (src/update_intraday.py).
RISK_FREE_RATE = 0.0625

#: Futures panels use Upstox interval names; Kite spot uses its own for two of them.
SPOT_INTERVAL_ALIASES = {"1day": "day", "1minute": "minute", "1hour": "60minute"}

#: Bars in one NSE session (09:15-15:30, 375 minutes) per interval.
BARS_PER_DAY = {"1day": 1, "day": 1, "60minute": 7, "30minute": 13, "15minute": 25,
                "5minute": 75, "1minute": 375, "minute": 375}

#: Upstox v3 historical-candle (unit, multiplier) per interval.
V3_UNIT = {"1day": ("days", "1"), "1minute": ("minutes", "1"), "5minute": ("minutes", "5"),
           "15minute": ("minutes", "15"), "30minute": ("minutes", "30"), "60minute": ("minutes", "60")}

#: Interval token for /v2/expired-instruments/historical-candle. Hour units are
#: rejected there, so expired contracts can't be fetched at 60minute.
EXPIRED_INTERVAL = {"1day": "day", "1minute": "1minute", "5minute": "5minute",
                    "15minute": "15minute", "30minute": "30minute"}

#: Kite interval name and its per-request day window (Kite historical API limits).
KITE_INTERVAL = {"1day": ("day", 2000), "60minute": ("60minute", 400), "30minute": ("30minute", 200),
                 "15minute": ("15minute", 200), "5minute": ("5minute", 100), "1minute": ("minute", 60)}

#: Per-contract fetch window: a contract trades as F3/F2/F1 over ~3 months, so
#: 180 days before expiry captures its F3 period too.
CONTRACT_LOOKBACK_DAYS = 180

#: Default history depth for a spot series fetched from scratch.
SPOT_LOOKBACK_DAYS = 5 * 365


def upstox_token() -> str:
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN")
    if not tok:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN is not set")
    return tok


def kite_credentials() -> tuple[str, str]:
    key, tok = os.environ.get("KITE_API_KEY"), os.environ.get("KITE_ACCESS_TOKEN")
    if not (key and tok):
        raise RuntimeError("KITE_API_KEY and KITE_ACCESS_TOKEN must be set to fetch spot from Kite")
    return key, tok
