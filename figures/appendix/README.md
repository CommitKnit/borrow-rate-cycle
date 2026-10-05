# Appendix — exploratory figures

125 charts from the original research, carried over unchanged. They are the
working record: several are superseded by the regenerated figures in
`figures/`, and they do not share a common style. Kept because they show the
path the research actually took.

The figures in `figures/00…17` are the ones the write-up relies on.

| Directory | Files | What it shows | Original script |
|---|---|---|---|
| `original_cycle_charts/` | 26 | Per-cycle spread + b12 with entry/exit markers, SBICARD and RVNL, plus the original summary panels | `generate_research.py` |
| `kpittech_oi/` | 17 | Per-cycle build/peak/collapse with F1 and F2 open interest, KPITTECH | `kpittech_oi_ratio_analysis.py` |
| `ireda_oi/` | 16 | The same for IREDA | same, parameterised |
| `bdl_oi/` | 7 | The same for BDL | same, parameterised |
| `sbicard_smile/` | 16 | Implied-volatility smile and term structure through the cycle | SBICARD smile scripts |
| `roll_vs_b12/` | 6 | First version of the roll-versus-peak study, 2 tickers | `roll_vs_b12_analysis.py` |
| `spread_collapse_signals/` | 6 | Cross-ticker collapse signals by borrow tier, IV and risk reversal | `spread_collapse_signals.py` |
| `spread_swing_bt/` | 6 | Bidirectional swing variant — trades both the build and the collapse | `spread_swing_bt.py` |
| `inflection_entry_bt/` | 5 | Original output of the headline backtest | `inflection_entry_bt.py` |
| `b12_collapse_bt/` | 4 | Entry on the b12 slope flip | `b12_collapse_strategy_bt.py` |
| `b12_peak_entry_bt/` | 4 | Entry at the b12 peak | `b12_peak_entry_bt.py` |
| `b12_true_synth_calendar/` | 4 | Options synthetic replicating the futures calendar | `b12_true_synth_calendar_bt.py` |
| `oi_feature_importance/` | 4 | Which roll features predict the collapse direction | `oi_feature_importance.py` |
| `borrow_thesis/` | 3 | Early test: does the spread widen more in up cycles? | `borrow_thesis.py` |
| `nan_diagnostic/` | 1 | Missing-data heatmap across panels | `nan_diagnostic.py` |

Two figures from `spread_collapse_signals/` (`g7_hmm_state_profiles.png`,
`g8_s4_timing.png`) are deliberately excluded: they came from a 4-state Hidden
Markov phase model that this repository replaces with explicit rules. See
[../../docs/07_provenance.md](../../docs/07_provenance.md).

All generating scripts are preserved in [`../../recovered/`](../../recovered/).
