import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

import ecodata.multidim_annotation_func as m


class TestMultidimensionalDerivedMetrics(unittest.TestCase):

    # ------------------------------------------------------------------
    # Helper configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _make_config(
        output_csv,
        *,
        keep_diagnostics=False,
        derive_vertical_motion=False,
        derive_thermal_proxy=False,
    ):
        """
        Minimal configuration sufficient for testing the finalizer.

        Input files do not need to exist because these tests call
        the derived-metric finalization functions directly rather
        than running the complete annotation workflow.
        """
        return m.MultidimAnnotationConfig(
            movement_csv="dummy_movement.csv",
            output_csv=str(output_csv),

            id_col="id",
            time_col="time",
            lat_col="lat",
            lon_col="lon",
            height_col="height",

            geopotential_file="dummy_geopotential.nc",
            geopotential_variable="z",

            multilevel=m.DatasetSpec(
                path="dummy_multilevel.nc",
                variables=["dummy"],
                continuous=["dummy"],
            ),

            keep_diagnostics=keep_diagnostics,
            save_per_individual=False,

            derive_vertical_motion=derive_vertical_motion,
            derive_thermal_proxy=derive_thermal_proxy,
        )

    # ------------------------------------------------------------------
    # 1. Thermal uplift w*
    # ------------------------------------------------------------------

    def test_w_star_positive_heat_flux_is_positive(self):
        w = m.compute_thermal_updraft_w_star(
            100.0,
            1000.0,
            300.0,
        )

        self.assertTrue(np.isfinite(w))
        self.assertGreater(w, 0.0)

    def test_w_star_negative_heat_flux_is_negative(self):
        w = m.compute_thermal_updraft_w_star(
            -100.0,
            1000.0,
            300.0,
        )

        self.assertTrue(np.isfinite(w))
        self.assertLess(w, 0.0)

    def test_w_star_zero_heat_flux_is_zero(self):
        w = m.compute_thermal_updraft_w_star(
            0.0,
            1000.0,
            300.0,
        )

        self.assertAlmostEqual(
            w,
            0.0,
            places=12,
        )

    def test_w_star_sign_symmetry(self):
        w_positive = m.compute_thermal_updraft_w_star(
            150.0,
            1200.0,
            290.0,
        )

        w_negative = m.compute_thermal_updraft_w_star(
            -150.0,
            1200.0,
            290.0,
        )

        self.assertAlmostEqual(
            abs(w_positive),
            abs(w_negative),
            places=12,
        )

        self.assertGreater(
            w_positive,
            0.0,
        )

        self.assertLess(
            w_negative,
            0.0,
        )

    # ------------------------------------------------------------------
    # 2-3. Orographic uplift
    # ------------------------------------------------------------------

    def test_orographic_uplift_exact_upslope(self):
        """
        East-facing upslope:
            aspect = 90 degrees

        Pure eastward wind:
            u = +10 m/s
            v = 0

        Slope = 30 degrees.

        Expected:
            Wo = 10 * sin(30 deg) = 5 m/s
        """
        wo = m.compute_orographic_uplift(
            u10_ms=10.0,
            v10_ms=0.0,
            slope_rad=math.radians(30.0),
            aspect_rad=math.radians(90.0),
        )

        self.assertAlmostEqual(
            wo,
            5.0,
            places=10,
        )

    def test_orographic_uplift_downslope_clipped_to_zero(self):
        """
        East-facing slope with westward wind must give
        a negative raw terrain projection, which is clipped to 0.
        """
        wo = m.compute_orographic_uplift(
            u10_ms=-10.0,
            v10_ms=0.0,
            slope_rad=math.radians(30.0),
            aspect_rad=math.radians(90.0),
        )

        self.assertAlmostEqual(
            wo,
            0.0,
            places=12,
        )

    def test_orographic_uplift_cross_slope_is_zero(self):
        """
        East-facing slope with pure northward wind is cross-slope
        and should give essentially zero orographic uplift.
        """
        wo = m.compute_orographic_uplift(
            u10_ms=0.0,
            v10_ms=10.0,
            slope_rad=math.radians(30.0),
            aspect_rad=math.radians(90.0),
        )

        self.assertLess(
            abs(wo),
            1e-12,
        )

    # ------------------------------------------------------------------
    # 4. Pressure associated with vertically sampled omega
    # ------------------------------------------------------------------

    def test_pressure_from_nearest_vertical_level(self):
        diag = {
            "vertical_method": "nearest",
            "matched_level": 850.0,
        }

        p = m._pressure_hpa_from_vertical_diag(
            diag
        )

        self.assertAlmostEqual(
            p,
            850.0,
            places=12,
        )

    def test_pressure_from_linear_vertical_interpolation(self):
        """
        For logarithmic interpolation with weight=0.5:

            p = exp(
                0.5*ln(900)
                + 0.5*ln(850)
            )

        which equals sqrt(900*850).
        """
        diag = {
            "vertical_method": "linear",
            "lower_level": 900.0,
            "upper_level": 850.0,
            "vertical_weight_upper": 0.5,
            "matched_level": 900.0,
        }

        p = m._pressure_hpa_from_vertical_diag(
            diag
        )

        expected = math.sqrt(
            900.0 * 850.0
        )

        self.assertAlmostEqual(
            p,
            expected,
            places=10,
        )

    # ------------------------------------------------------------------
    # 5. ABL level and potential temperature
    # ------------------------------------------------------------------

    def test_highest_pressure_level_below_abl_top(self):
        """
        Terrain = 250 m MSL
        BLH     = 900 m

        ABL top = 1150 m MSL

        Profiles:
            925 hPa -> 850 m
            900 hPa -> 1090 m  <- expected
            875 hPa -> 1340 m  <- above ABL
        """
        pressure_levels = np.array(
            [925.0, 900.0, 875.0]
        )

        heights_msl = np.array(
            [850.0, 1090.0, 1340.0]
        )

        temperatures = np.array(
            [284.0, 281.0, 278.0]
        )

        theta, z_agl, diag = (
            m.compute_abl_top_thermal_state(
                pressure_levels=pressure_levels,
                geopotential_heights_msl_m=heights_msl,
                temperature_profile_K=temperatures,
                boundary_layer_height_m=900.0,
                terrain_elevation_m=250.0,
            )
        )

        self.assertTrue(
            diag["thermal_profile_available"]
        )

        self.assertAlmostEqual(
            diag["thermal_abl_top_msl_m"],
            1150.0,
            places=12,
        )

        self.assertAlmostEqual(
            diag["thermal_selected_pressure_hpa"],
            900.0,
            places=12,
        )

        self.assertAlmostEqual(
            diag[
                "thermal_selected_level_height_msl_m"
            ],
            1090.0,
            places=12,
        )

        # Height of selected pressure level above terrain:
        # 1090 - 250 = 840 m.
        self.assertAlmostEqual(
            z_agl,
            840.0,
            places=12,
        )

        expected_theta = (
            281.0
            * (1000.0 / 900.0)
            ** (m._R_DRY / m._CP_AIR)
        )

        self.assertAlmostEqual(
            theta,
            expected_theta,
            places=10,
        )

    def test_abl_profile_without_terrain_returns_nan(self):
        theta, z_agl, diag = (
            m.compute_abl_top_thermal_state(
                pressure_levels=[
                    925.0,
                    900.0,
                    875.0,
                ],
                geopotential_heights_msl_m=[
                    850.0,
                    1090.0,
                    1340.0,
                ],
                temperature_profile_K=[
                    284.0,
                    281.0,
                    278.0,
                ],
                boundary_layer_height_m=900.0,
                terrain_elevation_m=np.nan,
            )
        )

        self.assertTrue(
            np.isnan(theta)
        )

        self.assertTrue(
            np.isnan(z_agl)
        )

        self.assertEqual(
            diag["thermal_profile_warning"],
            "terrain_elevation_unavailable",
        )

    # ------------------------------------------------------------------
    # 6. Heat-flux input conventions
    # ------------------------------------------------------------------

    def test_all_heat_flux_modes_convert_to_same_upward_flux(self):
        """
        All four inputs describe the same physical upward heat flux:

            +100 W/m2 upward.
        """
        seconds = 3600.0

        values = [
            m.convert_surface_heat_flux_to_upward_wm2(
                100.0,
                "upward_wm2",
                seconds,
            ),

            m.convert_surface_heat_flux_to_upward_wm2(
                -100.0,
                "downward_wm2",
                seconds,
            ),

            m.convert_surface_heat_flux_to_upward_wm2(
                100.0 * seconds,
                "accumulated_upward_jm2",
                seconds,
            ),

            m.convert_surface_heat_flux_to_upward_wm2(
                -100.0 * seconds,
                "accumulated_downward_jm2",
                seconds,
            ),
        ]

        for value in values:
            self.assertAlmostEqual(
                value,
                100.0,
                places=12,
            )

    def test_wm2_units_reject_accumulated_mode(self):
        ds = xr.Dataset(
            {
                "sshf": xr.DataArray(
                    [100.0],
                    dims=["time"],
                    attrs={
                        "units": "W m-2",
                    },
                )
            }
        )

        with self.assertRaises(ValueError):
            m.validate_surface_heat_flux_metadata(
                ds,
                "sshf",
                "accumulated_downward_jm2",
            )

    def test_jm2_units_reject_flux_mode(self):
        ds = xr.Dataset(
            {
                "sshf": xr.DataArray(
                    [-360000.0],
                    dims=["time"],
                    attrs={
                        "units": "J m-2",
                    },
                )
            }
        )

        with self.assertRaises(ValueError):
            m.validate_surface_heat_flux_metadata(
                ds,
                "sshf",
                "downward_wm2",
            )

    def test_explicit_sign_metadata_mismatch_is_rejected(self):
        ds = xr.Dataset(
            {
                "sshf": xr.DataArray(
                    [100.0],
                    dims=["time"],
                    attrs={
                        "units": "W m-2",
                        "positive": "downward",
                    },
                )
            }
        )

        with self.assertRaises(ValueError):
            m.validate_surface_heat_flux_metadata(
                ds,
                "sshf",
                "upward_wm2",
            )

    # ------------------------------------------------------------------
    # 4 continued. omega -> geometric w
    # ------------------------------------------------------------------

    def test_negative_omega_produces_positive_vertical_motion(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "vertical.csv"

            config = self._make_config(
                output,
                keep_diagnostics=True,
                derive_vertical_motion=True,
            )

            df = pd.DataFrame(
                {
                    "id": ["bird_1"],
                    "time": [
                        pd.Timestamp(
                            "2014-06-01 12:00:00"
                        )
                    ],
                    "lat": [45.0],
                    "lon": [-80.0],
                    "height": [1500.0],

                    # ERA5 omega:
                    "td_w_at_height": [-0.5],

                    # Pressure-level temperature:
                    "td_temperature_at_height": [280.0],

                    # Internal pressure associated specifically
                    # with vertically sampled omega:
                    "_vertical_motion_pressure_hpa": [
                        850.0
                    ],
                }
            )

            result = (
                m._finalize_and_save_annotation_output(
                    df.copy(),
                    config,
                )
            )

            w = float(
                result.loc[
                    0,
                    "vertical_motion_ms",
                ]
            )

            self.assertGreater(
                w,
                0.0,
            )

            rho = (
                85000.0
                / (
                    m._R_DRY
                    * 280.0
                )
            )

            expected = (
                0.5
                / (
                    rho
                    * m._G
                )
            )

            self.assertAlmostEqual(
                w,
                expected,
                places=12,
            )

    def test_vertical_motion_independent_of_keep_diagnostics(self):
        """
        keep_diagnostics must affect only output diagnostics,
        never the calculated vertical velocity.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            base_df = pd.DataFrame(
                {
                    "id": ["bird_1"],
                    "time": [
                        pd.Timestamp(
                            "2014-06-01 12:00:00"
                        )
                    ],
                    "lat": [45.0],
                    "lon": [-80.0],
                    "height": [1500.0],
                    "td_w_at_height": [-0.5],
                    "td_temperature_at_height": [280.0],
                    "_vertical_motion_pressure_hpa": [
                        850.0
                    ],
                }
            )

            config_false = self._make_config(
                tmp_path / "diag_false.csv",
                keep_diagnostics=False,
                derive_vertical_motion=True,
            )

            config_true = self._make_config(
                tmp_path / "diag_true.csv",
                keep_diagnostics=True,
                derive_vertical_motion=True,
            )

            result_false = (
                m._finalize_and_save_annotation_output(
                    base_df.copy(),
                    config_false,
                )
            )

            result_true = (
                m._finalize_and_save_annotation_output(
                    base_df.copy(),
                    config_true,
                )
            )

            w_false = float(
                result_false.loc[
                    0,
                    "vertical_motion_ms",
                ]
            )

            w_true = float(
                result_true.loc[
                    0,
                    "vertical_motion_ms",
                ]
            )

            self.assertAlmostEqual(
                w_false,
                w_true,
                places=12,
            )

    # ------------------------------------------------------------------
    # 5 continued. T2m + BLH fallback
    # ------------------------------------------------------------------

    def test_thermal_w_star_t2m_blh_fallback(self):
        """
        If profile-derived potential temperature is unavailable,
        finalizer must retain the original ERA5 T2m + BLH fallback.
        """
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "thermal.csv"

            config = self._make_config(
                output,
                keep_diagnostics=True,
                derive_thermal_proxy=True,
            )

            df = pd.DataFrame(
                {
                    "id": ["bird_1"],
                    "time": [
                        pd.Timestamp(
                            "2014-06-01 12:00:00"
                        )
                    ],
                    "lat": [45.0],
                    "lon": [-80.0],
                    "height": [1000.0],

                    "surface_surface_sensible_heat_flux": [
                        100.0
                    ],
                    "surface_boundary_layer_height": [
                        900.0
                    ],
                    "surface_2m_temperature": [
                        290.0
                    ],
                }
            )

            result = (
                m._finalize_and_save_annotation_output(
                    df.copy(),
                    config,
                )
            )

            expected = (
                m.compute_thermal_updraft_w_star(
                    100.0,
                    900.0,
                    290.0,
                )
            )

            actual = float(
                result.loc[
                    0,
                    "thermal_updraft_w_star_ms",
                ]
            )

            self.assertAlmostEqual(
                actual,
                expected,
                places=12,
            )

            self.assertEqual(
                result.loc[
                    0,
                    "thermal_temperature_source",
                ],
                "t2m_blh_fallback",
            )


if __name__ == "__main__":
    unittest.main()