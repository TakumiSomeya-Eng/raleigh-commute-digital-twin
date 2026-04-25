# FRD — Raleigh Commute Digital Twin: *Uber vs. My AI*

**Document version:** 1.0
**Status:** Draft for review
**Owner:** Takumi
**Last updated:** 2026-04-19
**Companion to:** `PRD.md` v2.0
**Governs:** Functional scope and completion criteria for all code-level features.

---

## 0. How to read this document

Each functional requirement (FR) has the following fields:

| Field | Meaning |
|---|---|
| **ID** | `FR-X.Y` — stable reference used in TRD and Dev Plan |
| **Name** | Short human-readable label |
| **Description** | 1–3 sentences stating what the feature does |
| **Inputs / Outputs** | Concrete artifact types and paths. Schemas live in the TRD. |
| **Acceptance Criteria** | Objective, verifiable bullets. "Done" means all boxes check. |
| **Priority** | `MUST` / `SHOULD` / `COULD` (MoSCoW) |
| **Dependencies** | Other FR IDs, or `—` |

**Priority semantics:**

- **MUST**: Required for the tool to do its stated job (PRD §0 one-line pitch). Absence = broken.
- **SHOULD**: Required for the tool to be *good*. Absence = works but embarrassing.
- **COULD**: Nice to have. Absence = no one notices.

**Implicit rule:** Every FR that produces a file is idempotent — re-running with the same inputs produces a byte-identical output (modulo timestamps in metadata). This is a non-functional requirement enforced globally in the TRD.

---

## 1. Design Defaults (locked-in for v1 unless revised)

These are decisions made in advance of implementation to avoid bikeshedding mid-build. Each is revisable in FRD v1.1 if experience contradicts it.

| Area | Default | Rationale |
|---|---|---|
| Map-matching | **Valhalla Meili** (self-hosted Docker) | Higher accuracy than OSRM on noisy GPS; API stable; no cloud dependency |
| Trajectory synthesis | **Quintic polynomial (jerk-minimizing), segment-wise** | Closed-form, differentiable, well-studied for AV planning; QP is overkill for a single-ego baseline |
| Reporting | **Jinja2 → static HTML**, **Folium** for map overlays | No API keys, no runtime; renders anywhere a browser exists |
| AWS compute mix | **ECS Fargate** for Python steps, **EKS (EC2)** for ROS 2 nodes | DDS needs pod-pod UDP; Fargate fine for batch; Step Functions orchestrates both |
| Cost ceiling | **< $50/month** steady state, **< $10/eval-run** | Hard non-functional requirement (see FR-12.6) |
| Local dev parity | **`docker compose` mirror** of the EKS topology | Fast laptop iteration without AWS |
| Intermediate format | **Parquet** for tabular, **MCAP** (ROS 2 default) for time-series replay | CSV forbidden past the ingestion boundary |

---

## 2. Scope map (PRD → FRD)

| PRD milestone | FR coverage |
|---|---|
| Step 1 (Data + noise) | FR-1, FR-2 |
| Step 2 (Fusion) | FR-3, FR-4, FR-5, FR-6 |
| Step 3 (Infra) | FR-12 |
| Step 4 (CI/CD) | FR-7, FR-8 (+ FR-12.5) |
| Step 5 (Ideal + scoring) | FR-9, FR-10 |
| Step 6 (Reports) | FR-11 |

Cross-cutting (applies to all): FR-7 (CLI/build), FR-8 (testing).

---

## 3. Functional Requirements

---

### FR-1 — Data Ingestion

**Purpose:** Convert raw Sensor Logger CSVs from one recording session into a single time-aligned, projected, schema-validated Parquet file ready for downstream consumption.

---

#### FR-1.1 — Sensor Logger CSV parser

- **Description:** Parse the seven sensor channels used by the stack (Location, Accelerometer, Gyroscope, Gravity, Orientation, Magnetometer, TotalAcceleration) from a Sensor Logger export directory.
- **Inputs:** Directory path containing the CSVs of one session (e.g. `data/day1/`).
- **Outputs:** In-memory dataframes per channel with typed columns, or a structured error if a required file is missing or malformed.
- **Acceptance Criteria:**
  - [ ] Parses all seven channels from both `day1` and `day2` fixtures without error.
  - [ ] Rejects a session missing `Location.csv` or `Accelerometer.csv` with a typed exception (`MissingRequiredChannelError`).
  - [ ] Timestamp column (`time`, epoch-ns) is read as `int64`, not float (no precision loss).
  - [ ] Unit-tested against both fixture sessions.
- **Priority:** MUST
- **Dependencies:** —

---

#### FR-1.2 — Clock alignment to 100 Hz common grid

- **Description:** Resample all channels onto a single 100 Hz timeline expressed in `seconds_elapsed` (relative to recording start). Location (1 Hz) is interpolated; IMU channels (~100 Hz) are re-timed to the exact grid.
- **Inputs:** Per-channel dataframes from FR-1.1.
- **Outputs:** Single dataframe, one row per 10 ms tick, columns for every sensor channel, NaN-free after warm-up period.
- **Acceptance Criteria:**
  - [ ] Output timestamps are `[0.00, 0.01, 0.02, …]` to machine precision.
  - [ ] Linear interpolation for Location; zero-order-hold explicitly forbidden (would create synthetic step discontinuities).
  - [ ] First 0.5 s of the output is dropped as warm-up (IMU sensors typically aren't settled).
  - [ ] Unit test: inject a known-sinusoid signal → output preserves it within 1 % amplitude error.
- **Priority:** MUST
- **Dependencies:** FR-1.1

---

#### FR-1.3 — WGS-84 → local ENU projection

- **Description:** Project GPS `(lat, lon)` to a local East-North-Up frame anchored at a fixed reference point (`35.773 °N, −78.610 °W`, corridor centroid). Uses flat-earth / equirectangular approximation (valid for the < 10 km span used).
- **Inputs:** Aligned dataframe from FR-1.2.
- **Outputs:** Same dataframe with two new columns `px_m`, `py_m` (local ENU in meters). `lat, lon` are retained for reporting.
- **Acceptance Criteria:**
  - [ ] Reference anchor is a config-file constant (`config/data_gen.yaml`), not hard-coded.
  - [ ] Unit test: a 1° latitude delta at the anchor projects to ~111,000 m ±1 % on the north axis.
  - [ ] Round-trip ENU → WGS-84 → ENU is identity within 0.1 m for the day2 trace.
- **Priority:** MUST
- **Dependencies:** FR-1.2

---

#### FR-1.4 — Parquet output with declared schema

- **Description:** Write the aligned, projected dataframe to Parquet with a pydantic-declared schema. Schema lives in `src/data_engine/schemas.py` as the single source of truth.
- **Inputs:** Dataframe from FR-1.3.
- **Outputs:** File at `{out_dir}/{trip_id}/aligned_100hz.parquet`. Snappy compression.
- **Acceptance Criteria:**
  - [ ] Parquet round-trips (write → read) as bit-identical dataframe.
  - [ ] Schema violation (e.g. NaN in `px_m`) raises `SchemaValidationError` before write.
  - [ ] File size for day2 (~14.8 min @ 100 Hz) is between 2 MB and 20 MB. (Sanity check — catches accidental column explosions.)
- **Priority:** MUST
- **Dependencies:** FR-1.3

---

#### FR-1.5 — CLI entry point

- **Description:** A single `make data TRACE=day1` target runs FR-1.1 through FR-1.4 end-to-end on one session.
- **Inputs:** `TRACE` Make variable naming a subdirectory under `data/`.
- **Outputs:** Parquet file as per FR-1.4, plus a one-line stdout summary (rows, duration, mean horiz-acc).
- **Acceptance Criteria:**
  - [ ] `make data TRACE=day1 && make data TRACE=day2` both succeed from a clean checkout.
  - [ ] Exit code 0 on success, non-zero on any upstream failure.
  - [ ] `--dry-run` flag lists what would be written without writing.
- **Priority:** MUST
- **Dependencies:** FR-1.4, FR-7.1

---

### FR-2 — Noise Modeling & Synthetic Generation

**Purpose:** Extract empirical noise distributions from the real calibration traces, then generate perturbed synthetic scenarios that stay statistically inside the real-data support. Guards against filter overfitting (see PRD S2).

---

#### FR-2.1 — Empirical noise fit

- **Description:** For each sensor channel, fit a parametric noise model (Gaussian for IMU, Rayleigh or truncated log-normal for GPS `horizontalAccuracy`, von Mises for bearing) to the residuals after a reference smoother.
- **Inputs:** Aligned Parquet files from FR-1.4 for day1 AND day2.
- **Outputs:** `config/noise_fit.yaml` with fitted parameters per channel.
- **Acceptance Criteria:**
  - [ ] Fit converges for all seven used channels on both days.
  - [ ] Fitted parameters for day1 and day2 agree within 2σ (else emit warning — suggests the calibration set is too small).
  - [ ] QQ-plot per channel saved as PNG for visual inspection (FR-11 reuses these).
- **Priority:** MUST
- **Dependencies:** FR-1.4

---

#### FR-2.2 — Synthetic scenario generator

- **Description:** Given a "base" real trip and a scenario config, generate a perturbed Parquet file with injected noise (and optional stress events: GPS dropout, IMU bias jump, magnetic anomaly).
- **Inputs:** Base Parquet (from FR-1.4), `config/data_gen.yaml` specifying seed and stress events.
- **Outputs:** `{out_dir}/synthetic/{scenario_id}/aligned_100hz.parquet`.
- **Acceptance Criteria:**
  - [ ] Fully deterministic given the seed (two runs with the same seed → byte-identical output).
  - [ ] Seed is written into the file metadata and into `scenario_manifest.json`.
  - [ ] Supports at least three stress event types: `gps_dropout(start_s, end_s)`, `imu_bias_step(axis, delta)`, `mag_anomaly(start_s, duration_s)`.
- **Priority:** MUST
- **Dependencies:** FR-2.1

---

#### FR-2.3 — KS-test guardrail

- **Description:** Two-sample Kolmogorov–Smirnov test, per channel, between N real-trip samples and N synthetic-trip samples. Fails the pipeline if too many channels drift.
- **Inputs:** A directory of real Parquets and a directory of synthetic Parquets.
- **Outputs:** `ks_report.json` + exit code.
- **Acceptance Criteria:**
  - [ ] Report contains p-value per channel.
  - [ ] Exit code 0 iff `p > 0.05` on ≥ 80 % of channels (PRD S2 threshold).
  - [ ] CI integration point: this is the gate that blocks Step 1 merges.
- **Priority:** MUST
- **Dependencies:** FR-2.2

---

#### FR-2.4 — Batch scenario orchestrator

- **Description:** Generate `n ≥ 10` scenarios in one call, each with an independent seed, from one base trip.
- **Inputs:** Base trip + count.
- **Outputs:** `n` Parquet files + `scenario_manifest.json` listing all seeds and stress events.
- **Acceptance Criteria:**
  - [ ] Parallel execution (multiprocessing pool) for speed — 100 scenarios should take < 60 s on a laptop.
  - [ ] Manifest is append-safe (re-running adds new scenarios without clobbering prior ones).
- **Priority:** SHOULD
- **Dependencies:** FR-2.2

---

### FR-3 — ROS 2 Bag Conversion

**Purpose:** Convert Parquet (the Python-side interchange format) into MCAP (the ROS 2 replay format) so the C++ fusion nodes can consume the same data without re-parsing CSVs.

---

#### FR-3.1 — Parquet → MCAP converter

- **Description:** Read a Parquet aligned-100 Hz file and emit a ROS 2 bag (MCAP) with three topics: `/gps/fix` (`sensor_msgs/NavSatFix`), `/imu/data` (`sensor_msgs/Imu`), `/mag` (`sensor_msgs/MagneticField`).
- **Inputs:** Parquet file from FR-1.4 or FR-2.2.
- **Outputs:** `{out_dir}/{trip_id}/trip.mcap`.
- **Acceptance Criteria:**
  - [ ] Resulting bag plays correctly with `ros2 bag play` and `ros2 bag info` reports expected durations and message counts.
  - [ ] Message timestamps preserve nanosecond precision (Parquet int64 → ROS `builtin_interfaces/Time`).
  - [ ] `Imu.orientation_covariance[0] = -1` where orientation is unavailable (ROS convention).
  - [ ] `NavSatFix.position_covariance_type` set to `COVARIANCE_TYPE_DIAGONAL_KNOWN` with `horizontalAccuracy²` on the diagonal.
- **Priority:** MUST
- **Dependencies:** FR-1.4

---

#### FR-3.2 — Bag metadata YAML

- **Description:** Emit a sidecar `trip.metadata.yaml` describing the bag (trip_id, duration, message counts, checksum).
- **Inputs:** Bag from FR-3.1.
- **Outputs:** YAML next to the bag.
- **Acceptance Criteria:**
  - [ ] Contains SHA-256 of the MCAP file for later integrity checks.
  - [ ] Parsed by the fusion pipeline before it starts playback (fails fast on mismatch).
- **Priority:** SHOULD
- **Dependencies:** FR-3.1

---

### FR-4 — Localization: EKF Node

**Purpose:** Implement a CTRV-model Extended Kalman Filter that fuses GPS and IMU to produce a smoothed odometry stream.

---

#### FR-4.1 — CTRV motion model library

- **Description:** A header-only C++ library (`src/localization/ctrv_model.hpp`) with the CTRV state-transition function, its Jacobian, and a covariance propagator. Shared between EKF and UKF nodes.
- **Inputs:** State vector, control input (optional longitudinal accel), dt.
- **Outputs:** Predicted state + Jacobian.
- **Acceptance Criteria:**
  - [ ] Straight-line prediction (ψ̇ = 0) matches constant-velocity trivially within 1e-9.
  - [ ] Circular-motion test: 10 s at v = 10 m/s, ψ̇ = 0.1 rad/s → state returns to start within 1e-6.
  - [ ] Jacobian verified numerically (finite-difference vs. analytical) within 1e-6.
  - [ ] gtest unit tests cover edge cases: ψ̇ → 0 (l'Hôpital limit), ψ wraparound at ±π.
- **Priority:** MUST
- **Dependencies:** —

---

#### FR-4.2 — `ekf_node` implementation

- **Description:** A ROS 2 node that subscribes to `/gps/fix`, `/imu/data`, `/mag`, runs an EKF using FR-4.1, and publishes `/fused/odom` (`nav_msgs/Odometry`).
- **Inputs:** Three topics listed above.
- **Outputs:** `/fused/odom` at 100 Hz.
- **Acceptance Criteria:**
  - [ ] Runs headless against the day2 MCAP bag and completes without crashing.
  - [ ] Publish rate is 100 ±1 Hz (matches IMU prediction step).
  - [ ] All parameters (process noise `Q`, measurement noise base `R`, outlier gate χ² threshold) loaded from `config/ekf.yaml`, not hard-coded.
  - [ ] No `std::cout` in hot path; uses ROS 2 logger at configurable level.
- **Priority:** MUST
- **Dependencies:** FR-4.1, FR-3.1

---

#### FR-4.3 — Adaptive GPS measurement noise

- **Description:** The measurement covariance R for GPS position is set per-measurement from the `position_covariance` field of `NavSatFix` (populated from Sensor Logger's `horizontalAccuracy`).
- **Inputs:** GPS message.
- **Outputs:** Per-measurement R matrix.
- **Acceptance Criteria:**
  - [ ] A fix with `horizontalAccuracy = 100` contributes ~ (100/3)² less weight than a fix with `horizontalAccuracy = 3`.
  - [ ] Unit-tested with a synthetic sequence including a 122 m outlier (day1 reproducer).
- **Priority:** MUST
- **Dependencies:** FR-4.2

---

#### FR-4.4 — χ² outlier gate

- **Description:** Before applying a measurement, compute innovation Mahalanobis distance. If it exceeds the χ² threshold at 99 % for the measurement dimension, reject the update and log the rejection.
- **Inputs:** Innovation, innovation covariance.
- **Outputs:** Boolean accept/reject + rejection counter on the node.
- **Acceptance Criteria:**
  - [ ] On day1, at least the 122 m outlier is rejected.
  - [ ] Rejection rate overall is < 5 % (else the gate is mis-tuned).
  - [ ] Rejected-message count is published on `/fused/diagnostics` as part of FR-4.5.
- **Priority:** MUST
- **Dependencies:** FR-4.2

---

#### FR-4.5 — Diagnostics topic

- **Description:** A `/fused/diagnostics` topic (`diagnostic_msgs/DiagnosticArray`) published at 1 Hz with rejection count, mean NEES, current Q/R, and filter health enum.
- **Inputs:** Internal filter state.
- **Outputs:** Topic.
- **Acceptance Criteria:**
  - [ ] Visible in `ros2 topic echo`.
  - [ ] Consumed by FR-6.3 (evaluation harness).
- **Priority:** SHOULD
- **Dependencies:** FR-4.2

---

### FR-5 — Localization: UKF Node

**Purpose:** Parallel implementation using an Unscented Kalman Filter, to compare against EKF on turn segments where linearization is worst.

---

#### FR-5.1 — Sigma-point generator and reconstructor

- **Description:** Julier–Uhlmann scaled sigma points (2n+1 points) with configurable α, β, κ.
- **Inputs:** State mean, covariance.
- **Outputs:** Sigma points + weights.
- **Acceptance Criteria:**
  - [ ] Weighted mean of reconstructed sigma points equals original mean within 1e-12.
  - [ ] Weighted covariance equals original covariance within 1e-10.
  - [ ] Unit-tested with 5-D state (CTRV size).
- **Priority:** MUST
- **Dependencies:** —

---

#### FR-5.2 — `ukf_node` implementation

- **Description:** Mirror of `ekf_node` using sigma-point propagation instead of Jacobians.
- **Inputs:** Same three topics as FR-4.2.
- **Outputs:** `/fused/odom` (same topic — one filter runs at a time, selected by launch parameter) or `/fused/odom_ukf` (both simultaneously, selected by `--compare` flag).
- **Acceptance Criteria:**
  - [ ] On a straight segment (day2, minutes 2–4), UKF output matches EKF within 0.5 m RMSE (both should be near-identical where linearization is fine).
  - [ ] On turn segments (Wade Ave), divergence is measured and reported — this is the interesting result.
  - [ ] Shares `ctrv_model.hpp` (FR-4.1) — no duplicate motion model.
- **Priority:** MUST
- **Dependencies:** FR-5.1, FR-4.1, FR-3.1

---

### FR-6 — Filter Evaluation

**Purpose:** Compute S1 metrics from the fused outputs. This is where the filter's merit is quantified.

---

#### FR-6.1 — Ground-truth reference generator

- **Description:** Run an RTS (Rauch–Tung–Striebel) smoother offline over the full trip using all GPS fixes to produce a "soft ground truth" trajectory. This is the baseline the on-line filters are scored against.
- **Inputs:** Aligned Parquet (FR-1.4).
- **Outputs:** `{trip_id}/ground_truth.parquet` with smoothed `px_m`, `py_m`, `v`, `ψ`.
- **Acceptance Criteria:**
  - [ ] Smoother is batch, offline — allowed to look ahead.
  - [ ] Output is smoother (by integrated-curvature metric) than the raw GPS input.
  - [ ] Documented in the eval report as "soft GT" — the tool never claims this is absolute truth.
- **Priority:** MUST
- **Dependencies:** FR-1.4

---

#### FR-6.2 — RMSE harness

- **Description:** Compute horizontal RMSE between on-line fused output (`/fused/odom` recorded during bag playback) and soft ground truth.
- **Inputs:** Fused odom (recorded to bag or parquet), soft GT.
- **Outputs:** `rmse_report.json`: overall RMSE, per-segment RMSE, GPS-only-baseline RMSE (for S1 comparison).
- **Acceptance Criteria:**
  - [ ] For day2, EKF RMSE < 0.75 × GPS-only RMSE (the S1 target: ≥ 25 % improvement).
  - [ ] Report includes per-minute breakdown so we can see *where* the filter helps.
- **Priority:** MUST
- **Dependencies:** FR-4.2, FR-6.1

---

#### FR-6.3 — NEES and innovation statistics

- **Description:** Normalized Estimation Error Squared and innovation chi-squared histograms, both computed post-hoc.
- **Inputs:** Diagnostics stream from FR-4.5 + ground truth.
- **Outputs:** Extended fields in `rmse_report.json`.
- **Acceptance Criteria:**
  - [ ] Mean NEES is within the 95 % confidence interval for the state dimension (5 → roughly 3.0–7.0).
  - [ ] If outside, the report flags "filter is inconsistent — Q or R mis-tuned".
- **Priority:** SHOULD
- **Dependencies:** FR-4.5, FR-6.1

---

#### FR-6.4 — EKF vs UKF comparator

- **Description:** Run both filters on the same bag and produce a side-by-side report.
- **Inputs:** Two fused-odom streams.
- **Outputs:** `filter_comparison.json`, per-segment winner.
- **Acceptance Criteria:**
  - [ ] Identifies at least one segment where the two differ by > 0.5 m (or reports "filters are equivalent on this data", which is itself a valid finding).
- **Priority:** SHOULD
- **Dependencies:** FR-4.2, FR-5.2, FR-6.2

---

### FR-7 — CLI & Build System

**Purpose:** One-command entry points for every stage; reproducible from a fresh clone.

---

#### FR-7.1 — Top-level Makefile

- **Description:** Make targets: `bootstrap`, `data`, `synth`, `bag`, `fuse`, `eval`, `ideal`, `score`, `report`, `deploy`, `clean`, `test`.
- **Inputs:** Make variables (`TRACE`, `FILTER`, `N_SCENARIOS`).
- **Outputs:** Stage-appropriate artifacts.
- **Acceptance Criteria:**
  - [ ] `make help` lists all targets with one-line descriptions.
  - [ ] No target writes outside `out/`, `build/`, or the deploy targets — enforced by `.gitignore` + CI check.
  - [ ] Every target prints a one-line completion summary including output path.
- **Priority:** MUST
- **Dependencies:** —

---

#### FR-7.2 — Dockerfiles (dev + runtime)

- **Description:** Two Dockerfiles: one for Python dev (data + eval), one for ROS 2 Jazzy runtime (fusion nodes). Both multi-stage, with a shared base.
- **Inputs:** `requirements.txt`, `package.xml` (ROS).
- **Outputs:** Local images `rct-python:dev`, `rct-ros2:dev`.
- **Acceptance Criteria:**
  - [ ] `docker build` clean in < 10 min on a first-time laptop.
  - [ ] Image sizes: Python < 1.5 GB, ROS 2 < 3 GB.
  - [ ] Hadolint passes on both.
- **Priority:** MUST
- **Dependencies:** —

---

#### FR-7.3 — `docker compose` local topology

- **Description:** `docker-compose.yml` that stands up the whole local dev stack: Python container (mounts repo), ROS 2 container (mounts bag dir), Valhalla container (for map-matching), a minimal S3-compatible service (MinIO) for dev-time storage parity.
- **Inputs:** `.env` with non-secret config.
- **Outputs:** Running local stack.
- **Acceptance Criteria:**
  - [ ] `docker compose up -d && make eval TRACE=day2` is green end-to-end without touching AWS.
  - [ ] No service requires internet access after initial `compose pull`.
- **Priority:** MUST
- **Dependencies:** FR-7.2

---

#### FR-7.4 — `bootstrap.sh`

- **Description:** Idempotent laptop-setup script: checks Python version, installs pre-commit hooks, pulls docker images, creates `out/` subtree.
- **Inputs:** —
- **Outputs:** Ready-to-develop laptop.
- **Acceptance Criteria:**
  - [ ] Re-runnable without side effects.
  - [ ] Clearly reports each step's status.
  - [ ] Exits non-zero on missing prerequisite (e.g. Docker not installed).
- **Priority:** SHOULD
- **Dependencies:** FR-7.2

---

### FR-8 — Testing Infrastructure

**Purpose:** Fast, reliable, layered tests that run locally in seconds and in CI in minutes.

---

#### FR-8.1 — pytest suite for Python modules

- **Description:** Unit + integration tests for `data_engine`, `ideal_driver`, `scoring`, `evaluation`.
- **Inputs:** Fixture Parquets (tiny slices of day1/day2 committed under `tests/fixtures/`).
- **Outputs:** Test reports.
- **Acceptance Criteria:**
  - [ ] Total run time < 30 s locally.
  - [ ] Coverage ≥ 80 % for non-glue code (filter math, projections, schemas, scoring functions).
  - [ ] All tests deterministic (explicit seeds, no network, no system time).
- **Priority:** MUST
- **Dependencies:** —

---

#### FR-8.2 — gtest suite for C++ fusion code

- **Description:** Unit tests for `ctrv_model`, sigma-point transform, chi-squared gate, and isolated EKF/UKF update step.
- **Inputs:** Synthetic state + measurement sequences.
- **Outputs:** Test reports.
- **Acceptance Criteria:**
  - [ ] Total run time < 10 s.
  - [ ] Covers Jacobian validation, sigma-point consistency, outlier rejection edge cases.
- **Priority:** MUST
- **Dependencies:** FR-4.1, FR-5.1

---

#### FR-8.3 — Integration test: headless bag replay

- **Description:** Play a 60 s slice of day2 through `ekf_node` in a container, capture `/fused/odom`, assert RMSE < threshold.
- **Inputs:** Slice bag in `tests/fixtures/`.
- **Outputs:** Pass/fail.
- **Acceptance Criteria:**
  - [ ] Runs in CI in < 3 min.
  - [ ] Failure message identifies which metric regressed.
- **Priority:** MUST
- **Dependencies:** FR-4.2, FR-7.3

---

#### FR-8.4 — Pre-commit hooks

- **Description:** `pre-commit` config with: ruff (Python lint+format), clang-format (C++), yamllint, hadolint, markdownlint, terraform fmt, check-added-large-files.
- **Inputs:** Staged git changes.
- **Outputs:** Blocks bad commits locally.
- **Acceptance Criteria:**
  - [ ] All existing repo files pass the hooks from day 1 (set baseline).
  - [ ] CI runs the same hooks to enforce on PRs (no laptop-only enforcement).
- **Priority:** SHOULD
- **Dependencies:** —

---

### FR-9 — Ideal Driver

**Purpose:** Given a trip's origin–destination and actual driven route, synthesize the trajectory a conservative, smooth, law-abiding AI driver would have produced on the same route. This is the yardstick Uber rides are measured against.

**Design defaults used here:** Valhalla Meili for map-matching, quintic polynomial for jerk-minimizing segments.

---

#### FR-9.1 — Map-matcher client

- **Description:** Python client to a local Valhalla Meili service that converts a sequence of noisy fused positions into a sequence of snapped-to-road positions plus the matched OSM way IDs.
- **Inputs:** Fused odometry (FR-4.2 / FR-5.2 output).
- **Outputs:** `{trip_id}/route_matched.parquet`: per-100 Hz tick, `osm_way_id`, `snapped_px`, `snapped_py`, `distance_from_road_m`.
- **Acceptance Criteria:**
  - [ ] ≥ 98 % of ticks snap within 10 m of a road.
  - [ ] Wade Ave is correctly identified (by OSM way ID) in day2.
  - [ ] Handles Valhalla-unreachable points gracefully (emits NaN, not crash).
- **Priority:** MUST
- **Dependencies:** FR-4.2, FR-7.3

---

#### FR-9.2 — Speed-limit lookup

- **Description:** Given OSM way IDs, return posted speed limits. Uses OSM `maxspeed` tags where available, falls back to hand-coded limits in `config/speed_limits.yaml` for the three known corridors (Saint Mary's, Wade Ave, I-440).
- **Inputs:** OSM way IDs.
- **Outputs:** Per-way speed limit (m/s).
- **Acceptance Criteria:**
  - [ ] 100 % of ways touched by day2 resolve to a limit (tagged or hand-coded).
  - [ ] Unrecognized way → warning + fallback to urban default (13.4 m/s ≈ 30 mph).
- **Priority:** MUST
- **Dependencies:** FR-9.1

---

#### FR-9.3 — Reference path extraction

- **Description:** From the matched route, extract the centerline geometry as a densely-sampled polyline in local ENU. Compute signed curvature κ(s) along the path.
- **Inputs:** Map-matched route from FR-9.1.
- **Outputs:** `reference_path.parquet`: `s_m`, `px_m`, `py_m`, `heading_rad`, `curvature_1pm`, `speed_limit_mps`.
- **Acceptance Criteria:**
  - [ ] Path is sampled every 1 m along arc length.
  - [ ] Curvature is finite everywhere; infinite-curvature OSM nodes (kinks) are smoothed with a 5 m rolling spline.
- **Priority:** MUST
- **Dependencies:** FR-9.1, FR-9.2

---

#### FR-9.4 — Ideal speed profile

- **Description:** For each point on the reference path, compute the target speed as `v*(s) = min(speed_limit(s), sqrt(a_lat_max / |κ(s)|))` where `a_lat_max = 2.0 m/s²` (from `config/ideal.yaml`). Then smooth the profile so it respects `|a_lon| ≤ 1.5 m/s²` (accel), `|a_lon| ≤ 2.5 m/s²` (decel), `|j| ≤ 2.0 m/s³`.
- **Inputs:** Reference path (FR-9.3).
- **Outputs:** `ideal_speed.parquet`: `s_m`, `v_ideal_mps`, `a_ideal_mps2`, `j_ideal_mps3`.
- **Acceptance Criteria:**
  - [ ] Resulting profile satisfies all four limits pointwise.
  - [ ] Through Wade Ave's sharpest turn, `v_ideal` drops below posted limit (the curvature-limited regime is real).
  - [ ] Unit-tested against a synthetic "corner" path with a known analytical ideal.
- **Priority:** MUST
- **Dependencies:** FR-9.3

---

#### FR-9.5 — Quintic-polynomial trajectory synthesis

- **Description:** Segment the path at waypoints (curvature extrema), fit a jerk-minimizing quintic polynomial in each segment with boundary conditions from FR-9.4. Stitch into a full trajectory `(t, px, py, v, a, ψ)`.
- **Inputs:** Reference path + ideal speed profile.
- **Outputs:** `{trip_id}/ideal_trajectory.parquet`.
- **Acceptance Criteria:**
  - [ ] Trajectory is C² continuous at segment boundaries (verified numerically).
  - [ ] Total trip time is within 15 % of the fastest legal time (a sanity check that we aren't crawling).
  - [ ] Position stays within 0.5 m of the reference centerline at all times.
- **Priority:** MUST
- **Dependencies:** FR-9.4

---

#### FR-9.6 — Ideal trajectory visual QA

- **Description:** Render the ideal trajectory on a map overlay alongside the actual fused trajectory, as a sanity-check PNG emitted per trip.
- **Inputs:** Fused + ideal trajectories.
- **Outputs:** `{trip_id}/qa_ideal_vs_actual.png`.
- **Acceptance Criteria:**
  - [ ] Visible difference between smooth ideal and jittery actual on day2.
  - [ ] Generated by a single `make ideal TRACE=day2` target.
- **Priority:** SHOULD
- **Dependencies:** FR-9.5

---

### FR-10 — Scoring & Tip Lookup

**Purpose:** Quantify how far the actual ride deviated from the ideal, aggregate into a single score, and map to a suggested tip band.

**Design defaults used here:** PRD §5.5 weights and tip bands as initial values; stored in `config/scoring.yaml` and tuneable without code changes.

---

#### FR-10.1 — Jerk penalty

- **Description:** Integrate `|j_actual| − |j_ideal|` clipped at zero over the trip, normalized by trip duration.
- **Inputs:** Fused odom (FR-4.2), ideal trajectory (FR-9.5).
- **Outputs:** Scalar `jerk_penalty` ∈ [0, 1] (normalized so that a typical "calm drive" scores < 0.2, a typical "harsh drive" scores > 0.5).
- **Acceptance Criteria:**
  - [ ] Monotonic in harshness: doubling all jerk events doubles the penalty (up to saturation).
  - [ ] Unit-tested with synthetic "calm" and "harsh" trajectories.
- **Priority:** MUST
- **Dependencies:** FR-4.2, FR-9.5

---

#### FR-10.2 — Harsh braking penalty

- **Description:** Count events where deceleration exceeds 3.5 m/s² for ≥ 0.3 s. Normalize by trip duration (events per minute).
- **Inputs:** Fused accel.
- **Outputs:** Scalar `harsh_brake_penalty` ∈ [0, 1].
- **Acceptance Criteria:**
  - [ ] Zero events on my own day2 drive (assumed a calm drive).
  - [ ] Event detector doesn't double-count (hysteresis / debounce).
- **Priority:** MUST
- **Dependencies:** FR-4.2

---

#### FR-10.3 — Lateral acceleration penalty

- **Description:** Integrate `max(0, |a_lat_actual| − a_lat_ideal)²`, normalized.
- **Inputs:** Fused lateral accel, ideal lateral limit.
- **Outputs:** Scalar `lat_accel_penalty` ∈ [0, 1].
- **Acceptance Criteria:**
  - [ ] Respects the calibration: body-frame lateral accel from FR-1.3/gravity decomposition, not raw phone x-axis.
- **Priority:** MUST
- **Dependencies:** FR-4.2, FR-9.4

---

#### FR-10.4 — Speed compliance penalty

- **Description:** Time-weighted integral of `max(0, v_actual − speed_limit)`, normalized.
- **Inputs:** Fused speed, speed limits per way (FR-9.2).
- **Outputs:** Scalar `speed_penalty` ∈ [0, 1].
- **Acceptance Criteria:**
  - [ ] 5 mph over for 1 minute scores higher than 1 mph over for 5 minutes (quadratic-ish in excess).
  - [ ] Tolerance band of ±2 mph around posted limit is free (GPS speed noise floor).
- **Priority:** MUST
- **Dependencies:** FR-4.2, FR-9.2

---

#### FR-10.5 — Route deviation penalty

- **Description:** Integrate lateral distance from the reference centerline.
- **Inputs:** Fused position, reference path (FR-9.3).
- **Outputs:** Scalar `deviation_penalty` ∈ [0, 1].
- **Acceptance Criteria:**
  - [ ] Within-lane driving (< 1.5 m off centerline) scores ~ 0.
  - [ ] A 3 m drift (half a lane width) is a noticeable penalty.
- **Priority:** MUST
- **Dependencies:** FR-4.2, FR-9.3

---

#### FR-10.6 — Lane change penalty

- **Description:** Count abrupt yaw excursions (Δψ > 0.15 rad over < 2 s) that correlate with sustained lateral displacement (indicating a lane change as opposed to a swerve).
- **Inputs:** Fused ψ and lateral position.
- **Outputs:** Scalar `lane_change_penalty` ∈ [0, 1].
- **Acceptance Criteria:**
  - [ ] Highway lane changes are detected; small in-lane wiggles are not.
- **Priority:** SHOULD
- **Dependencies:** FR-4.2
- **Note:** Thresholds will likely need calibration after first Uber rides.

---

#### FR-10.7 — Aggregate score

- **Description:** Weighted sum of FR-10.1 through FR-10.6, scaled to `[0, 100]`. Weights from `config/scoring.yaml` (PRD §5.5 defaults).
- **Inputs:** Six component penalties.
- **Outputs:** `{trip_id}/score.json`: aggregate + per-component breakdown.
- **Acceptance Criteria:**
  - [ ] Score of 100 = all penalties 0; score of 0 = all penalties saturated.
  - [ ] `score.json` includes the config hash used, so post-hoc reproducibility is exact.
- **Priority:** MUST
- **Dependencies:** FR-10.1–FR-10.6

---

#### FR-10.8 — Tip band lookup

- **Description:** Map aggregate score to a suggested tip rate via a lookup table (PRD §5.5 defaults: 90+ → 25 %, 75–89 → 20 %, 60–74 → 15 %, < 60 → 10 %). The table is data, not code.
- **Inputs:** Aggregate score from FR-10.7.
- **Outputs:** `suggested_tip_pct` + the matched band label.
- **Acceptance Criteria:**
  - [ ] Defined in `config/scoring.yaml`, not hard-coded.
  - [ ] Output clearly labeled "SUGGESTED — final decision is manual" in the report (enforced by FR-11.2 template).
- **Priority:** MUST
- **Dependencies:** FR-10.7

---

### FR-11 — Reporting

**Purpose:** Present one trip's result as a single-page static HTML report that a human can read in 30 seconds to decide on a tip.

**Design defaults used here:** Jinja2 templates + Folium maps, static output. No runtime server required.

---

#### FR-11.1 — Per-trip HTML report

- **Description:** Render a Jinja2 template with score, per-component bar chart, map overlay, and trip metadata into a single self-contained HTML file.
- **Inputs:** `score.json`, `ideal_trajectory.parquet`, fused odom.
- **Outputs:** `{trip_id}/report.html` + any assets (PNG/SVG) it references.
- **Acceptance Criteria:**
  - [ ] Opens correctly as a local file (no server, no CDN dependency).
  - [ ] Total size < 5 MB per report.
  - [ ] Page loads and renders map in < 2 s on a laptop.
- **Priority:** MUST
- **Dependencies:** FR-10.7

---

#### FR-11.2 — Map overlay (actual vs ideal)

- **Description:** Folium-generated Leaflet map with two polylines (actual = red, ideal = green) and markers for harsh-braking events.
- **Inputs:** Fused + ideal trajectories, event list.
- **Outputs:** Embedded in FR-11.1's HTML.
- **Acceptance Criteria:**
  - [ ] Divergence is visible at a glance.
  - [ ] Click on a harsh-brake marker shows timestamp + decel magnitude.
- **Priority:** MUST
- **Dependencies:** FR-11.1

---

#### FR-11.3 — Per-component breakdown chart

- **Description:** A small SVG or PNG bar chart showing each of the six penalty components, with the aggregate score called out.
- **Inputs:** `score.json`.
- **Outputs:** Embedded in FR-11.1's HTML.
- **Acceptance Criteria:**
  - [ ] Components are labeled with human-readable names, not FR IDs.
  - [ ] Visually obvious which component dominates the score.
- **Priority:** MUST
- **Dependencies:** FR-10.7

---

#### FR-11.4 — Index / trip-list page

- **Description:** A top-level `index.html` listing all scored trips with date, duration, aggregate score, and a link to each per-trip report. Auto-regenerated on every `make report`.
- **Inputs:** Glob of `{trip_id}/score.json`.
- **Outputs:** `out/reports/index.html`.
- **Acceptance Criteria:**
  - [ ] Sortable (client-side, no JS framework — vanilla).
  - [ ] Shows running Spearman ρ against subjective ratings once FR-11.5 data is present.
- **Priority:** SHOULD
- **Dependencies:** FR-11.1

---

#### FR-11.5 — Subjective-rating ingest

- **Description:** Read a simple `ratings.yaml` file where the rider logs post-trip 1–5 subjective scores, and merge into the index report.
- **Inputs:** `config/ratings.yaml`.
- **Outputs:** Extra column in FR-11.4 + Spearman ρ computed across trips.
- **Acceptance Criteria:**
  - [ ] Missing rating for a trip is handled gracefully (column shows "—").
  - [ ] Spearman ρ requires ≥ 5 rated trips, otherwise shows "n/a".
- **Priority:** SHOULD
- **Dependencies:** FR-11.4
- **Note:** This is the implementation hook for PRD S4 (score validity).

---

### FR-12 — AWS Deployment

**Purpose:** Same pipeline runs in the cloud, reproducibly, via Terraform. Local dev mirror is the contract — if `make eval` works locally, the deployed version produces the same `score.json`.

**Design defaults used here:** ECS Fargate for Python jobs, EKS (EC2) for ROS 2 nodes, Step Functions orchestration. Cost ceiling $50/month.

---

#### FR-12.1 — S3 bucket + prefix layout

- **Description:** One Terraform-managed S3 bucket with the prefix tree from PRD §4.1 (`/raw`, `/processed`, `/synthetic`, `/fused`, `/ideal`, `/scores`, `/reports`). Versioning enabled, lifecycle rule archiving `/synthetic` to Glacier after 30 days.
- **Inputs:** —
- **Outputs:** Terraform module `infra/s3/`.
- **Acceptance Criteria:**
  - [ ] `terraform plan` produces no drift on successive applies.
  - [ ] Bucket blocks all public access (default).
  - [ ] Bucket name is parameterized (suffix from `terraform.tfvars`) — no hard-coded globally unique name.
- **Priority:** MUST
- **Dependencies:** —

---

#### FR-12.2 — ECR repositories

- **Description:** Two ECR repos: `rct/python-worker`, `rct/ros2-worker`. Image scanning on push. Lifecycle policy keeping the last 10 tags.
- **Inputs:** —
- **Outputs:** Terraform module `infra/ecr/`.
- **Acceptance Criteria:**
  - [ ] `docker push` from GHA (via OIDC) succeeds.
  - [ ] Vulnerability scan runs automatically and is visible in the AWS Console (even though console is not used for operations — scanner is acceptable observability).
- **Priority:** MUST
- **Dependencies:** FR-7.2

---

#### FR-12.3 — EKS cluster for ROS 2 jobs

- **Description:** Minimal EKS cluster (1–3 EC2 worker nodes, `t3.medium`), configured for DDS multicast via security-group rules. Cluster name + kubeconfig output for `make deploy` to consume.
- **Inputs:** —
- **Outputs:** Terraform module `infra/eks/`.
- **Acceptance Criteria:**
  - [ ] `eksctl`- or `terraform`-created — fully IaC, no console steps.
  - [ ] `kubectl apply -f src/localization/k8s/` deploys ekf_node and ukf_node successfully.
  - [ ] Cluster autoscaler configured, min=0 so idle clusters cost near zero.
- **Priority:** MUST
- **Dependencies:** FR-4.2, FR-5.2, FR-12.2

---

#### FR-12.4 — Step Functions orchestration

- **Description:** State machine for the full pipeline: S3 trigger → Fargate (ingest) → Fargate (noise fit) → EKS job (fuse) → Fargate (ideal + score + report) → S3 write. Retries on transient failure, DLQ on terminal.
- **Inputs:** —
- **Outputs:** Terraform module `infra/stepfn/`.
- **Acceptance Criteria:**
  - [ ] State machine visualization (in AWS console, post-hoc only) is readable — states named after FR IDs where applicable.
  - [ ] End-to-end run on a newly uploaded `day2` raw directory produces `score.json` in `/scores/` in < 15 min.
- **Priority:** MUST
- **Dependencies:** FR-12.1, FR-12.2, FR-12.3

---

#### FR-12.5 — GitHub Actions CI/CD

- **Description:** Two workflows: `ci.yaml` (lint + test on every PR), `deploy.yaml` (on merge to main: build Python + ROS 2 images, push to ECR, deploy to EKS, run a smoke eval).
- **Inputs:** Git events.
- **Outputs:** CI status + deployed images.
- **Acceptance Criteria:**
  - [ ] Uses OIDC to assume an AWS role — no long-lived keys in GitHub secrets.
  - [ ] `ci.yaml` runs in < 10 min.
  - [ ] `deploy.yaml` fails loudly if the headless smoke eval's RMSE regresses > 10 % vs. the baseline in `tests/fixtures/baseline_rmse.json`.
- **Priority:** MUST
- **Dependencies:** FR-8, FR-12.2, FR-12.3

---

#### FR-12.6 — Cost observability + ceiling

- **Description:** AWS Budget with a $50/month hard alert and a $10/eval-run soft alert. Daily cost exported to CloudWatch Dashboard defined in Terraform. Optional: nightly scheduled Lambda that tears down the EKS node group if `kubectl get pods` is empty for 24 h.
- **Inputs:** —
- **Outputs:** Terraform module `infra/observability/`.
- **Acceptance Criteria:**
  - [ ] Budget alert has email destination.
  - [ ] Per-service breakdown visible in the dashboard.
  - [ ] Auto-teardown Lambda is enabled by default.
- **Priority:** SHOULD
- **Dependencies:** FR-12.3

---

#### FR-12.7 — IAM (least privilege)

- **Description:** IAM roles for GHA (OIDC), Step Functions execution, Fargate task role, EKS node role. No `*`-wildcard policies on production paths.
- **Inputs:** —
- **Outputs:** Terraform module `infra/iam/`.
- **Acceptance Criteria:**
  - [ ] `iamlive` or equivalent dry-run shows no unused permissions.
  - [ ] GHA role can only push to the two declared ECR repos and invoke the `deploy` state machine.
- **Priority:** MUST
- **Dependencies:** —

---

## 4. Priority rollup

| Priority | Count | IDs |
|---|---|---|
| MUST | 43 | FR-1.1–1.5, FR-2.1–2.3, FR-3.1, FR-4.1–4.4, FR-5.1–5.2, FR-6.1–6.2, FR-7.1–7.3, FR-8.1–8.3, FR-9.1–9.5, FR-10.1–10.5, FR-10.7, FR-10.8, FR-11.1–11.3, FR-12.1–12.5, FR-12.7 |
| SHOULD | 11 | FR-2.4, FR-3.2, FR-4.5, FR-6.3, FR-6.4, FR-7.4, FR-8.4, FR-9.6, FR-10.6, FR-11.4, FR-11.5, FR-12.6 |
| COULD | 0 | — |

**S1 path (minimum to demonstrate filter merit):** FR-1.1→1.5, FR-3.1, FR-4.1–4.4, FR-6.1, FR-6.2. 11 features, all MUST.

**S4 path (minimum to demonstrate score validity):** Add FR-9.1–9.5, FR-10.1–10.5, FR-10.7, FR-10.8, FR-11.1–11.3, FR-11.5. +15 features.

**Full PRD scope:** all 54 MUST+SHOULD above.

---

## 5. Out of scope for v1 (explicit)

- Real-time onboard execution (PRD §1.4)
- Perception stack (cameras, LiDAR)
- Multi-vehicle / multi-agent simulation
- Learning a planner from data (vs. the rule-based ideal driver in FR-9)
- Auto-tipping integration with rideshare apps (PRD §1.4)
- Mobile UI
- Multi-user / account system
- Any telemetry upload to external services

---

## 6. Open items for FRD v1.1

These are known to need revision, but after first-pass implementation exposes reality:

1. **FR-10 weights** — will need a calibration pass after the first 8 Uber rides (PRD S4, FR-11.5 feedback loop).
2. **FR-10.6 lane-change thresholds** — depend on observed distribution of yaw rates in real rideshare data.
3. **FR-9.2 speed-limit coverage** — OSM completeness unknown until FR-9.1 is run on diverse routes.
4. **FR-12.3 EKS instance sizing** — `t3.medium` is a guess; may need GPU-less `c6i` class if CTRV+UKF at 100 Hz × 4 parallel trips saturates CPU.
5. **FR-6.1 RTS smoother fidelity** — if the "soft GT" is visibly worse than the on-line filter, we need a better reference (e.g. temporarily borrow a better phone with dual-frequency GPS).

---

## 7. Review checklist (for the FRD reviewer — me, in two days)

- [ ] Does every MUST trace back to a PRD success criterion?
- [ ] Is any FR small enough to implement but too small to be worth naming? (Fold it up.)
- [ ] Is any FR so large it should be broken further? (Typical smell: more than 6 acceptance criteria, or description needs more than 3 sentences.)
- [ ] Are any dependencies circular?
- [ ] Did we accidentally recreate a feature under two IDs?
- [ ] Are all "Design Defaults" in §1 still defensible after seeing the full decomposition?
