# Scoring Analytics — day2 (EKF)

Run date: 2026-05-23
Command: `PYTHONPATH=src py -3.10 -m scoring score --trace day2 --filter ekf --out-dir out`

---

## Score History

| Run | `score_0_100` | `jerk` | `speed` | `deviation` | `lane_change` | Note |
|---|---|---|---|---|---|---|
| Initial (2026-05-23) | 34.0 | 1.000 | 1.000 | 1.000 | 1.000 | Before any fixes |
| After jerk LPF fix | 53.4 | **0.353** | 1.000 | 1.000 | 1.000 | double-LPF on `components.py` |
| After OSM speed limits | 54.4 | 0.353 | **0.954** | 1.000 | 1.000 | 59 corridors in `speed_limits.yaml` |
| After KDTree projection | **58.3** | 0.353 | **0.759** | 1.000 | 1.000 | nearest-point replaces arc-length |
| After T3.5 adaptive gate | 57.8 | **0.345** | 0.783 | 1.000 | 1.000 | pure-Python EKF; divergence loop fixed |
| After T3.6 Valhalla re-match | 64.3 | 0.260 | 0.596 | 1.000 | 1.000 | reference_path direction fix at min 3–4 |
| After T3.7 GPS-primary + road-relative LC | **69.6** | 0.260 | **0.591** | **0.790** | **0.372** | GPS positions for deviation; road-relative lane-change |

Current result:

| Field | Value |
|---|---|
| `score_0_100` | **69.6 / 100** |
| `suggested_tip_band` | 60–74 ("Fair") |
| `suggested_tip_pct` | 15% |
| `fused_source` | ekf |
| `aggregate_raw` | 0.3036 |

---

## Component Breakdown (current)

| Component | raw | weight | weighted | Status |
|---|---|---|---|---|
| `jerk` | 0.260 | 0.30 | 0.078 | Fixed (was 1.000); double-LPF on velocity |
| `harsh_brake` | 0.000 | 0.20 | 0.000 | OK — no event exceeded −3.5 m/s² |
| `lat_accel` | 0.065 | 0.15 | 0.010 | OK — 6.5 % of 4.0 m²/s⁴ saturation |
| `speed` | 0.591 | 0.20 | 0.118 | Improved (was 1.000); reflects genuine speeding on Capital Blvd / I-440 |
| `deviation` | 0.790 | 0.10 | 0.079 | **Unsaturated** — GPS/OSM accuracy floor; raw GPS gives mean_excess = 2.37 m |
| `lane_change` | 0.372 | 0.05 | 0.019 | Improved (was 1.000); 11 events at 0.74 epm; road-relative algorithm |

---

## Root Cause Analysis

### `jerk` — FIXED (was: EKF velocity noise double-differentiation)

**Diagnosis**: `jerk_penalty()` computed `j = d²v/dt²` via two successive
`np.gradient` calls with no filtering. At 100 Hz, a 1 m/s velocity step between
adjacent EKF samples produces `a_lon = 100 m/s²` and `j_lon = 10,000 m/s³` —
far above the 3.0 m/s³ saturation constant.

```
actual j_lon before fix:  std = 47.4 m/s³,  min = -1150,  max = +1043 m/s³
                           |j| > 3.0 m/s³ in 57.1 % of samples
ideal j_lon_mps3:          p50 ≈ 0,  p75 = 0.033 m/s³  (near-zero baseline)
```

Key diagnostic: `harsh_brake = 0.0` despite `jerk` being saturated. Since
`harsh_brake` applies a 3 Hz LPF before detection, its zero score confirmed the
driver did not brake harshly — the jerk penalty was reacting to noise, not real jerk.

**Fix** (`src/scoring/components.py`): apply `_lpf_accel(a_lon, 3 Hz)` then
`_lpf_accel(j_lon, 1 Hz)` before comparing against `j_ideal`. The second stage at
1 Hz is key: acceleration changes over less than 1 second do not contribute to the
jerk penalty, which is the appropriate timescale for ride-comfort assessment.

```
actual j_lon after fix:   std = 2.26 m/s³,  |j| > 3.0 in 14.0 % of samples
mean_excess = 1.06 m/s³  →  penalty = 0.353 (further reduced to 0.260 after T3.5/T3.6 EKF fixes)
```

---

### `speed` — IMPROVED (was: uniform 30 mph OSM fallback)

**Diagnosis**: `config/speed_limits.yaml` had `corridors: {}` (empty). All 71 OSM
way IDs fell back to `urban_default_mps: 13.4` (30 mph). With the driver's median
speed at 18.2 m/s (65 km/h), 59.6 % of samples exceeded the 14.29 m/s threshold,
saturating the penalty immediately.

**Fix**: Queried Overpass API for all 71 way IDs. Got real `maxspeed` tags for 57/71 ways.
KDTree projection then assigns each EKF position to the correct OSM road segment.

```
reference_path speed_limit_mps after fix:
  25 mph  (11.18 m/s)  :  2.9%  — local streets (West Johnson St, etc.)
  30 mph  (13.40 m/s)  : 14.2%  — service roads + untagged (default)
  35 mph  (15.65 m/s)  :  2.8%  — West Peace Street
  40 mph  (17.88 m/s)  :  2.5%  — South New Hope Road
  45 mph  (20.12 m/s)  : 35.4%  — Capital Boulevard (dominant segment)
  55 mph  (24.59 m/s)  :  5.5%
  60 mph  (26.82 m/s)  : 30.4%  — I-440
  70 mph  (31.29 m/s)  :  6.4%  — I-87 / US-64 / US-264
```

**Remaining**: `speed` raw = 0.591. Residual penalty reflects genuine speeding on
Capital Boulevard and I-440.

---

### `deviation` — IMPROVED (was: EKF position drift)

**Root cause 1 — EKF divergence loop at minute 12 (T3.5 FIXED):** Chi-squared gate
rejected valid GPS updates when EKF diverged → divergence loop. Fixed via adaptive
gate bypass in `scripts/py_ekf.py`.

**Root cause 2 — Reference_path direction mismatch at minute 3–4 (T3.6 FIXED):**
Valhalla originally matched to the southbound carriageway while GPS was on northbound.
Fixed by re-running Valhalla map matching with corrected EKF positions.

**Root cause 3 — EKF positions worse than raw GPS (T3.7 FIXED):**
CTRV propagation between 1 Hz GPS fixes drifts 1–4 m from GPS due to IMU integration
error. Fix: `py_ekf.py` outputs GPS-interpolated `px_m/py_m` directly (from
`aligned_100hz.parquet`), while EKF velocity/heading remain filter-derived.

```
position accuracy vs reference_path (after T3.7):
  raw GPS (1 Hz, n=9199):         median = 3.58 m,  mean_excess = 2.37 m  →  raw = 0.791
  GPS-interp 100 Hz (aligned):    median = 3.50 m,  mean_excess = 2.37 m  →  raw = 0.791
  EKF output (GPS-primary):       median = 4.22 m,  mean_excess = 2.37 m  →  raw = 0.790
```

**Remaining floor — GPS + OSM accuracy:**

```
GPS hacc median: 2.05 m
OSM centerline uncertainty: 2–5 m
Inherent distance floor: 3–6 m
mean_excess = 2.37 m  →  deviation_raw = 0.790 (below saturation)
```

Further improvement requires cm-level positioning (RTK GPS or HD map matching).

---

### `lane_change` — IMPROVED (was: algorithm triggered on highway curves)

**Root cause 1 — Heading-based lateral displacement (T3.7 FIXED):**
Original algorithm measured lateral displacement in vehicle-heading coordinates.
Highway interchanges and curves produce 45–90° heading changes, causing false
displacement readings up to 42 m → 39 false "lane changes".

**Fix** (`src/scoring/components.py`): when `reference_path` is provided, use
road-relative lateral offset (change in distance-to-centerline) instead.
Road curves preserve this distance; lane changes shift it by ~one lane width.
Added on-road guards (both event endpoints must be within 10 m of reference)
and raised threshold from 2.0 m → 3.0 m (minimum AASHTO lane width; sub-3m
changes cannot represent a full lane change with GPS hacc = 2 m).

```
Before T3.7:  39 detected events  rate = 2.64 epm  raw = 1.000
After T3.7:   11 detected events  rate = 0.74 epm  raw = 0.372
```

**Remaining 11 events (rate = 0.74 epm):**
All have road-relative offset changes of 3.2–6.2 m and are plausible single lane
changes on Capital Boulevard (3-lane per direction) and I-440 interchange.
With GPS hacc = 2 m, SNR for a 3.6 m lane change is ~1.3 — some events may
still be GPS noise artifacts.

---

### `harsh_brake` and `lat_accel` — no issues

`harsh_brake = 0.0`: no deceleration event exceeded −3.5 m/s² for ≥ 0.3 s after
3 Hz LPF. The driver braked smoothly throughout the trip.

`lat_accel = 0.065`: only 6.5 % of the 4.0 m²/s⁴ saturation. Cornering behaviour
is close to the ideal profile.

---

## Fix Log

| Priority | Component | Root cause | Status |
|---|---|---|---|
| P1 | `jerk` | Double-diff of noisy EKF velocity | **FIXED** — double LPF (3 Hz on `a_lon`, 1 Hz on `j_lon`) in `components.py` |
| P2 | `speed` | OSM speed limit uniform 30 mph fallback | **FIXED** — 59 corridors in `speed_limits.yaml` + KDTree projection; raw 1.0 → 0.591 |
| P3 | `deviation` / `lane_change` | Arc-length coordinate mismatch | **FIXED** — KDTree nearest-point projection in `speed_penalty()` + `deviation_penalty()` |
| P4 | `deviation` / `lane_change` | EKF divergence loop at minute 12 | **FIXED** — adaptive gate bypass in `ekf_node.cpp` + `scripts/py_ekf.py` |
| P5 | `deviation` | Reference_path direction mismatch at min 3–4 | **FIXED** — T3.6: Valhalla re-match with corrected EKF positions |
| P6 | `deviation` | EKF CTRV drift > GPS accuracy between 1 Hz fixes | **FIXED** — T3.7: GPS-primary positions in `py_ekf.py` |
| P7 | `lane_change` | Heading-based algorithm triggers on highway curves | **FIXED** — T3.7: road-relative offset algorithm + on-road guard + 3.0 m threshold |

## Remaining Issues

### `deviation` — GPS/OSM accuracy floor

`deviation` raw = 0.790. mean_excess = 2.37 m, saturation at 3.0 m (30% margin).
The GPS/OSM accuracy floor (GPS hacc = 2 m + OSM centerline ± 2–5 m) yields a
minimum inherent distance of 3–6 m from the road centerline. Further reduction
requires cm-level positioning or HD maps.

### `lane_change` — GPS accuracy limits SNR

11 events at 0.74 epm (raw = 0.372). SNR for a 3.6 m lane change with GPS hacc = 2 m
is approximately 1.3 — borderline. Sub-3.5m change events cannot be distinguished
from GPS noise without higher-precision positioning.

### `speed` — genuine speeding

speed raw = 0.591 reflects genuine speeding on Capital Boulevard (45 mph) and
I-440 (60 mph). Not reducible without route/speed data changes.

### Score-for-≥70 analysis

```
Current: aggregate_raw = 0.3036  →  score = 69.6
Gap to 70: aggregate_raw needs to drop by 0.0036

Components at floor:
  deviation:   0.790 × 0.10 = 0.0790  (GPS/OSM floor)
  speed:       0.591 × 0.20 = 0.1182  (genuine speeding)
  lane_change: 0.372 × 0.05 = 0.0186  (GPS accuracy limit at 2m hacc)
  jerk:        0.260 × 0.30 = 0.0780  (physical driving)

Score ≥ 75 ("Good" band, 20% tip) requires aggregate_raw ≤ 0.25.
Gap = 0.054 — requires sub-meter positioning or significant route change.
```
