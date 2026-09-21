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

The annualised rate implied by the two contracts is:

```
b12 = (F1 − F2) / F2  ×  365 / F1_dte
```

where `F1_dte` is calendar days to front-month expiry.

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
   Measured: F1 open interest peaks first in 78% of cycles, with a median lead
   of 4.6 sessions, and has already fallen to 58% of its own maximum by the
   time the spread peaks.

2. **The trade should be direction-neutral.** A convergence forced by expiry
   should pay whether the stock rises or falls. Measured: the futures calendar
   returned +₹11,164 per lot when the underlying rose and +₹13,418 when it
   fell, and its per-cycle result correlates +0.95 with how far the spread
   converged.

## Why `b12` is a classifier and never a timer

`b12` is the natural way to *describe* the borrow premium, because annualising
makes cycles comparable across different times to expiry. It is a poor way to
*time* anything.

The problem is the `365 / F1_dte` term. As expiry approaches, the denominator
shrinks toward zero, so `b12` rises even when the raw spread is flat or
falling. Its slope and acceleration near expiry mostly measure the passage of
time, not the behaviour of the premium.

Two consequences shape every result in this repository:

- **All timing is done on the raw F1 − F2 spread**, in rupees per share. The
  entry rule, the peak, and the build/collapse moments are all located on the
  raw spread.
- **The conventional 5% HTB threshold barely filters anything.** Measured
  across all 126 cycles, **90% clear it** — because at one day to expiry
  almost any positive spread annualises above 5%. The binary filter is close
  to meaningless. The informative cut is the tier split (non-HTB <5%, moderate
  5–15%, extreme ≥15% at the cycle's peak), which does separate the data:
  median amplitude runs 39 / 63 / 160 bps across the three tiers.

## A note on day counts

Two conventions appear in the source pipeline and they are not interchangeable:

| Quantity | Convention | Used for |
|---|---|---|
| `F1_dte` | calendar days | `b12`, cycle timing, everything in this repo |
| `F1_dte_star` | trading days, decrementing 15/375 per bar | the source repo's option pricing |

`b12` and option-implied carry are therefore **not directly comparable**.
Nothing here mixes them, but any extension that did would be comparing
different units. See [05_limitations.md](05_limitations.md), item 11.
