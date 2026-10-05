"""ArcticDB access in the engine's slim layout.

Each value is stored exactly once:

* spot           only in the Kite ``spot`` library (its own LMDB path, larger map_size)
* futures inputs only in the ``futures`` F1/F2/F3 panel
* borrow_rates   only the columns it computes (dte_star, b1..b13, spreads, OI_ratio, U_t, feat_*)

Writes strip any column that belongs to another library; ``read_panel`` and
``read_borrow`` join them back on the same timestamps. These rules mirror
``backtesting-engine/data/storage/arctic_store.py`` exactly (a test checks the
constants match), because both code bases write to the same store.

Existing symbols are only ever extended with ``upsert`` (ArcticDB ``update``), which
adds new data without storing a second full copy. Full rewrites prune the previous
version; ``snapshot`` is the only way an old version is kept, and only on purpose.
"""
from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd
from arcticdb import Arctic

from . import config as cfg

LIB_SPOT, LIB_FUTURES, LIB_CONTRACTS = "spot", "futures", "futures_contracts"
LIB_BORROW, LIB_OPTIONS = "borrow_rates", "options_enriched"
_SPOT_STORE_LIBS = {"spot", "spot_adjusted"}

FUTURES_SPOT_COLUMNS = (
    "SPOT_open", "SPOT_high", "SPOT_low", "SPOT_close", "SPOT_volume", "SPOT_ticker", "spot_missing",
)
BORROW_COPIED_COLUMNS = (
    "F1_close", "F2_close", "F3_close", "SPOT_close",
    "F1_dte", "F2_dte", "F3_dte", "F1_oi", "F2_oi", "F3_oi",
    "cycle_id", "is_expiry_day", "F1_volume", "F2_volume",
)
PANEL_FIELD = re.compile(r"^F[123]_(open|high|low|close|volume|oi|dte|expiry|ticker)$")

# Raw 1-minute spot carries log columns in the engine store (kept consistent here).
LOG_COLUMNS = ("log_price", "log_return", "corp_action_flag")
SPOT_LOG_INTERVALS = frozenset({"minute", "1minute"})
CORP_ACTION_LOGRET_THRESHOLD = 0.5


def to_ist(df: pd.DataFrame) -> pd.DataFrame:
    """Named Asia/Kolkata tz-aware, sorted DatetimeIndex called ``timestamp``."""
    df = df.copy()
    idx = pd.DatetimeIndex(df.index)
    df.index = idx.tz_convert(cfg.TZ) if idx.tz is not None else idx.tz_localize(cfg.TZ)
    df.index.name = "timestamp"
    return df.sort_index(kind="stable")


def _ist_ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_convert(cfg.TZ) if t.tz is not None else t.tz_localize(cfg.TZ)


def add_log_columns(df: pd.DataFrame, prev_close: float | None = None) -> pd.DataFrame:
    out = df.copy()
    close = out["close"].astype("float64")
    out["log_price"] = np.log(close.where(close > 0))
    out["log_return"] = out["log_price"].diff()
    if prev_close is not None and len(out):
        out.iloc[0, out.columns.get_loc("log_return")] = out["log_price"].iloc[0] - np.log(float(prev_close))
    out["corp_action_flag"] = (out["log_return"].abs() > CORP_ACTION_LOGRET_THRESHOLD).astype(bool)
    return out


def slim(library: str, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """Drop columns stored in another library (the engine's ``ArcticStore._slim``)."""
    if library == LIB_FUTURES:
        drop = [c for c in FUTURES_SPOT_COLUMNS if c in df.columns]
    elif library == LIB_BORROW and not symbol.endswith("/cycle_summary"):
        drop = [c for c in df.columns
                if c in BORROW_COPIED_COLUMNS or c in FUTURES_SPOT_COLUMNS or PANEL_FIELD.match(str(c))]
    else:
        return df
    return df.drop(columns=drop) if drop else df


class Store:
    def __init__(self, main_uri: str | None = None, spot_uri: str | None = None):
        self.main_uri = main_uri or cfg.MAIN_URI
        self.spot_uri = spot_uri or (cfg.SPOT_URI if main_uri is None else self.main_uri)
        self.main = Arctic(self.main_uri)
        # LMDB can't open one path twice in a process: share the connection if equal.
        self.spot = self.main if self.spot_uri == self.main_uri else Arctic(self.spot_uri)
        self._libs: dict = {}

    # ── basics ──────────────────────────────────────────────────────────────
    def lib(self, name: str):
        if name not in self._libs:
            arctic = self.spot if name in _SPOT_STORE_LIBS else self.main
            self._libs[name] = arctic.get_library(name, create_if_missing=True)
        return self._libs[name]

    def has(self, library: str, symbol: str) -> bool:
        return self.lib(library).has_symbol(symbol)

    def symbols(self, library: str, prefix: str = "") -> list[str]:
        return sorted(s for s in self.lib(library).list_symbols() if s.startswith(prefix))

    def columns(self, library: str, symbol: str) -> list[str]:
        return [c.name for c in self.lib(library).get_description(symbol).columns]

    def span(self, library: str, symbol: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        if not self.has(library, symbol):
            return None
        d = self.lib(library).get_description(symbol)
        if not d.row_count or d.date_range is None or d.date_range[0] is None:
            return None
        return tuple(_ist_ts(x) for x in d.date_range)

    def row_count(self, library: str, symbol: str) -> int:
        return self.lib(library).get_description(symbol).row_count if self.has(library, symbol) else 0

    def versions(self, library: str, symbol: str) -> int:
        return len(self.lib(library).list_versions(symbol)) if self.has(library, symbol) else 0

    def read(self, library: str, symbol: str, date_range=None, columns=None) -> pd.DataFrame:
        if not self.has(library, symbol):
            return pd.DataFrame()
        df = self.lib(library).read(symbol, date_range=date_range, columns=columns).data
        return to_ist(df) if isinstance(df.index, pd.DatetimeIndex) else df

    def metadata(self, library: str, symbol: str) -> dict:
        return (self.lib(library).read_metadata(symbol).metadata or {}) if self.has(library, symbol) else {}

    # ── writes (slim layout enforced) ───────────────────────────────────────
    def _prepare(self, library: str, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        df = slim(library, symbol, df)
        if isinstance(df.index, pd.DatetimeIndex):
            df = to_ist(df)
        return df

    def write(self, library: str, symbol: str, df: pd.DataFrame, metadata: dict | None = None) -> None:
        """Full write of a symbol; the previous version is pruned (no copy kept)."""
        df = self._prepare(library, symbol, df)
        if library == LIB_SPOT and symbol.rsplit("/", 1)[-1] in SPOT_LOG_INTERVALS \
                and not set(LOG_COLUMNS) & set(df.columns):
            df = add_log_columns(df)
        self.lib(library).write(symbol, df, metadata=metadata, prune_previous_versions=True)

    def upsert(self, library: str, symbol: str, df: pd.DataFrame) -> int:
        """Replace the date range ``df`` covers and insert the rest (ArcticDB ``update``).

        Only the new data is written; unchanged history is shared with the previous
        version, so this never stores a second full copy. Columns are aligned to the
        stored schema: missing ones (e.g. feat_* on freshly appended borrow rows) are
        filled with NaN/False, unknown extra ones raise.
        """
        if not len(df):
            return 0
        if not self.has(library, symbol):
            self.write(library, symbol, df)
            return len(df)
        df = self._prepare(library, symbol, df)
        lib = self.lib(library)
        stored = self.columns(library, symbol)
        extra = [c for c in df.columns if c not in stored]
        if extra:
            raise ValueError(f"{library}/{symbol}: columns {extra} are not in the stored schema; "
                             f"a schema change needs a full rewrite")
        tail = lib.tail(symbol, 1).data
        if library == LIB_SPOT and set(LOG_COLUMNS) <= set(stored) and not set(LOG_COLUMNS) & set(df.columns):
            before = lib.read(symbol, date_range=(None, df.index.min() - pd.Timedelta(1, "ns")),
                              columns=["close"]).data
            df = add_log_columns(df, before["close"].iloc[-1] if len(before) else None)
        for c in stored:
            if c not in df.columns:
                df[c] = False if tail[c].dtype == bool else np.nan
        df = df[stored]
        for c in stored:
            if tail[c].dtype != df[c].dtype:
                try:
                    df[c] = df[c].astype(tail[c].dtype)
                except (TypeError, ValueError):
                    pass
        lib.update(symbol, df)
        lib.prune_previous_versions(symbol)       # keep one version (snapshots still protect theirs)
        return len(df)

    def snapshot(self, libraries: list[str], name: str) -> str:
        for l in libraries:
            if name not in self.lib(l).list_snapshots():
                self.lib(l).snapshot(name)
        return name

    @staticmethod
    def snapshot_name(script: str) -> str:
        return f"borrowcycle_pre_{script}_{date.today().isoformat()}"

    # ── spot (Kite) ─────────────────────────────────────────────────────────
    def spot_symbol_for(self, ticker: str, interval: str) -> str | None:
        """Kite name first (day/minute/60minute): short Upstox series stored under
        1minute/1hour by other tools must not shadow the full Kite history."""
        for iv in (cfg.SPOT_INTERVAL_ALIASES.get(interval), interval):
            if iv and self.has(LIB_SPOT, f"{ticker}/{iv}"):
                return f"{ticker}/{iv}"
        return None

    def kite_symbol(self, ticker: str, interval: str) -> str:
        """Symbol a newly fetched Kite series is written under."""
        return f"{ticker}/{cfg.KITE_INTERVAL[interval][0]}"

    # ── joined reads ────────────────────────────────────────────────────────
    def attach_spot(self, ticker: str, interval: str, df: pd.DataFrame) -> pd.DataFrame:
        sym = self.spot_symbol_for(ticker, interval)
        cols = ["open", "high", "low", "close", "volume"]
        spot = pd.DataFrame(columns=cols)
        if sym is not None and len(df):
            spot = self.read(LIB_SPOT, sym, date_range=(df.index.min(), df.index.max()), columns=cols)
            spot = spot[~spot.index.duplicated(keep="last")]
        spot = spot.reindex(df.index)
        out = df.copy()
        for c in cols:
            out[f"SPOT_{c}"] = spot[c].astype("float64")
        out["SPOT_volume"] = out["SPOT_volume"].fillna(0)
        out["SPOT_ticker"] = ticker if sym is not None else ""
        out["spot_missing"] = out["SPOT_close"].isna()
        return out

    def read_panel(self, ticker: str, interval: str, date_range=None, with_spot: bool = True) -> pd.DataFrame:
        df = self.read(LIB_FUTURES, f"{ticker}/{interval}", date_range=date_range)
        return self.attach_spot(ticker, interval, df) if (with_spot and len(df)) else df

    def read_borrow(self, ticker: str, interval: str, date_range=None, joined: bool = True) -> pd.DataFrame:
        sym = f"{ticker}/{interval}"
        br = self.read(LIB_BORROW, sym, date_range=date_range)
        if not joined or br.empty:
            return br
        inputs = [c for c in BORROW_COPIED_COLUMNS if c != "SPOT_close"]
        fut = self.read(LIB_FUTURES, sym, date_range=(br.index.min(), br.index.max()))
        have = [c for c in inputs if c in fut.columns]
        out = self.attach_spot(ticker, interval, fut[have].join(br, how="right"))
        return out[have[:3] + ["SPOT_close"] + have[3:] + list(br.columns)]

    # ── options ─────────────────────────────────────────────────────────────
    def option_expiries(self, ticker: str, interval: str) -> list[str]:
        return sorted(s.rsplit("/", 1)[-1] for s in self.symbols(LIB_OPTIONS, f"{ticker}/{interval}/"))

    def read_options(self, ticker: str, interval: str, expiry: str) -> pd.DataFrame:
        """Enriched chain with both known column layouts normalised."""
        df = self.read(LIB_OPTIONS, f"{ticker}/{interval}/{expiry}")
        return df.rename(columns={"strike_price": "strike", "instrument_type": "opt_type",
                                  "futures_price": "futures_ref_price"})
