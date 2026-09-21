"""
Cycle anatomy — locating BUILD, PEAK and COLLAPSE in the F1-F2 futures spread.

The borrow premium in a hard-to-borrow name follows the same shape every
expiry month: the front-month future drifts rich against the next month
(BUILD), tops out (PEAK), then converges mechanically into expiry (COLLAPSE).
This module turns that description into three timestamps per cycle so the
shape can be measured rather than asserted.

Two detector variants are provided:

``legacy=True``  reproduces the original rule, which searched only inside the
expiry week (``DTE <= 7``). That rule is *censored*: it reports the window
edge as the build moment for most cycles (p25 = median = p75 = 7.0), and
widening the window just moves the artefact. It is kept so the bias can be
shown rather than quietly corrected -- see docs/02_cycle_anatomy.md.

``legacy=False`` (default) searches the whole cycle and is what every
published statistic uses.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

#: Centred rolling window used by the original research, in 15-minute bars.
SMOOTH = 11

#: A cycle counts as hard-to-borrow when smoothed b12 peaks at or above this
#: (5% annualised). Note this filter is close to non-binding -- the 365/dte
#: term inflates b12 near expiry, so ~91% of cycles clear it. The economically
#: meaningful cut is the tier split below.
HTB_THRESH = 0.05

#: Bars a slope/acceleration condition must hold to count as a real turn.
#: The original 1-bar rule fired a median of 1 bar after the peak, which made
#: the collapse moment indistinguishable from the peak itself.
MIN_SUSTAIN = 4

TIER_EDGES = (0.05, 0.15)
TIERS = ("NON", "MOD", "EXT")


def smooth(s: pd.Series, w: int = SMOOTH, center: bool = True) -> np.ndarray:
    """Rolling mean of a forward-filled series.

    ``center=True`` matches the original research but looks (w-1)//2 bars into
    the future. That is acceptable for *describing* a cycle after the fact,
    and is look-ahead bias when used to *time an entry* -- the backtest
    therefore runs both variants. See docs/05_limitations.md.
    """
    return s.ffill().rolling(w, center=center, min_periods=1).mean().to_numpy(float)


def spread(cyc: pd.DataFrame) -> pd.Series:
    """The traded series: front-month minus next-month futures close."""
    return cyc["F1_close"].ffill() - cyc["F2_close"].ffill()


def peak_b12(cyc: pd.DataFrame, w: int = SMOOTH) -> float:
    """Maximum smoothed annualised borrow rate in the cycle (the HTB filter)."""
    return float(np.nanmax(smooth(cyc["b12"], w)))


def htb_tier(pk: float) -> str:
    """Bucket a cycle by borrow premium: NON <5%, MOD 5-15%, EXT >=15%."""
    if not np.isfinite(pk):
        return "NON"
    lo, hi = TIER_EDGES
    return "NON" if pk < lo else ("MOD" if pk < hi else "EXT")


def _first_sustained(mask: np.ndarray, start: int, n: int) -> int | None:
    """First index >= start where ``mask`` is True for ``n`` consecutive bars."""
    run = 0
    for i in range(start, len(mask)):
        run = run + 1 if mask[i] else 0
        if run >= n:
            return i - n + 1
    return None


@dataclass
class CycleMoments:
    """The three moments of one expiry cycle, plus the flags needed to judge them."""
    ticker: str
    cid: int
    n_bars: int
    i_build: int
    i_peak: int
    i_collapse: int | None
    i_collapse25: int | None
    build_censored: bool      # trough sits at the cycle edge -> not a real turn
    build_confirmed: bool     # sustained positive slope follows the trough
    peak_in_expiry_week: bool
    phases_ordered: bool
    gap_flag: bool            # fewer than 5 bars inside the expiry week

    def as_dict(self) -> dict:
        return asdict(self)


def detect_moments(
    cyc: pd.DataFrame,
    ticker: str = "",
    cid: int = 0,
    w: int = SMOOTH,
    min_sustain: int = MIN_SUSTAIN,
    center: bool = True,
    legacy: bool = False,
) -> CycleMoments | None:
    """Locate BUILD, PEAK and COLLAPSE on the smoothed spread.

    PEAK      argmax of the smoothed spread.
    BUILD     argmin of the smoothed spread *before* the peak -- the last
              trough the premium built from.
    COLLAPSE  first bar after the peak where slope < 0 and acceleration < 0
              hold for ``min_sustain`` consecutive bars.

    ``i_collapse25`` is an independent, amplitude-based alternative: the first
    post-peak bar giving back >=25% of (peak - build). Agreement between the
    two definitions is reported as a robustness check.

    Returns None when the cycle is too short or the spread is unusable.
    """
    if len(cyc) < 10:
        return None

    sm = smooth(spread(cyc), w, center=center)
    if not np.isfinite(sm).any():
        return None

    dte = cyc["F1_dte"].to_numpy(float) if "F1_dte" in cyc.columns else np.full(len(cyc), np.nan)
    in_week = np.isfinite(dte) & (dte <= 7)

    if legacy:
        # Original rule: search only inside the expiry week. Retained to
        # demonstrate the censoring artefact, not used for published numbers.
        if not in_week.any():
            return None
        idx = np.where(in_week)[0]
        lo, hi = int(idx[0]), int(idx[-1]) + 1
        seg = sm[lo:hi]
        if not np.isfinite(seg).any():
            return None
        i_peak = lo + int(np.nanargmax(seg))
        slope_seg = np.diff(seg, prepend=seg[0])
        j = _first_sustained(slope_seg > 0, 0, 2)
        i_build = lo + (j if j is not None else int(np.nanargmin(seg)))
    else:
        i_peak = int(np.nanargmax(sm))
        pre = sm[:i_peak] if i_peak > 0 else sm[:1]
        i_build = int(np.nanargmin(pre)) if np.isfinite(pre).any() else 0

    slope = np.diff(sm, prepend=sm[0])
    accel = np.diff(slope, prepend=slope[0])
    turn = np.isfinite(slope) & np.isfinite(accel) & (slope < 0) & (accel < 0)
    i_collapse = _first_sustained(turn, i_peak + 1, min_sustain)

    amplitude = sm[i_peak] - sm[i_build]
    i_collapse25 = None
    if np.isfinite(amplitude) and amplitude > 1e-9:
        thresh = sm[i_peak] - 0.25 * amplitude
        below = np.zeros(len(sm), dtype=bool)
        below[i_peak + 1:] = sm[i_peak + 1:] <= thresh
        hit = np.where(below)[0]
        i_collapse25 = int(hit[0]) if len(hit) else None

    return CycleMoments(
        ticker=ticker,
        cid=int(cid),
        n_bars=len(cyc),
        i_build=i_build,
        i_peak=i_peak,
        i_collapse=i_collapse,
        i_collapse25=i_collapse25,
        build_censored=bool(i_build <= 1),
        build_confirmed=bool(
            _first_sustained(slope > 0, i_build, min_sustain) is not None
            and i_build < i_peak
        ),
        peak_in_expiry_week=bool(in_week[i_peak]) if len(in_week) > i_peak else False,
        phases_ordered=bool(
            i_build < i_peak and (i_collapse is None or i_peak < i_collapse)
        ),
        gap_flag=bool(in_week.sum() < 5),
    )
