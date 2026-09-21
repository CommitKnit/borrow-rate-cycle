"""
00_export_data.py — build the shipped parquet dataset.

THIS SCRIPT IS NOT RUNNABLE FROM A CLONE OF THIS REPO.

It is the one script that touches the original ArcticDB store, and it is kept
for provenance: it documents exactly how everything in data/ was produced.
Every other script in this repo reads only the parquet files it writes.

Run it from the source research repo:

    python 00_export_data.py --src C:/Users/risha/backtesting-engine/backtesting-engine \
                             --dst C:/Users/risha/borrow-rate-research

Outputs
-------
  data/borrow_panel/{TICKER}.parquet      full 15-minute futures/borrow panel
  data/options_atm/{TICKER}_{expiry}.parquet
                                          ATM +/- 3 strikes, the only option
                                          data the backtest ever reads
  data/MANIFEST.json                      rows, cycles, span and sha256 per file
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

TICKERS = ["SBICARD", "RVNL", "KPITTECH", "ASTRAL", "BDL", "IREDA", "VOLTAS"]
OPTION_TICKERS = ["SBICARD", "RVNL"]
INTERVAL = "15minute"

#: Only these option columns are needed by the backtest.
OPT_COLS = ["strike", "opt_type", "close", "iv", "delta", "vega",
            "dte", "futures_ref_price", "expiry", "oi", "volume"]

#: Strikes either side of the money to keep. The backtest only ever selects
#: the single ATM strike, so +/-3 is a generous margin.
STRIKE_PAD = 3


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="source research repo root")
    ap.add_argument("--dst", required=True, help="this repo root")
    args = ap.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    sys.path.insert(0, str(src))
    from loguru import logger
    logger.remove()
    from data.storage.arctic_store import ArcticStore

    store = ArcticStore()
    panel_dir = dst / "data" / "borrow_panel"
    opt_dir = dst / "data" / "options_atm"
    panel_dir.mkdir(parents=True, exist_ok=True)
    opt_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"borrow_panel": {}, "options_atm": {}}

    # ---- borrow / futures panels --------------------------------------------
    for t in TICKERS:
        df = store.read_borrow_rates(t, INTERVAL).sort_index()
        path = panel_dir / f"{t}.parquet"
        df.to_parquet(path, compression="zstd")
        manifest["borrow_panel"][t] = {
            "rows": int(len(df)),
            "cols": int(df.shape[1]),
            "cycles": int(df["cycle_id"].nunique()) if "cycle_id" in df else None,
            "start": str(df.index.min()),
            "end": str(df.index.max()),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        print(f"panel  {t:9s} rows={len(df):6d} cols={df.shape[1]:3d} "
              f"{path.stat().st_size/1e6:5.2f} MB")

    # ---- ATM option extracts -------------------------------------------------
    for t in OPTION_TICKERS:
        for exp in store.list_option_expiries(t, INTERVAL):
            try:
                o = store.read_options(t, INTERVAL, exp)
            except Exception as exc:
                print(f"opts   {t} {exp}: unavailable ({exc})")
                continue
            if o.empty or "strike" not in o.columns:
                continue

            strikes = np.sort(o["strike"].dropna().unique())
            step = float(np.median(np.diff(strikes))) if len(strikes) > 1 else 0.0
            if step <= 0:
                continue

            ref = (o["futures_ref_price"] if "futures_ref_price" in o.columns
                   else pd.Series(np.nan, index=o.index))
            keep = (o["strike"] - ref).abs() <= STRIKE_PAD * step
            slim = o.loc[keep, [c for c in OPT_COLS if c in o.columns]].copy()
            if slim.empty:
                continue

            path = opt_dir / f"{t}_{exp}.parquet"
            slim.to_parquet(path, compression="zstd")
            manifest["options_atm"][f"{t}_{exp}"] = {
                "rows": int(len(slim)),
                "strike_step": step,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            print(f"opts   {t:9s} {exp}  rows={len(slim):6d} "
                  f"{path.stat().st_size/1e6:5.2f} MB")

    total = sum(v["bytes"] for v in manifest["borrow_panel"].values())
    total += sum(v["bytes"] for v in manifest["options_atm"].values())
    manifest["total_bytes"] = total
    manifest["generated_by"] = "scripts/00_export_data.py"
    (dst / "data" / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))

    print(f"\nTotal shipped data: {total/1e6:.1f} MB "
          f"({len(manifest['borrow_panel'])} panels, "
          f"{len(manifest['options_atm'])} option extracts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
