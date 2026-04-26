# FR-4 / FR-5 — localization package build configuration.
# Implemented in task T2.2 (initial), T2.4 (ekf_node), T2.7 (ukf_node).
cmake_minimum_required(VERSION 3.22)
project(localization)

find_package(ament_cmake REQUIRED)
find_package(Eigen3 REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(diagnostic_msgs REQUIRED)

# ---------------------------------------------------------------------------
# Header-only interface libraries (shared by ekf_node, ukf_node, tests)
# ---------------------------------------------------------------------------

add_library(ctrv_model INTERFACE)
target_include_directories(ctrv_model INTERFACE
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_link_libraries(ctrv_model INTERFACE Eigen3::Eigen)
target_compile_features(ctrv_model INTERFACE cxx_std_17)

add_library(chi2_gate INTERFACE)
target_include_directories(chi2_gate INTERFACE
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_link_libraries(chi2_gate INTERFACE Eigen3::Eigen)
target_compile_features(chi2_gate INTERFACE cxx_std_17)

# ---------------------------------------------------------------------------
# ekf_node executable
# ---------------------------------------------------------------------------

add_executable(ekf_node src/ekf_node.cpp)
target_include_directories(ekf_node PRIVATE
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
target_compile_features(ekf_node PRIVATE cxx_std_17)
ament_target_dependencies(ekf_node rclcpp sensor_msgs nav_msgs diagnostic_msgs)
target_link_libraries(ekf_node ctrv_model chi2_gate)

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

install(TARGETS ekf_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY launch/ DESTINATION share/${PROJECT_NAME}/launch)
install(FILES
  ${CMAKE_SOURCE_DIR}/../../config/ekf.yaml
  DESTINATION share/${PROJECT_NAME}/config)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_ctrv_model test/test_ctrv_model.cpp)
  target_link_libraries(test_ctrv_model ctrv_model)
  ament_add_gtest(test_chi2_gate test/test_chi2_gate.cpp)
  target_link_libraries(test_chi2_gate chi2_gate)
  ament_add_gtest(test_diagnostics test/test_diagnostics.cpp)
  target_include_directories(test_diagnostics PRIVATE
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
endif()

ament_package()
