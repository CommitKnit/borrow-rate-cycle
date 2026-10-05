# The mechanism

## Where the borrow shows up: first against spot, then between F1 and F2

To short a stock outright you must borrow it. In India that happens through
the **Securities Lending & Borrowing (SLB)** market, where lenders post
inventory and borrowers pay a fee. When short interest is high relative to
lendable inventory the stock becomes **hard to borrow (HTB)**: the fee rises,
and above some level the borrow stops being worth arranging at all.

Single-stock futures are the alternative. A short future gives the same
exposure with no borrow, no recall risk and no fee, only margin. So when a
name becomes hard to borrow, short-selling demand migrates into the futures.

**The borrow fee acts as a continuous dividend.** Whoever holds the stock can lend
it out and earn the fee `b`; a future forgoes that income, so it is priced net of
it. Cost of carry with a borrow yield is

```
F = S·exp((r − b)·τ)
```

When `b` exceeds the funding rate `r`, the future trades **below** spot, and the
longer the contract, the deeper the discount. Inverting gives the implied borrow
rates:

```
b1  = r − ln(F1 / S)  / τ1                 r = 6.25%
b12 = r − ln(F2 / F1) / (τ2 − τ1)
```

where τ is each contract's time to expiry in years, computed from a fractional DTE
that ticks down through the day: `F{n}_dte_star = (F{n}_dte + 1) − i/25` for the
`i`-th 15-minute bar of the session (calendar days, /365). Rates are floored at 0.

The premium then moves through two stages over each expiry cycle.

**1. Early in the cycle, the borrow sits between spot and F1.** With weeks to go,
the front month carries the borrow against spot. In extreme cycles (peak b12 ≥ 15%)
spot trades a median 13–14 bps **above** F1 from 20 to 10 sessions out, and the
implied `b1` is 8–9%, above `r`. In the no-premium control the opposite holds: F1
sits 26–58 bps above spot, which is normal carry.

**2. Into expiry, F1 converges to spot and the borrow moves to F1–F2.** As τ1
shrinks, F1 can embed less and less of the borrow and converges to spot. Spot − F1
falls to about 10 bps four sessions out and to 0 three sessions out. F2 still carries
a month of borrow, and the shorts rolling out of F1 sell into it. So the F1 − F2
spread widens through the roll, from 55 bps at 20 sessions out to 122 at 4 and 138
at 1, and **b12 rises with it, from 13% to 23%**. At settlement F1 *is* spot, F2
becomes the new front month, and the premium resets (see the next section).

Median values in extreme cycles (from [`results/roll_mechanics.md`](../results/roll_mechanics.md) §7):

| sessions to expiry | 20 | 10 | 6 | 4 | 3 | 2 | 1 |
|---|---|---|---|---|---|---|---|
| spot − F1 (bps of spot) | 14 | 14 | 9 | 10 | 0 | −11 | −11 |
| F1 − F2 (bps of F1) | 55 | 82 | 114 | 122 | 124 | 128 | 138 |
| b1 (%) | 8.1 | 9.2 | 10.5 | 12.6 | 6.2 | – | – |
| b12 (%) | 13.2 | 15.4 | 20.0 | 20.8 | 20.4 | 21.7 | 22.6 |

Inside the last three sessions τ1 is close to zero, so `b1` is dominated by noise
and floored at 0. It is left blank there.

**Caveats.** The spot leg is a median and noisy cycle by cycle. In extreme cycles
spot is above F1 on 55% of sessions 5–20 out (spot − F1 interquartile range −18 to
+69 bps), and the share ranges from 37% (ASTRAL) to 84% (RVNL). Moderate cycles
(peak b12 5–15%) show neither stage clearly: they sit close to normal carry for most
of the cycle, with F1 above spot and a median b12 of 2–6%.

## Why it has to collapse

At expiry the front-month future converges to spot by definition — the
contract settles. Whatever premium it carried must go to zero on a known date.

This is the part that makes the cycle tradeable rather than merely
interesting. The convergence is **not a forecast about the stock**. It does not
require the borrow to ease, short sellers to capitulate, or the price to move
in any particular direction. It requires only that the calendar advances.

Two testable consequences follow, and both are examined in
[02_cycle_anatomy.md](02_cycle_anatomy.md):

1. **The roll should lead the collapse.** Short sellers who want to stay short
   must move from F1 to F2 before F1 expires. If they drive the premium, their
   open interest should leave the front month *before* the spread tops out.
   Measured: F1 open interest peaks first in 79% of cycles, with a median lead
   of 4.7 sessions, and has already fallen to 56% of its own maximum by the
   time the spread peaks. Measured on the roll itself, the spread is widest
   while the F1/F2 open-interest ratio is between 4× and 1×, and converges once
   the front month is the minority ([08_roll_mechanics.md](08_roll_mechanics.md)).

2. **The trade should be direction-neutral.** A convergence forced by expiry
   should pay whether the stock rises or falls. Measured: the futures calendar
   returned +₹11,164 per lot when the underlying rose and +₹13,418 when it
   fell, and its per-cycle result correlates +0.95 with how far the spread
   converged.

## Why `b12` is a classifier and never a timer

`b12` is the natural way to *describe* the borrow premium, because annualising
makes cycles comparable across different times to expiry. It is a poor way to
*time* anything: it depends on both legs' time to expiry, while the quantity that
is actually traded, and that converges at settlement, is the raw spread.

Two consequences shape every result in this repository:

- **All timing is done on the raw F1 − F2 spread.** The entry rule, the peak and
  the build/collapse moments are located on the raw spread; figures report it in
  bps of F1.
- **The conventional 5% HTB threshold barely filters anything.** Whenever F1 ≥ F2,
  `ln(F2/F1) ≤ 0`, so `b12 ≥ r = 6.25%`, which is already above 5%. A cycle clears
  the threshold as soon as its smoothed front month trades above the next month.
  F1 trades above F2 on 89% of bars in these pre-selected names, so **90% of the
  135 cycles clear it**. The binary filter is close to meaningless. The informative
  cut is the tier split (non-HTB <5%, moderate 5–15%, extreme ≥15% at the cycle's
  peak), which does separate the data: median amplitude is 38 / 63 / 168 bps across
  the three tiers.

`b12` does not explode near expiry: `τ2 − τ1` stays close to one month. Its median
is 10.1% with 15 or more days to expiry and 12.7% inside the final five days.

## A note on day counts

| Quantity | Convention | Used for |
|---|---|---|
| `F1_dte` | whole calendar days to expiry | cycle timing in doc 02, the anatomy tables |
| `F1_dte_star` | calendar days plus the fraction of the session left, ticking down 1/25 per 15-minute bar | τ in `b1…b13` |
| sessions to expiry | trading sessions, 0 = expiry day | the roll-mechanics event axis (doc 08) |

Sessions to expiry is the right axis for pooling cycles. Monthly expiry moved from
the last Thursday to the last Tuesday in September 2025, so a given calendar DTE lands
on different weekdays in different cycles. See [05_limitations.md](05_limitations.md),
item 11.
