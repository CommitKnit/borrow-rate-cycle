"""Shared CLI plumbing for the pipeline scripts (repo root on sys.path, mode flags)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The seven names the research covers (borrowcycle.data.TICKERS).
RESEARCH_TICKERS = ["SBICARD", "RVNL", "KPITTECH", "ASTRAL", "BDL", "IREDA", "VOLTAS"]


def parser(description: str, tickers_required: bool) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=description)
    if tickers_required:
        ap.add_argument("tickers", nargs="+", metavar="TICKER")
    else:
        ap.add_argument("tickers", nargs="*", metavar="TICKER",
                        help=f"default: the research universe {RESEARCH_TICKERS}")
    ap.add_argument("--intervals", nargs="+", default=["15minute"],
                    choices=["1day", "60minute", "30minute", "15minute", "5minute", "1minute"])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--update", action="store_true", help="extend existing data with new rows")
    g.add_argument("--force", action="store_true", help="snapshot, then rebuild from scratch")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen; no writes, no API calls")
    return ap


def mode(args) -> str:
    return "force" if args.force else "update" if args.update else "missing"


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
