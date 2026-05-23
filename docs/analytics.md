# Scoring Analytics — day2 (EKF)

Run date: 2026-05-23
Command: `PYTHONPATH=src py -3.10 -m scoring score --trace day2 --filter ekf --out-dir out`

---

## Score Summary

| Run | `score_0_100` | `aggregate_raw` | `jerk` raw | Note |
|---|---|---|---|---|
| Initial (2026-05-23) | 34.0 | 0.6597 | 1.000 | Before LPF fix |
| After jerk LPF fix | **53.4** | 0.4657 | **0.353** | `components.py` double-LPF |

Current result (after fix):

| Field | Value |
|---|---|
| `score_0_100` | **53.4 / 100** |
| `suggested_tip_band` | 0–59 ("Poor") |
| `suggested_tip_pct` | 10% |
| `fused_source` | ekf |
| `aggregate_raw` | 0.4657 |

---

## Component Breakdown (after jerk LPF fix)

| Component | raw | weight | weighted | Status |
|---|---|---|---|---|
| `jerk` | 0.353 | 0.30 | 0.106 | Fixed (was 1.000) |
| `harsh_brake` | 0.000 | 0.20 | 0.000 | OK |
| `lat_accel` | 0.065 | 0.15 | 0.010 | OK |
| `speed` | 1.000 | 0.20 | 0.200 | **SATURATED** |
| `deviation` | 1.000 | 0.10 | 0.100 | **SATURATED** |
| `lane_change` | 1.000 | 0.05 | 0.050 | **SATURATED** |

3 of 6 components remain saturated; `speed`, `deviation`, `lane_change` require
`reference_path` data fixes (P2/P3 below).

---

## Root Cause Analysis

### `speed` — saturated due to uniform OSM speed limit

`reference_path.parquet` has `speed_limit_mps = 13.4` (30 mph) for all 13,851 samples
(std ≈ 3.6e-15, i.e. exactly constant). This is the OSM fallback default applied when
real speed-limit tags are absent.

```
reference_path speed_limit_mps:
  count  13851   unique values: 1
  value  13.4 m/s  (≈ 30 mph) — entire 13.85 km route

fused_ekf v_mps:
  mean   15.3 m/s  (≈ 55 km/h)
  p50    18.2 m/s  (≈ 65 km/h)
  p75    22.5 m/s  (≈ 81 km/h)
  max    29.4 m/s  (≈ 106 km/h)

→ 59.6 % of samples exceed limit + tolerance (13.4 + 0.89 = 14.29 m/s)
```

Normal highway driving saturates the penalty immediately.
Fix: regenerate `reference_path` with real per-segment OSM speed tags, or override
in `config/ideal.yaml`.

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

## Proposed Fixes

| Priority | Component | Root cause | Fix |
|---|---|---|---|
| P1 | `jerk` | Double-diff of noisy EKF velocity | Apply 3 Hz LPF to `a_lon` in `jerk_penalty()` before computing `j_lon` (`components.py:152`) |
| P2 | `speed` | OSM speed limit defaults to 30 mph everywhere | Regenerate `reference_path` with real per-segment speed tags |
| P3 | `deviation` / `lane_change` | `reference_path` centerline offset | Improves automatically once `reference_path` is corrected |

The `jerk` fix is a one-line code change; the `speed` / `deviation` / `lane_change`
fixes require a data pipeline re-run.
