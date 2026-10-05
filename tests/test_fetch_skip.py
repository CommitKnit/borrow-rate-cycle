"""fetch_ticker: what is stored is never fetched again; updates append; force snapshots."""
from datetime import date, timedelta

from borrowcycle.pipeline.run import build_borrow, fetch_ticker
from borrowcycle.pipeline.store import LIB_BORROW, LIB_CONTRACTS, LIB_FUTURES, LIB_SPOT


def _run(store, fakes, **kw):
    up, kite, today = fakes
    return fetch_ticker(store, "T", "1day", upstox_factory=lambda: up, kite_factory=lambda: kite,
                        today=kw.pop("today", today), **kw)


def test_first_run_fetches_then_everything_is_skipped(store, fakes):
    up, kite, _ = fakes
    res = _run(store, fakes)
    assert res["api_calls"] > 0
    assert store.has(LIB_SPOT, "T/day") and store.has(LIB_FUTURES, "T/1day")
    assert len(store.symbols(LIB_CONTRACTS, "T/1day/")) == 5
    up.calls = kite.calls = 0
    again = _run(store, fakes)
    assert again["api_calls"] == 0 and up.calls == 0 and kite.calls == 0
    assert any("already stored" in l for l in again["lines"])


def test_spot_present_means_no_kite_call(store, fakes):
    up, kite, today = fakes
    kite_df = kite.candles(1, "1day", today - timedelta(days=400), today)
    store.write(LIB_SPOT, "T/day", kite_df)
    kite.calls = 0
    _run(store, fakes)
    assert kite.calls == 0


def test_cached_expired_contracts_are_not_refetched(store, fakes):
    up, _, _ = fakes
    _run(store, fakes)
    store.lib(LIB_FUTURES).delete("T/1day")          # panel gone, contracts cached
    up.fetch_log.clear()
    _run(store, fakes)
    assert not [f for f in up.fetch_log if f[0] == "expired"]
    assert store.has(LIB_FUTURES, "T/1day")


def test_update_appends_without_full_copy(store, fakes):
    up, _, today = fakes
    _run(store, fakes)
    rows0 = store.row_count(LIB_FUTURES, "T/1day")
    later = today + timedelta(days=7)
    up.fetch_log.clear()
    res = _run(store, fakes, mode="update", today=later)
    v3 = [f for f in up.fetch_log if f[0] == "v3"]
    assert v3 and all(f[2] >= today - timedelta(days=4) for f in v3)   # only from the last cached bar
    assert store.row_count(LIB_FUTURES, "T/1day") > rows0
    assert store.versions(LIB_FUTURES, "T/1day") == 1
    panel = store.read(LIB_FUTURES, "T/1day")
    assert panel["cycle_id"].is_monotonic_increasing
    assert any("extended" in l for l in res["lines"])


def test_force_snapshots_first(store, fakes):
    _run(store, fakes)
    res = _run(store, fakes, mode="force")
    snaps = store.lib(LIB_FUTURES).list_snapshots()
    assert any(s.startswith("borrowcycle_pre_fetch_ticker_") for s in snaps)
    assert any("Delete it once checked" in l for l in res["lines"])


def test_dry_run_writes_nothing(store, fakes):
    up, kite, _ = fakes
    res = _run(store, fakes, dry_run=True)
    assert res["api_calls"] == 0 and not store.has(LIB_FUTURES, "T/1day") and not store.has(LIB_SPOT, "T/day")


def test_borrow_after_fetch_stores_only_computed_columns(store, fakes):
    _run(store, fakes)
    build_borrow(store, "T", "1day")
    cols = store.columns(LIB_BORROW, "T/1day")
    assert "b12" in cols and "F1_close" not in cols and "SPOT_close" not in cols
    assert store.has(LIB_BORROW, "T/1day/cycle_summary")
    assert "up to date" in build_borrow(store, "T", "1day", mode="update")[0]
