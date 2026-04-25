# FR-4 / FR-5 — localization package build configuration.
# Implemented in task T2.2 (initial), T2.4 (ekf_node), T2.7 (ukf_node).
cmake_minimum_required(VERSION 3.22)
project(localization)

find_package(ament_cmake REQUIRED)
find_package(Eigen3 REQUIRED)

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

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_ctrv_model test/test_ctrv_model.cpp)
  target_link_libraries(test_ctrv_model ctrv_model)
  ament_add_gtest(test_chi2_gate test/test_chi2_gate.cpp)
  target_link_libraries(test_chi2_gate chi2_gate)
endif()

ament_package()
