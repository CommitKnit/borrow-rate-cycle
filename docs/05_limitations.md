# Limitations

Read this before using any number in this repository.

The headline that started this work was *"100% win rate over 22 cycles,
Sharpe 1.83."* That number is in the repository, it reproduces exactly, and it
is wrong in four separate ways. Each is documented below with the size of its
effect. The corrected result — 107 cycles, 64.5% win rate, +50 bps mean,
per-cycle Sharpe 0.49 — is weaker and is the one to use.

---

## A. Defects found and corrected

### 1. Look-ahead bias in the entry signal

The entry rule smooths the F1−F2 spread with an **11-bar centred rolling
mean**. A centred window of width 11 averages 5 bars on each side, so the
smoothed value at any bar depends on the next 5 bars — about **75 minutes of
future information**. The signal fires "just after the peak" partly because it
can already see that the peak has happened.

Both variants are published. Switching to a trailing window, which uses only
past bars:

| | n | Win | Mean bps | Sharpe |
|---|---|---|---|---|
| Centred (look-ahead) | 105 | 77.1% | +77.0 | +0.75 |
| Trailing (implementable) | 107 | 64.5% | +50.3 | +0.49 |

**About a third of the apparent edge was look-ahead.** The tier ordering
survives, which is the reason the result is still worth reporting.

### 2. Sample selection: an options gate on a futures-only strategy

`generate_research.py` dropped any cycle where the matching option expiries
could not be loaded:

```python
if not f1_exp or not f2_exp:
    continue          # skips the whole cycle, including S1
```

S1 is a futures calendar spread. It uses no options at all. Of 33 qualifying
SBICARD/RVNL cycles, **11 were discarded** for missing data that S1 never
needed — and the surviving 22 were, by construction, the cycles with the
cleanest data.

Corrected here: option loading raises `OptionsUnavailable`, S2 and S3 become
`NaN`, and S1 is still computed. The backtest carries an assertion that would
have caught this:

```python
assert n_S1 >= n_S2   # a futures-only strategy cannot have fewer observations
```

Cycles that genuinely cannot be traded are published in a `skip_reason` column
rather than silently dropped.

### 3. Inverted slippage sign — costs were credited, not charged

The original adjusted PnL by price *deltas*, and the sign was wrong on all
four futures legs:

```python
pnl -= slip(F1e, False, True) - F1e
#      \____ = F1e*(1-0.001) - F1e = -0.001*F1e ____/
#      so:  pnl -= (-0.001*F1e)   ->   pnl += 0.001*F1e
```

Selling at a worse price should *reduce* PnL. Instead each leg **added** 0.1%
of notional. Across four legs the strategy was credited roughly 0.4% of
notional per cycle instead of being charged it — an ~80 bps swing, comparable
to the entire claimed edge.

On the original 22-cycle sample, correcting the sign alone:

| | n | Win | Mean |
|---|---|---|---|
| Costs credited (original) | 22 | 100% | ₹+13,429/lot |
| Costs charged (correct) | 22 | 86% | ₹+8,674/lot |

**₹4,755 per cycle, and the 100% win rate does not survive it.**

Fixed by filling each leg at its slipped price directly rather than adjusting
by deltas, which makes the sign impossible to get wrong.

### 4. A published explanation that the data does not support

The original attributed S3's losses to vega — the long next-month call being
crushed as the borrow collapse compressed F2 implied volatility. Measured:
F2 ATM implied volatility **rose** in 71% of these trades, and its correlation
with the S3 result is −0.44 (p = 0.21, n = 10): weak, and the wrong sign.

The actual cause is structural. A long call plus a short put is a **synthetic
long**, so S3 correlates **+0.93** with the underlying's move and **−0.07**
with the spread collapse. It was never trading the spread.

See [04_negative_results.md](04_negative_results.md).

---

## B. Limitations that remain

### 5. The parameters were chosen in-sample

`SMOOTH = 11`, the 5% tier boundary, the slope-and-acceleration entry rule and
the 12:00 expiry-day exit were all selected after looking at these cycles.
There is no holdout period and no walk-forward test. The robustness grid in
[02_cycle_anatomy.md](02_cycle_anatomy.md) shows the *measurements* are stable
across smoothing choices, but that is not the same as out-of-sample validation
of the *strategy*.

### 6. The cycles are not independent

107 cycles sounds like a large sample. It is 7 names over 25 months, sharing
market direction, the same monthly roll calendar, and in several cases the
same sector. Cycles that settle in the same month are exposed to the same
conditions. The effective sample size is materially smaller than 107, so
bootstrap confidence intervals and a sign test are reported alongside the
point estimates in `results/summary_stats.md`.

### 7. The names are a case study, not a universe

SBICARD, RVNL, KPITTECH, ASTRAL, BDL, IREDA and VOLTAS were chosen because
they were known to be interesting hard-to-borrow candidates. No screen was run
over the NSE F&O list. Selection bias is therefore real and unquantified — a
proper study would define the universe first and measure what the screen picks
up. Survivorship is limited (all 7 traded throughout), but that is the lesser
problem.

### 8. The hard-to-borrow filter barely filters

`peak_b12 >= 0.05` admits **90% of all cycles**, because the `365/F1_dte` term
inflates `b12` mechanically near expiry: at one day to expiry, almost any
positive spread annualises above 5%. The binary filter is close to meaningless;
the tier split is the economically informative cut, and the non-HTB tier is
better treated as a control group than as an exclusion.

### 9. The cost model covers slippage only

Charged: 0.1% per futures leg, 0.5% per options leg, on entry and exit.

Not modelled: exchange and clearing fees, STT and stamp duty, SPAN + exposure
margin and the cost of financing it (a calendar spread earns margin benefit,
but not a full offset), market impact beyond the fixed slippage, and the fact
that a 12:00 exit on expiry day sits in the least liquid hours of a dying
contract.

**The Sharpe figures are per-cycle PnL ratios — mean divided by standard
deviation across cycles. They are not annualised and not capital-adjusted,
because this study has no capital base.** They are comparable to each other
and to nothing else.

### 10. Data quality

- **BDL** ships 24 columns instead of 52 — no `feat_*` features — so it appears
  in spread and open-interest statistics but is excluded from every
  implied-volatility and roll-feature result.
- **7 of 126 cycles** have fewer than 5 bars inside the expiry week
  (`gap_flag`).
- **11% of cycles** have a build trough at the cycle boundary, where it cannot
  be distinguished from the edge of the data (`build_censored`).
- **33% of cycles** peak *outside* the final expiry week, so "the premium peaks
  in expiry week" is a tendency, not a rule.
- F1 and F2 closes are forward-filled with no stale-quote detection.

### 11. Two day-count conventions coexist

`b12` annualises with **calendar days / 365** (`F1_dte`). The options layer of
the source pipeline prices on **trading days**, with `F1_dte_star` decrementing
by 15/375 per bar. The Black-76 fallback in this repository uses `dte/365` for
consistency with `b12`.

Consequence: `b12` and option-implied carry are **not directly comparable**,
and any attempt to arbitrage one against the other would be comparing different
units. Since the results here never mix the two, this is a documentation issue
rather than a live error — but it would become one immediately in any
extension.

### 12. The detector is a rule, not a model

BUILD is defined as the argmin before the peak, so `build < peak` is true by
construction and the 94% "phases ordered" figure proves nothing on its own.

The claim rests instead on the placebo test: the same measurement anchored on
arbitrary mid-cycle spread peaks retraces a median of **0.41**, against
**0.87** into expiry (Mann-Whitney p = 3×10⁻⁷). That comparison is what
distinguishes expiry-forced convergence from ordinary mean reversion.

### 13. Lot sizes are unknown for 5 of 7 names

Only SBICARD (800) and RVNL (1525) lot sizes appear anywhere in the source
research, and the panel has no lot-size column. They are deliberately **not
guessed**. Wide-scope results are therefore reported in basis points of F1,
never in rupees per lot; only the two-name scope carries rupee figures.

---

## C. What would be needed to trust this as a strategy

1. Define a universe first — screen the full NSE F&O list for borrow premium —
   and re-measure, so selection bias is quantified rather than assumed away.
2. Walk-forward the parameters, or fix them on the first half and test on the
   second.
3. Model margin, financing and taxes, and express the result as a return on
   capital at risk rather than PnL per cycle.
4. Measure realised fills against the assumed slippage, particularly for the
   expiry-day exit.
5. Extend the sample beyond 25 months so cycles are not dominated by one
   market regime.
