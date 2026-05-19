# Learning Notes: `src/ideal_driver/`

**Project:** Raleigh Commute Digital Twin — *Uber vs. My AI*
**Module:** `src/ideal_driver/` (Phase P4 — FR-9)
**Purpose of this document:** Post-completion study notes written to close the implementation-level understanding gap that emerged from AI-assisted development (Claude Code). This is the fourth in a planned series covering each module in the project.

---

## Overview

`ideal_driver` generates the "ideal AI driver" trajectory that real Uber rides are scored against. It takes the EKF-fused trajectory and produces a physically plausible reference trajectory: one that follows the road network, respects speed limits, and minimizes jerk.

### File map

| File | Role | Make target |
|---|---|---|
| `valhalla_client.py` | Map-match EKF trajectory to road network | `make ideal` |
| `reference_path.py` | Extract road centerline at 1 m intervals | `make ref` |
| `speed_limits.py` | Look up speed limits from OSM via Overpass API | called by `reference_path.py` |
| `speed_profile.py` | Compute comfort- and curvature-constrained speed profile | `make speed` |
| `quintic.py` | Synthesize time-domain trajectory via quintic polynomials | `make traj` |

### Execution order and data flow

```

fused_ekf.parquet
    ↓ make ideal  (valhalla_client.py)
route_matched.parquet   (snapped positions + OSM way IDs)
    ↓ make ref    (reference_path.py + speed_limits.py)
reference_path.parquet  (1 m grid: s, px, py, heading, curvature, speed_limit)
    ↓ make speed  (speed_profile.py)
ideal_speed.parquet     (s, v_ideal, a_ideal, j_ideal)
    ↓ make traj   (quintic.py)
ideal_trajectory.parquet (t, px, py, v, a_lon, a_lat, j_lon, psi, psi_dot)

```

---

## `valhalla_client.py` — map-matching the EKF trajectory

### Why map-matching is necessary

The EKF output (`fused_ekf.parquet`) is derived from GPS and IMU fusion. GPS has 3–20 m noise, so the estimated trajectory deviates from the true road centerline. Without map-matching, it is impossible to know which road segment was being traveled, and therefore impossible to retrieve the correct speed limit or compute route deviation.

### What Valhalla is

Valhalla is an open-source routing engine based on OpenStreetMap data. In this project it runs inside a Docker container at `localhost:8002`. Docker is used because Valhalla requires a large set of pre-processed OSM tiles (several GB for the Raleigh corridor) and has complex OS-level dependencies (boost, protobuf, prime_server, etc.). Packaging everything in a container guarantees identical behavior across Windows, macOS, and Linux without dependency conflicts.

### What the Meili algorithm does

Meili is Valhalla's map-matching algorithm, based on a Hidden Markov Model (HMM). For each input GPS point there are multiple candidate road segments nearby. A naive "nearest road" approach would cause the matched path to jump between roads due to GPS noise. HMM solves this by jointly maximizing two probabilities: how close each point is to a candidate road (emission probability) and how likely it is to transition from one road to the next (transition probability). The result is the most probable sequence of road segments for the entire trajectory.

### Sub-sampling to 5 Hz

```python
stride = max(1, int(round(source_hz / subsample_hz)))
sub = fused.iloc[::stride]

```

The EKF output is 100 Hz (88,000 rows). Sending all points to Valhalla would produce large payloads and slow processing. Map-matching does not require high temporal resolution — at 5 Hz and 36 km/h (10 m/s), points are 2 m apart, which is sufficient to track road geometry. Sub-sampling reduces the payload to ~4,500 points.

### Chunking with overlap

```python
_CHUNK_SIZE    = 2000
_CHUNK_OVERLAP = 20

```

Long traces are split into 2,000-point chunks. The HMM processes each chunk independently, so the last matched road of chunk N is unknown when chunk N+1 begins. Without overlap, boundary points are matched without context and often snap to the wrong road.

The overlap re-sends the last 20 points of chunk N at the beginning of chunk N+1. This gives the HMM the context it needs. On output, the first 10 rows of each non-first chunk are discarded to remove the duplicated overlap region.

### Exponential back-off retry

```python
for attempt in range(1, _HTTP_RETRIES + 1):
    ...
    time.sleep(2**attempt)   # 2 s, 4 s

```

Valhalla may return 5xx errors when temporarily overloaded (processing a large chunk is CPU and memory intensive). The retry loop waits 2 s after the first failure and 4 s after the second. Exponential back-off is used instead of a fixed interval because it gives the server progressively more time to recover, and if multiple processes are retrying simultaneously it spreads their retry attempts over time.

4xx errors (client-side errors such as malformed JSON) are not retried because the same request will always fail.

**`ConnectionError` vs `Timeout`:**

```

ConnectionError: TCP connection could not be established
                 (Docker container is not running, network is down)

Timeout:         TCP connection succeeded but no HTTP response
                 arrived within 60 seconds
                 (Valhalla is alive but overwhelmed by the chunk)

```

### OSM way ID

Each road segment in OpenStreetMap is a `way` element with a globally unique integer ID. Valhalla returns the way ID of the matched road segment for each input point. This ID is later used by `speed_limits.py` to query the posted speed limit from the Overpass API.

### Confidence score

```python
confidence = max(0.0, 1.0 - dist / _MAX_SNAP_DISTANCE_M)

```

Points snapped within 0 m of the road centerline receive confidence 1.0. Points more than 50 m from the nearest road receive confidence 0.0 and are treated as unmatched. `reference_path.py` filters out all rows with `confidence == 0.0`.

### Quality gate

```python
_MIN_MATCH_RATE = 0.60
if match_rate < _MIN_MATCH_RATE:
    return _EXIT_UNMATCHED   # exit code 3

```

If fewer than 60% of points are matched, the pipeline stops. Typical urban GPS in Raleigh achieves 70–90% match rate. Below 60% suggests severely degraded GPS or a route that was not driven on public roads.

---

## `reference_path.py` — road centerline at 1 m intervals

### Why uniform arc-length resampling is needed

`route_matched.parquet` is time-based: points are denser in slow sections and sparser in fast sections. Speed profile computation and quintic synthesis both need a uniform spatial grid (arc-length based) so that curvature and speed limits are consistently defined per metre of road.

### Step 1 — Removing consecutive duplicate points

```python
dists = np.hypot(np.diff(px), np.diff(py))
keep = np.concatenate([[True], dists > tol_m])

```

`np.hypot(a, b) = sqrt(a² + b²)` computes the Euclidean (straight-line) distance between consecutive points. Points closer than 1 cm are dropped. Without this step, arc-length computation would produce zero-length segments causing division-by-zero in curvature calculations.

### Step 2 — Cumulative arc length

```python
seg_lens = np.hypot(np.diff(px_raw), np.diff(py_raw))
s_orig = np.concatenate([[0.0], np.cumsum(seg_lens)])

```

`np.cumsum` computes the running total: `[d1, d1+d2, d1+d2+d3, ...]`. This converts point-to-point distances into a monotonically increasing arc-length coordinate `s`, measured in metres from the start of the route.

### Step 3 — Resampling to 1 m grid

```python
s_new = np.arange(0.0, total_len, 1.0)
px_r = np.interp(s_new, s_orig, px_raw)
py_r = np.interp(s_new, s_orig, py_raw)

```

`np.interp` uses the arc-length axis (`s_orig`) as the common reference and linearly interpolates the coordinate values at evenly spaced 1 m intervals. A 15 km route produces approximately 15,000 rows.

### Step 4 — Heading computation

```python
dpx = np.gradient(px_r, s_new)
dpy = np.gradient(py_r, s_new)
heading_unwrapped = np.unwrap(np.arctan2(dpy, dpx))

```

**`np.gradient` uses central differences** for interior points:

```

df/ds[i] ≈ (f[i+1] - f[i-1]) / (s[i+1] - s[i-1])

```

Central differences use points on both sides of i, making them second-order accurate (error proportional to Δs²) compared to forward differences which are only first-order accurate (error proportional to Δs). End points use forward/backward differences as fallback.

**`np.unwrap`** removes the ±π wrap-around discontinuity from `arctan2` output, making heading a continuous function of arc length. This is essential for the subsequent `np.gradient(heading, s)` call — without it, a 179° → -179° transition would appear as a Δψ of ≈ 6.28 rad instead of ≈ 0.

After curvature computation, heading is renormalized to `[-π, π]` for output.

### Step 5 — Curvature computation and smoothing

```python
curvature_raw = np.gradient(heading_unwrapped, s_new)
curvature = uniform_filter1d(curvature_raw, size=window_pts, mode="nearest")

```

Curvature κ = dψ/ds (heading change per metre). Its physical meaning:

```

κ = 1/r  where r = turning radius

κ = 0.02 /m  →  r = 50 m  (tight urban corner)
κ = 0.005 /m →  r = 200 m (gentle curve)
κ ≈ 0    /m  →  straight road

```

OSM road geometry has small digitization kinks that produce spurious curvature spikes. A 5 m uniform (box) filter smooths these without introducing the ringing artifacts that a Gaussian filter would cause at sharp corners.

### Step 6 — Way ID gap filling

```python
# Forward pass: propagate known IDs forward
for i, w in enumerate(result):
    if w is not None: last = w
    elif last is not None: result[i] = last

# Backward pass: propagate known IDs backward
for i in range(len(result)-1, -1, -1):
    ...

```

Valhalla sometimes returns no way ID for intersection points. The two-pass fill propagates the nearest known way ID both forward and backward, ensuring every row has a valid way ID for the speed limit lookup.

---

## `speed_limits.py` — speed limit lookup

### Three-level priority chain

```

Priority 1: Overpass API  → OSM maxspeed tag for the way ID
Priority 2: config/speed_limits.yaml → hand-coded corridor overrides
Priority 3: urban_default_mps = 13.4 m/s (30 mph)

```

Results are memoized in `_cache` so each way ID is queried from Overpass at most once per pipeline run.

### Overpass API query

```

[out:json][timeout:20];
way(id:111,222,333);out tags;

```

Only unique way IDs are queried (duplicate IDs from the 15,000-row reference path are deduplicated with `set()`). `out tags` returns only OSM tag metadata, not geometry, keeping the response small.

### Parsing the `maxspeed` tag

OSM `maxspeed` tags are not standardized across contributors:

```

"35 mph"   → 35 × 0.44704 = 15.6 m/s
"35mph"    → same
"50"       → 50 / 3.6 = 13.9 m/s  (bare integer = km/h by OSM convention)
"50 km/h"  → same
"none"     → None (no limit, e.g. German Autobahn)
"signals"  → None (speed set by signal display)

```

Regular expressions handle the `mph` and `km/h`/bare-integer cases. Unrecognized formats return `None`, which falls through to the YAML/default priority chain.

### `skip_overpass` flag

When `True`, the Overpass query is skipped entirely. Used in CI environments without internet access and in offline development. The pipeline continues using YAML overrides and the default speed limit.

### Graceful degradation

The Overpass query is wrapped in a broad `except Exception` block that returns an empty dict on any failure. This is intentional: if the Overpass server is unavailable, the pipeline should continue rather than abort. The speed limit error introduced by falling back to the default value (13.4 m/s) is smaller than the uncertainty in the GPS trace itself.

---

## `speed_profile.py` — ideal speed profile

### Why five passes are needed

Speed limits alone are insufficient:

1. A vehicle following the speed limit into a tight curve would experience excessive lateral G.
2. Braking must begin before a curve, not at the curve entry.
3. Sudden speed changes produce high jerk, making the trajectory unrealistic.

The five-pass algorithm resolves these in sequence.

### Pass 1 — Curvature speed cap

```python
v_curv = np.sqrt(a_lat_max / kappa_abs)
v_raw = np.minimum(speed_limit, v_curv)

```

From circular motion physics: lateral acceleration `a_lat = v² × κ`. To keep `a_lat ≤ a_lat_max`:

```

v ≤ sqrt(a_lat_max / κ)

```

Example with `a_lat_max = 2.0 m/s²`:

```

κ = 0.02 /m (50 m radius): v_curv = sqrt(2.0 / 0.02) = 10.0 m/s
κ = 0.005/m (200 m radius): v_curv = sqrt(2.0/0.005) = 20.0 m/s
κ ≈ 0 (straight): _KAPPA_MIN = 1e-6 prevents division by zero

```

### Pass 2 — Forward acceleration pass

```python
v_limit = np.sqrt(v[i-1]**2 + 2.0 * a_max * ds)

```

From the kinematic equation `v² = v₀² + 2as`. Enforces that the vehicle cannot accelerate faster than `a_lon_max` over each 1 m step. Applied left-to-right.

### Pass 3 — Backward deceleration pass

```python
v_limit = np.sqrt(v[i+1]**2 + 2.0 * a_dec * ds)

```

Same kinematic equation applied right-to-left. Enforces that the vehicle decelerates early enough to reach the required speed at the upcoming curve. Without this pass, the ideal driver would enter curves at full speed and then brake.

### Pass 4 — Jerk smoothing

```python
sigma_s = max(3.0, v_mean * a_lon_max / max(j_max, 0.1))
v_smooth = gaussian_filter1d(v_in, sigma=sigma_pts, mode="nearest")
v_smooth = np.minimum(v_smooth, v_in)

```

**`sigma_s` derivation:** Jerk `j = da/dt = v × da/ds`. To keep `|j| ≤ j_max`, velocity changes must be spread over at least `sigma_s = v_mean × a_lon_max / j_max` metres.

After Gaussian smoothing, acceleration/deceleration feasibility is restored by re-running passes 2 and 3. Two smoothing cycles are applied to further reduce residual jerk peaks.

**`np.minimum(v_smooth, v_in)`** ensures smoothing never raises speed above the curvature/speed-limit cap. The Gaussian filter would otherwise partially fill speed "valleys" that correspond to curvature-limited corners.

### Pass 5 — Time-domain derivatives

```python
dv_ds = np.gradient(v, s)
a = v * dv_ds        # chain rule: dv/dt = v × dv/ds
da_ds = np.gradient(a, s)
j = v * da_ds        # chain rule: da/dt = v × da/ds

```

The chain rule conversion `d/dt = v × d/ds` arises because `ds/dt = v`. A vehicle moving faster traverses the same arc-length interval in less time, so the time-derivative of any quantity is larger by a factor of v.

---

## `quintic.py` — quintic polynomial trajectory synthesis

### Why quintic polynomials

To match position, velocity, and acceleration at both endpoints of a segment requires six boundary conditions. A degree-5 (quintic) polynomial has exactly six coefficients, making it the minimum-degree polynomial that satisfies all six conditions. Quintic polynomials are also the standard choice for minimum-jerk trajectory planning in robotics.

### Waypoint detection

```python
peaks, _ = find_peaks(kappa_abs, height=0.005, distance=20)
waypoints = np.unique(np.concatenate([[0], peaks, [len(kappa)-1]]))

```

`scipy.signal.find_peaks` finds local maxima of |κ| above 0.005 /m (radius ≤ 200 m) that are at least 20 m apart. Curvature peaks (apex of curves) are chosen as segment boundaries because speed is lowest there, minimizing the velocity discontinuity that would occur if the boundary were placed elsewhere.

### Segment travel time via trapezoidal integration

```python
inv_v = 1.0 / np.maximum(v_seg, _V_FLOOR)
T_seg = float(np.trapz(inv_v, s_seg))

```

The travel time for a segment of length L with variable speed v(s) is `T = ∫₀ᴸ ds/v(s)`. `np.trapz` applies the trapezoidal rule:

```

∫f(x)dx ≈ Σ (f[i] + f[i+1]) / 2 × (x[i+1] - x[i])

```

`_V_FLOOR = 0.1 m/s` prevents division by zero if v approaches zero.

### Quintic coefficient derivation

```python
c0 = 0.0        # s(0) = 0  (position starts at 0)
c1 = v0         # s'(0) = v0
c2 = a0 / 2.0  # s''(0) = a0

```

The first three coefficients follow directly from the start boundary conditions by substituting t=0 into the polynomial and its derivatives.

The remaining three are obtained by substituting t=T into the polynomial:

```

s(T)   = L  →  c0 + c1T + c2T² + c3T³ + c4T⁴ + c5T⁵ = L
s'(T)  = v1 →  c1 + 2c2T + 3c3T² + 4c4T³ + 5c5T⁴ = v1
s''(T) = a1 →  2c2 + 6c3T + 12c4T² + 20c5T³ = a1

```

After substituting the known c0–c2 and rearranging, this becomes a 3×3 linear system in c3, c4, c5. The 3×3 matrix has determinant 2, so its inverse has a closed-form expression. The resulting formulas:

```

c3 = (20r0 - 8r1 + r2) / 2
c4 = (-30r0 + 14r1 - 2r2) / (2T)
c5 = (12r0 - 6r1 + r2) / (2T²)

```

are computed in O(1) without numerical matrix inversion, making the code both fast and numerically stable.

### C2 continuity guarantee

Each segment's end boundary conditions `(v1, a1)` are taken from `ideal_speed.parquet` at the waypoint arc-length. The next segment's start conditions `(v0, a0)` use the same values from the same waypoint. Position continuity is guaranteed by construction (both segments are evaluated to the same arc-length). Velocity and acceleration continuity follow from sharing the same boundary values. Jerk (third derivative) is generally discontinuous at waypoints, which is acceptable for this application.

### Heading interpolation with unwrapping

```python
heading_unwrapped = np.unwrap(heading_arr)
interp_heading = interp1d(s_arr, heading_unwrapped, ...)
heading_out = (interp_heading(s_out) + np.pi) % (2*np.pi) - np.pi

```

Interpolating heading directly in `[-π, π]` fails across the ±π boundary. For example, if one sample is 3.10 rad and the next is -3.10 rad (both near ±π), linear interpolation gives 0 rad at the midpoint instead of ≈ ±π. Unwrapping first makes the heading a continuous monotone function, then the result is renormalized to `[-π, π]` for output.

### Lateral acceleration and yaw rate from circular motion

```python
a_lat_out   = v_out**2 * kappa_out    # a_lat = v²κ = v²/r
psi_dot_out = v_out * kappa_out       # ψ̇ = v/r = vκ

```

These come directly from circular motion: for a vehicle traveling at speed v along a curve of radius r = 1/κ, the centripetal acceleration is v²/r and the angular velocity is v/r.

---

## Key concepts encountered for the first time

**Docker and OS dependencies:** Software rarely runs in isolation. Valhalla requires specific versions of boost, protobuf, sqlite3, and other libraries. On different operating systems these may not be available, may conflict with other installed software, or may require manual compilation. Docker solves this by packaging the application and all its dependencies into a container — a lightweight isolated environment that behaves identically on any host OS.

**Hidden Markov Model (HMM) for map-matching:** Each GPS point has multiple candidate road segments. A naive nearest-road approach causes noisy jumps. HMM finds the globally most probable sequence of roads by jointly considering how close each point is to each candidate (emission probability) and how plausible each road-to-road transition is (transition probability). Meili is Valhalla's implementation of this approach.

**OSM way ID:** OpenStreetMap organizes road data as `way` elements, each with a globally unique integer ID. A way represents one continuous road segment between intersections. The way ID is the key used to retrieve speed limits, road classification, and other metadata from the Overpass API.

**Exponential back-off:** A retry strategy where the wait time doubles after each failure (2 s, 4 s, ...). Prevents all retrying clients from hammering an overloaded server simultaneously, giving it time to recover. Used here because Valhalla 5xx errors are transient — the server is alive but temporarily overwhelmed.

**Arc-length parameterization:** Using cumulative distance along the path (rather than time) as the independent variable. Makes curvature, speed limits, and spatial quantities uniform across the path regardless of vehicle speed. Enables consistent 1 m grid resampling.

**Central difference:** An approximation of a derivative using points on both sides of the evaluation point: `f'[i] ≈ (f[i+1] - f[i-1]) / (s[i+1] - s[i-1])`. More accurate than forward differences (second-order vs. first-order in Δs) because the second-order error terms cancel when both sides are used.

**`np.unwrap`:** Removes ±π discontinuities from angle arrays by detecting jumps larger than π and adding or subtracting 2π to make the sequence continuous. Essential before differentiating or interpolating angular quantities.

**Trapezoidal rule (`np.trapz`):** Numerical integration approximating ∫f(x)dx as a sum of trapezoids: `Σ (f[i]+f[i+1])/2 × Δx`. Used here to compute segment travel time T = ∫ds/v.

**Quintic polynomial (degree-5):** The minimum-degree polynomial satisfying six boundary conditions (position, velocity, acceleration at both endpoints). Produces C2-continuous trajectories (continuous up to second derivative) with inherently low jerk — the standard choice for minimum-jerk motion planning.

**C2 continuity:** A trajectory is C2 continuous if position, velocity, and acceleration are all continuous functions of time. Guaranteeing C2 continuity at waypoints means the ideal driver never experiences instantaneous changes in acceleration, making the reference trajectory physically realistic.

**Chain rule for time derivatives:** `d/dt = v × d/ds` converts arc-length derivatives to time derivatives. Arises because `ds/dt = v`. Used to compute acceleration `a = v × dv/ds` and jerk `j = v × da/ds` from the arc-length speed profile.

---

## What I would do differently knowing this now

1. **Read `reference_path.py` before `speed_profile.py` and `quintic.py`.** The arc-length parameterization introduced in `reference_path.py` — particularly why heading must be unwrapped before differentiation — is a prerequisite for understanding every computation in the downstream files.

2. **Understand map-matching output quality before building on it.** The `match_confidence` column is the reliability signal for everything downstream. Understanding what causes low confidence (multipath, off-road GPS, missing OSM ways) clarifies why `reference_path.py` filters on confidence and why the 60% quality gate exists.

3. **Trace the arc-length axis explicitly.** Both `reference_path.parquet` and `ideal_speed.parquet` use `s_m` as their common axis. `quintic.py` consumes both together. Making this relationship explicit early would have clarified why 1 m resampling is needed and how the two files are aligned.

4. **Recognize that the five speed-profile passes are sequential refinements, not independent steps.** Each pass produces a profile that satisfies one more constraint: curvature limits → forward acceleration → backward deceleration → jerk smoothing. Missing this ordering makes the logic seem redundant.

---

## Next in this series

- `src/scoring/` — Six-component penalty model and tip lookup
- `src/reporting/` — Jinja2 + Folium HTML report generation
