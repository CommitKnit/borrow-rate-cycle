"""build_features.py — the 25 stored features on borrow_rates/{TICKER}/{interval}.

Recomputes every feature in memory and writes only rows that changed (new bars), so
re-running on up-to-date data writes nothing. See README.md in this folder.

    python pipeline/build_features/build_features.py --dry-run
    python pipeline/build_features/build_features.py KAYNES DIXON
    python pipeline/build_features/build_features.py RVNL --intervals 1day
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _cli  # noqa: E402

from borrowcycle.pipeline.run import build_features  # noqa: E402
from borrowcycle.pipeline.store import Store  # noqa: E402


def main(argv=None) -> int:
    args = _cli.parser(__doc__.splitlines()[0], tickers_required=False).parse_args(argv)
    _cli.setup_logging()
    store = Store()
    for t in [t.upper() for t in args.tickers] or _cli.RESEARCH_TICKERS:
        for iv in args.intervals:
            for line in build_features(store, t, iv, mode=_cli.mode(args), dry_run=args.dry_run):
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
