# Roll mechanics: the premium against the open-interest transfer

133 cycles, 7 tickers, end-of-session values on an axis of trading sessions to F1 expiry (0 = expiry day). Spread = (F1 − F2)/F1 in bps; *normalised* spread = 0 at the cycle's build trough, 1 at its peak (smoothed).

## 1. The premium by open-interest ratio

| F1/F2 OI ratio | median spread (bps) | IQR | median normalised | median sessions left | bars |
|---|---|---|---|---|---|
| 0.25-0.5 | 36 | -47 – 149 | 0.42 | 1 | 112 |
| 0.5-1 | 63 | -22 – 200 | 0.45 | 2 | 112 |
| 1-2 | 97 | 1 – 242 | 0.42 | 3 | 119 |
| 2-4 | 140 | 57 – 232 | 0.34 | 5 | 232 |
| 4-8 | 84 | 16 – 172 | 0.24 | 8 | 494 |
| 8-16 | 38 | -24 – 123 | 0.21 | 12 | 605 |
| <0.25 | 54 | -49 – 205 | 0.14 | 0 | 120 |
| >16 | -8 | -40 – 51 | 0.22 | 16 | 738 |

The median spread is highest in the **2-4×** bin and falls away on both sides.

## 2. Where each cycle peaks

- F1/F2 OI ratio at the spread peak: median **0.96×** (IQR 0.34 – 4.47); front-month share of OI at the peak: median **55%**.
- Hard-to-borrow cycles only (MOD + EXT, n=120): OI ratio at the peak median **0.81×**, front-month share **49%**, peak **2** sessions before expiry.
- 32% of all cycles peak while the ratio is between 0.5× and 4× (n=42/133); the rest mostly peak in the final session, after it.
- Peak: median **2** sessions before expiry. Roll midpoint (ratio first < 1): median **2** sessions before expiry (reached in 123/133 cycles).
- Peak minus midpoint: median +0 sessions, mean +3.7 (95% CI +2.3 to +5.3); the peak comes at or before the midpoint in 63% of cycles (sign test p = 0.32).

## 3. How much of the premium is left as the roll progresses

All cycles. Normalised spread on the first session the OI ratio drops below each level (1.0 = the cycle's peak, 0 = its build trough).

| ratio first below | median normalised spread | IQR | median sessions left | cycles |
|---|---|---|---|---|
| 4× | 0.38 | 0.17 – 0.63 | 4 | 123 |
| 2× | 0.41 | 0.16 – 0.65 | 3 | 123 |
| 1× | 0.49 | 0.16 – 0.76 | 2 | 123 |
| 0.5× | 0.47 | -0.15 – 0.84 | 1 | 121 |

Of the give-back from the peak to the expiry close, a median **44%** happens after the front month has become the minority (n=114).

## 4. By borrow tier

| tier | cycles | median amplitude (bps) | OI ratio at peak | peak sessions left | midpoint sessions left |
|---|---|---|---|---|---|
| EXT | 92 | 164 | 0.62 | 2 | 2 |
| MOD | 28 | 66 | 3.14 | 4 | 2 |
| NON | 13 | 36 | 16.35 | 9 | 2 |

## 5. Median profile, last 15 sessions

| sessions left | spread (bps) | normalised | F1 share of OI | F1/F2 ratio |
|---|---|---|---|---|
| 15 | 44 | 0.18 | 95% | 17.79 |
| 14 | 38 | 0.21 | 94% | 15.24 |
| 13 | 44 | 0.21 | 93% | 13.68 |
| 12 | 48 | 0.16 | 92% | 12.16 |
| 11 | 49 | 0.22 | 92% | 11.61 |
| 10 | 58 | 0.23 | 91% | 10.71 |
| 9 | 68 | 0.29 | 90% | 8.79 |
| 8 | 66 | 0.28 | 88% | 7.40 |
| 7 | 67 | 0.30 | 87% | 6.59 |
| 6 | 80 | 0.33 | 85% | 5.59 |
| 5 | 76 | 0.34 | 83% | 4.72 |
| 4 | 81 | 0.39 | 79% | 3.74 |
| 3 | 92 | 0.43 | 63% | 1.69 |
| 2 | 84 | 0.45 | 43% | 0.76 |
| 1 | 84 | 0.48 | 25% | 0.33 |
| 0 | -9 | -0.03 | 12% | 0.14 |
