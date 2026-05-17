# Learning Notes: `src/localization/`

**Project:** Raleigh Commute Digital Twin — *Uber vs. My AI*
**Module:** `src/localization/` (Phase P2 — FR-4, FR-5)
**Purpose of this document:** Post-completion study notes written to close the implementation-level understanding gap that emerged from AI-assisted development (Claude Code). This is the second in a planned series covering each module in the project.

---

## Overview

`localization` is the sensor fusion stage of the pipeline. It takes the MCAP bag file produced by `bag_bridge` and runs an Extended Kalman Filter (EKF) or Unscented Kalman Filter (UKF) to fuse GPS (~1 Hz) and IMU (100 Hz) into a high-frequency, low-noise trajectory estimate. The output is published as a ROS 2 `nav_msgs/Odometry` topic at 100 Hz, which is then recorded back to `fused_ekf.parquet` or `fused_ukf.parquet`.

### File map

| File | Role |
|---|---|
| `include/localization/ctrv_model.hpp` | CTRV motion model: `predict()` and `jacobian()` |
| `include/localization/chi2_gate.hpp` | Chi-squared outlier gate and adaptive GPS R matrix |
| `include/localization/sigma_points.hpp` | UKF sigma-point generation and reconstruction |
| `include/localization/diagnostics.hpp` | 10-second sliding-window health monitor |
| `src/ekf_node.cpp` | EKF ROS 2 node |
| `src/ukf_node.cpp` | UKF ROS 2 node |
| `src/bag_bridge.cpp` | Stub (not yet implemented at time of study) |

All four headers are header-only and have no ROS dependency, making them independently unit-testable.

### The core problem this module solves

GPS is accurate but slow (~1 Hz, ~3–20 m noise). IMU is fast (100 Hz) but accumulates drift over time. The Kalman filter continuously balances these two sources:

```
GPS  → accurate position, slow update   → corrects drift
IMU  → fast, noisy acceleration/rotation → fills gaps between GPS fixes
```

---

## State vector and covariance matrix

### State vector x (5-dimensional)

```
x = [px, py, v, psi, psi_dot]ᵀ

px      = East position from ENU anchor (m)
py      = North position from ENU anchor (m)
v       = Forward speed (m/s)
psi     = Heading / yaw angle (rad), normalized to [-π, π]
psi_dot = Yaw rate (rad/s)
```

### Covariance matrix P (5×5)

P always accompanies x. While x is the best estimate of the current state, P quantifies how much that estimate can be trusted.

```
     px    py    v     psi   psi_dot
px [ σpx²  .     .     .     .     ]
py [ .     σpy²  .     .     .     ]
v  [ .     .     σv²   .     .     ]
psi[ .     .     .     σψ²   .     ]
ψ̇ [ .     .     .     .     σψ̇²  ]
```

The diagonal elements are variances (larger = less confident). The off-diagonal elements are covariances (how errors in two variables are correlated). At initialization these are zero, but they grow non-zero as the filter runs, capturing for example "when heading uncertainty is large, position uncertainty also grows."

### The predict–update cycle

Every IMU callback (100 Hz) runs a predict step: P grows because the vehicle moved and we are less certain where it ended up. Every GPS callback (~1 Hz) runs an update step: P shrinks because we observed reality and corrected the estimate.

```
Predict (IMU, 100 Hz): x and P evolve forward in time → P grows
Update  (GPS,   1 Hz): x and P are corrected by measurement → P shrinks
```

---

## Initialization — waiting for 3 GPS fixes

### Why initialization is needed

At startup, the EKF has no knowledge of position, speed, or heading. It cannot begin predicting until it has a meaningful starting point.

### The 3-fix accumulation

```cpp
void on_gps(sensor_msgs::msg::NavSatFix::SharedPtr msg) {
    latlon_to_enu(msg->latitude, msg->longitude, px, py);

    if (!initialized_) {
        init_positions_.push_back({px, py});
        if (init_positions_.size() < 3) return;  // wait
        initialize_from_gps(px, py, msg->position_covariance[0]);
        return;
    }
    // normal GPS update ...
}
```

GPS fix 1 and 2 are stored in `init_positions_`. On fix 3, `initialize_from_gps()` is called. This takes approximately 2–3 seconds because GPS updates at ~1 Hz.

### What `!initialized_` means

`!` is the C++ logical NOT operator.

```
initialized_ = false  →  !initialized_ = true  (not yet ready)
initialized_ = true   →  !initialized_ = false (ready)
```

During IMU callbacks before initialization, the node stores only `last_imu_stamp_` and returns immediately, preventing `dt` from being computed incorrectly on the first active IMU message.

### initialize_from_gps() step by step

```cpp
void initialize_from_gps(double px, double py, double var_h) {
    x_.setZero();
    x_[kPx] = px;
    x_[kPy] = py;
    x_[kV]  = 0.0;

    if (init_positions_.size() >= 2) {
        const auto& p0 = init_positions_.front();
        x_[kPsi] = std::atan2(py - p0[1], px - p0[0]);
    }
    x_[kPsiDot] = 0.0;

    P_ = Mat5::Zero();
    P_(kPx, kPx) = var_h;
    P_(kPy, kPy) = var_h;
    P_(kV,  kV)  = 4.0 * 4.0;
    P_(kPsi,kPsi)= M_PI * M_PI;
    P_(kPsiDot,kPsiDot) = 0.5 * 0.5;

    initialized_ = true;
}
```

**Position:** The third GPS coordinate is used directly as the initial position.

**Speed:** Set to 0.0. The vehicle is assumed to be stationary or slow at startup. The large initial P(v,v) = 16.0 ensures the first GPS speed update corrects this quickly.

**Heading via `std::atan2`:** The direction from fix 1 to fix 3 is computed as `atan2(py - p0[1], px - p0[0])`. Using the first and third fixes (rather than consecutive fixes) reduces the influence of GPS noise because the distance between them is larger, making the direction estimate more stable.

**Initial P values:**

| Element | Value | Meaning |
|---|---|---|
| P(px,px), P(py,py) | var_h | GPS self-reported accuracy (not a guess) |
| P(v,v) | 16.0 (= 4²) | Speed unknown within ±4 m/s |
| P(psi,psi) | 9.87 (= π²) | Heading nearly unknown — all directions possible |
| P(psi_dot,psi_dot) | 0.25 (= 0.5²) | Yaw rate near zero at startup |

These initial values are empirical decisions (hard-coded), not derived from data. The Kalman filter's self-correcting property means that even if these are wrong, the filter converges to the correct values within a few seconds of receiving GPS and IMU measurements.

**`M_PI`** is a compile-time constant from `<cmath>` equal to 3.14159265358979..., not computed at runtime.

---

## `ctrv_model.hpp` — motion model

### What CTRV assumes

CTRV (Constant Turn Rate and Velocity) assumes that over one short time step dt, both the speed v and the yaw rate psi_dot remain constant. Under this assumption the vehicle traces a circular arc.

### predict() — two cases

**Straight-line case (|psi_dot| < 1e-6):**

```
px_new = px + v * cos(psi) * dt
py_new = py + v * sin(psi) * dt
```

This is simple vector decomposition: the distance traveled (v × dt) is split into East and North components using the current heading.

**Turning case (|psi_dot| ≥ 1e-6):**

```
px_new = px + (v / psi_dot) * (sin(psi_new) - sin(psi))
py_new = py + (v / psi_dot) * (-cos(psi_new) + cos(psi))
```

This is the closed-form integral of circular arc motion. The derivation:

```
dx/dt = v * cos(psi + psi_dot * t)

∫₀ᵈᵗ v * cos(psi + psi_dot * t) dt

= v * (1 / psi_dot) * [sin(psi + psi_dot * t)]₀ᵈᵗ

= (v / psi_dot) * (sin(psi_new) - sin(psi))
```

The `1 / psi_dot` factor comes from the chain rule in reverse: differentiating `sin(psi + psi_dot * t)` gives `cos(...) * psi_dot`, so integrating divides by `psi_dot`.

The threshold 1e-6 prevents division by near-zero psi_dot. The straight-line formula is the Taylor-series limit of the turning formula as psi_dot → 0.

**Heading normalization:**

```cpp
next[kPsi] = std::remainder(next[kPsi], 2.0 * M_PI);
```

`std::remainder` maps the result into `[-π, π]`. This is essential: without it, repeated right turns would let psi grow unboundedly, causing two problems:

1. Innovation calculation breaks: 359° and 1° would appear to be 358° apart instead of 2°.
2. Floating-point precision degrades: at psi = 1000 rad, changes smaller than ~0.001 rad cannot be represented.

`std::fmod` is not used here because it maps to `[0, 2π]`, not `[-π, π]`. The EKF needs `[-π, π]` for symmetric innovation computation.

### jacobian() — why the EKF needs it

`predict()` contains sin/cos and is therefore nonlinear. The covariance matrix P cannot be passed through a nonlinear function directly. The Jacobian F is the first-order linear approximation of predict() around the current state x:

```
predict(x + δx) ≈ predict(x) + F * δx
```

This approximation holds when δx is small, which it is when dt = 0.01 s. Under this approximation:

```
P_new = F * P * Fᵀ + Q
```

Example entries from the straight-line case:

```
F(px, v)   = cos(psi) * dt    — if v increases by 1, px increases by cos(psi)*dt
F(px, psi) = -v*sin(psi)*dt   — if psi changes by 1 rad, px changes accordingly
F(psi, psi_dot) = dt           — heading advances by psi_dot * dt
```

The UKF does not need the Jacobian. Instead it passes 11 sigma points directly through `predict()`, capturing the nonlinear shape without linearization.

---

## `chi2_gate.hpp` — outlier rejection

### Why outlier rejection is necessary

GPS can jump tens of meters due to multipath (signal reflection off buildings) or satellite geometry changes. Feeding such an outlier into the Kalman update would shift the estimated position by tens of meters in one step.

### Mahalanobis distance d²

```cpp
const double d2 = innovation.transpose() * S.inverse() * innovation;
```

**Why not Euclidean distance?** The same 10 m innovation means different things depending on GPS quality:

- GPS accuracy = 20 m → 10 m deviation is within normal variation → normal
- GPS accuracy = 2 m  → 10 m deviation is statistically impossible → outlier

Mahalanobis distance normalizes by S, which encodes "how large a deviation is statistically normal right now."

**1D simplification to build intuition:**

```
d² = innovation² / S
   = (measured deviation)² / (prediction uncertainty + measurement noise)
```

This is the square of the number of standard deviations the measurement is from the prediction.

**2D case — contours become ellipses:**

When S has non-zero off-diagonal elements (east-west and north-south errors are correlated), the equal-d² contours form a tilted ellipse rather than a circle. Mahalanobis distance measures "how many standard deviations" along the principal axes of that ellipse.

**Step-by-step computation:**

```
Step 1: S⁻¹ · innov   (2×2)·(2×1) = (2×1) — normalize by S
Step 2: innovᵀ · (step 1)   (1×2)·(2×1) = scalar — this is d²
```

### Chi-squared threshold

When the filter is working correctly, d² follows a chi-squared distribution with degrees of freedom equal to the innovation dimension. The 99th percentile of this distribution is the threshold:

```
1 DOF (yaw rate): 6.635
2 DOF (GPS position): 9.210
3 DOF (3D): 11.345
```

These are compile-time constants computed from `scipy.stats.chi2.ppf(0.99, dof)`. If `d² > threshold`, the measurement is rejected. With confidence=0.99, the gate incorrectly rejects 1% of valid measurements — an acceptable false-positive rate.

### S is recomputed on every GPS callback

```cpp
const Mat2 S = H * P_ * H.transpose() + R;
```

S has two components:

- `H * P_ * Hᵀ` — current EKF prediction uncertainty (grows between GPS fixes, shrinks after updates)
- `R` — GPS measurement noise from `msg->position_covariance[0]` (varies with satellite count and signal quality)

S is therefore dynamic: it is neither fixed nor stored between callbacks. When GPS quality is poor (large R), the gate becomes more lenient. When the filter has been running without GPS for a long time (large P), the gate also becomes more lenient.

### gps_r_matrix() — adaptive measurement noise

```cpp
inline Eigen::Matrix2d gps_r_matrix(double horizontal_accuracy_m) {
    const double var = horizontal_accuracy_m * horizontal_accuracy_m;
    return Eigen::Vector2d(var, var).asDiagonal();
}
```

The GPS receiver reports its own accuracy estimate per message. Squaring it converts from standard deviation (metres) to variance (metres²) to match the units of the covariance matrix. This makes the filter automatically trust GPS less when the receiver signals poor conditions.

---

## `sigma_points.hpp` — UKF sigma points

### Why 11 sigma points

For state dimension n=5, the UKF uses 2n+1 = 11 sigma points. This is the minimum number needed to capture the mean and covariance of the distribution after a nonlinear transformation to third-order accuracy.

### Generation

```cpp
const double lambda = alpha² * (n + kappa) - n;
Cholesky decompose: L = chol((n + lambda) * P)

sigma point 0:   x  (the mean)
sigma points 1–5:   x + L.col(i)   (positive directions)
sigma points 6–10:  x - L.col(i)   (negative directions)
```

L (the Cholesky factor of the scaled P) encodes the shape and size of the uncertainty ellipsoid. Each column of L is one principal axis. Placing sigma points at ±L.col(i) from the mean samples the distribution along all principal axes.

If Cholesky decomposition fails, P is no longer positive definite (numerical drift has corrupted the covariance matrix). The `std::runtime_error` thrown here acts as a safety valve.

### Weights

```
Wm(0) = lambda / (n + lambda)              — mean weight for center point
Wc(0) = Wm(0) + (1 - alpha² + beta)       — cov weight (beta=2 optimal for Gaussian)
Wm(i) = Wc(i) = 0.5 / (n + lambda)        — equal weights for remaining 10 points
```

beta=2.0 is the theoretically optimal value for Gaussian distributions, incorporating knowledge of the fourth-order moment (kurtosis).

### Reconstruction after propagation

```cpp
x_new = Σ Wm(i) * sigma_point_pred(i)     // weighted mean
P_new = Σ Wc(i) * (sigma_i - x_new)(sigma_i - x_new)ᵀ + Q  // weighted cov
```

Because the sigma points were passed through the actual nonlinear `predict()`, the reconstructed mean and covariance reflect the true nonlinear transformation rather than a linear approximation.

---

## `diagnostics.hpp` — filter health monitoring

### 10-second sliding window

```cpp
std::deque<AcceptSample> accepted_;   // {time_s, nis}
std::deque<RejectSample> rejected_;   // {time_s}
```

`std::deque` is used because front deletion is O(1). Old events are pruned from the front; new events are appended to the back. The window always covers exactly the last 10 seconds.

### Three-level health state machine

```
OK       → rejection rate in window ≤ 5%  AND  NIS mean ≤ 15
DEGRADED → rejection rate in window >  5%
DIVERGED → NIS mean > 15  AND  sustained for ≥ 5 seconds
```

**Why NIS mean > 15 indicates divergence:** When the filter is consistent, NIS follows a chi-squared distribution whose mean equals the measurement dimension (2 for GPS position). If NIS mean far exceeds this (threshold = 3 × state_dim = 15), it means P is too small relative to the actual errors — the filter is overconfident and likely diverging.

**Why the 5-second delay for DIVERGED:** A momentary GPS outage (passing under a bridge) causes NIS to spike briefly. Requiring the condition to persist for 5 seconds prevents false DIVERGED alerts from transient events.

**Why 5% rejection rate triggers DEGRADED:** With chi2_confidence=0.99, the expected false-positive rejection rate is 1%. A rate of 5% means GPS is genuinely noisy or the filter model is mismatched — worth flagging but not necessarily fatal.

---

## EKF vs UKF — differences and trade-offs

### Where they differ

The two nodes share the same initialization, GPS speed update, yaw-rate update, output format, and diagnostics. They differ only in the IMU predict step and the GPS position update.

**IMU predict — EKF (linearization):**

```
predict() called once on x
P updated via Jacobian: P_new = F·P·Fᵀ + Q
```

**IMU predict — UKF (sigma points):**

```
predict() called 11 times (once per sigma point)
P reconstructed from weighted covariance of propagated sigma points
```

**GPS update — EKF:** Uses constant H matrix (linear measurement model).

**GPS update — UKF:** Projects sigma points into measurement space, computes cross-covariance Pxy directly. The Kalman gain becomes `K = Pxy * S⁻¹` instead of `K = P * Hᵀ * S⁻¹`.

**Why speed and yaw-rate updates are identical in both:** The measurement models `z = v` and `z = psi_dot` are linear (H is constant). Linearization and sigma-point propagation give the same result for linear models, so the simpler EKF form is used in both nodes.

### Trade-offs

| | EKF | UKF |
|---|---|---|
| predict() calls per IMU tick | 1 | 11 |
| Accuracy on sharp curves | Degrades (linear approximation) | Maintained (nonlinear) |
| Jacobian required | Yes | No |
| Implementation complexity | Lower | Higher (sigma point weights) |

For Raleigh's mostly straight commute roads, EKF is generally sufficient. The `evaluation` module computes RMSE for both and determines which is better for each specific trace.

---

## Output — `/fused/odom` topic

### Message structure

```
nav_msgs/Odometry
  header.stamp         → IMU measurement timestamp (not receive time)
  header.frame_id      → "odom" (ENU coordinate frame)
  child_frame_id       → "base_link" (vehicle body frame)

  pose.pose.position   → x_[kPx], x_[kPy], 0.0
  pose.pose.orientation→ quaternion from heading psi (yaw-only)
  pose.covariance      → 6×6 array mapped from 5×5 P_

  twist.twist.linear.x → x_[kV]       (forward speed)
  twist.twist.angular.z→ x_[kPsiDot]  (yaw rate)
```

### Heading to quaternion conversion

ROS 2 uses quaternions for orientation. For a yaw-only rotation (roll = pitch = 0):

```
w = cos(psi / 2)
x = 0
y = 0
z = sin(psi / 2)
```

This is derived from the general quaternion rotation formula restricted to the z-axis.

### 5×5 P → 6×6 covariance array

NavMsgs Odometry defines a 36-element (6×6) covariance array for [x, y, z, roll, pitch, yaw]. Since the EKF is a 2D model (no z, roll, or pitch), those diagonal elements are set to 1e-9 rather than exactly zero. The reason is that downstream nodes may invert this matrix, and exact zeros would cause division-by-zero.

```
covariance[0]  = P(px, px)   // index = row*6 + col
covariance[7]  = P(py, py)
covariance[35] = P(psi, psi)
covariance[14] = 1e-9        // z-z sentinel
covariance[21] = 1e-9        // roll-roll sentinel
covariance[28] = 1e-9        // pitch-pitch sentinel
```

### Why IMU timestamp rather than receive time

ROS 2 message delivery has variable latency. Using `msg->header.stamp` (the sensor's own timestamp) ensures that `dt = stamp - last_stamp` reflects actual elapsed sensor time, not queuing delay. Using receive time would introduce variable dt errors into every predict step.

### Downstream consumers

```
/fused/odom (100 Hz)
    ↓
bag_bridge → fused_ekf.parquet / fused_ukf.parquet
    ↓
evaluation  → RMSE, NEES gate
scoring     → six-component penalty model
reporting   → Folium map with actual vs ideal trajectory
```

---

## Key concepts encountered for the first time

**Kalman gain K:** The weight that determines how much to trust the new measurement versus the current prediction. K is large when P is large (uncertain prediction) or R is small (accurate measurement), and small in the opposite case. It is not a tunable parameter — it is computed optimally from P and R at each step.

**CTRV model:** A vehicle motion model that assumes constant yaw rate and speed over one time step. Produces circular arc motion. The key insight is that both the straight-line and turning cases share the same mathematical form — the straight line is the limit of the arc as radius → ∞.

**Mahalanobis distance:** A distance metric that accounts for the shape of the uncertainty distribution. Unlike Euclidean distance, it is dimensionless ("number of standard deviations") and naturally handles correlated, anisotropic uncertainty.

**Chi-squared distribution:** Arises when summing squared normal random variables. Used here because the normalized innovation squared d² follows this distribution when the filter is consistent. The 99th percentile is the gate threshold.

**Cholesky decomposition:** Factors a positive-definite matrix P into L·Lᵀ where L is lower-triangular. Used in sigma-point generation to find the "square root" of the covariance matrix — the directions and magnitudes of uncertainty.

**`std::remainder` vs `std::fmod`:** Both compute a remainder, but `remainder` maps to `[-y/2, y/2]` while `fmod` maps to `[0, y]`. For heading normalization, `[-π, π]` is required so that angular differences are computed correctly across the 0°/360° boundary.

**`!initialized_`:** The `!` operator negates a boolean. `!initialized_` reads as "not yet initialized." A common C++ idiom for guard conditions at the start of callbacks.

---

## What I would do differently knowing this now

1. **Read `ctrv_model.hpp` before either node file.** The state indices `kPx, kPy, kV, kPsi, kPsiDot` are defined there, and `predict()` is the function that every other piece of math builds on. Starting with the node files means constantly looking up what these indices mean.

2. **Understand P as a living object, not a static matrix.** I initially thought of P as a fixed configuration parameter. Realizing that P changes every 10 ms (growing during predict, shrinking during update) and that this dynamic behavior is what makes the filter adaptive was the most important conceptual shift.

3. **Trace the GPS accuracy field explicitly.** `msg->position_covariance[0]` is `horizontal_accuracy_m²`, not `horizontal_accuracy_m`. This single field drives both the chi-squared gate threshold (via S) and the Kalman gain (via R). Understanding this connection clarifies why the filter behaves differently in open areas versus urban canyons.

4. **Recognize which updates are linear and which are not.** Speed update and yaw-rate update are linear → same code in EKF and UKF. Position prediction is nonlinear → different code. Making this distinction explicit from the start would have made the EKF/UKF comparison much clearer.

---

## Next in this series

- `src/evaluation/` — RMSE, NEES, RTS smoother, P3 gate
- `src/ideal_driver/` — Valhalla map-matching and quintic trajectory synthesis
- `src/scoring/` — Six-component penalty model and tip lookup
- `src/reporting/` — Jinja2 + Folium HTML report generation
