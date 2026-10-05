# What the options surface does during a borrow cycle

Measured at each cycle's spread peak, across 108 cycles with option-derived features (7 tickers).


## 1. Front-month implied vol and skew at the spread peak

| Tier | n | median ATM IV | median 25d risk reversal |
|---|---|---|---|
| NON | 11 | 0.361 | -0.0108 |
| MOD | 25 | 0.286 | -0.0102 |
| EXT | 72 | 0.316 | +0.0037 |

Mann-Whitney on ATM implied vol, extreme-HTB vs non-HTB cycles (n=72 vs 11): p = 0.063.

There is **no significant difference**. A rich borrow premium is not accompanied by a distinctive level of front-month implied volatility.

This is worth stating plainly because it is a useful negative result: the borrow signal in the futures term structure is **not** visible as an implied-volatility level in the options of the same name. Anyone hoping to detect hard-to-borrow stress from the vol surface alone would not find it here.


## 2. Does put skew track the borrow premium?

Spearman correlation between the peak borrow rate and the 25-delta risk reversal at the peak: rho = +0.09 (p = 0.423, n = 91).

The relationship is not statistically significant. The borrow premium in the futures does not reliably show up as put skew in the options.


## 3. For contrast: the futures signal is strong

The same cycles show a clear relationship between the borrow premium and the size of the spread cycle: Spearman rho = +0.77 (p = 7.80e-28, n = 135).

The information is in the futures term structure, not the option surface. That is the practical conclusion of this section.
