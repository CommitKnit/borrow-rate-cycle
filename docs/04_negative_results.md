# Negative results

Results that did not work, and one published explanation that turned out to be wrong.


## 1. The options expression of a correct view still loses

S3 buys an ATM call on the next month and sells an ATM put on the front month. Directionally it looks like the same view as the futures calendar. It loses money anyway.

| Strategy | n | Win rate | Mean (Rs/lot) | Cumulative |
|---|---|---|---|---|
| S1 | 22 | 100% | Rs+13,429 | Rs+295,447 |
| S2 | 9 | 100% | Rs+9,615 | Rs+86,535 |
| S3 | 10 | 40% | Rs-2,975 | Rs-29,750 |

### The stated reason (vega) is not supported by the data

The original write-up attributed the S3 losses to vega: the long next-month call being crushed as the borrow collapse compressed F2 implied volatility. Measured over these trades, that is not what happened. F2 ATM implied volatility *rose* in **71%** of cycles (median change +0.008 vol points, n=14), and its correlation with the S3 result is -0.44 (p = 0.21, n = 10) -- weak, and of the opposite sign to the vega story.


### The actual reason: S3 is a synthetic long, not a spread

A long call plus a short put is a synthetic long position. Because S3 buys a call and sells a put at near-identical strikes, its delta exposure to the underlying does not cancel -- it compounds. The trade is a directional bet wearing the costume of a spread trade.

| Exposure | correlation with S3 | correlation with S1 |
|---|---|---|
| Move in the underlying | **+0.93** (p=0.0001) | - |
| Collapse of the F1-F2 spread | -0.07 (p=0.85) | **+0.95** (p=0.0000) |

S3 tracks the stock almost perfectly and the spread not at all. Split by which way the underlying went:

| Underlying | n | mean S3 | mean S1 |
|---|---|---|---|
| rose | 7 | Rs+4,867 | Rs+11,164 |
| fell | 3 | Rs-21,272 | Rs+13,418 |

S1 is profitable in both directions, which is exactly what a convergence trade forced by expiry should do. S3 is profitable only when the stock happens to rise. Its losses are not evidence against the thesis -- they are evidence that S3 was never testing the thesis.

n = 10 for these correlations, so the magnitude is uncertain even though the direction is clear.


## 2. The edge vanishes where the mechanism is absent

Cycles whose borrow premium never exceeds 5% annualised are a natural control group: the mechanism says there is nothing to harvest there.

| Tier | n | Win rate | Mean bps |
|---|---|---|---|
| NON | 13 | 0% | -37.8 |
| MOD | 28 | 61% | +10.3 |
| EXT | 72 | 78% | +83.2 |

In the 13 control cycles the strategy wins 0% of the time and loses an average of 38 bps. That is the correct outcome: the trade is a bet on a specific mechanism and should not pay when the mechanism is absent. With n=13 this is a consistency check, not a statistical test.


## 3. What this rules out

- **Not a directional bet.** S1 correlates +0.95 with the spread collapse, and is profitable whether the underlying rose or fell.
- **Not generic mean reversion.** An identical measurement anchored on arbitrary mid-cycle spread peaks retraces a median of 0.41, against 0.87 into expiry (see `anatomy_summary.md`, section 5).