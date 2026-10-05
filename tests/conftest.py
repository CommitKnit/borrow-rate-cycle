"""Shared fixtures. Every test uses a throwaway LMDB store, never the engine store.

map_size=20MB: on Windows LMDB allocates the whole map on disk per library at open.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from borrowcycle.pipeline.store import Store

TZ = "Asia/Kolkata"


@pytest.fixture
def store(tmp_path):
    return Store(main_uri=f"lmdb://{tmp_path}?map_size=20MB")


def business_days(start: date, end: date) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end, tz=TZ)


def daily_candles(start: date, end: date, base: float, seed: int = 0) -> list:
    """Upstox-style raw rows [iso_ts, o, h, l, c, v, oi] for business days."""
    rng = np.random.default_rng(seed)
    rows = []
    for i, ts in enumerate(business_days(start, end)):
        c = base + i * 0.1 + rng.normal(0, 0.05)
        rows.append([ts.isoformat(), c, c + 1, c - 1, c, 100 + i, 1000 + 10 * i])
    return rows


class FakeUpstox:
    """Expired: E1, E2.  Live: E3, E4.  Counts every call like the real client."""

    def __init__(self, expiries_expired, expiries_live, today: date):
        self.exp_expired, self.exp_live, self.today = expiries_expired, expiries_live, today
        self.calls = 0
        self.fetch_log: list = []

    def _c(self):
        self.calls += 1

    def resolve_spot_key(self, ticker):
        self._c(); return f"NSE_EQ|{ticker}"

    def expiries(self, underlying, start_year=2022):
        self._c(); return list(self.exp_expired)

    def expired_contract_key(self, underlying, expiry):
        self._c(); return f"NSE_FO|1{expiry.replace('-', '')[2:]}|{expiry[8:10]}-{expiry[5:7]}-{expiry[:4]}"

    def expired_candles(self, key, interval, frm, to):
        self._c(); self.fetch_log.append(("expired", key, frm, to))
        return daily_candles(frm, to, base=100 + int(key[-4:]) % 7)

    def live_futures(self, ticker):
        self._c()
        return [{"instrument_key": f"NSE_FO|9{e.replace('-', '')}", "expiry": e} for e in self.exp_live]

    def v3_candles(self, key, interval, frm, to):
        self._c(); self.fetch_log.append(("v3", key, frm, to))
        return daily_candles(frm, to, base=101)


class FakeKite:
    def __init__(self):
        self.calls = 0

    def resolve_equity(self, ticker):
        self.calls += 1; return 42

    def candles(self, token, interval, frm, to):
        self.calls += 1
        idx = business_days(frm, to)
        c = 99 + np.arange(len(idx)) * 0.1
        return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000}, index=idx)


@pytest.fixture
def fakes():
    today = date(2026, 3, 10)
    up = FakeUpstox(["2025-12-30", "2026-01-27"], ["2026-03-30", "2026-04-28", "2026-05-26"], today)
    return up, FakeKite(), today
