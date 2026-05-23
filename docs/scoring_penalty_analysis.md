# Scoring Penalty Analysis

Each of the six scoring components computes a penalty scalar in `[0, 1]`.
The aggregate score is `100 × (1 − weighted_sum)`.
All thresholds and saturation constants live in `config/scoring.yaml` and can be tuned without code changes.

---

## Component Summary

| Component | Weight | Source file |
|---|---|---|
| `jerk` | 0.30 | `src/scoring/components.py:114` |
| `harsh_brake` | 0.20 | `src/scoring/components.py:165` |
| `lat_accel` | 0.15 | `src/scoring/components.py:262` |
| `speed` | 0.20 | `src/scoring/components.py:326` |
| `deviation` | 0.10 | `src/scoring/components.py:387` |
| `lane_change` | 0.05 | `src/scoring/components.py:452` |

---

## 1. `jerk` — Longitudinal Jerk (weight 0.30)

### Algorithm

```
j_actual = d²v/dt²          (second derivative of fused v_mps)
excess   = max(0, |j_actual| − |j_ideal|)
penalty  = clip(mean_excess / 3.0 m/s³, 0, 1)
```

### Why a penalty fires

- The actual longitudinal jerk exceeds the ideal trajectory's jerk at that instant.
- No low-pass filter is applied before differentiation — impulse spikes from hard
  acceleration or braking land directly on the penalty.
- `j_ideal` is linearly interpolated from `ideal_trajectory.parquet` onto the fused
  time grid; a poorly tuned ideal profile makes the baseline too lenient or too strict.

**Saturation**: mean excess jerk of **3.0 m/s³** over the full trip maps to penalty = 1.

---

## 2. `harsh_brake` — Harsh Braking Events (weight 0.20)

### Algorithm

```
a     = LPF(dv/dt, cutoff=3 Hz, order=2)   # suppress road-vibration spikes
event = contiguous interval where a < −3.5 m/s²
        AND duration ≥ 0.3 s
        AND gap from previous event end ≥ 1.0 s (cooldown)
penalty = clip(events_per_minute / 2.0, 0, 1)
```

### Why a penalty fires

- The LPF-smoothed deceleration drops below **−3.5 m/s² (≈ 0.36 g)** for at least
  **0.3 seconds** after road vibration is removed.
- Typical causes: late braking before intersections, reaction to cut-ins, or
  aggressive speed scrubbing before corners.
- The 1 s cooldown prevents double-counting when the signal briefly bounces across
  the threshold within a single braking event.

**Saturation**: **2 events/minute** maps to penalty = 1.

---

## 3. `lat_accel` — Lateral Acceleration (weight 0.15)

### Algorithm

```
a_lat_actual = v × psi_dot          (centripetal, body frame)
a_lat_ideal  = interpolated from ideal_trajectory.parquet
excess = max(0, |a_lat_actual| − |a_lat_ideal|)
penalty = clip(mean_squared_excess / 4.0 m²/s⁴, 0, 1)
```

### Why a penalty fires

- The driver corners faster or turns more aggressively than the ideal profile allows.
- Because the excess is **squared before integrating**, a brief large spike (e.g.,
  5 mph too fast through a curve) counts far more than a prolonged small excess.
- High speed combined with high yaw rate is the most common cause.

**Saturation**: mean squared excess of **4.0 m²/s⁴** maps to penalty = 1.

---

## 4. `speed` — Speed Compliance (weight 0.20)

### Algorithm

```
s_fused   = cumulative arc-length from fused px_m, py_m
v_limit   = speed_limit_mps interpolated from reference_path at s_fused
excess    = max(0, v − (v_limit + 0.89 m/s))   # ±2 mph tolerance band
penalty   = clip(mean_squared_excess / 4.0 m²/s², 0, 1)
```

### Why a penalty fires

- The driver exceeds the posted speed limit by more than **0.89 m/s (≈ 2 mph)**.
- Excess is squared, so 5 mph over scores disproportionately worse than 1 mph over.
- Position accuracy of `fused_ekf.parquet` directly affects the arc-length mapping;
  GPS drift can shift the vehicle onto the wrong speed-limit segment.

**Saturation**: mean squared excess of **4.0 m²/s²** maps to penalty = 1.

---

## 5. `deviation` — Route Deviation (weight 0.10)

### Algorithm

```
dev    = Euclidean distance from fused position to reference_path centerline
excess = max(0, dev − 1.5 m)   # ±1.5 m in-lane free zone
penalty = clip(mean_excess / 3.0 m, 0, 1)
```

### Why a penalty fires

- The vehicle drifts more than **1.5 m** from the reference centerline and stays
  there (the excess is time-integrated, so brief excursions barely matter).
- Common causes: lane changes, roadside stops, or EKF position error accumulating
  over long straight segments.
- The reference centerline comes from `reference_path.parquet` (OSM-derived); if the
  OSM geometry does not match the actual road, false deviations appear.

**Saturation**: mean excess of **3.0 m** maps to penalty = 1.

---

## 6. `lane_change` — Abrupt Lane Changes (weight 0.05)

### Algorithm

```
yaw_change[i] = |ψ[i + win] − ψ[i]|        (2-second rolling window)
event start   = yaw_change > 0.15 rad
confirmation  = lateral displacement ≥ 2.0 m  (measured 2 s + 3 s after start)
               → distinguishes true lane changes from swerves that return
penalty = clip(lane_changes_per_minute / 2.0, 0, 1)
```

### Why a penalty fires

- Within a 2-second window the heading changes by more than **0.15 rad (≈ 8.6°)**
  AND the vehicle ends up at least **2.0 m laterally displaced** from its starting
  direction — confirming a lane change rather than a swerve.
- The cooldown advances to the full measurement window (`win + sus_win`) after each
  confirmed event, preventing the "unwind" of the same manoeuvre from double-counting.

**Saturation**: **2 events/minute** maps to penalty = 1.

---

## Cross-Cutting Risk Factors

| Root cause | Affected components |
|---|---|
| Poor EKF positional accuracy (GPS drift) | `deviation`, `speed`, `lane_change` |
| `ideal_trajectory.parquet` not calibrated for this trip | `jerk`, `lat_accel` |
| OSM `speed_limit_mps` mismatches actual posted limits | `speed` |
| LPF cutoff (3 Hz) too aggressive for the IMU sample rate | `harsh_brake` |
| `reference_path` centerline offset from actual road | `deviation` |

---

## Running the Scorer

```bash
python -m scoring --trace day2 --filter ekf
# Requires: out/day2/fused_ekf.parquet
#           out/day2/ideal_trajectory.parquet
#           out/day2/reference_path.parquet
# Output:   out/day2/score.json
```

The resulting `score.json` breaks down each component's `raw`, `weight`, and `weighted`
values, making it straightforward to see which component is responsible for the largest
penalty.
