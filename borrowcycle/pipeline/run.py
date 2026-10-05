"""Orchestration for the three pipeline steps (used by pipeline/*/ scripts and tests).

Modes
-----
missing  (default) write only what is absent; anything already stored is skipped
         with zero API calls.
update   extend existing symbols with new data via ``Store.upsert`` (only new rows
         are written; no second full copy is stored).
force    snapshot the affected libraries, then rebuild from scratch (previous
         versions are pruned; the snapshot is the only copy kept, and the summary
         prints how to delete it once checked).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Callable

import numpy as np
import pandas as pd

from . import borrow, features
from . import config as cfg
from .panel import build_panel
from .store import LIB_BORROW, LIB_CONTRACTS, LIB_FUTURES, LIB_SPOT, Store
from .upstox import parse_candles

log = logging.getLogger(__name__)
MODES = ("missing", "update", "force")


def _last_session(today: date) -> date:
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def snapshot_hint(name: str, libraries: list[str]) -> str:
    libs = ", ".join(repr(l) for l in libraries)
    return (f"snapshot {name!r} kept on {libs}. Delete it once checked:\n"
            f"  python -c \"from borrowcycle.pipeline.store import Store; s=Store(); "
            f"[s.lib(l).delete_snapshot({name!r}) for l in ({libs},)]\"")


# ── step 1: fetch ─────────────────────────────────────────────────────────────

def fetch_spot(store: Store, ticker: str, interval: str, mode: str, dry_run: bool,
               kite_factory: Callable, today: date) -> str:
    """Kite spot only. ``force`` never rewrites spot: it is shared Nifty-500 data."""
    sym = store.spot_symbol_for(ticker, interval)
    if sym:
        lo, hi = store.span(LIB_SPOT, sym)
        if mode != "update" or hi.date() >= _last_session(today):
            return f"spot   {sym}: already stored ({store.row_count(LIB_SPOT, sym):,} rows, {lo.date()} -> {hi.date()})"
        start = hi.date()
        if dry_run:
            return f"spot   {sym}: would fetch Kite {start} -> {today}"
        kite = kite_factory()
        df = kite.candles(kite.resolve_equity(ticker), interval, start, today)
        n = store.upsert(LIB_SPOT, sym, df[df.index > hi]) if len(df) else 0
        return f"spot   {sym}: appended {n:,} Kite bars"
    target = store.kite_symbol(ticker, interval)
    start = today - timedelta(days=cfg.SPOT_LOOKBACK_DAYS)
    if dry_run:
        return f"spot   {target}: missing -> would fetch Kite {start} -> {today}"
    kite = kite_factory()
    df = kite.candles(kite.resolve_equity(ticker), interval, start, today)
    if df.empty:
        return f"spot   {target}: Kite returned no data"
    store.write(LIB_SPOT, target, df, metadata={"source": "kite", "fetched_by": "borrowcycle"})
    return f"spot   {target}: wrote {len(df):,} Kite bars ({df.index.min().date()} -> {df.index.max().date()})"


def _cached_contracts(store: Store, ticker: str, interval: str) -> dict[str, pd.Timestamp]:
    prefix = f"{ticker}/{interval}/"
    return {s[len(prefix):]: store.span(LIB_CONTRACTS, s)[1] for s in store.symbols(LIB_CONTRACTS, prefix)
            if store.span(LIB_CONTRACTS, s)}


def fetch_contracts(store: Store, ticker: str, interval: str, mode: str,
                    upstox_factory: Callable, today: date) -> list[str]:
    """Fill ``futures_contracts`` for every expired and live contract, skipping cached ones."""
    up = upstox_factory()
    notes: list[str] = []
    cached = {} if mode == "force" else _cached_contracts(store, ticker, interval)
    underlying = up.resolve_spot_key(ticker)
    start_year = min(int(e[:4]) for e in cached) if cached else 2022
    for exp in up.expiries(underlying, start_year=start_year):
        exp_d = date.fromisoformat(exp)
        if exp_d >= today:
            continue
        sym = f"{ticker}/{interval}/{exp}"
        last = cached.get(exp)
        if last is not None and last.date() >= exp_d:
            continue                                          # complete contract already stored
        if interval not in cfg.EXPIRED_INTERVAL:
            notes.append(f"{exp}: expired contracts can't be fetched at {interval}")
            continue
        key = up.expired_contract_key(underlying, exp)
        frm = last.date() if last is not None else exp_d - timedelta(days=cfg.CONTRACT_LOOKBACK_DAYS)
        df = parse_candles(up.expired_candles(key, interval, frm, exp_d), key, ticker, exp, interval)
        if len(df):
            (store.upsert if last is not None else store.write)(LIB_CONTRACTS, sym, df)
            notes.append(f"{exp}: {'extended' if last is not None else 'fetched'} {len(df):,} bars")
    for c in up.live_futures(ticker):
        exp = c["expiry"]
        exp_d = date.fromisoformat(exp)
        if exp_d < today:
            continue
        sym = f"{ticker}/{interval}/{exp}"
        last = cached.get(exp)
        frm = last.date() if last is not None else exp_d - timedelta(days=cfg.CONTRACT_LOOKBACK_DAYS)
        df = parse_candles(up.v3_candles(c["instrument_key"], interval, frm, today),
                           c["instrument_key"], ticker, exp, interval)
        if len(df):
            (store.upsert if last is not None else store.write)(LIB_CONTRACTS, sym, df)
            notes.append(f"{exp} (live): {'extended' if last is not None else 'fetched'} {len(df):,} bars")
    return notes


def _read_contracts(store: Store, ticker: str, interval: str, since=None) -> pd.DataFrame:
    frames = [store.read(LIB_CONTRACTS, s, date_range=(since, None))
              for s in store.symbols(LIB_CONTRACTS, f"{ticker}/{interval}/")]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames).sort_index(kind="stable") if frames else pd.DataFrame()


def build_or_extend_panel(store: Store, ticker: str, interval: str, extend: bool) -> str:
    sym = f"{ticker}/{interval}"
    if not extend or not store.has(LIB_FUTURES, sym):
        panel = build_panel(_read_contracts(store, ticker, interval), ticker, interval)
        if panel.empty:
            return f"panel  {sym}: no contracts cached"
        store.write(LIB_FUTURES, sym, panel, metadata={"built_by": "borrowcycle", "from": LIB_CONTRACTS})
        return f"panel  {sym}: wrote {len(panel):,} bars, {panel['cycle_id'].nunique()} cycles"
    hi = store.span(LIB_FUTURES, sym)[1]
    day = hi.normalize()
    prev = store.read(LIB_FUTURES, sym, date_range=(None, day - pd.Timedelta(1, "ns")),
                      columns=["F1_expiry", "cycle_id"]).tail(1)
    seg = build_panel(_read_contracts(store, ticker, interval, since=day), ticker, interval,
                      prev_f1_expiry=prev["F1_expiry"].iloc[0] if len(prev) else None,
                      prev_cycle_id=int(prev["cycle_id"].iloc[0]) if len(prev) else 0)
    new = int((seg.index > hi).sum())
    if not new:
        return f"panel  {sym}: up to date ({hi})"
    store.upsert(LIB_FUTURES, sym, seg)
    return f"panel  {sym}: extended by {new:,} bars to {seg.index.max()}"


def fetch_ticker(store: Store, ticker: str, interval: str, *, mode: str = "missing", dry_run: bool = False,
                 upstox_factory: Callable | None = None, kite_factory: Callable | None = None,
                 today: date | None = None) -> dict:
    today = today or date.today()
    clients: dict = {}

    def lazy(name, factory):
        def get():
            if name not in clients:
                clients[name] = factory()
            return clients[name]
        return get

    from .kite import KiteSpotClient
    from .upstox import UpstoxClient
    up = lazy("upstox", upstox_factory or UpstoxClient)
    kite = lazy("kite", kite_factory or KiteSpotClient)
    lines = [fetch_spot(store, ticker, interval, mode, dry_run, kite, today)]

    sym = f"{ticker}/{interval}"
    if store.has(LIB_FUTURES, sym) and mode == "missing":
        lo, hi = store.span(LIB_FUTURES, sym)
        n_c = len(store.symbols(LIB_CONTRACTS, f"{sym}/"))
        lines.append(f"panel  {sym}: already stored ({store.row_count(LIB_FUTURES, sym):,} bars, "
                     f"{lo.date()} -> {hi.date()}); {n_c} contracts cached")
    elif dry_run:
        n_c = len(store.symbols(LIB_CONTRACTS, f"{sym}/"))
        verb = {"missing": "missing -> would", "update": "would", "force": "would snapshot and"}[mode]
        lines.append(f"panel  {sym}: {verb} query Upstox for expired + live contracts "
                     f"({n_c} cached) and {'rebuild' if mode != 'update' else 'extend'} the panel")
    else:
        if mode == "force":
            name = store.snapshot([LIB_FUTURES, LIB_CONTRACTS], Store.snapshot_name("fetch_ticker"))
            lines.append(snapshot_hint(name, [LIB_FUTURES, LIB_CONTRACTS]))
        lines += [f"contract {n}" for n in fetch_contracts(store, ticker, interval, mode, up, today)]
        lines.append(build_or_extend_panel(store, ticker, interval, extend=(mode == "update")))
    calls = sum(getattr(c, "calls", 0) for c in clients.values())
    lines.append(f"API calls: {calls}")
    return {"ticker": ticker, "interval": interval, "lines": lines, "api_calls": calls}


# ── step 2: borrow rates ──────────────────────────────────────────────────────

def _write_summary(store: Store, ticker: str, interval: str) -> None:
    joined = store.read_borrow(ticker, interval)
    panel = store.read(LIB_FUTURES, f"{ticker}/{interval}", columns=["F1_expiry"])
    src = joined.join(panel, how="left")
    store.write(LIB_BORROW, f"{ticker}/{interval}/cycle_summary", borrow.cycle_summary(src, ticker, interval))


def build_borrow(store: Store, ticker: str, interval: str, *, mode: str = "missing", dry_run: bool = False) -> list[str]:
    sym = f"{ticker}/{interval}"
    if not store.has(LIB_FUTURES, sym):
        return [f"borrow {sym}: no futures panel - run fetch_ticker first"]
    exists = store.has(LIB_BORROW, sym)
    if exists and mode == "missing":
        lo, hi = store.span(LIB_BORROW, sym)
        return [f"borrow {sym}: already stored ({store.row_count(LIB_BORROW, sym):,} rows, {lo.date()} -> {hi.date()})"]
    if not exists or mode == "force":
        if dry_run:
            return [f"borrow {sym}: would {'snapshot and ' if exists else ''}compute all rows"]
        out = []
        if exists:
            name = store.snapshot([LIB_BORROW], Store.snapshot_name("build_borrow_rates"))
            out += [snapshot_hint(name, [LIB_BORROW]),
                    "feature columns are dropped by a full rebuild - re-run build_features"]
        comp = borrow.compute(store.read_panel(ticker, interval), interval)
        store.write(LIB_BORROW, sym, comp, metadata={"built_by": "borrowcycle", "spot_source": "kite spot"})
        _write_summary(store, ticker, interval)
        return out + [f"borrow {sym}: wrote {len(comp):,} rows; spot-based columns NaN on "
                      f"{int(comp['b1'].isna().sum()):,} bars (no Kite spot yet)"]
    # update: recompute in memory; write from the first row that differs (new panel
    # bars, or bars whose spot was missing before and Kite now covers)
    full = borrow.compute(store.read_panel(ticker, interval), interval)
    cols = borrow.computed_columns(interval)
    old = store.read(LIB_BORROW, sym, columns=cols).reindex(full.index)
    changed = ~np.isclose(old.to_numpy(float), full[cols].to_numpy(float),
                          rtol=1e-9, atol=1e-12, equal_nan=True).all(axis=1)
    if not changed.any():
        return [f"borrow {sym}: up to date"]
    start = full.index[changed.argmax()].normalize()       # whole day: dte_star ranks bars per day
    if dry_run:
        return [f"borrow {sym}: would recompute {int((full.index >= start).sum()):,} rows from {start.date()}"]
    comp = full[full.index >= start]
    keep = store.read(LIB_BORROW, sym, date_range=(start, None))
    for c in keep.columns:                   # keep stored feature values; build_features refreshes them
        if c not in comp.columns:
            comp[c] = keep[c].reindex(comp.index)
    store.upsert(LIB_BORROW, sym, comp)
    _write_summary(store, ticker, interval)
    return [f"borrow {sym}: updated {len(comp):,} rows from {start.date()}"]


# ── step 3: features ──────────────────────────────────────────────────────────

def build_features(store: Store, ticker: str, interval: str, *, mode: str = "missing",
                   dry_run: bool = False) -> list[str]:
    sym = f"{ticker}/{interval}"
    if not store.has(LIB_BORROW, sym):
        return [f"features {sym}: no borrow_rates - run build_borrow_rates first"]
    joined = store.read_borrow(ticker, interval)
    expiry = store.read(LIB_FUTURES, sym, columns=["cycle_id", "F1_expiry"]).groupby("cycle_id")["F1_expiry"].first()
    new = features.compute(store, ticker, interval, joined, expiry)
    stored_cols = store.columns(LIB_BORROW, sym)
    stored_feats = [c for c in stored_cols if c.startswith("feat_")]
    iv_note = "" if new["feat_atm_iv"].notna().any() else " (no option chains: IV features NaN)"
    if mode == "force" or set(stored_feats) != set(features.FEATURES):
        if dry_run:
            return [f"features {sym}: would write all {len(new):,} rows{iv_note}"]
        out = []
        if mode == "force" and stored_feats:
            name = store.snapshot([LIB_BORROW], Store.snapshot_name("build_features"))
            out.append(snapshot_hint(name, [LIB_BORROW]))
        base = store.read(LIB_BORROW, sym)
        base = base[[c for c in base.columns if not c.startswith("feat_")]].join(new)
        store.write(LIB_BORROW, sym, base, metadata={**store.metadata(LIB_BORROW, sym),
                                                     "features": features.FEATURES})
        return out + [f"features {sym}: wrote {len(new):,} rows x {len(features.FEATURES)} features{iv_note}"]
    old = store.read(LIB_BORROW, sym, columns=features.FEATURES).reindex(new.index)
    changed = ~np.isclose(old.to_numpy(float), new.to_numpy(float), rtol=1e-9, atol=1e-12, equal_nan=True).all(axis=1)
    if not changed.any():
        return [f"features {sym}: up to date ({len(new):,} rows){iv_note}"]
    first = new.index[changed.argmax()]
    if dry_run:
        return [f"features {sym}: would update {int((new.index >= first).sum()):,} rows from {first}"]
    rows = store.read(LIB_BORROW, sym, date_range=(first, None))
    rows[features.FEATURES] = new.loc[new.index >= first, features.FEATURES]
    store.upsert(LIB_BORROW, sym, rows)
    return [f"features {sym}: updated {len(rows):,} rows from {first}{iv_note}"]
