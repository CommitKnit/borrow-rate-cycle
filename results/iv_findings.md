# What the options surface does during a borrow cycle

Measured at each cycle's spread peak, across 96 cycles with option-derived features (6 tickers; BDL ships without them).


## 1. Front-month implied vol and skew at the spread peak

| Tier | n | median ATM IV | median 25d risk reversal |
|---|---|---|---|
| NON | 5 | 0.335 | -0.0244 |
| MOD | 23 | 0.293 | -0.0114 |
| EXT | 68 | 0.316 | -0.0000 |

Mann-Whitney on ATM implied vol, extreme-HTB vs non-HTB cycles (n=68 vs 5): p = 0.437.

There is **no significant difference**. A rich borrow premium is not accompanied by a distinctive level of front-month implied volatility.

This is worth stating plainly because it is a useful negative result: the borrow signal in the futures term structure is **not** visible as an implied-volatility level in the options of the same name. Anyone hoping to detect hard-to-borrow stress from the vol surface alone would not find it here.


## 2. Does put skew track the borrow premium?

Spearman correlation between the peak borrow rate and the 25-delta risk reversal at the peak: rho = +0.14 (p = 0.224, n = 79).

The relationship is not statistically significant. The borrow premium in the futures does not reliably show up as put skew in the options.


## 3. For contrast: the futures signal is strong

The same cycles show a clear relationship between the borrow premium and the size of the spread cycle: Spearman rho = +0.76 (p = 2.80e-25, n = 126).

The information is in the futures term structure, not the option surface. That is the practical conclusion of this section.
