# Provenance

Where this research came from, and how its source code was recovered.

## The original work

This repository is a rebuild of research that lived in a private backtesting
engine as a mix of committed output and uncommitted scripts. The narrative
survived as `htb_stocks/README.md`, the results as a 22-row CSV, and about 90
charts as committed PNGs.

**None of the code that produced any of it was ever committed to any branch.**

## Recovery

The generating scripts existed only as **dangling git blobs** — objects written
into `.git/objects` by an auto-stash whose reflog entry had since been dropped.
They were unreachable from every branch, tag and stash, and a single `git gc`
would have removed them permanently. The repository had 643 loose objects and
no packs, meaning no garbage collection had yet run.

Recovery was by content hash:

```bash
git cat-file -s <sha>          # verify the object exists and its size
git cat-file -p <sha> > recovered/<name>.py
```

All 17 objects were recovered at byte counts matching their originals exactly,
and every `.py` compiles. They are preserved unmodified in
[`recovered/`](../recovered/) as the historical record; the library in
`borrowcycle/` is a port of them, not a copy.

| Object | SHA | Bytes |
|---|---|---|
| `inflection_entry_bt.py` — the headline backtest | `8ca21ad6645ad5edf27ac40a5a027e705597e952` | 31,501 |
| `kpittech_oi_ratio_analysis.py` — the 3-moment detector | `039e28084adf9f519808315fffedf38d36356093` | 19,514 |
| `roll_vs_b12_analysis.py` | `4a5dbe9465f1c777ca7d48c6fadcbe52eca7d511` | 19,779 |
| `spread_collapse_signals.py` | `a9e6171066ebfd4ce43a4feba184406bf27fa042` | 41,712 |
| `oi_feature_importance.py` | `962d4c207bd87fb0d7d2e698ed56399d047925a1` | 33,365 |
| `spread_swing_bt.py` | `8901323deb66a405eeaf339135f41bf27de90739` | 31,427 |
| `b12_collapse_strategy_bt.py` | `1c99b80845d0996da16d1d65bbdc8d8a7a653f89` | 36,569 |
| `b12_peak_entry_bt.py` | `3f240f7fc66bd74eba34e089439c8e52816df5fd` | 33,539 |
| `b12_true_synth_calendar_bt.py` | `ed250d685e694c9ffba4f42f8d664c0177bc06e9` | 30,293 |
| `borrow_thesis.py` | `2bc75f53bfc9420dc6740a4051511672f18d17dd` | 16,889 |
| `nan_diagnostic.py` | `aa206e2eb9f13c996250e607959386afd3e68ac5` | 15,576 |
| `bdl_put_skew_htb.py` | `466b40028d750cf8208ab5fc3e01bec77ebe1ac6` | 10,186 |
| `iv_term_structure_analysis.py` | `d82f308b40afa1f6dfa016600c232b481357e44f` | 8,151 |
| `htb_next_cycle_predictor.py` | `b7b2f26d6cf07ca9cdba79dac9beb9a4c1ee13a1` | 7,256 |
| `htb_flag_backtest.py` | `6e16b29a8892bce5e936bbfa8b27a822429d6568` | 4,591 |
| `research_iv_pairs.csv` (1,502 rows) | `78dc9ec1cac25ec65c9393c439189cded74b89c7` | 203,801 |
| `research_collapse_signals.csv` (14,732 rows) | `8df7a1a4731f9ee85576cc42cd59866f1d0ac6d6` | 1,964,600 |

Two further objects were identified and deliberately **not** recovered:
earlier revisions of the source repo's `arctic_store.py`
(`731a3a41870425e6b8f1afe5414b1133e3e09c8a`) and `feature_builder.py`
(`ac39bdd36cdbd4524b5f8463dcb676f947f6712a`). Both belong to the data pipeline
rather than the research, and this repository ships the data instead of the
pipeline.

## What changed in the port

- **The ArcticDB dependency was removed.** The original read a 25 GB LMDB
  store. The port reads 25.8 MB of parquet through `borrowcycle/data.py`.
  Equivalence was verified once, before the store was dropped: for all 7
  panels, `load_panel(t)` equals `ArcticStore().read_borrow_rates(t,'15minute')`
  under `pandas.testing.assert_frame_equal`.
- **Three defects were fixed** — a look-ahead smoother, a sample-selection
  gate, and an inverted slippage sign. Each is reproducible in its original
  form via a flag, so the size of every correction can be measured. See
  [05_limitations.md](05_limitations.md).
- **A published explanation was corrected.** The stated cause of the S3 failure
  is not supported by the data. See
  [04_negative_results.md](04_negative_results.md).
- **The Hidden Markov Model work was dropped.** The original fitted a 4-state
  Gaussian HMM to label cycle phases. The phases here are defined by explicit
  rules instead, which are inspectable, reproducible without a fitted model,
  and easier to argue with.

## Data lineage

NSE single-stock futures and options, 15-minute bars, sourced via the Upstox
and Zerodha Kite APIs into an ArcticDB store, with `b12`, DTE and the `feat_*`
columns computed by the source repository's feature pipeline.

`scripts/00_export_data.py` is the extraction step and the only script that
cannot run from a clone. It is kept so the lineage is auditable, and
`data/MANIFEST.json` carries a sha256 for every shipped file.
