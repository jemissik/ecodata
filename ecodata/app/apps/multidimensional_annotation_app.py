"""
Multidimensional Annotation UI
"""

from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple
import panel as pn
import pandas as pd

try:
    import xarray as xr
except Exception:  # pragma: no cover
    xr = None

try:
    import geopandas as gpd
except Exception:  # pragma: no cover
    gpd = None

from ecodata.app.config import DEFAULT_TEMPLATE
from ecodata.app.models import FileSelector
from ecodata.panel_utils import register_view

try:
    from ecodata.annotation_eng_func import safe_open_nc_with_time_decoding, load_vector_extent_info
except Exception:  # pragma: no cover
    safe_open_nc_with_time_decoding = None
    load_vector_extent_info = None

try:
    from ecodata.multidim_annotation_func import run_multidimensional_annotation_from_paths
except Exception as exc:  # pragma: no cover
    run_multidimensional_annotation_from_paths = None
    BACKEND_IMPORT_ERROR = exc
else:
    BACKEND_IMPORT_ERROR = None

logger = logging.getLogger(__name__)


class Multidimensional_Annotation_App:
    def __init__(self):
        self.name = "Multidimensional Annotation Engine App (ERA5 only)"
        self._movement_columns: List[str] = []
        self._movement_df: Optional[pd.DataFrame] = None
        self.boundary_path: Optional[str] = None

        def make_file_selector(name: str, file_pattern: str = "*", expanded: bool = True) -> FileSelector:
            return FileSelector(
                name=name,
                directory=str(Path.home()),
                file_pattern=file_pattern,
                only_files=True,
                constrain_path=False,
                expanded=expanded,
                size=10,
                sizing_mode="stretch_width",
            )

        self.movement_csv = make_file_selector("Movement CSV", "*.csv")
        self.load_movement_button = pn.widgets.Button(
            name="Load movement data", button_type="primary", sizing_mode="stretch_width"
        )
        self.taxon_multiselect = pn.widgets.MultiSelect(
            name="Select Taxon (use Ctrl or ⌘ for multiple)",
            options=[],
            value=[],
            height=140,
            sizing_mode="stretch_width",
        )
        self.id_multiselect = pn.widgets.MultiSelect(
            name="Select ID (use Ctrl or ⌘ for multiple)", options=[], value=[], height=140, sizing_mode="stretch_width"
        )
        self.id_column = pn.widgets.Select(name="ID column", options=[], value=None, sizing_mode="stretch_width")
        self.time_column = pn.widgets.Select(
            name="Timestamp column", options=[], value=None, sizing_mode="stretch_width"
        )
        self.lat_column = pn.widgets.Select(name="Latitude column", options=[], value=None, sizing_mode="stretch_width")
        self.lon_column = pn.widgets.Select(
            name="Longitude column", options=[], value=None, sizing_mode="stretch_width"
        )
        self.height_column = pn.widgets.Select(
            name="Height / altitude column", options=[], value=None, sizing_mode="stretch_width"
        )
        self.height_units = pn.widgets.Select(
            name="Height units", options=["m"], value="m", sizing_mode="stretch_width"
        )
        self.height_reference = pn.widgets.Select(
            name="Height reference",
            options=[
                "WGS84 ellipsoidal height (Movebank GPS height)",
                "Already orthometric / MSL-like",
                "Height above ground level (requires DEM)",
            ],
            value="WGS84 ellipsoidal height (Movebank GPS height)",
            sizing_mode="stretch_width",
        )
        self.geoid_mode = pn.widgets.Select(
            name="Geoid correction", options=["geographiclib", "constant", "none"],
            value="geographiclib", sizing_mode="stretch_width",
        )
        self.constant_geoid_undulation_m = pn.widgets.FloatInput(
            name="Constant geoid undulation N, m", value=0.0, step=1.0, sizing_mode="stretch_width"
        )
        self.movement_info = pn.pane.HTML(
            "File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: - <br>",
            sizing_mode="stretch_width",
        )

        self.geopotential_file = make_file_selector("Geopotential file", "*.nc")
        self.scan_geopotential_button = pn.widgets.Button(
            name="Scan geopotential file", button_type="primary", sizing_mode="stretch_width"
        )
        self.geopotential_variable = pn.widgets.Select(
            name="Geopotential variable", options=[], value=None, sizing_mode="stretch_width"
        )
        self.nc_time_var = pn.widgets.Select(name="Time variable", options=[], value=None, sizing_mode="stretch_width")
        self.nc_lat_var = pn.widgets.Select(
            name="Latitude variable", options=[], value=None, sizing_mode="stretch_width"
        )
        self.nc_lon_var = pn.widgets.Select(
            name="Longitude variable", options=[], value=None, sizing_mode="stretch_width"
        )
        self.nc_level_var = pn.widgets.Select(
            name="Vertical / level variable", options=[], value=None, sizing_mode="stretch_width"
        )
        self.geopotential_units = pn.widgets.Select(
            name="Geopotential units",
            options=["m2 s-2", "geopotential metres"],
            value="m2 s-2",
            sizing_mode="stretch_width",
        )
        self.convert_geopotential_to_height = pn.widgets.Checkbox(
            name="Convert geopotential to height using z / 9.80665", value=True, sizing_mode="stretch_width"
        )
        self.gravity_constant = pn.widgets.FloatInput(
            name="Gravity constant", value=9.80665, step=0.00001, disabled=True
        )

        self.multilevel_var_file = make_file_selector("Annotated var (multilevel) file", "*.nc")
        self.scan_multilevel_button = pn.widgets.Button(
            name="Scan multilevel file", button_type="primary", sizing_mode="stretch_width"
        )
        self.multilevel_variable = pn.widgets.Select(
            name="Annotated var (multilevel, first selected)", options=[], value=None, sizing_mode="stretch_width"
        )
        self.multilevel_continuous_vars = pn.widgets.MultiSelect(
            name="Continuous multilevel variables (use Ctrl or ⌘ for multiple)",
            options=[],
            value=[],
            height=180,
            sizing_mode="stretch_width",
        )
        self.multilevel_categorical_vars = pn.widgets.MultiSelect(
            name="Categorical/QC multilevel variables (use Ctrl or ⌘ for multiple)",
            options=[],
            value=[],
            height=180,
            sizing_mode="stretch_width",
        )
        self.surface_var_file = make_file_selector("Annotated var (surface) file", "*.nc")
        self.scan_surface_button = pn.widgets.Button(
            name="Scan surface file", button_type="default", sizing_mode="stretch_width"
        )
        self.surface_variable = pn.widgets.Select(
            name="Annotated var (surface, first selected)", options=[], value=None, sizing_mode="stretch_width"
        )
        self.surface_continuous_vars = pn.widgets.MultiSelect(
            name="Continuous surface variables (use Ctrl or ⌘ for multiple)",
            options=[],
            value=[],
            height=140,
            sizing_mode="stretch_width",
        )
        self.surface_categorical_vars = pn.widgets.MultiSelect(
            name="Categorical/QC surface variables (use Ctrl or ⌘ for multiple)",
            options=[],
            value=[],
            height=140,
            sizing_mode="stretch_width",
        )
        self.env_info = pn.pane.HTML(
            "File: not selected <br>Multilevel parameters: - <br>Surface parameters: - <br>Time range: - <br>Spatial range: - <br>Vertical levels: - <br>",
            sizing_mode="stretch_width",
        )
        self.boundary_file = make_file_selector("Boundary data (.shp/.geojson)", "*")
        self.load_boundary_button = pn.widgets.Button(
            name="Load boundary data", button_type="primary", sizing_mode="stretch_width"
        )
        self.reset_boundary_button = pn.widgets.Button(
            name="(!) Reset boundary", button_type="primary", sizing_mode="stretch_width"
        )
        self.boundary_info = pn.pane.HTML(
            "Boundary file: not selected <br>Spatial range: = environmental data boundary", sizing_mode="stretch_width"
        )
        self.spatial_interpolation_method = pn.widgets.Select(
            name="Spatial interpolation method",
            options=["Nearest neighbor", "Inverse Distance Weighting"],
            value="Nearest neighbor",
            sizing_mode="stretch_width",
        )
        self.control_smoothing = pn.widgets.Select(
            name="Number of nearest grid points",
            options=["1", "2", "4", "6", "8"],
            value="1",
            sizing_mode="stretch_width",
        )
        self.vertical_matching_method = pn.widgets.Select(
            name="Vertical matching method",
            options=["Nearest geopotential-height level", "Linear vertical interpolation"],
            value="Nearest geopotential-height level",
            sizing_mode="stretch_width",
        )
        self.use_surface_as_lower_anchor = pn.widgets.Checkbox(
            name="Use surface variable as lower vertical anchor", value=True, sizing_mode="stretch_width"
        )
        self.surface_anchor_height_agl_m = pn.widgets.FloatInput(
            name="Surface anchor height above ground, m", value=2.0, step=0.5, sizing_mode="stretch_width"
        )

        self.u_file = make_file_selector("U wind component file", "*.nc", expanded=False)
        self.u_variable = pn.widgets.Select(name="U variable", options=[], value=None, sizing_mode="stretch_width")
        self.v_file = make_file_selector("V wind component file", "*.nc", expanded=False)
        self.v_variable = pn.widgets.Select(name="V variable", options=[], value=None, sizing_mode="stretch_width")
        self.w_file = make_file_selector("W vertical velocity file", "*.nc", expanded=False)
        self.w_variable = pn.widgets.Select(name="W variable", options=[], value=None, sizing_mode="stretch_width")
        self.temperature_file = make_file_selector("Temperature file", "*.nc", expanded=False)
        self.temperature_variable = pn.widgets.Select(
            name="Temperature variable", options=[], value=None, sizing_mode="stretch_width"
        )

        self.surface_heat_flux_file = make_file_selector("Surface sensible heat flux file", "*.nc", expanded=False)
        self.surface_heat_flux_variable = pn.widgets.Select(
            name="Surface sensible heat flux variable", options=[], value=None, sizing_mode="stretch_width"
        )
        self.surface_heat_flux_mode = pn.widgets.Select(
            name="Surface heat flux input convention",
            options=[
                "Upward-positive instantaneous flux (W m-2)",
                "Downward-positive instantaneous flux (W m-2)",
                "Downward-positive accumulated energy (J m-2)",
                "Upward-positive accumulated energy (J m-2)",
            ],
            value="Upward-positive instantaneous flux (W m-2)",
            sizing_mode="stretch_width",
        )
        self.surface_heat_flux_accumulation_seconds = pn.widgets.FloatInput(
            name="Heat-flux accumulation period, seconds",
            value=3600.0,
            step=3600.0,
            start=1.0,
            disabled=True,
            sizing_mode="stretch_width",
        )
        self.boundary_layer_height_file = make_file_selector("Boundary layer height file", "*.nc", expanded=False)
        self.boundary_layer_height_variable = pn.widgets.Select(
            name="Boundary layer height variable", options=[], value=None, sizing_mode="stretch_width"
        )
        self.temperature_2m_file = make_file_selector("2 m temperature file", "*.nc", expanded=False)
        self.temperature_2m_variable = pn.widgets.Select(
            name="2 m temperature variable", options=[], value=None, sizing_mode="stretch_width"
        )
        self.u10_file = make_file_selector("10 m U wind component file", "*.nc", expanded=False)
        self.u10_variable = pn.widgets.Select(
            name="10 m U wind variable", options=[], value=None, sizing_mode="stretch_width"
        )
        self.v10_file = make_file_selector("10 m V wind component file", "*.nc", expanded=False)
        self.v10_variable = pn.widgets.Select(
            name="10 m V wind variable", options=[], value=None, sizing_mode="stretch_width"
        )

        self.scan_optional_components_button = pn.widgets.Button(
            name="Scan all derived-metric input files", button_type="primary", sizing_mode="stretch_width"
        )
        self.topography_source = pn.widgets.Select(
            name="Topography source",
            options=[
                "None",
                "ETOPO1 Ice Surface Global Relief Model",
                "SRTM 1 Arc-Second DEM",
                "ASTER ASTGTM3 Global 30-m DEM",
                "Custom DEM / GeoTIFF",
            ],
            value="None",
            sizing_mode="stretch_width",
        )
        self.dem_file = make_file_selector("DEM file", "*.tif")
        self.dem_units = pn.widgets.Select(
            name="DEM vertical units", options=["m"], value="m", disabled=True, sizing_mode="stretch_width"
        )
        self.dem_reference = pn.widgets.Select(
            name="DEM reference",
            options=["Assumed orthometric / MSL-like"],
            value="Assumed orthometric / MSL-like",
            disabled=True,
            sizing_mode="stretch_width",
        )

        self.derive_wind_support_crosswind = pn.widgets.Checkbox(
            name="Wind support and cross wind", value=False, disabled=True, sizing_mode="stretch_width"
        )
        self.derive_wind_speed_direction = pn.widgets.Checkbox(
            name="Wind speed and direction", value=False, disabled=True, sizing_mode="stretch_width"
        )
        self.derive_vertical_motion = pn.widgets.Checkbox(
            name="Vertical motion from W", value=False, disabled=True, sizing_mode="stretch_width"
        )
        self.derive_thermal_uplift = pn.widgets.Checkbox(
            name="Thermal uplift / stability proxy", value=False, disabled=True, sizing_mode="stretch_width"
        )
        self.derive_orographic_uplift = pn.widgets.Checkbox(
            name="Orographic uplift", value=False, disabled=True, sizing_mode="stretch_width"
        )
        self.track_direction_source = pn.widgets.Select(
            name="Track direction source",
            options=["Compute from consecutive points", "Use existing heading column"],
            value="Compute from consecutive points",
            disabled=True,
            sizing_mode="stretch_width",
        )
        self.heading_column = pn.widgets.Select(
            name="Heading column", options=[], value=None, disabled=True, sizing_mode="stretch_width"
        )
        self.open_derived_metrics_button = pn.widgets.Button(
            name="Configure derived metrics and inputs...", button_type="primary", sizing_mode="stretch_width"
        )
        self.derived_metrics_status = pn.pane.Markdown(
            ("**Selected metrics:** none  \n" "**Configured component fields:** 0/9"),
            sizing_mode="stretch_width",
            styles={"border": "1px solid #ddd", "padding": "8px", "border-radius": "5px"},
        )
        self.apply_derived_metrics_button = pn.widgets.Button(
            name="Apply and close", button_type="primary", sizing_mode="stretch_width"
        )
        self.template = None
        self.derived_metrics_modal_content = self._build_derived_metrics_content()
        self.output_csv = pn.widgets.TextInput(
            name="Output CSV",
            value=str(Path.home() / "Downloads" / "multidimensional_annotation_output.csv"),
            sizing_mode="stretch_width",
        )
        self.save_per_individual = pn.widgets.Checkbox(
            name="Save per individual", value=True, sizing_mode="stretch_width"
        )
        self.keep_diagnostics = pn.widgets.Checkbox(
            name="Keep diagnostic columns", value=True, sizing_mode="stretch_width"
        )
        self.validate_button = pn.widgets.Button(
            name="Validate configuration", button_type="primary", sizing_mode="stretch_width"
        )
        self.run_button = pn.widgets.Button(
            name="Run multidimensional annotation", button_type="primary", sizing_mode="stretch_width"
        )

        self.preview = pn.pane.Markdown(
            "### Preview\nNo files scanned yet.", sizing_mode="stretch_width", styles=self._pane_style()
        )
        self.validation = pn.pane.Markdown(
            "### Validation\nNot validated yet.", sizing_mode="stretch_width", styles=self._pane_style()
        )
        self.log = pn.pane.Markdown("### Log\nReady.", sizing_mode="stretch_width", styles=self._pane_style())
        self.load_movement_button.on_click(self._on_load_movement)
        self.scan_geopotential_button.on_click(self._on_scan_geopotential)
        self.scan_multilevel_button.on_click(self._on_scan_multilevel)
        self.scan_surface_button.on_click(self._on_scan_surface)
        self.scan_optional_components_button.on_click(self._on_scan_optional_components)
        self.open_derived_metrics_button.on_click(self._open_derived_metrics_panel)
        self.apply_derived_metrics_button.on_click(self._apply_derived_metrics_panel)
        self.load_boundary_button.on_click(self._on_load_boundary)
        self.reset_boundary_button.on_click(self._on_reset_boundary)
        self.validate_button.on_click(self._on_validate)
        self.run_button.on_click(self._on_run)
        self.taxon_multiselect.param.watch(self._update_ids_by_taxon, "value")
        self.spatial_interpolation_method.param.watch(self._update_smoothing_options, "value")

        for widget in (
            self.u_file,
            self.u_variable,
            self.v_file,
            self.v_variable,
            self.w_file,
            self.w_variable,
            self.temperature_file,
            self.temperature_variable,
            self.surface_heat_flux_file,
            self.surface_heat_flux_variable,
            self.surface_heat_flux_mode,
            self.surface_heat_flux_accumulation_seconds,
            self.boundary_layer_height_file,
            self.boundary_layer_height_variable,
            self.temperature_2m_file,
            self.temperature_2m_variable,
            self.u10_file,
            self.u10_variable,
            self.v10_file,
            self.v10_variable,
            self.dem_file,
            self.topography_source,
            self.track_direction_source,
            self.derive_wind_speed_direction,
            self.derive_wind_support_crosswind,
            self.derive_vertical_motion,
            self.derive_thermal_uplift,
            self.derive_orographic_uplift,
        ):
            widget.param.watch(self._update_dynamic_states, "value")

        self._wire_variable_split_guards()
        self._update_smoothing_options()
        self._update_dynamic_states()

    @staticmethod
    def _pane_style():
        return {"border": "1px solid #ddd", "padding": "10px", "border-radius": "6px"}

    def _append_log(self, message: str) -> None:
        old = self.log.object or "### Log\n"
        if old.strip() == "### Log\nReady.":
            old = "### Log\n"
        self.log.object = old + f"\n- {message}"

    def _build_derived_metrics_content(self):
        return pn.Column(
            pn.pane.Markdown(
                """
                ## Derived metrics configuration
                Configure the additional atmospheric fields used for derived metrics.
                The selected values are retained after the window is closed.
                """,
                sizing_mode="stretch_width",
            ),
            pn.Card(
                pn.Column(
                    pn.pane.Markdown("**File for pressure-level U variable**", sizing_mode="stretch_width"),
                    self.u_file,
                    self.u_variable,
                    pn.pane.Markdown("**File for pressure-level V variable***", sizing_mode="stretch_width"),
                    self.v_file,
                    self.v_variable,
                    sizing_mode="stretch_width",
                ),
                title="U and V wind components",
                collapsible=True,
                collapsed=False,
                sizing_mode="stretch_width",
            ),
            pn.Card(
                pn.Column(
                    self.derive_wind_speed_direction,
                    self.derive_wind_support_crosswind,
                    self.track_direction_source,
                    self.heading_column,
                    sizing_mode="stretch_width",
                ),
                title="Wind-derived metrics",
                collapsible=True,
                collapsed=False,
                sizing_mode="stretch_width",
            ),
            pn.Card(
                pn.Column(
                    pn.pane.Markdown(
                        (
                            "The W field is sampled at movement height. "
                            "Pressure-level temperature is optional, but "
                            "it allows a more accurate conversion of omega "
                            "from Pa/s to m/s."
                        ),
                        sizing_mode="stretch_width",
                    ),
                    pn.pane.Markdown("**File for pressure-level W variable**", sizing_mode="stretch_width"),
                    self.w_file,
                    self.w_variable,
                    pn.pane.Markdown("**File for pressure-level temperature variable**", sizing_mode="stretch_width"),
                    self.temperature_file,
                    self.temperature_variable,
                    self.derive_vertical_motion,
                    sizing_mode="stretch_width",
                ),
                title="Vertical motion",
                collapsible=True,
                collapsed=False,
                sizing_mode="stretch_width",
            ),
            pn.Card(
                pn.Column(
                    pn.pane.Markdown(
                        (
                            "Thermal uplift requires surface sensible "
                            "heat flux, boundary-layer height and "
                            "2 m temperature."
                        ),
                        sizing_mode="stretch_width",
                    ),
                    pn.pane.Markdown("**File for surface sensible heat flux variable**", sizing_mode="stretch_width"),
                    self.surface_heat_flux_file,
                    self.surface_heat_flux_variable,
                    self.surface_heat_flux_mode,
                    self.surface_heat_flux_accumulation_seconds,
                    pn.pane.Markdown("**File for boundary layer height variable**", sizing_mode="stretch_width"),
                    self.boundary_layer_height_file,
                    self.boundary_layer_height_variable,
                    pn.pane.Markdown("**File for 2 m temperature variable**", sizing_mode="stretch_width"),
                    self.temperature_2m_file,
                    self.temperature_2m_variable,
                    self.derive_thermal_uplift,
                    sizing_mode="stretch_width",
                ),
                title="Thermal uplift inputs",
                collapsible=True,
                collapsed=False,
                sizing_mode="stretch_width",
            ),
            pn.Card(
                pn.Column(
                    pn.pane.Markdown(
                        (
                            "Orographic uplift uses 10 m U/V wind "
                            "components and the DEM selected in the "
                            "Topography section. Pressure-level U/V "
                            "fields are not used here."
                        ),
                        sizing_mode="stretch_width",
                    ),
                    pn.pane.Markdown("**File for 10 m U wind variable**", sizing_mode="stretch_width"),
                    self.u10_file,
                    self.u10_variable,
                    pn.pane.Markdown("**File for 10 m V wind variable**", sizing_mode="stretch_width"),
                    self.v10_file,
                    self.v10_variable,
                    self.derive_orographic_uplift,
                    sizing_mode="stretch_width",
                ),
                title="Orographic uplift inputs",
                collapsible=True,
                collapsed=False,
                sizing_mode="stretch_width",
            ),
            self.scan_optional_components_button,
            self.apply_derived_metrics_button,
            sizing_mode="stretch_width",
            width=760,
            max_height=760,
            scroll=True,
            margin=10,
        )

    def _open_derived_metrics_panel(self, event=None) -> None:
        self._update_dynamic_states()

        if self.template is None:
            self._append_log("Cannot open derived metrics window: " "application template is not initialized.")
            return

        self.template.open_modal()
        self._append_log("Derived metrics configuration window opened.")

    def _apply_derived_metrics_panel(self, event=None) -> None:
        self._update_dynamic_states()
        self._refresh_preview()

        if self.template is not None:
            self.template.close_modal()

        self._append_log("Derived metrics configuration updated.")

    def _update_derived_metrics_status(self) -> None:
        metric_states = [
            ("Wind speed/direction", bool(self.derive_wind_speed_direction.value)),
            ("Wind support/cross wind", bool(self.derive_wind_support_crosswind.value)),
            ("Vertical motion", bool(self.derive_vertical_motion.value)),
            ("Thermal uplift", bool(self.derive_thermal_uplift.value)),
            ("Orographic uplift", bool(self.derive_orographic_uplift.value)),
        ]

        selected_metrics = [label for label, enabled in metric_states if enabled]
        component_states = {
            "U pressure-level": (self._path_exists(self.u_file.value) and bool(self.u_variable.value)),
            "V pressure-level": (self._path_exists(self.v_file.value) and bool(self.v_variable.value)),
            "W pressure-level": (self._path_exists(self.w_file.value) and bool(self.w_variable.value)),
            "Temperature pressure-level": (
                self._path_exists(self.temperature_file.value) and bool(self.temperature_variable.value)
            ),
            "Surface heat flux": (
                self._path_exists(self.surface_heat_flux_file.value) and bool(self.surface_heat_flux_variable.value)
            ),
            "Boundary-layer height": (
                self._path_exists(self.boundary_layer_height_file.value)
                and bool(self.boundary_layer_height_variable.value)
            ),
            "2 m temperature": (
                self._path_exists(self.temperature_2m_file.value) and bool(self.temperature_2m_variable.value)
            ),
            "U10": (self._path_exists(self.u10_file.value) and bool(self.u10_variable.value)),
            "V10": (self._path_exists(self.v10_file.value) and bool(self.v10_variable.value)),
        }

        configured_components = [label for label, configured in component_states.items() if configured]
        selected_text = ", ".join(selected_metrics) if selected_metrics else "none"
        configured_text = ", ".join(configured_components) if configured_components else "none"

        self.derived_metrics_status.object = (
            f"**Selected metrics:** {selected_text}  \n"
            f"**Configured component fields:** "
            f"{len(configured_components)}/9 ({configured_text})"
        )

    @staticmethod
    def _file_value(value):
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            return values[0] if values else None
        return value

    @staticmethod
    def _path_exists(value: str) -> bool:
        value = Multidimensional_Annotation_App._file_value(value)
        if not value:
            return False
        path = Path(str(value)).expanduser()
        return path.exists() and path.is_file()

    @staticmethod
    def _optional_path(value):
        value = Multidimensional_Annotation_App._file_value(value)
        if not value:
            return None
        path = Path(str(value)).expanduser()
        return str(path) if path.exists() and path.is_file() else None

    @staticmethod
    def _guess_column(columns: List[str], candidates: List[str]) -> Optional[str]:
        lower_map = {c.lower(): c for c in columns}
        normalized_map = {re.sub(r"[-:._\s]+", "_", c.lower()): c for c in columns}
        for cand in candidates:
            c1 = cand.lower()
            c2 = re.sub(r"[-:._\s]+", "_", c1)
            if c1 in lower_map:
                return lower_map[c1]
            if c2 in normalized_map:
                return normalized_map[c2]
        for col in columns:
            cl = col.lower()
            cn = re.sub(r"[-:._\s]+", "_", cl)
            if any(cand.lower() in cl or re.sub(r"[-:._\s]+", "_", cand.lower()) in cn for cand in candidates):
                return col
        return columns[0] if columns else None

    @staticmethod
    def _open_dataset_for_scan(path):
        if xr is None:
            raise RuntimeError("xarray is not available.")
        if safe_open_nc_with_time_decoding is not None:
            return safe_open_nc_with_time_decoding(path)
        return xr.open_dataset(path, decode_times=False)

    @staticmethod
    def _read_nc_metadata(path_value: str) -> Tuple[List[str], List[str], dict]:
        path_value = Multidimensional_Annotation_App._file_value(path_value)
        if not path_value:
            raise FileNotFoundError("No NetCDF file selected.")
        path = Path(str(path_value)).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"File not found or not a file: {path}")
        if path.suffix.lower() != ".nc":
            raise ValueError(f"Expected a .nc file, got: {path}")

        ds = Multidimensional_Annotation_App._open_dataset_for_scan(path)
        try:
            data_vars = sorted([str(v) for v in ds.data_vars])
            all_names = sorted([str(v) for v in ds.variables])
            meta = Multidimensional_Annotation_App._nc_info(ds)
            return data_vars, all_names, meta
        finally:
            try:
                ds.close()
            except Exception:
                pass

    @staticmethod
    def _nc_info(ds) -> dict:
        names = set(ds.variables) | set(ds.coords)
        time_name = next(
            (c for c in ("time", "valid_time", "forecast_time", "verification_time", "datetime", "date") if c in names),
            None,
        )
        lat_name = next((c for c in ("lat", "latitude", "y") if c in names), None)
        lon_name = next((c for c in ("lon", "longitude", "long", "x") if c in names), None)
        level_name = next(
            (
                c
                for c in ("level", "lev", "plev", "pressure", "pressure_level", "isobaricInhPa", "isobaric_in_hPa")
                if c in names
            ),
            None,
        )

        time_text = "-"
        spatial_text = "-"
        level_text = "-"

        if time_name and time_name in ds:
            try:
                vals = pd.to_datetime(ds[time_name].values)
                time_text = f"{vals.min():%Y-%m-%d %H:%M:%S} — {vals.max():%Y-%m-%d %H:%M:%S}"
            except Exception:
                pass

        if lat_name and lon_name and lat_name in ds and lon_name in ds:
            try:
                spatial_text = (
                    f"lat[{float(ds[lat_name].min()):.3f}..{float(ds[lat_name].max()):.3f}], "
                    f"lon[{float(ds[lon_name].min()):.3f}..{float(ds[lon_name].max()):.3f}]"
                )
            except Exception:
                pass

        if level_name and level_name in ds:
            try:
                vals = ds[level_name].values
                if len(vals) <= 20:
                    level_text = ", ".join([str(v) for v in vals])
                else:
                    level_text = f"{len(vals)} levels, {vals[0]} … {vals[-1]}"
            except Exception:
                pass

        return {
            "time_name": time_name,
            "lat_name": lat_name,
            "lon_name": lon_name,
            "level_name": level_name,
            "time_text": time_text,
            "spatial_text": spatial_text,
            "level_text": level_text,
        }

    def _set_coord_selectors(self, all_names: List[str], meta: dict) -> None:
        for widget in (self.nc_time_var, self.nc_lat_var, self.nc_lon_var, self.nc_level_var):
            widget.options = all_names
        self.nc_time_var.value = meta.get("time_name") if meta.get("time_name") in all_names else None
        self.nc_lat_var.value = meta.get("lat_name") if meta.get("lat_name") in all_names else None
        self.nc_lon_var.value = meta.get("lon_name") if meta.get("lon_name") in all_names else None
        self.nc_level_var.value = meta.get("level_name") if meta.get("level_name") in all_names else None

    def _update_env_info(self, file_name="-", meta=None) -> None:
        multilevel = self._unique_values(self.multilevel_continuous_vars.value, self.multilevel_categorical_vars.value)
        surface = self._unique_values(self.surface_continuous_vars.value, self.surface_categorical_vars.value)
        meta = meta or {}
        self.env_info.object = (
            f"File: {file_name} <br>"
            f"Multilevel parameters: {', '.join(multilevel) if multilevel else '-'} <br>"
            f"Surface parameters: {', '.join(surface) if surface else '-'} <br>"
            f"Time range: {meta.get('time_text', '-')} <br>"
            f"Spatial range: {meta.get('spatial_text', '-')} <br>"
            f"Vertical levels: {meta.get('level_text', '-')} <br>"
        )

    @staticmethod
    def _unique_values(*lists):
        out, seen = [], set()
        for values in lists:
            for v in list(values or []):
                if v not in seen:
                    seen.add(v)
                    out.append(v)
        return out

    def _on_load_movement(self, event=None) -> None:
        path_value = self._file_value(self.movement_csv.value)
        if not path_value:
            self._append_log("No movement CSV selected.")
            return

        path = Path(str(path_value)).expanduser()
        if not path.exists() or not path.is_file():
            self._append_log(f"Movement CSV does not exist or is not a file: {path}")
            return

        try:
            df_sample = pd.read_csv(path, nrows=100)
            full_df = pd.read_csv(path)
        except Exception as exc:
            self._append_log(f"Failed to read movement CSV: {exc}")
            return

        columns = list(df_sample.columns)
        self._movement_columns = columns
        self._movement_df = full_df

        for widget in (
            self.id_column,
            self.time_column,
            self.lat_column,
            self.lon_column,
            self.height_column,
            self.heading_column,
        ):
            widget.options = columns

        self.id_column.value = self._guess_column(
            columns, ["individual_local_identifier", "individual-local-identifier", "id"]
        )
        self.time_column.value = self._guess_column(
            columns, ["timestamp", "eobs_start_timestamp", "time", "datetime", "date"]
        )
        self.lat_column.value = self._guess_column(columns, ["location_lat", "location-lat", "lat", "latitude"])
        self.lon_column.value = self._guess_column(
            columns, ["location_lon", "location_long", "location-long", "lon", "longitude"]
        )
        self.height_column.value = self._guess_column(
            columns,
            ["height-above-ellipsoid", "height_above_ellipsoid", "height", "altitude", "elevation", "height_above_msl"],
        )
        self.heading_column.value = self._guess_column(columns, ["heading", "bearing", "direction"])

        self._populate_taxa_ids_and_info(path, full_df)
        self._append_log(f"Movement CSV loaded: {len(columns)} column(s) detected.")
        self._update_dynamic_states()
        self._refresh_preview()

    def _populate_taxa_ids_and_info(self, path: Path, df: pd.DataFrame) -> None:
        id_col = self.id_column.value
        taxon_col = self._guess_column(
            list(df.columns), ["individual-taxon-canonical-name", "individual_taxon_canonical_name", "taxon", "species"]
        )

        ids = sorted(df[id_col].dropna().astype(str).unique()) if id_col and id_col in df.columns else []
        taxa = sorted(df[taxon_col].dropna().astype(str).unique()) if taxon_col and taxon_col in df.columns else []

        self.id_multiselect.options = ids
        self.id_multiselect.value = ids
        self.taxon_multiselect.options = taxa
        self.taxon_multiselect.value = []

        time_text = "-"
        if self.time_column.value and self.time_column.value in df.columns:
            ts = pd.to_datetime(df[self.time_column.value], errors="coerce", dayfirst=True)
            if ts.notna().any():
                time_text = f"{ts.min():%Y-%m-%d %H:%M:%S} — {ts.max():%Y-%m-%d %H:%M:%S}"

        spatial_text = "-"
        if self.lat_column.value in df.columns and self.lon_column.value in df.columns:
            lat = pd.to_numeric(df[self.lat_column.value], errors="coerce")
            lon = pd.to_numeric(df[self.lon_column.value], errors="coerce")
            if lat.notna().any() and lon.notna().any():
                spatial_text = f"lat[{float(lat.min()):.3f}..{float(lat.max()):.3f}], lon[{float(lon.min()):.3f}..{float(lon.max()):.3f}]"

        self.movement_info.object = (
            f"File: {path.name} <br>Taxons: {len(taxa)} <br>IDs: {len(ids)} <br>"
            f"Time range: {time_text} <br>Spatial range: {spatial_text} <br>"
        )

    def _update_ids_by_taxon(self, event=None) -> None:
        if self._movement_df is None:
            return
        df = self._movement_df
        id_col = self.id_column.value
        if not id_col or id_col not in df.columns:
            return
        taxon_col = self._guess_column(
            list(df.columns), ["individual-taxon-canonical-name", "individual_taxon_canonical_name", "taxon", "species"]
        )
        selected_taxa = list(self.taxon_multiselect.value or [])
        if selected_taxa and taxon_col and taxon_col in df.columns:
            ids = sorted(df.loc[df[taxon_col].astype(str).isin(selected_taxa), id_col].dropna().astype(str).unique())
        else:
            ids = sorted(df[id_col].dropna().astype(str).unique())
        self.id_multiselect.options = ids
        self.id_multiselect.value = ids
        self._refresh_preview()

    def _on_scan_geopotential(self, event=None) -> None:
        try:
            vars_, all_names, meta = self._read_nc_metadata(self.geopotential_file.value)
        except Exception as exc:
            self._append_log(f"Failed to scan geopotential file: {exc}")
            return
        self.geopotential_variable.options = vars_
        self.geopotential_variable.value = vars_[0] if vars_ else None
        self._set_coord_selectors(all_names, meta)
        self._append_log(f"Scanned geopotential file: {len(vars_)} variable(s) found.")
        self._refresh_preview()

    def _on_scan_multilevel(self, event=None) -> None:
        try:
            vars_, all_names, meta = self._read_nc_metadata(self.multilevel_var_file.value)
        except Exception as exc:
            self._append_log(f"Failed to scan multilevel file: {exc}")
            return
        self.multilevel_variable.options = vars_
        self.multilevel_variable.value = vars_[0] if vars_ else None
        self.multilevel_continuous_vars.options = vars_
        self.multilevel_categorical_vars.options = vars_
        self.multilevel_continuous_vars.value = vars_
        self.multilevel_categorical_vars.value = []
        if not self.nc_time_var.options:
            self._set_coord_selectors(all_names, meta)
        self._update_env_info(Path(str(self._file_value(self.multilevel_var_file.value))).name, meta)
        self._append_log(f"Scanned multilevel file: {len(vars_)} variable(s) found.")
        self._refresh_preview()

    def _on_scan_surface(self, event=None) -> None:
        try:
            vars_, _, meta = self._read_nc_metadata(self.surface_var_file.value)
        except Exception as exc:
            self._append_log(f"Failed to scan surface file: {exc}")
            return
        self.surface_variable.options = vars_
        self.surface_variable.value = vars_[0] if vars_ else None
        self.surface_continuous_vars.options = vars_
        self.surface_categorical_vars.options = vars_
        self.surface_continuous_vars.value = vars_
        self.surface_categorical_vars.value = []
        self._update_env_info(Path(str(self._file_value(self.surface_var_file.value))).name, meta)
        self._append_log(f"Scanned surface file: {len(vars_)} variable(s) found.")
        self._refresh_preview()

    def _scan_nc_to_select(self, path_widget, select_widget, label: str) -> None:
        if not self._path_exists(path_widget.value):
            return
        try:
            vars_, _, _ = self._read_nc_metadata(path_widget.value)
        except Exception as exc:
            self._append_log(f"Failed to scan {label} file: {exc}")
            return
        select_widget.options = vars_
        select_widget.value = vars_[0] if vars_ else None
        self._append_log(f"Scanned {label} file: {len(vars_)} variable(s) found.")
        self._refresh_preview()

    def _on_scan_optional_components(self, event=None) -> None:
        scan_items = [
            (self.u_file, self.u_variable, "pressure-level U component"),
            (self.v_file, self.v_variable, "pressure-level V component"),
            (self.w_file, self.w_variable, "pressure-level W component"),
            (self.temperature_file, self.temperature_variable, "pressure-level temperature"),
            (self.surface_heat_flux_file, self.surface_heat_flux_variable, "surface sensible heat flux"),
            (self.boundary_layer_height_file, self.boundary_layer_height_variable, "boundary-layer height"),
            (self.temperature_2m_file, self.temperature_2m_variable, "2 m temperature"),
            (self.u10_file, self.u10_variable, "10 m U wind component"),
            (self.v10_file, self.v10_variable, "10 m V wind component"),
        ]

        selected_count = 0

        for path_widget, select_widget, label in scan_items:
            if not self._path_exists(path_widget.value):
                continue

            selected_count += 1
            self._scan_nc_to_select(path_widget, select_widget, label)

        if selected_count == 0:
            self._append_log("No derived-metric input files were selected.")

        self._update_dynamic_states()

    def _on_load_boundary(self, event=None) -> None:
        path = self._optional_path(self.boundary_file.value)
        if not path:
            self._append_log("No boundary file selected.")
            return
        try:
            if load_vector_extent_info is not None:
                loaded_path, south, north, west, east = load_vector_extent_info(path)
            else:
                if gpd is None:
                    raise RuntimeError("geopandas is not available.")
                gdf = gpd.read_file(path)
                west, south, east, north = gdf.total_bounds
                loaded_path = path
            self.boundary_path = str(loaded_path)
            self.boundary_info.object = (
                f"Boundary file: {Path(loaded_path).name} <br>"
                f"Spatial range: lat[{south:.3f}..{north:.3f}], lon[{west:.3f}..{east:.3f}]"
            )
            self._append_log(f"Boundary loaded: {Path(loaded_path).name}")
        except Exception as exc:
            self._append_log(f"Failed to load boundary: {exc}")

    def _on_reset_boundary(self, event=None) -> None:
        self.boundary_path = None
        self.boundary_info.object = "Boundary file: not selected <br>Spatial range: = environmental data boundary"
        self._append_log("Boundary reset to environmental data boundary.")

    def _update_smoothing_options(self, event=None) -> None:
        if self.spatial_interpolation_method.value == "Nearest neighbor":
            self.control_smoothing.options = ["1"]
            self.control_smoothing.value = "1"
        else:
            self.control_smoothing.options = ["2", "4", "6", "8"]
            if self.control_smoothing.value not in self.control_smoothing.options:
                self.control_smoothing.value = "4"

    def _update_dynamic_states(self, *_events) -> None:
        has_u = self._path_exists(self.u_file.value) and bool(self.u_variable.value)
        has_v = self._path_exists(self.v_file.value) and bool(self.v_variable.value)
        has_w = self._path_exists(self.w_file.value) and bool(self.w_variable.value)
        has_pressure_temperature = self._path_exists(self.temperature_file.value) and bool(
            self.temperature_variable.value
        )

        has_surface_heat_flux = self._path_exists(self.surface_heat_flux_file.value) and bool(
            self.surface_heat_flux_variable.value
        )
        has_boundary_layer_height = self._path_exists(self.boundary_layer_height_file.value) and bool(
            self.boundary_layer_height_variable.value
        )
        has_temperature_2m = self._path_exists(self.temperature_2m_file.value) and bool(
            self.temperature_2m_variable.value
        )
        has_u10 = self._path_exists(self.u10_file.value) and bool(self.u10_variable.value)
        has_v10 = self._path_exists(self.v10_file.value) and bool(self.v10_variable.value)
        has_dem = self.topography_source.value != "None" and self._path_exists(self.dem_file.value)
        heat_flux_is_accumulated = "accumulated energy" in str(self.surface_heat_flux_mode.value).lower()
        self.surface_heat_flux_accumulation_seconds.disabled = not heat_flux_is_accumulated
        self.dem_units.disabled = self.topography_source.value == "None"
        self.dem_reference.disabled = self.topography_source.value == "None"
        wind_ready = has_u and has_v
        thermal_ready = has_surface_heat_flux and has_boundary_layer_height and has_temperature_2m
        orographic_ready = has_u10 and has_v10 and has_dem
        self.derive_wind_speed_direction.disabled = not wind_ready
        self.derive_wind_support_crosswind.disabled = not wind_ready
        self.track_direction_source.disabled = not wind_ready
        self.heading_column.disabled = not (
            wind_ready and self.track_direction_source.value == "Use existing heading column"
        )

        self.derive_vertical_motion.disabled = not has_w
        self.derive_thermal_uplift.disabled = not thermal_ready
        self.derive_orographic_uplift.disabled = not orographic_ready

        if not wind_ready:
            self.derive_wind_speed_direction.value = False
            self.derive_wind_support_crosswind.value = False

        if not has_w:
            self.derive_vertical_motion.value = False

        if not thermal_ready:
            self.derive_thermal_uplift.value = False

        if not orographic_ready:
            self.derive_orographic_uplift.value = False

        self._update_derived_metrics_status()

    def _enforce_split_unique(self, first, second, changed: str, new_values: list):
        a = list(first.value or [])
        b = list(second.value or [])
        if changed == "first":
            overlap = set(new_values) & set(b)
            if overlap:
                second.value = [v for v in b if v not in overlap]
        else:
            overlap = set(new_values) & set(a)
            if overlap:
                first.value = [v for v in a if v not in overlap]
        self._update_env_info()
        self._refresh_preview()

    def _wire_variable_split_guards(self):
        self.multilevel_continuous_vars.param.watch(
            lambda e: self._enforce_split_unique(
                self.multilevel_continuous_vars, self.multilevel_categorical_vars, "first", list(e.new or [])
            ),
            "value",
        )
        self.multilevel_categorical_vars.param.watch(
            lambda e: self._enforce_split_unique(
                self.multilevel_continuous_vars, self.multilevel_categorical_vars, "second", list(e.new or [])
            ),
            "value",
        )
        self.surface_continuous_vars.param.watch(
            lambda e: self._enforce_split_unique(
                self.surface_continuous_vars, self.surface_categorical_vars, "first", list(e.new or [])
            ),
            "value",
        )
        self.surface_categorical_vars.param.watch(
            lambda e: self._enforce_split_unique(
                self.surface_continuous_vars, self.surface_categorical_vars, "second", list(e.new or [])
            ),
            "value",
        )

    def _selected_multilevel_vars(self) -> List[str]:
        return self._unique_values(self.multilevel_continuous_vars.value, self.multilevel_categorical_vars.value)

    def _selected_surface_vars(self) -> List[str]:
        return self._unique_values(self.surface_continuous_vars.value, self.surface_categorical_vars.value)

    def _first_or_none(self, values):
        values = list(values or [])
        return values[0] if values else None

    def _refresh_preview(self) -> None:
        lines = [
            "### Preview",
            f"- **Movement CSV:** `{self.movement_csv.value or '-'}`",
            f"- **Selected IDs:** `{len(self.id_multiselect.value or [])}`",
            f"- **Height column:** `{self.height_column.value or '-'}`",
            f"- **Geopotential file:** `{self.geopotential_file.value or '-'}`",
            f"- **Geopotential variable:** `{self.geopotential_variable.value or '-'}`",
            f"- **Multilevel file:** `{self.multilevel_var_file.value or '-'}`",
            f"- **Continuous multilevel variables:** `{list(self.multilevel_continuous_vars.value or [])}`",
            f"- **Categorical multilevel variables:** `{list(self.multilevel_categorical_vars.value or [])}`",
            f"- **Surface file:** `{self.surface_var_file.value or '-'}`",
            f"- **Continuous surface variables:** `{list(self.surface_continuous_vars.value or [])}`",
            f"- **Categorical surface variables:** `{list(self.surface_categorical_vars.value or [])}`",
            f"- **Spatial method:** `{self.spatial_interpolation_method.value}`",
            f"- **Nearest grid points:** `{self.control_smoothing.value}`",
            f"- **Vertical method:** `{self.vertical_matching_method.value}`",
            f"- **Boundary file:** `{self.boundary_path or '-'}`",
            f"- **Topography source:** `{self.topography_source.value}`",
            "",
            "**Derived metrics enabled:**",
            f"- Wind speed/direction: `{self.derive_wind_speed_direction.value}`",
            f"- Wind support/cross wind: `{self.derive_wind_support_crosswind.value}`",
            f"- Vertical motion: `{self.derive_vertical_motion.value}`",
            f"- Thermal proxy: `{self.derive_thermal_uplift.value}`",
            f"- Orographic uplift: `{self.derive_orographic_uplift.value}`",
        ]
        self.preview.object = "\n".join(lines)

    def _backend_height_reference(self) -> str:
        if self.height_reference.value == "WGS84 ellipsoidal height (Movebank GPS height)":
            return "ellipsoidal"
        if self.height_reference.value == "Height above ground level (requires DEM)":
            return "agl"
        return "already_orthometric"

    def _backend_heading_source(self) -> str:
        return "column" if self.track_direction_source.value == "Use existing heading column" else "compute"

    def _backend_heat_flux_mode(self) -> str:
        mapping = {
            "Upward-positive instantaneous flux (W m-2)": "upward_wm2",
            "Downward-positive instantaneous flux (W m-2)": "downward_wm2",
            "Downward-positive accumulated energy (J m-2)": "accumulated_downward_jm2",
            "Upward-positive accumulated energy (J m-2)": "accumulated_upward_jm2",
        }

        return mapping.get(self.surface_heat_flux_mode.value, "upward_wm2")

    def _on_validate(self, event=None) -> None:
        errors, warnings = [], []

        if not self._path_exists(self.movement_csv.value):
            errors.append("Movement CSV is missing or does not exist.")
        for name, widget in [
            ("ID column", self.id_column),
            ("Timestamp column", self.time_column),
            ("Latitude column", self.lat_column),
            ("Longitude column", self.lon_column),
            ("Height column", self.height_column),
        ]:
            if not widget.value:
                errors.append(f"{name} is not selected.")

        if not self._path_exists(self.geopotential_file.value):
            errors.append("Geopotential file is required and does not exist.")
        if not self.geopotential_variable.value:
            errors.append("Geopotential variable is not selected.")
        if not self._path_exists(self.multilevel_var_file.value):
            errors.append("Annotated multilevel variable file is required and does not exist.")
        if not self._selected_multilevel_vars():
            errors.append("No multilevel annotation variables selected.")

        if (
            self.surface_var_file.value
            and self._path_exists(self.surface_var_file.value)
            and not self._selected_surface_vars()
        ):
            errors.append("Surface variable file is set but no surface variable is selected.")
        if self.surface_var_file.value and not self._path_exists(self.surface_var_file.value):
            warnings.append(
                "Surface variable file is not selected or is not a file; surface variables will be ignored."
            )

        if not self.id_multiselect.value:
            warnings.append(
                "No individual IDs selected; backend currently processes all rows unless ID filtering is implemented."
            )
        if (
            self.spatial_interpolation_method.value == "Inverse Distance Weighting"
            and int(self.control_smoothing.value) < 2
        ):
            errors.append("IDW requires at least 2 nearest grid points.")
        if self.use_surface_as_lower_anchor.value and not self.surface_continuous_vars.value:
            warnings.append("Surface anchor is enabled, but no continuous surface variable is selected.")

        if self.topography_source.value != "None" and not self._path_exists(self.dem_file.value):
            errors.append("Topography source is selected, but DEM file is missing or does not exist.")
        if self.height_reference.value == "Height above ground level (requires DEM)" and not self._path_exists(
            self.dem_file.value
        ):
            errors.append("Height reference is AGL, but DEM file is missing or does not exist.")
        if self.height_reference.value == "Already orthometric / MSL-like":
            warnings.append(
                "Movement height is assumed to be already comparable to ERA5 geopotential height. No geoid correction will be applied."
            )
        if self.height_reference.value == "WGS84 ellipsoidal height (Movebank GPS height)":
            warnings.append(
                "Movement height will be converted to MSL/orthometric height using selected geoid correction mode."
            )
        if self.geopotential_units.value == "m2 s-2" and not self.convert_geopotential_to_height.value:
            warnings.append("Geopotential units are m2 s-2, but conversion to height is disabled.")
        if (
            self.derive_wind_support_crosswind.value
            and self.track_direction_source.value == "Use existing heading column"
            and not self.heading_column.value
        ):
            errors.append("Heading column is required when using existing heading column.")

        if self.derive_vertical_motion.value:
            if not (self._path_exists(self.w_file.value) and self.w_variable.value):
                errors.append("Vertical motion requires a W/omega file and variable.")

            if not (self._path_exists(self.temperature_file.value) and self.temperature_variable.value):
                warnings.append(
                    "Vertical motion is enabled without pressure-level "
                    "temperature. Approximate air density will be used."
                )

        if self.derive_thermal_uplift.value:
            thermal_missing = []

            if not (self._path_exists(self.surface_heat_flux_file.value) and self.surface_heat_flux_variable.value):
                thermal_missing.append("surface sensible heat flux")

            if not (
                self._path_exists(self.boundary_layer_height_file.value) and self.boundary_layer_height_variable.value
            ):
                thermal_missing.append("boundary-layer height")

            if not (self._path_exists(self.temperature_2m_file.value) and self.temperature_2m_variable.value):
                thermal_missing.append("2 m temperature")

            if thermal_missing:
                errors.append("Thermal uplift requires: " + ", ".join(thermal_missing) + ".")

            if (
                "accumulated energy" in str(self.surface_heat_flux_mode.value).lower()
                and float(self.surface_heat_flux_accumulation_seconds.value or 0.0) <= 0.0
            ):
                errors.append("Heat-flux accumulation period must be greater than zero.")

        if self.derive_orographic_uplift.value:
            orographic_missing = []

            if not (self._path_exists(self.u10_file.value) and self.u10_variable.value):
                orographic_missing.append("10 m U component")

            if not (self._path_exists(self.v10_file.value) and self.v10_variable.value):
                orographic_missing.append("10 m V component")

            if not self._path_exists(self.dem_file.value):
                orographic_missing.append("DEM")

            if orographic_missing:
                errors.append("Orographic uplift requires: " + ", ".join(orographic_missing) + ".")

        if errors:
            lines = ["### Validation", "**Status:** Issues found", "", *[f"- {e}" for e in errors]]
            if warnings:
                lines += ["", "**Warnings:**", *[f"- {w}" for w in warnings]]
            self.validation.object = "\n".join(lines)
            self._append_log(f"Validation completed with {len(errors)} error(s).")
        else:
            lines = ["### Validation", "**Status:** OK", "", "- UI configuration is sufficient for backend run."]
            if warnings:
                lines += ["", "**Warnings:**", *[f"- {w}" for w in warnings]]
            self.validation.object = "\n".join(lines)
            self._append_log("Validation completed successfully.")
        self._refresh_preview()

    def _on_run(self, event=None) -> None:
        self._on_validate()
        if "Issues found" in str(self.validation.object):
            self._append_log("Run cancelled because validation found errors.")
            return
        if run_multidimensional_annotation_from_paths is None:
            self._append_log(f"Backend import failed: {BACKEND_IMPORT_ERROR}")
            return

        self.run_button.disabled = True
        self.run_button.name = "Running multidimensional annotation..."
        try:
            output_csv = Path(str(self.output_csv.value)).expanduser()
            output_csv.parent.mkdir(parents=True, exist_ok=True)
            multilevel_cont = list(self.multilevel_continuous_vars.value or [])
            multilevel_cat = list(self.multilevel_categorical_vars.value or [])
            surface_cont = list(self.surface_continuous_vars.value or [])
            surface_cat = list(self.surface_categorical_vars.value or [])
            surface_file = self._optional_path(self.surface_var_file.value) if (surface_cont or surface_cat) else None

            self._append_log("Starting multidimensional annotation.")

            result = run_multidimensional_annotation_from_paths(
                movement_csv=self._optional_path(self.movement_csv.value),
                output_csv=str(output_csv),
                id_col=self.id_column.value,
                selected_ids=list(self.id_multiselect.value) if self.id_multiselect.value else None,
                time_col=self.time_column.value,
                lat_col=self.lat_column.value,
                lon_col=self.lon_column.value,
                boundary_path=self.boundary_path or None,
                height_col=self.height_column.value,
                geopotential_file=self._optional_path(self.geopotential_file.value),
                geopotential_variable=self.geopotential_variable.value,
                geopotential_units=self.geopotential_units.value,
                convert_geopotential_to_height=bool(self.convert_geopotential_to_height.value),
                nc_time_var=self.nc_time_var.value or None,
                nc_lat_var=self.nc_lat_var.value or None,
                nc_lon_var=self.nc_lon_var.value or None,
                nc_level_var=self.nc_level_var.value or None,
                multilevel_var_file=self._optional_path(self.multilevel_var_file.value),
                surface_var_file=surface_file,
                multilevel_continuous_vars=multilevel_cont,
                multilevel_categorical_vars=multilevel_cat,
                surface_continuous_vars=surface_cont,
                surface_categorical_vars=surface_cat,
                dem_file=self._optional_path(self.dem_file.value),
                save_per_individual=bool(self.save_per_individual.value),
                keep_diagnostics=bool(self.keep_diagnostics.value),
                vertical_matching_method=self.vertical_matching_method.value,
                height_reference=self._backend_height_reference(),
                geoid_mode=self.geoid_mode.value,
                constant_geoid_undulation_m=float(self.constant_geoid_undulation_m.value or 0.0),
                u_file=self._optional_path(self.u_file.value),
                u_variable=self.u_variable.value if self._path_exists(self.u_file.value) else None,
                v_file=self._optional_path(self.v_file.value),
                v_variable=self.v_variable.value if self._path_exists(self.v_file.value) else None,
                w_file=self._optional_path(self.w_file.value),
                w_variable=self.w_variable.value if self._path_exists(self.w_file.value) else None,
                temperature_file=self._optional_path(self.temperature_file.value),
                temperature_variable=(
                    self.temperature_variable.value if self._path_exists(self.temperature_file.value) else None
                ),
                surface_heat_flux_file=self._optional_path(self.surface_heat_flux_file.value),
                surface_heat_flux_variable=(
                    self.surface_heat_flux_variable.value
                    if self._path_exists(self.surface_heat_flux_file.value)
                    else None
                ),
                surface_heat_flux_mode=self._backend_heat_flux_mode(),
                surface_heat_flux_accumulation_seconds=float(
                    self.surface_heat_flux_accumulation_seconds.value or 3600.0
                ),
                boundary_layer_height_file=self._optional_path(self.boundary_layer_height_file.value),
                boundary_layer_height_variable=(
                    self.boundary_layer_height_variable.value
                    if self._path_exists(self.boundary_layer_height_file.value)
                    else None
                ),
                temperature_2m_file=self._optional_path(self.temperature_2m_file.value),
                temperature_2m_variable=(
                    self.temperature_2m_variable.value if self._path_exists(self.temperature_2m_file.value) else None
                ),
                u10_file=self._optional_path(self.u10_file.value),
                u10_variable=(self.u10_variable.value if self._path_exists(self.u10_file.value) else None),
                v10_file=self._optional_path(self.v10_file.value),
                v10_variable=(self.v10_variable.value if self._path_exists(self.v10_file.value) else None),
                derive_wind_speed_direction=bool(self.derive_wind_speed_direction.value),
                derive_wind_support_crosswind=bool(self.derive_wind_support_crosswind.value),
                derive_vertical_motion=bool(self.derive_vertical_motion.value),
                derive_thermal_proxy=bool(self.derive_thermal_uplift.value),
                smoothing_k=int(self.control_smoothing.value),
                derive_orographic_uplift=bool(self.derive_orographic_uplift.value),
                heading_col=self.heading_column.value,
                heading_source=self._backend_heading_source(),
            )

            n_rows = len(result) if result is not None else 0
            n_cols = len(result.columns) if result is not None else 0
            self._append_log(f"Annotation completed: {n_rows} row(s), {n_cols} column(s).")
            self._append_log(f"Output saved to: {output_csv}")
            preview_cols = list(result.columns[:20]) if result is not None else []
            self.preview.object = "\n".join(
                [
                    "### Preview",
                    f"- **Output CSV:** `{output_csv}`",
                    f"- **Rows:** `{n_rows}`",
                    f"- **Columns:** `{n_cols}`",
                    "",
                    "**First output columns:**",
                    *[f"- `{col}`" for col in preview_cols],
                ]
            )
        except Exception as exc:
            logger.exception("Multidimensional annotation failed.")
            self._append_log(f"Annotation failed: {exc}")
        finally:
            self.run_button.disabled = False
            self.run_button.name = "Run multidimensional annotation"

    def _card(self, title: str, *items):
        return pn.Card(
            pn.Column(*items, sizing_mode="stretch_width"),
            title=title,
            collapsible=True,
            collapsed=False,
            sizing_mode="stretch_width",
            margin=0,
            styles={"margin": "0px", "border-radius": "0px"},
        )

    def view(self):
        COL_H = 3500
        col1 = pn.Column(
            self._card(
                "1. Movement data",
                self.movement_csv,
                self.load_movement_button,
                self.taxon_multiselect,
                self.id_multiselect,
                self.id_column,
                self.time_column,
                self.lat_column,
                self.lon_column,
                self.height_column,
                self.height_units,
                self.height_reference,
                self.geoid_mode,
                self.constant_geoid_undulation_m,
                self.movement_info,
            ),
            self._card(
                "2. Vertical reference / geopotential",
                self.geopotential_file,
                self.scan_geopotential_button,
                self.geopotential_variable,
                self.nc_time_var,
                self.nc_lat_var,
                self.nc_lon_var,
                self.nc_level_var,
                self.geopotential_units,
                self.convert_geopotential_to_height,
                self.gravity_constant,
            ),
            sizing_mode="stretch_width",
            height=COL_H,
            margin=0,
            styles={"gap": "0px", "display": "flex", "flex-direction": "column"},
        )

        col2 = pn.Column(
            self._card(
                "3. Annotation variables",
                self.multilevel_var_file,
                self.scan_multilevel_button,
                self.multilevel_continuous_vars,
                self.multilevel_categorical_vars,
                self.surface_var_file,
                self.scan_surface_button,
                self.surface_continuous_vars,
                self.surface_categorical_vars,
                self.env_info,
            ),
            sizing_mode="stretch_width",
            height=COL_H,
            margin=0,
            styles={"gap": "0px", "display": "flex", "flex-direction": "column"},
        )

        col3 = pn.Column(
            self._card(
                "4. Boundary data",
                self.boundary_file,
                pn.Row(self.load_boundary_button, self.reset_boundary_button),
                self.boundary_info,
            ),
            self._card(
                "5. Interpolation / vertical matching",
                self.spatial_interpolation_method,
                self.control_smoothing,
                self.vertical_matching_method,
                self.use_surface_as_lower_anchor,
                self.surface_anchor_height_agl_m,
            ),
            self._card("6. Topography", self.topography_source, self.dem_file, self.dem_units, self.dem_reference),
            self._card(
                "7. Derived metrics",
                pn.pane.Markdown(
                    ("Configure optional atmospheric components and " "select the derived metrics to calculate."),
                    sizing_mode="stretch_width",
                ),
                self.open_derived_metrics_button,
                self.derived_metrics_status,
            ),
            self._card(
                "8. Output",
                self.output_csv,
                self.save_per_individual,
                self.keep_diagnostics,
                self.validate_button,
                self.run_button,
            ),
            sizing_mode="stretch_width",
            height=COL_H,
            margin=0,
            styles={"gap": "0px", "display": "flex", "flex-direction": "column"},
        )

        return pn.Column(
            "# Multidimensional Annotation Engine App (ERA5 only)",
            pn.GridBox(col1, col2, col3, ncols=3, sizing_mode="stretch_width", height=COL_H, scroll=True),
            pn.Row(self.preview, self.validation, self.log, sizing_mode="stretch_width"),
            sizing_mode="stretch_width",
        )


@register_view(ext_args=[])
def view():
    app = Multidimensional_Annotation_App()
    template = DEFAULT_TEMPLATE(main=[app.view()], sidebar=[])
    template.modal.append(app.derived_metrics_modal_content)
    app.template = template
    
    return template


if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})


if __name__.startswith("bokeh"):
    view()
