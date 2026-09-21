"""
spread_swing_bt.py
==================
Volatility swing strategy on the F1-F2 futures spread.
ASTRAL and KPITTECH only.  Futures only — no options.

Signal rules  (computed on raw F1-F2 spread, NOT b12)
------------------------------------------------------
  Smooth spread = 11-bar centred rolling mean of (F1_close - F2_close)
  slope = diff(smooth), accel = diff(slope)

  PEAK signal  : accel < 0  AND  slope crosses (≥0 → <0)
                 → enter  SHORT F1 + LONG F2      (spread will narrow)
                 → if already LONG_SPREAD: flip     (exit LONG, enter SHORT)

  TROUGH signal: accel > 0  AND  slope crosses (≤0 → >0)
                 → flip to  LONG F1 + SHORT F2    (spread will widen)

Positions flip at every signal until F1 expiry (close at 12:00 PM expiry day).
PnL per lot = spread_change × lot_size  (minus slippage 0.1% per futures leg × 4 legs per round-trip).
"""
from __future__ import annotations
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data.storage.arctic_store import ArcticStore

warnings.filterwarnings("ignore")

# ── constants ─────────────────────────────────────────────────────────────────
TICKERS    = ["ASTRAL", "KPITTECH"]
LOT_SIZES  = {"ASTRAL": 425, "KPITTECH": 425}
INTERVAL   = "15minute"
HTB_THRESH = 0.05
SLIP_FUT   = 0.001   # 0.1% per futures leg at entry and at exit
SMOOTH     = 11
OUT_DIR    = ROOT / "plots" / "spread_swing_bt"


# ── helpers ───────────────────────────────────────────────────────────────────

def find_exit_ts(cyc: pd.DataFrame) -> pd.Timestamp | None:
    if "is_expiry_day" in cyc.columns:
        rows = cyc[cyc["is_expiry_day"] == 1]
        exp_d = rows.index[0].date() if not rows.empty else cyc.index.max().date()
    else:
        exp_d = cyc.index.max().date()
    noon = pd.Timestamp(f"{exp_d} 12:00:00")
    if cyc.index.tz is not None:
        noon = noon.tz_localize(cyc.index.tz)
    cands = cyc[cyc.index <= noon]
    return cands.index[-1] if not cands.empty else None


def bar_at(cyc: pd.DataFrame, i: int) -> dict:
    row = cyc.iloc[i]
    f1 = float(row.get("F1_close", np.nan))
    f2 = float(row.get("F2_close", np.nan))
    return {
        "idx": i,
        "ts": cyc.index[i],
        "F1": f1, "F2": f2,
        "spread": f1 - f2 if (np.isfinite(f1) and np.isfinite(f2)) else np.nan,
        "b12": float(row.get("b12", np.nan)),
        "dte": float(row.get("F1_dte", np.nan)),
        "slope": float(row.get("feat_b12_slope_1d", np.nan)),
        "accel": float(row.get("feat_b12_accel", np.nan)),
    }


def bar_nearest(cyc: pd.DataFrame, ts: pd.Timestamp) -> dict:
    """Return bar data for the bar at or just before ts."""
    sub = cyc[cyc.index <= ts]
    if sub.empty:
        sub = cyc
    abs_i = cyc.index.get_loc(sub.index[-1])
    return bar_at(cyc, abs_i)


# ── signal generator ──────────────────────────────────────────────────────────

def generate_signals(cyc: pd.DataFrame, exit_ts: pd.Timestamp) -> list[dict]:
    """
    State machine scanning from bar 0 to exit_ts.

    Slope and acceleration computed from the raw F1-F2 spread (not b12),
    smoothed with an 11-bar centred rolling mean.

    States  : FLAT → SHORT_SPREAD ↔ LONG_SPREAD
    Returns : ordered list of signal dicts (type = 'peak' | 'trough')
    """
    if "F1_close" not in cyc.columns or "F2_close" not in cyc.columns:
        return []

    raw_spread = cyc["F1_close"].ffill() - cyc["F2_close"].ffill()
    smoothed   = raw_spread.rolling(SMOOTH, center=True, min_periods=1).mean()
    slopes     = smoothed.diff().to_numpy(float)
    accels     = smoothed.diff().diff().to_numpy(float)
    spreads    = raw_spread.to_numpy(float)
    n          = len(cyc)

    # Initialise previous slope sign from first bar with a finite slope
    prev_ss = 0
    start_i = 1
    for j in range(n):
        if np.isfinite(slopes[j]):
            prev_ss = int(np.sign(slopes[j])) if slopes[j] != 0 else 0
            start_i = j + 1
            break
    else:
        return []

    signals: list[dict] = []
    state = "FLAT"

    for i in range(start_i, n):
        if cyc.index[i] > exit_ts:
            break

        s, a = slopes[i], accels[i]
        if not (np.isfinite(s) and np.isfinite(a)):
            if np.isfinite(s):
                prev_ss = int(np.sign(s)) if s != 0 else prev_ss
            continue

        curr_ss = int(np.sign(s)) if s != 0 else prev_ss

        # ENTRY GATE: only trade when F1 > F2 (positive spread / backwardation)
        pos_spread = np.isfinite(spreads[i]) and spreads[i] > 0

        if state in ("FLAT", "LONG_SPREAD"):
            # PEAK: accel negative, spread slope just crossed zero downward
            if a < 0 and curr_ss < 0 and prev_ss >= 0 and pos_spread:
                signals.append({**bar_at(cyc, i), "signal": "peak"})
                state = "SHORT_SPREAD"

        elif state == "SHORT_SPREAD":
            # TROUGH: accel positive, spread slope just crossed zero upward
            if a > 0 and curr_ss > 0 and prev_ss <= 0 and pos_spread:
                signals.append({**bar_at(cyc, i), "signal": "trough"})
                state = "LONG_SPREAD"

        prev_ss = curr_ss

    return signals


# ── leg builder ───────────────────────────────────────────────────────────────

def build_legs(signals: list[dict], cyc: pd.DataFrame,
               exit_ts: pd.Timestamp, lot: int) -> list[dict]:
    if not signals:
        return []

    # Build the event list: signals + forced exit at expiry
    expiry_bar = bar_nearest(cyc, exit_ts)
    expiry_bar = {**expiry_bar, "signal": "expiry"}
    events = signals + [expiry_bar]

    legs: list[dict] = []

    for i, entry_ev in enumerate(signals):
        exit_ev = events[i + 1]

        es = entry_ev["spread"]
        xs = exit_ev["spread"]
        if not (np.isfinite(es) and np.isfinite(xs)):
            continue

        position = "SHORT_SPREAD" if entry_ev["signal"] == "peak" else "LONG_SPREAD"

        # Gross PnL per share
        gross = (es - xs) if position == "SHORT_SPREAD" else (xs - es)

        # Slippage: we open 2 futures legs at entry and close 2 at exit
        # = SLIP × (F1_entry + F2_entry + F1_exit + F2_exit)
        eF1, eF2 = entry_ev.get("F1", np.nan), entry_ev.get("F2", np.nan)
        xF1, xF2 = exit_ev.get("F1", np.nan), exit_ev.get("F2", np.nan)
        slip = sum(v * SLIP_FUT for v in [eF1, eF2, xF1, xF2] if np.isfinite(v))

        net_per = gross - slip
        pnl     = round(net_per * lot, 2)

        # ── Adverse-move analysis ─────────────────────────────────────────
        e_idx = entry_ev["idx"]
        x_idx = exit_ev["idx"]
        post  = cyc.iloc[e_idx: x_idx + 1]

        adverse_bars  = 0
        max_adv_sp    = es
        adv_pct       = 0.0

        if "F1_close" in cyc.columns and "F2_close" in cyc.columns:
            psp = (post["F1_close"] - post["F2_close"]).dropna().values
            if len(psp):
                if position == "SHORT_SPREAD":        # spread rising = adverse
                    adverse_bars = int((psp > es + 0.01).sum())
                    max_adv_sp   = float(psp.max())
                    adv_pct      = ((max_adv_sp / abs(es)) - 1) * 100 if abs(es) > 0.1 else 0
                else:                                  # spread falling = adverse
                    adverse_bars = int((psp < es - 0.01).sum())
                    max_adv_sp   = float(psp.min())
                    adv_pct      = 0.0

        # ── Loss diagnosis ────────────────────────────────────────────────
        diagnosis   = ""
        suggest_5m  = False

        if pnl < 0:
            if position == "SHORT_SPREAD":
                if adv_pct > 15 and adverse_bars >= 3:
                    diagnosis  = (f"FALSE_PEAK — spread rose {adv_pct:.0f}% above entry "
                                  f"(peak→dip was a double-peak dip, not the true peak).")
                    suggest_5m = adverse_bars >= 6
                elif abs(gross) < 0.4:
                    diagnosis  = ("NOISE — gross spread change < 0.4₹; "
                                  "slippage exceeds the move; 5-min signal may filter.")
                    suggest_5m = True
                else:
                    diagnosis  = (f"PREMATURE_ENTRY — spread adverse for {adverse_bars} bars "
                                  f"({es:+.2f} → {xs:+.2f}). 5-min bars would shift entry later.")
                    suggest_5m = adverse_bars >= 4
            else:
                if adverse_bars >= 6:
                    diagnosis  = (f"FALSE_TROUGH — spread kept falling {adverse_bars} bars "
                                  f"after trough entry. True trough was later.")
                    suggest_5m = True
                else:
                    diagnosis  = (f"SPREAD_NARROWED — {es:+.2f} → {xs:+.2f}. "
                                  "Short-lived trough; 5-min may improve confirmation.")
                    suggest_5m = True

        legs.append({
            "position":     position,
            "entry_ts":     entry_ev["ts"],
            "exit_ts":      exit_ev["ts"],
            "exit_type":    exit_ev["signal"],
            "entry_spread": es,
            "exit_spread":  xs,
            "spread_chg":   xs - es,
            "entry_dte":    entry_ev.get("dte", np.nan),
            "gross_per":    round(gross, 4),
            "slip_per":     round(slip, 4),
            "net_per":      round(net_per, 4),
            "pnl":          pnl,
            "lot":          lot,
            "duration_bars": x_idx - e_idx,
            "adverse_bars": adverse_bars,
            "adv_pct":      adv_pct,
            "max_adv_sp":   max_adv_sp,
            "diagnosis":    diagnosis,
            "suggest_5m":   suggest_5m,
        })

    return legs


def hold_benchmark(signals: list[dict], cyc: pd.DataFrame,
                   exit_ts: pd.Timestamp, lot: int) -> float:
    """SHORT F1+LONG F2 from first PEAK signal held to expiry (baseline)."""
    peaks = [s for s in signals if s["signal"] == "peak"]
    if not peaks:
        return np.nan
    entry = peaks[0]
    es, eF1, eF2 = entry["spread"], entry.get("F1", np.nan), entry.get("F2", np.nan)
    xbar = bar_nearest(cyc, exit_ts)
    xs, xF1, xF2 = xbar["spread"], xbar.get("F1", np.nan), xbar.get("F2", np.nan)
    if not (np.isfinite(es) and np.isfinite(xs)):
        return np.nan
    gross = es - xs
    slip  = sum(v * SLIP_FUT for v in [eF1, eF2, xF1, xF2] if np.isfinite(v))
    return round((gross - slip) * lot, 2)


# ── console output ────────────────────────────────────────────────────────────

SEP = "─" * 130

def print_cycle(ticker: str, cid: int, peak_b12: float,
                legs: list[dict], h_pnl: float) -> None:
    swing = sum(lg["pnl"] for lg in legs)
    vs    = swing - h_pnl if np.isfinite(h_pnl) else np.nan
    h_str = f"₹{h_pnl:+,.0f}" if np.isfinite(h_pnl) else "n/a"
    vs_str= f"₹{vs:+,.0f}" if np.isfinite(vs) else "n/a"
    print(f"\n  {SEP}")
    print(f"  {ticker}  cid={cid}  peak_b12={peak_b12:.4f}  "
          f"legs={len(legs)}  "
          f"swing=₹{swing:+,.0f}  hold={h_str}  vs_hold={vs_str}")
    print(f"  {SEP}")

    if not legs:
        print("    (no signals fired this cycle)")
        return

    for j, lg in enumerate(legs, 1):
        pos_lbl = "SHORT F1+LNG F2" if lg["position"] == "SHORT_SPREAD" else "LNG F1+SHORT F2"
        ts_e    = str(lg["entry_ts"])[:16]
        ts_x    = str(lg["exit_ts"])[:16]
        flag    = "WIN " if lg["pnl"] >= 0 else "LOSS"
        xe      = lg["exit_type"][:6]
        print(
            f"    L{j} {flag} │ {pos_lbl} │ {ts_e} → {ts_x} [{xe:<6}] │ "
            f"spread: {lg['entry_spread']:>+7.2f} → {lg['exit_spread']:>+7.2f} "
            f"(Δ{lg['spread_chg']:>+6.2f}) │ "
            f"gross={lg['gross_per']:>+6.3f}  slip={lg['slip_per']:.3f} │ "
            f"PnL=₹{lg['pnl']:>+9,.0f}  [{lg['duration_bars']}bars]"
        )
        if lg["diagnosis"]:
            tf_hint = "  → 5-min bars likely improve this" if lg["suggest_5m"] else ""
            print(f"         ⚠  {lg['diagnosis']}{tf_hint}")


def print_summary(all_cycles: list[dict]) -> None:
    print(f"\n\n{'='*130}")
    print("  SWING STRATEGY — POOLED RESULTS — ASTRAL & KPITTECH")
    print(f"{'='*130}")

    for ticker in TICKERS:
        tc       = [c for c in all_cycles if c["ticker"] == ticker]
        if not tc:
            continue
        all_legs = [lg for c in tc for lg in c["legs"]]
        sw_arr   = np.array([c["swing_pnl"] for c in tc], float)
        ho_arr   = np.array([c["hold_pnl"]  for c in tc if np.isfinite(c["hold_pnl"])], float)
        lg_arr   = np.array([lg["pnl"] for lg in all_legs], float)
        sh_arr   = np.array([lg["pnl"] for lg in all_legs if lg["position"] == "SHORT_SPREAD"], float)
        lo_arr   = np.array([lg["pnl"] for lg in all_legs if lg["position"] == "LONG_SPREAD"],  float)

        def wr(a): return f"{100*(a>0).mean():.0f}%" if len(a) else "n/a"
        def mn(a): return f"₹{np.nanmean(a):+,.0f}" if len(a) else "n/a"

        print(f"\n  ── {ticker}  ({len(tc)} cycles, {len(all_legs)} legs) ──")
        print(f"    CYCLES   swing  win={wr(sw_arr):>5}  mean={mn(sw_arr):>10}  cum=₹{sw_arr.sum():+,.0f}")
        if len(ho_arr):
            print(f"    CYCLES   hold   win={wr(ho_arr):>5}  mean={mn(ho_arr):>10}  cum=₹{ho_arr.sum():+,.0f}")
        print(f"    ALL LEGS        win={wr(lg_arr):>5}  mean={mn(lg_arr):>10}  cum=₹{lg_arr.sum():+,.0f}")
        if len(sh_arr):
            print(f"    SHORT_SPREAD    win={wr(sh_arr):>5}  mean={mn(sh_arr):>10}  n={len(sh_arr)}")
        if len(lo_arr):
            print(f"    LONG_SPREAD     win={wr(lo_arr):>5}  mean={mn(lo_arr):>10}  n={len(lo_arr)}")

        # Loss breakdown
        losses = [lg for lg in all_legs if lg["pnl"] < 0]
        if losses:
            print(f"\n    LOSING LEGS ({len(losses)}/{len(all_legs)}):")
            for lg in losses:
                print(f"      {lg['position']:<14} "
                      f"entry={str(lg['entry_ts'])[:16]}  "
                      f"DTE={lg['entry_dte']:.0f}  "
                      f"spread {lg['entry_spread']:+.2f}→{lg['exit_spread']:+.2f}  "
                      f"PnL=₹{lg['pnl']:+,.0f}  adv_bars={lg['adverse_bars']}")
                if lg["diagnosis"]:
                    print(f"        → {lg['diagnosis']}")

        # Timeframe recommendation
        need_5m = [lg for lg in all_legs if lg.get("suggest_5m")]
        if need_5m:
            adv_avg = np.mean([lg["adverse_bars"] for lg in need_5m])
            print(f"\n    TIMEFRAME: {len(need_5m)}/{len(losses) or 1} problem legs averaged "
                  f"{adv_avg:.1f} adverse 15-min bars post-entry.")
            print(f"    → 15-min bars are too coarse for {ticker}'s fast double-peak oscillations.")
            print(f"    → 5-min bars would give ~3× more signal resolution, catching inflection")
            print(f"      earlier (estimated entry improvement: 1–3 bars × 15min = 15–45min).")
            print(f"    → Each missed bar costs ≈₹{np.mean([abs(lg['pnl']) for lg in need_5m]):.0f}/lot on average.")
        else:
            print(f"\n    TIMEFRAME: 15-min sufficient for {ticker} — losses not timing-driven.")

    # Pooled
    all_legs  = [lg for c in all_cycles for lg in c["legs"]]
    sw_all    = np.array([c["swing_pnl"] for c in all_cycles], float)
    ho_all    = np.array([c["hold_pnl"]  for c in all_cycles if np.isfinite(c["hold_pnl"])], float)
    lg_arr    = np.array([lg["pnl"] for lg in all_legs], float)
    n_5m      = sum(1 for lg in all_legs if lg.get("suggest_5m"))

    print(f"\n  ── POOLED ──")
    print(f"    cycles={len(all_cycles)}  legs={len(all_legs)}")
    print(f"    swing cum=₹{sw_all.sum():+,.0f}   hold cum=₹{ho_all.sum():+,.0f}   vs_hold=₹{sw_all.sum()-ho_all.sum():+,.0f}")
    print(f"    leg win rate={100*(lg_arr>0).mean():.1f}%   mean/leg=₹{np.nanmean(lg_arr):+,.0f}")
    print(f"    legs suggesting 5-min TF: {n_5m}/{len(all_legs)}")


# ── plots ─────────────────────────────────────────────────────────────────────

def plot_all(all_cycles: list[dict], br_store: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)

    # ── G1: per-cycle swing vs hold ────────────────────────────────────────
    fig, axes = plt.subplots(2, 1, figsize=(max(14, len(all_cycles)), 10))
    for ax, ticker in zip(axes, TICKERS):
        tc = [c for c in all_cycles if c["ticker"] == ticker]
        if not tc:
            continue
        x  = np.arange(len(tc))
        sw = [c["swing_pnl"] for c in tc]
        ho = [c["hold_pnl"] if np.isfinite(c["hold_pnl"]) else 0 for c in tc]
        w  = 0.33
        ax.bar(x - w/2, sw, w, label="Swing (all legs)",  color="#185FA5", alpha=0.85, zorder=3)
        ax.bar(x + w/2, ho, w, label="Hold to expiry",    color="#BA7517", alpha=0.85, zorder=3)
        for xi, sv, hv in zip(x, sw, ho):
            for yv, xo in [(sv, -w/2), (hv, w/2)]:
                if abs(yv) > 500:
                    ax.text(xi + xo, yv + (300 if yv >= 0 else -600),
                            f"₹{yv/1000:+.1f}k", ha="center", fontsize=7)
        labels = [f"c{c['cid']}\n{c['n_legs']}L" for c in tc]
        ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=8)
        ax.axhline(0, color="k", lw=0.8); ax.grid(alpha=0.2, axis="y", zorder=0)
        ax.set_title(f"{ticker} — per-cycle PnL: swing vs hold-to-expiry", fontsize=11)
        ax.set_ylabel("PnL ₹"); ax.legend(fontsize=9)
    fig.suptitle("Spread Swing Strategy — per-cycle PnL", fontsize=12)
    fig.tight_layout(); fig.savefig(out / "g1_cycle_pnl.png", dpi=120); plt.close(fig)
    print("  Saved g1_cycle_pnl.png")

    # ── G2: cumulative equity curves ────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(16, 6))
    clr = {"ASTRAL": "#185FA5", "KPITTECH": "#e74c3c"}
    for ticker in TICKERS:
        tc = sorted([c for c in all_cycles if c["ticker"] == ticker],
                    key=lambda c: c.get("first_entry_ts") or pd.Timestamp.max)
        if not tc:
            continue
        sw_cum = np.cumsum([c["swing_pnl"] for c in tc])
        ho_cum = np.nancumsum([c["hold_pnl"] if np.isfinite(c["hold_pnl"]) else 0 for c in tc])
        xs = range(len(tc))
        ax.plot(xs, sw_cum, color=clr[ticker], lw=2.2, marker="o", ms=5, label=f"{ticker} swing")
        ax.plot(xs, ho_cum, color=clr[ticker], lw=1.2, ls="--", alpha=0.55, label=f"{ticker} hold")
    ax.axhline(0, color="k", lw=0.8); ax.grid(alpha=0.2)
    ax.set_title("Cumulative PnL — swing (solid) vs hold-to-expiry (dashed)")
    ax.set_xlabel("cycle #"); ax.set_ylabel("₹ cumulative"); ax.legend(fontsize=9)
    fig.tight_layout(); fig.savefig(out / "g2_cumulative.png", dpi=120); plt.close(fig)
    print("  Saved g2_cumulative.png")

    # ── G3: leg PnL distributions ──────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for ax, ticker in zip(axes, TICKERS):
        tc = [c for c in all_cycles if c["ticker"] == ticker]
        all_legs = [lg for c in tc for lg in c["legs"]]
        sh_pnl = [lg["pnl"] for lg in all_legs if lg["position"] == "SHORT_SPREAD"]
        lo_pnl = [lg["pnl"] for lg in all_legs if lg["position"] == "LONG_SPREAD"]
        all_v  = sh_pnl + lo_pnl
        if not all_v:
            continue
        lo_b = min(all_v) - 500; hi_b = max(all_v) + 500
        bins = np.linspace(lo_b, hi_b, 20)
        if sh_pnl:
            ax.hist(sh_pnl, bins=bins, alpha=0.72, color="#185FA5", label=f"SHORT_SPREAD (n={len(sh_pnl)})")
        if lo_pnl:
            ax.hist(lo_pnl, bins=bins, alpha=0.72, color="#e74c3c", label=f"LONG_SPREAD  (n={len(lo_pnl)})")
        ax.axvline(0, color="k", lw=1, ls="--")
        ax.set_title(f"{ticker} — leg PnL distribution")
        ax.set_xlabel("PnL ₹/leg"); ax.set_ylabel("count"); ax.legend(fontsize=9); ax.grid(alpha=0.2)
    fig.suptitle("Leg PnL distribution by signal type", fontsize=12)
    fig.tight_layout(); fig.savefig(out / "g3_leg_dist.png", dpi=120); plt.close(fig)
    print("  Saved g3_leg_dist.png")

    # ── G4: b12 chart with signals for up to 6 most interesting cycles ─────
    # "most interesting" = highest number of legs (most oscillations)
    candidates = sorted(all_cycles, key=lambda c: c["n_legs"], reverse=True)
    # pick top 3 per ticker
    picks = []
    for ticker in TICKERS:
        tc_picks = [c for c in candidates if c["ticker"] == ticker][:3]
        picks += tc_picks
    picks = picks[:6]
    n = len(picks)
    if n == 0:
        return

    ncols = min(2, n); nrows = (n + 1) // 2
    fig, axes = plt.subplots(nrows, ncols, figsize=(16, 5 * nrows), squeeze=False)
    axes_flat  = [ax for row in axes for ax in row]

    for ax, cyc_rec in zip(axes_flat, picks):
        ticker = cyc_rec["ticker"]
        cid    = cyc_rec["cid"]
        br     = br_store[ticker]
        cyc    = br[br["cycle_id"] == cid].sort_index()

        raw_sp = (cyc["F1_close"].ffill() - cyc["F2_close"].ffill()).values
        sm_sp  = pd.Series(raw_sp).rolling(SMOOTH, center=True, min_periods=1).mean().values
        ts_plt = [t.tz_localize(None) if t.tzinfo is not None else t for t in cyc.index]

        ax.plot(ts_plt, raw_sp, color="lightgray", lw=1,   label="raw F1-F2 spread")
        ax.plot(ts_plt, sm_sp,  color="#2c3e50",   lw=1.5, alpha=0.7, label="smoothed spread")

        # Mark each leg's entry
        for lg in cyc_rec["legs"]:
            e_ts = lg["entry_ts"]
            if e_ts.tzinfo is not None:
                e_ts = e_ts.tz_localize(None)
            col  = "#185FA5" if lg["position"] == "SHORT_SPREAD" else "#e74c3c"
            mrkr = "v"       if lg["position"] == "SHORT_SPREAD" else "^"
            # find spread value at entry bar for marker placement
            sub = [(t, v) for t, v in zip(ts_plt, raw_sp) if t <= e_ts]
            b_v = sub[-1][1] if sub else raw_sp[0]
            ax.scatter([e_ts], [b_v], color=col, marker=mrkr, s=140, zorder=5)
            ax.axvline(e_ts, color=col, lw=0.7, ls="--", alpha=0.45)
            # annotate PnL
            ax.text(e_ts, b_v * 1.01,
                    f"₹{lg['pnl']/1000:+.1f}k",
                    fontsize=7, color=col, ha="center", va="bottom", rotation=0)

        # Mark expiry exit
        x_ts = cyc_rec.get("exit_ts")
        if x_ts is not None:
            if x_ts.tzinfo is not None:
                x_ts = x_ts.tz_localize(None)
            ax.axvline(x_ts, color="black", lw=1.5, ls=":", label="expiry 12PM")

        swing = cyc_rec["swing_pnl"]
        hold  = cyc_rec["hold_pnl"]
        ax.set_title(
            f"{ticker} cid={cid} | {cyc_rec['n_legs']} legs | "
            f"swing=₹{swing:+,.0f} | hold=₹{hold:+,.0f}",
            fontsize=10
        )
        ax.set_ylabel("F1-F2 spread (₹)"); ax.grid(alpha=0.2); ax.tick_params(axis="x", rotation=30)
        custom_leg = [
            Line2D([0],[0], color="#185FA5", marker="v", ls="", ms=9, label="peak entry (SHORT)"),
            Line2D([0],[0], color="#e74c3c", marker="^", ls="", ms=9, label="trough entry (LONG)"),
        ]
        ax.legend(handles=custom_leg, fontsize=8, loc="upper right")

    for ax in axes_flat[n:]:
        ax.set_visible(False)

    fig.suptitle("F1-F2 spread signal — peak▼ (short) and trough▲ (long) entries", fontsize=12)
    fig.tight_layout(); fig.savefig(out / "g4_signal_viz.png", dpi=120); plt.close(fig)
    print("  Saved g4_signal_viz.png")

    # ── G5: DTE at entry vs leg PnL scatter ────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for ax, ticker in zip(axes, TICKERS):
        tc = [c for c in all_cycles if c["ticker"] == ticker]
        all_legs = [lg for c in tc for lg in c["legs"]]
        for lg in all_legs:
            col  = "#185FA5" if lg["position"] == "SHORT_SPREAD" else "#e74c3c"
            mrkr = "v"       if lg["position"] == "SHORT_SPREAD" else "^"
            ax.scatter(lg.get("entry_dte", np.nan), lg["pnl"],
                       color=col, marker=mrkr, alpha=0.75, s=70)
        ax.axhline(0, color="k", lw=0.8, ls="--")
        ax.axvline(5, color="orange", lw=1.2, ls=":", label="DTE=5")
        ax.set_title(f"{ticker} — DTE at entry vs leg PnL")
        ax.set_xlabel("DTE at entry"); ax.set_ylabel("PnL ₹/leg"); ax.grid(alpha=0.2)
        ax.legend(handles=[
            Line2D([0],[0], color="#185FA5", marker="v", ls="", ms=9, label="SHORT_SPREAD"),
            Line2D([0],[0], color="#e74c3c", marker="^", ls="", ms=9, label="LONG_SPREAD"),
            Line2D([0],[0], color="orange",  lw=1.5, ls=":", label="DTE=5"),
        ], fontsize=9)
    fig.suptitle("Entry DTE vs leg PnL — swing strategy", fontsize=12)
    fig.tight_layout(); fig.savefig(out / "g5_dte_pnl.png", dpi=120); plt.close(fig)
    print("  Saved g5_dte_pnl.png")

    # ── G6: leg duration vs PnL — do shorter legs do better? ───────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for ax, ticker in zip(axes, TICKERS):
        tc = [c for c in all_cycles if c["ticker"] == ticker]
        all_legs = [lg for c in tc for lg in c["legs"]]
        sh = [(lg["duration_bars"], lg["pnl"]) for lg in all_legs if lg["position"] == "SHORT_SPREAD"]
        lo = [(lg["duration_bars"], lg["pnl"]) for lg in all_legs if lg["position"] == "LONG_SPREAD"]
        if sh:
            ax.scatter(*zip(*sh), color="#185FA5", marker="v", alpha=0.75, s=70, label="SHORT_SPREAD")
        if lo:
            ax.scatter(*zip(*lo), color="#e74c3c", marker="^", alpha=0.75, s=70, label="LONG_SPREAD")
        ax.axhline(0, color="k", lw=0.8, ls="--")
        ax.set_title(f"{ticker} — leg duration vs PnL")
        ax.set_xlabel("duration (15-min bars)"); ax.set_ylabel("PnL ₹/leg")
        ax.grid(alpha=0.2); ax.legend(fontsize=9)
    fig.suptitle("Leg duration vs PnL — does holding longer pay?", fontsize=12)
    fig.tight_layout(); fig.savefig(out / "g6_duration_pnl.png", dpi=120); plt.close(fig)
    print("  Saved g6_duration_pnl.png")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    store      = ArcticStore()
    all_cycles: list[dict] = []
    br_store:   dict = {}

    print("\nSPREAD SWING BACKTEST — ASTRAL & KPITTECH — FUTURES ONLY")
    print(f"Signal: computed on 11-bar smoothed F1-F2 spread (NOT b12)")
    print(f"        spread-accel<0 + spread-slope crosses ≥0→<0  → PEAK  (SHORT F1+LONG F2)")
    print(f"        spread-accel>0 + spread-slope crosses ≤0→>0  → TROUGH (LONG F1+SHORT F2)")
    print(f"ENTRY GATE: only enter when F1 > F2 (positive spread / backwardation)")
    print(f"Exit:   12:00 PM on F1 expiry day")
    print(f"Slip:   {SLIP_FUT*100:.1f}% × (F1_entry + F2_entry + F1_exit + F2_exit) per leg\n")

    skip_counts = {t: {"htb": 0, "no_pos_entry": 0, "traded": 0} for t in TICKERS}

    for ticker in TICKERS:
        br = store.read_borrow_rates(ticker, INTERVAL)
        if br is None or br.empty:
            print(f"[{ticker}] no data"); continue
        br = br.copy()
        br_store[ticker] = br
        lot = LOT_SIZES[ticker]

        for cid, cyc in br.groupby("cycle_id", sort=True):
            cyc = cyc.sort_index()
            b   = cyc["b12"].ffill().to_numpy(float)
            sm  = pd.Series(b).rolling(SMOOTH, center=True, min_periods=1).mean().to_numpy()
            peak_b12 = float(np.nanmax(sm))
            if peak_b12 < HTB_THRESH:
                continue
            skip_counts[ticker]["htb"] += 1

            exit_ts = find_exit_ts(cyc)
            if exit_ts is None:
                continue

            signals = generate_signals(cyc, exit_ts)
            legs    = build_legs(signals, cyc, exit_ts, lot)

            # Skip HTB cycles that never offered a F1>F2 entry
            if not legs:
                skip_counts[ticker]["no_pos_entry"] += 1
                print(f"\n  {ticker}  cid={cid}  peak_b12={peak_b12:.4f}  "
                      f"— no F1>F2 entry (spread stayed ≤ 0 at all signals)")
                continue
            skip_counts[ticker]["traded"] += 1

            h_pnl   = hold_benchmark(signals, cyc, exit_ts, lot)
            swing_pnl    = sum(lg["pnl"] for lg in legs)
            first_entry  = legs[0]["entry_ts"] if legs else None

            rec = {
                "ticker":         ticker,
                "cid":            int(cid),
                "peak_b12":       peak_b12,
                "n_legs":         len(legs),
                "legs":           legs,
                "swing_pnl":      swing_pnl,
                "hold_pnl":       h_pnl if np.isfinite(h_pnl) else np.nan,
                "first_entry_ts": first_entry,
                "exit_ts":        exit_ts,
            }
            all_cycles.append(rec)
            print_cycle(ticker, int(cid), peak_b12, legs, h_pnl)

    print(f"\n  ── CYCLE FILTERING (F1 > F2 entry gate) ──")
    for t in TICKERS:
        sc = skip_counts[t]
        print(f"    {t}: {sc['htb']} HTB cycles → {sc['traded']} traded "
              f"(F1>F2 entry found), {sc['no_pos_entry']} skipped (no F1>F2 entry)")

    if not all_cycles:
        print("No qualifying HTB cycles found."); return

    print_summary(all_cycles)

    print(f"\nGenerating plots → {OUT_DIR}")
    plot_all(all_cycles, br_store, OUT_DIR)

    n_total = len(all_cycles)
    n_legs  = sum(c["n_legs"] for c in all_cycles)
    print(f"\nDone. {n_total} HTB cycles, {n_legs} legs total.")


if __name__ == "__main__":
    main()
