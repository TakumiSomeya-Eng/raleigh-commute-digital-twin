# Learning Notes: `src/scoring/`

**Project:** Raleigh Commute Digital Twin — *Uber vs. My AI*

**Module:** `src/scoring/` (Phase P4 — FR-10)

**Purpose of this document:** Post-completion study notes written to close the implementation-level understanding gap that emerged from AI-assisted development (Claude Code). This is the fifth in a planned series covering each module in the project.

---

## Overview

`scoring` computes a 0–100 driver quality score by comparing the actual EKF trajectory against the ideal driver trajectory across six penalty components. The result is written to `score.json` along with a suggested tip percentage.

### File map

| File | Role |
|---|---|
| `components.py` | Six penalty functions (FR-10.1–10.6), each returning [0, 1] |
| `aggregate.py` | Weighted combination → score_0_100, builds score.json |
| `tip_lookup.py` | Maps score to tip band from config/scoring.yaml |
| `__main__.py` | CLI entry point (`python -m scoring score`) |

### Execution order

```

fused_ekf.parquet
ideal_trajectory.parquet   →  components.py  (six raw penalties)
reference_path.parquet              ↓
config/scoring.yaml        →  aggregate.py   (weighted sum → score_0_100)
                                    ↓
                           →  tip_lookup.py  (tip band)
                                    ↓
                              score.json

```

---

## Design principles shared across all penalty functions

### Every function returns a scalar in [0, 1]

```

0.0 = no worse than the ideal driver
1.0 = worst possible (saturation value reached or exceeded)

```

### Saturation value

```python
penalty = clip(mean_excess / saturation_value, 0.0, 1.0)

```

The saturation value is the "ceiling" — when `mean_excess` reaches it, penalty becomes 1.0. Values above it are clipped. All saturation values live in `config/scoring.yaml`, so thresholds can be tuned without touching code.

### Time-averaging

Every penalty integrates over time and divides by trip duration:

```python
mean_excess = np.trapz(excess, t) / trip_duration

```

This ensures a 15-minute trip and a 5-minute trip with identical driving behavior receive the same penalty, regardless of how long the raw excess accumulates.

---

## Kinematic helper functions

### `_a_lon` — longitudinal acceleration

```python
def _a_lon(t, v):
    return np.gradient(v, t)   # dv/dt

```

`np.gradient` uses central differences for interior points and forward/backward differences at endpoints. Central differences are second-order accurate (error proportional to Δt²) versus first-order for one-sided differences.

### `_a_lat` — lateral acceleration

```python
def _a_lat(v, psi_dot):
    return v * psi_dot

```

From circular motion: `a_lat = v²/r = v × (v/r) = v × ψ̇`. Larger speed or tighter curve (larger ψ̇) both increase lateral G.

### `_j_lon` — longitudinal jerk

```python
def _j_lon(t, a):
    return np.gradient(a, t)   # da/dt

```

Rate of change of acceleration. Sudden braking produces large negative jerk. A smooth stop produces small jerk spread over time.

### `_interp_ideal`

```python
def _interp_ideal(ideal, t_query, col):
    t_ideal = ideal["t_s"].to_numpy(dtype=float)
    vals    = ideal[col].to_numpy(dtype=float)
    return np.interp(t_query, t_ideal, vals, left=vals[0], right=vals[-1])

```

`ideal_trajectory.parquet` is sampled at 10 Hz; `fused_ekf.parquet` at 100 Hz. Linear interpolation aligns the ideal values to the fused time grid before comparison. `left=vals[0], right=vals[-1]` clamps extrapolation at both ends rather than returning NaN or zero.

---

## FR-10.1 — Jerk penalty

### Purpose

Penalize longitudinal jerk that exceeds what the ideal driver produces. Smooth deceleration (low jerk) is expected at traffic lights and curves; only jerk *beyond* the ideal level is penalized.

### Algorithm step by step

**Step 1 — Compute actual jerk:**

```python
j_actual = _j_lon(t, _a_lon(t, v))

```

Two nested `np.gradient` calls: first `dv/dt` to get acceleration, then `da/dt` to get jerk.

**Step 2 — Interpolate ideal jerk:**

```python
j_ideal = _interp_ideal(ideal, t, "j_lon_mps3")

```

The ideal trajectory already has `j_lon_mps3` precomputed by `quintic.py`.

**Step 3 — Compute excess:**

```python
excess = np.maximum(0.0, np.abs(j_actual) - np.abs(j_ideal))

```

Absolute values are taken before subtracting so that both braking jerk (negative) and acceleration jerk (positive) are treated symmetrically. Negative excess (actual less than ideal) is clipped to zero — being *smoother* than ideal is not penalized.

**Step 4 — Time-average and normalize:**

```python
mean_excess = np.trapz(excess, t) / trip_duration
return np.clip(mean_excess / jerk_sat, 0.0, 1.0)

```

`jerk_sat = 3.0 m/s³` (default). A trip with mean excess jerk of 3 m/s³ receives penalty 1.0.

---

## FR-10.2 — Harsh braking penalty

### Purpose

Count discrete harsh braking events rather than integrating a continuous signal. Counts events per minute to normalize for trip length.

### Algorithm step by step

**Step 1 — Generate braking flag:**

```python
a = _a_lon(t, v)
is_braking = (a < -thresh).astype(np.int8)   # thresh = 3.5 m/s²

```

Each sample is 1 if decelerating harder than 3.5 m/s², 0 otherwise.

**Step 2 — Edge detection with padding:**

```python
padded = np.concatenate([[0], is_braking, [0]])
diff   = np.diff(padded.astype(np.int16))

start_indices = np.where(diff == 1)[0]    # 0→1 transitions
end_indices   = np.where(diff == -1)[0]   # 1→0 transitions

```

Why padding: if `is_braking` starts or ends with 1, the `np.diff` without padding would miss that boundary transition. Prepending and appending 0 guarantees every event has both a start and an end transition.

Why `np.int16`: `np.diff` on `np.int8` can produce values of -1 that overflow the `int8` range in edge cases. Casting to `int16` before differencing avoids this.

**Index alignment:** `diff[i] = padded[i+1] - padded[i]`. Since `padded[i+1] = is_braking[i]` (the prepended zero shifts indices by 1), `diff[i] == 1` means `is_braking[i]` is where the event starts. The diff index and the original `is_braking` index are therefore aligned — the one-position shift from prepending cancels the one-position offset from the diff formula.

**Step 3 — Duration filter:**

```python
for s, e in zip(start_indices, end_indices):
    duration = t[min(e, len(t)-1)] - t[s]
    if duration >= min_dur:   # 0.3 s
        events += 1

```

`min(e, len(t)-1)` guards against `end_indices` pointing one past the end of the array when an event reaches the last sample.

Events shorter than 0.3 s are discarded as noise (e.g. pothole impacts).

**Step 4 — Rate and normalization:**

```python
rate_epm = events / max(trip_duration / 60.0, 1e-9)
return np.clip(rate_epm / sat_epm, 0.0, 1.0)

```

`sat_epm = 2.0`. A trip with 2 harsh brakes per minute receives penalty 1.0. Day2 recorded 17 events over 14.8 minutes → rate ≈ 1.15 epm → penalty ≈ 0.575.

---

## FR-10.3 — Lateral acceleration penalty

### Purpose

Penalize cornering G-force that exceeds what the ideal driver would produce on the same curve.

### Why squared excess

```python
excess  = np.maximum(0.0, np.abs(a_lat_actual) - np.abs(a_lat_ideal))
mean_sq = np.trapz(excess**2, t) / trip_duration

```

Lateral discomfort is nonlinear. Doubling the excess lateral G feels more than twice as bad. Squaring the excess captures this: 2 m/s² excess produces 4× the penalty of 1 m/s² excess.

This is different from the jerk penalty which uses linear excess. Jerk's contribution to discomfort is more proportional, whereas lateral G has a stronger perceptual nonlinearity.

---

## FR-10.4 — Speed compliance penalty

### Purpose

Penalize driving above the posted speed limit. A small tolerance band accounts for GPS velocity measurement error and the natural tendency to be slightly above the limit when passing a sign.

### Arc-length alignment

`fused_ekf.parquet` is time-based; `reference_path.parquet` is arc-length-based (1 m grid). They cannot be aligned by time directly. Instead the fused positions are converted to cumulative arc-length:

```python
def _cumulative_arc_length(px, py):
    ds = np.sqrt(np.diff(px)**2 + np.diff(py)**2)
    return np.concatenate([[0.0], np.cumsum(ds)])

```

`np.cumsum` produces the running total of segment lengths: `[d1, d1+d2, d1+d2+d3, ...]`. This gives each EKF sample a distance-from-start value that can be looked up in `reference_path.s_m`.

Then the speed limit at each EKF sample is obtained by linear interpolation:

```python
v_limit = np.interp(s_fused, ref_s, ref_vl, left=ref_vl[0], right=ref_vl[-1])

```

### Tolerance and squared excess

```python
tol = 0.89   # m/s ≈ 2 mph
excess = np.maximum(0.0, v - (v_limit + tol))
mean_sq = np.trapz(excess**2, t) / trip_duration

```

Speeds within 2 mph of the limit are free. Excess is squared for the same nonlinearity reason as lateral acceleration: 5 mph over is much worse than 1 mph over.

---

## FR-10.5 — Route deviation penalty

### Purpose

Penalize lateral distance from the road centerline. Small deviations (within a lane) are ignored; sustained deviation beyond 1.5 m is penalized linearly.

### Centerline lookup

```python
s_fused     = _cumulative_arc_length(px_f, py_f)
ref_px_at_s = np.interp(s_fused, ref_s, ref_px, ...)
ref_py_at_s = np.interp(s_fused, ref_s, ref_py, ...)

```

"At arc-length s metres along the route, where should the vehicle be?" The reference path provides the answer; `np.interp` retrieves it for each EKF sample.

### Distance and in-lane dead zone

```python
dev    = np.sqrt((px_f - ref_px_at_s)**2 + (py_f - ref_py_at_s)**2)
excess = np.maximum(0.0, dev - inlane_m)   # inlane_m = 1.5 m

```

Deviations under 1.5 m produce zero excess. 1.5 m is approximately half of a standard US lane width (≈ 3.5 m), so normal within-lane driving never incurs a penalty.

---

## FR-10.6 — Lane change penalty

### Purpose

Detect and count lane changes by combining two conditions: a heading excursion over a 2-second window AND sustained lateral displacement afterward. This distinguishes true lane changes from swerves (heading change that reverts) and normal cornering.

### Step 1 — Unwrap heading

```python
psi = np.unwrap(fused["psi_rad"].to_numpy(dtype=float))

```

`np.unwrap` removes ±π discontinuities. Without it, a heading transition from 3.14 to -3.14 rad would appear as a 6.28 rad change instead of ≈ 0, producing false positive detections.

### Step 2 — Rolling yaw change over 2-second window

```python
win        = int(yaw_window_s / dt_mean)        # 2 s / 0.01 s = 200 samples
yaw_change = np.abs(psi[win:] - psi[:-win])     # length n - win
is_excursion = yaw_change > yaw_delta            # 0.15 rad ≈ 8.6°

```

`yaw_change[i] = |ψ[i+200] - ψ[i]|` is the total heading change over the next 2 seconds starting from sample i. If it exceeds 0.15 rad, that sample is flagged as an excursion.

### Step 3 — Edge detection (same as FR-10.2)

The same pad-then-diff pattern detects contiguous excursion intervals.

### Step 4 — Lateral displacement check

```python
check_idx = min(n-1, s + win + sus_win)   # 5 s after event start

psi_pre = psi[max(0, s-1)]
perp_x  = -np.sin(psi_pre)    # perpendicular-left unit vector
perp_y  =  np.cos(psi_pre)

dx  = px[check_idx] - px[s]
dy  = py[check_idx] - py[s]
lat = abs(dx * perp_x + dy * perp_y)   # lateral component of displacement

if lat >= lat_disp_m:   # 2.0 m
    lane_changes += 1

```

**Why the perpendicular vector `(-sin ψ, cos ψ)`:** The heading direction is `(cos ψ, sin ψ)`. The vector perpendicular to this (pointing left) is `(-sin ψ, cos ψ)`. Taking the dot product of the displacement vector with this perpendicular extracts only the lateral component, ignoring how far the vehicle traveled forward.

**Why check 5 seconds later (`win + sus_win`):** A swerve (lane departure and return) will show a heading excursion but the lateral displacement will be small by `check_idx` because the vehicle has returned to its original lane. A true lane change will still show ≥ 2 m lateral displacement 5 seconds after the maneuver began.

### Step 5 — Cooldown

```python
next_allowed = 0

for s, _e in zip(event_starts, event_ends):
    if s < next_allowed:
        continue
    next_allowed = s + win + 1          # advance past yaw window

    ...

    if lat >= lat_disp_m:
        lane_changes += 1
        next_allowed = check_idx        # extend cooldown to full window

```

Without cooldown, the "unwind" of a lane change (heading returning toward the new lane's direction) would be detected as a second excursion. The cooldown skips events that start before `next_allowed`.

---

## `aggregate.py` — weighted combination

### `compute_aggregate`

```python
for name in _COMPONENT_NAMES:
    raw      = float(raw_penalties.get(name, 0.0))
    w        = float(weights.get(name, 0.0))
    weighted = raw * w
    aggregate += weighted

```

The canonical component order in `_COMPONENT_NAMES` is used to iterate so that the output JSON always has components in the same order regardless of dict insertion order.

### Score conversion

```python
score_0_100 = round(100.0 * (1.0 - aggregate_raw), 4)

```

```

aggregate_raw = 0.0  →  score = 100  (perfect)
aggregate_raw = 0.5  →  score = 50
aggregate_raw = 1.0  →  score = 0    (worst)

```

### Config hash

```python
def _config_hash(scoring_yaml, ideal_yaml):
    h = hashlib.sha256()
    for p in filter(None, [scoring_yaml, ideal_yaml]):
        if Path(p).exists():
            h.update(Path(p).read_bytes())
    return f"sha256:{h.hexdigest()}"

```

SHA-256 of the scoring and ideal YAML files concatenated. Embedded in `score.json` so that any future score can be traced back to the exact config that produced it. If weights or thresholds change, the hash changes, signaling that historical scores need recomputation.

`filter(None, [...])` removes any `None` entries before iterating, preventing `Path(None)` from raising a `TypeError` when `ideal_yaml` is not provided.

---

## `tip_lookup.py` — tip band mapping

### First-match semantics

```python
for band in bands:
    lo = float(band["min_score"])
    hi = float(band["max_score"])
    if lo <= score <= hi:
        return {...}

```

Bands are checked from top to bottom; the first match wins. If bands overlap in the YAML, the one defined earlier takes precedence. This makes the lookup order-dependent — a deliberate design choice that gives the YAML author full control over priority.

### Fallback

```python
last = bands[-1]

```

If no band matches (e.g. score is slightly outside all ranges due to floating-point), the last band (lowest tier) is returned. This prevents the function from ever returning `None` or raising an uncaught exception.

### `cfg.get("tip_bands", [])` and the ValueError

```python
bands = cfg.get("tip_bands", [])
if not bands:
    raise ValueError(f"No tip_bands found in {path}")

```

An empty list is falsy in Python (`if not []` is `True`). Raising immediately here rather than silently returning a default prevents a misconfigured YAML from producing a misleading score.json with no tip information.

### The `notes` field

```

"notes": "SUGGESTED — final tipping decision is manual."

```

Every return value carries this disclaimer. The scoring system provides a data-driven suggestion; the human rider makes the final decision. This is architecturally significant: the system never claims authority over the tip amount.

---

## `__main__.py` — CLI entry point

### `sys.argv` rewriting

```python
sub = sys.argv[1]
sys.argv = [f"scoring {sub}"] + sys.argv[2:]

```

Before calling `aggregate.main()`, `sys.argv` is rewritten so that `argparse` inside `aggregate.main()` sees only the remaining arguments (`--trace`, `--filter`, etc.) without the subcommand name. Without this rewrite, `argparse` would try to parse `"score"` as one of its own arguments and fail.

---

## Day2 actual score breakdown

```

Component       raw    weight  weighted
jerk            0.35   0.20    0.070
harsh_brake     0.70   0.20    0.140   ← 17 events / 14.8 min = 1.15 epm
lat_accel       0.25   0.20    0.050
speed           0.10   0.15    0.015
deviation       0.05   0.15    0.008
lane_change     0.08   0.10    0.008
─────────────────────────────────────
aggregate_raw              =   0.291  (approximate)
score_0_100    = 100 × (1 − 0.652) = 34.8
tip_band       = "0-44"  →  0% tip  (Unsafe)

```

The harsh braking component contributes the most due to 17 detected events. At 1.15 events per minute against a saturation of 2.0 epm, it produces a raw penalty of 0.575, which after weighting at 0.20 contributes 0.115 to the aggregate.

---

## Key concepts encountered for the first time

**Saturation value:** The "ceiling" input that maps to penalty 1.0. Values above it are clipped. Saturation values live in YAML so they can be tuned without code changes. Choosing the right saturation is a calibration problem: set it too low and every trip scores poorly; too high and bad trips look acceptable.

**Edge detection with padding:** The pad-then-diff pattern (`np.concatenate([[0], arr, [0]])` followed by `np.diff`) converts a boolean array into event start/end indices. Used for both harsh braking (FR-10.2) and lane change (FR-10.6). The prepended zero ensures boundary events are captured; the appended zero ensures every event has a closing transition.

**`np.int16` cast before `np.diff`:** `np.diff` on `np.int8` arrays can produce overflow when the difference is -1 (since `int8` range is -128 to 127, the subtraction itself is fine, but result interpretation can vary). Casting to `int16` before differencing is a defensive practice.

**Linear vs. squared penalty:** Jerk uses linear excess (`mean_excess`). Lateral acceleration and speed use squared excess (`mean_sq`). The choice reflects the perceptual nonlinearity: lateral G and speed violations have a much steeper discomfort curve at higher magnitudes than jerk does.

**Perpendicular vector for lateral displacement:** To measure how far a vehicle has moved sideways, project the displacement vector onto the direction perpendicular to the heading. For heading ψ, the forward direction is `(cos ψ, sin ψ)` and the left-perpendicular is `(-sin ψ, cos ψ)`. The dot product of the displacement with this perpendicular gives the signed lateral displacement.

**Two-condition lane change detection:** Heading excursion alone (FR-10.6 step 2) detects any curved motion including normal cornering. The second condition — sustained lateral displacement ≥ 2 m after 5 seconds — distinguishes true lane changes from curves and swerves that return to the original lane.

**SHA-256 config hash:** Hashing the YAML config files and embedding the hash in the output JSON creates a provenance trail. If parameters change, the hash changes, making it immediately visible that old and new scores are not comparable.

**`sys.argv` rewriting:** When a CLI module dispatches to a sub-module's `main()`, `sys.argv` must be rewritten to remove the subcommand token that the outer parser consumed. Without this, the inner `argparse` would see an unexpected positional argument.

---

## What I would do differently knowing this now

1. **Read `config/scoring.yaml` before reading `components.py`.** Every penalty function's behavior is controlled by `thresholds` and `saturation` sections in the YAML. Understanding those values first (what they mean physically, not just numerically) makes each function's logic immediately interpretable.

2. **Recognize the arc-length alignment problem early.** `fused_ekf.parquet` is time-based; `reference_path.parquet` is arc-length-based. Both `speed_penalty` and `deviation_penalty` silently convert fused positions to arc-length via `_cumulative_arc_length` before any comparison. Missing this alignment step would make it impossible to understand why these two functions use `px_m` and `py_m` as inputs rather than just `t_s`.

3. **Understand the edge detection pattern from FR-10.2 first, then apply it to FR-10.6.** The pad-then-diff pattern appears in both functions. Reading FR-10.2 (simpler, single threshold) before FR-10.6 (two-condition check with cooldown) builds the right mental model.

4. **Trace the saturation values to physical units.** `jerk_sat = 3.0 m/s³` means "a trip with mean excess jerk of 3 m/s³ scores 0." Anchoring each saturation to a real-world scenario (how many harsh brakes per minute is truly unacceptable?) clarifies the scoring model's intent and makes it easier to evaluate whether the calibration is reasonable.

---

## Next in this series

- `src/reporting/` — Jinja2 + Folium HTML report generation
