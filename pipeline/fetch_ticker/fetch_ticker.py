"""fetch_ticker.py — load futures (and missing Kite spot) for a ticker into the engine ArcticDB.

Checks what is already stored for each interval and fetches only what is missing.
See README.md in this folder.

    python pipeline/fetch_ticker/fetch_ticker.py RVNL --intervals 15minute 1day --dry-run
    python pipeline/fetch_ticker/fetch_ticker.py SWIGGY --update
    python pipeline/fetch_ticker/fetch_ticker.py NEWCO --intervals 15minute 1day
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _cli  # noqa: E402  (puts the repo root on sys.path)

from borrowcycle.pipeline.run import fetch_ticker  # noqa: E402
from borrowcycle.pipeline.store import Store  # noqa: E402


def main(argv=None) -> int:
    args = _cli.parser(__doc__.splitlines()[0], tickers_required=True).parse_args(argv)
    _cli.setup_logging()
    store = Store()
    for t in args.tickers:
        for iv in args.intervals:
            res = fetch_ticker(store, t.upper(), iv, mode=_cli.mode(args), dry_run=args.dry_run)
            print(f"\n== {t.upper()} {iv}")
            for line in res["lines"]:
                print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
