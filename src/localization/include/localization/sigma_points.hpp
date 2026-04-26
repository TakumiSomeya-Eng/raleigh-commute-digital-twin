// FR-5.1 — Julier-Uhlmann scaled sigma-point generator for CTRV state (n=5).
// Header-only; no ROS dependency — shared by ukf_node and unit tests.
//
// Implemented in task T2.7.
#pragma once

#include <Eigen/Cholesky>
#include <Eigen/Core>
#include <stdexcept>

namespace localization {

static constexpr int kSigmaN = 5;     // CTRV state dimension
static constexpr int kNumSigma = 11;  // 2*n + 1

using SigmaMat = Eigen::Matrix<double, kSigmaN, kNumSigma>;
using WeightVec = Eigen::Matrix<double, 1, kNumSigma>;

struct SigmaPoints {
  SigmaMat pts;  // each column is one sigma point
  WeightVec Wm;  // mean weights
  WeightVec Wc;  // covariance weights
};

// ---------------------------------------------------------------------------
// generate — compute 2n+1 scaled sigma points and associated weights.
//
// Parameters (Julier-Uhlmann convention):
//   alpha : spread of sigma points around mean (typ. 1e-3)
//   beta  : incorporates prior distribution knowledge (2 optimal for Gaussian)
//   kappa : secondary scaling (typ. 0)
// ---------------------------------------------------------------------------
inline SigmaPoints generate(const Eigen::Matrix<double, kSigmaN, 1>& x,
                            const Eigen::Matrix<double, kSigmaN, kSigmaN>& P, double alpha = 1e-3,
                            double beta = 2.0, double kappa = 0.0) {
  const double n = static_cast<double>(kSigmaN);
  const double lambda = alpha * alpha * (n + kappa) - n;

  Eigen::LLT<Eigen::Matrix<double, kSigmaN, kSigmaN>> llt((n + lambda) * P);
  if (llt.info() != Eigen::Success) {
    throw std::runtime_error("sigma_points::generate — Cholesky failed (P not positive definite)");
  }
  const Eigen::Matrix<double, kSigmaN, kSigmaN> L = llt.matrixL();

  SigmaPoints sp;
  sp.pts.col(0) = x;
  for (int i = 0; i < kSigmaN; ++i) {
    sp.pts.col(i + 1) = x + L.col(i);
    sp.pts.col(kSigmaN + i + 1) = x - L.col(i);
  }

  sp.Wm(0) = lambda / (n + lambda);
  sp.Wc(0) = lambda / (n + lambda) + (1.0 - alpha * alpha + beta);
  const double w_rest = 0.5 / (n + lambda);
  for (int i = 1; i < kNumSigma; ++i) {
    sp.Wm(i) = w_rest;
    sp.Wc(i) = w_rest;
  }

  return sp;
}

// ---------------------------------------------------------------------------
// weighted_mean — reconstruct state mean from sigma points.
// ---------------------------------------------------------------------------
inline Eigen::Matrix<double, kSigmaN, 1> weighted_mean(const SigmaPoints& sp) {
  // (5x11) * (11x1) = (5x1)
  return sp.pts * sp.Wm.transpose();
}

// ---------------------------------------------------------------------------
// weighted_cov — reconstruct covariance from sigma points and precomputed mean.
// ---------------------------------------------------------------------------
inline Eigen::Matrix<double, kSigmaN, kSigmaN> weighted_cov(
    const SigmaPoints& sp, const Eigen::Matrix<double, kSigmaN, 1>& mean) {
  Eigen::Matrix<double, kSigmaN, kSigmaN> C = Eigen::Matrix<double, kSigmaN, kSigmaN>::Zero();
  for (int i = 0; i < kNumSigma; ++i) {
    const auto d = sp.pts.col(i) - mean;
    C += sp.Wc(i) * d * d.transpose();
  }
  return C;
}

}  // namespace localization
