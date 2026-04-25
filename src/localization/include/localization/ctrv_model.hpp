// FR-4.1 — CTRV (Constant Turn Rate and Velocity) motion model.
// Header-only; shared between ekf_node and ukf_node.
// State: [px, py, v, psi, psi_dot]^T in local ENU.
//
// Implemented in task T2.2.
#pragma once

#include <Eigen/Core>
#include <cmath>

namespace localization {

// State indices for readability.
constexpr int kPx = 0;
constexpr int kPy = 1;
constexpr int kV = 2;
constexpr int kPsi = 3;
constexpr int kPsiDot = 4;

/// Predict the CTRV state forward by dt seconds with longitudinal acceleration a_lon.
///
/// State vector: [px, py, v, psi, psi_dot]
///   - Near-zero psi_dot (|psi_dot| < 1e-6): straight-line Taylor expansion.
///   - psi is normalized into [-pi, pi] after prediction.
inline Eigen::Matrix<double, 5, 1> predict(const Eigen::Matrix<double, 5, 1>& state, double dt,
                                           double a_lon) {
  const double px = state[kPx];
  const double py = state[kPy];
  const double v = state[kV];
  const double psi = state[kPsi];
  const double psi_dot = state[kPsiDot];

  Eigen::Matrix<double, 5, 1> next = state;

  if (std::abs(psi_dot) < 1e-6) {
    // Straight-line limit (Taylor expansion as psi_dot -> 0).
    next[kPx] = px + v * std::cos(psi) * dt;
    next[kPy] = py + v * std::sin(psi) * dt;
  } else {
    // General CTRV update.
    const double psi_new = psi + psi_dot * dt;
    next[kPx] = px + (v / psi_dot) * (std::sin(psi_new) - std::sin(psi));
    next[kPy] = py + (v / psi_dot) * (-std::cos(psi_new) + std::cos(psi));
    next[kPsi] = psi_new;
  }

  // Update velocity with longitudinal acceleration.
  next[kV] = v + a_lon * dt;

  // Normalize heading into [-pi, pi].
  next[kPsi] = std::remainder(next[kPsi], 2.0 * M_PI);

  return next;
}

/// Analytical Jacobian of predict() w.r.t. the state vector (evaluated at a_lon = 0).
///
/// Returns the 5x5 matrix df/dx where f = predict(x, dt, 0).
inline Eigen::Matrix<double, 5, 5> jacobian(const Eigen::Matrix<double, 5, 1>& state, double dt) {
  const double v = state[kV];
  const double psi = state[kPsi];
  const double psi_dot = state[kPsiDot];

  // Start from identity (each state maps to itself by default).
  Eigen::Matrix<double, 5, 5> J = Eigen::Matrix<double, 5, 5>::Identity();

  if (std::abs(psi_dot) < 1e-6) {
    // Straight-line case partial derivatives.
    // d(px)/d(v)   = cos(psi)*dt
    J(kPx, kV) = std::cos(psi) * dt;
    // d(px)/d(psi) = -v*sin(psi)*dt
    J(kPx, kPsi) = -v * std::sin(psi) * dt;
    // d(px)/d(psi_dot) = 0  (already zero)

    // d(py)/d(v)   = sin(psi)*dt
    J(kPy, kV) = std::sin(psi) * dt;
    // d(py)/d(psi) = v*cos(psi)*dt
    J(kPy, kPsi) = v * std::cos(psi) * dt;
    // d(py)/d(psi_dot) = 0  (already zero)

    // d(psi)/d(psi_dot) = dt
    J(kPsi, kPsiDot) = dt;
  } else {
    const double psi_new = psi + psi_dot * dt;
    const double inv_pd = 1.0 / psi_dot;
    const double inv_pd2 = inv_pd * inv_pd;

    // d(px)/d(v)
    J(kPx, kV) = inv_pd * (std::sin(psi_new) - std::sin(psi));
    // d(px)/d(psi)
    J(kPx, kPsi) = (v * inv_pd) * (std::cos(psi_new) - std::cos(psi));
    // d(px)/d(psi_dot)
    J(kPx, kPsiDot) =
        -v * inv_pd2 * (std::sin(psi_new) - std::sin(psi)) + (v * inv_pd) * std::cos(psi_new) * dt;

    // d(py)/d(v)
    J(kPy, kV) = inv_pd * (-std::cos(psi_new) + std::cos(psi));
    // d(py)/d(psi)
    J(kPy, kPsi) = (v * inv_pd) * (std::sin(psi_new) - std::sin(psi));
    // d(py)/d(psi_dot)
    J(kPy, kPsiDot) =
        -v * inv_pd2 * (-std::cos(psi_new) + std::cos(psi)) + (v * inv_pd) * std::sin(psi_new) * dt;

    // d(psi)/d(psi_dot)
    J(kPsi, kPsiDot) = dt;
  }

  // v row: d(v_new)/d(v) = 1 (already set by Identity), others zero.
  // psi_dot row: identity (psi_dot is constant in CTRV).

  return J;
}

}  // namespace localization
