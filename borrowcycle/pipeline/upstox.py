"""Upstox REST client for futures contracts (spot comes from Kite, see kite.py).

Ported from the research code base (src/data/fetch.py, contracts.py, ratelimit.py)
and the engine's instrument search (data/fetchers/api_fetcher.py). Endpoints:

  GET /v2/instruments/search                         ticker -> spot key, live futures
  GET /v2/expired-instruments/expiries               expired expiry dates
  GET /v2/expired-instruments/future/contract        expired contract key per expiry
  GET /v2/expired-instruments/historical-candle/...  expired contract candles
  GET /v3/historical-candle/...                      live contract candles
"""
from __future__ import annotations

import logging
import re
import threading
import time
from calendar import monthrange
from collections import deque
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from . import config as cfg

log = logging.getLogger(__name__)

_PLUS_PLAN_CODE, _RATE_LIMIT_CODE = "UDAPI1149", "UDAPI10005"
EXPIRED_KEY_RE = re.compile(r"^[A-Z_]+\|\d+\|\d{2}-\d{2}-\d{4}$")
_EXPIRY_DAY_SWITCH = date(2025, 9, 1)     # NSE monthly expiry: last Thursday -> last Tuesday


class UpstoxPlusPlanError(Exception):
    """UDAPI1149: data behind the Plus-plan paywall. Never retried."""


class UpstoxRateLimitError(Exception):
    """HTTP 429 / UDAPI10005."""


class RateLimiter:
    """Sliding windows for Upstox's 50/s, 500/min and 2000/30min limits."""

    LIMITS = ((1.0, 50), (60.0, 500), (1800.0, 2000))

    def __init__(self):
        self._lock = threading.Lock()
        self._windows = [deque() for _ in self.LIMITS]

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                for dq, (span, _) in zip(self._windows, self.LIMITS):
                    while dq and dq[0] <= now - span:
                        dq.popleft()
                if all(len(dq) < lim for dq, (_, lim) in zip(self._windows, self.LIMITS)):
                    for dq in self._windows:
                        dq.append(now)
                    return
                waits = [dq[0] + span - now for dq, (span, lim) in zip(self._windows, self.LIMITS) if len(dq) >= lim]
            time.sleep(max(min(waits) if waits else 0.01, 0.001))


def _raise_on_error_body(body: dict) -> None:
    for err in body.get("errors", []) or []:
        code = err.get("errorCode")
        if code == _PLUS_PLAN_CODE:
            raise UpstoxPlusPlanError(err.get("message", code))
        if code == _RATE_LIMIT_CODE:
            raise UpstoxRateLimitError(err.get("message", code))
    if body.get("status") == "error":
        raise RuntimeError(f"Upstox API error: {body}")


def _expiry_iso(value) -> str:
    """Upstox reports expiry as ms-epoch (instrument master) or an ISO string (search)."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=ZoneInfo(cfg.TZ)).date().isoformat()
    return str(value)[:10]


def month_chunks(from_date: date, to_date: date):
    """(from, to) pairs in calendar-month steps; the v3 endpoint takes ~1 month per call."""
    cur = from_date
    while cur <= to_date:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt - timedelta(days=1), to_date)
        cur = nxt


def parse_candles(raw: list, instrument_key: str, ticker: str, expiry: str, interval: str) -> pd.DataFrame:
    """Raw [ts, o, h, l, c, v, oi] rows -> the ``futures_contracts`` schema.

    Daily bars are normalised to midnight (v2 returns 00:00, v3 03:30/09:15) so all
    contracts and spot align. Intraday timestamps are floored to the candle boundary
    and cut to 09:15-15:30: the expired endpoint sometimes returns 1-minute offset
    bars (09:16, 09:31 ...) and a 03:30 pre-market summary bar.
    """
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df.index = pd.to_datetime(df.pop("timestamp"), utc=True).dt.tz_convert(cfg.TZ)
    df.index.name = "timestamp"
    if interval == "1day":
        df.index = df.index.normalize()
    else:
        minutes = int("".join(ch for ch in interval if ch.isdigit()))
        df.index = df.index.floor(f"{minutes}min")
        t = df.index
        in_session = ((t.hour > 9) | ((t.hour == 9) & (t.minute >= 15))) & \
                     ((t.hour < 15) | ((t.hour == 15) & (t.minute <= 30)))
        df = df[in_session]
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype("float64")
    df["volume"] = df["volume"].astype("int64")
    df["oi"] = df["oi"].astype("int64")
    df["instrument_token"] = instrument_key
    df["symbol"] = ticker
    df["expiry"] = expiry
    df["interval"] = interval
    exp = date.fromisoformat(expiry)
    df["dte"] = [max(0, (exp - ts.date()).days) for ts in df.index]
    df = df[~df.index.duplicated(keep="first")]
    return df.sort_index()


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month, monthrange(year, month)[1])
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def monthly_expiry_candidates(start_year: int, end_year: int) -> list[str]:
    """Last Thursday of each month (last Tuesday for months ending after 2025-09-01)."""
    cutoff = date.today().replace(day=1) - timedelta(days=1)
    out, y, m = [], start_year, 1
    while y <= end_year:
        weekday = 3 if date(y, m, monthrange(y, m)[1]) < _EXPIRY_DAY_SWITCH else 1
        exp = _last_weekday(y, m, weekday)
        if exp > cutoff:
            break
        out.append(exp.isoformat())
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    return out


class UpstoxClient:
    def __init__(self, token: str | None = None, session: requests.Session | None = None):
        self.token = token or cfg.upstox_token()
        self.session = session or requests.Session()
        self.limiter = RateLimiter()
        self.calls = 0

    # ── transport ────────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict | None = None) -> dict:
        self.limiter.wait()
        self.calls += 1
        resp = self.session.get(
            f"{cfg.UPSTOX_BASE_URL}{path}", params=params, timeout=30,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        if resp.status_code == 429:
            raise UpstoxRateLimitError(f"HTTP 429 on {path}")
        if resp.status_code != 200:
            try:
                _raise_on_error_body(resp.json())
            except ValueError:
                pass
            resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            _raise_on_error_body(body)
        return body

    # ── instrument resolution ───────────────────────────────────────────────
    def search(self, query: str, *, segments: str | None = None,
               instrument_types: str | None = None, exchanges: str = "NSE") -> list[dict]:
        params = {k: v for k, v in {"query": query, "exchanges": exchanges, "segments": segments,
                                    "instrument_types": instrument_types, "records": 30}.items() if v}
        out, page = [], 1
        while True:
            body = self._get("/v2/instruments/search", {**params, "page_number": page})
            out.extend(body.get("data", []))
            total = int(body.get("meta_data", {}).get("page", {}).get("total_pages", page))
            if page >= total:
                return out
            page += 1

    def resolve_spot_key(self, ticker: str) -> str:
        """Exact equity match first, index second (e.g. NIFTY -> NSE_INDEX|Nifty 50)."""
        target = ticker.upper()
        for seg, suffix in (("EQ", "_EQ"), ("INDEX", "_INDEX")):
            for r in self.search(ticker, segments=seg):
                if str(r.get("segment", "")).endswith(suffix) and target in (
                        str(r.get("trading_symbol", "")).upper(), str(r.get("name", "")).upper()):
                    return r["instrument_key"]
        raise LookupError(f"Upstox search found no EQ/INDEX instrument for {ticker!r}")

    def live_futures(self, ticker: str) -> list[dict]:
        """Live FUT contracts, sorted by expiry, each with an ISO ``expiry``."""
        target = ticker.upper()
        rows = [dict(r, expiry=_expiry_iso(r.get("expiry")))
                for r in self.search(ticker, segments="FO", instrument_types="FUT")
                if str(r.get("instrument_type", "")).upper() == "FUT"
                and target in (str(r.get("underlying_symbol", "")).upper(), str(r.get("name", "")).upper())]
        return sorted(rows, key=lambda r: r["expiry"])

    # ── expired contracts ───────────────────────────────────────────────────
    def expiries(self, underlying_key: str, start_year: int = 2022) -> list[str]:
        """Listing endpoint, plus a probe of month-end candidates for years it omits."""
        try:
            listing = sorted(str(d) for d in self._get(
                "/v2/expired-instruments/expiries", {"instrument_key": underlying_key}).get("data", []) if d)
        except Exception as exc:                      # listing is best-effort; the probe covers it
            log.warning("expiry listing failed for %s: %s", underlying_key, exc)
            listing = []
        found = set(listing)
        first_year = date.fromisoformat(listing[0]).year if listing else date.today().year
        if start_year < first_year:
            for exp in monthly_expiry_candidates(start_year, first_year - 1):
                try:
                    if self._get("/v2/expired-instruments/future/contract",
                                 {"instrument_key": underlying_key, "expiry_date": exp}).get("data"):
                        found.add(exp)
                except Exception:
                    continue
        return sorted(d for d in found if date.fromisoformat(d).year >= start_year)

    def expired_contract_key(self, underlying_key: str, expiry: str) -> str:
        data = self._get("/v2/expired-instruments/future/contract",
                         {"instrument_key": underlying_key, "expiry_date": expiry}).get("data", [])
        if not data:
            raise LookupError(f"no expired FUT contract for {underlying_key} {expiry}")
        fut = next((c for c in data if c.get("instrument_type") == "FUT"), data[0])
        key = fut.get("instrument_key", "")
        if not EXPIRED_KEY_RE.match(key):
            raise ValueError(f"unexpected expired instrument_key format: {key!r}")
        return key

    def _expired_window(self, key: str, interval: str, from_date: date, to_date: date) -> list:
        path = (f"/v2/expired-instruments/historical-candle/{quote(key, safe='')}/"
                f"{cfg.EXPIRED_INTERVAL[interval]}/{to_date.isoformat()}/{from_date.isoformat()}")
        return self._get(path)["data"]["candles"]

    def expired_candles(self, key: str, interval: str, from_date: date, to_date: date) -> list:
        """Bisect the window on any error until single days; Plus-plan/429 propagate."""
        if from_date > to_date:
            return []
        try:
            return self._expired_window(key, interval, from_date, to_date)
        except (UpstoxPlusPlanError, UpstoxRateLimitError):
            raise
        except Exception as exc:
            if from_date >= to_date:
                log.warning("expired candles: %s %s failed, skipping: %s", key, from_date, exc)
                return []
            mid = from_date + timedelta(days=(to_date - from_date).days // 2)
            return (self.expired_candles(key, interval, from_date, mid)
                    + self.expired_candles(key, interval, mid + timedelta(days=1), to_date))

    # ── live contracts ──────────────────────────────────────────────────────
    def v3_candles(self, key: str, interval: str, from_date: date, to_date: date) -> list:
        unit, n = cfg.V3_UNIT[interval]
        chunks = [(from_date, to_date)] if interval == "1day" else list(month_chunks(from_date, to_date))
        out = []
        for a, b in chunks:
            path = f"/v3/historical-candle/{quote(key, safe='')}/{unit}/{n}/{b.isoformat()}/{a.isoformat()}"
            out.extend(self._get(path)["data"]["candles"])
        return out
