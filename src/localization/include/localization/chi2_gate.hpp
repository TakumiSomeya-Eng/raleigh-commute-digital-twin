// FR-4.3 / FR-4.4 — Chi-squared gate and adaptive GPS measurement covariance.
// Header-only; used by ekf_node and ukf_node to reject outlier measurements
// and scale the GPS R matrix from the horizontal_accuracy field.
//
// Implemented in task T2.3.
#pragma once

#include <Eigen/Core>
#include <Eigen/LU>

namespace localization {

// ---------------------------------------------------------------------------
// Chi-squared thresholds (one-tailed, upper critical value)
//
// Source: chi2inv() / scipy.stats.chi2.ppf() lookup table, 99% confidence.
//   1 DOF, 99%:  chi2inv(0.99, 1) = 6.6349  -> rounded to 6.635
//   2 DOF, 99%:  chi2inv(0.99, 2) = 9.2103  -> rounded to 9.210
//   3 DOF, 99%:  chi2inv(0.99, 3) = 11.3449 -> rounded to 11.345
//
//   2 DOF, 95%:  chi2inv(0.95, 2) = 5.9915  -> rounded to 5.991
//   (used when confidence = 0.95 is requested via passes_gate())
// ---------------------------------------------------------------------------
constexpr double kChi2Threshold1D = 6.635;   // 1 DOF, 99%
constexpr double kChi2Threshold2D = 9.210;   // 2 DOF, 99%
constexpr double kChi2Threshold3D = 11.345;  // 3 DOF, 99%

// Additional threshold used by the 95%-confidence overload path.
// Source: chi2inv(0.95, 2) = 5.9915
constexpr double kChi2Threshold2D_95 = 5.991;  // 2 DOF, 95%

// ---------------------------------------------------------------------------
// passes_gate()
//
// Returns true iff the Mahalanobis distance squared
//   d^2 = innovation^T * S^{-1} * innovation
// is below the chi-squared threshold for the innovation dimension at the
// requested confidence level.
//
// Supported (dof, confidence) pairs:
//   (1, 0.99), (2, 0.99), (3, 0.99)  — uses kChi2Threshold{1,2,3}D
//   (2, 0.95)                         — uses kChi2Threshold2D_95
//
// For any other combination the function returns true (conservative / no-gate)
// so that unsupported configurations do not silently drop measurements.
// ---------------------------------------------------------------------------
inline bool passes_gate(const Eigen::VectorXd& innovation, const Eigen::MatrixXd& S,
                        double confidence = 0.99) {
  const int dof = static_cast<int>(innovation.size());

  // Mahalanobis distance squared: d^2 = z^T S^{-1} z
  const double d2 = innovation.transpose() * S.inverse() * innovation;

  // Select threshold based on (dof, confidence).
  double threshold = 0.0;
  bool supported = true;

  if (confidence >= 0.989 && confidence <= 0.991) {
    // 99% confidence
    if (dof == 1) {
      threshold = kChi2Threshold1D;
    } else if (dof == 2) {
      threshold = kChi2Threshold2D;
    } else if (dof == 3) {
      threshold = kChi2Threshold3D;
    } else {
      supported = false;
    }
  } else if (confidence >= 0.949 && confidence <= 0.951) {
    // 95% confidence
    if (dof == 2) {
      threshold = kChi2Threshold2D_95;
    } else {
      supported = false;
    }
  } else {
    supported = false;
  }

  if (!supported) {
    // Unknown (dof, confidence) — conservative: let measurement through.
    return true;
  }

  return d2 <= threshold;
}

// ---------------------------------------------------------------------------
// gps_r_matrix()
//
// Adaptive GPS measurement noise covariance (FR-4.4).
//
// Constructs R_gps = diag(sigma_h^2, sigma_h^2) where sigma_h is the
// horizontal_accuracy reported directly by the GPS receiver (metres, 1-sigma).
// The returned 2x2 matrix covers the [north, east] (or [x, y]) position
// components; heading / velocity noise is handled separately.
//
// Usage example:
//   auto R = localization::gps_r_matrix(msg.horizontal_accuracy);
// ---------------------------------------------------------------------------
inline Eigen::Matrix2d gps_r_matrix(double horizontal_accuracy_m) {
  const double var = horizontal_accuracy_m * horizontal_accuracy_m;
  return Eigen::Vector2d(var, var).asDiagonal();
}

}  // namespace localization
