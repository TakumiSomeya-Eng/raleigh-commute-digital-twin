"""Integration test: headless EKF bag replay (FR-8.3).

Plays tests/fixtures/tiny_day2_60s.mcap through ekf_node and asserts RMSE < threshold.
Implemented in task T2.5.
"""
import pytest

pytestmark = pytest.mark.integration
