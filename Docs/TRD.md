# TRD — Raleigh Commute Digital Twin: *Uber vs. My AI*

**Document version:** 1.0
**Status:** Draft for review
**Owner:** Takumi
**Last updated:** 2026-04-19
**Companion to:** `PRD.md` v2.0, `FRD.md` v1.0
**Scope:** Phase 1 (local-only). Phase 2 (AWS, FR-12) is deferred to a separate TRD addendum after local validation completes.

---

## 0. How to read this document

The PRD says *why* and the FRD says *what*. This document says *how*: the concrete data schemas, interfaces, quality bars, tooling versions, and test strategy that implementation must conform to.

Every statement here is a contract. Implementation code can change; these specs are the stable reference. Revisions are tracked by bumping the version above and noting the diff at the end of the document.

### 0.1 Phase boundary

| Phase | Scope | What this TRD covers |
|---|---|---|
| **Phase 1** | Local dev, Docker Compose, pytest/gtest, MinIO-backed S3-parity (optional) | **All sections 1–8 in this document** |
| **Phase 2** | AWS deployment (FR-12) | Addendum (separate doc), written after Phase 1 validation is green |

FR IDs referenced: FR-1 through FR-11. FR-12 appears only as a forward-looking placeholder (§9).

---

## 1. Data schemas

All inter-stage data uses **Parquet** (tabular) or **MCAP** (ROS 2 replay). No CSV past the ingestion boundary. Every Parquet file has a corresponding pydantic schema in `src/data_engine/schemas.py` — this file is the single source of truth, and all readers/writers round-trip through it.

### 1.1 Units and coordinate conventions (global)

| Quantity | Unit | Notes |
|---|---|---|
| Position | m (meters) | In local ENU unless suffixed `_wgs84` |
| Velocity | m/s | Scalar speed `v`; body-frame vx/vy avoided |
| Acceleration | m/s² | Body frame unless suffixed `_enu` |
| Jerk | m/s³ | Always body frame, longitudinal |
| Angles | rad | Normalized to `[-π, π]` for heading, `[-π/2, π/2]` for pitch |
| Angular rate | rad/s | |
| Time (relative) | s | `seconds_elapsed` — the canonical timeline for fusion |
| Time (absolute) | ns | `time_ns`, int64 epoch ns, preserved for audit only |
| Curvature | 1/m | Signed (left turn positive in ENU) |

**Local ENU anchor:** `lat0 = 35.773°N, lon0 = -78.610°W`, defined in `config/data_gen.yaml`. Flat-earth projection (valid for < 10 km span).

**Body frame convention:** +x forward (vehicle travel direction), +y left, +z up. Derived from gravity decomposition + GPS course-over-ground yaw alignment (FR-1.2 / FR-4.1).

### 1.2 `aligned_100hz.parquet` — FR-1.4 output

Primary tabular format. One row per 10 ms tick.

```python
class Aligned100Hz(BaseModel):
    # Time
    t_s: float              # seconds_elapsed, 0.00, 0.01, 0.02, ...
    time_ns: int            # epoch ns, preserved for audit

    # Position (local ENU)
    px_m: float             # East of anchor
    py_m: float             # North of anchor

    # Raw GPS (for readers that need WGS-84)
    lat_wgs84: float
    lon_wgs84: float

    # GPS quality (from Sensor Logger)
    horizontal_accuracy_m: float
    speed_accuracy_mps: float
    bearing_accuracy_deg: float
    gps_speed_mps: float
    gps_bearing_deg: float   # 0 .. 360, CW from north

    # IMU (body frame)
    ax_mps2: float
    ay_mps2: float
    az_mps2: float
    gx_rps: float
    gy_rps: float
    gz_rps: float

    # Gravity (body frame) — used for gravity removal
    grav_x: float
    grav_y: float
    grav_z: float

    # Orientation (as provided by phone, sanity only)
    quat_w: float
    quat_x: float
    quat_y: float
    quat_z: float

    # Magnetometer (body frame)
    mag_x_uT: float
    mag_y_uT: float
    mag_z_uT: float

    # Provenance
    gps_interpolated: bool   # True iff GPS was interpolated (not a real fix on this tick)
```

Constraints:
- No NaNs except the first 0.5 s (warm-up drop).
- `t_s` monotonic increasing in 0.01 s steps.
- `gps_interpolated == False` at most ~1 % of rows (approx. 1 Hz real GPS in 100 Hz grid).

### 1.3 `ground_truth.parquet` — FR-6.1 output

RTS-smoothed reference trajectory.

```python
class GroundTruth(BaseModel):
    t_s: float
    px_m: float
    py_m: float
    v_mps: float
    psi_rad: float          # [-π, π]
    psi_dot_rps: float
```

### 1.4 `route_matched.parquet` — FR-9.1 output

Per-tick map-matching result.

```python
class RouteMatched(BaseModel):
    t_s: float
    osm_way_id: Optional[int]        # None if unmatchable
    snapped_px_m: Optional[float]
    snapped_py_m: Optional[float]
    distance_from_road_m: Optional[float]
    match_confidence: float          # 0..1, from Valhalla Meili
```

### 1.5 `reference_path.parquet` — FR-9.3 output

Densely-sampled road centerline for the matched route. One row per 1 m of arc length.

```python
class ReferencePath(BaseModel):
    s_m: float              # arc length from start, 0, 1, 2, ...
    px_m: float
    py_m: float
    heading_rad: float
    curvature_1pm: float    # signed
    speed_limit_mps: float
    osm_way_id: int
```

### 1.6 `ideal_speed.parquet` — FR-9.4 output

Comfort- and limit-constrained speed profile along the reference path.

```python
class IdealSpeed(BaseModel):
    s_m: float
    v_ideal_mps: float
    a_ideal_mps2: float
    j_ideal_mps3: float
```

### 1.7 `ideal_trajectory.parquet` — FR-9.5 output

Quintic-polynomial-synthesized trajectory in the time domain.

```python
class IdealTrajectory(BaseModel):
    t_s: float
    px_m: float
    py_m: float
    v_mps: float
    a_lon_mps2: float       # body-frame longitudinal
    a_lat_mps2: float       # body-frame lateral, signed
    j_lon_mps3: float
    psi_rad: float
    psi_dot_rps: float
```

### 1.8 `score.json` — FR-10.7 output

Per-trip score record.

```json
{
  "trip_id": "day2",
  "config_hash": "sha256:...",
  "fused_source": "ekf" | "ukf",
  "components": {
    "jerk":          {"raw": 0.234, "weight": 0.30, "weighted": 0.0702},
    "harsh_brake":   {"raw": 0.0,   "weight": 0.20, "weighted": 0.0},
    "lat_accel":     {"raw": 0.112, "weight": 0.15, "weighted": 0.0168},
    "speed":         {"raw": 0.050, "weight": 0.20, "weighted": 0.0100},
    "deviation":     {"raw": 0.089, "weight": 0.10, "weighted": 0.0089},
    "lane_change":   {"raw": 0.000, "weight": 0.05, "weighted": 0.0000}
  },
  "aggregate_raw": 0.1059,
  "score_0_100": 89.41,
  "suggested_tip_band": "75-89",
  "suggested_tip_pct": 20,
  "timestamp_utc": "2026-04-19T18:34:05Z",
  "notes": "SUGGESTED — final tipping decision is manual."
}
```

### 1.9 `ks_report.json` — FR-2.3 output

```json
{
  "channels": {
    "ax_mps2":               {"p_value": 0.234, "pass": true},
    "gz_rps":                {"p_value": 0.412, "pass": true},
    "horizontal_accuracy_m": {"p_value": 0.017, "pass": false},
    "...": "..."
  },
  "overall_pass_rate": 0.85,
  "gate_threshold": 0.80,
  "gate_passed": true
}
```

### 1.10 `rmse_report.json` — FR-6.2 output

```json
{
  "trip_id": "day2",
  "filter": "ekf",
  "overall_rmse_m": 2.18,
  "gps_only_rmse_m": 3.47,
  "improvement_pct": 37.2,
  "per_minute_rmse_m": [2.1, 2.3, 1.9, ...],
  "s1_pass": true,
  "nees_mean": 4.8,
  "nees_ci_95": [3.0, 7.0],
  "nees_consistent": true,
  "rejection_count": 3,
  "rejection_rate": 0.0032
}
```

### 1.11 Parquet file-level conventions

- Compression: **Snappy**.
- Row group size: 100,000 (≈ 15 minutes at 100 Hz).
- Parquet metadata keys: `trip_id`, `git_sha`, `schema_version`, `generated_at_utc`, and for synthetic files, `base_trip_id` + `seed`.

### 1.12 `scenario_manifest.json` — FR-2.2 / FR-2.4 output

```json
{
  "manifest_version": 1,
  "base_trip_id": "day2",
  "scenarios": [
    {
      "scenario_id": "s0001",
      "seed": 42,
      "stress_events": [
        {"type": "gps_dropout",    "start_s": 120.0, "end_s": 135.0},
        {"type": "imu_bias_step",  "axis": "x", "delta": 0.05, "at_s": 400.0}
      ],
      "parquet_path": "synthetic/s0001/aligned_100hz.parquet",
      "generated_at_utc": "..."
    }
  ]
}
```

---

## 2. ROS 2 interfaces

### 2.1 Topics (input side, published by FR-3.1 bag)

| Topic | Type | Rate | Notes |
|---|---|---|---|
| `/gps/fix` | `sensor_msgs/msg/NavSatFix` | ~1 Hz (real) + interpolated fill | `position_covariance_type = COVARIANCE_TYPE_DIAGONAL_KNOWN`, covariance diagonal = `[σh², σh², σv²]` |
| `/imu/data` | `sensor_msgs/msg/Imu` | 100 Hz | `linear_acceleration_covariance[0] = -1` (not provided); `angular_velocity_covariance` filled from fitted Gaussian (FR-2.1) |
| `/mag` | `sensor_msgs/msg/MagneticField` | 50 Hz | Body-frame µT converted to Tesla (×1e-6) per ROS convention |

`frame_id` for all three: `"base_link"`. Fusion output is in `"odom"`.

### 2.2 Topics (output side, published by FR-4.2 / FR-5.2)

| Topic | Type | Rate | Notes |
|---|---|---|---|
| `/fused/odom` | `nav_msgs/msg/Odometry` | 100 Hz | When only one filter runs |
| `/fused/odom_ekf` | `nav_msgs/msg/Odometry` | 100 Hz | When `--compare` flag is set |
| `/fused/odom_ukf` | `nav_msgs/msg/Odometry` | 100 Hz | When `--compare` flag is set |
| `/fused/diagnostics` | `diagnostic_msgs/msg/DiagnosticArray` | 1 Hz | FR-4.5; fields: `rejection_count`, `nees_mean`, `Q_trace`, `R_pos_trace`, `health` ∈ {`OK`, `DEGRADED`, `DIVERGED`} |

### 2.3 Odometry message population

- `pose.pose.position.{x,y}` = local ENU in meters. `z` = 0.
- `pose.pose.orientation` = quaternion from fused heading ψ only (roll/pitch stay zero; we don't estimate them).
- `pose.covariance` (6×6 row-major): filled for `[x, y, yaw]` dimensions, others default to 1e-9.
- `twist.twist.linear.x` = fused speed v.
- `twist.twist.angular.z` = fused ψ̇.
- `header.stamp`: microsecond-accurate timestamp from the MCAP message that triggered the update.

### 2.4 QoS profile

| Usage | Profile |
|---|---|
| All sensor inputs during bag playback | `SENSOR_DATA` (best-effort, keep-last 10) |
| `/fused/odom` | Reliable, keep-last 100 (recorders must not drop) |
| `/fused/diagnostics` | Reliable, keep-last 10 |

### 2.5 Node parameters (declared, not hard-coded)

Every node declares its parameters via `declare_parameter`. Launch files override from YAML. No `getenv`, no compiled-in constants for anything a human might want to tune.

`ekf_node` parameters (from `config/ekf.yaml`):

```yaml
process_noise:
  sigma_a: 1.0        # m/s^2, longitudinal accel noise std
  sigma_psi_dot: 0.1  # rad/s, yaw rate noise std
measurement_noise:
  bearing_min_speed_mps: 2.0   # below this, bearing is not used
  mag_only_fallback_speed: 1.0 # below this, use magnetometer for heading
outlier_gate:
  chi2_confidence: 0.99
initialization:
  method: "first_gps"   # {first_gps, config_provided}
  wait_gps_count: 3     # wait for N good fixes before first publish
```

### 2.6 ROS ↔ Parquet bridging

FR-3.1 converts Parquet → MCAP in one direction only. The fused-odom stream is re-captured from ROS 2 bag after the run and converted back to Parquet via `src/evaluation/odom_to_parquet.py` for FR-6.2 to consume. **C++ fusion code never reads Parquet directly**; it only consumes MCAP. Python eval code never reads MCAP directly; it only consumes Parquet. This keeps language boundaries clean.

---

## 3. Configuration files

All config under `config/`, version-controlled, human-editable YAML. Any parameter read by code must come from here, never from env vars or CLI flags (except for `--dry-run` style switches).

| File | Consumed by | Content |
|---|---|---|
| `data_gen.yaml` | FR-1, FR-2 | ENU anchor, sample-rate targets, noise distributions to fit, synth seeds, stress event catalog |
| `ekf.yaml` | FR-4 | Process/measurement noise, gate threshold, initialization |
| `ukf.yaml` | FR-5 | Sigma-point α/β/κ, shared sections with ekf.yaml via YAML anchors |
| `ideal.yaml` | FR-9 | Max accel/decel/lat-accel/jerk; map-matcher endpoint; path resampling step |
| `speed_limits.yaml` | FR-9.2 | Hand-coded speed limits per OSM way ID for known corridors; urban default fallback |
| `scoring.yaml` | FR-10 | Per-component weights; normalization constants; tip-band lookup table |
| `ratings.yaml` | FR-11.5 | `{trip_id: subjective_rating_1_to_5}` — user-maintained |
| `noise_fit.yaml` | FR-2.1 output, FR-4 input | Fitted distribution params; **generated, not hand-edited** |

### 3.1 YAML conventions

- Two-space indentation.
- Comments above fields, not inline.
- All physical constants have their unit in the key name (`sigma_a_mps2`, not `sigma_a`) — redundant with the value's implicit unit, but removes a class of bug.
- YAML anchors (`&name` / `*name`) allowed; document references.

### 3.2 Config hash

Any code emitting a result file (`score.json`, `rmse_report.json`) must include `config_hash: sha256(sorted_yaml_concatenation)` computed over the exact set of config files it read. This makes results reproducible even if `config/` changes between runs.

---

## 4. Non-functional requirements (NFR)

### 4.1 Performance targets

| Stage | Target | Measured on |
|---|---|---|
| FR-1 ingestion (15-min trip) | < 10 s | Laptop with NVMe SSD, single-threaded |
| FR-2.1 noise fit (2 trips) | < 30 s | Same |
| FR-2.4 100 synthetic scenarios | < 60 s | Multi-process pool, `min(n_cores, n_scenarios)` |
| FR-3.1 Parquet → MCAP | < 5 s per trip | — |
| FR-4.2 `ekf_node` real-time playback | ≥ 1.0x real-time | 15-min trip completes in < 15 min |
| FR-4.2 at 5× playback | ≥ 5.0x real-time | Stretch goal |
| FR-5.2 `ukf_node` | ≥ 0.5x real-time | UKF is allowed to be slower; still must be faster than "the user is waiting" |
| FR-6.2 RMSE eval | < 10 s | — |
| FR-9 ideal synthesis (one trip) | < 30 s | Incl. Valhalla round-trip |
| FR-10 scoring (one trip) | < 5 s | — |
| FR-11 HTML report generation | < 10 s | — |
| **End-to-end `make score TRACE=day2`** | **< 3 min** | From `make clean` |

If any of these is missed by > 2×, the stage is profiled before the next feature is added.

### 4.2 Determinism and reproducibility

- Every randomized step takes an explicit `seed` from config. No `time.time()` or `/dev/urandom` reads in production paths.
- Two runs with identical inputs + config + git SHA must produce byte-identical Parquet (excepting the `generated_at_utc` metadata field) and logically-identical JSON (same field values; whitespace normalized).
- CI has an explicit determinism test: run FR-1 through FR-10 twice on `day2` and diff every output.

### 4.3 Idempotency

- All `make` targets are re-runnable without manual cleanup. Running `make score TRACE=day2` after it has succeeded either short-circuits (file exists, inputs unchanged) or regenerates cleanly. No "half-written" file states.
- Detection: content-hash-based. A stamp file `out/{trip_id}/.stamps/{fr_id}.ok` contains the sha256 of the inputs; if the current inputs match, the stage is skipped. `make -B` forces regeneration.

### 4.4 Observability

Every stage logs to stdout in the following format (no exceptions):

```
[2026-04-19T18:34:05Z] [FR-4.2 ekf_node] INFO  Published 90000 odometry messages, 3 rejected, mean NEES 4.8
```

- Timestamps in ISO-8601 UTC.
- First tag: stage ID. Second tag: human name.
- Levels: `DEBUG`, `INFO`, `WARN`, `ERROR` — settable via `RCT_LOG_LEVEL` env var (one allowed exception to the "no env vars" rule in §3).
- Each stage emits a structured summary JSON at completion to `out/{trip_id}/.stamps/{fr_id}.summary.json` — used by the report page and by CI assertions.

### 4.5 Error handling

- Fail fast, fail loud. No silent fallbacks. A missing channel raises `MissingRequiredChannelError` (FR-1.1); a schema violation raises `SchemaValidationError` (FR-1.4). These are defined once in `src/data_engine/errors.py` and imported by all stages.
- Exit codes:
  - 0: success
  - 1: user error (bad args, missing file, config validation fail)
  - 2: data error (schema violation, NaN, empty input)
  - 3: upstream dependency error (Valhalla unreachable, Docker service down)
  - 4: gate failure (e.g. KS-test failed, RMSE regressed)
  - 64+: implementation bug (unreachable code, assertion failure)

### 4.6 Security & privacy

- No telemetry, no crash reporting, no analytics. Ever.
- No API keys in the repo. Not even for Valhalla (self-hosted, no auth needed for local).
- `config/ratings.yaml` is in `.gitignore` (personal ride log).
- `out/` is in `.gitignore`.
- Raw trace data lives under `data/` which is in `.gitignore`; the repo ships only tiny fixture slices under `tests/fixtures/` (pre-anonymized: fixture anchor is shifted from the real commute to a generic Raleigh location).

### 4.7 Code quality bars

- **Python**: type-hinted everywhere (`mypy --strict` clean). Ruff as linter + formatter (`ruff check` and `ruff format`). Docstrings on all public functions (Google style). No `print()` — use the logger.
- **C++**: C++17. `clang-format` with `.clang-format` (Google style base, 100-col). `clang-tidy` clean on the `readability-*`, `performance-*`, `bugprone-*` check groups. No raw `new`/`delete`; smart pointers only. No `using namespace` at file scope.
- **YAML**: `yamllint` clean; default rules relaxed to allow line length 120.
- **Dockerfile**: `hadolint` clean.
- **Markdown**: `markdownlint` clean; repo uses CommonMark.
- **Terraform** (Phase 2): `terraform fmt`, `tflint`, `tfsec`.

### 4.8 Dependency discipline

- `requirements.txt` pinned to exact versions (`==`, not `>=`).
- `package.xml` uses rosdep-managed versions.
- New dependency requires a one-line justification in the PR description. "I like X" is not a justification; "X replaces 80 lines of Y with 1" is.

---

## 5. Toolchain — pinned versions

Locked versions as of this TRD. Upgrades are revisions to this document.

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11.9 | 3.12 reserved; ROS 2 Jazzy bundles 3.12 but we isolate our Python code in its own container |
| ROS 2 | Jazzy Jalisco (LTS) | On Ubuntu 24.04 |
| Ubuntu base (ROS container) | 24.04 LTS | |
| Ubuntu base (Python container) | 24.04 LTS | |
| C++ compiler | gcc 13.2 | Matches Ubuntu 24.04 default |
| CMake | ≥ 3.22 | ROS 2 Jazzy minimum |
| Docker Engine | ≥ 25.0 | Compose V2 assumed (`docker compose`, not `docker-compose`) |
| Docker Compose | V2 plugin | |
| Valhalla (meili) | 3.5.x | Self-hosted `gisops/valhalla` image |
| MinIO (optional, S3-parity) | RELEASE.2025-*.* | Local dev only; optional in Phase 1 |
| pre-commit | ≥ 3.7 | |
| ruff | 0.5.x | |
| mypy | 1.10.x | |
| pytest | 8.x | |
| gtest | 1.14 | Vendored via CMake FetchContent |
| clang-format / clang-tidy | 17 | |
| hadolint | 2.12.x | |
| markdownlint-cli2 | 0.13.x | |
| MCAP tooling | `mcap` CLI ≥ 0.11 | For bag inspection |

### 5.1 Python libraries (`requirements.txt`, pinned)

```
# Core data
numpy==1.26.4
pandas==2.2.2
pyarrow==16.1.0
pydantic==2.7.4
pyyaml==6.0.1

# Scientific
scipy==1.13.1      # KS test, RTS smoother
filterpy==1.4.5    # reference Kalman implementation for unit-test comparison

# Geo
pyproj==3.6.1      # WGS-84 ↔ ENU round-trip verification
shapely==2.0.4
folium==0.16.0     # FR-11.2 map overlay

# Templating
jinja2==3.1.4

# HTTP (Valhalla client)
requests==2.32.3

# Dev/test
pytest==8.2.2
pytest-cov==5.0.0
ruff==0.5.0
mypy==1.10.0
pre-commit==3.7.1
```

### 5.2 C++ libraries (via `package.xml` + CMake FetchContent)

- Eigen 3.4 (vectors/matrices; CTRV math)
- ROS 2 standard messages: `sensor_msgs`, `nav_msgs`, `geometry_msgs`, `diagnostic_msgs`, `builtin_interfaces`
- `rclcpp`, `rclcpp_components`
- MCAP reader (`mcap_vendor` or direct vendoring)
- GTest 1.14 (via FetchContent; no system dependency)

---

## 6. Testing strategy

### 6.1 Test pyramid

```
                                     ┌─────────────────────────┐
                                     │  FR-8.3 Integration     │   Few, slow (≤ 3 min CI)
                                     │  (headless bag replay)  │
                                     └───────────┬─────────────┘
                                       ┌─────────┴─────────┐
                                       │ FR-8.2 gtest (C++)│   Medium (~10 s)
                                       └─────────┬─────────┘
                        ┌──────────────────────────┴──────────────────────────┐
                        │                 FR-8.1 pytest (Python)              │   Many, fast (< 30 s)
                        │  data_engine · ideal_driver · scoring · evaluation  │
                        └─────────────────────────────────────────────────────┘
```

### 6.2 Layer responsibilities

| Layer | Tests what | Does not test |
|---|---|---|
| Unit (pytest) | Pure functions, schema validation, scoring math, speed-profile solver, noise fitting | Anything that requires ROS 2 running |
| Unit (gtest) | `ctrv_model`, sigma-point generator, χ² gate, single EKF/UKF update step | The full ROS node; I/O |
| Integration (gtest or pytest + subprocess) | Full ROS node against a 60-s fixture MCAP, asserting RMSE < threshold | Map-matching against external network |
| Determinism | Two end-to-end runs byte-match (NFR 4.2) | — |

### 6.3 Fixtures

All fixtures committed under `tests/fixtures/` — small (total < 20 MB).

| Fixture | Source | Size | Purpose |
|---|---|---|---|
| `tiny_day2_60s/*.csv` | Day 2, minutes 3:00–4:00 (straight segment) | ~5 MB | FR-1, FR-2, FR-4 unit tests |
| `tiny_day2_60s.mcap` | Generated from above | ~3 MB | FR-4.2, FR-5.2 integration |
| `synthetic_sinusoid/*.csv` | Hand-constructed, known-answer | < 100 KB | FR-1.2 interpolation precision test |
| `synthetic_circle.mcap` | Hand-constructed, 10 s constant-turn-rate | ~500 KB | FR-4.1 / FR-5.1 motion model test |
| `outlier_day1_sample.csv` | Day 1, 10 s window around the 122 m outlier | < 500 KB | FR-4.4 outlier gate test |
| `baseline_rmse.json` | Generated once from known-good run | tiny | FR-12.5 regression gate reference |

**Fixture anonymization:** the commute anchor in the tiny fixtures is offset to a generic point in Raleigh that is **not** my actual address. Speed and heading data is preserved; only the absolute location is shifted.

### 6.4 Baseline management

`baseline_rmse.json` is updated via a deliberate commit labeled `baseline:` in the PR title. The CI check compares against the committed value with a 10 % regression tolerance (FR-12.5 / FR-8.3). A drifting baseline must be justified in the PR description — the reviewer's job is to ask "did we get better, or did we mis-tune?"

### 6.5 Coverage thresholds

- Python: ≥ 80 % line coverage for `src/data_engine/`, `src/ideal_driver/`, `src/scoring/`, `src/evaluation/`. Glue code (`src/*/__main__.py`, CLI entry points) is excluded.
- C++: ≥ 70 % line coverage for `src/localization/` (filter nodes are largely I/O; the math is in `ctrv_model.hpp`, which hits > 95 %).
- Coverage measured by `pytest-cov` and `gcovr` respectively.

### 6.6 CI matrix (local mirror — the same checks run via `make test`)

| Job | Runs | Wall-clock target |
|---|---|---|
| Lint (ruff, clang-format, yamllint, hadolint, markdownlint) | On every commit (pre-commit) | < 15 s |
| Python unit | On every commit | < 30 s |
| C++ unit | On every commit | < 10 s |
| Integration (headless bag) | On every PR | < 3 min |
| Determinism | Nightly + on release-candidate PRs | < 6 min |

---

## 7. Repository layout (exact)

Matches PRD §4.3 / FRD §0 repo sketch; this is the authoritative version.

```
.
├── Makefile
├── README.md
├── PRD.md
├── FRD.md
├── TRD.md                      ← this document
├── docker-compose.yml
├── .dockerignore
├── .gitignore
├── .pre-commit-config.yaml
├── .clang-format
├── .clang-tidy
├── .markdownlint.jsonc
├── pyproject.toml              # black/ruff/mypy/pytest config
├── requirements.txt
├── requirements-dev.txt
├── config/
│   ├── data_gen.yaml
│   ├── ekf.yaml
│   ├── ukf.yaml
│   ├── ideal.yaml
│   ├── speed_limits.yaml
│   ├── scoring.yaml
│   └── ratings.yaml            # gitignored; user-maintained
├── src/
│   ├── data_engine/
│   │   ├── __init__.py
│   │   ├── __main__.py         # `python -m data_engine ingest|synth|ks`
│   │   ├── schemas.py          # single source of truth for Parquet schemas
│   │   ├── errors.py           # shared exception types
│   │   ├── ingest.py           # FR-1
│   │   ├── noise_fit.py        # FR-2.1
│   │   ├── synth.py            # FR-2.2, FR-2.4
│   │   ├── ks_test.py          # FR-2.3
│   │   ├── projection.py       # FR-1.3 (WGS-84 ↔ ENU)
│   │   └── parquet_io.py       # FR-1.4 + read helpers
│   ├── localization/
│   │   ├── CMakeLists.txt
│   │   ├── package.xml
│   │   ├── include/
│   │   │   └── localization/
│   │   │       ├── ctrv_model.hpp       # FR-4.1
│   │   │       ├── sigma_points.hpp     # FR-5.1
│   │   │       └── chi2_gate.hpp        # FR-4.4
│   │   └── src/
│   │       ├── ekf_node.cpp             # FR-4.2
│   │       ├── ukf_node.cpp             # FR-5.2
│   │       └── bag_bridge.cpp           # MCAP consumption
│   ├── evaluation/
│   │   ├── __init__.py
│   │   ├── rts_smoother.py     # FR-6.1
│   │   ├── rmse.py             # FR-6.2
│   │   ├── nees.py             # FR-6.3
│   │   ├── comparator.py       # FR-6.4
│   │   └── odom_to_parquet.py  # bag → parquet bridge for eval
│   ├── ideal_driver/
│   │   ├── __init__.py
│   │   ├── valhalla_client.py  # FR-9.1
│   │   ├── speed_limits.py     # FR-9.2
│   │   ├── reference_path.py   # FR-9.3
│   │   ├── speed_profile.py    # FR-9.4
│   │   └── quintic.py          # FR-9.5
│   ├── scoring/
│   │   ├── __init__.py
│   │   ├── components.py       # FR-10.1 .. FR-10.6
│   │   ├── aggregate.py        # FR-10.7
│   │   └── tip_lookup.py       # FR-10.8
│   └── reporting/
│       ├── __init__.py
│       ├── render.py           # FR-11.1 Jinja renderer
│       ├── map_overlay.py      # FR-11.2
│       ├── bar_chart.py        # FR-11.3
│       ├── index.py            # FR-11.4
│       ├── ratings.py          # FR-11.5
│       └── templates/
│           ├── report.html.j2
│           └── index.html.j2
├── bag_bridge/                 # Python ↔ ROS 2 bag tooling (FR-3.1)
│   ├── parquet_to_mcap.py
│   └── mcap_to_parquet.py      # for recording fused odom after replay
├── infra/                      # Phase 2 — empty placeholder in Phase 1
│   └── README.md               # "Populated in Phase 2"
├── .github/
│   └── workflows/              # Phase 1: ci.yaml only. Phase 2 adds deploy.yaml
│       └── ci.yaml
├── scripts/
│   ├── bootstrap.sh            # FR-7.4
│   ├── run_local_eval.sh
│   └── make_fixtures.py        # regenerates tests/fixtures from data/
├── docker/
│   ├── python.Dockerfile       # FR-7.2
│   ├── ros2.Dockerfile         # FR-7.2
│   └── valhalla/
│       └── config.json         # Valhalla tile config (pre-generated for NC)
├── tests/
│   ├── fixtures/
│   │   ├── tiny_day2_60s/
│   │   ├── synthetic_sinusoid/
│   │   ├── synthetic_circle.mcap
│   │   ├── outlier_day1_sample.csv
│   │   └── baseline_rmse.json
│   ├── unit/
│   │   ├── test_schemas.py
│   │   ├── test_projection.py
│   │   ├── test_ingest.py
│   │   ├── test_noise_fit.py
│   │   ├── test_synth_determinism.py
│   │   ├── test_ks_test.py
│   │   ├── test_scoring_components.py
│   │   ├── test_aggregate.py
│   │   ├── test_tip_lookup.py
│   │   ├── test_quintic.py
│   │   └── test_speed_profile.py
│   └── integration/
│       ├── test_headless_ekf.py
│       ├── test_headless_ukf.py
│       ├── test_end_to_end_day2.py
│       └── test_determinism_pair.py
└── out/                        # gitignored; all generated outputs land here
    └── .gitkeep
```

### 7.1 Gitignore policy

```
/data/                         # raw trace data, never committed
/out/                          # all derived artifacts
/config/ratings.yaml           # personal
/config/noise_fit.yaml         # generated
*.mcap                         # except under tests/fixtures/
__pycache__/
*.egg-info/
.pytest_cache/
.coverage
.ruff_cache/
.mypy_cache/
build/
install/
log/                           # colcon build artifacts
```

---

## 8. Implementation conventions (cross-cutting)

### 8.1 Stage contract

Every stage (FR-X.Y that produces a file) follows the same contract, implemented via a small shared base in `src/common/stage.py`:

1. Parse and validate inputs (schema + existence).
2. Check `out/{trip_id}/.stamps/{fr_id}.ok` for input hash match. If match, skip and log "CACHED".
3. Execute.
4. Validate outputs (schema + file size sanity).
5. Write `.stamps/{fr_id}.ok` and `.stamps/{fr_id}.summary.json`.
6. Emit the standard completion log line.

C++ stages follow the same contract, expressed as a helper in `src/localization/include/localization/stage.hpp`.

### 8.2 Timestamp discipline

- Inside fusion: `seconds_elapsed` only, `double`.
- Inside ROS messages: `builtin_interfaces/Time`, full nanosecond.
- Inside Parquet: `t_s: float` (seconds_elapsed) AND `time_ns: int64` (absolute epoch ns). Never one without the other.
- The epoch-ns timestamp is treated as audit metadata: never used for math, never compared across devices.

### 8.3 Random seeding

- Every file that generates randomness has a `SEED = <int>` at its config level.
- Python: one `np.random.Generator` instance per stage, seeded at entry. Never `np.random.seed` (global). Never `random.random`.
- C++ unit tests: `std::mt19937(seed)` with seed in the test body.

### 8.4 "Single source of truth" boundaries

| Truth | Location | Consumers |
|---|---|---|
| Parquet schemas | `src/data_engine/schemas.py` | All Python readers/writers, Parquet metadata |
| ROS message schema | ROS 2 standard types | All nodes |
| Scoring weights | `config/scoring.yaml` | FR-10.7, FR-11 |
| Tip band lookup | `config/scoring.yaml` | FR-10.8, FR-11 |
| Speed limits | `config/speed_limits.yaml` + OSM `maxspeed` tags | FR-9.2 |
| ENU anchor | `config/data_gen.yaml` | FR-1.3 + anyone rendering maps |
| FR definitions | `FRD.md` | This TRD, Dev Plan, PR descriptions |

Duplication of any of the above in code is a review-blocking issue.

### 8.5 Logging conventions

- Python: `logging.getLogger(__name__)`. A shared helper in `src/common/logging.py` installs the formatter from NFR 4.4.
- C++: `RCLCPP_INFO`, `RCLCPP_WARN`, etc. ROS 2 formatter is configured to match the same output shape.

### 8.6 Docstring / comment discipline

- Public Python functions: docstring with 1-line summary + args + returns + raises. Examples where nontrivial.
- C++ headers: one-line Doxygen-style comments on public methods. `///` not `//` for doc comments.
- Inline comments explain **why**, not what. "Increment counter" is never a comment. "Hysteresis: a single dropped message shouldn't mark the filter unhealthy" is.

---

## 9. Phase 2 placeholder (FR-12)

Intentionally deferred. After Phase 1 passes PRD S1, S2, S3 (local-def. of S3 = "reproducible via `docker compose`"), and S4, a TRD v2.0 addendum will cover:

- S3 bucket structure, lifecycle, encryption
- ECR image build + push pipeline, retention
- EKS cluster sizing, DDS on Kubernetes (multicast / unicast discovery), node autoscaling config
- Step Functions state machine (detailed state-by-state spec)
- GitHub Actions OIDC role configuration
- IAM least-privilege matrix
- Cost observability, budgets, auto-teardown
- Differences between local Docker Compose behavior and cloud behavior (and how the test harness covers the gap)

Phase 2 will **not** introduce new FRs; it implements exactly what FRD §FR-12 already specifies, at full detail.

---

## 10. Review checklist (for the TRD reviewer — me, in two days)

- [ ] Does every Parquet schema in §1 match the FR that produces it?
- [ ] Does any config file in §3 lack a listed consumer in §8.4?
- [ ] Are NFR performance targets (§4.1) achievable on my actual laptop, or aspirational? Mark aspirational ones.
- [ ] Are the pinned versions in §5 the latest *stable* as of 2026-04-19, or outdated?
- [ ] Is every "hard" bar (schema, gate, threshold) traceable to a PRD success criterion?
- [ ] Any NFR that can't be tested? (If we can't test it, we can't claim it.)
- [ ] Anything in §8 that's never actually enforced by tooling? (If not enforced, it's folklore, not a contract.)

---

## 11. Revision log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-04-19 | Initial draft, Phase 1 scope only |
