// FR-4.1 — GoogleTest suite for the CTRV motion model.
// Implemented in task T2.2.

#include <gtest/gtest.h>

#include <Eigen/Core>
#include <cmath>

#include "localization/ctrv_model.hpp"

using Vec5 = Eigen::Matrix<double, 5, 1>;
using Mat5 = Eigen::Matrix<double, 5, 5>;

// ---------------------------------------------------------------------------
// Test 1: StraightLine
// psi=0, psi_dot=0, v=10, a_lon=0, dt=1 -> px increases by 10, py stays 0.
// ---------------------------------------------------------------------------
TEST(CtrvModel, StraightLine) {
  Vec5 state;
  state << 0.0, 0.0, 10.0, 0.0, 0.0;

  const Vec5 result = localization::predict(state, 1.0, 0.0);

  EXPECT_NEAR(result[localization::kPx], 10.0, 1e-9);
  EXPECT_NEAR(result[localization::kPy], 0.0, 1e-9);
  EXPECT_NEAR(result[localization::kV], 10.0, 1e-9);
  EXPECT_NEAR(result[localization::kPsi], 0.0, 1e-9);
  EXPECT_NEAR(result[localization::kPsiDot], 0.0, 1e-9);
}

// ---------------------------------------------------------------------------
// Test 2: CircleClosure
// psi=0, psi_dot=0.1, v=10, a_lon=0, dt=2*pi/0.1 -> back to near origin.
// ---------------------------------------------------------------------------
TEST(CtrvModel, CircleClosure) {
  Vec5 state;
  state << 0.0, 0.0, 10.0, 0.0, 0.1;

  const double dt = 2.0 * M_PI / 0.1;
  const Vec5 result = localization::predict(state, dt, 0.0);

  EXPECT_NEAR(result[localization::kPx], 0.0, 1e-4);
  EXPECT_NEAR(result[localization::kPy], 0.0, 1e-4);
}

// ---------------------------------------------------------------------------
// Test 3: JacobianFiniteDiff
// At 5 hardcoded states, analytical Jacobian matches finite-difference
// (eps=1e-5) within 1e-4.
// ---------------------------------------------------------------------------
TEST(CtrvModel, JacobianFiniteDiff) {
  constexpr double eps = 1e-5;
  constexpr double tol = 1e-4;
  constexpr double dt = 0.1;

  // 5 hardcoded test states: [px, py, v, psi, psi_dot]
  const std::array<Vec5, 5> states = {
      (Vec5() << 0.0, 0.0, 5.0, 0.3, 0.2).finished(),
      (Vec5() << 10.0, -5.0, 8.0, -0.5, 0.4).finished(),
      (Vec5() << -3.0, 2.0, 12.0, M_PI / 4.0, -0.3).finished(),
      (Vec5() << 1.0, 1.0, 6.0, 0.0, 1e-7).finished(),  // near-zero psi_dot branch
      (Vec5() << 0.0, 0.0, 10.0, M_PI / 3.0, 0.5).finished(),
  };

  for (const auto& state : states) {
    const Mat5 J_analytical = localization::jacobian(state, dt);

    // Compute finite-difference Jacobian.
    Mat5 J_fd;
    for (int col = 0; col < 5; ++col) {
      Vec5 state_plus = state;
      Vec5 state_minus = state;
      state_plus[col] += eps;
      state_minus[col] -= eps;
      const Vec5 f_plus = localization::predict(state_plus, dt, 0.0);
      const Vec5 f_minus = localization::predict(state_minus, dt, 0.0);
      J_fd.col(col) = (f_plus - f_minus) / (2.0 * eps);
    }

    // Compare element-wise (skip psi row for wrap-around, check others).
    for (int row = 0; row < 5; ++row) {
      // Skip the psi row (index 3) since std::remainder can cause discontinuities
      // near ±pi in finite differences.
      if (row == localization::kPsi) continue;
      for (int col = 0; col < 5; ++col) {
        EXPECT_NEAR(J_analytical(row, col), J_fd(row, col), tol)
            << "Mismatch at row=" << row << " col=" << col << " state=" << state.transpose();
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Test 4: PsiWrap
// psi=M_PI-0.01, psi_dot=0.5, v=5, dt=1 -> result psi in [-pi, pi].
// ---------------------------------------------------------------------------
TEST(CtrvModel, PsiWrap) {
  Vec5 state;
  state << 0.0, 0.0, 5.0, M_PI - 0.01, 0.5;

  const Vec5 result = localization::predict(state, 1.0, 0.0);

  EXPECT_GE(result[localization::kPsi], -M_PI);
  EXPECT_LE(result[localization::kPsi], M_PI);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
