"""build_borrow_rates.py — implied borrow rates from the stored F1/F2/F3 panel and Kite spot.

Writes the computed columns to borrow_rates/{TICKER}/{interval} plus its cycle_summary.
See README.md in this folder.

    python pipeline/build_borrow_rates/build_borrow_rates.py --dry-run
    python pipeline/build_borrow_rates/build_borrow_rates.py KAYNES --intervals 15minute 1day
    python pipeline/build_borrow_rates/build_borrow_rates.py SWIGGY --update
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _cli  # noqa: E402

from borrowcycle.pipeline.run import build_borrow  # noqa: E402
from borrowcycle.pipeline.store import Store  # noqa: E402


def main(argv=None) -> int:
    args = _cli.parser(__doc__.splitlines()[0], tickers_required=False).parse_args(argv)
    _cli.setup_logging()
    store = Store()
    for t in [t.upper() for t in args.tickers] or _cli.RESEARCH_TICKERS:
        for iv in args.intervals:
            for line in build_borrow(store, t, iv, mode=_cli.mode(args), dry_run=args.dry_run):
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
