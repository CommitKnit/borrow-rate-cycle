# 06_make_figures

## What it does
Renders the narrative figures used in the README and docs from the CSVs written by scripts 01–05, plus a few cycle-level panels read from the store.

## Inputs
`results/*.csv` from scripts 01–05; for the single-cycle and anatomy charts, the joined borrow panel through `borrowcycle/data.py`.

## Outputs
`figures/01_mechanism_single_cycle.png` … `figures/10_negative_results.png` (figures 11–13 come from scripts 02, 04 and 05).

## How it works
1. Load the result CSVs.
2. Draw each chart in the house style (`borrowcycle/plotting.py`).
3. Write the PNGs to `figures/`.

## Usage
```bash
python scripts/06_make_figures/06_make_figures.py   # after 01-05
```

## Re-running / what gets skipped
Overwrites the PNGs; takes seconds.

## Caveats
* Figures reflect whatever results are on disk. Re-run 01–05 first after the data changes.
