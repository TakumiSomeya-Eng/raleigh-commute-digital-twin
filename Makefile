# Raleigh Commute Digital Twin — top-level Makefile
# Full implementation: task T0.4.
# Trace name → raw data directory mapping (agreed in project setup).
TRACE_day1 := Data/Saint_Marys_Street-2026-04-16_13-27-45
TRACE_day2 := Data/Saint_Marys_Street-2026-04-17_13-20-03

# Resolve TRACE variable to DATA_DIR.  Usage: make data TRACE=day2
DATA_DIR := $(TRACE_$(TRACE))

# Default values
TRACE    ?= day2
FILTER   ?= ekf
N_SCENARIOS ?= 10

.PHONY: help bootstrap data synth bag fuse eval ideal score report deploy clean test

## help        : List all targets with descriptions
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'

## bootstrap   : Install pre-commit hooks, verify Docker (T0.4 / T7.4)
bootstrap:
	@echo "[T0.4] bootstrap — not yet implemented"

## data        : Ingest raw CSVs → aligned Parquet  (T1.3)  TRACE=day1|day2
data:
	@echo "[T1.3] data TRACE=$(TRACE) DATA_DIR=$(DATA_DIR) — not yet implemented"

## synth       : Generate synthetic scenarios  (T1.5)
synth:
	@echo "[T1.5] synth N=$(N_SCENARIOS) — not yet implemented"

## bag         : Convert Parquet → MCAP bag  (T2.1)  TRACE=...
bag:
	@echo "[T2.1] bag TRACE=$(TRACE) — not yet implemented"

## fuse        : Run EKF/UKF fusion  (T2.5/T2.7)  TRACE=...  FILTER=ekf|ukf
fuse:
	@echo "[T2.5] fuse TRACE=$(TRACE) FILTER=$(FILTER) — not yet implemented"

## eval        : Compute RMSE, NEES  (T3.2/T3.3)  TRACE=...  FILTER=...
eval:
	@echo "[T3.2] eval TRACE=$(TRACE) FILTER=$(FILTER) — not yet implemented"

## ideal       : Map-match + synthesize ideal trajectory  (T4.1–T4.4)  TRACE=...
ideal:
	@echo "[T4.1] ideal TRACE=$(TRACE) — not yet implemented"

## score       : Compute score.json  (T4.7)  TRACE=...
score:
	@echo "[T4.7] score TRACE=$(TRACE) — not yet implemented"

## report      : Render HTML report  (T5.1)  TRACE=...
report:
	@echo "[T5.1] report TRACE=$(TRACE) — not yet implemented"

## deploy      : Deploy to AWS  (Phase 2)
deploy:
	@echo "[Phase 2] deploy — deferred to Phase 2"

## test        : Run pytest + ctest
test:
	pytest
	@echo "[T0.4] ctest — not yet wired"

## clean       : Remove out/ and build/
clean:
	rm -rf out/ build/
	mkdir -p out
	@echo "[clean] Done."
