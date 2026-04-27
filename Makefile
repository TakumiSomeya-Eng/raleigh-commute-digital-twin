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

# Raw-data directories (relative from this worktree to the project Data/ folder).
TRACE_day1 := ../../../Data/Saint_Marys_Street-2026-04-16_13-27-45
TRACE_day2 := ../../../Data/Saint_Marys_Street-2026-04-17_13-20-03
DATA_DIR    := $(TRACE_$(TRACE))

# ---------------------------------------------------------------------------
# Phony targets
# ---------------------------------------------------------------------------

.PHONY: help bootstrap data fit synth ks bag fuse eval ideal score report deploy clean test

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

## fit         : Fit noise distributions from aligned Parquet (FR-2.1)  TRACE=day1|day2
fit:
	python -m data_engine fit \
		--traces  "$(TRACE)" \
		--out-dir out

## synth       : Generate N synthetic scenarios from base trace (FR-2.2)  TRACE=day2 N_SCENARIOS=10
synth:
	python -m data_engine synth \
		--base      "$(TRACE)" \
		--n         "$(N_SCENARIOS)" \
		--out-dir   out

## ks          : Run KS-test gate real vs. synthetic (FR-2.3)  TRACE=day2
ks:
	python -m data_engine ks \
		--real  "out/$(TRACE)" \
		--synth out/synthetic \
		--out   gates/p1_ks.json

## fixture     : Generate tests/fixtures/tiny_{TRACE}_60s/trip.mcap (60 s slice)
fixture:
	PYTHONPATH="src" python scripts/make_fixtures.py \
		--trace  "$(TRACE)" \
		--out-dir tests/fixtures

## bag         : Convert aligned Parquet -> MCAP bag (FR-3.1)  TRACE=...
bag:
	PYTHONPATH="src" python -m bag_bridge.parquet_to_mcap \
		--parquet  "out/$(TRACE)/aligned_100hz.parquet" \
		--noise-fit "config/noise_fit_$(TRACE).yaml" \
		--out-dir  "out/$(TRACE)"

## fuse        : Run EKF/UKF sensor fusion (FR-4.2/5.2)  TRACE=...  FILTER=ekf|ukf
fuse:
	mkdir -p out/$(TRACE)
	PYTHONPATH="src" python scripts/run_fuse.py \
		--trace  "$(TRACE)" \
		--filter "$(FILTER)" \
		--out-dir out

## eval        : Compute RMSE evaluation (FR-6.2)  TRACE=...  FILTER=...  [STAGE=gt|rmse|all]
STAGE ?= all
eval:
ifeq ($(filter $(STAGE),gt all),)
else
	mkdir -p out/$(TRACE)
	PYTHONPATH="src" python -m evaluation smooth \
		--trace "$(TRACE)" \
		--out-dir out
endif
ifeq ($(filter $(STAGE),rmse all),)
else
	PYTHONPATH="src" python -m evaluation rmse \
		--trace  "$(TRACE)" \
		--filter "$(FILTER)" \
		--out-dir out \
		--config-dir config
endif

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
