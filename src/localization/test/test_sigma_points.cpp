// FR-5.1 — Unit tests for sigma-point generator round-trips.
// No ROS dependency: tests pure C++ logic in sigma_points.hpp.
//
// DoD (from DEV_PLAN T2.7):
//   (1) Weighted mean reconstructs original mean within 1e-12.
//   (2) Weighted covariance reconstructs original covariance within 1e-10.
//
// Implemented in task T2.7.

#include <gtest/gtest.h>

#include "localization/sigma_points.hpp"

using localization::generate;
using localization::SigmaPoints;
using localization::weighted_cov;
using localization::weighted_mean;

using Vec5 = Eigen::Matrix<double, 5, 1>;
using Mat5 = Eigen::Matrix<double, 5, 5>;

// ---------------------------------------------------------------------------
// Helper: build a valid (positive-definite) diagonal covariance.
// ---------------------------------------------------------------------------
static Mat5 make_diag_cov(double px, double py, double v, double psi, double pd) {
  Mat5 P = Mat5::Zero();
  P(0, 0) = px;
  P(1, 1) = py;
  P(2, 2) = v;
  P(3, 3) = psi;
  P(4, 4) = pd;
  return P;
}

// ---------------------------------------------------------------------------
// Test 1: Mean round-trip — diagonal P, default parameters
// ---------------------------------------------------------------------------
TEST(SigmaPoints, MeanRoundTripDiagonal) {
  Vec5 x;
  x << 10.0, 20.0, 5.0, 0.3, 0.05;
  Mat5 P = make_diag_cov(4.0, 4.0, 16.0, 1.0, 0.25);

  const SigmaPoints sp = generate(x, P);
  const Vec5 x_rec = weighted_mean(sp);

  EXPECT_NEAR((x_rec - x).norm(), 0.0, 1e-12);
}

// ---------------------------------------------------------------------------
// Test 2: Covariance round-trip — diagonal P, default parameters
// ---------------------------------------------------------------------------
TEST(SigmaPoints, CovRoundTripDiagonal) {
  Vec5 x;
  x << 0.0, 0.0, 0.0, 0.0, 0.0;
  Mat5 P = make_diag_cov(1.0, 2.0, 3.0, 0.5, 0.1);

  const SigmaPoints sp = generate(x, P);
  const Vec5 mean = weighted_mean(sp);
  const Mat5 P_rec = weighted_cov(sp, mean);

  EXPECT_NEAR((P_rec - P).norm(), 0.0, 1e-10);
}

// ---------------------------------------------------------------------------
// Test 3: Mean round-trip — full (dense) positive-definite covariance
// ---------------------------------------------------------------------------
TEST(SigmaPoints, MeanRoundTripDense) {
  Vec5 x;
  x << 5.0, -3.0, 2.0, 1.5, -0.2;

  // Build SPD matrix via P = A^T A + epsilon*I.
  Mat5 A;
  // clang-format off
  A << 1.0, 0.2, 0.0, 0.1, 0.0,
       0.0, 1.5, 0.3, 0.0, 0.1,
       0.0, 0.0, 0.8, 0.2, 0.0,
       0.0, 0.0, 0.0, 0.6, 0.1,
       0.0, 0.0, 0.0, 0.0, 0.4;
  // clang-format on
  Mat5 P = A.transpose() * A + 0.01 * Mat5::Identity();

  const SigmaPoints sp = generate(x, P);
  const Vec5 x_rec = weighted_mean(sp);

  EXPECT_NEAR((x_rec - x).norm(), 0.0, 1e-12);
}

// ---------------------------------------------------------------------------
// Test 4: Covariance round-trip — full (dense) positive-definite covariance
// ---------------------------------------------------------------------------
TEST(SigmaPoints, CovRoundTripDense) {
  Vec5 x;
  x << 1.0, 2.0, 3.0, 0.1, 0.01;

  Mat5 A;
  // clang-format off
  A << 2.0, 0.1, 0.0, 0.2, 0.0,
       0.0, 1.2, 0.4, 0.0, 0.1,
       0.0, 0.0, 0.9, 0.3, 0.0,
       0.0, 0.0, 0.0, 0.7, 0.2,
       0.0, 0.0, 0.0, 0.0, 0.5;
  // clang-format on
  Mat5 P = A.transpose() * A + 0.01 * Mat5::Identity();

  const SigmaPoints sp = generate(x, P);
  const Vec5 mean = weighted_mean(sp);
  const Mat5 P_rec = weighted_cov(sp, mean);

  EXPECT_NEAR((P_rec - P).norm(), 0.0, 1e-10);
}

// ---------------------------------------------------------------------------
// Test 5: Weight sum — Wm sums to 1.0 (probability simplex)
// ---------------------------------------------------------------------------
TEST(SigmaPoints, WeightSumToOne) {
  Vec5 x = Vec5::Zero();
  Mat5 P = Mat5::Identity();

  const SigmaPoints sp = generate(x, P);
  const double wm_sum = sp.Wm.sum();

  EXPECT_NEAR(wm_sum, 1.0, 1e-14);
}

// ---------------------------------------------------------------------------
// Test 6: Correct number of sigma points (2n+1 = 11)
// ---------------------------------------------------------------------------
TEST(SigmaPoints, CorrectPointCount) {
  EXPECT_EQ(localization::kNumSigma, 11);
  EXPECT_EQ(localization::kSigmaN, 5);
}

// ---------------------------------------------------------------------------
// Test 7: X_0 is the mean itself
// ---------------------------------------------------------------------------
TEST(SigmaPoints, CentralPointIsMean) {
  Vec5 x;
  x << 3.0, 1.0, -2.0, 0.5, 0.0;
  Mat5 P = make_diag_cov(1.0, 1.0, 1.0, 1.0, 1.0);

  const SigmaPoints sp = generate(x, P);
  EXPECT_NEAR((sp.pts.col(0) - x).norm(), 0.0, 1e-15);
}

// ---------------------------------------------------------------------------
// Test 8: Symmetric sigma points (X_{i} and X_{n+i} are symmetric around mean)
// ---------------------------------------------------------------------------
TEST(SigmaPoints, SymmetricPoints) {
  Vec5 x;
  x << 0.0, 0.0, 1.0, 0.2, 0.01;
  Mat5 P = make_diag_cov(2.0, 3.0, 0.5, 0.8, 0.1);

  const SigmaPoints sp = generate(x, P);

  for (int i = 0; i < localization::kSigmaN; ++i) {
    const Vec5 hi = sp.pts.col(i + 1);                          // x + col_i
    const Vec5 lo = sp.pts.col(localization::kSigmaN + i + 1);  // x - col_i
    EXPECT_NEAR((hi + lo - 2.0 * x).norm(), 0.0, 1e-13);
  }
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
