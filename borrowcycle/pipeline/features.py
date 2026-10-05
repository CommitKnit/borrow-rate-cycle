"""Feature engineering on the joined borrow panel.

Ported from backtesting-engine/scripts/feature_builder.py (current version: the two
features that used to pool the whole history now use earlier cycles only, and
feat_roll_fraction was removed). Two changes for this repo:

* Windows are defined in trading days and converted to bars per interval, so any
  interval can be built (the engine version hard-codes 15-minute bar counts).
* The implied-vol group is optional: NaN, with one log line, when there is no
  enriched option chain; both known options column layouts are accepted.

Every feature uses only data up to its own bar (within-cycle diffs, earlier-cycle
statistics, trailing windows), so recomputing never changes a stored row.

Inputs come from ``Store.read_borrow`` (computed borrow columns + panel inputs +
Kite SPOT_close). Only the ``feat_*`` columns are stored; F1_volume/F2_volume are
read from the futures panel.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config as cfg

log = logging.getLogger(__name__)

DTE_FLOOR = 0.04            # expiry-day guard on the DTE denominator (~1 hour)
DELTA_TARGETS = {
    "feat_otm_25d_call_iv": ("CE", +0.25), "feat_otm_15d_call_iv": ("CE", +0.15),
    "feat_otm_25d_put_iv": ("PE", -0.25), "feat_otm_15d_put_iv": ("PE", -0.15),
}
OI_FEATURES = ["feat_oi_concentration", "feat_roll_velocity", "feat_roll_acceleration",
               "feat_relative_oi_change", "feat_net_roll_flow", "feat_flow_intensity",
               "feat_roll_deviation"]
VOLUME_FEATURES = ["feat_Vshare", "feat_volflow"]
IV_FEATURES = ["feat_atm_iv"] + list(DELTA_TARGETS)
DERIVED_FEATURES = ["feat_b12_percentile", "feat_25d_risk_reversal", "feat_beta_regression",
                    "feat_r2_regression", "feat_beta_tsat", "feat_beta_rolling", "feat_r2_rolling",
                    "feat_beta_reversal", "feat_b12_slope_1d", "feat_b12_accel", "feat_residual_atm_iv"]
#: The 25 stored feature columns, in stored order.
FEATURES = OI_FEATURES + VOLUME_FEATURES + IV_FEATURES + DERIVED_FEATURES
IV_DEPENDENT = IV_FEATURES + ["feat_25d_risk_reversal", "feat_residual_atm_iv"]


class Windows:
    """Window lengths in bars for an interval (15-minute values = the engine's)."""

    def __init__(self, interval: str):
        bpd = cfg.BARS_PER_DAY[interval]
        self.rolling = max(10 * bpd, 10)                 # 10 trading days (250 bars at 15m)
        self.rolling_min = max(3, round(0.08 * self.rolling))   # 20 at 15m
        self.slope = max(bpd, 5)                          # 1 trading day (25 at 15m); >=5 bars
        self.slope_min = max(3, round(0.4 * self.slope))  # 10 at 15m
        self.accel_lag = max(1, round(0.2 * bpd))         # 75 minutes (5 bars at 15m)


# ── earlier-cycle statistics (look-ahead-free) ───────────────────────────────

def _cycle_order(df: pd.DataFrame) -> pd.Series:
    first_ts = pd.Series(df.index, index=df.index).groupby(df["cycle_id"]).min()
    return df["cycle_id"].map(first_ts.rank(method="first").astype(int) - 1)


def prior_cycle_dte_mean(df: pd.DataFrame, col: str) -> pd.Series:
    """Bar-weighted mean of ``col`` over EARLIER cycles at the same integer F1_dte."""
    tmp = pd.DataFrame({"v": df[col].astype("float64"), "c": _cycle_order(df),
                        "d": df["F1_dte"].round(0)}, index=df.index).dropna(subset=["v", "d"])
    agg = tmp.groupby(["d", "c"])["v"].agg(["sum", "count"])
    prior = agg.groupby(level="d").cumsum() - agg
    mean = prior["sum"] / prior["count"].replace(0, np.nan)
    keys = pd.MultiIndex.from_arrays([df["F1_dte"].round(0), _cycle_order(df)])
    return pd.Series(mean.reindex(keys).to_numpy(), index=df.index, dtype="float64")


def prior_cycle_dte_percentile(df: pd.DataFrame, col: str) -> pd.Series:
    """Percentile (0–100, ties half) of ``col`` among EARLIER cycles at the same F1_dte."""
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    tmp = pd.DataFrame({"v": df[col].astype("float64"), "c": _cycle_order(df),
                        "d": df["F1_dte"].round(0)}, index=df.index).dropna(subset=["v", "d"])
    for _d, grp in tmp.groupby("d", sort=False):
        hist = np.empty(0)
        for _c, cyc in grp.groupby("c", sort=True):
            vals = cyc["v"].to_numpy()
            if hist.size:
                lo, hi = np.searchsorted(hist, vals, "left"), np.searchsorted(hist, vals, "right")
                out.loc[cyc.index] = (lo + hi) / 2.0 / hist.size * 100.0
            hist = np.sort(np.concatenate([hist, vals]))
    return out


def prior_cycle_dte_median_residual(df: pd.DataFrame, col: str) -> pd.Series:
    """``col`` minus the median of ``col`` over EARLIER cycles at the same F1_dte."""
    dte = df["F1_dte"].round(0).astype(int)
    tmp = df.assign(_d=dte, _c=_cycle_order(df)).dropna(subset=[col])
    lists = tmp.groupby(["_c", "_d"])[col].agg(list)
    med: dict = {}
    for d in sorted(dte.unique()):
        try:
            per_cycle = lists.xs(int(d), level="_d").sort_index()
        except KeyError:
            continue
        running: list = []
        for c, vals in per_cycle.items():
            med[(int(c), int(d))] = float(np.nanmedian(running)) if running else np.nan
            running.extend(v for v in vals if not np.isnan(float(v)))
    order = _cycle_order(df)
    hist = pd.Series([med.get((int(c), int(d)), np.nan) for c, d in zip(order.values, dte.values)],
                     index=df.index, dtype="float64")
    return (df[col] - hist).astype("float64")


# ── groups ────────────────────────────────────────────────────────────────────

def oi_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("cycle_id", sort=False)
    f1, f2 = df["F1_oi"].astype("float64"), df["F2_oi"].astype("float64")
    valid = (f1 > 0) & (f2 > 0)
    out = pd.DataFrame(index=df.index)
    out["feat_oi_concentration"] = (f1 / (f1 + f2)).where(valid)
    out["feat_roll_velocity"] = out["feat_oi_concentration"].groupby(df["cycle_id"], sort=False).diff()
    out["feat_roll_acceleration"] = out["feat_roll_velocity"].groupby(df["cycle_id"], sort=False).diff()
    d1, d2 = g["F1_oi"].diff(), g["F2_oi"].diff()
    p1 = g["F1_oi"].shift(1).astype("float64").replace(0, np.nan)
    p2 = g["F2_oi"].shift(1).astype("float64").replace(0, np.nan)
    out["feat_relative_oi_change"] = (d2 / p2 - d1 / p1).where(valid)
    nrf = d2 - d1
    out["feat_net_roll_flow"] = nrf.where(valid)
    rf = (nrf / (f1 + f2).replace(0, np.nan)).where(valid)          # intermediate only
    out["feat_flow_intensity"] = rf / df["F1_dte_star"].astype("float64").clip(lower=DTE_FLOOR)
    tmp = df.assign(feat_oi_concentration=out["feat_oi_concentration"])
    out["feat_roll_deviation"] = out["feat_oi_concentration"] - prior_cycle_dte_mean(tmp, "feat_oi_concentration")
    return out


def volume_features(df: pd.DataFrame, net_roll_flow: pd.Series) -> pd.DataFrame:
    v1, v2 = df["F1_volume"].astype("float64"), df["F2_volume"].astype("float64")
    ok = (v1 > 0) & (v2 > 0)
    vshare = (v2 / (v1 + v2)).where(ok)
    return pd.DataFrame({"feat_Vshare": vshare, "feat_volflow": (net_roll_flow * vshare).where(ok)},
                        index=df.index)


def _atm_iv(opt: pd.DataFrame) -> pd.Series:
    o = opt.dropna(subset=["iv", "strike", "futures_ref_price"]).reset_index()
    if o.empty:
        return pd.Series(dtype="float64")
    ts = o.columns[0]
    o["sdist"] = (o["strike"] - o["futures_ref_price"]).abs()
    atm = o.loc[o.groupby(ts)["sdist"].idxmin()].set_index(ts)["strike"]
    o = o.merge(atm.rename("atm_strike"), left_on=ts, right_index=True)
    return o[np.isclose(o["strike"], o["atm_strike"])].groupby(ts)["iv"].mean()


def _delta_iv(opt: pd.DataFrame, opt_type: str, target: float) -> pd.Series:
    o = opt[opt["opt_type"] == opt_type].dropna(subset=["delta", "iv"]).reset_index()
    if o.empty:
        return pd.Series(dtype="float64")
    ts = o.columns[0]
    o["ddist"] = (o["delta"] - target).abs()
    return o.loc[o.groupby(ts)["ddist"].idxmin()].set_index(ts)["iv"]


def iv_features(store, ticker: str, interval: str, df: pd.DataFrame,
                cycle_expiry: pd.Series) -> pd.DataFrame:
    """Front-month (F1) chain IV per bar; NaN where no chain exists for the cycle's expiry."""
    out = pd.DataFrame(np.nan, index=df.index, columns=IV_FEATURES)
    available = set(store.option_expiries(ticker, interval))
    used = 0
    for cid, expiry in cycle_expiry.items():
        if not isinstance(expiry, str) or expiry not in available:
            continue
        opt = store.read_options(ticker, interval, expiry)
        if "slot" in opt.columns:
            opt = opt[opt["slot"] == "F1"]
        opt = opt[opt.index.isin(df.index[df["cycle_id"] == cid])]
        if opt.empty:
            continue
        used += 1
        atm = _atm_iv(opt)
        out.loc[atm.index, "feat_atm_iv"] = atm.values
        for col, (otype, target) in DELTA_TARGETS.items():
            s = _delta_iv(opt, otype, target)
            if not s.empty:
                out.loc[s.index, col] = s.values
    if not used:
        log.info("%s/%s: no enriched option chains for any cycle -> IV features NaN", ticker, interval)
    return out


def _ols(n, sx, sy, sxx, sxy, syy, min_n):
    db = n * sxx - sx ** 2
    nb = n * sxy - sx * sy
    dy = n * syy - sy ** 2
    enough = (n >= min_n).values
    ok_b = enough & (np.abs(db.values) > 1e-10)
    ok_r = ok_b & (np.abs((db * dy).values) > 1e-10)
    resid = db * dy - nb ** 2
    ok_t = (n >= max(min_n, 3)).values & (np.abs(db.values) > 1e-10) & (resid.values > 1e-12)
    with np.errstate(invalid="ignore", divide="ignore"):
        beta = np.where(ok_b, nb.values / db.values, np.nan)
        r2 = np.where(ok_r, nb.values ** 2 / (db * dy).values, np.nan)
        t = np.where(ok_t, nb.values * np.sqrt(np.maximum(n.values - 2.0, 0.0)) / np.sqrt(resid.values), np.nan)
    return beta, r2, t


def derived_features(df: pd.DataFrame, iv: pd.DataFrame, w: Windows) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["feat_b12_percentile"] = prior_cycle_dte_percentile(df, "b12")
    out["feat_25d_risk_reversal"] = (iv["feat_otm_25d_call_iv"] - iv["feat_otm_25d_put_iv"]).astype("float64")

    # log(F1) regressed on dte_star. NOTE: dte_star falls steadily through a cycle, so
    # the slope is essentially the cycle-to-date price TREND (momentum), not carry.
    y, x = np.log(df["F1_close"].astype("float64")), df["F1_dte_star"].astype("float64")
    valid = y.notna() & x.notna()
    xf, yf, vf = x.where(valid, 0.0), y.where(valid, 0.0), valid.astype("float64")
    cg = df["cycle_id"]
    cs = lambda s: s.groupby(cg, sort=False).cumsum()
    b, r2, t = _ols(cs(vf), cs(xf), cs(yf), cs(xf ** 2), cs(xf * yf), cs(yf ** 2), min_n=3)
    out["feat_beta_regression"], out["feat_r2_regression"], out["feat_beta_tsat"] = b, r2, t
    roll = lambda s: s.rolling(w.rolling, min_periods=1).sum()
    b, r2, _ = _ols(roll(vf), roll(xf), roll(yf), roll(xf ** 2), roll(xf * yf), roll(yf ** 2), min_n=w.rolling_min)
    out["feat_beta_rolling"], out["feat_r2_rolling"] = b, r2
    out["feat_beta_reversal"] = out["feat_beta_rolling"] - out["feat_beta_regression"]

    slopes = []
    for _cid, cyc in df.groupby("cycle_id", sort=False):
        slopes.append(cyc["b12"].rolling(w.slope, min_periods=w.slope_min).apply(
            lambda v: np.polyfit(np.arange(len(v)), v, 1)[0], raw=True))
    slope = pd.concat(slopes).reindex(df.index).astype("float64") if slopes else pd.Series(np.nan, index=df.index)
    out["feat_b12_slope_1d"] = slope
    out["feat_b12_accel"] = slope.groupby(df["cycle_id"], sort=False).diff(w.accel_lag)
    out["feat_residual_atm_iv"] = prior_cycle_dte_median_residual(df.assign(feat_atm_iv=iv["feat_atm_iv"]),
                                                                  "feat_atm_iv")
    return out


def compute(store, ticker: str, interval: str, joined: pd.DataFrame, cycle_expiry: pd.Series) -> pd.DataFrame:
    """All 25 stored features for every row of ``joined`` (Store.read_borrow output)."""
    w = Windows(interval)
    oi = oi_features(joined)
    vol = volume_features(joined, oi["feat_net_roll_flow"])
    iv = iv_features(store, ticker, interval, joined, cycle_expiry)
    der = derived_features(joined, iv, w)
    return pd.concat([oi, vol, iv, der], axis=1)[FEATURES].astype("float64")
