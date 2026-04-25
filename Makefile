# Raleigh Commute Digital Twin -- top-level Makefile (T0.4 / FR-7.1)
#
# Usage:
#   make help
#   make test
#   make clean
#   make data  TRACE=day2
#   make fuse  TRACE=day2 FILTER=ukf
#   make synth N_SCENARIOS=20

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

TRACE       ?= day2
FILTER      ?= ekf
N_SCENARIOS ?= 10

# Map TRACE names to raw-data subdirectories under data/.
TRACE_day1 := data/Saint_Marys_Street-2026-04-16_13-27-45
TRACE_day2 := data/Saint_Marys_Street-2026-04-17_13-20-03
DATA_DIR    := $(TRACE_$(TRACE))

# ---------------------------------------------------------------------------
# Phony targets
# ---------------------------------------------------------------------------

.PHONY: help bootstrap data synth bag fuse eval ideal score report deploy clean test

# ---------------------------------------------------------------------------
# help -- scans ## comments to self-document all targets
# ---------------------------------------------------------------------------

## help        : List all targets with descriptions
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'

# ---------------------------------------------------------------------------
# clean -- idempotent; rm -rf is a no-op when dirs do not exist
# ---------------------------------------------------------------------------

## clean       : Remove out/ and build/ (idempotent)
clean:
	rm -rf out/ build/
	@printf '[%s] [FR-7.1 clean] INFO  Removed out/ and build/\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------------------
# test -- pytest (exit 5 = no tests yet -> OK) + ctest if build/ exists
# ---------------------------------------------------------------------------

## test        : Run pytest (Python) and ctest (C++); exit 0 with zero tests
test:
	@printf '[%s] [FR-8 test] INFO  Running pytest...\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	@pytest -q; code=$$?; \
	[ "$$code" -eq 5 ] && \
		printf '[%s] [FR-8 test] INFO  No tests collected yet -- OK\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
	|| [ "$$code" -eq 0 ] \
	|| exit $$code
	@if [ -f build/CTestTestfile.cmake ]; then \
		printf '[%s] [FR-8.2 test] INFO  Running ctest...\n' \
			"$$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
		cd build && ctest --output-on-failure; \
	else \
		printf '[%s] [FR-8.2 test] INFO  No C++ build found -- skipping ctest\n' \
			"$$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
	fi
	@printf '[%s] [FR-8 test] INFO  Done\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------------------
# Stub targets -- implemented in later tasks; all exit 0 so CI passes early
# Log format: TRD sec.4.4  [ISO-8601Z] [FR-x.y stage] INFO  message
# ---------------------------------------------------------------------------

## bootstrap   : Set up dev environment (pre-commit, Docker images) (FR-7.4)
bootstrap:
	@printf '[%s] [FR-7.4 bootstrap] INFO  T0.4 bootstrap -- not yet implemented\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)"

## data        : Ingest raw CSVs -> aligned_100hz.parquet (FR-1.5)  TRACE=day1|day2
data:
	python -m data_engine ingest \
		--trace    "$(TRACE)" \
		--data-dir "$(DATA_DIR)" \
		--out-dir  out

## synth       : Generate synthetic scenarios + KS gate (FR-2.2/2.3)  N_SCENARIOS=10
synth:
	@printf '[%s] [FR-2.2 synth] INFO  T1.5 synth TRACE=%s N=%s -- not yet implemented\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(TRACE)" "$(N_SCENARIOS)"

## bag         : Convert aligned Parquet -> MCAP bag (FR-3.1)  TRACE=...
bag:
	@printf '[%s] [FR-3.1 bag] INFO  T2.1 bag TRACE=%s -- not yet implemented\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(TRACE)"

## fuse        : Run EKF/UKF sensor fusion (FR-4.2/5.2)  TRACE=...  FILTER=ekf|ukf
fuse:
	@printf '[%s] [FR-4.2 fuse] INFO  T2.5 fuse TRACE=%s FILTER=%s -- not yet implemented\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(TRACE)" "$(FILTER)"

## eval        : Compute RMSE / NEES filter evaluation (FR-6.2)  TRACE=...  FILTER=...
eval:
	@printf '[%s] [FR-6.2 eval] INFO  T3.2 eval TRACE=%s FILTER=%s -- not yet implemented\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(TRACE)" "$(FILTER)"

## ideal       : Map-match + synthesize ideal trajectory (FR-9)  TRACE=...
ideal:
	@printf '[%s] [FR-9.1 ideal] INFO  T4.1 ideal TRACE=%s -- not yet implemented\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(TRACE)"

## score       : Compute score.json + tip lookup (FR-10.7)  TRACE=...
score:
	@printf '[%s] [FR-10.7 score] INFO  T4.7 score TRACE=%s FILTER=%s -- not yet implemented\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(TRACE)" "$(FILTER)"

## report      : Render HTML report (FR-11.1)  TRACE=...
report:
	@printf '[%s] [FR-11.1 report] INFO  T5.1 report TRACE=%s -- not yet implemented\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(TRACE)"

## deploy      : Deploy to AWS (Phase 2 -- deferred)
deploy:
	@printf '[%s] [Phase-2 deploy] INFO  deploy -- deferred to Phase 2\n' \
		"$$(date -u +%Y-%m-%dT%H:%M:%SZ)"
