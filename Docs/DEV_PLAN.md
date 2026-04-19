# Dev Plan — Raleigh Commute Digital Twin: *Uber vs. My AI*

**Document version:** 1.0
**Status:** Draft for review
**Owner:** Takumi
**Last updated:** 2026-04-19
**Companion to:** `PRD.md` v2.0, `FRD.md` v1.0, `TRD.md` v1.0
**Scope:** Phase 1 (local-only). Phase 2 (AWS) has its own Dev Plan authored after Phase 1 sign-off.

---

## 0. How to read this document

This plan breaks the Phase 1 FRD into **37 tasks** organized in **6 phases**. Each task is sized to roughly 1–3 hours of implementation plus tests — i.e. one Claude Code session. Larger FRs (like `ekf_node`) are split across tasks.

Every task has the following fields:

| Field | Meaning |
|---|---|
| **ID** | `T{phase}.{n}` — stable reference for commit messages and PR titles |
| **Name** | Short human-readable label |
| **Covers** | FR IDs this task implements or partially implements |
| **Blockers** | Task IDs that must be done first |
| **Deliverables** | Files/artifacts produced |
| **DoD** | Definition of Done — objective checks, lift directly from FRD acceptance criteria |
| **Claude Code prompt** | The exact (or near-exact) prompt to open the session with |
| **Est.** | Rough hours, for self-scheduling only |

**Phase gates:** each phase ends with a gate that must be green before the next phase starts. Gates are italicized at the end of each phase section.

**Conventions:**
- Commit messages follow `{task_id}: {imperative verb} {object}` — e.g. `T1.3: add ENU projection with round-trip test`.
- PR titles match commit messages for single-task PRs; multi-task PRs list all IDs.
- Branch names: `{phase_id}/{task_id}-{kebab-name}` — e.g. `phase1/T1.3-enu-projection`.

---

## 1. Phase overview

| Phase | Name | Task count | Covers | Ends with gate |
|---|---|---|---|---|
| **P0** | Foundation (scaffolding, tooling) | 5 | FR-7, FR-8 partial | Repo builds, tests run green (on empty code) |
| **P1** | Data engine | 6 | FR-1, FR-2 | **PRD S2**: KS-test passes on day1+day2 |
| **P2** | Fusion (EKF + UKF) | 8 | FR-3, FR-4, FR-5 | Nodes run against day2 MCAP without crashing |
| **P3** | Filter evaluation | 4 | FR-6 | **PRD S1**: EKF RMSE < 0.75 × GPS-only RMSE |
| **P4** | Ideal driver + scoring | 8 | FR-9, FR-10 | `score.json` emitted end-to-end for day2 |
| **P5** | Reporting + validation | 6 | FR-11 + subjective-ratings loop | **PRD S3 (local)** + **S4** measurable |

Total: **37 tasks**. Estimated wall-clock: 60–100 hours of focused work, spread over 3–6 weeks depending on concurrent time.

---

## 2. Dependency graph (high-level)

```
P0 Foundation
   │
   ▼
P1 Data engine  ──▶ P2 Fusion ──▶ P3 Filter eval ──▶ S1 gate
                                       │
                                       ▼
                                  P4 Ideal + Scoring
                                       │
                                       ▼
                                  P5 Reporting ──▶ S3/S4 gates
```

Within phases, tasks form a DAG. Blockers are listed per task.

---

## 3. Phase P0 — Foundation

Ends with: repo builds, `make test` runs green (zero real tests yet), pre-commit green, Docker images built.

---

### T0.1 — Repo scaffold and gitignore

- **Covers:** FR-7 (partial)
- **Blockers:** —
- **Deliverables:** Root tree from TRD §7 (empty files OK), `.gitignore`, `README.md` stub, `LICENSE` (MIT), `pyproject.toml`, `requirements.txt` + `requirements-dev.txt` (both pinned per TRD §5.1), `.dockerignore`, empty `infra/README.md` placeholder.
- **DoD:**
  - [ ] `git init && git add -A && git status` shows no unexpected files.
  - [ ] `tree -L 2` matches TRD §7 skeleton exactly.
  - [ ] `pip install -r requirements.txt -r requirements-dev.txt` succeeds in a fresh venv.
- **Claude Code prompt:**
  > Create the initial directory structure for the "Raleigh Commute Digital Twin" project exactly as specified in TRD.md §7. Create empty placeholder files (with docstrings/comments explaining their future purpose) for every Python module. Generate `requirements.txt` and `requirements-dev.txt` with the pinned versions from TRD §5.1. Set up `.gitignore` per TRD §7.1. Create a minimal `README.md` that links to PRD.md, FRD.md, TRD.md, and this dev plan. Create `pyproject.toml` configured with ruff (line length 100), mypy (strict), and pytest. Do not implement any feature logic — this task is structural only.
- **Est.:** 1h

---

### T0.2 — Docker images (Python + ROS 2)

- **Covers:** FR-7.2, FR-7.3
- **Blockers:** T0.1
- **Deliverables:** `docker/python.Dockerfile`, `docker/ros2.Dockerfile`, `docker-compose.yml` (Python + ROS 2 + Valhalla services; MinIO optional/commented out).
- **DoD:**
  - [ ] `docker build -f docker/python.Dockerfile -t rct-python:dev .` succeeds.
  - [ ] `docker build -f docker/ros2.Dockerfile -t rct-ros2:dev .` succeeds.
  - [ ] Image sizes: Python < 1.5 GB, ROS 2 < 3 GB.
  - [ ] `hadolint` clean on both Dockerfiles.
  - [ ] `docker compose config` validates (no services started yet).
- **Claude Code prompt:**
  > Create `docker/python.Dockerfile` and `docker/ros2.Dockerfile` per TRD §5 version pins. Use Ubuntu 24.04 as the base for both; ROS 2 Jazzy for the ROS image. Multi-stage builds (builder + runtime). Mount points: repo at `/workspace`, data at `/data`, outputs at `/out`. Install pinned Python deps in the Python image via `requirements.txt`. Install `ros-jazzy-desktop` and build tools in the ROS image. Create `docker-compose.yml` with three services: `python`, `ros2`, `valhalla` (using `gisops/valhalla` image with a volume for `custom_files`). All services share a named bridge network `rct-net`. Add `.dockerignore` to exclude `out/`, `data/`, `.git/`, `__pycache__/`. Both Dockerfiles must pass `hadolint`.
- **Est.:** 2h

---

### T0.3 — Pre-commit and lint setup

- **Covers:** FR-8.4
- **Blockers:** T0.1
- **Deliverables:** `.pre-commit-config.yaml`, `.clang-format`, `.clang-tidy`, `.markdownlint.jsonc`, `.yamllint`, ruff config inside `pyproject.toml`.
- **DoD:**
  - [ ] `pre-commit install && pre-commit run --all-files` is green on the scaffold (i.e. no baseline violations in the empty code).
  - [ ] Each linter (ruff, clang-format, hadolint, markdownlint, yamllint) is wired.
  - [ ] `check-added-large-files` blocks files > 2 MB (prevents raw CSVs from sneaking in).
- **Claude Code prompt:**
  > Set up pre-commit hooks per TRD §4.7 and §6.6. Tools: ruff (lint + format), clang-format (Google style, 100-col), clang-tidy (readability/performance/bugprone groups), hadolint, markdownlint-cli2 (CommonMark), yamllint (line length 120), check-added-large-files (2 MB limit), trailing-whitespace, end-of-file-fixer. Create a `.clang-format` file (Google base, `ColumnLimit: 100`) and `.clang-tidy` with the check groups enabled. Ensure `pre-commit run --all-files` is green on the current scaffold.
- **Est.:** 1h

---

### T0.4 — Makefile and CLI targets (stubs)

- **Covers:** FR-7.1
- **Blockers:** T0.2
- **Deliverables:** `Makefile` with all targets from FRD §FR-7.1 (`bootstrap`, `data`, `synth`, `bag`, `fuse`, `eval`, `ideal`, `score`, `report`, `deploy`, `clean`, `test`, `help`), currently echoing "not implemented" except for `help`, `clean`, and `test`.
- **DoD:**
  - [ ] `make help` lists all targets with 1-line descriptions.
  - [ ] `make clean` removes `out/` and `build/` (idempotent).
  - [ ] `make test` runs pytest and gtest (both pass with zero tests or placeholders).
  - [ ] Makefile respects `TRACE=<name>` variable pattern used throughout.
- **Claude Code prompt:**
  > Write the top-level `Makefile` per FRD §FR-7.1 and TRD §4.4 logging conventions. Targets: `bootstrap`, `data`, `synth`, `bag`, `fuse`, `eval`, `ideal`, `score`, `report`, `deploy`, `clean`, `test`, `help`. For each unimplemented target, echo `"[T{phase}.{n}] <name> — not yet implemented"` and exit 0 (so CI passes early). Implement `help` (self-documenting, scans `##` comments), `clean` (removes `out/` and `build/`), and `test` (runs `pytest` and `ctest` where applicable). Every target accepts `TRACE=<dir>` as a variable. Log lines must match TRD NFR 4.4 format (ISO-8601 timestamp, stage tag).
- **Est.:** 1.5h

---

### T0.5 — CI pipeline (local mirror)

- **Covers:** FR-8 cross-cutting (not Phase 2's FR-12.5)
- **Blockers:** T0.3, T0.4
- **Deliverables:** `.github/workflows/ci.yaml` — lint, Python unit, C++ unit, integration (placeholder job that passes).
- **DoD:**
  - [ ] Workflow triggers on pull_request and push to main.
  - [ ] Four jobs defined: `lint`, `py-unit`, `cpp-unit`, `integration`.
  - [ ] Runs in < 5 min total (no real tests yet, so should be near-instant).
  - [ ] Uses `actions/setup-python` pinned to 3.11.9; uses official ROS 2 Jazzy container for C++ jobs.
- **Claude Code prompt:**
  > Create `.github/workflows/ci.yaml` per TRD §6.6. Four jobs: `lint` (pre-commit run --all-files), `py-unit` (pytest in the Python container), `cpp-unit` (colcon test in the ROS 2 container), `integration` (placeholder echo "not yet wired"). Each job uses the appropriate Docker image built by T0.2 or the official `osrf/ros:jazzy-desktop` image as fallback. Cache `~/.cache/pip` and `~/.cache/pre-commit`. All jobs must complete in < 5 min on a fresh runner.
- **Est.:** 1.5h

---

### P0 Gate

- [ ] `make bootstrap && make test` green from a fresh clone.
- [ ] CI green on a trivial PR.
- [ ] Pre-commit green.
- [ ] All Docker images build.

---

## 4. Phase P1 — Data engine

Ends with: **PRD S2 pass** — synthetic data stays inside real support per KS test.

---

### T1.1 — Schemas and shared errors

- **Covers:** FR-1.4 (schema), part of all downstream FRs
- **Blockers:** T0.1
- **Deliverables:** `src/data_engine/schemas.py` (pydantic models for `Aligned100Hz`, `GroundTruth`, `RouteMatched`, `ReferencePath`, `IdealSpeed`, `IdealTrajectory` per TRD §1), `src/data_engine/errors.py` (`MissingRequiredChannelError`, `SchemaValidationError`, and the exit-code taxonomy from TRD §4.5).
- **DoD:**
  - [ ] Every schema in TRD §1.2–1.7 has a matching pydantic model.
  - [ ] Unit test: round-trip pydantic model ↔ Parquet preserves all fields.
  - [ ] `mypy --strict src/data_engine/schemas.py` clean.
- **Claude Code prompt:**
  > Implement pydantic v2 BaseModel classes in `src/data_engine/schemas.py` for every Parquet schema in TRD §1.2 through §1.7 (Aligned100Hz, GroundTruth, RouteMatched, ReferencePath, IdealSpeed, IdealTrajectory). Include field descriptions and validators where units matter (e.g. `heading_rad` must be in [-π, π]). In `src/data_engine/errors.py`, define `MissingRequiredChannelError`, `SchemaValidationError`, and a `StageExitCode` enum matching TRD §4.5. Add unit tests in `tests/unit/test_schemas.py` that: (1) construct each model with valid data, (2) reject invalid data, (3) round-trip through pyarrow Table and back.
- **Est.:** 2h

---

### T1.2 — ENU projection

- **Covers:** FR-1.3
- **Blockers:** T1.1
- **Deliverables:** `src/data_engine/projection.py` with `wgs84_to_enu(lat, lon, lat0, lon0)` and inverse, using flat-earth approximation.
- **DoD:**
  - [ ] Round-trip error < 0.1 m over day2 span (verified in test).
  - [ ] 1° lat delta ≈ 111,000 m ± 1 % test passes.
  - [ ] Anchor read from `config/data_gen.yaml`, not hard-coded.
  - [ ] Cross-check test: pyproj-based projection agrees with flat-earth within 1 m over day2 span.
- **Claude Code prompt:**
  > Implement `src/data_engine/projection.py` per FRD §FR-1.3 and TRD §1.1. Flat-earth / equirectangular approximation anchored at `(lat0, lon0)` from `config/data_gen.yaml`. Provide `wgs84_to_enu(lat, lon, lat0, lon0) -> (east_m, north_m)` and `enu_to_wgs84(east_m, north_m, lat0, lon0) -> (lat, lon)`. Vectorized over numpy arrays. Add tests in `tests/unit/test_projection.py`: (1) 1° northward delta at the anchor ≈ 111 km ±1%, (2) round-trip over day2 GPS points within 0.1 m, (3) comparison against `pyproj` over same points within 1 m. Create `config/data_gen.yaml` with the anchor `(35.773, -78.610)`.
- **Est.:** 1.5h

---

### T1.3 — CSV ingestion + clock alignment + Parquet output

- **Covers:** FR-1.1, FR-1.2, FR-1.4, FR-1.5
- **Blockers:** T1.1, T1.2
- **Deliverables:** `src/data_engine/ingest.py`, `src/data_engine/parquet_io.py`, `src/data_engine/__main__.py` with `ingest` subcommand.
- **DoD:**
  - [ ] `make data TRACE=day1` and `TRACE=day2` both produce `out/day{1,2}/aligned_100hz.parquet`.
  - [ ] Output file sizes between 2 MB and 20 MB.
  - [ ] First 0.5 s dropped as warm-up; remaining rows NaN-free.
  - [ ] Interpolation preserves known sinusoid within 1 % amplitude error (synthetic fixture test).
  - [ ] Missing-channel test raises `MissingRequiredChannelError`.
- **Claude Code prompt:**
  > Implement FR-1.1 (CSV parser), FR-1.2 (100 Hz alignment), FR-1.4 (Parquet writer), FR-1.5 (CLI) per FRD and TRD §1.2. `ingest.py` parses the seven required channels (Location, Accelerometer, Gyroscope, Gravity, Orientation, Magnetometer, TotalAcceleration); missing any required channel raises `MissingRequiredChannelError`. `parquet_io.py` reads and writes schema-validated Parquet with Snappy compression and metadata keys per TRD §1.11. `__main__.py` exposes `python -m data_engine ingest --trace day1 --data-dir ./data --out-dir ./out`. Interpolation is linear for Location; IMU/Gyro/etc. are re-timed to the 10 ms grid via linear interpolation in time. Drop first 0.5 s. Add `gps_interpolated: bool` column (True iff the row is not backed by a real GPS fix within ±50 ms). Wire the Makefile `data` target. Add tests with the sinusoid fixture (TRD §6.3), a missing-channel case, and a day2 smoke test (but bundle a tiny slice under `tests/fixtures/tiny_day2_60s/` first).
- **Est.:** 3h

---

### T1.4 — Noise fitting (FR-2.1)

- **Covers:** FR-2.1
- **Blockers:** T1.3
- **Deliverables:** `src/data_engine/noise_fit.py`, QQ-plot PNGs per channel, `config/noise_fit.yaml` generation.
- **DoD:**
  - [ ] Fits converge for all 7 channels on day1 AND day2.
  - [ ] Day1 vs day2 fitted parameters within 2σ (else warning).
  - [ ] `config/noise_fit.yaml` generated; contains distribution family + params per channel.
  - [ ] QQ-plots saved under `out/{trip_id}/qq_plots/`.
- **Claude Code prompt:**
  > Implement FR-2.1 per FRD and TRD §1. For each sensor channel in TRD §1.2: fit a parametric noise model to residuals from a simple reference smoother (moving average is fine — the filter itself comes later). Distributions: Gaussian for IMU/Gyro/Gravity/TotalAccel; Rayleigh (or truncated log-normal) for `horizontal_accuracy_m`; von Mises for bearing. Use scipy.stats for fits. Emit `config/noise_fit.yaml` with distribution family + parameters per channel. Generate QQ-plot PNG per channel via matplotlib, saved to `out/{trip_id}/qq_plots/`. Compare day1 vs day2 params; warn if any parameter differs by more than 2σ. CLI: `python -m data_engine fit --traces day1,day2`. Tests: (1) fit returns finite parameters on both days, (2) warning fires when fed synthetic data with deliberately different distributions.
- **Est.:** 2.5h

---

### T1.5 — Synthetic scenario generator (FR-2.2, FR-2.4)

- **Covers:** FR-2.2, FR-2.4
- **Blockers:** T1.4
- **Deliverables:** `src/data_engine/synth.py`, scenario manifest.
- **DoD:**
  - [ ] Deterministic: same seed → byte-identical Parquet output (test).
  - [ ] Supports three stress events: `gps_dropout`, `imu_bias_step`, `mag_anomaly`.
  - [ ] 100 scenarios generated in < 60 s on laptop (multiprocess pool).
  - [ ] `scenario_manifest.json` written per TRD §1.12.
- **Claude Code prompt:**
  > Implement FR-2.2 and FR-2.4 per FRD and TRD §1.12. `synth.py` takes a base `aligned_100hz.parquet` and a scenario config (seed + stress events). Inject noise sampled from the fitted distributions in `config/noise_fit.yaml`. Support stress events per FRD §FR-2.2: `gps_dropout(start_s, end_s)`, `imu_bias_step(axis, delta, at_s)`, `mag_anomaly(start_s, duration_s)`. Output to `out/synthetic/{scenario_id}/aligned_100hz.parquet`. Emit `scenario_manifest.json` per TRD §1.12 (append-safe). CLI: `python -m data_engine synth --base day2 --n 10 --config config/data_gen.yaml`. Use `multiprocessing.Pool` for N > 1. Tests: (1) two runs with same seed produce byte-identical Parquet, (2) 10 scenarios in < 10 s, (3) stress event visible in output (e.g. dropout zeroes `gps_interpolated` region).
- **Est.:** 3h

---

### T1.6 — KS-test gate (FR-2.3)

- **Covers:** FR-2.3
- **Blockers:** T1.5
- **Deliverables:** `src/data_engine/ks_test.py`, `ks_report.json` writer.
- **DoD:**
  - [ ] CLI: `python -m data_engine ks --real out/day2 --synth out/synthetic` exits 0 iff gate passes.
  - [ ] Gate threshold (80 % pass rate at p > 0.05) from config, not hard-coded.
  - [ ] `ks_report.json` schema matches TRD §1.9.
  - [ ] **PRD S2 passes on real day1+day2 vs. 10-scenario synthetic batch generated from day2.**
- **Claude Code prompt:**
  > Implement FR-2.3 per FRD and TRD §1.9. Two-sample KS test per channel between real and synthetic Parquet samples. Use `scipy.stats.ks_2samp`. Emit `ks_report.json` per TRD §1.9. CLI: `python -m data_engine ks --real <dir> --synth <dir>`. Exit code 0 iff ≥ 80 % of channels have p > 0.05 (configurable via `config/data_gen.yaml`). Wire the Makefile `synth` target to run generation + KS check in sequence. Tests: (1) synth-from-real passes KS, (2) deliberately misconfigured noise (2× std) fails KS. **Run on day2 + 10 synthetic scenarios; commit the resulting `ks_report.json` as evidence that P1 gate is met.**
- **Est.:** 2h

---

### P1 Gate (PRD S2)

- [ ] `make data TRACE=day1 && make data TRACE=day2` both produce valid Parquet.
- [ ] `make synth` generates 10 scenarios from day2 in < 60 s.
- [ ] `make ks` exits 0 (≥ 80 % channels pass).
- [ ] `ks_report.json` committed to `out/.gates/p1_ks.json` (or CI-produced artifact).

---

## 5. Phase P2 — Fusion (EKF + UKF)

Ends with: EKF and UKF run against `day2.mcap` without crashing and publish `/fused/odom`.

---

### T2.1 — Parquet → MCAP converter (FR-3.1, FR-3.2)

- **Covers:** FR-3.1, FR-3.2
- **Blockers:** T1.3
- **Deliverables:** `bag_bridge/parquet_to_mcap.py`, metadata YAML sidecar.
- **DoD:**
  - [ ] `ros2 bag info out/day2/trip.mcap` shows 3 topics with expected message counts (GPS ~1 Hz × duration, IMU ~100 Hz × duration, Mag ~50 Hz × duration).
  - [ ] NavSatFix `position_covariance` filled from `horizontal_accuracy_m²`.
  - [ ] `Imu.orientation_covariance[0] = -1`.
  - [ ] SHA-256 checksum in sidecar YAML.
- **Claude Code prompt:**
  > Implement FR-3.1 and FR-3.2 per FRD and TRD §2.1. `bag_bridge/parquet_to_mcap.py` reads `aligned_100hz.parquet` and writes `trip.mcap` with three topics: `/gps/fix` (NavSatFix), `/imu/data` (Imu), `/mag` (MagneticField). Publish GPS only on rows where `gps_interpolated == False`. Fill covariances per TRD §2.1 (NavSatFix diagonal from `horizontal_accuracy_m²`, Imu orientation covariance `[0] = -1`, angular velocity covariance from `config/noise_fit.yaml`). Preserve ns precision (int64 → builtin_interfaces/Time). Emit sidecar `trip.metadata.yaml` with SHA-256 of the MCAP, message counts, trip duration. Use the official `mcap` Python library. Wire the Makefile `bag` target. Tests: (1) `ros2 bag info` (mocked if ROS not available in pytest env) shows expected counts, (2) metadata YAML parses and checksum validates.
- **Est.:** 2.5h

---

### T2.2 — CTRV motion model (FR-4.1)

- **Covers:** FR-4.1
- **Blockers:** T0.2
- **Deliverables:** `src/localization/include/localization/ctrv_model.hpp`, gtest suite.
- **DoD:**
  - [ ] Straight-line prediction matches CV model within 1e-9.
  - [ ] Circle-closure test within 1e-6.
  - [ ] Jacobian finite-diff check within 1e-6.
  - [ ] ψ̇ → 0 limit (l'Hôpital branch) tested.
  - [ ] ψ wraparound at ±π tested.
- **Claude Code prompt:**
  > Implement FR-4.1: a header-only CTRV motion model in `src/localization/include/localization/ctrv_model.hpp`. State is `Eigen::Vector<double, 5>` = `[px, py, v, psi, psi_dot]`. Provide: `predict(state, dt, control_a_lon) -> state`, `jacobian(state, dt) -> Matrix5d`. Handle the `psi_dot ≈ 0` singularity via Taylor expansion when `|psi_dot| < 1e-6`. Normalize `psi` into `[-π, π]` after prediction. gtest suite in `src/localization/test/test_ctrv_model.cpp`: (1) straight-line (psi_dot=0) matches CV, (2) 10s circle with v=10, psi_dot=0.1 returns to start within 1e-6, (3) analytical Jacobian matches finite-difference within 1e-6 at 10 random states, (4) ψ wraparound at ±π works. Wire into `CMakeLists.txt` and `package.xml`.
- **Est.:** 3h

---

### T2.3 — χ² gate and adaptive R (FR-4.3, FR-4.4)

- **Covers:** FR-4.3, FR-4.4
- **Blockers:** T2.2
- **Deliverables:** `chi2_gate.hpp`, tests.
- **DoD:**
  - [ ] Mahalanobis threshold for 2D (GPS position) at 99 % = 9.21, encoded as constant with source comment.
  - [ ] Unit test: the 122 m day1 outlier is rejected.
  - [ ] Adaptive R helper: `R_gps = diag(sigma_h², sigma_h²)` with `sigma_h` from message.
- **Claude Code prompt:**
  > Implement FR-4.3 and FR-4.4 per FRD. In `src/localization/include/localization/chi2_gate.hpp`, provide `bool passes_gate(const VectorXd& innovation, const MatrixXd& S, double confidence=0.99)` computing Mahalanobis distance and comparing to χ² threshold. Hard-code the 2D, 1D, 3D thresholds as constexpr constants with a source citation comment. Also provide `Matrix2d gps_r_matrix(double horizontal_accuracy_m)` returning the adaptive R. gtest: (1) inflation of covariance by factor 10 flips a borderline measurement from rejected to accepted, (2) replay the day1 outlier sample (use `tests/fixtures/outlier_day1_sample.csv`) through a dummy filter and assert it's rejected.
- **Est.:** 1.5h

---

### T2.4 — `ekf_node` skeleton + bag bridge (FR-4.2 part a)

- **Covers:** FR-4.2 (subscribe, params, publish stub, bag-playback harness)
- **Blockers:** T2.1, T2.2
- **Deliverables:** `src/localization/src/ekf_node.cpp` (skeleton), launch file, `config/ekf.yaml`.
- **DoD:**
  - [ ] Node subscribes to the three topics; publishes `/fused/odom` at 100 Hz (initially just a pass-through of GPS).
  - [ ] All params declared via `declare_parameter` and loaded from `config/ekf.yaml`.
  - [ ] `ros2 launch localization ekf.launch.py bag:=out/day2/trip.mcap` runs to completion without crashing.
- **Claude Code prompt:**
  > Create the `ekf_node` skeleton per FRD §FR-4.2 and TRD §2.2, §2.5. Subscribe to `/gps/fix` (SENSOR_DATA QoS), `/imu/data` (SENSOR_DATA), `/mag` (SENSOR_DATA). Publish `/fused/odom` (Reliable, keep-last 100). Declare all parameters per TRD §2.5 via `declare_parameter` — do not read them yet, just declare and log at startup. For this task, the "filter" is a pass-through: on each GPS message, publish an Odometry with position set to `[px, py, 0]` from the GPS fix (projected via a C++ port of the ENU projection from T1.2). Create a ROS launch file `launch/ekf.launch.py` that: (a) plays the bag at the path given by `bag:=` arg, (b) starts `ekf_node`. Create `config/ekf.yaml` with the params from TRD §2.5. Verify the node runs against `out/day2/trip.mcap` for the full 15 min without crashing.
- **Est.:** 3h

---

### T2.5 — EKF predict/update implementation (FR-4.2 part b)

- **Covers:** FR-4.2 (math), FR-4.3, FR-4.4 integration
- **Blockers:** T2.3, T2.4
- **Deliverables:** Full EKF implementation in `ekf_node.cpp`.
- **DoD:**
  - [ ] Predict step runs at IMU rate (100 Hz).
  - [ ] Update step runs on each GPS fix.
  - [ ] χ² gate wired; rejections counted.
  - [ ] Initialization: waits for 3 good GPS fixes before publishing (per TRD §2.5).
  - [ ] Integration test: runs against `tests/fixtures/tiny_day2_60s.mcap`, publishes odometry for full duration.
- **Claude Code prompt:**
  > Replace the pass-through in `ekf_node` with a full EKF per FRD §FR-4.2. Use `CTRVModel` from T2.2 for prediction; use `chi2_gate` from T2.3 for outlier rejection. Measurement model per TRD §2.1 and FRD §FR-4.3: GPS position (2D), GPS speed (scalar), GPS bearing (scalar, only when `speed > bearing_min_speed_mps`). IMU contributes the longitudinal accel control input (body frame, gravity-removed) and the yaw-rate pseudo-measurement. Initialization: wait for `wait_gps_count` good fixes, seed state from the mean. Publish `/fused/odom` at IMU rate (100 Hz) with full pose + covariance per TRD §2.3. Count rejected measurements. Integration test in `tests/integration/test_headless_ekf.py`: replay `tests/fixtures/tiny_day2_60s.mcap`, record `/fused/odom`, assert ≥ 5500 messages received (60 s × 100 Hz × 0.9 tolerance) and final position within 50 m of GPS final.
- **Est.:** 4h

---

### T2.6 — Diagnostics topic (FR-4.5)

- **Covers:** FR-4.5
- **Blockers:** T2.5
- **Deliverables:** `/fused/diagnostics` publisher in `ekf_node`.
- **DoD:**
  - [ ] Topic visible in `ros2 topic echo` during playback.
  - [ ] Fields: `rejection_count`, `nees_mean`, `Q_trace`, `R_pos_trace`, `health` enum.
  - [ ] `health = DEGRADED` when rejection rate > 5 %; `DIVERGED` when NEES > 3× dim for > 5 s.
- **Claude Code prompt:**
  > Add the `/fused/diagnostics` topic per FRD §FR-4.5 and TRD §2.2. Publish `diagnostic_msgs/DiagnosticArray` at 1 Hz with fields: `rejection_count` (cumulative), `nees_mean` (windowed over last 10 s), `Q_trace`, `R_pos_trace`, `health` as KeyValue pairs. `health` enum: `OK` (default), `DEGRADED` (rejection rate > 5 % in window), `DIVERGED` (mean NEES > 3 × state_dim for > 5 s). Unit test the health-state transitions in isolation (no ROS), then add an integration assertion that during normal day2 playback, health stays `OK`.
- **Est.:** 2h

---

### T2.7 — Sigma points + `ukf_node` (FR-5.1, FR-5.2)

- **Covers:** FR-5.1, FR-5.2
- **Blockers:** T2.2, T2.5
- **Deliverables:** `sigma_points.hpp`, `ukf_node.cpp`, `config/ukf.yaml`.
- **DoD:**
  - [ ] Sigma point consistency test: weighted mean/cov round-trip within 1e-10.
  - [ ] `ukf_node` shares `CTRVModel` with EKF (no duplicate motion code).
  - [ ] On a straight fixture segment, UKF and EKF outputs match within 0.5 m RMSE.
- **Claude Code prompt:**
  > Implement FR-5.1 (sigma points) and FR-5.2 (UKF node) per FRD and TRD §2.5. Header: `src/localization/include/localization/sigma_points.hpp` with Julier-Uhlmann scaled sigma points (α, β, κ configurable). Node: `src/localization/src/ukf_node.cpp` that mirrors `ekf_node` but uses sigma-point propagation instead of Jacobian linearization. Both nodes must use the same `CTRVModel` from T2.2 — the motion model is shared code, not copy-pasted. Add `config/ukf.yaml` using YAML anchors to share noise parameters with `config/ekf.yaml`. gtest for sigma points: (1) reconstruct mean within 1e-12 across 100 random states, (2) reconstruct covariance within 1e-10. Integration test: run both nodes against `tests/fixtures/tiny_day2_60s.mcap`, assert EKF-UKF RMSE < 0.5 m on the straight segment.
- **Est.:** 4h

---

### T2.8 — Bag recording of fused odom

- **Covers:** Bridge between fusion output and evaluation input
- **Blockers:** T2.5, T2.7
- **Deliverables:** `bag_bridge/mcap_to_parquet.py`, launch configurations that record `/fused/odom` during playback.
- **DoD:**
  - [ ] `make fuse TRACE=day2 FILTER=ekf` produces `out/day2/fused_ekf.parquet`.
  - [ ] Same for `FILTER=ukf`.
  - [ ] Parquet schema: `t_s, px_m, py_m, v_mps, psi_rad, psi_dot_rps` + covariance diagonal.
- **Claude Code prompt:**
  > Add the recording side of the bag bridge: `bag_bridge/mcap_to_parquet.py` reads a bag containing `/fused/odom` and writes a Parquet file with schema `[t_s, px_m, py_m, v_mps, psi_rad, psi_dot_rps, cov_xx, cov_yy, cov_yaw]`. Wire the Makefile `fuse` target: `make fuse TRACE=day2 FILTER=ekf` should (a) launch the chosen node against `out/day2/trip.mcap`, (b) record `/fused/odom` to `out/day2/fused_{filter}.bag`, (c) convert to `out/day2/fused_{filter}.parquet`. FILTER defaults to `ekf`. Add the mcap→parquet step to the Python unit tests.
- **Est.:** 2h

---

### P2 Gate

- [ ] `make fuse TRACE=day2 FILTER=ekf` and `FILTER=ukf` both produce Parquet files.
- [ ] Integration tests for both nodes green in CI.
- [ ] No crashes on the full day2 15-min trace.

---

## 6. Phase P3 — Filter evaluation

Ends with: **PRD S1 pass** — EKF RMSE ≤ 0.75 × GPS-only RMSE on day2.

---

### T3.1 — RTS smoother for ground truth (FR-6.1)

- **Covers:** FR-6.1
- **Blockers:** T1.3
- **Deliverables:** `src/evaluation/rts_smoother.py`, `ground_truth.parquet` generator.
- **DoD:**
  - [ ] Offline batch smoother; can look ahead.
  - [ ] Output trajectory is smoother (lower integrated |curvature derivative|) than raw GPS.
  - [ ] `make eval TRACE=day2 STAGE=gt` writes `out/day2/ground_truth.parquet`.
  - [ ] Schema matches TRD §1.3.
- **Claude Code prompt:**
  > Implement FR-6.1 per FRD and TRD §1.3. Batch RTS (Rauch-Tung-Striebel) smoother in `src/evaluation/rts_smoother.py`. Forward pass: same CTRV-EKF as the online filter but pure Python (use `filterpy.kalman.KalmanFilter` or write from scratch; either is fine — this is reference code, not production). Backward pass: standard RTS. Output conforms to `GroundTruth` schema (TRD §1.3). CLI: `python -m evaluation smooth --trace day2`. Extend Makefile `eval` target with STAGE sub-selection. Unit test with a known-answer synthetic trajectory (known state + measurement noise → smoother recovers state within known bound).
- **Est.:** 3h

---

### T3.2 — RMSE harness (FR-6.2)

- **Covers:** FR-6.2
- **Blockers:** T2.8, T3.1
- **Deliverables:** `src/evaluation/rmse.py`, `rmse_report.json` writer.
- **DoD:**
  - [ ] Overall RMSE, GPS-only RMSE, per-minute breakdown all computed.
  - [ ] `rmse_report.json` schema matches TRD §1.10.
  - [ ] **PRD S1 assertion: EKF RMSE ≤ 0.75 × GPS-only RMSE on day2.**
  - [ ] Test fails (loudly) if S1 doesn't pass — this is the gate.
- **Claude Code prompt:**
  > Implement FR-6.2 per FRD and TRD §1.10. Compute horizontal RMSE between `fused_ekf.parquet` (or `fused_ukf.parquet`) and `ground_truth.parquet`. Also compute the same RMSE using raw GPS positions from `aligned_100hz.parquet` as a baseline. Emit `rmse_report.json` per TRD §1.10 with per-minute breakdown. CLI: `python -m evaluation rmse --trace day2 --filter ekf`. Set exit code 4 (gate failure per TRD §4.5) if `overall_rmse_m >= 0.75 * gps_only_rmse_m`. Add a test `tests/integration/test_s1_gate.py` that runs the full pipeline on day2 and asserts S1 passes.
- **Est.:** 2h

---

### T3.3 — NEES + innovation statistics (FR-6.3)

- **Covers:** FR-6.3
- **Blockers:** T3.2
- **Deliverables:** NEES/innovation fields in `rmse_report.json`; flag when inconsistent.
- **DoD:**
  - [ ] Mean NEES computed; flag if outside [3.0, 7.0] for 5D state.
  - [ ] Report includes `nees_mean`, `nees_ci_95`, `nees_consistent` boolean.
- **Claude Code prompt:**
  > Extend `src/evaluation/rmse.py` (or add `src/evaluation/nees.py`) to compute Normalized Estimation Error Squared per FRD §FR-6.3. Consume `/fused/diagnostics` (from T2.6) for rejection count and live NEES, and compute post-hoc NEES from `fused_ekf.parquet` + `ground_truth.parquet` covariances. Extend the `rmse_report.json` schema with `nees_mean`, `nees_ci_95`, `nees_consistent`, `rejection_count`, `rejection_rate`. If `nees_consistent == False`, add a `notes` field flagging "Q or R possibly mis-tuned". Unit tests on synthetic states with known covariance.
- **Est.:** 2h

---

### T3.4 — EKF vs UKF comparator (FR-6.4)

- **Covers:** FR-6.4
- **Blockers:** T3.2
- **Deliverables:** `src/evaluation/comparator.py`, `filter_comparison.json`.
- **DoD:**
  - [ ] Per-segment winner identified (or "equivalent" declared).
  - [ ] Segments split by absolute curvature (straight vs turns).
  - [ ] Report includes delta-RMSE per segment.
- **Claude Code prompt:**
  > Implement FR-6.4 per FRD. `src/evaluation/comparator.py` loads `fused_ekf.parquet` and `fused_ukf.parquet`, segments the trip by path curvature (use `abs(psi_dot_rps) > 0.05` as the "turning" threshold), computes RMSE per segment per filter, and emits `filter_comparison.json` with per-segment winner or "equivalent (|delta| < 0.3 m)". CLI: `python -m evaluation compare --trace day2`. Add to Makefile `eval` target. Test with synthetic straight + curve fixture.
- **Est.:** 2h

---

### P3 Gate (PRD S1)

- [ ] `make eval TRACE=day2 FILTER=ekf` exits 0 (S1 passes).
- [ ] `rmse_report.json` shows `s1_pass: true` and `improvement_pct >= 25`.
- [ ] NEES is flagged consistent (or, if not, a PR notes the Q/R tuning fix).
- [ ] `filter_comparison.json` exists for the record.

**If S1 doesn't pass:** investigate before P4. Likely causes: (a) Q is too small (filter too confident, rejects good GPS); (b) R not adaptive enough; (c) initialization bias. This is a debugging phase, not an implementation phase — add a task T3.5 if needed.

---

## 7. Phase P4 — Ideal driver + scoring

Ends with: `make score TRACE=day2` produces a valid `score.json`.

---

### T4.1 — Valhalla integration + map-matching client (FR-9.1)

- **Covers:** FR-9.1
- **Blockers:** T0.2 (for compose), T2.8 (for fused trajectory)
- **Deliverables:** `src/ideal_driver/valhalla_client.py`, Valhalla tile config for NC, `route_matched.parquet`.
- **DoD:**
  - [ ] Valhalla container starts via `docker compose up valhalla`.
  - [ ] NC tiles pre-generated or downloaded at first run (documented in README).
  - [ ] ≥ 98 % of day2 ticks snap within 10 m.
  - [ ] Wade Ave correctly identified by OSM way ID.
- **Claude Code prompt:**
  > Implement FR-9.1 per FRD and TRD §1.4. Stand up a local Valhalla Meili map-matching service via `docker-compose.yml` (already added in T0.2 — verify or extend). Tile coverage: North Carolina only; use the `gisops/valhalla` image's built-in tile-download for OSM NC extract from Geofabrik (or document a manual step in `docker/valhalla/README.md`). `src/ideal_driver/valhalla_client.py` wraps the Meili `/trace_attributes` endpoint: input = list of `(t_s, lat, lon)` from `fused_ekf.parquet` (sub-sampled to 5 Hz to keep payload small), output = `route_matched.parquet` per TRD §1.4. Handle Valhalla-unreachable points (NaN output, log warning, exit code 3 if > 5% unmatched). Wire Makefile `ideal` target (stage a: map-match). Test with a tiny synthetic route (mock Valhalla) and with day2 live (network-dependent; mark with `@pytest.mark.integration`).
- **Est.:** 4h

---

### T4.2 — Speed limits + reference path (FR-9.2, FR-9.3)

- **Covers:** FR-9.2, FR-9.3
- **Blockers:** T4.1
- **Deliverables:** `src/ideal_driver/speed_limits.py`, `src/ideal_driver/reference_path.py`, `config/speed_limits.yaml`, `reference_path.parquet`.
- **DoD:**
  - [ ] Every OSM way touched by day2 resolves to a speed limit (tagged or hand-coded).
  - [ ] Reference path sampled every 1 m.
  - [ ] Curvature finite everywhere (spline-smoothed at kinks).
- **Claude Code prompt:**
  > Implement FR-9.2 and FR-9.3 per FRD and TRD §1.5. `speed_limits.py` reads OSM way IDs from `route_matched.parquet`, looks up `maxspeed` tags (via Valhalla's `way` attribute in the trace response — Valhalla already has them). For ways without a tag, fall back to `config/speed_limits.yaml` (hand-coded for Saint Mary's, Wade Ave, I-440). Urban default: 13.4 m/s (30 mph). `reference_path.py` takes the snapped polyline, resamples to 1 m arc length, computes signed curvature via 5-point finite differences on cumulative heading. Smooth curvature over 5 m windows where |κ| > 0.1 (kinks at OSM node junctions). Output matches TRD §1.5 `ReferencePath`. Create `config/speed_limits.yaml` stub; run on day2 and populate it with any ways that fell back to the default. Extend Makefile `ideal` (stage b).
- **Est.:** 3h

---

### T4.3 — Ideal speed profile (FR-9.4)

- **Covers:** FR-9.4
- **Blockers:** T4.2
- **Deliverables:** `src/ideal_driver/speed_profile.py`, `ideal_speed.parquet`.
- **DoD:**
  - [ ] Resulting profile respects all four limits: `|a_lon| ≤ 1.5` (accel), `|a_lon| ≤ 2.5` (decel), `|a_lat| ≤ 2.0`, `|j| ≤ 2.0`.
  - [ ] At Wade Ave's sharpest curve, `v_ideal < speed_limit` (curvature-limited regime active).
  - [ ] Unit test against synthetic corner with analytical answer.
- **Claude Code prompt:**
  > Implement FR-9.4 per FRD and TRD §1.6. Two-pass algorithm: (forward) for each point `s`, cap `v` at `min(speed_limit(s), sqrt(a_lat_max / |curvature(s)|))`; (backward + forward) enforce longitudinal accel/decel/jerk limits by iterating until the profile respects all constraints. Parameters from `config/ideal.yaml` (create it with the values from FRD §FR-9.4). Output conforms to `IdealSpeed` schema. Unit test: synthetic "approach a 20m-radius curve" path has an analytical `v_target = sqrt(a_lat_max * R) = sqrt(2 * 20) ≈ 6.32 m/s` at the apex; assert solver matches within 0.1 m/s. Extend Makefile `ideal` (stage c).
- **Est.:** 3h

---

### T4.4 — Quintic-polynomial trajectory synthesis (FR-9.5)

- **Covers:** FR-9.5, FR-9.6
- **Blockers:** T4.3
- **Deliverables:** `src/ideal_driver/quintic.py`, `ideal_trajectory.parquet`, QA PNG.
- **DoD:**
  - [ ] Trajectory is C² continuous at segment boundaries (numerical check).
  - [ ] Stays within 0.5 m of reference centerline everywhere.
  - [ ] QA PNG shows ideal vs fused overlaid on map.
- **Claude Code prompt:**
  > Implement FR-9.5 and FR-9.6 per FRD and TRD §1.7. Segment `ideal_speed.parquet` at curvature extrema; fit a jerk-minimizing quintic polynomial in each segment with boundary conditions (position, velocity, acceleration) matched at segment ends. Convert from arc-length parameterization back to time domain. Output `ideal_trajectory.parquet` per TRD §1.7. Numerically verify C² continuity at segment boundaries (differences < 1e-3 in jerk across boundaries). FR-9.6 visual QA: render `fused_ekf` and `ideal_trajectory` overlaid on a Folium map, save as `out/{trip_id}/qa_ideal_vs_actual.html` (we skip the PNG — HTML with Leaflet is more useful). Extend Makefile `ideal` (stage d, final).
- **Est.:** 3h

---

### T4.5 — Scoring components: jerk, harsh brake, lat accel (FR-10.1–10.3)

- **Covers:** FR-10.1, FR-10.2, FR-10.3
- **Blockers:** T4.4
- **Deliverables:** `src/scoring/components.py` (partial), unit tests.
- **DoD:**
  - [ ] Each penalty ∈ [0, 1].
  - [ ] `jerk_penalty` monotonic in harshness (test).
  - [ ] `harsh_brake_penalty` zero on day2 (calm drive).
  - [ ] Lateral accel uses body-frame (gravity-decomposed), not raw phone axis.
- **Claude Code prompt:**
  > Implement FR-10.1, FR-10.2, FR-10.3 in `src/scoring/components.py`. Inputs: fused parquet, ideal trajectory parquet. Each function returns a scalar in [0, 1]. Normalization constants from `config/scoring.yaml` (create it with defaults from PRD §5.5). `jerk_penalty(fused, ideal)`: integrate `max(0, |j_actual| - |j_ideal|)` over trip, normalize by trip duration, clip at 1.0 with saturation. `harsh_brake_penalty(fused)`: count decel events > 3.5 m/s² lasting ≥ 0.3 s (with hysteresis to avoid double-counting); normalize to events-per-minute, clip. `lat_accel_penalty(fused, ideal)`: integrate `max(0, |a_lat| - a_lat_ideal)²`, normalize. Lateral accel from fused Odometry is body-frame via the gravity decomposition already done in ingestion. Unit tests: synthetic calm drive scores < 0.05 per component; synthetic harsh drive scores > 0.5.
- **Est.:** 3h

---

### T4.6 — Scoring components: speed, deviation, lane change (FR-10.4–10.6)

- **Covers:** FR-10.4, FR-10.5, FR-10.6
- **Blockers:** T4.5
- **Deliverables:** `components.py` (complete).
- **DoD:**
  - [ ] Speed penalty quadratic-ish in excess over limit.
  - [ ] ±2 mph tolerance band free.
  - [ ] Route deviation penalty ~0 within ±1.5 m (in-lane).
  - [ ] Lane-change detection distinguishes true lane changes from swerves.
- **Claude Code prompt:**
  > Complete `src/scoring/components.py` with FR-10.4, FR-10.5, FR-10.6. `speed_penalty(fused, reference_path)`: time-weighted integral of `max(0, v - speed_limit)²` with a ±2 mph (≈ 0.89 m/s) tolerance band (no penalty within it). `deviation_penalty(fused, reference_path)`: integrate lateral distance from matched centerline. `lane_change_penalty(fused)`: detect yaw excursions Δψ > 0.15 rad over < 2 s, correlated with lateral displacement > 2 m for > 3 s (distinguishes actual lane changes from swerves). Thresholds in `config/scoring.yaml`. Unit tests: a synthetic "5 mph over for 60 s" scores worse than "1 mph over for 300 s"; an in-lane drive scores near 0 on deviation; a clean highway lane change is detected once, a swerve is not detected.
- **Est.:** 3h

---

### T4.7 — Aggregate score + tip lookup (FR-10.7, FR-10.8)

- **Covers:** FR-10.7, FR-10.8
- **Blockers:** T4.6
- **Deliverables:** `src/scoring/aggregate.py`, `src/scoring/tip_lookup.py`, `score.json`.
- **DoD:**
  - [ ] `score.json` schema matches TRD §1.8 exactly.
  - [ ] Config hash included (sha256 over `scoring.yaml` + `ideal.yaml`).
  - [ ] Tip band lookup is data-driven (from `config/scoring.yaml`).
  - [ ] `make score TRACE=day2` works end-to-end.
- **Claude Code prompt:**
  > Implement FR-10.7 and FR-10.8 per FRD and TRD §1.8. `aggregate.py` combines all six components with weights from `config/scoring.yaml` into an aggregate in [0, 1], then scales to [0, 100] as `100 * (1 - aggregate)`. `tip_lookup.py` maps the 0–100 score to a suggested tip percentage via the lookup table in `config/scoring.yaml` (PRD §5.5 defaults: 90+ → 25%, 75–89 → 20%, 60–74 → 15%, <60 → 10%). Emit `score.json` exactly per TRD §1.8, including `config_hash` (sha256 of concatenated relevant configs) and the notice `"SUGGESTED — final tipping decision is manual."`. CLI: `python -m scoring score --trace day2 --filter ekf`. Wire Makefile `score` target as the full pipeline orchestrator: data → bag → fuse → ideal → score.
- **Est.:** 2h

---

### T4.8 — End-to-end smoke test (day2)

- **Covers:** Integration of P1 through P4
- **Blockers:** T4.7
- **Deliverables:** `tests/integration/test_end_to_end_day2.py`.
- **DoD:**
  - [ ] Runs full pipeline on day2 from clean state in < 5 min.
  - [ ] Asserts `score.json` valid, `aggregate_raw` < 0.3 (assumed calm drive).
  - [ ] Asserts all intermediate files exist with correct schemas.
- **Claude Code prompt:**
  > Create `tests/integration/test_end_to_end_day2.py` — an end-to-end smoke test that: (1) `make clean`, (2) `make data TRACE=day2`, (3) `make synth N=5`, (4) `make bag TRACE=day2`, (5) `make fuse TRACE=day2 FILTER=ekf`, (6) `make eval TRACE=day2 FILTER=ekf`, (7) `make ideal TRACE=day2` (requires Valhalla running; use `pytest.mark.integration` + skip if container unreachable), (8) `make score TRACE=day2`. Assert each stage's output file exists and validates against its schema. Assert `score.json.aggregate_raw < 0.3` for my own calm day2 drive. Target wall-clock: < 5 min on laptop.
- **Est.:** 2h

---

### P4 Gate

- [ ] `make score TRACE=day2` produces valid `score.json`.
- [ ] All component penalties ∈ [0, 1].
- [ ] Aggregate `day2` score is "reasonable" (my own calm drive should score high — at least 80).
- [ ] End-to-end test green.

---

## 8. Phase P5 — Reporting + validation

Ends with: **PRD S3 (local definition)** + **S4 measurable**.

---

### T5.1 — Jinja report renderer (FR-11.1)

- **Covers:** FR-11.1
- **Blockers:** T4.7
- **Deliverables:** `src/reporting/render.py`, `templates/report.html.j2`, per-trip `report.html`.
- **DoD:**
  - [ ] Self-contained HTML (no CDN deps except optional Leaflet tiles).
  - [ ] File size < 5 MB.
  - [ ] Opens correctly as `file://` in a browser.
- **Claude Code prompt:**
  > Implement FR-11.1 per FRD. `src/reporting/render.py` reads `score.json` + associated Parquets and renders `templates/report.html.j2` into a self-contained `out/{trip_id}/report.html`. Template sections: header (trip metadata, aggregate score, suggested tip band, "SUGGESTED — manual decision" disclaimer), component breakdown (placeholder div for T5.2), map overlay (placeholder div for T5.3), trip log (duration, distance, max speed). Use inline CSS, no external fonts, no CDN. Verify file size < 5 MB.
- **Est.:** 2h

---

### T5.2 — Bar chart for components (FR-11.3)

- **Covers:** FR-11.3
- **Blockers:** T5.1
- **Deliverables:** SVG bar chart embedded in report.
- **DoD:**
  - [ ] All six components labeled with human-readable names.
  - [ ] Dominant component visually obvious.
  - [ ] Inline SVG, no external assets.
- **Claude Code prompt:**
  > Implement FR-11.3 per FRD. In `src/reporting/bar_chart.py`, generate an inline SVG bar chart of the six component penalties (weighted, so the bar heights sum to the aggregate). Use a color ramp from green (low) to red (high). Labels: "Jerk", "Harsh braking", "Lateral accel", "Speed compliance", "Route deviation", "Lane changes". Embed in the report template (T5.1) via Jinja include. No JS, no external CSS.
- **Est.:** 1.5h

---

### T5.3 — Folium map overlay (FR-11.2)

- **Covers:** FR-11.2
- **Blockers:** T5.1
- **Deliverables:** Folium map HTML fragment embedded in report.
- **DoD:**
  - [ ] Actual (red) vs ideal (green) polylines.
  - [ ] Harsh-brake markers clickable.
  - [ ] Uses OpenStreetMap tiles (no API key).
- **Claude Code prompt:**
  > Implement FR-11.2 per FRD. In `src/reporting/map_overlay.py`, use Folium to render a Leaflet map with: (1) actual fused trajectory as a red polyline, (2) ideal trajectory as a green polyline, (3) markers at harsh-brake timestamps (icon: warning triangle), clickable with timestamp + decel magnitude. Tiles: OpenStreetMap default (no API key). Save as a standalone HTML fragment; inject into the main report via Jinja include. Test that opening the report as `file://` renders the map correctly without internet access (tiles are cached or omitted gracefully).
- **Est.:** 2h

---

### T5.4 — Index page + ratings ingest (FR-11.4, FR-11.5)

- **Covers:** FR-11.4, FR-11.5
- **Blockers:** T5.1
- **Deliverables:** `src/reporting/index.py`, `src/reporting/ratings.py`, `out/reports/index.html`, `config/ratings.yaml` template (in `.gitignore`).
- **DoD:**
  - [ ] Index lists all `out/*/score.json` trips.
  - [ ] Sortable (client-side vanilla JS).
  - [ ] Spearman ρ displayed once ≥ 5 rated trips.
  - [ ] Missing ratings shown as "—".
- **Claude Code prompt:**
  > Implement FR-11.4 and FR-11.5 per FRD. `index.py` globs `out/*/score.json`, reads each, and renders `out/reports/index.html` (template `templates/index.html.j2`) listing trips with columns: date, duration, aggregate score, suggested tip, subjective rating, link to per-trip report. Vanilla JS for client-side sort (no framework). `ratings.py` reads `config/ratings.yaml` (mapping `trip_id → 1..5`), merges into the index. Compute Spearman ρ across trips with both a tool score and a subjective rating; display it once ≥ 5 rated trips exist, else "n/a". Ensure `config/ratings.yaml` is in `.gitignore`; ship `config/ratings.yaml.example` instead.
- **Est.:** 2.5h

---

### T5.5 — Docker Compose end-to-end "PRD S3 (local)" proof

- **Covers:** PRD S3 at Phase 1 definition (reproducibility via `docker compose`)
- **Blockers:** T5.1–T5.4
- **Deliverables:** `scripts/run_full_pipeline.sh`, README "Quickstart from fresh clone" section.
- **DoD:**
  - [ ] Fresh clone → `docker compose up -d && ./scripts/run_full_pipeline.sh day2` produces a valid `report.html` in < 30 min (including first-time Docker pulls).
  - [ ] README quickstart verified on a clean machine (or a fresh Docker volume).
- **Claude Code prompt:**
  > Write `scripts/run_full_pipeline.sh` — a single shell script that runs the entire Phase 1 pipeline for a given TRACE argument (default `day2`). Checks prerequisites (Docker running, day2 data present under `data/day2/`), then `docker compose up -d`, waits for Valhalla to be healthy, runs all `make` targets in order (`data → synth → bag → fuse → eval → ideal → score → report`), tears down with `docker compose down`. Log every step with timing. Extend README.md with a "Quickstart" section documenting this as the PRD S3 reproducibility path, and noting: "Phase 1 reproducibility is via Docker Compose; Phase 2 will add AWS-native reproducibility." Time it on a clean machine; assert < 30 min total (including first-time image pulls).
- **Est.:** 2h

---

### T5.6 — Documentation pass + GitHub polish

- **Covers:** Shippability
- **Blockers:** T5.5
- **Deliverables:** Polished `README.md`, inline screenshots, trimmed `.gitignore` for public release, LICENSE confirmed MIT.
- **DoD:**
  - [ ] README has: vision/why, architecture diagram (embed image), quickstart, results summary, links to PRD/FRD/TRD/Dev Plan, "Phase 2 planned" note.
  - [ ] Screenshot of `report.html` committed under `docs/screenshots/`.
  - [ ] All four documents (PRD, FRD, TRD, Dev Plan) linked from the README with 1-line summaries.
  - [ ] Repo is presentable — no TODOs in code, no stub files left over, no secrets.
- **Claude Code prompt:**
  > Final polish pass. Rewrite `README.md` as a public-facing document: vision paragraph (adapt PRD §1.1), architecture diagram (ASCII or Mermaid), quickstart (link to T5.5 script), results (embed `report.html` screenshot from `docs/screenshots/`), links to the four design docs with a one-sentence purpose each ("PRD = why and what we're building; FRD = what features define done; TRD = how we build them; Dev Plan = in what order"). Add a "Phase 2 (AWS) planned" section with a brief sketch. Run `git grep -i -E 'TODO|FIXME|XXX|placeholder'` and resolve or justify each hit. Ensure the `docs/screenshots/` dir has a rendered example report. Confirm LICENSE is MIT and year is current.
- **Est.:** 2h

---

### P5 Gate (PRD S3 local + S4 measurable)

- [ ] Fresh-clone to `report.html` via docker compose in < 30 min.
- [ ] Index page renders with at least day2 scored.
- [ ] Ratings ingestion working (placeholder rating acceptable for gate; S4 validation itself requires real Uber data in Phase 1.5).
- [ ] README polished; GitHub public-ready.

---

## 9. Cross-phase logistics

### 9.1 Task execution rhythm

- One task per Claude Code session; the task's "Claude Code prompt" is the opening message.
- After the session, review the diff, run `make test` locally, commit with `{task_id}: {summary}`, open a PR, let CI green, merge.
- If a task blows past its estimate by > 2×, stop, audit what went wrong, possibly re-scope or split.

### 9.2 Branching

- `main` is always green.
- Feature branches `phase{N}/T{N}.{n}-{slug}` per task.
- PRs are small, 1 task each, reviewed by self with a 24-hour cool-down before merge if possible.

### 9.3 When a gate fails

- Do not proceed to the next phase. Open a debug task (e.g. T3.5) to diagnose.
- Gate failures are fine and expected; they are the reason gates exist.
- If a gate failure reveals a FRD or TRD error, revise those documents first, then fix the code.

### 9.4 Out-of-scope items surfacing during implementation

If, during a task, a thing that "really should be done" appears but isn't in any current task:

- < 30 min fix: do it, note in PR description as "in-session scope creep, small".
- ≥ 30 min: stop, file an issue, return to the task as scoped. Creep kills plans.

### 9.5 What a "done" Phase 1 looks like

- PRD §2.3 criteria S1, S2, S3 (local def.), S5 (Phase 1 scope) all pass.
- S4 is *measurable* (the pipeline produces Spearman ρ); *passing* S4 requires real Uber rides, which is a Phase 1.5 data-collection effort, not an implementation effort.
- Repo public, README polished, four design docs pushed.
- Resume one-liner updated: "End-to-end autonomous-navigation digital twin on ROS 2 (Phase 1: local; Phase 2: AWS planned), EKF/UKF sensor fusion of mobile IMU/GPS/magnetometer for post-hoc rideshare scoring."

---

## 10. Phase 1.5 (between Phase 1 and Phase 2) — data collection

Not a code phase, but acknowledged here:

- Record ≥ 8 Uber/Lyft rides with Sensor Logger.
- Write a subjective 1–5 rating within 10 minutes of each ride, before running the tool.
- Run `make score` on each.
- At n = 8, compute Spearman ρ. If < 0.6, one weight recalibration (documented in `config/scoring.yaml` changelog), then re-assess.

This is the true validation of PRD S4. Without it, Phase 1 "works" but hasn't proven its stated purpose.

---

## 11. Revision log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-04-19 | Initial Phase 1 plan, 37 tasks across 6 phases |
