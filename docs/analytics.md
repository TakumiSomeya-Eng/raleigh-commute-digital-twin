# Scoring Analytics — day2 (EKF)

Run date: 2026-05-23
Command: `PYTHONPATH=src py -3.10 -m scoring score --trace day2 --filter ekf --out-dir out`

---

## Score History

| Run | `score_0_100` | `jerk` | `speed` | `deviation` | Note |
|---|---|---|---|---|---|
| Initial (2026-05-23) | 34.0 | 1.000 | 1.000 | 1.000 | Before any fixes |
| After jerk LPF fix | 53.4 | **0.353** | 1.000 | 1.000 | double-LPF on `components.py` |
| After OSM speed limits | 54.4 | 0.353 | **0.954** | 1.000 | 59 corridors in `speed_limits.yaml` |
| After KDTree projection | **58.3** | 0.353 | **0.759** | 1.000 | nearest-point replaces arc-length |
| After T3.5 adaptive gate | 57.8 | **0.345** | 0.783 | 1.000 | pure-Python EKF; divergence loop fixed; `deviation` still saturated (see below) |

Current result:

| Field | Value |
|---|---|
| `score_0_100` | **57.8 / 100** |
| `suggested_tip_band` | 0–59 ("Poor") |
| `suggested_tip_pct` | 10% |
| `fused_source` | ekf |
| `aggregate_raw` | 0.4218 |

---

## Component Breakdown (current)

| Component | raw | weight | weighted | Status |
|---|---|---|---|---|
| `jerk` | 0.345 | 0.30 | 0.104 | Fixed (was 1.000); improved after smoother EKF |
| `harsh_brake` | 0.000 | 0.20 | 0.000 | OK |
| `lat_accel` | 0.078 | 0.15 | 0.012 | OK |
| `speed` | 0.783 | 0.20 | 0.157 | Improved (was 1.000); slight increase vs arc-length run due to accurate road segment lookup |
| `deviation` | 1.000 | 0.10 | 0.100 | **SATURATED — reference_path / GPS accuracy** (see T3.6) |
| `lane_change` | 1.000 | 0.05 | 0.050 | **SATURATED — EKF noise + genuine events** |

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
mean_excess = 1.06 m/s³  →  penalty = 0.353
```

---

### `speed` — IMPROVED (was: uniform 30 mph OSM fallback)

**Diagnosis**: `config/speed_limits.yaml` had `corridors: {}` (empty). All 71 OSM
way IDs fell back to `urban_default_mps: 13.4` (30 mph). With the driver's median
speed at 18.2 m/s (65 km/h), 59.6 % of samples exceeded the 14.29 m/s threshold,
saturating the penalty immediately.

**Fix**: Queried Overpass API (with `User-Agent` header — without it returns HTTP 406)
for all 71 way IDs. Got real `maxspeed` tags for 57/71 ways. For the remaining 14,
queried `highway` type: 2 were `motorway_link` (set to 45 mph); 12 were `service`
roads (parking/driveway geometry, 30 mph default is appropriate).

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

**Remaining**: `speed` raw = 0.954. Fraction still exceeding limit + tolerance: 21.2 %.
Mean excess where positive: 3.55 m/s. Mean squared excess: 5.29 m²/s² (sat = 4.0).
Residual penalty appears to reflect genuine speeding on Capital Boulevard and I-440.

---

### `speed` — further improved by KDTree projection

After OSM corridors were populated, `speed` raw dropped from 1.000 to 0.954 (arc-length
interpolation was still assigning some fused positions to the wrong speed-limit segment).
Replacing arc-length interpolation with `cKDTree` 2D nearest-point projection in
`speed_penalty()` reduced `speed` raw from 0.954 → **0.759**.

---

### `deviation` — OPEN (EKF position drift)

**KDTree fix applied**: arc-length interpolation replaced with `cKDTree` nearest-point
projection in `deviation_penalty()` (`src/scoring/components.py`). This eliminated
the coordinate-system mismatch but `deviation` raw remains **1.000**.

**True root cause — EKF position drift**: `cKDTree` nearest-point distances reveal
that the EKF positions themselves are far from the road centerline:

```
nearest-point distance distribution (fused_ekf.parquet → reference_path):
  median  :   4.5 m
  p90     :  29.7 m
  max     :  46.2 m
  mean_excess (> inlane_m=1.5): 7.2 m   →   penalty = clip(7.2 / 3.0, 0, 1) = 1.000
```

The EKF state vector tracks `[px, py, v, psi]` but **never receives GPS position
updates** — only speed and heading from the GPS fix are used. Without position
corrections, accumulated IMU integration error reaches 46 m over the 20-minute trip.
`deviation_penalty` is not meaningful until the EKF fuses GPS position.

**Fix required**: add `[lat, lon]` → EKF measurement update so the filter corrects its
position estimate at each ~1 Hz GPS fix. This is a P2-layer change (EKF implementation),
not a scoring-layer change.

---

### `lane_change` — OPEN (EKF position noise / genuine events)

`lane_change_penalty()` does not use `reference_path`; it detects yaw excursions
followed by sustained lateral displacement > 2 m in `fused (px_m, py_m)`. With EKF
positions drifting up to 46 m, the lateral-displacement check sees spurious offsets
from accumulated IMU error in addition to any genuine lane changes. The penalty is
likely a mix of genuine manoeuvres on I-440 / Capital Boulevard and EKF noise;
it cannot be trusted until EKF position drift is resolved.

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
| P2 | `speed` | OSM speed limit uniform 30 mph fallback | **FIXED** — 59 corridors in `speed_limits.yaml` + KDTree projection; raw 1.0 → 0.759 |
| P3 | `deviation` / `lane_change` | Arc-length coordinate mismatch | **FIXED** — KDTree nearest-point projection in `speed_penalty()` + `deviation_penalty()` |
| P4 | `deviation` / `lane_change` | EKF divergence loop at minute 12 | **FIXED** — adaptive gate bypass in `ekf_node.cpp` + pure-Python EKF (`scripts/py_ekf.py`) |
| P5 | `deviation` | Reference_path direction mismatch on divided highway at min 3–4 | **OPEN** — backlogged as **T3.6** |

## Remaining Issues

### `deviation` — OPEN: reference_path alignment + GPS/OSM accuracy floor

**EKF divergence loop (T3.5) is fixed.** After re-running with the adaptive gate
(`scripts/py_ekf.py`), the per-minute distance from centerline is now:

```
post-T3.5 EKF → reference_path:
  Overall:     median = 4.66 m,  p90 = 12.78 m,  mean_excess = 7.0 m
  min  3–4:    median = 61.4 m   (reference_path direction mismatch — see below)
  min  5–11:   median = 2.1–5.0 m  (tracking correct)
  min 12–15:   median = 3.7–6.4 m  (divergence loop FIXED; was 37.5 m)
```

`deviation` raw is still 1.000 because `mean_excess = 7.0 m >> sat_m = 3.0 m`.

**Root cause 1 — reference_path direction mismatch at minute 3–4 (T3.6):**

```
GPS at min 3–4:  (35.7940–35.7950 N,  −78.6411 to −78.6412 E)
Reference path:  (35.7935–35.7950 N,  −78.6402 to −78.6404 E)
Offset:          ~85 m consistently WEST of reference
GPS hacc:        1.5–2.1 m  (GPS self-reports accurate)
```

The vehicle was on the **northbound** lanes of a divided arterial (Capital Boulevard /
US-1) while Valhalla map-matched the reference_path to the **southbound** lanes, or
vice versa.  Median GPS distance to reference = 64.5 m across the full minute.
EKF correctly follows GPS to that offset → deviation penalty saturated by this alone.

**Root cause 2 — GPS + OSM accuracy floor:**

Even excluding minute 3–4, mean_excess = 4.3 m (penalty = 1.44 × sat → still saturated).
With GPS hacc = 1.5–2 m and OSM centerline accuracy = 2–5 m, the inherent EKF-to-reference
distance floor is ~3–6 m.  Reducing `deviation` below 1.0 requires either:

- Correcting the reference_path direction at min 3–4 (T3.6) — removes the 7 m/trip
  contribution from that segment
- Tightening GPS noise through better sensor fusion (cm-level positioning)

**Score-for-≥70 analysis (post-T3.5):**

```
Base aggregate (jerk+harsh_brake+lat+speed): 0.272
For score=70: need deviation×0.1 + lane_change×0.05 ≤ 0.028
  → if lane_change=0: deviation ≤ 0.28  (mean_excess ≤ 0.85 m)
  → currently (excl min 3–4): mean_excess = 4.3 m  (5× too high)
```

Fixing the reference_path direction mismatch at minute 3–4 would eliminate ~3.0 m from
mean_excess (contribution: 64m excess × 60s / 889s ≈ 4.3 m).  After that fix,
mean_excess ≈ 4.3 – 4.3 = effectively the GPS/OSM floor.  Score ≥ 70 then requires
addressing the GPS/OSM accuracy gap.

**Fix**: see **T3.6** in `docs/DEV_PLAN.md`.
