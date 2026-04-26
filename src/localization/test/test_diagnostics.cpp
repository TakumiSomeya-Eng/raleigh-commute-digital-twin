// FR-4.5 — Unit tests for DiagnosticsState health transitions.
// No ROS dependency: tests pure C++ logic in diagnostics.hpp.
//
// Implemented in task T2.6.

#include <gtest/gtest.h>

#include "localization/diagnostics.hpp"

using localization::DiagnosticsState;

// ---------------------------------------------------------------------------
// Helper: fill window with N accepted measurements at NIS = nis_val,
//         starting at time_s and spaced 1 s apart.
// ---------------------------------------------------------------------------
static void fill_accepted(DiagnosticsState& d, int n, double start_s, double nis_val) {
  for (int i = 0; i < n; ++i) {
    d.record_accepted(start_s + i, nis_val);
  }
}

static void fill_rejected(DiagnosticsState& d, int n, double start_s) {
  for (int i = 0; i < n; ++i) {
    d.record_rejected(start_s + i);
  }
}

// ---------------------------------------------------------------------------
// Test 1: Fresh state is OK
// ---------------------------------------------------------------------------
TEST(Diagnostics, InitialStateIsOk) {
  DiagnosticsState d;
  d.update(0.0);
  EXPECT_EQ(d.health, DiagnosticsState::kOk);
  EXPECT_EQ(d.rejection_count, 0);
  EXPECT_DOUBLE_EQ(d.nees_mean, 0.0);
}

// ---------------------------------------------------------------------------
// Test 2: High rejection rate (>5 %) triggers DEGRADED
// ---------------------------------------------------------------------------
TEST(Diagnostics, HighRejectionRateDegraded) {
  DiagnosticsState d;
  // 10 accepted, 1 rejected -> 1/11 ≈ 9 % > 5 %
  fill_accepted(d, 10, 0.0, 2.0);
  fill_rejected(d, 1, 10.0);
  d.update(11.0);
  EXPECT_EQ(d.health, DiagnosticsState::kDegraded);
}

// ---------------------------------------------------------------------------
// Test 3: Low rejection rate (4 %) stays OK
// ---------------------------------------------------------------------------
TEST(Diagnostics, LowRejectionRateOk) {
  DiagnosticsState d;
  // 24 accepted, 1 rejected -> 1/25 = 4 % <= 5 %
  fill_accepted(d, 24, 0.0, 2.0);
  fill_rejected(d, 1, 24.0);
  d.update(25.0);
  EXPECT_EQ(d.health, DiagnosticsState::kOk);
}

// ---------------------------------------------------------------------------
// Test 4: High NIS for < 5 s stays DEGRADED (not DIVERGED)
// ---------------------------------------------------------------------------
TEST(Diagnostics, HighNiesLessThan5sNotDiverged) {
  DiagnosticsState d;
  // NIS = 20 > kDivergedNees=15, but only for 3 s.
  fill_accepted(d, 3, 0.0, 20.0);
  d.update(3.0);
  // Should not be DIVERGED yet (< 5 s).
  EXPECT_NE(d.health, DiagnosticsState::kDiverged);
}

// ---------------------------------------------------------------------------
// Test 5: High NIS for >= 5 s continuously triggers DIVERGED
// ---------------------------------------------------------------------------
TEST(Diagnostics, HighNies5sContinuousDiverged) {
  DiagnosticsState d;
  // Feed high NIS samples for 6 s.
  for (int i = 0; i <= 6; ++i) {
    d.record_accepted(static_cast<double>(i), 20.0);  // NIS=20 > 15
    d.update(static_cast<double>(i));
  }
  EXPECT_EQ(d.health, DiagnosticsState::kDiverged);
}

// ---------------------------------------------------------------------------
// Test 6: Recovery — NIS drops back to normal, health returns to OK
// ---------------------------------------------------------------------------
TEST(Diagnostics, RecoveryFromDiverged) {
  DiagnosticsState d;
  // 6 s of high NIS → DIVERGED.
  for (int i = 0; i <= 6; ++i) {
    d.record_accepted(static_cast<double>(i), 20.0);
    d.update(static_cast<double>(i));
  }
  EXPECT_EQ(d.health, DiagnosticsState::kDiverged);

  // Another 11 s of healthy NIS (clears the 10 s window).
  for (int i = 7; i <= 18; ++i) {
    d.record_accepted(static_cast<double>(i), 2.0);
    d.update(static_cast<double>(i));
  }
  EXPECT_EQ(d.health, DiagnosticsState::kOk);
}

// ---------------------------------------------------------------------------
// Test 7: NIS mean is correctly averaged over window
// ---------------------------------------------------------------------------
TEST(Diagnostics, NeesMeanAveraging) {
  DiagnosticsState d;
  d.record_accepted(0.0, 3.0);
  d.record_accepted(1.0, 7.0);
  d.update(1.0);
  EXPECT_NEAR(d.nees_mean, 5.0, 1e-9);  // (3+7)/2
}

// ---------------------------------------------------------------------------
// Test 8: Windowed samples older than 10 s are pruned
// ---------------------------------------------------------------------------
TEST(Diagnostics, OldSamplesPruned) {
  DiagnosticsState d;
  // High-rejection window at t=0..4, then clean window at t=15..20.
  fill_rejected(d, 3, 0.0);
  fill_accepted(d, 2, 3.0, 2.0);  // 3 rej + 2 acc at early time
  // Advance time by 15 s (old samples outside 10 s window).
  fill_accepted(d, 10, 15.0, 2.0);
  d.update(25.0);
  // Rejections from t=0..4 are outside window [15,25], so rate should be 0.
  EXPECT_EQ(d.health, DiagnosticsState::kOk);
}

// ---------------------------------------------------------------------------
// Test 9: Cumulative rejection_count is not windowed
// ---------------------------------------------------------------------------
TEST(Diagnostics, CumulativeRejectionCountNotWindowed) {
  DiagnosticsState d;
  fill_rejected(d, 5, 0.0);
  // Advance past window.
  d.update(20.0);
  EXPECT_EQ(d.rejection_count, 5);  // cumulative count preserved
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
