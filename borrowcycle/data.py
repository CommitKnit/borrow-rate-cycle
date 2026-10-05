"""
Data access for the analysis scripts — reads the backtesting engine's ArcticDB.

All market data lives in the engine store (see borrowcycle/pipeline/config.py and
pipeline/README.md); nothing is copied into this repository. ``load_panel`` returns
the joined 15-minute borrow panel: the computed borrow columns (b1..b13, dte_star,
features) plus the futures-panel inputs (F1-F3 close/OI/DTE, cycle_id, is_expiry_day)
and Kite spot, joined on timestamp at read time.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

PKG_ROOT = Path(__file__).resolve().parent.parent

INTERVAL = "15minute"
TZ       = "Asia/Kolkata"

#: The seven names the research covers.
TICKERS = ["SBICARD", "RVNL", "KPITTECH", "ASTRAL", "BDL", "IREDA", "VOLTAS"]

#: Tickers the options strategies (S2/S3) are evaluated on. Enriched chains exist
#: for all seven at 15minute, but lot sizes are only known for these two, and the
#: published options results are scoped to them.
OPTION_TICKERS = ["SBICARD", "RVNL"]

#: NSE lot sizes. Only these two are known from the source research; the
#: others are deliberately absent rather than guessed, which is why
#: wide-scope results are reported in basis points of F1 and never in rupees
#: per lot. See docs/06_data_dictionary.md.
LOT_SIZES = {"SBICARD": 800, "RVNL": 1525}

#: 15-minute bars in one NSE equity session (09:15-15:30 = 375 minutes).
BARS_PER_DAY = 25

#: Option columns the backtest reads, and strikes kept either side of the money
#: (the backtest only ever selects the single ATM strike).
OPT_COLS = ["strike", "opt_type", "close", "iv", "delta", "vega",
            "dte", "futures_ref_price", "expiry", "oi", "volume"]
STRIKE_PAD = 3


class OptionsUnavailable(FileNotFoundError):
    """Raised when no option chain is stored for a ticker/expiry.

    Callers must degrade the options strategies to NaN and *keep* the
    futures-only result. Silently skipping the whole cycle is the selection
    bug documented in docs/03_strategy_results.md.
    """


class StoreUnavailable(RuntimeError):
    """The engine ArcticDB store (or the arcticdb package) is not available."""


@lru_cache(maxsize=1)
def store():
    from .pipeline import config as cfg
    try:
        from .pipeline.store import Store
    except ImportError as exc:                       # arcticdb not installed
        raise StoreUnavailable(f"arcticdb is required to read the data: {exc}") from exc
    if not cfg.MAIN_DIR.exists() or not cfg.SPOT_DIR.exists():
        raise StoreUnavailable(
            f"ArcticDB store not found under {cfg.ENGINE_ROOT / 'data_cache'}. Build it with the "
            "pipeline (see pipeline/README.md), or set BORROWCYCLE_ENGINE_ROOT to an existing store.")
    return Store()


def available_tickers() -> list[str]:
    return sorted({s.split("/")[0] for s in store().symbols("borrow_rates") if s.endswith(f"/{INTERVAL}")})


def load_panel(ticker: str, columns: list[str] | None = None) -> pd.DataFrame:
    """Load one ticker's joined 15-minute futures/borrow panel.

    Returns a frame with a tz-aware (Asia/Kolkata), monotonically increasing
    DatetimeIndex. The exit rule compares against a tz-localised noon timestamp
    and would mis-time silently if the index came back naive.
    """
    df = store().read_borrow(ticker, INTERVAL)
    if df.empty:
        raise FileNotFoundError(f"No borrow_rates/{ticker}/{INTERVAL} in the store. "
                                f"Available: {available_tickers()}")
    if columns is not None:
        df = df[[c for c in columns if c in df.columns]]
    df.index = df.index.tz_convert(TZ)
    df = df.sort_index()
    assert df.index.is_monotonic_increasing, f"{ticker}: non-monotonic index"
    return df


def iter_cycles(ticker: str, min_bars: int = 40) -> Iterator[tuple[int, pd.DataFrame]]:
    """Yield (cycle_id, frame) for each expiry cycle with enough bars to measure."""
    df = load_panel(ticker)
    for cid, cyc in df.groupby("cycle_id", sort=True):
        cyc = cyc.sort_index()
        if len(cyc) >= min_bars:
            yield int(cid), cyc


def list_option_expiries(ticker: str) -> list[str]:
    """Expiry dates (ISO strings) with an enriched option chain in the store."""
    return store().option_expiries(ticker, INTERVAL)


def load_options(ticker: str, expiry: str) -> pd.DataFrame:
    """The ATM +/- 3-strike option chain for one expiry.

    Raises OptionsUnavailable when the chain is absent — callers must handle
    it explicitly rather than dropping the cycle.
    """
    o = store().read_options(ticker, INTERVAL, expiry)
    if o.empty or "strike" not in o.columns:
        raise OptionsUnavailable(f"No option chain for {ticker} {expiry}")
    strikes = np.sort(o["strike"].dropna().unique())
    step = float(np.median(np.diff(strikes))) if len(strikes) > 1 else 0.0
    if step > 0 and "futures_ref_price" in o.columns:
        o = o[(o["strike"] - o["futures_ref_price"]).abs() <= STRIKE_PAD * step]
    o = o[[c for c in OPT_COLS if c in o.columns]]
    if o.empty:
        raise OptionsUnavailable(f"Option chain for {ticker} {expiry} has no near-the-money rows")
    o.index = o.index.tz_convert(TZ)
    return o.sort_index()
