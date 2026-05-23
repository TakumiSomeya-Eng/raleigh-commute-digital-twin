# Scoring Analytics — day2 (EKF)

Run date: 2026-05-23
Command: `PYTHONPATH=src py -3.10 -m scoring score --trace day2 --filter ekf --out-dir out`

---

## Score History

| Run | `score_0_100` | `jerk` | `speed` | Note |
|---|---|---|---|---|
| Initial (2026-05-23) | 34.0 | 1.000 | 1.000 | Before any fixes |
| After jerk LPF fix | 53.4 | **0.353** | 1.000 | double-LPF on `components.py` |
| After OSM speed limits | **54.4** | 0.353 | **0.954** | 59 corridors in `speed_limits.yaml` |

Current result:

| Field | Value |
|---|---|
| `score_0_100` | **54.4 / 100** |
| `suggested_tip_band` | 0–59 ("Poor") |
| `suggested_tip_pct` | 10% |
| `fused_source` | ekf |
| `aggregate_raw` | 0.4564 |

---

## Component Breakdown (current)

| Component | raw | weight | weighted | Status |
|---|---|---|---|---|
| `jerk` | 0.353 | 0.30 | 0.106 | Fixed (was 1.000) |
| `harsh_brake` | 0.000 | 0.20 | 0.000 | OK |
| `lat_accel` | 0.065 | 0.15 | 0.010 | OK |
| `speed` | 0.954 | 0.20 | 0.191 | Improved (was 1.000) |
| `deviation` | 1.000 | 0.10 | 0.100 | **SATURATED** |
| `lane_change` | 1.000 | 0.05 | 0.050 | **SATURATED** |

---

## Root Cause Analysis

### `speed` — was saturated due to uniform OSM speed limit; partially fixed

**Initial state**: `reference_path.parquet` had `speed_limit_mps = 13.4` (30 mph) for
all 13,851 samples — the fallback default when OSM `maxspeed` tags were absent.

**Fix applied**: Queried Overpass API for all 71 OSM way IDs in the route, obtaining
real speed limits for 57/71 ways. Added 2 `motorway_link` ramps (45 mph) from highway
type inference. Results written to `config/speed_limits.yaml` (59 corridors total).

```
reference_path speed_limit_mps after fix:
  25 mph  (11.18 m/s)  :  2.9%  — local streets (West Johnson St, etc.)
  30 mph  (13.40 m/s)  : 14.2%  — untagged ways (service roads) + default
  35 mph  (15.65 m/s)  :  2.8%  — West Peace Street
  40 mph  (17.88 m/s)  :  2.5%  — South New Hope Road area
  45 mph  (20.12 m/s)  : 35.4%  — Capital Boulevard (dominant segment)
  55 mph  (24.59 m/s)  :  5.5%
  60 mph  (26.82 m/s)  : 30.4%  — I-440
  70 mph  (31.29 m/s)  :  6.4%  — I-87 / US-64 / US-264
```

**Remaining issue**: `speed` raw = 0.954 (not yet fully resolved).
Fraction of samples still exceeding limit + tolerance: **21.2 %**.
Mean excess where positive: **3.55 m/s**.
Mean squared excess: **5.29 m²/s²** (saturation = 4.0 m²/s²).

The driver appears to genuinely exceed posted limits on Capital Boulevard and I-440
segments, which accounts for residual penalty. The remaining 14 untagged `service`
ways retain the 30 mph default, which is appropriate for parking-lot/driveway geometry.

---

### `jerk` — saturated due to double-differentiation of noisy EKF velocity

`jerk_penalty()` computes `j = d²v/dt²` via two successive `np.gradient` calls with no
low-pass filter. Small velocity noise in the 100 Hz EKF output is amplified ~10,000×
by the second derivative.

```
actual j_lon (from fused_ekf):
  std    47.4 m/s³   (ideal std: 1.19 m/s³)
  min  -1150 m/s³
  max  +1043 m/s³    ← physically impossible; pure numerical artifact
  |j| > 3.0 m/s³:  57.1 % of samples

ideal j_lon_mps3 (from ideal_trajectory):
  p50  ≈ 0 m/s³
  p75    0.033 m/s³  → baseline is near-zero for 75 % of the trip
```

At 100 Hz, a 1 m/s velocity step between adjacent samples produces `a_lon = 100 m/s²`,
and then `j_lon = 10,000 m/s³` — far above the 3.0 m/s³ saturation constant.

Note: `harsh_brake = 0.0` despite `jerk` being saturated. This is consistent with the
diagnosis — `harsh_brake` applies a 3 Hz Butterworth LPF before detection, which
removes the same noise that inflates jerk. The driver did not brake harshly; the jerk
penalty is reacting to EKF velocity noise, not real jerk.

Fix: apply the same LPF to `a_lon` inside `jerk_penalty()` before computing the second
derivative (`src/scoring/components.py:152`).

---

### `deviation` and `lane_change` — likely amplified by reference_path geometry offset

Both penalties use `reference_path` centerline positions (`px_m`, `py_m`) as the
baseline. If the OSM geometry is offset from the actual road (common on multi-lane
roads), every sample will carry a systematic deviation, saturating both penalties.
Fixing the `reference_path` regeneration (same action as `speed`) is expected to
reduce these as well.

---

### `harsh_brake` and `lat_accel` — no issues

`harsh_brake = 0.0`: no deceleration event exceeded −3.5 m/s² for ≥ 0.3 s after
3 Hz low-pass filtering. The driver braked smoothly throughout the trip.

`lat_accel = 0.065`: lateral acceleration excess is only 6.5% of saturation
(4.0 m²/s⁴). Cornering behaviour is close to the ideal profile.

---

## Fix Log

| Priority | Component | Root cause | Status |
|---|---|---|---|
| P1 | `jerk` | Double-diff of noisy EKF velocity | **FIXED** — double LPF (3 Hz on `a_lon`, 1 Hz on `j_lon`) in `components.py` |
| P2 | `speed` | OSM speed limit uniform 30 mph fallback | **IMPROVED** — 59 corridors populated in `speed_limits.yaml`; raw 1.0 → 0.954 |
| P3 | `deviation` / `lane_change` | Arc-length mismatch between fused positions and `reference_path` | **OPEN** — requires nearest-point projection in scoring pipeline |

## Remaining Issues

### `deviation` and `lane_change` still saturated

Both use arc-length-based interpolation from `reference_path`:

```python
s_fused   = cumulative_arc_length(px_fused, py_fused)   # 0 .. 14,398 m
v_limit   = np.interp(s_fused, ref_s, ref_vl)           # ref_s: 0 .. 13,850 m
```

`s_fused` (14,398 m) > `ref_s` (13,850 m): the fused arc-length exceeds the reference
path length by 548 m. Values beyond `ref_s[-1]` are clamped to the last reference point,
misassigning positions near the trip end to the wrong road segment.

More fundamentally, the arc-length coordinate systems differ: `s_fused` is computed
from the (potentially drifting) EKF positions, while `ref_s` is from the OSM-matched
centerline. Unless both start and end at exactly the same locations with no drift,
the mapping will be systematically off.

**Proper fix**: replace arc-length interpolation with nearest-point projection
(find the closest `reference_path` row to each fused `(px_m, py_m)` in 2D) in
`speed_penalty()`, `deviation_penalty()`, and `lane_change_penalty()`.
