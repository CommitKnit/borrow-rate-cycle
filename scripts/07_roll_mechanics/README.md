# 07_roll_mechanics

## What it does
Measures the F1 − F2 premium against the open-interest roll itself. It answers when the
premium builds, peaks and collapses in terms of **how far the roll has progressed**:
the share of open interest still in the front month and the F1/F2 open-interest ratio.
It also checks that cycles with no borrow premium show none of this.

## Inputs
All reads go through `borrowcycle/data.py`, which reads the backtesting engine's
ArcticDB: the joined 15-minute borrow panel (`F1_close F2_close F1_oi F2_oi F1_dte b12
cycle_id`). The helpers live in `borrowcycle/roll.py`.

## Outputs
| File | Content |
|---|---|
| `results/roll_sessions.csv` | one row per cycle-session (end of day): sessions to expiry, spread (bps and normalised), F1 share of OI, F1/F2 ratio, tier |
| `results/roll_cycles.csv` | one row per cycle: OI ratio and session at the spread peak, roll midpoint, spread left at each ratio milestone |
| `results/roll_profile_by_session.csv` | median and IQR per session to expiry: all cycles, hard-to-borrow vs not, by tier, by ticker |
| `results/spread_by_oi_ratio.csv` | spread by F1/F2 OI-ratio bin, same groupings |
| `results/roll_midpoint_event.csv` | spread and OI share aligned on the roll midpoint |
| `results/roll_mechanics.md` | the threshold statistics quoted in the README and `docs/08_roll_mechanics.md` |

Figures 00 and 14–17 are drawn from these files by `06_make_figures`.

## How it works
1. **Event time = trading sessions to F1 expiry** (0 = expiry day), counted on each cycle's
   own trading dates. Calendar days to expiry are not used: NSE moved monthly expiry from
   Thursday to Tuesday in September 2025, so a given calendar DTE falls on different
   weekdays in different cycles, and pooling on it creates a fake weekly pattern.
2. Each session's last bar gives `spread_bps = (F1 − F2)/F1 × 10⁴`, `oi_share_f1 =
   F1_oi/(F1_oi + F2_oi)` and `oi_ratio = F1_oi/F2_oi`.
3. **Normalised spread:** 0 at the cycle's build trough, 1 at its peak (smoothed, as in
   01), so cycles of different size can be compared.
4. **Roll midpoint:** the first session where `oi_ratio < 1`, i.e. the front month holds
   less open interest than the next month.
5. **Groupings:** hard-to-borrow (peak `b12` ≥ 5%, tiers MOD + EXT) against the control
   (NON); by tier; by ticker.
6. **Statistics:** the OI ratio and session at each cycle's spread peak, peak vs midpoint
   timing (bootstrap CI, sign test), the spread left at the first session below 4×, 2×,
   1× and 0.5×, and the share of the give-back that happens after the midpoint.

## Usage
```bash
python scripts/07_roll_mechanics/07_roll_mechanics.py
python scripts/06_make_figures/06_make_figures.py     # draws figures 00, 14-17 from its outputs
```

## Re-running / what gets skipped
Deterministic: it rewrites its outputs from whatever is in the store. Nothing is written
to ArcticDB.

## Caveats
* Open interest is end-of-bar, and the analysis is end-of-session, so moves within a
  day are not resolved.
* Ratio bins pool bars from different cycles and sessions. The per-cycle statistics in
  section 2 of `roll_mechanics.md` are the cycle-level check.
* The control group (no borrow premium) is small, about a dozen cycles.
* The roll and the premium both follow the expiry calendar. Their alignment is
  consistent with the roll driving the premium, but not proof of it.
