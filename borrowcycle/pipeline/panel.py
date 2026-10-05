"""F1/F2/F3 wide panel from per-contract candles (the engine's slim ``futures`` schema).

Ported from src/data/slot.py. Differences, both fixes:

* No spot is joined: spot lives only in the Kite ``spot`` library and is joined at
  read time (``Store.read_panel``).
* The panel is always built over every stored contract, or extended with
  ``cycle_id`` continued from the stored tail, so ``cycle_id`` never depends on a
  chosen start date and no partial rebuild can truncate stored history.

Roll rule: contracts are ranked by expiry once per trading date; F1 is the nearest,
F2 the next, F3 the one after. ``ROLL_ZONE_DTE = 0`` keeps the front contract through
its expiry day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ROLL_ZONE_DTE = 0
SLOTS = ("F1", "F2", "F3")
_FIELDS = ("open", "high", "low", "close", "volume", "oi", "dte", "expiry", "symbol")

#: Stored column order of a ``futures/{T}/{interval}`` panel (31 columns).
PANEL_COLUMNS = (
    [f"{s}_{f}" for f in ("open", "high", "low", "close", "volume", "oi", "dte", "expiry", "ticker") for s in SLOTS]
    + ["cycle_id", "is_expiry_day", "symbol", "interval"]
)
_FLOAT = {f"{s}_{f}" for s in SLOTS for f in ("open", "high", "low", "close")}
_INT = {f"{s}_{f}" for s in SLOTS for f in ("volume", "oi", "dte")}


def _dte(df: pd.DataFrame) -> pd.Series:
    days = (pd.to_datetime(df["expiry"]).values - pd.to_datetime(df.index.date).values)
    return pd.Series(days.astype("timedelta64[D]").astype(int).clip(0), index=df.index)


def assign_slots(contracts: pd.DataFrame) -> tuple[pd.DataFrame, set]:
    """Tag each row F1/F2/F3 by expiry rank within its trading date.

    Ranking per date (not per bar) means a contract with no print at one minute can't
    flip the slot order for that bar. Returns (slotted rows, ISO dates with an expiry).
    """
    df = contracts.copy()
    df["dte"] = _dte(df)
    df["_d"] = df.index.normalize()
    expiry_days = set(df.index[df["dte"] == 0].strftime("%Y-%m-%d"))
    df["_rank"] = df.groupby("_d")["expiry"].rank(method="dense").astype(int) - 1
    front_dte = df.loc[df["_rank"] == 0].groupby("_d")["dte"].first()
    df["_slot"] = df["_rank"] - (df["_d"].map(front_dte) < ROLL_ZONE_DTE).astype(int)
    df = df[df["_slot"].between(0, 2)].copy()
    df["slot"] = df["_slot"].map({0: "F1", 1: "F2", 2: "F3"})
    return df.drop(columns=["_d", "_rank", "_slot"]), expiry_days


def pivot_to_wide(slotted: pd.DataFrame) -> pd.DataFrame:
    mi = slotted.set_index("slot", append=True)[list(_FIELDS)]
    mi = mi[~mi.index.duplicated(keep="first")]
    wide = mi.unstack("slot")
    wide.columns = [f"{slot}_{'ticker' if f == 'symbol' else f}" for f, slot in wide.columns]
    for c in PANEL_COLUMNS[:27]:
        if c not in wide.columns:      # slot absent in this whole span: same as an empty bar
            wide[c] = 0 if c in _INT else np.nan
    return wide


def add_metadata(wide: pd.DataFrame, ticker: str, interval: str, expiry_days: set,
                 prev_f1_expiry: str | None = None, prev_cycle_id: int = 0) -> pd.DataFrame:
    """``cycle_id`` = running count of F1-contract changes, continued from the stored
    tail when extending (``prev_*``). A missing F1 label at an isolated bar is filled
    first, so a thin minute can't fake a roll."""
    wide = wide.copy()
    wide["F1_expiry"] = wide["F1_expiry"].replace("", pd.NA).ffill().bfill()
    prev = wide["F1_expiry"].shift(1)
    if prev_f1_expiry is not None and len(prev):
        prev.iloc[0] = prev_f1_expiry
    wide["cycle_id"] = (prev_cycle_id + (wide["F1_expiry"] != prev).cumsum()).astype("int64")
    wide["is_expiry_day"] = pd.Index(wide.index.strftime("%Y-%m-%d")).isin(expiry_days)
    wide["symbol"] = ticker
    wide["interval"] = interval
    return wide


def cast(wide: pd.DataFrame) -> pd.DataFrame:
    wide = wide.copy()
    for c in PANEL_COLUMNS:
        if c in _FLOAT:
            wide[c] = wide[c].astype("float64")
        elif c in _INT:
            wide[c] = wide[c].fillna(0).astype("int64")
        elif c == "cycle_id":
            wide[c] = wide[c].astype("int64")
        elif c == "is_expiry_day":
            wide[c] = wide[c].astype(bool)
        else:
            # expiry/ticker labels: a bar where the slot is empty keeps NaN, as stored
            wide[c] = wide[c].astype(object)
    return wide[PANEL_COLUMNS]


def build_panel(contracts: pd.DataFrame, ticker: str, interval: str,
                prev_f1_expiry: str | None = None, prev_cycle_id: int = 0) -> pd.DataFrame:
    """Stacked per-contract candles (columns open..oi, expiry, symbol) -> slim panel."""
    if contracts.empty:
        return pd.DataFrame(columns=PANEL_COLUMNS)
    slotted, expiry_days = assign_slots(contracts)
    wide = pivot_to_wide(slotted)
    wide = add_metadata(wide, ticker, interval, expiry_days, prev_f1_expiry, prev_cycle_id)
    return cast(wide).sort_index()
