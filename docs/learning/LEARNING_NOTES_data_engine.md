# Learning Notes: `src/data_engine/`

**Project:** Raleigh Commute Digital Twin — *Uber vs. My AI*
**Module:** `src/data_engine/` (Phase P1 — FR-1, FR-2)
**Purpose of this document:** Post-completion study notes written to close the implementation-level understanding gap that emerged from AI-assisted development (Claude Code). This is the first in a planned series covering each module in the project.

---

## Overview

`data_engine` is the first stage of the pipeline. It takes raw CSV files exported from the Sensor Logger iOS app and produces a clean, schema-validated Parquet file at a uniform 100 Hz sample rate. It also fits statistical noise models to the real sensor data and generates synthetic trip scenarios for downstream EKF testing.

### File map

| File | Role | Make target |
|---|---|---|
| `errors.py` | Shared exception classes and exit code taxonomy | — |
| `schemas.py` | Pydantic models for every Parquet schema in the project | — |
| `projection.py` | WGS-84 ↔ local ENU coordinate conversion | — |
| `ingest.py` | CSV parsing and 100 Hz time-grid alignment | `make data` |
| `parquet_io.py` | Schema-validated Parquet reader / writer | called by ingest |
| `noise_fit.py` | Per-channel noise distribution fitting | `make fit` |
| `synth.py` | Synthetic scenario generation | `make synth` |
| `ks_test.py` | Two-sample KS-test gate | `make ks` |
| `__main__.py` | CLI entry point (`python -m data_engine`) | — |

### Execution order

```
make data  → __main__.py → _cmd_ingest()
                           ├── ingest.py      parse_and_align()
                           │     ├── _read_channel()  × 7
                           │     ├── np.interp()      per column
                           │     └── projection.py    wgs84_to_enu()
                           └── parquet_io.py  write_parquet()

make fit   → noise_fit.py  fit_trip() → fit_channel() × 15

make synth → synth.py      generate_batch() → generate_scenario() × N
                                └── _inject_noise() → _apply_stress()

make ks    → ks_test.py    run_ks_test() → ks_2samp() × 12 channels
```

---

## `errors.py` — Exit codes and shared exceptions

### What I learned

All CLI stages in this project return a structured integer exit code defined in `StageExitCode(IntEnum)`. This is intentional: `make` reads the exit code and stops the pipeline if a stage fails. Without this convention, a failing stage would silently produce no output and the next stage would crash with a confusing error.

```python
class StageExitCode(IntEnum):
    SUCCESS          = 0
    USER_ERROR       = 1   # bad args, missing file, config error
    DATA_ERROR       = 2   # schema violation, NaN, empty input
    DEPENDENCY_ERROR = 3   # Valhalla unreachable, Docker down
    GATE_FAILURE     = 4   # KS-test failed, RMSE regressed
    IMPL_BUG         = 64  # unreachable code, assertion failure
```

`GATE_FAILURE = 4` is semantically distinct from other errors. It means "the code ran correctly but the output didn't meet the quality standard." This distinction matters when reading CI logs.

`MissingRequiredChannelError` carries the channel name as an attribute so tests can assert on exactly which file was missing, rather than just checking that some exception was raised.

---

## `schemas.py` — Single source of truth for all Parquet schemas

### What I learned

Every Parquet file written by this project has a corresponding pydantic `BaseModel` in this file. The key insight is that schemas live in one place and both the writer (`write_parquet`) and the reader (`read_parquet`) import from here. If a column is renamed anywhere, mypy will catch the mismatch at type-check time.

**`Aligned100Hz`** is the central data structure for the whole pipeline. Its 28 columns are organized into groups: time, ENU position, raw GPS, GPS quality metadata, IMU (body frame), gravity vector, orientation quaternion, magnetometer, and the `gps_interpolated` flag.

The `gps_interpolated: bool` field deserves special attention. It is `True` when no real GPS fix exists within ±50 ms of that 100 Hz tick. Since GPS updates at ~1 Hz, about 99% of rows are interpolated. The EKF node is designed to skip its measurement update step for those rows and predict using IMU alone.

Fields like `psi_rad` and `heading_rad` carry `@field_validator` decorators that enforce the `[-π, π]` range. Without normalization at schema level, angular errors accumulate silently in downstream matrix math.

---

## `projection.py` — WGS-84 to local ENU

### What I learned

The EKF state vector operates in Cartesian (metric) coordinates. GPS outputs latitude and longitude (degrees on a sphere). The conversion is handled by a simple flat-earth approximation anchored at a fixed point near the Raleigh trip corridor.

```python
_M_PER_DEG_LAT = 111_132.954          # constant: metres per degree latitude

def _m_per_deg_lon(lat0_deg):
    return _M_PER_DEG_LAT * cos(radians(lat0_deg))

def wgs84_to_enu(lat, lon, lat0_deg, lon0_deg):
    east_m  = (lon - lon0_deg) * _m_per_deg_lon(lat0_deg)
    north_m = (lat - lat0_deg) * _M_PER_DEG_LAT
    return east_m, north_m
```

**Why latitude scale is constant but longitude scale is not:** Lines of longitude converge toward the poles. At the equator, 1° of longitude ≈ 111 km. At Raleigh (35.8°N), `cos(35.8°) ≈ 0.811`, so 1° of longitude ≈ 90 km. Ignoring this would introduce a systematic east-west scaling error in the ENU coordinates.

**Why flat-earth is acceptable here:** The Raleigh commute corridor is ~15 km. Over that distance, the flat-earth error is less than 0.1 m, verified against `pyproj` in `tests/unit/test_projection.py`. Using `pyproj` in production would add a dependency and ~10× more code for no measurable improvement in accuracy.

The anchor `(35.773°N, 78.610°W)` is loaded from `config/data_gen.yaml`, not hard-coded. This makes it easy to apply the same pipeline to trips in a different city.

---

## `ingest.py` — CSV parsing and 100 Hz alignment

### What I learned

This is the most complex file in the module. The core challenge is that the seven Sensor Logger channels sample at different rates:

| Channel | Approximate rate |
|---|---|
| Location (GPS) | ~1 Hz |
| Accelerometer, Gyroscope, Gravity, TotalAcceleration, Orientation | ~100 Hz |
| Magnetometer | ~10 Hz |

**Step 1 — Load all channels.** A dict comprehension calls `_read_channel()` seven times. One missing file raises `MissingRequiredChannelError` immediately, before any computation begins.

**Step 2 — Define the common time range.** `t0_ns` is the minimum timestamp of the Location channel (GPS determines the reference clock). `t_end_ns` is the minimum of all channels' maximum timestamps, ensuring the output only covers the period where every sensor was active.

**Step 3 — Build the 100 Hz grid in integer nanoseconds.**

```python
dt_ns = int(round(1e9 / 100.0))   # = 10,000,000 ns exactly
t_grid = np.arange(t0_ns, t_end_ns, dt_ns, dtype=np.int64)
```

Using integer nanoseconds is deliberate. If floats were used, accumulated floating-point rounding errors would cause drift in the timestamps over a 15-minute trip.

**Step 4 — Linear interpolation with `np.interp`.** All channels are resampled onto `t_grid` using `numpy.interp`. No scipy is needed. The function assumes sorted input, which Sensor Logger provides by construction. For GPS (~1 Hz), this is upsampling — values are linearly interpolated between real fixes. For IMU (~100 Hz), it is effectively a re-timing to the exact grid.

**Step 5 — Mark `gps_interpolated`.**

```python
gps_interp = np.ones(len(t_grid), dtype=bool)   # start: all True
for rt in loc["time"].values:
    gps_interp[np.abs(t_grid - rt) <= 50_000_000] = False
```

For each real GPS fix timestamp, any grid tick within ±50 ms is marked `False` (real). The rest remain `True` (interpolated). At 1 Hz GPS, approximately 1 in 100 ticks is a real fix.

**Step 6 — Drop warm-up and reset `t_s`.**

```python
df = df[df["t_s"] >= warmup_s].copy()
df["t_s"] = np.round(df["t_s"] - df["t_s"].iloc[0], 2)
```

The first 0.5 s are discarded because smartphone sensors sometimes exhibit startup transients. After the drop, `t_s` is renumbered from 0.00 so downstream code can treat it as a simple elapsed time without caring about the original UTC epoch.

---

## `parquet_io.py` — Schema-validated Parquet I/O

### What I learned

**Why Parquet instead of CSV?** With 88,000 rows × 30 columns, a CSV would be tens of megabytes and must be fully parsed to access any single column. Parquet stores data column-by-column with Snappy compression, resulting in ~3 MB. When the EKF evaluation stage only needs `ax_mps2`, it reads just that column without touching the rest.

**Three-layer validation in `write_parquet`:**

1. First-row pydantic check — catches schema-level errors (wrong type, out-of-range value) cheaply.
2. Full float-column NaN scan — catches interpolation gaps that slipped through.
3. PyArrow type inference — catches type mismatches at the columnar storage level.

**Metadata embedding:**

```python
kv = {
    b"trip_id":          b"day2",
    b"git_sha":          b"5e72520",
    b"schema_version":   b"1.0",
    b"generated_at_utc": b"2026-...",
}
```

Embedding `git_sha` means that any Parquet file can be traced back to the exact commit that generated it. If a bug is fixed, it is immediately clear which files need to be regenerated.

**`row_group_size = 100_000`:** A row group is the unit of parallel I/O in Parquet. Setting it to 100,000 rows means the day2 trip (~88,000 rows) fits in a single row group. If a reader wants only the first 1,000 rows, it skips the rest of the group metadata in one seek operation.

---

## `noise_fit.py` — Per-channel distribution fitting

### What I learned

The purpose of this stage is to characterize "how noisy is this specific iPhone's sensors" so that synthetic data can be generated with statistically realistic noise.

### Why three different distributions

**Gaussian (12 channels — IMU and magnetometer):** The physical source of noise in these sensors is thermal agitation of electrons in the analog circuits. By the Central Limit Theorem, the sum of many independent small noise sources converges to a normal distribution. After removing the low-frequency trend (the actual vehicle motion), the residuals are well-approximated by `N(0, σ)` where `σ ≈ 0.03 m/s²` for accelerometers.

**Rayleigh (horizontal_accuracy_m):** GPS positioning error is a two-dimensional vector. The east-west error component and the north-south error component are each independently Gaussian with mean zero. The magnitude (length) of this 2D vector follows a Rayleigh distribution — always non-negative, with a peak around a few metres and a long right tail (occasional large errors). `floc=0` fixes the distribution to start at zero because negative accuracy is physically impossible.

**von Mises (gps_bearing_deg):** Bearing (heading) is an angular quantity on the circle [0°, 360°). A normal distribution cannot represent this correctly because it would treat 359° and 1° as far apart when they are actually 2° apart. The von Mises distribution is the natural analog of the normal distribution on a circle. Its concentration parameter `kappa` plays the role of 1/σ²: large kappa means tightly clustered headings, kappa → 0 means uniformly distributed around the full circle.

### Trend removal before fitting

```python
trend = pd.Series(series).rolling(window=50, center=True).mean()
residuals = series - trend
```

Raw accelerometer values include the physical motion of the vehicle (acceleration, braking). Fitting a Gaussian to the raw values would produce a large `σ` that conflates vehicle dynamics with sensor noise. By subtracting a 500 ms rolling mean (window=50 ticks at 100 Hz), the trend is removed, leaving only the sensor noise residuals.

---

## `synth.py` — Synthetic scenario generation

### What I learned

The fundamental idea: the real trip has two components — vehicle dynamics (the physical movement, captured in the rolling-mean trend) and sensor noise (random fluctuations around that trend). Synthetic generation keeps the vehicle dynamics and replaces the noise with fresh samples drawn from the fitted distributions.

```python
trend = rolling_mean(base_df["ax_mps2"], window=50)
noise = rng.normal(loc, scale, size=n)       # from noise_fit_day2.yaml
df["ax_mps2"] = trend + noise
```

The result is a trip that follows the same route as day2 but with a different noise realization — as if the same drive had been recorded a second time with a slightly different phone.

### Determinism via seeding

```python
rng = np.random.default_rng(seed)
```

Scenario `i` always uses `seed = seed0 + i`. Given the same seed and the same base Parquet, the output is byte-identical across runs. This is essential for reproducible tests: a regression can always be reproduced by re-running with the same seed.

### Three stress event types

**`gps_dropout`** simulates tunnels or underpasses. The `gps_interpolated` flag is forced to `True` and `horizontal_accuracy_m` is set to 50 m (essentially unusable) for the specified interval. This tests whether the EKF can maintain position accuracy using IMU only, without any GPS correction, for 30 seconds or more.

**`imu_bias_step`** simulates sensor drift due to temperature change. A constant offset is added to one accelerometer axis from a specific time onward. The EKF should detect and compensate for this bias; the RMSE report reveals whether it does.

**`mag_anomaly`** injects 500 µT random spikes into the magnetometer channels. Earth's field is ~50 µT, so 500 µT is ten times larger — the kind of anomaly caused by large transformers or steel-reinforced structures. Any heading estimation that relies on the magnetometer should degrade gracefully.

---

## `ks_test.py` — Two-sample KS-test gate

### What I learned

### What the KS test actually asks

The Kolmogorov-Smirnov two-sample test asks: *could these two sets of numbers have been drawn from the same probability distribution?*

It computes the empirical CDF (cumulative distribution function) of each sample and finds the largest vertical gap between the two CDF curves. That gap is the KS statistic D. The p-value is the probability of seeing a gap at least this large by chance if the two samples truly came from the same distribution.

- `p > 0.05` → the gap is plausibly due to chance → cannot reject "same distribution" → **PASS**
- `p ≤ 0.05` → the gap is too large to be chance → distributions are likely different → **FAIL**

### Why max_n = 200

With 88,000 rows, the KS test has enormous statistical power. It will reject even a 0.001% difference between the real and synthetic distributions — a difference that is physically meaningless given that the noise model is a deliberate approximation. Capping both pools at 200 rows produces a critical D value of approximately 0.136. This means the test detects distribution shifts larger than ~14% of the CDF range while tolerating the minor approximation errors that are inherent in any noise model.

Balancing the pool sizes (making both samples the same size) is also important. An unbalanced test has asymmetric power: it is more sensitive to deviations in the larger sample.

### Why three channels are excluded

`gps_bearing_deg` wraps around at 360°/0°. A CDF built from angular data is not monotone in the usual sense — a value of 359° and a value of 1° are close, but their CDFs are at opposite ends of the [0, 360] axis. This violates the KS test's assumption of a monotone CDF.

`horizontal_accuracy_m` and `speed_accuracy_mps` have distributions that the noise model approximates poorly (bimodal behavior, heavy tails). Including them would cause frequent false FAILs unrelated to the quality of the noise model for the channels that matter to the EKF.

### The gate

12 channels are tested. The gate passes if at least 10 (≥ 80%) return `p > 0.05`. This allows for 1–2 channels where the noise model is a weaker fit without blocking the entire pipeline. Exit code 0 = gate passed; exit code 4 (`GATE_FAILURE`) = gate failed, `make` stops.

---

## Key concepts encountered for the first time

**MLE (Maximum Likelihood Estimation):** A method for fitting distribution parameters. Given data and a distribution family, MLE finds the parameter values that make the observed data most probable. `scipy.stats.norm.fit(residuals)` uses MLE internally to return `(loc, scale)`.

**Allan deviation / noise model:** Not directly visible in the code but referenced in the design documents. A technique for measuring IMU noise characteristics (angle random walk, velocity random walk, bias instability) from static data. The `noise_fit.yaml` captures a simplified version of this.

**Row group (Parquet):** The unit of parallel I/O in a Parquet file. Setting `row_group_size` controls the granularity of random access. Too small → many seeks; too large → must read more data than needed for column-selective queries.

**von Mises distribution:** The circular analog of the normal distribution, parameterized by a mean direction `mu` and concentration `kappa`. Used here for GPS bearing because bearing is an angular quantity where 0° and 360° are the same point.

**Rayleigh distribution:** A special case of the chi distribution with two degrees of freedom. Arises naturally when the magnitude of a 2D vector is taken and both components are independent zero-mean Gaussians. Used here for GPS horizontal accuracy.

---

## What I would do differently knowing this now

1. **Read `schemas.py` first before any other file.** It defines the vocabulary for the entire pipeline. Understanding what `Aligned100Hz` contains makes every other file immediately readable.

2. **Understand `gps_interpolated` earlier.** I initially assumed all 100 Hz GPS values were real measurements. Realizing that 99% are interpolated and the EKF skips measurement updates for those rows changes how I understand the fusion logic.

3. **Trace the config dependency explicitly.** `config/data_gen.yaml` controls `lat0`, `lon0`, `warmup_s`, `target_hz`, `ks_gate_p_threshold`, and `ks_gate_pass_rate`. All of these affect behavior in ways that are not obvious from reading the Python alone.

---

## Next in this series

- `bag_bridge/` — Parquet → MCAP conversion for ROS 2
- `src/localization/` — C++ EKF and UKF nodes
- `src/evaluation/` — RMSE, NEES, RTS smoother
- `src/ideal_driver/` — Valhalla map-matching and quintic trajectory synthesis
- `src/scoring/` — Six-component penalty model and tip lookup
- `src/reporting/` — Jinja2 + Folium HTML report generation
