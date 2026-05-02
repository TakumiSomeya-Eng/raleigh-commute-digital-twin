// FR-4.3 / FR-4.4 — GoogleTest suite for chi2_gate.hpp.
// Implemented in task T2.3.

#include <gtest/gtest.h>

#include <Eigen/Core>

#include "localization/chi2_gate.hpp"

// ---------------------------------------------------------------------------
// Test 1: InGate
// A 2D innovation well inside the gate must pass (d^2 << 9.21).
// innovation = [1.0, 1.0], S = I  ->  d^2 = 2.0  (well below 9.21)
// ---------------------------------------------------------------------------
TEST(Chi2Gate, InGate) {
  Eigen::VectorXd z(2);
  z << 1.0, 1.0;

  Eigen::MatrixXd S = Eigen::Matrix2d::Identity();

  EXPECT_TRUE(localization::passes_gate(z, S, 0.99));
}

// ---------------------------------------------------------------------------
// Test 2: CovarianceInflationFlipsBorderline
// Borderline-rejected measurement flips to accepted when S is inflated 10x.
//
// innovation = [3.0, 3.0], S = I  ->  d^2 = 18.0  > 9.21  => rejected
// innovation = [3.0, 3.0], S = 10*I ->  d^2 = 1.8   < 9.21  => accepted
// ---------------------------------------------------------------------------
TEST(Chi2Gate, CovarianceInflationFlipsBorderline) {
  Eigen::VectorXd z(2);
  z << 3.0, 3.0;

  Eigen::MatrixXd S_tight = Eigen::Matrix2d::Identity();
  Eigen::MatrixXd S_inflated = 10.0 * Eigen::Matrix2d::Identity();

  EXPECT_FALSE(localization::passes_gate(z, S_tight, 0.99));
  EXPECT_TRUE(localization::passes_gate(z, S_inflated, 0.99));
}

// ---------------------------------------------------------------------------
// Test 3: Day1OutlierRejected
// Synthetic values modelling the 122 m GPS jump observed on day 1.
// innovation = [86.7, 86.7] m, S = 100 * I
//   d^2 = (86.7^2 + 86.7^2) / 100 = 150.4  >> 9.21  => must be rejected.
// ---------------------------------------------------------------------------
TEST(Chi2Gate, Day1OutlierRejected) {
  Eigen::VectorXd z(2);
  z << 86.7, 86.7;

  Eigen::MatrixXd S = 100.0 * Eigen::Matrix2d::Identity();

  EXPECT_FALSE(localization::passes_gate(z, S, 0.99));
}

// ---------------------------------------------------------------------------
// Test 4: GpsRMatrix
// gps_r_matrix(5.0) must return diag(25.0, 25.0).
// ---------------------------------------------------------------------------
TEST(Chi2Gate, GpsRMatrix) {
  const Eigen::Matrix2d R = localization::gps_r_matrix(5.0);

  EXPECT_NEAR(R(0, 0), 25.0, 1e-12);
  EXPECT_NEAR(R(1, 1), 25.0, 1e-12);
  EXPECT_NEAR(R(0, 1), 0.0, 1e-12);
  EXPECT_NEAR(R(1, 0), 0.0, 1e-12);
}

// ---------------------------------------------------------------------------
// Test 5: Confidence95Threshold
// confidence=0.95 uses the 5.991 threshold for 2 DOF.
//
// innovation = [2.4, 0.0], S = I  ->  d^2 = 5.76  < 5.991  => passes at 95%
// innovation = [2.5, 0.0], S = I  ->  d^2 = 6.25  > 5.991  => rejected at 95%
//                                  but 6.25 < 9.21           => passes at 99%
// ---------------------------------------------------------------------------
TEST(Chi2Gate, Confidence95Threshold) {
  Eigen::MatrixXd S = Eigen::Matrix2d::Identity();

  Eigen::VectorXd z_inside(2);
  z_inside << 2.4, 0.0;  // d^2 = 5.76 < 5.991
  EXPECT_TRUE(localization::passes_gate(z_inside, S, 0.95));

  Eigen::VectorXd z_outside(2);
  z_outside << 2.5, 0.0;  // d^2 = 6.25 > 5.991 but < 9.21
  EXPECT_FALSE(localization::passes_gate(z_outside, S, 0.95));
  EXPECT_TRUE(localization::passes_gate(z_outside, S, 0.99));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
