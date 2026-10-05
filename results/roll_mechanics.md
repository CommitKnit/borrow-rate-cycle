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

## 5. Median profile of hard-to-borrow cycles (MOD + EXT), last 15 sessions

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

## 6. By ticker, extreme cycles only (EXT, b12 ≥ 15%)

Median spread (bps) by sessions to expiry. Restricting to EXT keeps a name's no-premium cycles from diluting its profile. Peaks are in sessions to expiry.

| ticker | EXT cycles | 10 | 6 | 4 | 3 | 2 | 1 | 0 | peak of median profile | median per-cycle peak |
|---|---|---|---|---|---|---|---|---|---|---|
| SBICARD | 17 | 85 | 114 | 122 | 119 | 135 | 106 | 81 | 2 | 2 |
| RVNL | 12 | 202 | 217 | 222 | 217 | 284 | 306 | 211 | 1 | 1 |
| KPITTECH | 11 | 20 | 51 | 43 | 20 | 11 | 28 | -49 | 6 | 4 |
| ASTRAL | 11 | 66 | 74 | 66 | 59 | 124 | 114 | 35 | 2 | 2 |
| BDL | 9 | 73 | 99 | 102 | 110 | 123 | 220 | -53 | 1 | 1 |
| IREDA | 19 | 97 | 145 | 186 | 226 | 253 | 228 | 63 | 2 | 1 |
| VOLTAS | 13 | 73 | 91 | 108 | 105 | 81 | 72 | 61 | 4 | 1 |

## 7. Where the borrow sits: spot vs F1, then F1 vs F2

Median end-of-session values. spot − F1 and spot − F2 in bps of spot (positive = the future trades below spot); b1, b2, b12 annualised in % (r = 6.25%). Near expiry τ1 → 0 and b1 is floored at 0, so read it only well before expiry.

**Extreme cycles (EXT)**

| sessions to expiry | 20 | 15 | 10 | 6 | 4 | 3 | 2 | 1 | 0 |
|---|---|---|---|---|---|---|---|---|---|
| spot − F1 (bps) | 14 | 13 | 14 | 9 | 10 | 0 | -11 | -11 | 13 |
| spot − F2 (bps) | 52 | 96 | 106 | 121 | 141 | 126 | 130 | 143 | 43 |
| F1 − F2 (bps of F1) | 55 | 80 | 82 | 114 | 122 | 124 | 128 | 138 | 43 |
| b1 (%) | 8.1 | 8.4 | 9.2 | 10.5 | 12.6 | 6.2 | 0.0 | 0.0 | – |
| b2 (%) | 9.6 | 13.1 | 15.0 | 17.7 | 20.6 | 19.1 | 19.9 | 21.8 | 11.2 |
| b12 (%) | 13.2 | 14.8 | 15.4 | 20.0 | 20.8 | 20.4 | 21.7 | 22.6 | 11.9 |

**Moderate cycles (MOD)**

| sessions to expiry | 20 | 15 | 10 | 6 | 4 | 3 | 2 | 1 | 0 |
|---|---|---|---|---|---|---|---|---|---|
| spot − F1 (bps) | -36 | -22 | -15 | -23 | -11 | -6 | -11 | 7 | 12 |
| spot − F2 (bps) | -49 | -56 | -42 | -51 | -44 | -22 | -42 | -24 | -48 |
| F1 − F2 (bps of F1) | -6 | -27 | -23 | -27 | -33 | -25 | -26 | -43 | -62 |
| b1 (%) | 1.6 | 2.7 | 2.7 | 0.0 | 0.4 | 0.0 | 0.0 | 33.6 | – |
| b2 (%) | 3.2 | 2.4 | 2.8 | 1.3 | 1.8 | 3.8 | 2.1 | 3.7 | 0.6 |
| b12 (%) | 5.5 | 3.2 | 3.6 | 3.0 | 2.3 | 3.3 | 3.5 | 1.4 | 0.0 |

**No borrow premium (NON, control)**

| sessions to expiry | 20 | 15 | 10 | 6 | 4 | 3 | 2 | 1 | 0 |
|---|---|---|---|---|---|---|---|---|---|
| spot − F1 (bps) | -47 | -58 | -26 | -19 | -4 | -13 | -4 | -8 | 20 |
| spot − F2 (bps) | -95 | -104 | -79 | -75 | -54 | -62 | -51 | -62 | -51 |
| F1 − F2 (bps of F1) | -50 | -49 | -51 | -56 | -52 | -40 | -49 | -51 | -72 |
| b1 (%) | 0.2 | 0.0 | 0.3 | 0.0 | 4.1 | 0.0 | 1.9 | 0.0 | – |
| b2 (%) | 0.1 | 0.0 | 0.0 | 0.0 | 0.6 | 0.1 | 0.4 | 0.0 | 0.2 |
| b12 (%) | 0.0 | 0.0 | 0.0 | 0.1 | 0.0 | 1.2 | 0.4 | 0.0 | 0.0 |

Extreme cycles, 5–20 sessions out: spot is above F1 on 55% of sessions; spot − F1 IQR -18 to 69 bps.

