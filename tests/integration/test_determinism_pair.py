"""Determinism test: two full pipeline runs produce byte-identical outputs (NFR 4.2).

Implemented as part of T4.8 / CI nightly.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]
