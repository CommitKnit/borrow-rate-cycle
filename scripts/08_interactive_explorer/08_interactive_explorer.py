"""
08_interactive_explorer.py — a self-contained HTML page to explore every cycle.

Pick a ticker and an expiry cycle and see that cycle's F1 - F2 spread and the
open-interest transfer against the population of hard-to-borrow cycles, plus its path
on the spread-vs-OI-ratio chart and a stats card. The page loads plotly.js from a CDN
and embeds only the data, so it can be hosted as-is (e.g. GitHub Pages from docs/).

Inputs:  borrowcycle.data (the engine ArcticDB), results/roll_sessions.csv,
         results/roll_cycles.csv, results/cycle_moments.csv, results/pnl_per_cycle.csv
Output:  docs/explorer/index.html
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.offline as po

from borrowcycle import data as bd
from borrowcycle import roll

RESULTS = bd.PKG_ROOT / "results"
OUT = bd.PKG_ROOT / "docs" / "explorer" / "index.html"
SESSIONS = 20


def r(v, k=2):
    return None if v is None or not np.isfinite(v) else round(float(v), k)


def cycle_series(cyc: pd.DataFrame) -> dict:
    """Hourly (last bar per hour) spread and OI share on a sessions-to-expiry axis."""
    sess = roll.session_frame(cyc)
    left = pd.Series(sess["sessions_left"].to_numpy(), index=sess.index.normalize())
    b = cyc.assign(spread_bps=roll.spread_bps(cyc),
                   share=cyc["F1_oi"] / (cyc["F1_oi"] + cyc["F2_oi"]).replace(0, np.nan))
    hourly = b.groupby([b.index.normalize(), b.index.hour]).tail(1)
    day = hourly.index.normalize()
    pos = hourly.groupby(day).cumcount().to_numpy()
    n = hourly.groupby(day)["spread_bps"].transform("size").to_numpy()
    x = -left.reindex(day).to_numpy() - 1 + (pos + 1) / n
    keep = np.isfinite(x) & (x >= -SESSIONS - 1)
    eod = sess[sess["sessions_left"] <= SESSIONS]
    return {"x": [r(v, 3) for v in x[keep]],
            "spread": [r(v, 1) for v in hourly["spread_bps"].to_numpy()[keep]],
            "share": [r(v * 100, 1) for v in hourly["share"].to_numpy()[keep]],
            "eod_ratio": [r(v, 3) for v in eod["oi_ratio"]],
            "eod_spread": [r(v, 1) for v in eod["spread_bps"]],
            "eod_left": [int(v) for v in eod["sessions_left"]],
            "expiry": roll.f1_expiry_date(cyc).strftime("%d %b %Y")}


def population(sess: pd.DataFrame) -> dict:
    win = sess[sess.sessions_left.between(0, SESSIONS)]
    prof = {}
    for g in ("HTB", "non-HTB"):
        p = win[win.htb == g].groupby("sessions_left").agg(
            med=("spread_bps", "median"), p25=("spread_bps", lambda v: v.quantile(.25)),
            p75=("spread_bps", lambda v: v.quantile(.75)), share=("oi_share_f1", "median")).sort_index()
        prof[g] = {"x": [-int(i) for i in p.index], "med": [r(v, 1) for v in p.med],
                   "p25": [r(v, 1) for v in p.p25], "p75": [r(v, 1) for v in p.p75],
                   "share": [r(v * 100, 1) for v in p.share]}
    d = sess[(sess.htb == "HTB") & sess.oi_ratio.between(0.15, 60)].copy()
    edges = np.geomspace(0.15, 60, 19)
    d["b"] = pd.cut(d.oi_ratio, edges)
    a = d.groupby("b", observed=True).spread_bps.agg(
        med="median", p25=lambda v: v.quantile(.25), p75=lambda v: v.quantile(.75), n="size")
    a = a[a.n >= 40]
    prof["phase"] = {"x": [r(np.sqrt(i.left * i.right), 3) for i in a.index], "med": [r(v, 1) for v in a.med],
                     "p25": [r(v, 1) for v in a.p25], "p75": [r(v, 1) for v in a.p75]}
    return prof


def main() -> int:
    sess = pd.read_csv(RESULTS / "roll_sessions.csv")
    rc = pd.read_csv(RESULTS / "roll_cycles.csv").set_index(["ticker", "cid"])
    mom = pd.read_csv(RESULTS / "cycle_moments.csv").set_index(["ticker", "cid"])
    pnl = pd.read_csv(RESULTS / "pnl_per_cycle.csv")
    pnl = pnl[pnl.variant == "trailing"].set_index(["ticker", "cid"])

    cycles: dict = {}
    for t in bd.TICKERS:
        cycles[t] = []
        for cid, cyc in bd.iter_cycles(t):
            if (t, cid) not in rc.index:
                continue
            c, m = rc.loc[(t, cid)], mom.loc[(t, cid)] if (t, cid) in mom.index else None
            s = cycle_series(cyc)
            s.update({"cid": int(cid), "tier": c.tier,
                      "stats": {"amplitude_bps": r(c.amplitude_bps, 0),
                                "retracement": r(m.retracement) if m is not None else None,
                                "peak_b12": r(m.peak_b12 * 100, 1) if m is not None else None,
                                "oi_ratio_at_peak": r(c.oi_ratio_at_peak),
                                "peak_sessions_left": r(c.peak_sessions_left, 0),
                                "mid_sessions_left": r(c.mid_sessions_left, 0),
                                "s1_bps": r(pnl.loc[(t, cid), "S1_bps"], 1) if (t, cid) in pnl.index else None}})
            cycles[t].append(s)
    payload = {"tickers": bd.TICKERS, "cycles": cycles, "pop": population(sess), "sessions": SESSIONS}
    html = TEMPLATE.replace("__PLOTLY_VERSION__", po.get_plotlyjs_version()).replace(
        "__DATA__", json.dumps(payload, separators=(",", ":")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    n = sum(len(v) for v in cycles.values())
    print(f"wrote {OUT.relative_to(bd.PKG_ROOT)} ({OUT.stat().st_size / 1e6:.2f} MB, {n} cycles)")
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Borrow Cycle Explorer</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@__PLOTLY_VERSION__/plotly.min.js"></script>
<style>
  :root { --bg:#ffffff; --fg:#1f2933; --muted:#616e7c; --card:#f5f7fa; --line:#d9e2ec;
          --spread:#e67e22; --f1:#c0392b; --f2:#2980b9; --ink:#2c3e50; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14181d; --fg:#e4e7eb; --muted:#9aa5b1; --card:#1f252c; --line:#323f4b; --ink:#cbd2d9; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 48px; }
  h1 { font-size: 1.5rem; margin: 0 0 4px; }
  .lede { color: var(--muted); margin: 0 0 18px; max-width: 70ch; }
  .controls { display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin-bottom: 14px; }
  label { font-size: .8rem; color: var(--muted); display:flex; flex-direction:column; gap:4px; }
  select { font: inherit; padding: 6px 10px; border-radius: 8px; border:1px solid var(--line);
           background: var(--card); color: var(--fg); min-width: 170px; }
  .grid { display:grid; grid-template-columns: 2fr 1fr; gap: 16px; }
  .panel { background: var(--card); border-radius: 12px; padding: 8px; min-width: 0; }
  .stats { display:grid; grid-template-columns: 1fr 1fr; gap: 10px 16px; padding: 14px; align-content:start; }
  .stat .v { font-size: 1.35rem; font-weight: 600; }
  .stat .k { font-size: .78rem; color: var(--muted); }
  .note { font-size: .82rem; color: var(--muted); margin-top: 14px; }
  @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<main>
  <h1>Borrow Cycle Explorer</h1>
  <p class="lede">NSE single-stock futures: the front-month premium (F1 − F2, in bps of F1) builds as short
    holders begin rolling, peaks while open interest moves to the next month, and collapses at settlement.
    Pick a cycle to compare it with the median hard-to-borrow cycle.</p>
  <div class="controls">
    <label>Ticker <select id="ticker"></select></label>
    <label>Expiry cycle <select id="cycle"></select></label>
  </div>
  <div class="grid">
    <div class="panel"><div id="spread" style="height:330px"></div><div id="oi" style="height:250px"></div></div>
    <div>
      <div class="panel stats" id="stats"></div>
      <div class="panel" style="margin-top:16px"><div id="phase" style="height:330px"></div></div>
    </div>
  </div>
  <p class="note">Shaded band: middle 50% of hard-to-borrow cycles (peak b12 ≥ 5%); dashed grey: cycles with no
    borrow premium. x-axis: trading sessions to front-month expiry. S1 = short F1 / long F2 from the first bar
    after the (trailing) spread peak to noon on expiry day, net of 0.1% slippage per leg. Source and method:
    <a href="../../README.md">README</a>, <a href="../08_roll_mechanics.md">docs/08_roll_mechanics.md</a>.</p>
</main>
<script>
const D = __DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const tSel = document.getElementById("ticker"), cSel = document.getElementById("cycle");
D.tickers.forEach(t => tSel.add(new Option(`${t} (${D.cycles[t].length} cycles)`, t)));

function fillCycles() {
  cSel.innerHTML = "";
  cSel.add(new Option("All cycles (median only)", "median"));
  D.cycles[tSel.value].forEach((c, i) => cSel.add(new Option(`${c.expiry}  ·  ${c.tier}`, i)));
  const big = D.cycles[tSel.value].reduce((b, c, i, a) =>
    (c.stats.amplitude_bps ?? -1) > (a[b].stats.amplitude_bps ?? -1) ? i : b, 0);
  cSel.value = String(big);
}

function layout(title, xt, yt, extra = {}) {
  return Object.assign({
    title: { text: title, font: { size: 13 } }, margin: { l: 56, r: 14, t: 34, b: 42 },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: css("--fg"), size: 11 },
    xaxis: { title: xt, gridcolor: css("--line"), zeroline: false, tickangle: 0 },
    yaxis: { title: yt, gridcolor: css("--line"), zeroline: true, zerolinecolor: css("--line") },
    legend: { orientation: "h", y: -0.25, font: { size: 10 } }, hovermode: "x unified" }, extra);
}
const band = (x, lo, hi, name) => [
  { x, y: hi, mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false },
  { x, y: lo, mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: "rgba(230,126,34,0.18)",
    name, hoverinfo: "skip" }];

function draw() {
  const P = D.pop, H = P.HTB, N = P["non-HTB"], cfg = { displaylogo: false, responsive: true };
  const c = cSel.value === "median" ? null : D.cycles[tSel.value][+cSel.value];
  const ticks = { tickangle: 0, tickvals: [...Array(11).keys()].map(i => -2 * i), ticktext: [...Array(11).keys()].map(i => 2 * i) };

  const sp = [...band(H.x, H.p25, H.p75, "hard-to-borrow, middle 50%"),
    { x: H.x, y: H.med, name: "hard-to-borrow median", line: { color: css("--spread"), width: 3 } },
    { x: N.x, y: N.med, name: "no-premium median", line: { color: "#7f8c8d", dash: "dash", width: 2 } }];
  if (c) sp.push({ x: c.x, y: c.spread, name: `${tSel.value} ${c.expiry}`, line: { color: css("--ink"), width: 1.6 } });
  Plotly.react("spread", sp, layout(c ? `${tSel.value} · ${c.expiry} · spread` : "F1 − F2 spread (medians)",
    "", "bps of F1", { xaxis: Object.assign({ range: [-D.sessions - 0.5, 0.3], gridcolor: css("--line") }, ticks) }), cfg);

  const oi = [{ x: H.x, y: H.share, name: "hard-to-borrow median", line: { color: css("--f1"), width: 3 } }];
  if (c) oi.push({ x: c.x, y: c.share, name: "this cycle", line: { color: css("--ink"), width: 1.6 } });
  Plotly.react("oi", oi, layout("Front-month share of OI", "trading sessions to expiry", "% of F1 + F2 OI",
    { xaxis: Object.assign({ range: [-D.sessions - 0.5, 0.3], gridcolor: css("--line") }, ticks),
      yaxis: { range: [0, 100], gridcolor: css("--line") },
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 50, y1: 50, line: { dash: "dot", color: css("--muted") } }] }), cfg);

  const ph = [...band(P.phase.x, P.phase.p25, P.phase.p75, "middle 50%"),
    { x: P.phase.x, y: P.phase.med, name: "hard-to-borrow median", line: { color: css("--spread"), width: 3 } }];
  if (c) ph.push({ x: c.eod_ratio, y: c.eod_spread, mode: "lines+markers", name: "this cycle (end of day)",
    text: c.eod_left.map(s => `${s} sessions to expiry`), hovertemplate: "%{text}<br>F1/F2 %{x:.2f}x<br>%{y:.0f} bps<extra></extra>",
    line: { color: css("--ink"), width: 1.4 }, marker: { size: 6, color: c.eod_left, colorscale: "Viridis", reversescale: true } });
  Plotly.react("phase", ph, layout("Spread vs F1/F2 OI ratio", "F1 OI / F2 OI (roll progresses →)", "bps of F1",
    { xaxis: { type: "log", autorange: "reversed", gridcolor: css("--line") }, hovermode: "closest",
      shapes: [1, 2, 4].map(v => ({ type: "line", x0: v, x1: v, yref: "paper", y0: 0, y1: 1,
        line: { dash: "dot", color: v === 1 ? css("--f1") : css("--muted") } })) }), cfg);

  const s = c ? c.stats : null, fmt = (v, u = "") => v === null || v === undefined ? "–" : `${v}${u}`;
  document.getElementById("stats").innerHTML = c ? [
    ["Borrow tier", c.tier], ["Peak b12", fmt(s.peak_b12, "%")],
    ["Amplitude, build → peak", fmt(s.amplitude_bps, " bps")], ["Retraced by settlement", s.retracement === null ? "–" : `${Math.round(100 * s.retracement)}%`],
    ["F1/F2 OI at the peak", fmt(s.oi_ratio_at_peak, "x")], ["Peak, sessions to expiry", fmt(s.peak_sessions_left)],
    ["Roll midpoint (F1/F2 < 1)", fmt(s.mid_sessions_left, " sessions out")], ["S1 calendar trade", fmt(s.s1_bps, " bps")]
  ].map(([k, v]) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("")
    : `<div class="stat" style="grid-column:1/-1"><div class="k">Showing the population medians. Pick a cycle to see its own path and statistics.</div></div>`;
}
tSel.onchange = () => { fillCycles(); draw(); };
cSel.onchange = draw;
tSel.value = "RVNL"; fillCycles(); draw();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
