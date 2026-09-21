"""Summary statistics for per-cycle PnL and cycle-anatomy measurements."""
from __future__ import annotations

import numpy as np
from scipy import stats as sps


def _clean(a) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    return a[np.isfinite(a)]


def win_rate(a) -> float:
    """Percentage of cycles with a positive result."""
    v = _clean(a)
    return float((v > 0).mean() * 100) if len(v) else np.nan


def sharpe(a) -> float:
    """Per-cycle mean/std.

    This is NOT annualised and NOT capital-adjusted: there is no capital base
    in this study. It compares dispersion across cycles, nothing more.
    """
    v = _clean(a)
    return float(v.mean() / v.std()) if len(v) >= 2 and v.std() > 0 else np.nan


def max_dd(a) -> float:
    """Largest peak-to-trough drop of the cumulative per-cycle PnL."""
    v = _clean(a)
    if not len(v):
        return np.nan
    eq = np.cumsum(v)
    return float(np.min(eq - np.maximum.accumulate(eq)))


def bootstrap_ci(a, n: int = 10_000, alpha: float = 0.05, seed: int = 42):
    """Percentile bootstrap CI for the mean."""
    v = _clean(a)
    if len(v) < 3:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    means = rng.choice(v, size=(n, len(v)), replace=True).mean(axis=1)
    return (float(np.percentile(means, 100 * alpha / 2)),
            float(np.percentile(means, 100 * (1 - alpha / 2))))


def sign_test(a) -> float:
    """Two-sided binomial p-value that the median is zero."""
    v = _clean(a)
    if not len(v):
        return np.nan
    pos, tot = int((v > 0).sum()), int((v != 0).sum())
    if tot == 0:
        return np.nan
    return float(sps.binomtest(pos, tot, 0.5).pvalue)


def binom_p(successes: int, n: int, p: float = 0.5) -> float:
    """Two-sided binomial p-value against a null rate."""
    return float(sps.binomtest(successes, n, p).pvalue) if n else np.nan
