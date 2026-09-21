# The Borrow-Rate Cycle

**Build, peak, collapse: measuring the hard-to-borrow premium in NSE single-stock futures.**

When a stock is hard to borrow, short sellers stop borrowing it and short the
future instead. That demand makes the front-month future rich against the next
month, and the richness dies at expiry because settlement forces the two
contracts together. The result is a premium that builds for weeks, tops out
near expiry, and collapses — every month, in the same shape.

This repository measures that cycle across **126 expiry cycles in 7 NSE names,
July 2024 – August 2026, on 67,838 fifteen-minute bars**, and tests whether it
can be traded.

**What the data says:**

- The premium starts building a median of **26 days** before expiry and peaks
  **4 days** before it. The median cycle moves **116 bps of the front-month
  price** from trough to peak and gives back **87%** of it by settlement;
  54% of cycles give back more than 80%.
- The collapse is caused by the roll. Front-month open interest falls from a
  median of **20.4×** the next month at the spread trough to **1.0×** at the
  spread peak (paired Wilcoxon, p = 3×10⁻¹⁹), and it turns down *before* the
  spread does in **98 of 126 cycles (78%,** p = 3×10⁻¹⁰**)**, with a median
  lead of 4.6 sessions.
- This is not generic mean reversion. Anchoring the identical measurement on
  arbitrary mid-cycle spread peaks gives a median retracement of **0.41**,
  against **0.87** into expiry (p = 3×10⁻⁷).
- Trading it with a **trailing** (non-look-ahead) signal over 107 cycles
  returns a mean of **+50 bps** per cycle, win rate 64.5%, per-cycle Sharpe
  0.49 — and the win rate rises monotonically with the borrow premium:
  **0% → 61% → 78%** across the three tiers.

**What was wrong with the original research, and is corrected here:** a
look-ahead smoother, a sample-selection gate, an inverted slippage sign, and a
published explanation of a failure that the data does not support. All four are
documented in [docs/05_limitations.md](docs/05_limitations.md). Collectively
they took the headline result from *"100% win rate over 22 cycles, Sharpe
1.83"* to the numbers above. The corrected result is weaker and considerably
more believable.

![one cycle](figures/01_mechanism_single_cycle.png)

---

## 1. The mechanism

A stock becomes **hard to borrow** when short interest is high relative to
lendable inventory. Borrowing it through the Securities Lending & Borrowing
(SLB) market gets expensive, so short sellers express the view in
**single-stock futures** instead: no borrow required, and the exposure is
identical.

That demand sits in the **front month (F1)**, because that is where the
liquidity is. It does not sit in the **next month (F2)**, which continues to
trade near fair carry. So F1 becomes rich relative to F2, and the premium
widens as expiry approaches and the borrow gets scarcer.

The annualised borrow rate implied by the two futures is:

```
b12 = (F1 − F2) / F2  ×  365 / F1_dte
```

At expiry the front month converges to spot by definition. The premium has to
go — not because anyone changes their mind about the stock, but because the
contract stops existing. **The collapse is mechanical, which is why it does
not depend on which way the stock moves.**

> `b12` is used here only to *classify* cycles, never to time them. The
> `365/F1_dte` term explodes near expiry, so `b12` rises even when the raw
> spread is flat: 90% of cycles clear the conventional 5% hard-to-borrow
> threshold, which makes it nearly useless as a filter. All timing is done on
> the raw F1 − F2 spread. See [docs/01_mechanism.md](docs/01_mechanism.md).

## 2. Is the roll really what collapses it?

If short sellers rolling out of the front month drive the collapse, their open
interest should leave *before* the spread tops out. It does, in every name:

![roll](figures/02_roll_leads_peak.png)

By the time the spread peaks, front-month open interest has already fallen to
a median of **58% of its own maximum**. The roll is most of the way done
before the premium starts to fall.

## 3. The shape of the cycle, measured

Each cycle's spread is normalised by its own amplitude and aligned on its
peak, so cycles of different size are comparable. The asymmetry is the finding:
a slow grind up over weeks, then a sharp give-back over days.

![anatomy](figures/03_cycle_anatomy_eventtime.png)

| | p25 | median | p75 |
|---|---|---|---|
| Days to expiry at build start | 20 | **26** | 27 |
| Days to expiry at peak | 1 | **4** | 13 |
| Amplitude, trough to peak (bps of F1) | 51 | **116** | 261 |
| Retracement (given back ÷ built) | 0.62 | **0.87** | 1.13 |

Note what the normalised chart shows and does not show: the *shape* is
common to all three borrow tiers, but the *size* is not — median amplitude
runs 39 bps in non-hard-to-borrow cycles against 160 bps in the extreme tier.
The premium changes the amplitude of the cycle, not its anatomy.

Three qualifications, stated because they matter: the peak falls inside the
final expiry week in only **67%** of cycles, **11%** have a build trough at the
cycle boundary where it cannot be distinguished from the edge, and the
`build < peak` ordering is true by construction — which is why the placebo test
above, not the ordering, is what carries the claim.

![oi](figures/05_oi_ratio_three_moments.png)

Full detail, including the detector-robustness grid and the censoring artefact
found and fixed along the way: [docs/02_cycle_anatomy.md](docs/02_cycle_anatomy.md).

## 4. Trading it

**Entry** — the first bar after the smoothed spread peaks where slope and
acceleration are both negative. **Exit** — the last bar at or before 12:00 on
expiry day. **Costs** — 0.1% slippage per futures leg, charged on entry and
exit.

Three constructions were tested: **S1** short F1 / long F2 (futures only),
**S2** the same via options, **S3** long call on F2 + short put on F1.

| Scope | n | Win | Mean | Sharpe |
|---|---|---|---|---|
| As originally published *(2 names, options-gated, look-ahead, costs credited)* | 22 | 100% | ₹+13,429/lot | +1.83 |
| Costs charged correctly | 22 | 86% | ₹+8,674/lot | — |
| All names, no gate, look-ahead signal | 105 | 77.1% | +77.0 bps | +0.75 |
| **All names, no gate, trailing signal** | **107** | **64.5%** | **+50.3 bps** | **+0.49** |

The last row is the honest one. The tier structure is what makes it credible —
the edge concentrates exactly where the mechanism says it should:

| Borrow premium at peak | n | Win rate | Mean bps |
|---|---|---|---|
| Non-HTB (<5%) | 12 | 0% | −36.9 |
| Moderate (5–15%) | 28 | 61% | +10.3 |
| **Extreme (≥15%)** | **67** | **78%** | **+82.6** |

![equity](figures/07_equity_curve.png)

The result is the spread collapse and nothing else: S1's per-cycle PnL
correlates **+0.95** with how far the spread converged, and it is profitable
whether the underlying rose (+₹11,164) or fell (+₹13,418).

## 5. What doesn't work

- **S3 loses money, and not for the published reason.** The original
  attributed it to vega. The data disagrees: next-month implied vol *rose* in
  71% of these trades. The real problem is structural — a long call plus a
  short put is a **synthetic long**, so S3 correlates **+0.93** with the
  underlying and **−0.07** with the spread. It was never trading the spread.
- **The borrow signal is not in the options surface.** Front-month implied vol
  does not separate hard-to-borrow cycles from ordinary ones (p = 0.44), and
  put skew does not track the borrow premium (ρ = +0.14, p = 0.22) — while the
  futures spread tracks it at ρ = **+0.76** (p = 3×10⁻²⁵).
- **The strategy loses where the mechanism is absent**, which is the correct
  behaviour for a mechanism-specific trade.

[docs/04_negative_results.md](docs/04_negative_results.md)

## 6. Limitations

The four corrected defects, plus: the parameters were chosen in-sample; 107
cycles across 7 correlated names is a smaller effective sample than it looks;
the names were picked as known hard-to-borrow candidates rather than screened
from a universe; costs cover slippage only, with no fees, taxes, margin
financing or market impact; and the reported Sharpe is a per-cycle PnL ratio,
**not annualised and not capital-adjusted**.

Read [docs/05_limitations.md](docs/05_limitations.md) before using any number
here.

## 7. Reproducing this

Everything needed is in the repository — 25.8 MB of parquet, no database, no
credentials.

```bash
pip install -e .
make all
```

That regenerates every table and figure in about ten minutes.
`scripts/00_export_data.py` is the one script that cannot run from a clone: it
documents how the shipped data was extracted from the original ArcticDB store.

## 8. Repository map

| Path | What it holds |
|---|---|
| `borrowcycle/` | Library: data access, cycle detection, backtest, statistics, plotting |
| `scripts/01…06` | Numbered, runnable analysis steps |
| `data/` | 7 futures/borrow panels + 33 ATM option extracts, with `SCHEMA.md` and a checksummed `MANIFEST.json` |
| `results/` | Every number quoted above, as CSV or Markdown |
| `figures/` | The 13 figures in the write-up; `appendix/` holds 125 exploratory charts |
| `docs/` | Mechanism, anatomy, results, negative results, limitations, data dictionary, provenance |

---

*Data: NSE single-stock futures and options, 15-minute bars, via Upstox and
Zerodha Kite. This is research, not investment advice.*
