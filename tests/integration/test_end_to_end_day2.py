"""End-to-end smoke test: full pipeline on day2 (T4.8).

Runs all make targets in order on day2 and asserts score.json is valid.
Implemented in task T4.8.
"""
import pytest

pytestmark = pytest.mark.integration
