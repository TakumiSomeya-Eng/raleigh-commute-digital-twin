// FR-5.2 — Unscented Kalman Filter ROS 2 node.
// State vector: [px, py, v, psi, psi_dot]   (ENU metres, m/s, rad, rad/s)
// Predict step : sigma-point propagation at 100 Hz (CTRV + a_lon control input)
// Update steps : GPS position (2D) and IMU yaw-rate pseudo-measurement
// Diagnostics  : /fused/diagnostics at 1 Hz (FR-4.5)
// Shares CTRVModel and chi2_gate with ekf_node — no duplicate motion code.
//
// T2.7: UKF node

#include <Eigen/Cholesky>
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

#include "localization/chi2_gate.hpp"
#include "localization/ctrv_model.hpp"
#include "localization/diagnostics.hpp"
#include "localization/sigma_points.hpp"

namespace localization {

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
using Vec5 = Eigen::Matrix<double, 5, 1>;
using Mat5 = Eigen::Matrix<double, 5, 5>;
using Vec2 = Eigen::Matrix<double, 2, 1>;
using Mat2 = Eigen::Matrix<double, 2, 2>;

// ---------------------------------------------------------------------------
// ENU flat-earth projection  (matches ekf_node and Python ingest.py)
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
// Process noise Q — identical formulation to ekf_node.
// ---------------------------------------------------------------------------
static Mat5 build_Q(double sigma_a, double sigma_psi_dot, double dt) {
  Mat5 Q = Mat5::Zero();
  Q(kV, kV) = (sigma_a * dt) * (sigma_a * dt);
  Q(kPsiDot, kPsiDot) = (sigma_psi_dot * dt) * (sigma_psi_dot * dt);
  return Q;
}

// ---------------------------------------------------------------------------
// UkfNode
// ---------------------------------------------------------------------------

class UkfNode : public rclcpp::Node {
 public:
  UkfNode() : Node("ukf_node") {
    declare_params();
    load_params();
    log_params();
    create_subscriptions();
    create_publishers();
    RCLCPP_INFO(get_logger(), "[FR-5.2 ukf] node started — waiting for %d GPS fixes",
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
    declare_parameter<double>("sigma_points.alpha", 1e-3);
    declare_parameter<double>("sigma_points.beta", 2.0);
    declare_parameter<double>("sigma_points.kappa", 0.0);
  }

  void load_params() {
    sigma_a_ = get_parameter("process_noise.sigma_a_mps2").as_double();
    sigma_psi_dot_ = get_parameter("process_noise.sigma_psi_dot_rps").as_double();
    bearing_min_speed_ = get_parameter("measurement_noise.bearing_min_speed_mps").as_double();
    chi2_confidence_ = get_parameter("outlier_gate.chi2_confidence").as_double();
    wait_gps_count_ = static_cast<int>(get_parameter("initialization.wait_gps_count").as_int());
    ukf_alpha_ = get_parameter("sigma_points.alpha").as_double();
    ukf_beta_ = get_parameter("sigma_points.beta").as_double();
    ukf_kappa_ = get_parameter("sigma_points.kappa").as_double();
  }

  void log_params() {
    RCLCPP_INFO(get_logger(), "[FR-5.2 ukf] sigma_a=%.3f  sigma_psi_dot=%.3f", sigma_a_,
                sigma_psi_dot_);
    RCLCPP_INFO(get_logger(), "[FR-5.2 ukf] alpha=%.4g  beta=%.1f  kappa=%.1f", ukf_alpha_,
                ukf_beta_, ukf_kappa_);
    RCLCPP_INFO(get_logger(), "[FR-5.2 ukf] chi2=%.2f  init=first_gps  wait_gps=%d",
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
      init_positions_.push_back({px, py});
      if (static_cast<int>(init_positions_.size()) < wait_gps_count_) {
        return;
      }
      initialize_from_gps(px, py, msg->position_covariance[0]);
      return;
    }

    // --- UKF GPS position update (2D) ---
    const double var_h = msg->position_covariance[0];
    const Mat2 R = Mat2::Identity() * var_h;

    // Generate sigma points from current estimate.
    const SigmaPoints sp = generate(x_, P_, ukf_alpha_, ukf_beta_, ukf_kappa_);

    // Project sigma points to measurement space (px, py).
    Eigen::Matrix<double, 2, kNumSigma> Z;
    for (int i = 0; i < kNumSigma; ++i) {
      Z(0, i) = sp.pts(kPx, i);
      Z(1, i) = sp.pts(kPy, i);
    }

    // Predicted measurement mean.
    Vec2 z_pred = Vec2::Zero();
    for (int i = 0; i < kNumSigma; ++i) {
      z_pred += sp.Wm(i) * Z.col(i);
    }

    // Innovation covariance S and cross-covariance Pxy.
    Mat2 S = R;
    Eigen::Matrix<double, 5, 2> Pxy = Eigen::Matrix<double, 5, 2>::Zero();
    for (int i = 0; i < kNumSigma; ++i) {
      const Vec2 dz = Z.col(i) - z_pred;
      const Vec5 dx = sp.pts.col(i) - x_;
      S += sp.Wc(i) * dz * dz.transpose();
      Pxy += sp.Wc(i) * dx * dz.transpose();
    }

    const Vec2 innov = Vec2{px, py} - z_pred;
    const double nis = (innov.transpose() * S.inverse() * innov)(0, 0);
    const double time_s = rclcpp::Time(msg->header.stamp).seconds();

    if (!passes_gate(innov, S, chi2_confidence_)) {
      ++rejection_count_;
      diag_.record_rejected(time_s);
      RCLCPP_DEBUG(get_logger(), "[FR-5.2 gate] GPS rejected  d2=%.1f  total=%d", nis,
                   rejection_count_);
      return;
    }

    diag_.record_accepted(time_s, nis);
    diag_.r_pos_trace = 2.0 * var_h;

    // Kalman update.
    const Eigen::Matrix<double, 5, 2> K = Pxy * S.inverse();
    x_ += K * innov;
    P_ -= K * S * K.transpose();
    x_[kPsi] = std::remainder(x_[kPsi], 2.0 * M_PI);
  }

  // ---- IMU callback --------------------------------------------------------

  void on_imu(sensor_msgs::msg::Imu::SharedPtr msg) {
    const rclcpp::Time stamp(msg->header.stamp);

    if (!initialized_) {
      last_imu_stamp_ = stamp;
      return;
    }

    const double dt = (stamp - last_imu_stamp_).seconds();
    last_imu_stamp_ = stamp;

    if (dt <= 0.0 || dt > 0.5) {
      return;
    }

    const double a_lon = msg->linear_acceleration.x;

    // --- UKF predict step ---
    // Generate sigma points, propagate through CTRV, reconstruct.
    const SigmaPoints sp = generate(x_, P_, ukf_alpha_, ukf_beta_, ukf_kappa_);

    SigmaPoints sp_pred = sp;
    for (int i = 0; i < kNumSigma; ++i) {
      sp_pred.pts.col(i) = localization::predict(sp.pts.col(i), dt, a_lon);
    }

    x_ = weighted_mean(sp_pred);
    x_[kPsi] = std::remainder(x_[kPsi], 2.0 * M_PI);
    const Mat5 Q = build_Q(sigma_a_, sigma_psi_dot_, dt);
    P_ = weighted_cov(sp_pred, x_) + Q;
    diag_.q_trace = Q.trace();

    // --- Yaw-rate pseudo-measurement from gz (linear — identical to EKF) ---
    const double r_yaw = msg->angular_velocity_covariance[8];
    if (r_yaw > 0.0) {
      const double z_yaw = msg->angular_velocity.z;

      Eigen::Matrix<double, 1, 5> H_yaw = Eigen::Matrix<double, 1, 5>::Zero();
      H_yaw(0, kPsiDot) = 1.0;

      const double innov_yaw = z_yaw - x_[kPsiDot];
      const double S_yaw = (H_yaw * P_ * H_yaw.transpose())(0, 0) + r_yaw;

      Eigen::VectorXd innov_v(1);
      innov_v(0) = innov_yaw;
      Eigen::MatrixXd S_m(1, 1);
      S_m(0, 0) = S_yaw;
      if (passes_gate(innov_v, S_m, chi2_confidence_)) {
        const Vec5 K_yaw = P_ * H_yaw.transpose() / S_yaw;
        x_ += K_yaw * innov_yaw;
        P_ = (Mat5::Identity() - K_yaw * H_yaw) * P_;
        x_[kPsi] = std::remainder(x_[kPsi], 2.0 * M_PI);
      }
    }

    x_[kV] = std::max(0.0, x_[kV]);

    publish_odom(stamp);
  }

  // ---- MagneticField callback (unused at T2.7) -----------------------------

  void on_mag(sensor_msgs::msg::MagneticField::SharedPtr msg) { (void)msg; }

  // ---- Initialisation ------------------------------------------------------

  void initialize_from_gps(double px, double py, double var_h) {
    x_.setZero();
    x_[kPx] = px;
    x_[kPy] = py;
    x_[kV] = 0.0;

    if (init_positions_.size() >= 2) {
      const auto& p0 = init_positions_.front();
      x_[kPsi] = std::atan2(py - p0[1], px - p0[0]);
    }
    x_[kPsiDot] = 0.0;

    P_ = Mat5::Zero();
    P_(kPx, kPx) = var_h;
    P_(kPy, kPy) = var_h;
    P_(kV, kV) = 4.0 * 4.0;
    P_(kPsi, kPsi) = M_PI * M_PI;
    P_(kPsiDot, kPsiDot) = 0.5 * 0.5;

    initialized_ = true;
    RCLCPP_INFO(get_logger(), "[FR-5.2 ukf] initialized  px=%.1f  py=%.1f  psi=%.3f", x_[kPx],
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

    const double half_psi = x_[kPsi] * 0.5;
    odom.pose.pose.orientation.w = std::cos(half_psi);
    odom.pose.pose.orientation.x = 0.0;
    odom.pose.pose.orientation.y = 0.0;
    odom.pose.pose.orientation.z = std::sin(half_psi);

    odom.pose.covariance.fill(0.0);
    odom.pose.covariance[0] = P_(kPx, kPx);
    odom.pose.covariance[1] = P_(kPx, kPy);
    odom.pose.covariance[6] = P_(kPy, kPx);
    odom.pose.covariance[7] = P_(kPy, kPy);
    odom.pose.covariance[5] = P_(kPx, kPsi);
    odom.pose.covariance[30] = P_(kPsi, kPx);
    odom.pose.covariance[11] = P_(kPy, kPsi);
    odom.pose.covariance[31] = P_(kPsi, kPy);
    odom.pose.covariance[35] = P_(kPsi, kPsi);
    odom.pose.covariance[14] = 1e-9;
    odom.pose.covariance[21] = 1e-9;
    odom.pose.covariance[28] = 1e-9;

    odom.twist.twist.linear.x = x_[kV];
    odom.twist.twist.angular.z = x_[kPsiDot];

    odom_pub_->publish(odom);
  }

  // ---- Diagnostics publishing (1 Hz) ---------------------------------------

  void publish_diagnostics() {
    const double now_s = now().seconds();
    diag_.update(now_s);

    auto make_kv = [](const std::string& key, const std::string& val) {
      diagnostic_msgs::msg::KeyValue kv;
      kv.key = key;
      kv.value = val;
      return kv;
    };

    std::vector<diagnostic_msgs::msg::KeyValue> values;
    values.push_back(make_kv("rejection_count", std::to_string(diag_.rejection_count)));
    values.push_back(make_kv("nees_mean", std::to_string(diag_.nees_mean)));
    values.push_back(make_kv("Q_trace", std::to_string(diag_.q_trace)));
    values.push_back(make_kv("R_pos_trace", std::to_string(diag_.r_pos_trace)));
    values.push_back(make_kv("health", diag_.health));

    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "ukf_node";
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

    diagnostic_msgs::msg::DiagnosticArray diag_msg;
    diag_msg.header.stamp = now();
    diag_msg.status.push_back(status);
    diag_pub_->publish(diag_msg);
  }

  // ---- members -------------------------------------------------------------

  // Parameters
  double sigma_a_{1.0};
  double sigma_psi_dot_{0.1};
  double bearing_min_speed_{2.0};
  double chi2_confidence_{0.99};
  int wait_gps_count_{3};
  double ukf_alpha_{1e-3};
  double ukf_beta_{2.0};
  double ukf_kappa_{0.0};

  // UKF state
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
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_pub_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diag_pub_;
  rclcpp::TimerBase::SharedPtr diag_timer_;
};

}  // namespace localization

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<localization::UkfNode>());
  rclcpp::shutdown();
  return 0;
}
