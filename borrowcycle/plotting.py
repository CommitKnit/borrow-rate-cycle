"""
House style for every regenerated figure.

Two fixes live here that the original research scripts each got wrong in
their own way:

1. ``bar_axis`` — intraday series were plotted against a DatetimeIndex, so the
   17.5-hour overnight gap and weekends were drawn as long straight diagonals
   across the panel. Plotting against bar position removes them.

2. ``annotate_no_overlap`` — callouts used fixed pixel offsets, so when entry
   and exit were close together the boxes collided with each other and with
   the legend. This places them greedily and draws a leader line back.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from . import data as bd

FIG_DIR = bd.PKG_ROOT / "figures"

C_SPREAD = "#e67e22"
C_B12    = "#2c3e50"
C_F1OI   = "#c0392b"
C_F2OI   = "#2980b9"
C_BUILD  = "#27ae60"
C_PEAK   = "#e67e22"
C_COLL   = "#c0392b"
C_ENTRY  = "#27ae60"
C_EXIT   = "#c0392b"

TIER_COLORS = {"NON": "#9e9e9e", "MOD": "#1f77b4", "EXT": "#d62728"}
TIER_LABELS = {"NON": "non-HTB (<5%)", "MOD": "moderate (5-15%)", "EXT": "extreme (>=15%)"}

TICKER_COLORS = {
    "SBICARD": "#e74c3c", "RVNL": "#3498db", "KPITTECH": "#2ecc71",
    "ASTRAL": "#9b59b6", "BDL": "#f39c12", "IREDA": "#1abc9c",
    "VOLTAS": "#34495e",
}

STYLE = {
    "figure.dpi": 110,
    "savefig.dpi": 150,
    "savefig.bbox": "tight",
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "semibold",
    "axes.labelsize": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.18,
    "grid.linewidth": 0.7,
    "legend.frameon": True,
    "legend.framealpha": 0.9,
    "legend.fontsize": 9,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
}


def use_style() -> None:
    plt.rcParams.update(STYLE)


def bar_axis(ax, index: pd.DatetimeIndex, fmt: str = "%d %b", max_ticks: int = 9):
    """Label an integer bar axis with dates, so session gaps do not draw.

    Returns the x positions to plot against.
    """
    x = np.arange(len(index))

    def _fmt(val, _pos):
        i = int(round(val))
        if i < 0 or i >= len(index):
            return ""
        return index[i].strftime(fmt)

    ax.xaxis.set_major_formatter(FuncFormatter(_fmt))
    step = max(1, len(index) // max_ticks)
    ax.set_xticks(x[::step])
    for lab in ax.get_xticklabels():
        lab.set_rotation(20)
        lab.set_ha("right")

    # Faint rule at each session boundary.
    days = pd.Series(index.date, index=range(len(index)))
    bounds = np.where(days.values[1:] != days.values[:-1])[0] + 1
    for b in bounds:
        ax.axvline(b, color="#000000", alpha=0.045, lw=0.7, zorder=0)
    return x


def annotate_no_overlap(ax, items, fontsize: int = 8):
    """Place callout boxes so they never overlap each other or the legend.

    ``items`` is a sequence of (x, y, text, color) in data coordinates.
    """
    fig = ax.figure
    fig.canvas.draw()
    placed = []
    leg = ax.get_legend()
    if leg is not None:
        try:
            placed.append(leg.get_window_extent())
        except Exception:
            pass

    for x, y, text, color in items:
        for dx, dy in [(10, 26), (10, -46), (-95, 26), (-95, -46),
                       (10, 66), (-95, 66), (10, -86), (-95, -86)]:
            ann = ax.annotate(
                text, xy=(x, y), xytext=(dx, dy), textcoords="offset points",
                fontsize=fontsize, color=color, ha="left", va="center", zorder=8,
                bbox=dict(boxstyle="round,pad=0.32", fc="white", ec=color, alpha=0.92),
                arrowprops=dict(arrowstyle="-", color=color, lw=0.8, alpha=0.7),
            )
            fig.canvas.draw()
            bb = ann.get_window_extent()
            if not any(bb.overlaps(p) for p in placed):
                placed.append(bb)
                break
            ann.remove()
        else:
            # Every candidate slot collided; keep the last position anyway.
            ann = ax.annotate(
                text, xy=(x, y), xytext=(10, 26), textcoords="offset points",
                fontsize=fontsize, color=color, zorder=8,
                bbox=dict(boxstyle="round,pad=0.32", fc="white", ec=color, alpha=0.92),
            )
            placed.append(ann.get_window_extent())


def save(fig, name: str, out_dir: Path | None = None) -> Path:
    """Save a figure into figures/ and close it."""
    out = (out_dir or FIG_DIR)
    out.mkdir(parents=True, exist_ok=True)
    path = out / (name if name.endswith(".png") else f"{name}.png")
    fig.savefig(path)
    plt.close(fig)
    return path
