// FR-4.2 — Extended Kalman Filter ROS 2 node.
// Subscribes: /gps/fix, /imu/data, /mag
// Publishes:  /fused/odom (100 Hz), /fused/diagnostics (1 Hz)
//
// T2.4 skeleton: parameters declared, subscriptions wired, GPS pass-through odom.
// T2.5 will replace the pass-through with full EKF predict/update.

#include <cmath>
#include <nav_msgs/msg/odometry.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <sensor_msgs/msg/magnetic_field.hpp>
#include <sensor_msgs/msg/nav_sat_fix.hpp>

namespace localization {

// ---------------------------------------------------------------------------
// ENU flat-earth projection  (mirrors Python data_engine/ingest.py §T1.3)
// Anchor: lat0_deg=35.773, lon0_deg=-78.610  (config/data_gen.yaml)
// Valid for corridor spans < 10 km (equirectangular approximation).
// ---------------------------------------------------------------------------
static constexpr double kR_EARTH_M = 6'371'000.0;
static constexpr double kDeg2Rad = M_PI / 180.0;
static constexpr double kLat0_rad = 35.773 * kDeg2Rad;
static constexpr double kLon0_rad = -78.610 * kDeg2Rad;

static void latlon_to_enu(double lat_deg, double lon_deg, double& px_m, double& py_m) {
  const double lat_rad = lat_deg * kDeg2Rad;
  const double lon_rad = lon_deg * kDeg2Rad;
  px_m = (lon_rad - kLon0_rad) * std::cos(kLat0_rad) * kR_EARTH_M;
  py_m = (lat_rad - kLat0_rad) * kR_EARTH_M;
}

// ---------------------------------------------------------------------------
// EkfNode
// ---------------------------------------------------------------------------

class EkfNode : public rclcpp::Node {
 public:
  EkfNode() : Node("ekf_node") {
    declare_params();
    log_params();
    create_subscriptions();
    create_publishers();
    RCLCPP_INFO(get_logger(), "[FR-4.2 ekf] node started (T2.4 pass-through skeleton)");
  }

 private:
  // ---- parameter declaration -----------------------------------------------

  void declare_params() {
    declare_parameter<double>("process_noise.sigma_a_mps2", 1.0);
    declare_parameter<double>("process_noise.sigma_psi_dot_rps", 0.1);
    declare_parameter<double>("measurement_noise.bearing_min_speed_mps", 2.0);
    declare_parameter<double>("measurement_noise.mag_only_fallback_speed_mps", 1.0);
    declare_parameter<double>("outlier_gate.chi2_confidence", 0.99);
    declare_parameter<std::string>("initialization.method", "first_gps");
    declare_parameter<int>("initialization.wait_gps_count", 3);
  }

  void log_params() {
    RCLCPP_INFO(get_logger(), "[FR-4.2 ekf] sigma_a=%.3f  sigma_psi_dot=%.3f",
                get_parameter("process_noise.sigma_a_mps2").as_double(),
                get_parameter("process_noise.sigma_psi_dot_rps").as_double());
    RCLCPP_INFO(get_logger(), "[FR-4.2 ekf] chi2_confidence=%.2f  init=%s  wait_gps=%ld",
                get_parameter("outlier_gate.chi2_confidence").as_double(),
                get_parameter("initialization.method").as_string().c_str(),
                get_parameter("initialization.wait_gps_count").as_int());
  }

  // ---- subscriptions -------------------------------------------------------

  void create_subscriptions() {
    // SENSOR_DATA QoS: best-effort, keep-last 10 (TRD §2.4)
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

  // ---- publishers ----------------------------------------------------------

  void create_publishers() {
    // Reliable, keep-last 100 (TRD §2.4)
    auto odom_qos =
        rclcpp::QoS(rclcpp::KeepLast(100)).reliability(rclcpp::ReliabilityPolicy::Reliable);
    odom_pub_ = create_publisher<nav_msgs::msg::Odometry>("/fused/odom", odom_qos);
  }

  // ---- callbacks -----------------------------------------------------------

  void on_gps(sensor_msgs::msg::NavSatFix::SharedPtr msg) {
    // T2.4 pass-through: project GPS fix to ENU and publish directly.
    // T2.5 will replace this with EKF update + predict.
    double px_m = 0.0;
    double py_m = 0.0;
    latlon_to_enu(msg->latitude, msg->longitude, px_m, py_m);

    auto odom = nav_msgs::msg::Odometry();
    odom.header.stamp = msg->header.stamp;
    odom.header.frame_id = "odom";
    odom.child_frame_id = "base_link";
    odom.pose.pose.position.x = px_m;
    odom.pose.pose.position.y = py_m;
    odom.pose.pose.position.z = 0.0;
    // Orientation identity quaternion (T2.5 will fill from fused psi).
    odom.pose.pose.orientation.w = 1.0;

    // Populate pose covariance from GPS horizontal_accuracy (TRD §2.3).
    const double var_h = msg->position_covariance[0];  // sigma_h^2 from NavSatFix diagonal
    odom.pose.covariance[0] = var_h;                   // x-x
    odom.pose.covariance[7] = var_h;                   // y-y
    odom.pose.covariance[35] = 1e-9;                   // yaw-yaw (unknown at T2.4)

    odom_pub_->publish(odom);
  }

  void on_imu(sensor_msgs::msg::Imu::SharedPtr msg) {
    (void)msg;  // T2.5 will use this for predict step
  }

  void on_mag(sensor_msgs::msg::MagneticField::SharedPtr msg) {
    (void)msg;  // T2.5 will use this for heading initialisation
  }

  // ---- members -------------------------------------------------------------

  rclcpp::Subscription<sensor_msgs::msg::NavSatFix>::SharedPtr gps_sub_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_sub_;
  rclcpp::Subscription<sensor_msgs::msg::MagneticField>::SharedPtr mag_sub_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_pub_;
};

}  // namespace localization

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<localization::EkfNode>());
  rclcpp::shutdown();
  return 0;
}
