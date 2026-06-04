"""Unit tests for data_engine.sumo_adapter (T8.5 — TDD).

All contracts are derived directly from sumo_adapter_spec.py.
No implementation details are assumed beyond what the spec states.

Test groups
-----------
1.  parse_fcd — returns correct columns and dtypes
2.  parse_fcd — t_s contract (starts at 0, monotonically increasing)
3.  parse_fcd — speed_mps contract (always >= 0)
4.  parse_fcd — bearing contract (always in [0, 360))
5.  parse_fcd — error: file not found
6.  parse_fcd — error: empty FCD (no <vehicle> elements)
7.  parse_fcd — error: missing required XML attributes
8.  add_noise — output shape equals input shape
9.  add_noise — speed_mps remains >= 0 after noise
10. add_noise — bearing remains in [0, 360) after noise
11. add_noise — deterministic with fixed seed
12. add_noise — different seeds produce different outputs
13. add_noise — GPS 3-sigma rule (99.7 % of errors within 3 * gps_sigma metres)
14. to_sensor_logger_csvs — exactly seven files written
15. to_sensor_logger_csvs — return dict keys match SENSOR_LOGGER_FILES exactly
16. to_sensor_logger_csvs — out_dir is created when absent
17. to_sensor_logger_csvs — Location.csv: time is int64 epoch nanoseconds
18. to_sensor_logger_csvs — Location.csv: speed column is in m/s (non-negative)
19. to_sensor_logger_csvs — Location.csv: bearing equals bearing from fcd_df
20. to_sensor_logger_csvs — Location.csv: horizontalAccuracy matches style
21. to_sensor_logger_csvs — Accelerometer.csv: columns are time, x, y, z
22. to_sensor_logger_csvs — Gyroscope.csv: columns are time, x, y, z
23. to_sensor_logger_csvs — Gravity.csv: x=0.0, y=0.0, z=-9.81 for every row
24. to_sensor_logger_csvs — Orientation.csv: has qw, qx, qy, qz columns
25. to_sensor_logger_csvs — Magnetometer.csv: columns are time, x, y, z
26. to_sensor_logger_csvs — TotalAcceleration.csv: vector sum of Accel + Gravity
27. to_sensor_logger_csvs — all time columns are int64
28. to_sensor_logger_csvs — all time columns are strictly increasing
29. convert — equivalent to parse_fcd → add_noise → to_sensor_logger_csvs
30. convert — produces all seven files for each driving style
31. convert — deterministic with fixed seed
"""

from __future__ import annotations

import textwrap
import typing
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from data_engine.sumo_adapter import (
    NOISE_SIGMAS,
    SENSOR_LOGGER_FILES,
    add_noise,
    convert,
    parse_fcd,
    to_sensor_logger_csvs,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sumo"
_CALM_FCD = _FIXTURE_DIR / "tiny_calm_30s.xml"
_NORMAL_FCD = _FIXTURE_DIR / "tiny_normal_30s.xml"
_AGGRESSIVE_FCD = _FIXTURE_DIR / "tiny_aggressive_30s.xml"

_STYLES = ["calm", "normal", "aggressive"]
_STYLE_FCD: dict[str, Path] = {
    "calm": _CALM_FCD,
    "normal": _NORMAL_FCD,
    "aggressive": _AGGRESSIVE_FCD,
}

# ---------------------------------------------------------------------------
# Minimal in-memory FCD XML helpers (for error-case tests only)
# ---------------------------------------------------------------------------

_VALID_FCD_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <fcd-export>
      <timestep time="0.00">
        <vehicle id="v0" x="-78.620" y="35.770" speed="8.0" angle="45.0"
                 pos="0.0" lane="E1_0"/>
      </timestep>
      <timestep time="1.00">
        <vehicle id="v0" x="-78.619" y="35.771" speed="8.5" angle="46.0"
                 pos="8.0" lane="E1_0"/>
      </timestep>
    </fcd-export>
""")

_EMPTY_FCD_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <fcd-export>
      <timestep time="0.00"/>
      <timestep time="1.00"/>
    </fcd-export>
""")

_MISSING_SPEED_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <fcd-export>
      <timestep time="0.00">
        <vehicle id="v0" x="-78.620" y="35.770" angle="45.0"
                 pos="0.0" lane="E1_0"/>
      </timestep>
    </fcd-export>
""")

_MISSING_X_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <fcd-export>
      <timestep time="0.00">
        <vehicle id="v0" y="35.770" speed="8.0" angle="45.0"
                 pos="0.0" lane="E1_0"/>
      </timestep>
    </fcd-export>
""")


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def calm_df() -> pd.DataFrame:
    return parse_fcd(_CALM_FCD)


@pytest.fixture(scope="module")
def normal_df() -> pd.DataFrame:
    return parse_fcd(_NORMAL_FCD)


@pytest.fixture(scope="module")
def aggressive_df() -> pd.DataFrame:
    return parse_fcd(_AGGRESSIVE_FCD)


@pytest.fixture()
def noisy_normal(normal_df: pd.DataFrame) -> pd.DataFrame:
    return add_noise(normal_df, "normal", seed=42)


# ---------------------------------------------------------------------------
# 1. parse_fcd — returns correct columns and dtypes
# ---------------------------------------------------------------------------


class TestParseFcdColumns:
    _REQUIRED: typing.ClassVar[set[str]] = {"t_s", "lon", "lat", "speed_mps", "bearing"}

    def test_required_columns_present(self, calm_df: pd.DataFrame) -> None:
        assert self._REQUIRED.issubset(calm_df.columns)

    def test_t_s_is_float(self, calm_df: pd.DataFrame) -> None:
        assert calm_df["t_s"].dtype.kind == "f"

    def test_lat_is_float(self, calm_df: pd.DataFrame) -> None:
        assert calm_df["lat"].dtype.kind == "f"

    def test_lon_is_float(self, calm_df: pd.DataFrame) -> None:
        assert calm_df["lon"].dtype.kind == "f"

    def test_speed_mps_is_float(self, calm_df: pd.DataFrame) -> None:
        assert calm_df["speed_mps"].dtype.kind == "f"

    def test_bearing_is_float(self, calm_df: pd.DataFrame) -> None:
        assert calm_df["bearing"].dtype.kind == "f"

    def test_row_count_matches_timesteps(self, calm_df: pd.DataFrame) -> None:
        # tiny_calm_30s.xml has 31 timesteps (t=0..30, step=1 s)
        assert len(calm_df) == 31


# ---------------------------------------------------------------------------
# 2. parse_fcd — t_s contract
# ---------------------------------------------------------------------------


class TestParseFcdTsContract:
    def test_t_s_starts_at_zero(self, calm_df: pd.DataFrame) -> None:
        assert calm_df["t_s"].iloc[0] == pytest.approx(0.0)

    def test_t_s_monotonically_increasing(self, calm_df: pd.DataFrame) -> None:
        diffs = calm_df["t_s"].diff().dropna()
        assert (diffs > 0).all()

    @pytest.mark.parametrize("style", _STYLES)
    def test_t_s_starts_at_zero_all_styles(self, style: str) -> None:
        df = parse_fcd(_STYLE_FCD[style])
        assert df["t_s"].iloc[0] == pytest.approx(0.0)

    @pytest.mark.parametrize("style", _STYLES)
    def test_t_s_monotonic_all_styles(self, style: str) -> None:
        df = parse_fcd(_STYLE_FCD[style])
        assert (df["t_s"].diff().dropna() > 0).all()


# ---------------------------------------------------------------------------
# 3. parse_fcd — speed_mps contract
# ---------------------------------------------------------------------------


class TestParseFcdSpeedContract:
    @pytest.mark.parametrize("style", _STYLES)
    def test_speed_mps_non_negative(self, style: str) -> None:
        df = parse_fcd(_STYLE_FCD[style])
        assert (df["speed_mps"] >= 0).all()

    def test_speed_mps_values_are_reasonable(self, normal_df: pd.DataFrame) -> None:
        # Road speeds: < 50 m/s (~180 km/h)
        assert (normal_df["speed_mps"] < 50).all()


# ---------------------------------------------------------------------------
# 4. parse_fcd — bearing contract
# ---------------------------------------------------------------------------


class TestParseFcdBearingContract:
    @pytest.mark.parametrize("style", _STYLES)
    def test_bearing_in_half_open_range(self, style: str) -> None:
        df = parse_fcd(_STYLE_FCD[style])
        assert (df["bearing"] >= 0.0).all()
        assert (df["bearing"] < 360.0).all()

    def test_bearing_near_zero_wraps_correctly(self, aggressive_df: pd.DataFrame) -> None:
        # tiny_aggressive_30s.xml contains angle=359.0 and 358.0 — must stay < 360
        assert (aggressive_df["bearing"] < 360.0).all()
        assert (aggressive_df["bearing"] >= 0.0).all()


# ---------------------------------------------------------------------------
# 5. parse_fcd — error: file not found
# ---------------------------------------------------------------------------


class TestParseFcdFileNotFound:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_fcd(tmp_path / "does_not_exist.xml")


# ---------------------------------------------------------------------------
# 6. parse_fcd — error: empty FCD
# ---------------------------------------------------------------------------


class TestParseFcdEmptyFcd:
    def test_raises_value_error_on_empty_fcd(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "empty.xml", _EMPTY_FCD_XML)
        with pytest.raises(ValueError):
            parse_fcd(p)


# ---------------------------------------------------------------------------
# 7. parse_fcd — error: missing required XML attributes
# ---------------------------------------------------------------------------


class TestParseFcdMissingAttributes:
    def test_raises_on_missing_speed(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "no_speed.xml", _MISSING_SPEED_XML)
        with pytest.raises(ValueError):
            parse_fcd(p)

    def test_raises_on_missing_x(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "no_x.xml", _MISSING_X_XML)
        with pytest.raises(ValueError):
            parse_fcd(p)


# ---------------------------------------------------------------------------
# 8. add_noise — output shape equals input shape
# ---------------------------------------------------------------------------


class TestAddNoiseShape:
    @pytest.mark.parametrize("style", _STYLES)
    def test_shape_preserved(self, normal_df: pd.DataFrame, style: str) -> None:
        noisy = add_noise(normal_df, style, seed=0)
        assert noisy.shape == normal_df.shape

    def test_column_names_preserved(self, normal_df: pd.DataFrame) -> None:
        noisy = add_noise(normal_df, "normal", seed=0)
        assert set(noisy.columns) == set(normal_df.columns)


# ---------------------------------------------------------------------------
# 9. add_noise — speed_mps remains >= 0 (zero-clipped)
# ---------------------------------------------------------------------------


class TestAddNoiseSpeedClip:
    @pytest.mark.parametrize("style", _STYLES)
    def test_speed_non_negative_after_noise(self, normal_df: pd.DataFrame, style: str) -> None:
        noisy = add_noise(normal_df, style, seed=0)
        assert (noisy["speed_mps"] >= 0).all()


# ---------------------------------------------------------------------------
# 10. add_noise — bearing remains in [0, 360) after noise
# ---------------------------------------------------------------------------


class TestAddNoiseBearingRange:
    @pytest.mark.parametrize("style", _STYLES)
    def test_bearing_in_range_after_noise(self, normal_df: pd.DataFrame, style: str) -> None:
        noisy = add_noise(normal_df, style, seed=0)
        assert (noisy["bearing"] >= 0.0).all()
        assert (noisy["bearing"] < 360.0).all()


# ---------------------------------------------------------------------------
# 11. add_noise — deterministic with fixed seed
# ---------------------------------------------------------------------------


class TestAddNoiseDeterminism:
    def test_same_seed_same_output(self, normal_df: pd.DataFrame) -> None:
        a = add_noise(normal_df, "normal", seed=99)
        b = add_noise(normal_df, "normal", seed=99)
        pd.testing.assert_frame_equal(a, b)

    def test_same_seed_calm_vs_aggressive_differ(self, normal_df: pd.DataFrame) -> None:
        calm = add_noise(normal_df, "calm", seed=0)
        agg = add_noise(normal_df, "aggressive", seed=0)
        # Different sigmas must produce different lat distributions
        assert not calm["lat"].equals(agg["lat"])


# ---------------------------------------------------------------------------
# 12. add_noise — different seeds produce different outputs
# ---------------------------------------------------------------------------


class TestAddNoiseSeedVariation:
    def test_different_seeds_differ(self, normal_df: pd.DataFrame) -> None:
        a = add_noise(normal_df, "normal", seed=1)
        b = add_noise(normal_df, "normal", seed=2)
        assert not a["lat"].equals(b["lat"])


# ---------------------------------------------------------------------------
# 13. add_noise — GPS 3-sigma rule
#
# The spec states: "99.7 % of GPS position errors are within 3 * gps_sigma metres".
# We test with the larger normal/aggressive fixtures to get meaningful statistics,
# and use seed=0 so the test is reproducible.
# With n=31 rows a single draw cannot yield stable statistics; the contract is
# therefore verified over 1 000 independent noise applications (Monte-Carlo).
# ---------------------------------------------------------------------------


class TestAddNoiseGps3Sigma:
    _DEG_PER_M = 1.0 / 111_320.0
    _N_TRIALS = 1_000

    @pytest.mark.parametrize("style", _STYLES)
    def test_gps_lat_error_within_3sigma(self, normal_df: pd.DataFrame, style: str) -> None:
        sigma_m = NOISE_SIGMAS[style]["gps_m"]
        sigma_deg = sigma_m * self._DEG_PER_M
        rng = np.random.default_rng(0)

        lat_errors: list[float] = []
        for _i in range(self._N_TRIALS):
            noisy = add_noise(normal_df, style, seed=int(rng.integers(0, 2**31)))
            lat_errors.extend((noisy["lat"] - normal_df["lat"]).abs().tolist())

        errors = np.array(lat_errors)
        pct_within = float((errors <= 3 * sigma_deg).mean())
        # Allow a small margin below the theoretical 99.7 %
        assert (
            pct_within >= 0.994
        ), f"{style} lat: {pct_within:.4f} of errors within 3-sigma (expected >= 0.994)"

    @pytest.mark.parametrize("style", _STYLES)
    def test_gps_lon_error_within_3sigma(self, normal_df: pd.DataFrame, style: str) -> None:
        sigma_m = NOISE_SIGMAS[style]["gps_m"]
        sigma_deg = sigma_m * self._DEG_PER_M
        rng = np.random.default_rng(1)

        lon_errors: list[float] = []
        for _i in range(self._N_TRIALS):
            noisy = add_noise(normal_df, style, seed=int(rng.integers(0, 2**31)))
            lon_errors.extend((noisy["lon"] - normal_df["lon"]).abs().tolist())

        errors = np.array(lon_errors)
        pct_within = float((errors <= 3 * sigma_deg).mean())
        assert (
            pct_within >= 0.994
        ), f"{style} lon: {pct_within:.4f} of errors within 3-sigma (expected >= 0.994)"


# ---------------------------------------------------------------------------
# 14-16. to_sensor_logger_csvs - file existence and directory creation
# ---------------------------------------------------------------------------


class TestCsvFileExistence:
    @pytest.mark.parametrize("style", _STYLES)
    def test_exactly_seven_files_written(
        self, normal_df: pd.DataFrame, style: str, tmp_path: Path
    ) -> None:
        out = tmp_path / style
        to_sensor_logger_csvs(normal_df, style, out)
        written = [p for p in out.iterdir() if p.suffix == ".csv"]
        assert len(written) == 7

    @pytest.mark.parametrize("style", _STYLES)
    def test_return_keys_match_sensor_logger_files(
        self, normal_df: pd.DataFrame, style: str, tmp_path: Path
    ) -> None:
        paths = to_sensor_logger_csvs(normal_df, style, tmp_path / style)
        assert set(paths.keys()) == set(SENSOR_LOGGER_FILES)

    @pytest.mark.parametrize("style", _STYLES)
    def test_all_returned_paths_exist(
        self, normal_df: pd.DataFrame, style: str, tmp_path: Path
    ) -> None:
        paths = to_sensor_logger_csvs(normal_df, style, tmp_path / style)
        for fname, p in paths.items():
            assert p.exists(), f"{fname} was not written"

    def test_out_dir_created_when_absent(self, normal_df: pd.DataFrame, tmp_path: Path) -> None:
        out = tmp_path / "new" / "nested" / "dir"
        assert not out.exists()
        to_sensor_logger_csvs(normal_df, "normal", out)
        assert out.is_dir()


# ---------------------------------------------------------------------------
# 17. to_sensor_logger_csvs — Location.csv: time is int64 epoch nanoseconds
# ---------------------------------------------------------------------------


class TestLocationCsvTime:
    @pytest.fixture()
    def location(self, normal_df: pd.DataFrame, tmp_path: Path) -> pd.DataFrame:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        return pd.read_csv(paths["Location.csv"])

    def test_time_dtype_is_int64(self, location: pd.DataFrame) -> None:
        assert location["time"].dtype == np.int64

    def test_time_is_plausible_epoch_ns(self, location: pd.DataFrame) -> None:
        # Must be after 2020-01-01 and before 2100-01-01 in epoch nanoseconds
        ns_2020 = 1_577_836_800_000_000_000
        ns_2100 = 4_102_444_800_000_000_000
        assert (location["time"] > ns_2020).all()
        assert (location["time"] < ns_2100).all()


# ---------------------------------------------------------------------------
# 18. to_sensor_logger_csvs — Location.csv: speed in m/s
# ---------------------------------------------------------------------------


class TestLocationCsvSpeed:
    def test_speed_is_non_negative(self, normal_df: pd.DataFrame, tmp_path: Path) -> None:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        loc = pd.read_csv(paths["Location.csv"])
        assert (loc["speed"] >= 0).all()

    def test_speed_is_plausible_mps(self, normal_df: pd.DataFrame, tmp_path: Path) -> None:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        loc = pd.read_csv(paths["Location.csv"])
        assert (loc["speed"] < 50).all()  # < 180 km/h


# ---------------------------------------------------------------------------
# 19. to_sensor_logger_csvs — Location.csv: bearing equals bearing from fcd_df
# ---------------------------------------------------------------------------


class TestLocationCsvCourse:
    def test_bearing_equals_bearing(self, normal_df: pd.DataFrame, tmp_path: Path) -> None:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        loc = pd.read_csv(paths["Location.csv"])
        np.testing.assert_array_almost_equal(
            loc["bearing"].to_numpy(),
            normal_df["bearing"].to_numpy(),
            decimal=6,
            err_msg="Location.csv 'bearing' must equal bearing from fcd_df",
        )


# ---------------------------------------------------------------------------
# 20. to_sensor_logger_csvs — Location.csv: horizontalAccuracy matches style
#
# From sumo-osm.md: calm=3.0, normal=5.0, aggressive=8.0
# ---------------------------------------------------------------------------


class TestLocationCsvHorizontalAccuracy:
    _EXPECTED: typing.ClassVar[dict[str, float]] = {
        "calm": 3.0,
        "normal": 5.0,
        "aggressive": 8.0,
    }

    @pytest.mark.parametrize("style,expected", _EXPECTED.items())
    def test_horizontal_accuracy_value(
        self, normal_df: pd.DataFrame, style: str, expected: float, tmp_path: Path
    ) -> None:
        paths = to_sensor_logger_csvs(normal_df, style, tmp_path / style)
        loc = pd.read_csv(paths["Location.csv"])
        assert "horizontalAccuracy" in loc.columns
        np.testing.assert_allclose(
            loc["horizontalAccuracy"].to_numpy(),
            expected,
            rtol=0,
            atol=1e-9,
            err_msg=f"horizontalAccuracy for style={style} must be {expected}",
        )


# ---------------------------------------------------------------------------
# 21. to_sensor_logger_csvs — Accelerometer.csv: columns time, x, y, z
# ---------------------------------------------------------------------------


class TestAccelerometerCsv:
    @pytest.fixture()
    def accel(self, normal_df: pd.DataFrame, tmp_path: Path) -> pd.DataFrame:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        return pd.read_csv(paths["Accelerometer.csv"])

    def test_required_columns(self, accel: pd.DataFrame) -> None:
        assert list(accel.columns[:4]) == ["time", "x", "y", "z"]

    def test_row_count_matches_fcd(self, normal_df: pd.DataFrame, accel: pd.DataFrame) -> None:
        assert len(accel) == len(normal_df)


# ---------------------------------------------------------------------------
# 22. to_sensor_logger_csvs — Gyroscope.csv: columns time, x, y, z
# ---------------------------------------------------------------------------


class TestGyroscopeCsv:
    @pytest.fixture()
    def gyro(self, normal_df: pd.DataFrame, tmp_path: Path) -> pd.DataFrame:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        return pd.read_csv(paths["Gyroscope.csv"])

    def test_required_columns(self, gyro: pd.DataFrame) -> None:
        assert list(gyro.columns[:4]) == ["time", "x", "y", "z"]

    def test_row_count_matches_fcd(self, normal_df: pd.DataFrame, gyro: pd.DataFrame) -> None:
        assert len(gyro) == len(normal_df)


# ---------------------------------------------------------------------------
# 23. to_sensor_logger_csvs — Gravity.csv: constant x=0, y=0, z=-9.81
# ---------------------------------------------------------------------------


class TestGravityCsv:
    @pytest.fixture()
    def gravity(self, normal_df: pd.DataFrame, tmp_path: Path) -> pd.DataFrame:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        return pd.read_csv(paths["Gravity.csv"])

    def test_x_is_zero_for_all_rows(self, gravity: pd.DataFrame) -> None:
        np.testing.assert_array_equal(gravity["x"].to_numpy(), 0.0)

    def test_y_is_zero_for_all_rows(self, gravity: pd.DataFrame) -> None:
        np.testing.assert_array_equal(gravity["y"].to_numpy(), 0.0)

    def test_z_is_minus_9_81_for_all_rows(self, gravity: pd.DataFrame) -> None:
        np.testing.assert_allclose(gravity["z"].to_numpy(), -9.81, rtol=0, atol=1e-9)

    @pytest.mark.parametrize("style", _STYLES)
    def test_gravity_constants_all_styles(
        self, normal_df: pd.DataFrame, style: str, tmp_path: Path
    ) -> None:
        paths = to_sensor_logger_csvs(normal_df, style, tmp_path / style)
        g = pd.read_csv(paths["Gravity.csv"])
        np.testing.assert_array_equal(g["x"].to_numpy(), 0.0)
        np.testing.assert_array_equal(g["y"].to_numpy(), 0.0)
        np.testing.assert_allclose(g["z"].to_numpy(), -9.81, rtol=0, atol=1e-9)


# ---------------------------------------------------------------------------
# 24. to_sensor_logger_csvs — Orientation.csv: quaternion columns
# ---------------------------------------------------------------------------


class TestOrientationCsv:
    @pytest.fixture()
    def orient(self, normal_df: pd.DataFrame, tmp_path: Path) -> pd.DataFrame:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        return pd.read_csv(paths["Orientation.csv"])

    def test_has_quaternion_columns(self, orient: pd.DataFrame) -> None:
        assert {"qw", "qx", "qy", "qz"}.issubset(orient.columns)

    def test_quaternion_unit_norm(self, orient: pd.DataFrame) -> None:
        norms = np.sqrt(
            orient["qw"] ** 2 + orient["qx"] ** 2 + orient["qy"] ** 2 + orient["qz"] ** 2
        )
        np.testing.assert_allclose(norms.to_numpy(), 1.0, atol=1e-6)

    def test_row_count_matches_fcd(self, normal_df: pd.DataFrame, orient: pd.DataFrame) -> None:
        assert len(orient) == len(normal_df)


# ---------------------------------------------------------------------------
# 25. to_sensor_logger_csvs — Magnetometer.csv: columns time, x, y, z
# ---------------------------------------------------------------------------


class TestMagnetometerCsv:
    @pytest.fixture()
    def mag(self, normal_df: pd.DataFrame, tmp_path: Path) -> pd.DataFrame:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        return pd.read_csv(paths["Magnetometer.csv"])

    def test_required_columns(self, mag: pd.DataFrame) -> None:
        assert list(mag.columns[:4]) == ["time", "x", "y", "z"]

    def test_row_count_matches_fcd(self, normal_df: pd.DataFrame, mag: pd.DataFrame) -> None:
        assert len(mag) == len(normal_df)


# ---------------------------------------------------------------------------
# 26. to_sensor_logger_csvs — TotalAcceleration = Accelerometer + Gravity
# ---------------------------------------------------------------------------


class TestTotalAcceleration:
    def test_total_is_vector_sum_of_accel_and_gravity(
        self, normal_df: pd.DataFrame, tmp_path: Path
    ) -> None:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        accel = pd.read_csv(paths["Accelerometer.csv"])
        grav = pd.read_csv(paths["Gravity.csv"])
        total = pd.read_csv(paths["TotalAcceleration.csv"])

        np.testing.assert_allclose(
            total["x"].to_numpy(),
            (accel["x"] + grav["x"]).to_numpy(),
            rtol=1e-6,
            err_msg="TotalAcceleration x must equal Accelerometer x + Gravity x",
        )
        np.testing.assert_allclose(
            total["y"].to_numpy(),
            (accel["y"] + grav["y"]).to_numpy(),
            rtol=1e-6,
            err_msg="TotalAcceleration y must equal Accelerometer y + Gravity y",
        )
        np.testing.assert_allclose(
            total["z"].to_numpy(),
            (accel["z"] + grav["z"]).to_numpy(),
            rtol=1e-6,
            err_msg="TotalAcceleration z must equal Accelerometer z + Gravity z",
        )

    def test_has_required_columns(self, normal_df: pd.DataFrame, tmp_path: Path) -> None:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        total = pd.read_csv(paths["TotalAcceleration.csv"])
        assert list(total.columns[:4]) == ["time", "x", "y", "z"]


# ---------------------------------------------------------------------------
# 27. to_sensor_logger_csvs — all time columns are int64
# ---------------------------------------------------------------------------


class TestAllTimeColumnsInt64:
    @pytest.mark.parametrize("fname", SENSOR_LOGGER_FILES)
    def test_time_is_int64(self, normal_df: pd.DataFrame, tmp_path: Path, fname: str) -> None:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        df = pd.read_csv(paths[fname])
        assert "time" in df.columns, f"{fname} has no 'time' column"
        assert (
            df["time"].dtype == np.int64
        ), f"{fname}: 'time' dtype is {df['time'].dtype}, expected int64"


# ---------------------------------------------------------------------------
# 28. to_sensor_logger_csvs — all time columns are strictly increasing
# ---------------------------------------------------------------------------


class TestAllTimeColumnsStrictlyIncreasing:
    @pytest.mark.parametrize("fname", SENSOR_LOGGER_FILES)
    def test_time_strictly_increasing(
        self, normal_df: pd.DataFrame, tmp_path: Path, fname: str
    ) -> None:
        paths = to_sensor_logger_csvs(normal_df, "normal", tmp_path)
        df = pd.read_csv(paths[fname])
        diffs = df["time"].diff().dropna()
        assert (diffs > 0).all(), f"{fname}: 'time' is not strictly increasing"


# ---------------------------------------------------------------------------
# 29. convert — equivalent to parse_fcd → add_noise → to_sensor_logger_csvs
# ---------------------------------------------------------------------------


class TestConvertEquivalence:
    def test_convert_matches_manual_pipeline(self, tmp_path: Path) -> None:
        seed = 77
        style = "normal"

        # Manual pipeline
        df_manual = parse_fcd(_NORMAL_FCD)
        df_manual = add_noise(df_manual, style, seed=seed)
        paths_manual = to_sensor_logger_csvs(df_manual, style, tmp_path / "manual")

        # convert()
        paths_convert = convert(_NORMAL_FCD, style, tmp_path / "convert", seed=seed)

        loc_manual = pd.read_csv(paths_manual["Location.csv"])
        loc_convert = pd.read_csv(paths_convert["Location.csv"])
        pd.testing.assert_frame_equal(loc_manual, loc_convert)


# ---------------------------------------------------------------------------
# 30. convert — produces all seven files for each driving style
# ---------------------------------------------------------------------------


class TestConvertAllStyles:
    @pytest.mark.parametrize("style", _STYLES)
    def test_seven_files_produced(self, style: str, tmp_path: Path) -> None:
        paths = convert(_STYLE_FCD[style], style, tmp_path / style, seed=0)
        assert set(paths.keys()) == set(SENSOR_LOGGER_FILES)
        for fname, p in paths.items():
            assert p.exists(), f"{fname} missing for style={style}"


# ---------------------------------------------------------------------------
# 31. convert — deterministic with fixed seed
# ---------------------------------------------------------------------------


class TestConvertDeterminism:
    @pytest.mark.parametrize("style", _STYLES)
    def test_same_seed_same_location_csv(self, style: str, tmp_path: Path) -> None:
        a = convert(_STYLE_FCD[style], style, tmp_path / f"{style}_a", seed=42)
        b = convert(_STYLE_FCD[style], style, tmp_path / f"{style}_b", seed=42)
        loc_a = pd.read_csv(a["Location.csv"])
        loc_b = pd.read_csv(b["Location.csv"])
        pd.testing.assert_frame_equal(loc_a, loc_b)
