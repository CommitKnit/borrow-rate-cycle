# The Borrow-Rate Cycle

**In NSE single-stock futures, the front-month premium of hard-to-borrow stocks builds,
peaks in the final week, and collapses at expiry, in step with open interest rolling
from the front month (F1) to the next (F2).**

When a stock is hard to borrow, short sellers short the front-month future instead, so
F1 trades rich against F2. Those shorts have to roll before F1 expires. This
repository measures how the F1 − F2 spread moves as their open interest transfers,
across **135 expiry cycles in 7 NSE names (July 2024 – October 2026, 72,203
fifteen-minute bars)**, and tests whether the collapse can be traded.

![the roll drives the premium](figures/00_roll_drives_the_premium.png)

## The result

Median over hard-to-borrow cycles. Spread = (F1 − F2)/F1 in bps; x is trading sessions
to F1 expiry.

| Sessions to expiry | 15 | 10 | 6 | 4 | **3** | 2 | 1 | 0 |
|---|---|---|---|---|---|---|---|---|
| F1 − F2 spread (bps) | 44 | 58 | 80 | 81 | **92** | 84 | 84 | −9 |
| F1 share of open interest | 95% | 91% | 85% | 79% | 63% | 43% | 25% | 12% |
| F1/F2 open-interest ratio | 17.8× | 10.7× | 5.6× | 3.7× | 1.7× | 0.76× | 0.33× | 0.14× |

- **Build.** The spread nearly doubles (44 → 80 bps) while the front month still holds
  more than 80% of open interest.
- **Peak.** It tops out in the final week as open interest crosses over. Per cycle, the
  median F1/F2 ratio at the peak is **0.81×**, two sessions out.
- **Collapse.** Once the roll is done the premium has nothing left to sit on, and it
  converges to about zero at the expiry close.

**Evidence that the roll, not chance, sets the timing:**

1. **Open interest moves first.** Front-month OI turns down before the spread peaks in
   **106 of 135 cycles (79%, binomial p = 2×10⁻¹¹)**, with a median lead of 4.7
   sessions.
2. **The control is flat.** Cycles with no borrow premium sit at about −50 bps (normal
   carry) through the whole roll, with no build, peak or collapse.
3. **It is not generic mean reversion.** Into expiry the spread gives back a median
   **0.83** of what it built. Arbitrary mid-cycle peaks give back only **0.35**
   (p = 3×10⁻⁸).
4. **It holds name by name.** In extreme cycles (b12 ≥ 15%, n = 92) all seven tickers
   build into the final week and fall back at expiry. KPITTECH is the exception: it
   peaks earlier, about 6 sessions out
   ([figure 17](figures/17_roll_profile_by_ticker.png)).

The roll and the premium share the expiry calendar. Their alignment, the lead of open
interest and the flat control are what the roll mechanism predicts, but this is not a
controlled experiment ([limitations](docs/05_limitations.md)).

![phase portrait](figures/14_phase_portrait.png)

**Explore any cycle:** [`docs/explorer/index.html`](docs/explorer/index.html) is an
interactive page where you pick a ticker and expiry and compare it with the population.

## Is it tradeable?

The trade is short F1 / long F2, entered after the smoothed spread peaks and exited at
12:00 on expiry day, with 0.1% slippage per leg on entry and exit. The signal is
trailing, so there is no look-ahead. Over 113 cycles it returns **+51 bps per cycle**,
wins 64.6% of the time, and has a per-cycle Sharpe of 0.50. The edge sits where the
mechanism says it should:

| Borrow premium at peak (b12) | n | Win rate | Mean bps |
|---|---|---|---|
| None (< 5%) | 13 | 0% | −37.8 |
| Moderate (5–15%) | 28 | 61% | +10.3 |
| **Extreme (≥ 15%)** | **72** | **78%** | **+83.2** |

P&L correlates **+0.95** with how far the spread converged, and the trade is profitable
whether the stock rose or fell. The options version (long F2 call + short F1 put) is
really a synthetic long, so it does not trade the spread. Details are in
[strategy results](docs/03_strategy_results.md) and
[negative results](docs/04_negative_results.md).

## What I corrected

The original version of this research reported a *"100% win rate over 22 cycles, Sharpe
1.83"*. Four defects produced that number: a look-ahead smoother, a sample-selection
gate, an inverted slippage sign, and an explanation of a failed strategy that the data
does not support. The corrected result above is weaker and considerably more
believable. The original scripts are preserved unmodified in [`recovered/`](recovered/)
([provenance](docs/07_provenance.md), [limitations](docs/05_limitations.md)).

## Data pipeline: fetch → build → load

All market data lives in an **ArcticDB** store. The pipeline below builds it from the
broker APIs, and every analysis script reads it through one loader:

| Step | Script | What it does | Writes to ArcticDB | Needs |
|---|---|---|---|---|
| 1. Fetch | [`pipeline/fetch_ticker`](pipeline/fetch_ticker/) | Pulls every futures contract from Upstox, ranks them into the F1/F2/F3 roll panel ([`panel.py`](borrowcycle/pipeline/panel.py)), and fetches Kite spot if it is missing | `futures_contracts/{T}/{interval}/{expiry}`, `futures/{T}/{interval}`, `spot/{T}/…` | `UPSTOX_ACCESS_TOKEN`; `KITE_API_KEY` + `KITE_ACCESS_TOKEN` |
| 2. Build | [`pipeline/build_borrow_rates`](pipeline/build_borrow_rates/) | Implied borrow rates (b1…b13, b12), the F1 − F2 spread and the OI ratio ([`borrow.py`](borrowcycle/pipeline/borrow.py)) | `borrow_rates/{T}/{interval}`, `…/cycle_summary` | step 1 |
| 3. Features | [`pipeline/build_features`](pipeline/build_features/) | 25 `feat_*` columns: OI concentration, roll-flow intensity, implied vol and others ([`features.py`](borrowcycle/pipeline/features.py)) | `borrow_rates/{T}/{interval}` | step 2 |
| 4. Load | [`borrowcycle/data.py`](borrowcycle/data.py) | Joins panel, spot and borrow columns and yields one frame per expiry cycle (`iter_cycles`) | read only | steps 1–3 |
| 5. Analyse | [`scripts/01…08`](scripts/) | Results CSVs, figures, the explorer | read only | step 4 |

ArcticDB access and the rule that each value is stored exactly once live in
[`borrowcycle/pipeline/store.py`](borrowcycle/pipeline/store.py).
[`pipeline/README.md`](pipeline/README.md) has the store diagram, the library and symbol
layout, and the `--update` / `--force` / `--dry-run` flags.

```bash
pip install -e .
# 1-3: data (skips anything already stored; API tokens only needed for missing data)
python pipeline/fetch_ticker/fetch_ticker.py SBICARD RVNL KPITTECH ASTRAL BDL IREDA VOLTAS
python pipeline/build_borrow_rates/build_borrow_rates.py
python pipeline/build_features/build_features.py
# 5: analysis, figures and the explorer
make all
make test
```

The store lives in `./data_cache/` (git-ignored). To use an existing store, set
`BORROWCYCLE_ENGINE_ROOT` to the folder that contains its `data_cache/`.

- **Raw market data is not redistributed** (Upstox/Kite terms). The pipeline rebuilds
  it with your own API keys. The [`results/`](results/) CSVs hold the derived numbers
  behind every claim here.
- **Option chains are not fetched by this repo.** The `options_enriched` library comes
  from a separate backtesting engine. The roll result above (scripts 01, 02, 07 and
  figures 00, 14–17) needs only steps 1–2. The options strategies, the IV features and
  the IV results in doc 04 also need option chains.

## Limitations

- Parameters were chosen in-sample.
- 113 cycles across 7 correlated names is a smaller effective sample than it looks.
- The names were picked as known hard-to-borrow candidates, not screened from a
  universe.
- Costs cover slippage only. There are no fees, taxes, margin financing or market
  impact.
- The Sharpe is a per-cycle P&L ratio: **not annualised and not capital-adjusted**.

Read [docs/05_limitations.md](docs/05_limitations.md) before using any number here.

## Repository map

| Path | What it holds |
|---|---|
| `pipeline/` | Data steps: `fetch_ticker`, `build_borrow_rates`, `build_features` |
| `borrowcycle/pipeline/` | Their library: ArcticDB store, Upstox/Kite clients, roll panel, borrow rates, features |
| `borrowcycle/` | Analysis library: data loader, cycle detection, roll mechanics, backtest, statistics, plotting |
| `scripts/01…08` | Numbered analysis steps, one folder each with a README |
| `tests/` | Tests on throwaway stores and synthetic cycles |
| `results/` | Every number quoted here, as CSV or Markdown |
| `figures/` | The figures (00–17) |
| `docs/` | [Mechanism](docs/01_mechanism.md), [cycle anatomy](docs/02_cycle_anatomy.md), [strategy](docs/03_strategy_results.md), [negative results](docs/04_negative_results.md), [limitations](docs/05_limitations.md), [data dictionary](docs/06_data_dictionary.md), [provenance](docs/07_provenance.md), [roll mechanics](docs/08_roll_mechanics.md), `explorer/` |
| `recovered/` | The original, uncorrected scripts, kept as the historical record |

---

*Data: NSE single-stock futures (Upstox) and spot (Zerodha Kite), 15-minute bars. This
is research, not investment advice.*
