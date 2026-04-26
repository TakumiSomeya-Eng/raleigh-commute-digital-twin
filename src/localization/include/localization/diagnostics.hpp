// FR-4.5 — EKF diagnostics state tracker.
// Header-only; no ROS dependency so it can be unit-tested in isolation.
//
// Tracks a 10-second sliding window of:
//   - GPS measurement NIS values (accepted + rejected events)
//   - Health state machine (OK → DEGRADED / DIVERGED)
//
// Implemented in task T2.6.
#pragma once

#include <deque>
#include <string>

namespace localization {

// ---------------------------------------------------------------------------
// DiagnosticsState
// ---------------------------------------------------------------------------

struct DiagnosticsState {
  // Configuration (mirror ekf_node parameters).
  static constexpr int kStateDim = 5;             // CTRV state dimension
  static constexpr double kWindowSec = 10.0;      // sliding-window length
  static constexpr double kDegradedRate = 0.05;   // 5 % rejection rate
  static constexpr double kDivergedNees = 15.0;   // 3 × state_dim
  static constexpr double kDivergedMinSec = 5.0;  // must stay diverged 5 s

  // Health labels.
  static constexpr const char* kOk = "OK";
  static constexpr const char* kDegraded = "DEGRADED";
  static constexpr const char* kDiverged = "DIVERGED";

  // ---------------------------------------------------------------------------
  // Public state (read by ekf_node to fill DiagnosticArray).
  // ---------------------------------------------------------------------------

  int rejection_count{0};   // cumulative lifetime count
  double nees_mean{0.0};    // windowed NIS mean across accepted measurements
  double q_trace{0.0};      // set externally from current Q parameters
  double r_pos_trace{0.0};  // set externally from last GPS R matrix
  std::string health{kOk};

  // ---------------------------------------------------------------------------
  // Event recording (called by ekf_node on every measurement decision).
  // ---------------------------------------------------------------------------

  void record_accepted(double time_s, double nis) {
    _prune(time_s);
    accepted_.push_back({time_s, nis});
  }

  void record_rejected(double time_s) {
    _prune(time_s);
    rejected_.push_back({time_s});
    ++rejection_count;
  }

  // ---------------------------------------------------------------------------
  // Health update — call once per diagnostics publish tick (1 Hz).
  // ---------------------------------------------------------------------------

  void update(double current_time_s) {
    _prune(current_time_s);

    const int n_acc = static_cast<int>(accepted_.size());
    const int n_rej = static_cast<int>(rejected_.size());
    const int n_total = n_acc + n_rej;

    // NIS mean over accepted window.
    nees_mean = 0.0;
    if (n_acc > 0) {
      double sum = 0.0;
      for (const auto& s : accepted_) sum += s.nis;
      nees_mean = sum / n_acc;
    }

    // Rejection rate over window.
    const double rej_rate = (n_total > 0) ? (static_cast<double>(n_rej) / n_total) : 0.0;

    // Health state machine.
    const bool is_degraded = rej_rate > kDegradedRate;
    const bool is_high_nees = (n_acc > 0) && (nees_mean > kDivergedNees);

    if (is_high_nees) {
      if (diverged_since_ < 0.0) diverged_since_ = current_time_s;
    } else {
      diverged_since_ = -1.0;
    }

    const bool is_diverged =
        (diverged_since_ >= 0.0) && (current_time_s - diverged_since_ >= kDivergedMinSec);

    if (is_diverged) {
      health = kDiverged;
    } else if (is_degraded) {
      health = kDegraded;
    } else {
      health = kOk;
    }
  }

 private:
  struct AcceptSample {
    double time_s;
    double nis;
  };
  struct RejectSample {
    double time_s;
  };

  std::deque<AcceptSample> accepted_;
  std::deque<RejectSample> rejected_;
  double diverged_since_{-1.0};

  void _prune(double current_time_s) {
    const double cutoff = current_time_s - kWindowSec;
    while (!accepted_.empty() && accepted_.front().time_s < cutoff) accepted_.pop_front();
    while (!rejected_.empty() && rejected_.front().time_s < cutoff) rejected_.pop_front();
  }
};

}  // namespace localization
