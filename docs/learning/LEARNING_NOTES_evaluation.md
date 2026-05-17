# Learning Notes: `src/evaluation/`

**Project:** Raleigh Commute Digital Twin — *Uber vs. My AI*
**Module:** `src/evaluation/` (Phase P3 — FR-6)
**Purpose of this document:** Post-completion study notes written to close the implementation-level understanding gap that emerged from AI-assisted development (Claude Code). This is the third in a planned series covering each module in the project.

---

## Overview

`evaluation` measures how well the EKF and UKF performed. It cannot simply compare filter output to GPS, because GPS itself is noisy (~3–20 m) and only updates at ~1 Hz. Instead it uses a Rauch-Tung-Striebel (RTS) smoother to produce a higher-quality reference trajectory ("soft ground truth"), then measures RMSE and NEES against that reference.

### File map

| File | Role | Make target |
|---|---|---|
| `rts_smoother.py` | RTS smoother → `ground_truth.parquet` | `make smooth` |
| `rmse.py` | RMSE harness + S1 gate → `rmse_report_{filter}.json` | `make eval` |
| `nees.py` | NEES and GPS innovation statistics (called by rmse.py) | — |
| `comparator.py` | EKF vs UKF per-segment comparison → `filter_comparison.json` | `make compare` |
| `odom_to_parquet.py` | Bridge: `/fused/odom` MCAP → Parquet (delegates to bag_bridge) | — |

### Execution order

```

make smooth  →  rts_smoother.py   →  ground_truth.parquet
make eval    →  rmse.py           →  rmse_report_{filter}.json
                 └── nees.py      →  (embedded in rmse_report)
make compare →  comparator.py    →  filter_comparison.json

```

---

## Why a soft ground truth is needed

The EKF output cannot be evaluated against raw GPS directly for two reasons.

First, GPS accuracy is ~3–20 m and updates at ~1 Hz, making it too noisy to serve as a reliable reference. Second, the EKF already uses GPS as a measurement input, so comparing against raw GPS would be circular — the EKF can never do worse than GPS on the very measurements it ingests.

The RTS smoother solves both problems. It runs the same EKF forward pass but stores every intermediate result, then runs a backward pass that incorporates future GPS fixes to refine past estimates. The result is a trajectory that is smoother and more accurate than what any real-time filter can produce, making it a fair reference for evaluation.

---

## `rts_smoother.py` — generating soft ground truth

### The two-pass structure

**Forward pass (`_forward()`):** Identical to the online EKF. Runs from t=0 to t=T, updating state x and covariance P at each tick. Crucially, five arrays are saved at every timestep:

```

xs[i]  : state after update      (used as starting point in backward pass)
Ps[i]  : covariance after update (used to compute smoother gain G)
xp[i]  : state after predict, before update  (used to compute correction amount)
Pp[i]  : covariance after predict, before update (used in G denominator)
Fs[i]  : Jacobian F at this step (used for reverse propagation in backward pass)

```

Storing all five for 88,000 rows costs approximately 17 MB of memory — the main resource cost of RTS over a standard EKF.

**Backward pass (`_backward()`):** Runs from t=T-2 back to t=0, modifying each timestep using the already-corrected result from t+1.

```python
G = Ps[i] @ Fs[i+1].T @ inv(Pp[i+1])
x_s[i] = xs[i] + G @ (x_s[i+1] - xp[i+1])
P_s[i] = Ps[i] + G @ (P_s[i+1] - Pp[i+1]) @ G.T

```

Why start at T-2 and not T-1: the backward pass uses index i+1, so the last timestep has nothing ahead of it and keeps its forward-pass value unchanged.

### The CTRV motion model (`_ctrv_predict`, `_ctrv_jacobian`)

The RTS smoother uses the same CTRV (Constant Turn Rate and Velocity) model as the EKF nodes, reimplemented in Python.

**`_ctrv_predict(x, dt)`** has two cases:

Straight-line case (`|psi_dot| < 1e-6`):

```

px_new = px + v * cos(psi) * dt
py_new = py + v * sin(psi) * dt

```

Turning case (`|psi_dot| >= 1e-6`):

```

px_new = px + (v / psi_dot) * (sin(psi + psi_dot*dt) - sin(psi))
py_new = py + (v / psi_dot) * (-cos(psi + psi_dot*dt) + cos(psi))

```

The turning formula comes from integrating circular arc motion:

```

∫ v * cos(psi + psi_dot * t) dt = (v / psi_dot) * sin(psi + psi_dot * t) + C

```

The `1/psi_dot` factor arises from the chain rule in reverse: differentiating `sin(psi + psi_dot*t)` produces `cos(...) * psi_dot`, so integrating divides by `psi_dot`.

The threshold 1e-6 prevents division by near-zero psi_dot. The straight-line formula is the Taylor-series limit of the turning formula as psi_dot → 0.

**Heading normalization** after each predict:

```python
x[3] = (x[3] + math.pi) % (2 * math.pi) - math.pi

```

`std.remainder` (C++) and this Python idiom both map psi to `[-π, π]`. This is essential because without it repeated turns cause psi to grow unboundedly, eventually losing floating-point precision and breaking the angular innovation calculations.

**`_ctrv_jacobian(x, dt)`** is the 5×5 Jacobian F = ∂(predict)/∂x. It is needed in the forward pass to propagate P: `P_new = F @ P @ F.T + Q`. Each element is a partial derivative: for example, `F[0,2] = cos(psi)*dt` means "if v increases by 1, px_new increases by cos(psi)*dt".

### Process noise Q (`_process_noise`)

```python
G_noise = np.zeros((5, 2))
G_noise[2, 0] = 1.0   # acceleration noise → v
G_noise[4, 1] = 1.0   # yaw-rate noise → psi_dot

Qc = np.diag([sigma_a**2, sigma_psi_dot**2])
Q = G_noise @ Qc @ G_noise.T * dt

```

**Why `G_noise @ Qc @ G_noise.T`:** This is the covariance transformation formula. If w is a random noise vector with covariance Qc, then the covariance of `G_noise @ w` is `G_noise @ Qc @ G_noise.T`. G_noise maps the two noise sources (acceleration, yaw-rate) into the 5-dimensional state space.

**Why G_noise elements are 0 or 1:** G_noise encodes which noise source affects which state variable. Values of exactly 1 mean "this noise source affects this variable at full strength." Using 0 or 1 keeps the noise magnitude entirely in Qc (which stores variances σ²), avoiding double-scaling. If G_noise had a value of 2, the resulting variance would be 4σ² — which would be correct only if the physical relationship truly amplified the noise by a factor of 2.

**Why `* dt`:** The van Loan method discretizes continuous-time noise into dt-second intervals. Intuitively: the uncertainty accumulated over dt seconds is proportional to dt. With dt=0.01s and σa=1.0 m/s²: `Q(v,v) = 1.0 * 0.01 = 0.01 m²/s²`, meaning speed can drift by at most ±0.1 m/s in one step.

### Smoother gain G

```python
G = Ps[i] @ Fs[i+1].T @ inv(Pp[i+1])

```

G is a 5×5 matrix that determines how much of the future correction to propagate back to the current timestep.

**Three factors:**

`Ps[i]` — current uncertainty. Large Ps[i] means the forward estimate is poor → trust the future correction more → G grows.

`Fs[i+1].T` — backward propagation direction. `Fs[i+1]` is the Jacobian from i to i+1 (forward direction). Its transpose reverses the direction, propagating i+1 corrections back to i.

`inv(Pp[i+1])` — reliability of future prediction. Large Pp[i+1] means the i+1 prediction was uncertain → the correction amount is less reliable → G shrinks (because inv makes large values small).

**Intuitive summary:**

```

"If the current estimate is uncertain AND the future prediction was reliable,
 propagate the future correction strongly back to the current timestep."

```

**Comparison with Kalman gain K:**

```

Kalman gain K: balances prediction uncertainty vs measurement noise
               → how much to trust the current measurement

Smoother gain G: balances current uncertainty vs future prediction uncertainty
               → how much to propagate the future correction backward

```

Both are automatic optimal weighting mechanisms derived from the same Bayesian framework.

### State correction

```python
x_s[i] = xs[i] + G @ (x_s[i+1] - xp[i+1])

```

`x_s[i+1] - xp[i+1]` is the correction applied at i+1: how much the already-smoothed i+1 state differs from what the forward pass predicted before seeing the GPS at i+1. G weights this correction and adds it to the forward-pass estimate at i.

Concrete example:

```

Forward pass:
  xp[4] = 101 m  (predicted position at t=4, before GPS)
  xs[4] = 103 m  (after GPS update: GPS moved it +2 m)
  xs[3] = 100 m  (no GPS at t=3)

Backward pass at i=3:
  x_s[4] = 103 m  (already smoothed)
  correction = x_s[4] - xp[4] = 103 - 101 = 2 m
  G = 0.8

  x_s[3] = 100 + 0.8 * 2 = 101.6 m
  "The GPS at t=4 shifted t=3's estimate by 1.6 m"

```

### Covariance correction

```python
P_s[i] = Ps[i] + G @ (P_s[i+1] - Pp[i+1]) @ G.T

```

`P_s[i+1] - Pp[i+1]` is always a negative semi-definite matrix (GPS updates shrink P, so P_s[i+1] < Pp[i+1]). Adding a negative matrix to Ps[i] shrinks the uncertainty — incorporating future information reduces past uncertainty.

### Singular matrix and `LinAlgError`

A matrix is singular when its inverse does not exist — the matrix determinant is zero. Geometrically, this means the transformation collapses some dimension to zero, destroying information that cannot be recovered by inversion.

In this code, `inv(Pp[i+1])` can fail when:

1. GPS outage is long → Pp grows extremely large → floating-point errors destroy the matrix's symmetry → it becomes numerically singular.
2. Timesteps before `first_gps` → Pp was never properly initialized and may be zero.

`continue` skips the correction for that timestep. The forward-pass value remains unchanged at those rows.

### Timestamp handling

```python
if "time_ns" in df.columns:
    t_out = df.time_ns.to_numpy(dtype=float) / 1e9

```

`fused_ekf.parquet` uses absolute UTC epoch seconds (from ROS 2 Odometry header stamps). `aligned_100hz.parquet` uses relative seconds starting from 0 after warm-up drop. If relative seconds were used for ground_truth.parquet, `np.interp(t_gt, t_f, ...)` in rmse.py would interpolate across incompatible time axes, producing meaningless RMSE values.

### GPS bearing initialization

```python
psi0 = math.radians(float(row0.gps_bearing_deg)) \
       if float(row0.gps_speed_mps) > 2.0 else 0.0

```

GPS bearing is computed from the Doppler effect: the GPS receiver measures the frequency shift of signals from multiple satellites, derives east-velocity and north-velocity components, then computes `atan2(vE, vN)`. At low speed, the velocity signal is buried in noise, making bearing unreliable. The 2 m/s threshold ensures the Doppler signal is large enough relative to measurement noise.

The initial P(psi,psi) = π² is set deliberately large so that even if psi0 is wrong, the first few gyroscope updates correct it rapidly.

---

## `rmse.py` — RMSE harness and S1 gate

### S1 gate

```python
_S1_THRESHOLD = 0.75
s1_pass = overall_rmse < _S1_THRESHOLD * gps_only_rmse

```

The PRD requirement: EKF RMSE must be less than 75% of GPS-only RMSE. This ensures the filter provides at least 25% accuracy improvement over raw GPS. Exit code 4 (`GATE_FAILURE`) if the gate fails.

### GPS-only baseline

```python
px_gps = np.interp(t_gt, t_al, aligned.px_m)
gps_only_rmse = _horizontal_rmse(px_gps, py_gps, px_gt, py_gt)

```

`aligned_100hz.parquet`'s `px_m` and `py_m` are the GPS positions upsampled to 100 Hz via `np.interp`. This represents "what would happen if we just used GPS without any filtering." The EKF must beat this baseline by 25%.

### Horizontal RMSE

```python
def _horizontal_rmse(px_est, py_est, px_ref, py_ref):
    err2 = (px_est - px_ref)**2 + (py_est - py_ref)**2
    return float(np.sqrt(np.mean(err2)))

```

Two-dimensional (East + North) RMSE. The vertical dimension is excluded because the model is 2D (no altitude). Each error is the Euclidean distance between the estimated and reference position at that timestep.

### Time interpolation

```python
px_f, py_f, mask = _interp_to_gt(fused, gt)

```

`fused_ekf.parquet` and `ground_truth.parquet` have the same time axis (absolute UTC epoch), but may not have identical timestamps due to ROS message timing. `np.interp` aligns them to the ground truth time grid before computing RMSE.

---

## `nees.py` — filter consistency check

### What NEES measures

NEES (Normalized Innovation Squared) checks whether the filter's self-reported uncertainty P is calibrated correctly against its actual errors.

```

NEES_i = dpx_i² / cov_xx_i + dpy_i² / cov_yy_i

```

When the filter is working correctly, NEES follows a chi-squared distribution whose mean equals the degrees of freedom (2 for the 2D position sub-state).

```

NEES mean << 2  → P is too large (over-conservative filter)
NEES mean ≈  2  → P is well-calibrated
NEES mean >> 2  → P is too small (over-confident, possibly diverging)

```

### 95% confidence interval

```python
nu_n = 2 * n_confident           # chi2 DOF = DOF × N samples
ci_lo = chi2.ppf(0.025, nu_n) / n_confident
ci_hi = chi2.ppf(0.975, nu_n) / n_confident
consistent = ci_lo <= nees_mean <= ci_hi

```

The sum of N independent NEES values follows chi2(2N). Dividing by N gives the distribution of the sample mean. If the measured NEES mean falls inside the 95% CI, the filter is declared consistent.

### Why filter only "GPS-anchored" rows

```python
_MAX_COV_M2 = 25.0   # 5 m standard deviation

confident = (cov_xx <= _MAX_COV_M2) & (cov_yy <= _MAX_COV_M2)

```

During GPS outages, P grows large by design — the filter is correctly expressing that it does not know where the vehicle is. NEES computed during these periods reflects "GPS outage behavior," not filter quality. Restricting to rows where the position standard deviation is ≤ 5 m isolates the periods where the filter is GPS-anchored and NEES is meaningful.

### GPS rejection statistics

```python
mahal2 = innov_x**2 / s_xx + innov_y**2 / s_yy
n_rejected = int((mahal2 > _CHI2_GATE_99).sum())

```

Post-hoc reconstruction of which GPS fixes would have been rejected by the chi-squared gate (threshold 9.21 for 2 DOF at 99% confidence). This uses the fused covariance at each GPS fix time to form the approximate innovation covariance S. The rejection rate appears in the rmse_report JSON.

---

## `comparator.py` — EKF vs UKF per-segment comparison

### Curvature segmentation

```python
_TURN_THRESHOLD = 0.05   # rad/s

is_turning  = np.abs(psi_dot_gt) > _TURN_THRESHOLD
is_straight = ~is_turning

```

Each timestep is labeled straight or turning using the RTS smoother's `psi_dot_rps` — not the EKF's psi_dot. The smoother's value is used because it incorporates future gyroscope data and is less noisy than the real-time filter output.

0.05 rad/s ≈ 2.9°/s. A vehicle turning a 100 m radius circle at 36 km/h has psi_dot = 10/100 = 0.1 rad/s > threshold → classified as turning. A gentle highway curve would be < 0.05 rad/s → straight.

### Equivalence threshold

```python
_EQUIV_THRESHOLD = 0.3   # metres

def _winner(rmse_ekf, rmse_ukf):
    delta = rmse_ekf - rmse_ukf
    if abs(delta) < _EQUIV_THRESHOLD:
        return f"equivalent (|delta| < 0.3 m)"
    return "ekf" if delta < 0 else "ukf"

```

A difference smaller than 0.3 m is declared equivalent. This threshold is set at roughly the minimum GPS accuracy for typical urban conditions — below this, the difference is not practically meaningful.

### Expected result

On straight roads, EKF and UKF are expected to be equivalent because the CTRV prediction is nearly linear (small heading change → Jacobian is a good approximation). On curves, UKF is expected to win because it avoids the linearization error of the Jacobian.

---

## Key concepts encountered for the first time

**Singular matrix:** A matrix whose determinant is zero and whose inverse does not exist. Geometrically, it collapses at least one dimension to zero, making the transformation irreversible. In this code it arises when Pp grows too large during GPS outages and loses numerical symmetry, or when Pp is uninitialized (zero matrix) for pre-GPS timesteps.

**RTS vs online EKF:** The online EKF uses only past data at each timestep. The RTS smoother uses all data — past and future — making it strictly more accurate. This is why RTS output serves as "ground truth": it is the best possible estimate given the available sensors, obtained retrospectively.

**NEES (Normalized Innovation Squared):** A statistical test for filter consistency. It answers "does the filter's uncertainty estimate P match its actual errors?" A well-calibrated filter produces NEES ≈ DOF. Systematic deviation indicates P is mis-tuned.

**`Pp` vs `Ps`:** Pp is the covariance after the predict step (before any measurement update). Ps is the covariance after the update step. Pp ≥ Ps always (prediction grows uncertainty; updates shrink it). The RTS smoother needs both because the smoother gain G uses Pp[i+1] to assess the reliability of the future prediction, not the post-update Ps[i+1].

**`-> np.ndarray` type hint:** Python type annotation meaning "this function returns a numpy array." Not enforced at runtime, but enables static type checking with mypy and IDE auto-completion. Does not affect behavior.

**GPS bearing from Doppler:** GPS receivers compute heading by measuring the Doppler frequency shift from multiple satellites, deriving east and north velocity components, then computing `atan2(vE, vN)`. At low speed, the velocity signal is too small relative to noise, making bearing unreliable — hence the 2 m/s threshold before trusting it.

**`@` operator:** Matrix multiplication in Python (PEP 465, Python 3.5+). `A @ B` is equivalent to `np.dot(A, B)` but reads closer to mathematical notation. Distinct from `*` which performs element-wise multiplication.

**Van Loan method:** A technique to discretize continuous-time process noise into a dt-second Q matrix. The formula `G @ Qc @ G.T * dt` maps noise source variances (in Qc) through the coupling matrix G into the state space, then scales by dt. G elements are 0 or 1 to avoid scaling the noise magnitude — the magnitude lives entirely in Qc.

---

## What I would do differently knowing this now

1. **Understand Pp vs Ps before reading the backward pass.** The distinction between predicted covariance (Pp, before GPS update) and updated covariance (Ps, after GPS update) is the key to understanding why the smoother gain G has the form it does. Without this distinction, G's three factors are opaque.

2. **Read `_forward()` and `_ctrv_predict()` together.** The forward pass is just an EKF — but it saves five arrays at every step. Understanding *why* each array is saved (which part of the backward pass uses it) makes the overall structure clear immediately.

3. **Trace the time axis issue early.** The mismatch between absolute UTC epoch seconds (`fused_ekf.parquet`) and relative seconds (`aligned_100hz.parquet`) is easy to miss but would cause silent RMSE computation errors if not handled. Checking the `time_ns` branch in `rts_smoother.py` against the output format of `odom_to_parquet.py` at the start would have clarified this immediately.

4. **Recognize that NEES and RMSE measure different things.** RMSE measures accuracy (how close to the reference). NEES measures consistency (whether P matches the actual errors). A filter can have good RMSE but poor NEES (accidentally accurate despite wrong uncertainty model) or vice versa. Both metrics together give a complete picture.

---

## Next in this series

- `src/ideal_driver/` — Valhalla map-matching and quintic trajectory synthesis
- `src/scoring/` — Six-component penalty model and tip lookup
- `src/reporting/` — Jinja2 + Folium HTML report generation
