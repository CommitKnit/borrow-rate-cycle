# The mechanism

## Why a hard-to-borrow stock has a rich front-month future

To short a stock outright you must borrow it. In India that happens through
the **Securities Lending & Borrowing (SLB)** market, where lenders post
inventory and borrowers pay a fee. When short interest is high relative to
lendable inventory the stock becomes **hard to borrow (HTB)**: the fee rises,
and above some level the borrow stops being worth arranging at all.

Single-stock futures are the alternative. A short future gives the same
exposure with no borrow, no recall risk and no fee — only margin. So when a
name becomes hard to borrow, short-selling demand migrates into the futures.

That demand is concentrated in the **front month (F1)**, because that is where
the volume and open interest sit. The **next month (F2)** keeps trading near
fair carry. Persistent selling pressure in F1 does not push F1 *down* relative
to F2 — the cost of the scarce borrow is embedded in the futures basis, and F1
trades **rich** to F2, since the front month is where the borrow scarcity
binds.

The annualised borrow rate implied between the two contracts follows from cost of
carry, `F = S·exp((r − b)·τ)`:

```
b12 = r − ln(F2 / F1) / (τ2 − τ1)        r = 6.25%
```

where τ is each contract's time to expiry in years, computed from a fractional DTE
that ticks down through the day: `F{n}_dte_star = (F{n}_dte + 1) − i/25` for the
`i`-th 15-minute bar of the session (calendar days, /365). Rates are floored at 0.
Against spot, `b1 = r − ln(F1/S)/τ1`.

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
