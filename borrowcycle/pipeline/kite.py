"""Kite Connect client for spot, the only spot source in the store.

Ported from the engine's data/fetchers/kite_fetcher.py (resolve_equity,
fetch_candles_chunked). Used only when the Kite ``spot`` library has no series
for a ticker/interval, or when ``--update`` extends a stale one.
"""
from __future__ import annotations

import time
from datetime import date, datetime, time as dtime, timedelta

import pandas as pd

from . import config as cfg

_SESSION_OPEN, _SESSION_CLOSE = dtime(9, 0), dtime(23, 59, 59)


class KiteSpotClient:
    """Rate-limited (3 req/s) equity candle fetcher over the kiteconnect SDK."""

    def __init__(self, api_key: str | None = None, access_token: str | None = None, kite=None):
        if kite is None:
            from kiteconnect import KiteConnect
            key, tok = (api_key, access_token) if api_key and access_token else cfg.kite_credentials()
            kite = KiteConnect(api_key=key)
            kite.set_access_token(tok)
        self.kite = kite
        self.calls = 0
        self._last = 0.0
        self._instruments: list[dict] | None = None

    def _wait(self) -> None:
        gap = self._last + 1 / 3 - time.monotonic()
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def resolve_equity(self, ticker: str, exchange: str = "NSE") -> int:
        """Instrument token for an NSE equity; falls back to ``name`` for non-rolling
        series (e.g. HEG trades as HEG-BE in Trade-to-Trade)."""
        if self._instruments is None:
            self.calls += 1
            self._instruments = self.kite.instruments(exchange)
        target = ticker.upper()
        rows = [r for r in self._instruments if r.get("instrument_type") == "EQ"]
        hits = [r for r in rows if str(r.get("tradingsymbol", "")).upper() == target] or \
               [r for r in rows if str(r.get("name", "")).upper() == target]
        if not hits:
            raise LookupError(f"no Kite {exchange} EQ instrument for {ticker!r}")
        return int(hits[0]["instrument_token"])

    def candles(self, token: int, interval: str, from_date: date, to_date: date) -> pd.DataFrame:
        """OHLCV for ``interval`` (pipeline name, e.g. 15minute), chunked to Kite's
        per-request day limit, as an Asia/Kolkata-indexed frame."""
        kite_iv, max_days = cfg.KITE_INTERVAL[interval]
        rows, cur = [], from_date
        while cur <= to_date:
            end = min(cur + timedelta(days=max_days - 1), to_date)
            self._wait()
            self.calls += 1
            rows.extend(self.kite.historical_data(
                token, from_date=datetime.combine(cur, _SESSION_OPEN),
                to_date=datetime.combine(end, _SESSION_CLOSE), interval=kite_iv,
                continuous=False, oi=False))
            cur = end + timedelta(days=1)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df.pop("date"), utc=True).dt.tz_convert(cfg.TZ)
        df.index.name = "timestamp"
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df[["open", "high", "low", "close", "volume"]]
