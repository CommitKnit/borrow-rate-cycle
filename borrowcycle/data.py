"""
Local data access — replaces the ArcticDB layer of the source research repo.

Everything this package needs ships inside data/. There is no database
dependency: `load_panel` reads a parquet file and restores the tz-aware
Asia/Kolkata index that the signal code relies on.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pandas as pd

PKG_ROOT  = Path(__file__).resolve().parent.parent
PANEL_DIR = PKG_ROOT / "data" / "borrow_panel"
OPT_DIR   = PKG_ROOT / "data" / "options_atm"

INTERVAL = "15minute"
TZ       = "Asia/Kolkata"

#: All tickers shipped with the repo.
TICKERS = ["SBICARD", "RVNL", "KPITTECH", "ASTRAL", "BDL", "IREDA", "VOLTAS"]

#: Tickers for which an ATM options extract is shipped (S2/S3 are only
#: computable for these).
OPTION_TICKERS = ["SBICARD", "RVNL"]

#: NSE lot sizes. Only these two are known from the source research; the
#: others are deliberately absent rather than guessed, which is why
#: wide-scope results are reported in basis points of F1 and never in rupees
#: per lot. See docs/06_data_dictionary.md.
LOT_SIZES = {"SBICARD": 800, "RVNL": 1525}

#: 15-minute bars in one NSE equity session (09:15-15:30 = 375 minutes).
BARS_PER_DAY = 25


class OptionsUnavailable(FileNotFoundError):
    """Raised when an option chain is not shipped for a ticker/expiry.

    Callers must degrade the options strategies to NaN and *keep* the
    futures-only result. Silently skipping the whole cycle is the selection
    bug documented in docs/03_strategy_results.md.
    """


def available_tickers() -> list[str]:
    return sorted(p.stem for p in PANEL_DIR.glob("*.parquet"))


def load_panel(ticker: str, columns: list[str] | None = None) -> pd.DataFrame:
    """Load one ticker's 15-minute futures/borrow panel.

    Returns a frame with a tz-aware (Asia/Kolkata), monotonically increasing
    DatetimeIndex. The tz is restored explicitly: the exit rule compares
    against a tz-localised noon timestamp and would mis-time silently if the
    index came back naive.
    """
    path = PANEL_DIR / f"{ticker}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"No panel for {ticker!r} at {path}. Available: {available_tickers()}"
        )
    df = pd.read_parquet(path, columns=columns)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError(f"{ticker}: expected a DatetimeIndex, got {type(df.index)}")
    df.index = (
        df.index.tz_localize(TZ) if df.index.tz is None else df.index.tz_convert(TZ)
    )
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
    """Expiry dates (ISO strings) for which an ATM option extract is shipped."""
    return sorted(p.stem.split("_", 1)[1] for p in OPT_DIR.glob(f"{ticker}_*.parquet"))


def load_options(ticker: str, expiry: str) -> pd.DataFrame:
    """Load the ATM+/-3-strike option chain for one expiry.

    Raises OptionsUnavailable when the extract is absent — callers must handle
    it explicitly rather than dropping the cycle.
    """
    path = OPT_DIR / f"{ticker}_{expiry}.parquet"
    if not path.exists():
        raise OptionsUnavailable(f"No option extract for {ticker} {expiry} at {path}")
    df = pd.read_parquet(path)
    df.index = (
        df.index.tz_localize(TZ) if df.index.tz is None else df.index.tz_convert(TZ)
    )
    return df.sort_index()
