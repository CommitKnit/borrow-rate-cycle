# 08_interactive_explorer

## What it does
Builds `docs/explorer/index.html`, a single self-contained web page for exploring every
expiry cycle. Pick a ticker and a cycle to see its F1 − F2 spread and its open-interest
transfer against the population of hard-to-borrow cycles, its path on the
spread-vs-OI-ratio chart, and a stats card.

## Inputs
| Source | Used for |
|---|---|
| `borrowcycle/data.py` (engine ArcticDB) | hourly spread and F1 share of OI for each cycle |
| `results/roll_sessions.csv`, `results/roll_cycles.csv` (script 07) | population bands, OI ratio at peak, roll midpoint |
| `results/cycle_moments.csv` (script 01) | amplitude, retracement, peak b12 |
| `results/pnl_per_cycle.csv` (script 03) | S1 calendar-trade result (trailing signal) |

## Outputs
`docs/explorer/index.html` (about 0.4 MB). plotly.js is loaded from the jsDelivr CDN at
the version bundled with the installed `plotly` package, so only the data is embedded.

## How it works
1. For each cycle, take the last bar of each hour over the final 20 sessions and place
   it on a sessions-to-expiry axis (end of session = −sessions left).
2. Compute the population curves: the median and middle 50% of the spread, and the
   median F1 share of OI, for hard-to-borrow cycles and for the no-premium control. Plus
   the binned spread-vs-OI-ratio median (the same bins as figure 14).
3. Write everything as one JSON blob into an HTML template. A small script redraws the
   three charts and the stats card when the ticker or cycle changes.

## Usage
```bash
python scripts/08_interactive_explorer/08_interactive_explorer.py    # after 01, 03 and 07
python -m http.server 8000      # then open http://localhost:8000/docs/explorer/
```

To publish it, enable GitHub Pages on the repository with source `main` → `/docs`; the
page is then served at `https://<user>.github.io/<repo>/explorer/`.

## Re-running / what gets skipped
Rewrites the HTML file each run; no ArcticDB writes.

## Caveats
* It needs an internet connection the first time it loads plotly.js from the CDN.
* Hourly sampling is for display; all statistics come from the scripts' results.
