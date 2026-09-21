.PHONY: all anatomy roll backtest negative iv figures clean

all: anatomy roll backtest negative iv figures

anatomy:  ; python scripts/01_cycle_anatomy.py
roll:     ; python scripts/02_roll_evidence.py
backtest: ; python scripts/03_backtest.py
negative: ; python scripts/04_negative_results.py
iv:       ; python scripts/05_iv_term_structure.py
figures:  ; python scripts/06_make_figures.py

clean:
	rm -rf results/*.csv results/*.md figures/*.png
