"""Data pipeline: fetch futures (and missing spot) -> F1/F2/F3 panel -> borrow rates -> features.

Everything reads and writes the backtesting engine's ArcticDB store (see config.py),
in its slim layout: each value is stored exactly once, and readers join spot and
futures inputs back at read time (store.Store.read_panel / read_borrow).
"""
