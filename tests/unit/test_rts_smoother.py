"""Unit tests for FR-6.1 RTS smoother (T3.1).

Known-answer test: synthesize a straight-line trajectory with GPS noise;
verify the smoother recovers the true position within a tight bound.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from evaluation.rts_smoother import _backward, _ctrv_jacobian, _ctrv_predict, _forward

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _straight_line_df(
    n: int = 300,
    dt: float = 0.01,
    v: float = 10.0,
    noise_std: float = 3.0,
    seed: int = 0,
    gps_stride: int = 100,
) -> pd.DataFrame:
    """Synthetic straight-line trip (heading=0, constant speed).

    GPS rows every *gps_stride* ticks; GPS positions corrupted by Gaussian noise.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n) * dt
    px_true = v * t
    py_true = np.zeros(n)
    gz_true = np.zeros(n)

    # GPS every gps_stride ticks
    gps_interp = np.ones(n, dtype=bool)
    gps_interp[::gps_stride] = False

    px_meas = px_true.copy()
    py_meas = py_true.copy()
    px_meas[~gps_interp] += rng.normal(0, noise_std, (~gps_interp).sum())
    py_meas[~gps_interp] += rng.normal(0, noise_std, (~gps_interp).sum())

    return pd.DataFrame(
        {
            "t_s": t,
            "px_m": px_meas,
            "py_m": py_meas,
            "gps_speed_mps": np.full(n, v),
            "gps_bearing_deg": np.zeros(n),
            "horizontal_accuracy_m": np.full(n, noise_std),
            "gz_rps": gz_true + rng.normal(0, 0.01, n),
            "gps_interpolated": gps_interp,
        }
    )


# ---------------------------------------------------------------------------
# Tests: CTRV model
# ---------------------------------------------------------------------------


class TestCTRV:
    def test_straight_predict(self):
        x = np.array([0.0, 0.0, 10.0, 0.0, 0.0])
        xn = _ctrv_predict(x, 1.0)
        assert abs(xn[0] - 10.0) < 1e-9
        assert abs(xn[1]) < 1e-9

    def test_circular_predict(self):
        # Quarter circle: omega=pi/2 rad/s, v=10, t=1 -> moved 90 deg
        x = np.array([0.0, 0.0, 10.0, 0.0, math.pi / 2])
        xn = _ctrv_predict(x, 1.0)
        # Should be near (r, r) where r = v/omega = 20/pi
        r = 10.0 / (math.pi / 2)
        assert abs(xn[0] - r) < 0.01
        assert abs(xn[1] - r) < 0.01

    def test_jacobian_numerical(self):
        x = np.array([1.0, 2.0, 8.0, 0.3, 0.2])
        dt = 0.01
        F = _ctrv_jacobian(x, dt)
        eps = 1e-6
        F_num = np.zeros((5, 5))
        for j in range(5):
            xp = x.copy()
            xp[j] += eps
            F_num[:, j] = (_ctrv_predict(xp, dt) - _ctrv_predict(x, dt)) / eps
        np.testing.assert_allclose(F, F_num, atol=1e-5)

    def test_jacobian_zero_omega(self):
        x = np.array([0.0, 0.0, 5.0, 1.0, 0.0])
        F = _ctrv_jacobian(x, 0.1)
        assert F.shape == (5, 5)
        assert abs(F[3, 4] - 0.1) < 1e-9


# ---------------------------------------------------------------------------
# Tests: Forward / backward smoother
# ---------------------------------------------------------------------------


class TestSmoother:
    def setup_method(self):
        # GPS every 10 ticks (0.1 s) gives enough observations for the smoother
        self.df = _straight_line_df(n=500, noise_std=2.0, gps_stride=10)

    def test_forward_returns_correct_shapes(self):
        xs, Ps, xp, Pp, Fs = _forward(self.df)
        T = len(self.df)
        assert xs.shape == (T, 5)
        assert Ps.shape == (T, 5, 5)
        assert xp.shape == (T, 5)
        assert Pp.shape == (T, 5, 5)
        assert Fs.shape == (T, 5, 5)

    def test_forward_no_nan(self):
        xs, Ps, xp, Pp, Fs = _forward(self.df)
        assert not np.isnan(xs).any()
        assert not np.isnan(Ps).any()

    def test_backward_no_nan(self):
        xs, Ps, xp, Pp, Fs = _forward(self.df)
        x_s, P_s = _backward(xs, Ps, xp, Pp, Fs)
        assert not np.isnan(x_s).any()

    def test_smoother_reduces_position_error(self):
        """Smoother RMSE <= forward-filter RMSE on a synthetic straight trip."""
        v = 10.0
        dt = 0.01
        n = len(self.df)
        t = np.arange(n) * dt
        px_true = v * t
        py_true = np.zeros(n)

        xs, Ps, xp, Pp, Fs = _forward(self.df)
        x_s, _ = _backward(xs, Ps, xp, Pp, Fs)

        rmse_fwd = float(np.sqrt(np.mean((xs[:, 0] - px_true) ** 2 + (xs[:, 1] - py_true) ** 2)))
        rmse_rts = float(np.sqrt(np.mean((x_s[:, 0] - px_true) ** 2 + (x_s[:, 1] - py_true) ** 2)))

        assert rmse_rts <= rmse_fwd, (
            f"Smoother should be at least as good as forward filter: "
            f"rts={rmse_rts:.3f} > fwd={rmse_fwd:.3f}"
        )

    def test_smoother_recovers_within_bound(self):
        """Smoother recovers true trajectory within a loose bound."""
        # Dense GPS (every 10 ticks = every 0.1 s) so the smoother gets enough obs
        df = _straight_line_df(n=1000, noise_std=2.0, gps_stride=10)
        v = 10.0
        dt = 0.01
        n = len(df)
        t = np.arange(n) * dt
        px_true = v * t

        xs, Ps, xp, Pp, Fs = _forward(df)
        x_s, _ = _backward(xs, Ps, xp, Pp, Fs)

        rmse = float(np.sqrt(np.mean((x_s[:, 0] - px_true) ** 2)))
        # With noise_std=2.0 and GPS every 0.1 s, smoother should recover < noise_std
        assert rmse < 2.5, f"RTS RMSE too high: {rmse:.3f} m"

    def test_psi_normalized(self):
        xs, Ps, xp, Pp, Fs = _forward(self.df)
        x_s, _ = _backward(xs, Ps, xp, Pp, Fs)
        psi = x_s[:, 3]
        assert (psi >= -math.pi - 1e-9).all()
        assert (psi <= math.pi + 1e-9).all()
