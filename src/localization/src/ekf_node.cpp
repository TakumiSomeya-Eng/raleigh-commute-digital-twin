// FR-4.2 / FR-4.5 — Extended Kalman Filter ROS 2 node.
//
// State vector: [px, py, v, psi, psi_dot]   (ENU metres, m/s, rad, rad/s)
// Predict step : driven by IMU at 100 Hz (CTRV + a_lon control input)
// Update steps : GPS position (2D), GPS speed (scalar), IMU yaw-rate pseudo-measurement
// Diagnostics  : /fused/diagnostics at 1 Hz (FR-4.5)
//
// T2.5: full EKF math
// T2.6: diagnostics topic

#include <Eigen/Core>
#include <Eigen/LU>
#include <cmath>
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <diagnostic_msgs/msg/diagnostic_status.hpp>
#include <diagnostic_msgs/msg/key_value.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <sensor_msgs/msg/magnetic_field.hpp>
#include <sensor_msgs/msg/nav_sat_fix.hpp>
#include <std_msgs/msg/float64.hpp>

#include "localization/chi2_gate.hpp"
#include "localization/ctrv_model.hpp"
#include "localization/diagnostics.hpp"

namespace localization {

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
using Vec5 = Eigen::Matrix<double, 5, 1>;
using Mat5 = Eigen::Matrix<double, 5, 5>;
using Vec2 = Eigen::Matrix<double, 2, 1>;
using Mat2 = Eigen::Matrix<double, 2, 2>;
using Mat25 = Eigen::Matrix<double, 2, 5>;

// ---------------------------------------------------------------------------
// ENU flat-earth projection  (mirrors Python data_engine/ingest.py §T1.3)
// Anchor: lat0_deg=35.773, lon0_deg=-78.610  (config/data_gen.yaml)
// ---------------------------------------------------------------------------
static constexpr double kR_EARTH_M = 6'371'000.0;
static constexpr double kDeg2Rad = M_PI / 180.0;
static constexpr double kLat0_rad = 35.773 * kDeg2Rad;
static constexpr double kLon0_rad = -78.610 * kDeg2Rad;

static void latlon_to_enu(double lat_deg, double lon_deg, double& px_m, double& py_m) {
  px_m = (lon_deg * kDeg2Rad - kLon0_rad) * std::cos(kLat0_rad) * kR_EARTH_M;
  py_m = (lat_deg * kDeg2Rad - kLat0_rad) * kR_EARTH_M;
}

// ---------------------------------------------------------------------------
// Process noise Q  (discrete-time, noise on a_lon and psi_dot rate)
// Q = diag(0, 0, σ_a²·dt², 0, σ_ψ̇²·dt²)
// ---------------------------------------------------------------------------
static Mat5 build_Q(double sigma_a, double sigma_psi_dot, double dt) {
  Mat5 Q = Mat5::Zero();
  Q(kV, kV) = (sigma_a * dt) * (sigma_a * dt);
  Q(kPsiDot, kPsiDot) = (sigma_psi_dot * dt) * (sigma_psi_dot * dt);
  return Q;
}

// ---------------------------------------------------------------------------
// EkfNode
// ---------------------------------------------------------------------------

class EkfNode : public rclcpp::Node {
 public:
  EkfNode() : Node("ekf_node") {
    declare_params();
    load_params();
    log_params();
    create_subscriptions();
    create_publishers();
    RCLCPP_INFO(get_logger(), "[FR-4.2 ekf] node started — waiting for %d GPS fixes",
                wait_gps_count_);
  }

 private:
  // ---- parameters ----------------------------------------------------------

  void declare_params() {
    declare_parameter<double>("process_noise.sigma_a_mps2", 1.0);
    declare_parameter<double>("process_noise.sigma_psi_dot_rps", 0.1);
    declare_parameter<double>("measurement_noise.bearing_min_speed_mps", 2.0);
    declare_parameter<double>("measurement_noise.mag_only_fallback_speed_mps", 1.0);
    declare_parameter<double>("outlier_gate.chi2_confidence", 0.99);
    declare_parameter<std::string>("initialization.method", "first_gps");
    declare_parameter<int>("initialization.wait_gps_count", 3);
  }

  void load_params() {
    sigma_a_ = get_parameter("process_noise.sigma_a_mps2").as_double();
    sigma_psi_dot_ = get_parameter("process_noise.sigma_psi_dot_rps").as_double();
    bearing_min_speed_ = get_parameter("measurement_noise.bearing_min_speed_mps").as_double();
    chi2_confidence_ = get_parameter("outlier_gate.chi2_confidence").as_double();
    wait_gps_count_ = static_cast<int>(get_parameter("initialization.wait_gps_count").as_int());
  }

  void log_params() {
    RCLCPP_INFO(get_logger(), "[FR-4.2 ekf] sigma_a=%.3f  sigma_psi_dot=%.3f", sigma_a_,
                sigma_psi_dot_);
    RCLCPP_INFO(get_logger(), "[FR-4.2 ekf] chi2=%.2f  init=first_gps  wait_gps=%d",
                chi2_confidence_, wait_gps_count_);
  }

  // ---- subscriptions -------------------------------------------------------

  void create_subscriptions() {
    auto sensor_qos =
        rclcpp::QoS(rclcpp::KeepLast(10)).reliability(rclcpp::ReliabilityPolicy::BestEffort);

    gps_sub_ = create_subscription<sensor_msgs::msg::NavSatFix>(
        "/gps/fix", sensor_qos,
        [this](sensor_msgs::msg::NavSatFix::SharedPtr msg) { on_gps(msg); });

    imu_sub_ = create_subscription<sensor_msgs::msg::Imu>(
        "/imu/data", sensor_qos, [this](sensor_msgs::msg::Imu::SharedPtr msg) { on_imu(msg); });

    mag_sub_ = create_subscription<sensor_msgs::msg::MagneticField>(
        "/mag", sensor_qos,
        [this](sensor_msgs::msg::MagneticField::SharedPtr msg) { on_mag(msg); });

    spd_sub_ = create_subscription<std_msgs::msg::Float64>(
        "/gps/speed", sensor_qos, [this](std_msgs::msg::Float64::SharedPtr msg) { on_speed(msg); });
  }

  // ---- publishers and timers -----------------------------------------------

  void create_publishers() {
    auto odom_qos =
        rclcpp::QoS(rclcpp::KeepLast(100)).reliability(rclcpp::ReliabilityPolicy::Reliable);
    odom_pub_ = create_publisher<nav_msgs::msg::Odometry>("/fused/odom", odom_qos);

    auto diag_qos =
        rclcpp::QoS(rclcpp::KeepLast(10)).reliability(rclcpp::ReliabilityPolicy::Reliable);
    diag_pub_ =
        create_publisher<diagnostic_msgs::msg::DiagnosticArray>("/fused/diagnostics", diag_qos);

    diag_timer_ = create_wall_timer(std::chrono::seconds(1), [this]() { publish_diagnostics(); });
  }

  // ---- GPS callback --------------------------------------------------------

  void on_gps(sensor_msgs::msg::NavSatFix::SharedPtr msg) {
    double px = 0.0;
    double py = 0.0;
    latlon_to_enu(msg->latitude, msg->longitude, px, py);

    if (!initialized_) {
      // Accumulate init fixes.
      init_positions_.push_back({px, py});
      if (static_cast<int>(init_positions_.size()) < wait_gps_count_) {
        return;
      }
      initialize_from_gps(px, py, msg->position_covariance[0]);
      return;
    }

    // --- GPS position update (2D) ---
    Vec2 z;
    z << px, py;

    Mat25 H = Mat25::Zero();
    H(0, kPx) = 1.0;
    H(1, kPy) = 1.0;

    const double var_h = msg->position_covariance[0];  // σ_h² from NavSatFix
    Mat2 R = Mat2::Identity() * var_h;

    // Innovation and covariance.
    const Vec2 innov = z - H * x_;
    const Mat2 S = H * P_ * H.transpose() + R;

    const double nis = (innov.transpose() * S.inverse() * innov)(0, 0);
    const double time_s = rclcpp::Time(msg->header.stamp).seconds();

    if (!passes_gate(innov, S, chi2_confidence_)) {
      ++rejection_count_;
      diag_.record_rejected(time_s);
      RCLCPP_DEBUG(get_logger(), "[FR-4.3 gate] GPS rejected  d2=%.1f  total=%d", nis,
                   rejection_count_);
      return;
    }

    diag_.record_accepted(time_s, nis);
    diag_.r_pos_trace = 2.0 * var_h;  // trace of diag(var_h, var_h)

    const Eigen::Matrix<double, 5, 2> K = P_ * H.transpose() * S.inverse();
    x_ += K * innov;
    P_ = (Mat5::Identity() - K * H) * P_;
    x_[kPsi] = std::remainder(x_[kPsi], 2.0 * M_PI);
  }

  // ---- IMU callback --------------------------------------------------------

  void on_imu(sensor_msgs::msg::Imu::SharedPtr msg) {
    const rclcpp::Time stamp(msg->header.stamp);

    if (!initialized_) {
      last_imu_stamp_ = stamp;
      return;
    }

    // --- Predict step ---
    const double dt = (stamp - last_imu_stamp_).seconds();
    last_imu_stamp_ = stamp;

    if (dt <= 0.0 || dt > 0.5) {
      // Skip degenerate dt (out-of-order, first tick, or stale).
      return;
    }

    // Longitudinal accel from forward body-frame component (gravity-removed approx).
    const double a_lon = msg->linear_acceleration.x;

    const Vec5 x_pred = localization::predict(x_, dt, a_lon);
    const Mat5 F = localization::jacobian(x_, dt);
    const Mat5 Q = build_Q(sigma_a_, sigma_psi_dot_, dt);

    x_ = x_pred;
    P_ = F * P_ * F.transpose() + Q;
    diag_.q_trace = Q.trace();

    // --- Yaw-rate pseudo-measurement from gz ---
    const double r_yaw = msg->angular_velocity_covariance[8];  // gz variance (noise_fit YAML)
    if (r_yaw > 0.0) {
      const double z_yaw = msg->angular_velocity.z;

      // H = [0,0,0,0,1]
      Eigen::Matrix<double, 1, 5> H_yaw = Eigen::Matrix<double, 1, 5>::Zero();
      H_yaw(0, kPsiDot) = 1.0;

      const double innov_yaw = z_yaw - x_[kPsiDot];
      const double S_yaw = (H_yaw * P_ * H_yaw.transpose())(0, 0) + r_yaw;

      // 1D chi-squared gate.
      Eigen::VectorXd innov_v(1);
      innov_v(0) = innov_yaw;
      Eigen::MatrixXd S_m(1, 1);
      S_m(0, 0) = S_yaw;
      if (passes_gate(innov_v, S_m, chi2_confidence_)) {
        const double K_yaw_scalar = (P_ * H_yaw.transpose())(kPsiDot, 0) / S_yaw;
        Eigen::Matrix<double, 5, 1> K_yaw = P_ * H_yaw.transpose() / S_yaw;
        x_ += K_yaw * innov_yaw;
        P_ = (Mat5::Identity() - K_yaw * H_yaw) * P_;
        x_[kPsi] = std::remainder(x_[kPsi], 2.0 * M_PI);
        (void)K_yaw_scalar;  // suppress unused-variable warning
      }
    }

    // Clamp speed to non-negative (vehicle only drives forward in this model).
    x_[kV] = std::max(0.0, x_[kV]);

    publish_odom(stamp);
  }

  // ---- GPS speed callback --------------------------------------------------

  void on_speed(std_msgs::msg::Float64::SharedPtr msg) {
    if (!initialized_) return;

    const double v_gps = msg->data;
    if (v_gps < 0.0 || v_gps > 40.0) return;  // sanity guard

    // 1-DOF velocity update: z = v_gps, H = [0 0 1 0 0]
    const double innov_v = v_gps - x_[kV];
    const double s_v = P_(kV, kV) + 1.0;  // R_v = 1.0 m²/s²
    const Vec5 K_v = P_.col(kV) / s_v;
    x_ += K_v * innov_v;
    x_[kV] = std::max(0.0, x_[kV]);
    Eigen::Matrix<double, 5, 5> I_KH = Mat5::Identity();
    I_KH.col(kV) -= K_v;
    P_ = I_KH * P_;
    x_[kPsi] = std::remainder(x_[kPsi], 2.0 * M_PI);
  }

  // ---- MagneticField callback (unused at T2.5) ----------------------------

  void on_mag(sensor_msgs::msg::MagneticField::SharedPtr msg) {
    (void)msg;  // T2.5: magnetometer heading init deferred to T2.7
  }

  // ---- Initialisation ------------------------------------------------------

  void initialize_from_gps(double px, double py, double var_h) {
    // Seed position from last GPS fix; estimate heading from accumulated track.
    x_.setZero();
    x_[kPx] = px;
    x_[kPy] = py;
    x_[kV] = 0.0;

    // Estimate initial heading from first→last accumulated fix direction.
    if (init_positions_.size() >= 2) {
      const auto& p0 = init_positions_.front();
      x_[kPsi] = std::atan2(py - p0[1], px - p0[0]);
    }
    x_[kPsiDot] = 0.0;

    // Initial covariance: tight on position, loose on v/psi/psi_dot.
    P_ = Mat5::Zero();
    P_(kPx, kPx) = var_h;
    P_(kPy, kPy) = var_h;
    P_(kV, kV) = 4.0 * 4.0;        // ±4 m/s
    P_(kPsi, kPsi) = M_PI * M_PI;  // ±π rad
    P_(kPsiDot, kPsiDot) = 0.5 * 0.5;

    initialized_ = true;
    RCLCPP_INFO(get_logger(), "[FR-4.2 ekf] initialized  px=%.1f  py=%.1f  psi=%.3f", x_[kPx],
                x_[kPy], x_[kPsi]);
  }

  // ---- Odometry publishing -------------------------------------------------

  void publish_odom(const rclcpp::Time& stamp) {
    auto odom = nav_msgs::msg::Odometry();
    odom.header.stamp = stamp;
    odom.header.frame_id = "odom";
    odom.child_frame_id = "base_link";

    odom.pose.pose.position.x = x_[kPx];
    odom.pose.pose.position.y = x_[kPy];
    odom.pose.pose.position.z = 0.0;

    // Quaternion from heading psi (yaw-only, roll=pitch=0).
    const double half_psi = x_[kPsi] * 0.5;
    odom.pose.pose.orientation.w = std::cos(half_psi);
    odom.pose.pose.orientation.x = 0.0;
    odom.pose.pose.orientation.y = 0.0;
    odom.pose.pose.orientation.z = std::sin(half_psi);

    // Pose covariance (6×6 row-major: [x,y,z,roll,pitch,yaw]).
    odom.pose.covariance.fill(0.0);
    odom.pose.covariance[0] = P_(kPx, kPx);     // x-x
    odom.pose.covariance[1] = P_(kPx, kPy);     // x-y
    odom.pose.covariance[6] = P_(kPy, kPx);     // y-x
    odom.pose.covariance[7] = P_(kPy, kPy);     // y-y
    odom.pose.covariance[5] = P_(kPx, kPsi);    // x-yaw
    odom.pose.covariance[30] = P_(kPsi, kPx);   // yaw-x
    odom.pose.covariance[11] = P_(kPy, kPsi);   // y-yaw
    odom.pose.covariance[31] = P_(kPsi, kPy);   // yaw-y
    odom.pose.covariance[35] = P_(kPsi, kPsi);  // yaw-yaw
    // Fill remaining diagonal with a small sentinel so downstream code sees valid variances.
    odom.pose.covariance[14] = 1e-9;  // z-z
    odom.pose.covariance[21] = 1e-9;  // roll-roll
    odom.pose.covariance[28] = 1e-9;  // pitch-pitch

    odom.twist.twist.linear.x = x_[kV];
    odom.twist.twist.angular.z = x_[kPsiDot];

    odom_pub_->publish(odom);
  }

  // ---- Diagnostics publishing (1 Hz) ---------------------------------------

  void publish_diagnostics() {
    const double now_s = now().seconds();
    diag_.update(now_s);

    diagnostic_msgs::msg::KeyValue kv;
    std::vector<diagnostic_msgs::msg::KeyValue> values;

    auto make_kv = [](const std::string& key, const std::string& val) {
      diagnostic_msgs::msg::KeyValue kv;
      kv.key = key;
      kv.value = val;
      return kv;
    };

    values.push_back(make_kv("rejection_count", std::to_string(diag_.rejection_count)));
    values.push_back(make_kv("nees_mean", std::to_string(diag_.nees_mean)));
    values.push_back(make_kv("Q_trace", std::to_string(diag_.q_trace)));
    values.push_back(make_kv("R_pos_trace", std::to_string(diag_.r_pos_trace)));
    values.push_back(make_kv("health", diag_.health));

    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "ekf_node";
    status.hardware_id = "";
    status.message = diag_.health;
    if (diag_.health == DiagnosticsState::kOk) {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::OK;
    } else if (diag_.health == DiagnosticsState::kDegraded) {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::WARN;
    } else {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::ERROR;
    }
    status.values = values;

    diagnostic_msgs::msg::DiagnosticArray msg;
    msg.header.stamp = now();
    msg.status.push_back(status);
    diag_pub_->publish(msg);

    (void)kv;
  }

  // ---- members -------------------------------------------------------------

  // Parameters
  double sigma_a_{1.0};
  double sigma_psi_dot_{0.1};
  double bearing_min_speed_{2.0};
  double chi2_confidence_{0.99};
  int wait_gps_count_{3};

  // EKF state
  Vec5 x_{Vec5::Zero()};
  Mat5 P_{Mat5::Identity()};
  bool initialized_{false};
  int rejection_count_{0};

  // Diagnostics
  DiagnosticsState diag_;

  // Initialisation buffer
  std::vector<std::array<double, 2>> init_positions_;

  // Time tracking
  rclcpp::Time last_imu_stamp_{0, 0, RCL_ROS_TIME};

  // ROS handles
  rclcpp::Subscription<sensor_msgs::msg::NavSatFix>::SharedPtr gps_sub_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_sub_;
  rclcpp::Subscription<sensor_msgs::msg::MagneticField>::SharedPtr mag_sub_;
  rclcpp::Subscription<std_msgs::msg::Float64>::SharedPtr spd_sub_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_pub_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diag_pub_;
  rclcpp::TimerBase::SharedPtr diag_timer_;
};

}  // namespace localization

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<localization::EkfNode>());
  rclcpp::shutdown();
  return 0;
}
