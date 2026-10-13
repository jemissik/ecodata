import logging
from pathlib import Path
import panel as pn
import param
import pandas as pd
import numpy as np
from panel.io.loading import start_loading_spinner, stop_loading_spinner
from ecodata.app.models import FileSelector
from ecodata.panel_utils import param_widget, register_view, try_catch, rename_param_widgets
from ecodata.app.config import DEFAULT_TEMPLATE
from ecodata import load_vector_extent_info, load_taxa_and_ids_from_csv
from ecodata.annotation_eng_func import (
    start_annotation_process,
    convert_tif_to_nc_before_annotation,
    get_nc_bounds,
    safe_open_nc_with_time_decoding,
    validate_bbox,
    detect_time_name,
    normalize_longitude_values,
)
from ecodata.netcdf_adapters import inspect_open_dataset_dict

logger = logging.getLogger(__file__)

# Stable grid-profile labels shared with NCBuilder.
PROFILE_REGULAR = "Regular geographic"
PROFILE_PROJECTED = "Projected rectilinear"
PROFILE_CURVILINEAR = "Curvilinear geographic"
PROFILE_MANUAL = "Custom/manual"
CSV_FORMAT_MOVEBANK = "Movebank-compatible format"
CSV_FORMAT_CUSTOM = "Custom format"
CSV_FILE_TYPE_OPTIONS = [CSV_FORMAT_MOVEBANK, CSV_FORMAT_CUSTOM]
BOUNDARY_MODE_VECTOR = "From .shp/.geojson"
BOUNDARY_MODE_BBOX = "bbox"
BOUNDARY_MODE_OPTIONS = [BOUNDARY_MODE_VECTOR, BOUNDARY_MODE_BBOX]
TIME_RANGE_MODE_DELETE = "delete"
TIME_RANGE_MODE_PRESERVE = "preserve"
TIME_RANGE_MODE_OPTIONS = {
    "Delete records outside NetCDF range": TIME_RANGE_MODE_DELETE,
    "Preserve records outside NetCDF range": TIME_RANGE_MODE_PRESERVE,
}
GRID_PROFILE_OPTIONS = [PROFILE_REGULAR, PROFILE_PROJECTED, PROFILE_CURVILINEAR, PROFILE_MANUAL]
MANUAL_LEVEL_DIM_CANDIDATES = (
    "isobaricInhPa",
    "isobaric_in_hPa",
    "isobaricInPa",
    "pressure_level",
    "level",
    "lev",
    "plev",
    "model_level",
    "height",
    "altitude",
    "depth",
    "sigma",
    "hybrid",
)

MANUAL_HELPER_DIMS = {"bnds", "bounds", "nv", "vertex", "vertices"}
MANUAL_EXPVER_AUTO = "__auto_combine__"
# Compatibility bridge for the currently installed adapter package.
# The user sees the standardized grid names, while existing adapters continue
# to work unchanged until their public profile names are updated separately.
ADAPTER_PROFILE_ALIASES = {
    PROFILE_REGULAR: "Regular geographic lat/lon",
    PROFILE_PROJECTED: "NARR projected grid",
    PROFILE_MANUAL: "Custom/manual",
}


class movebank_annotation_engine(param.Parameterized):
    # === Annotation Engine widgets ===
    env_dataset_profile = pn.widgets.Select(
        name="NetCDF grid profile", options=GRID_PROFILE_OPTIONS, value=PROFILE_REGULAR
    )
    env_profile_info = pn.pane.HTML(
        "Profile: not validated <br>Grid type: - <br>Coordinates: - <br>Supported interpolation: - <br>Validation: -",
        sizing_mode="stretch_width",
    )
    env_data_selector = param_widget(
        FileSelector(name="Environmental data (.nc)", constrain_path=False, expanded=True, size=10)
    )
    bound_data_selector = param_widget(
        FileSelector(name="Boundary data (.shp/.geojson)", constrain_path=False, expanded=True, size=10)
    )
    boundary_mode = pn.widgets.Select(name="Boundary type", options=BOUNDARY_MODE_OPTIONS, value=BOUNDARY_MODE_VECTOR)

    env_files_multiselect = pn.widgets.MultiSelect(
        name="NetCDF files for annotation (use Ctrl or ⌘ for multiple selection)", options={}, value=[], height=180
    )

    boundary_south = pn.widgets.FloatInput(name="South latitude", value=None, step=0.1)
    boundary_north = pn.widgets.FloatInput(name="North latitude", value=None, step=0.1)
    boundary_west = pn.widgets.FloatInput(name="West longitude", value=None, step=0.1)
    boundary_east = pn.widgets.FloatInput(name="East longitude", value=None, step=0.1)
    movement_csv_type = pn.widgets.Select(
        name="CSV file type", options=CSV_FILE_TYPE_OPTIONS, value=CSV_FORMAT_MOVEBANK
    )

    movement_taxon_column = pn.widgets.Select(name="Taxon column", options={"— select column —": None}, value=None)
    movement_id_column = pn.widgets.Select(name="Animal ID column", options={"— select column —": None}, value=None)
    movement_time_column = pn.widgets.Select(name="Time column", options={"— select column —": None}, value=None)
    movement_lat_column = pn.widgets.Select(name="Latitude column", options={"— select column —": None}, value=None)
    movement_lon_column = pn.widgets.Select(name="Longitude column", options={"— select column —": None}, value=None)
    movement_data_selector = param_widget(
        FileSelector(name="Movement data (.csv)", constrain_path=False, expanded=True, size=10)
    )
    load_env_button = pn.widgets.Button(name="Load environmental data", button_type="primary")
    load_movement_button = pn.widgets.Button(name="Load movement data", button_type="primary")
    load_bound_button = pn.widgets.Button(name="Load boundary data", button_type="primary")
    reset_bound_button = pn.widgets.Button(name="(!) Reset boundary", button_type="primary")
    nc_time_var = pn.widgets.Select(name="Time variable", options=[], value=None)
    nc_lat_var = pn.widgets.Select(name="Latitude variable", options=[], value=None)
    nc_lon_var = pn.widgets.Select(name="Longitude variable", options=[], value=None)
    env_spatial_mode = pn.widgets.RadioButtonGroup(
        name="Detected spatial coordinate mode",
        options=["Regular geographic (lat/lon)", "Projected rectilinear (x/y)", "Curvilinear geographic (2D lat/lon)"],
        value="Regular geographic (lat/lon)",
        button_type="default",
        disabled=True,
    )
    env_x_select = pn.widgets.Select(name="X coordinate", options=[], value=None)
    env_y_select = pn.widgets.Select(name="Y coordinate", options=[], value=None)
    manual_config_file = pn.widgets.Select(name="Configure file", options={}, value=None)
    manual_vertical_dim = pn.widgets.Select(
        name="Vertical coordinate / dimension", options={"— none —": None}, value=None
    )
    manual_vertical_level = pn.widgets.Select(
        name="Vertical level", options={"— none —": None}, value=None, disabled=True
    )
    manual_grid_mapping_var = pn.widgets.Select(
        name="Grid mapping / CRS variable", options={"— none —": None}, value=None
    )
    manual_structure_info = pn.pane.Markdown(
        "Load a NetCDF file to inspect its structure.", sizing_mode="stretch_width"
    )
    env_continuous_selector = pn.widgets.MultiSelect(
        name="Continuous (use Ctrl or ⌘ for multiple selection)", options=[], value=[], height=180
    )

    env_categorical_selector = pn.widgets.MultiSelect(
        name="Categorical (use Ctrl or ⌘ for multiple selection)", options=[], value=[], height=180
    )
    taxon_multiselect = pn.widgets.MultiSelect(name="Select Taxon (use Ctrl or ⌘ for multiple)", height=140)
    id_multiselect = pn.widgets.MultiSelect(name="Select ID (use Ctrl or ⌘ for multiple)", height=140)
    env_info = pn.pane.HTML(
        "File: not selected <br>Environment parameters: - <br>Time range: - <br>Spatial range: - <br>",
        sizing_mode="stretch_width",
    )
    movement_info = pn.pane.HTML(
        "File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: - <br>",
        sizing_mode="stretch_width",
    )
    control_smoothing = pn.widgets.Select(name="Number of nearest grid points", options=["2", "4", "6", "8"], value="4")
    time_range_mode = pn.widgets.Select(
        name="Movement records outside environmental time range",
        options=TIME_RANGE_MODE_OPTIONS,
        value=TIME_RANGE_MODE_PRESERVE,
    )
    output_path = pn.widgets.TextInput(name="Output path", value=str(Path.home() / "Downloads" / "annotated_env.csv"))
    boundary_info_str = pn.pane.HTML(
        "Boundary file: not selected <br>Spatial range: = environment data boundary",
        name="",
        styles={"white-space": "pre-wrap"},
        sizing_mode="stretch_width",
    )
    interpolation_method = pn.widgets.Select(
        name="Interpolation method (spatial)",
        options=[
            "Nearest neighbor (time-linear)",
            "Inverse Distance Weighting (time-linear)",
            "Bilinear (projected x/y, time-linear)",
        ],
        value="Inverse Distance Weighting (time-linear)",
    )
    make_annotation_button = pn.widgets.Button(name="Make annotated file", button_type="primary")

    status_text = param.String("Ready...")
    # TIF widgets
    # === TIF Annotation Engine widgets ===
    tif_env_data_selector = param_widget(
        FileSelector(name="Select any .tif file in folder", constrain_path=False, expanded=True, size=10)
    )
    tif_movement_csv_type = pn.widgets.Select(
        name="CSV file type", options=CSV_FILE_TYPE_OPTIONS, value=CSV_FORMAT_MOVEBANK
    )

    tif_movement_taxon_column = pn.widgets.Select(name="Taxon column", options={"— select column —": None}, value=None)
    tif_movement_id_column = pn.widgets.Select(name="Animal ID column", options={"— select column —": None}, value=None)
    tif_movement_time_column = pn.widgets.Select(name="Time column", options={"— select column —": None}, value=None)
    tif_movement_lat_column = pn.widgets.Select(name="Latitude column", options={"— select column —": None}, value=None)
    tif_movement_lon_column = pn.widgets.Select(
        name="Longitude column", options={"— select column —": None}, value=None
    )
    tif_movement_data_selector = param_widget(
        FileSelector(name="Movement data (.csv)", constrain_path=False, expanded=True, size=10)
    )
    tif_bound_data_selector = param_widget(
        FileSelector(name="Boundary data", constrain_path=False, expanded=True, size=10)
    )
    tif_boundary_mode = pn.widgets.Select(
        name="Boundary type", options=BOUNDARY_MODE_OPTIONS, value=BOUNDARY_MODE_VECTOR
    )

    tif_boundary_south = pn.widgets.FloatInput(name="South latitude", value=None, step=0.1)
    tif_boundary_north = pn.widgets.FloatInput(name="North latitude", value=None, step=0.1)
    tif_boundary_west = pn.widgets.FloatInput(name="West longitude", value=None, step=0.1)
    tif_boundary_east = pn.widgets.FloatInput(name="East longitude", value=None, step=0.1)
    tif_load_env_button = pn.widgets.Button(name="Load TIF environmental data", button_type="primary")
    tif_load_movement_button = pn.widgets.Button(name="Load movement data", button_type="primary")
    tif_load_bound_button = pn.widgets.Button(name="Load boundary data", button_type="primary")
    tif_reset_bound_button = pn.widgets.Button(name="(!) Reset boundary", button_type="primary")
    tif_control_smoothing = pn.widgets.Select(
        name="Number of nearest grid points", options=["2", "4", "6", "8"], value="4"
    )
    tif_time_range_mode = pn.widgets.Select(
        name="Movement records outside environmental time range",
        options=TIME_RANGE_MODE_OPTIONS,
        value=TIME_RANGE_MODE_PRESERVE,
    )
    tif_env_data_multiselect = pn.widgets.MultiSelect(
        name="Environmental variables (use Ctrl or ⌘ for multiple)", options=[], height=140
    )
    # TIF variable type: continuous vs categorical
    tif_continuous_vars = pn.widgets.MultiSelect(
        name="Continuous variables (use Ctrl or ⌘ for multiple)", options=[], value=[], size=8
    )
    tif_categorical_vars = pn.widgets.MultiSelect(
        name="Categorical/QC variables (use Ctrl or ⌘ for multiple)", options=[], value=[], size=8
    )
    # prevent recursive watcher updates
    _syncing_tif_var_types = False
    tif_taxon_multiselect = pn.widgets.MultiSelect(name="Select Taxon (use Ctrl or ⌘ for multiple)", height=140)
    tif_id_multiselect = pn.widgets.MultiSelect(name="Select ID (use Ctrl or ⌘ for multiple)", height=140)
    tif_env_info = pn.pane.HTML(
        "File: not selected <br>Environment parameters: - <br>Time range: - <br>Spatial range: - <br>",
        sizing_mode="stretch_width",
    )
    tif_movement_info = pn.pane.HTML(
        "File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: - <br>",
        sizing_mode="stretch_width",
    )
    tif_output_path = pn.widgets.TextInput(
        name="Output path", value=str(Path.home() / "Downloads" / "annotated_env_tif.csv")
    )
    tif_boundary_info_str = pn.pane.HTML(
        "Boundary file: not selected <br> Spatial range: = environment data boundary", sizing_mode="stretch_width"
    )
    # --- TIF scaling (optional) ---
    tif_apply_scale = pn.widgets.Checkbox(name="Apply scale factor / offset", value=False)
    tif_scale_factor = pn.widgets.FloatInput(name="Scale factor", value=1.0, step=0.0001, start=None, disabled=True)
    tif_add_offset = pn.widgets.FloatInput(name="Add offset", value=0.0, step=0.1, start=None, disabled=True)

    tif_interpolation_method = pn.widgets.Select(
        name="Interpolation method (spatial)",
        options=["Nearest neighbor (time-linear)", "Inverse Distance Weighting (time-linear)"],
        value="Inverse Distance Weighting (time-linear)",
    )
    tif_make_annotation_button = pn.widgets.Button(name="Make annotated file", button_type="primary")

    def __init__(self, **params):
        super().__init__(**params)

        self.interpolation_method.name = "Spatial interpolation method (.nc)"
        self.tif_interpolation_method.name = "Spatial interpolation method (.tif)"
        self._wire_env_split_guards()
        self._apply_env_selector_labels()
        rename_param_widgets(
            self,
            [
                # === NC Annotation tab ===
                "env_dataset_profile",
                "env_profile_info",
                "env_data_selector",
                "env_files_multiselect",
                "boundary_mode",
                "boundary_south",
                "boundary_north",
                "boundary_west",
                "boundary_east",
                "bound_data_selector",
                "movement_data_selector",
                "movement_csv_type",
                "movement_taxon_column",
                "movement_id_column",
                "movement_time_column",
                "movement_lat_column",
                "movement_lon_column",
                "load_env_button",
                "load_bound_button",
                "reset_bound_button",
                "load_movement_button",
                "env_continuous_selector",
                "env_categorical_selector",
                "taxon_multiselect",
                "id_multiselect",
                "boundary_info_str",
                "interpolation_method",
                "control_smoothing",
                "time_range_mode",
                "env_info",
                "movement_info",
                "output_path",
                "make_annotation_button",
                "nc_time_var",
                "nc_lat_var",
                "nc_lon_var",
                "env_spatial_mode",
                "env_x_select",
                "env_y_select",
                "manual_config_file",
                "manual_vertical_dim",
                "manual_vertical_level",
                "manual_grid_mapping_var",
                "manual_structure_info",
                # === TIF Annotation tab ===
                "tif_env_data_selector",
                "tif_movement_csv_type",
                "tif_movement_taxon_column",
                "tif_movement_id_column",
                "tif_movement_time_column",
                "tif_movement_lat_column",
                "tif_movement_lon_column",
                "tif_movement_data_selector",
                "tif_bound_data_selector",
                "tif_boundary_mode",
                "tif_boundary_south",
                "tif_boundary_north",
                "tif_boundary_west",
                "tif_boundary_east",
                "tif_reset_bound_button",
                "tif_env_data_multiselect",
                "tif_continuous_vars",
                "tif_categorical_vars",
                "tif_taxon_multiselect",
                "tif_id_multiselect",
                "tif_interpolation_method",
                "tif_control_smoothing",
                "tif_time_range_mode",
                "tif_apply_scale",
                "tif_scale_factor",
                "tif_add_offset",
                "tif_env_info",
                "tif_movement_info",
                "tif_make_annotation_button",
            ],
        )

        self.nc_movement_df = None
        self.tif_movement_df = None
        self.env_descriptor = None
        self.env_descriptors_by_file = {}
        self.env_variable_sources = {}
        self.env_loaded_paths = []
        self.nc_boundary_path = None
        self.nc_boundary_extent = None
        self.tif_boundary_path = None
        self.tif_boundary_extent = None
        self.alert = pn.pane.Markdown(self.status_text)
        # ===
        # Custom/manual NetCDF structure UI
        # ===
        self._manual_ui_updating = False
        self._manual_structure_metadata = {}
        self.manual_extra_dim_widgets = {}
        self._manual_extra_dims_panel = pn.Column(
            pn.pane.Markdown("*No additional dimensions detected.*"), sizing_mode="stretch_width"
        )

        self._manual_coordinate_panel = pn.Card(
            self.manual_structure_info,
            self.manual_config_file,
            pn.pane.Markdown("##### Spatial structure"),
            self.env_spatial_mode,
            self.nc_time_var,
            self.nc_lat_var,
            self.nc_lon_var,
            self.env_x_select,
            self.env_y_select,
            self.manual_grid_mapping_var,
            pn.layout.Divider(),
            pn.pane.Markdown("##### Vertical coordinate"),
            self.manual_vertical_dim,
            self.manual_vertical_level,
            pn.layout.Divider(),
            pn.pane.Markdown("##### Additional dimensions"),
            self._manual_extra_dims_panel,
            pn.pane.Markdown(
                "*Vertical and additional-dimension selections "
                "are currently UI-only and are not yet passed "
                "to the annotation backend.*"
            ),
            title="Manual NetCDF structure",
            collapsed=False,
            visible=False,
            sizing_mode="stretch_width",
        )
        NC_H = 1080
        # === NC tab  ===
        self._nc_col1 = self._section(
            "1. Environmental data (.nc)",
            self.env_dataset_profile,
            pn.Column(self.env_data_selector, sizing_mode="stretch_width"),
            self.env_files_multiselect,
            self.load_env_button,
            self.env_profile_info,
            self.env_info,
            # Visible only for Custom/manual.
            self._manual_coordinate_panel,
            pn.layout.Divider(),
            pn.pane.Markdown("#### Select environmental variables"),
            self.env_continuous_selector,
            self.env_categorical_selector,
            self.interpolation_method,
            self.control_smoothing,
            pn.layout.Divider(),
            self.output_path,
            height=NC_H + 700,
        )

        self._movement_custom_columns_panel = pn.Column(
            self.movement_taxon_column,
            self.movement_id_column,
            self.movement_time_column,
            self.movement_lat_column,
            self.movement_lon_column,
            visible=False,
            sizing_mode="stretch_width",
        )

        self._nc_col2 = self._section(
            "2. Movement data (.csv)",
            self.movement_csv_type,
            pn.Column(self.movement_data_selector, sizing_mode="stretch_width"),
            self._movement_custom_columns_panel,
            self.load_movement_button,
            self.taxon_multiselect,
            self.id_multiselect,
            self.movement_info,
            pn.layout.Divider(),
            self.time_range_mode,
            height=NC_H + 700,
        )

        ### additional panels
        self._nc_boundary_file_panel = pn.Column(
            pn.Column(self.bound_data_selector, sizing_mode="stretch_width"),
            self.load_bound_button,
            visible=True,
            sizing_mode="stretch_width",
        )

        self._nc_bbox_panel = pn.Column(
            pn.Row(self.boundary_south, self.boundary_north, sizing_mode="stretch_width"),
            pn.Row(self.boundary_west, self.boundary_east, sizing_mode="stretch_width"),
            visible=False,
            sizing_mode="stretch_width",
        )

        self._nc_col3 = self._section(
            "3. Boundary data (.shp/.geojson / bbox)",
            self.boundary_mode,
            self._nc_boundary_file_panel,
            self._nc_bbox_panel,
            self.reset_bound_button,
            self.boundary_info_str,
            pn.layout.Divider(),
            pn.pane.Markdown("### 4. Start annotation"),
            self.make_annotation_button,
            height=NC_H + 700,
        )

        # synchronize heights after rendering
        pn.state.onload(self._sync_nc_column_heights)

        self.anotation_engine_tab = pn.Column(
            pn.pane.Markdown("### Annotation engine - .nc", sizing_mode="stretch_width"),
            pn.GridBox(
                self._nc_col1,
                self._nc_col2,
                self._nc_col3,
                ncols=3,
                sizing_mode="stretch_width",
                height=1600,
                scroll=True,
            ),
        )

        # === TIF ===
        TIF_H = 1800
        self._tif_col1 = self._section(
            "1. Environmental data (.tif) - select one (of)",
            pn.Column(self.tif_env_data_selector, sizing_mode="stretch_width"),
            self.tif_load_env_button,
            self.tif_continuous_vars,
            self.tif_categorical_vars,
            pn.layout.Divider(),
            self.tif_env_info,
            self.tif_interpolation_method,
            self.tif_control_smoothing,
            self.tif_output_path,
            pn.pane.Markdown("### Post-sampling correction for continuous variables"),
            self.tif_apply_scale,
            self.tif_scale_factor,
            self.tif_add_offset,
            height=TIF_H,
        )
        self._tif_movement_custom_columns_panel = pn.Column(
            self.tif_movement_taxon_column,
            self.tif_movement_id_column,
            self.tif_movement_time_column,
            self.tif_movement_lat_column,
            self.tif_movement_lon_column,
            visible=False,
            sizing_mode="stretch_width",
        )

        self._tif_col2 = self._section(
            "2. Movement data (.csv)",
            self.tif_movement_csv_type,
            pn.Column(self.tif_movement_data_selector, sizing_mode="stretch_width"),
            self._tif_movement_custom_columns_panel,
            self.tif_load_movement_button,
            self.tif_taxon_multiselect,
            self.tif_id_multiselect,
            self.tif_movement_info,
            pn.layout.Divider(),
            self.tif_time_range_mode,
            height=TIF_H,
        )
        ### additional panels
        self._tif_boundary_file_panel = pn.Column(
            pn.Column(self.tif_bound_data_selector, sizing_mode="stretch_width"),
            self.tif_load_bound_button,
            visible=True,
            sizing_mode="stretch_width",
        )

        self._tif_bbox_panel = pn.Column(
            pn.Row(self.tif_boundary_south, self.tif_boundary_north, sizing_mode="stretch_width"),
            pn.Row(self.tif_boundary_west, self.tif_boundary_east, sizing_mode="stretch_width"),
            visible=False,
            sizing_mode="stretch_width",
        )

        self._tif_col3 = self._section(
            "3. Boundary data (.shp/.geojson / bbox)",
            self.tif_boundary_mode,
            self._tif_boundary_file_panel,
            self._tif_bbox_panel,
            self.tif_reset_bound_button,
            self.tif_boundary_info_str,
            pn.layout.Divider(),
            pn.pane.Markdown("### 4. Start annotation"),
            self.tif_make_annotation_button,
            height=TIF_H,
        )

        self.anotation_engine_tif_tab = pn.Column(
            pn.pane.Markdown("### Annotation engine - .tif", sizing_mode="stretch_width"),
            pn.GridBox(self._tif_col1, self._tif_col2, self._tif_col3, ncols=3, sizing_mode="stretch_width"),
        )

        self.view = pn.Tabs(
            ("Annotation engine - .nc", self.anotation_engine_tab),
            ("Annotation engine - .tif", self.anotation_engine_tif_tab),
        )

        self.load_env_button.on_click(self.load_env_data)
        self.load_bound_button.on_click(self.load_boundary_data)
        self.reset_bound_button.on_click(self.reset_boundary_data)
        self.boundary_mode.param.watch(self._on_boundary_mode_changed, "value")

        for widget in (self.boundary_south, self.boundary_north, self.boundary_west, self.boundary_east):
            widget.param.watch(self._on_boundary_bbox_changed, "value")

        self._apply_boundary_mode_ui()
        self.load_movement_button.on_click(self.load_movement_data)
        self.movement_csv_type.param.watch(self._on_movement_csv_type_changed, "value")
        self.movement_data_selector._directory.param.watch(self._on_movement_file_changed, "value")
        self._apply_movement_csv_type_ui()
        self.taxon_multiselect.param.watch(self.update_annotation_ids_by_taxon, "value")
        self.make_annotation_button.on_click(self.run_annotation)
        self.env_continuous_selector.param.watch(
            lambda e: self.update_env_info_text(self._get_selected_env_vars()), "value"
        )
        self.env_categorical_selector.param.watch(
            lambda e: self.update_env_info_text(self._get_selected_env_vars()), "value"
        )
        self.taxon_multiselect.param.watch(lambda e: self.update_movement_info_text("Taxons", e.new), "value")
        self.id_multiselect.param.watch(lambda e: self.update_movement_info_text("IDs", e.new), "value")
        self.interpolation_method.param.watch(self._update_smoothing_options, "value")
        self.env_dataset_profile.param.watch(self._on_env_profile_changed, "value")
        self.env_data_selector._directory.param.watch(self._on_env_file_changed, "value")
        self.env_files_multiselect.param.watch(self._on_env_files_changed, "value")
        self.env_spatial_mode.param.watch(self._apply_env_spatial_mode, "value")

        # ===
        # Custom/manual NetCDF structure watchers
        # ===
        self.manual_vertical_dim.param.watch(self._on_manual_vertical_dim_changed, "value")

        for widget in (
            self.nc_time_var,
            self.nc_lat_var,
            self.nc_lon_var,
            self.env_x_select,
            self.env_y_select,
            self.env_spatial_mode,
        ):
            widget.param.watch(self._on_manual_structure_role_changed, "value")

        self._apply_env_profile_ui()

        # ===TIF on click ===
        self.tif_load_env_button.on_click(self.load_env_data_tif)
        self.tif_env_data_selector._directory.param.watch(self._on_tif_env_file_changed, "value")
        self.tif_load_bound_button.on_click(self.load_boundary_data_tif)
        self.tif_reset_bound_button.on_click(self.reset_boundary_data_tif)
        self.tif_boundary_mode.param.watch(self._on_tif_boundary_mode_changed, "value")

        for widget in (
            self.tif_boundary_south,
            self.tif_boundary_north,
            self.tif_boundary_west,
            self.tif_boundary_east,
        ):
            widget.param.watch(self._on_tif_boundary_bbox_changed, "value")

        self._apply_tif_boundary_mode_ui()
        self.tif_load_movement_button.on_click(self.load_movement_data_tif)
        self.tif_movement_csv_type.param.watch(self._on_tif_movement_csv_type_changed, "value")
        self.tif_movement_data_selector._directory.param.watch(self._on_tif_movement_file_changed, "value")
        self._apply_tif_movement_csv_type_ui()
        self.tif_make_annotation_button.on_click(self.run_annotation_tif)
        self.tif_taxon_multiselect.param.watch(self.update_annotation_ids_by_taxon_tif, "value")
        self.tif_continuous_vars.param.watch(
            lambda e: self.update_env_info_text_tif(
                list(self.tif_continuous_vars.value or [])
                + [
                    v
                    for v in list(self.tif_categorical_vars.value or [])
                    if v not in list(self.tif_continuous_vars.value or [])
                ]
            ),
            "value",
        )
        self.tif_categorical_vars.param.watch(
            lambda e: self.update_env_info_text_tif(
                list(self.tif_continuous_vars.value or [])
                + [
                    v
                    for v in list(self.tif_categorical_vars.value or [])
                    if v not in list(self.tif_continuous_vars.value or [])
                ]
            ),
            "value",
        )
        self.tif_taxon_multiselect.param.watch(lambda e: self.update_movement_info_text_tif("Taxons", e.new), "value")
        self.tif_id_multiselect.param.watch(lambda e: self.update_movement_info_text_tif("IDs", e.new), "value")
        self.tif_interpolation_method.param.watch(self._update_smoothing_options_tif, "value")
        self.tif_apply_scale.param.watch(self._update_tif_scale_widgets, "value")
        self._update_tif_scale_widgets()
        self.tif_continuous_vars.param.watch(self._sync_tif_variable_type_selection, "value")
        self.tif_categorical_vars.param.watch(self._sync_tif_variable_type_selection, "value")


    def update_annotation_ids_by_taxon(self, event):
        if self.nc_movement_df is None:
            return

        selected_taxa = event.new

        if not selected_taxa:
            ids = sorted(self.nc_movement_df["individual_local_identifier"].dropna().astype(str).unique())
        else:
            filtered = self.nc_movement_df[self.nc_movement_df["individual_taxon_canonical_name"].isin(selected_taxa)]

            ids = sorted(filtered["individual_local_identifier"].dropna().astype(str).unique())

        self.id_multiselect.options = ids
        self.id_multiselect.value = ids


    def _is_categorical_var(self, var_name: str, da) -> bool:
        """
        classification:
        - QC/flag/mask/class/category in name -> categorical
        - integer dtype + flag_values/flag_meanings attrs -> categorical
        - integer dtype + small number of unique values (sample) -> categorical
        """
        name = (var_name or "").lower()
        name_hits = ["qc", "quality", "flag", "mask", "class", "category", "type", "landcover", "biome"]
        if any(h in name for h in name_hits):
            return True

        try:
            import numpy as np

            if np.issubdtype(da.dtype, np.integer):
                attrs = getattr(da, "attrs", {}) or {}
                if ("flag_values" in attrs) or ("flag_meanings" in attrs):
                    return True

                # sample uniqueness (avoid loading whole array)
                # take first time slice if possible
                sample = da
                for dim in da.dims:
                    if dim.lower() in ("time",):
                        sample = sample.isel({dim: 0})
                        break
                vals = sample.values
                flat = vals.ravel()
                flat = flat[:5000]  # cap
                flat = flat[~np.isnan(flat)] if flat.dtype.kind == "f" else flat
                uniq = np.unique(flat)
                if len(uniq) <= 32:
                    return True
        except Exception:
            pass

        return False

    def _enforce_env_split_unique(self, changed: str, new_values: list):
        """
        Ensure the same variable cannot be selected in both selectors.
        changed: "cont" or "cat"
        """
        cont = list(self.env_continuous_selector.value or [])
        cat = list(self.env_categorical_selector.value or [])

        if changed == "cont":
            # remove from categorical...
            overlap = set(new_values) & set(cat)
            if overlap:
                self.env_categorical_selector.value = [v for v in cat if v not in overlap]

        elif changed == "cat":
            overlap = set(new_values) & set(cont)
            if overlap:
                self.env_continuous_selector.value = [v for v in cont if v not in overlap]

    def _wire_env_split_guards(self):
        """
        Attach watchers for mutual exclusivity.
        Call once in __init__.
        """
        self.env_continuous_selector.param.watch(
            lambda e: self._enforce_env_split_unique("cont", list(e.new or [])), "value"
        )
        self.env_categorical_selector.param.watch(
            lambda e: self._enforce_env_split_unique("cat", list(e.new or [])), "value"
        )

    def _normalize_interp_key(self, ui_value: str) -> str:
        """
        Convert UI label -> internal key expected by annotation engine.
        Returns 'nearest' or 'idw' (fallback: original string).
        """
        s = (ui_value or "").strip().lower()
        if s.startswith("nearest"):
            return "nearest"
        if s.startswith("inverse") or "idw" in s:
            return "idw"
        if "bilinear" in s:
            return "bilinear"
        return ui_value

    def _apply_env_selector_labels(self):
        """Make selector purposes obvious in UI."""
        self.env_continuous_selector.name = "Continuous (use Ctrl or ⌘ for multiple)"
        self.env_categorical_selector.name = "Categorical/QC (use Ctrl or ⌘ for multiple)"

    def _reset_manual_structure_ui(self):
        self._manual_structure_metadata = {}
        self.manual_extra_dim_widgets = {}
        self.manual_config_file.options = {}
        self.manual_config_file.value = None
        self.manual_vertical_dim.options = {"— none —": None}
        self.manual_vertical_dim.value = None
        self.manual_vertical_level.options = {"— none —": None}
        self.manual_vertical_level.value = None
        self.manual_vertical_level.disabled = True
        self.manual_grid_mapping_var.options = {"— none —": None}
        self.manual_grid_mapping_var.value = None
        self.manual_structure_info.object = "Load a NetCDF file to inspect its structure."

        if hasattr(self, "_manual_extra_dims_panel"):
            self._manual_extra_dims_panel.objects = [pn.pane.Markdown("*No additional dimensions detected.*")]

    def _manual_python_scalar(self, value):
        if isinstance(value, np.generic):
            try:
                return value.item()
            except Exception:
                return str(value)

        return value

    def _manual_get_dimension_values(self, ds, dim_name, max_values=1000):
        size = int(ds.sizes.get(dim_name, 0))

        if size <= 0:
            return []

        # Large dimensions should not create thousands
        # of items in a Select widget.
        if size > max_values:
            return None

        if dim_name in ds.variables and ds[dim_name].ndim == 1 and ds[dim_name].dims == (dim_name,):
            values = np.asarray(ds[dim_name].values)

            return [self._manual_python_scalar(value) for value in values]

        return list(range(size))

    def _populate_manual_structure_ui(self, ds, nc_path, descriptor):
        """
        Populate Custom/manual controls from the currently
        loaded NetCDF file.
        This stage changes UI only. Vertical and extra-dimension
        selections are not yet passed to the backend.
        """

        self._manual_ui_updating = True

        try:
            # ===
            # Current file
            # ===
            self.manual_config_file.options = {Path(nc_path).name: nc_path}

            self.manual_config_file.value = nc_path

            # ===
            # Store lightweight NetCDF structure in memory
            # ===
            dimensions = {str(name): int(size) for name, size in ds.sizes.items()}

            variable_dims = {str(name): tuple(var.dims) for name, var in ds.variables.items()}

            used_data_dims = set()

            for var in ds.data_vars.values():
                used_data_dims.update(var.dims)

            dim_values = {}
            dim_units = {}

            for dim_name in dimensions:
                dim_values[dim_name] = self._manual_get_dimension_values(ds, dim_name)

                if dim_name in ds.variables:
                    dim_units[dim_name] = str(ds[dim_name].attrs.get("units", "")).strip()
                else:
                    dim_units[dim_name] = ""

            # ===
            # Detect grid-mapping / CRS candidates
            # ===
            grid_mapping_candidates = []
            known_grid_mapping_names = {
                "crs",
                "lambert_conformal",
                "lambert_conformal_conic",
                "spatial_ref",
                "projection",
            }

            for name, var in ds.variables.items():

                attrs = dict(var.attrs or {})

                if (
                    "grid_mapping_name" in attrs
                    or "crs_wkt" in attrs
                    or "spatial_ref" in attrs
                    or name.lower() in known_grid_mapping_names
                ):
                    grid_mapping_candidates.append(name)

            grid_mapping_candidates = sorted(set(grid_mapping_candidates))
            self._manual_structure_metadata = {
                "path": str(nc_path),
                "dimensions": dimensions,
                "variable_dims": variable_dims,
                "used_data_dims": used_data_dims,
                "dim_values": dim_values,
                "dim_units": dim_units,
                "grid_mapping_candidates": (grid_mapping_candidates),
            }

            # ===
            # Vertical dimension
            # ===
            vertical_options = {"— none —": None}

            for dim_name in dimensions:
                vertical_options[dim_name] = dim_name

            self.manual_vertical_dim.options = vertical_options

            auto_vertical = next(
                (dim for dim in MANUAL_LEVEL_DIM_CANDIDATES if (dim in dimensions and dim in used_data_dims)), None
            )

            self.manual_vertical_dim.value = auto_vertical

            # -----------------------------------------------------
            # Grid mapping
            # -----------------------------------------------------
            grid_mapping_options = {"— none —": None}

            for name in grid_mapping_candidates:
                grid_mapping_options[name] = name

            self.manual_grid_mapping_var.options = grid_mapping_options
            preferred_mapping = descriptor.get("grid_mapping_name") if descriptor else None

            if preferred_mapping in grid_mapping_candidates:
                self.manual_grid_mapping_var.value = preferred_mapping

            elif grid_mapping_candidates:
                self.manual_grid_mapping_var.value = grid_mapping_candidates[0]

            else:
                self.manual_grid_mapping_var.value = None

            # ===
            # Suggest spatial mode
            # ===
            lat_name = self.nc_lat_var.value
            lon_name = self.nc_lon_var.value
            x_name = self.env_x_select.value
            y_name = self.env_y_select.value
            has_xy = bool(x_name and y_name and x_name in ds.variables and y_name in ds.variables)
            has_latlon = bool(lat_name and lon_name and lat_name in ds.variables and lon_name in ds.variables)
            latlon_2d = has_latlon and ds[lat_name].ndim == 2 and ds[lon_name].ndim == 2

            if has_xy and grid_mapping_candidates:
                self.env_spatial_mode.value = "Projected rectilinear (x/y)"

            elif latlon_2d:
                self.env_spatial_mode.value = "Curvilinear geographic (2D lat/lon)"

            elif has_latlon:
                self.env_spatial_mode.value = "Regular geographic (lat/lon)"

            elif has_xy:
                self.env_spatial_mode.value = "Projected rectilinear (x/y)"

            # ===
            # Information
            # ===
            dims_text = ", ".join(f"{name}={size}" for name, size in dimensions.items())

            self.manual_structure_info.object = (
                f"**File:** `{Path(nc_path).name}`  \n"
                f"**Dimensions:** {dims_text or '-'}  \n"
                "**Status:** structure detected. "
                "Coordinate mapping remains editable."
            )

        finally:
            self._manual_ui_updating = False

        self._update_manual_vertical_level_options()
        self._rebuild_manual_extra_dim_widgets()

    def _update_manual_vertical_level_options(self):
        metadata = self._manual_structure_metadata or {}
        dim_name = self.manual_vertical_dim.value

        if not dim_name or dim_name not in metadata.get("dimensions", {}):
            self.manual_vertical_level.options = {"— none —": None}
            self.manual_vertical_level.value = None
            self.manual_vertical_level.disabled = True
            return

        values = metadata.get("dim_values", {}).get(dim_name)
        units = metadata.get("dim_units", {}).get(dim_name, "")

        if values is None:
            size = metadata["dimensions"][dim_name]

            self.manual_vertical_level.options = {f"Index {i}": i for i in range(size)}

        elif not values:
            self.manual_vertical_level.options = {"— no values —": None}

        else:
            options = {}

            for value in values:

                label = str(value)

                if units:
                    label = f"{label} {units}"

                options[label] = value

            self.manual_vertical_level.options = options

        option_values = list(self.manual_vertical_level.options.values())
        self.manual_vertical_level.value = option_values[0] if option_values else None
        self.manual_vertical_level.disabled = False

    def _manual_assigned_dimensions(self):
        metadata = self._manual_structure_metadata or {}
        dimensions = metadata.get("dimensions", {})
        variable_dims = metadata.get("variable_dims", {})
        assigned = set()
        role_variables = (
            self.nc_time_var.value,
            self.nc_lat_var.value,
            self.nc_lon_var.value,
            self.env_x_select.value,
            self.env_y_select.value,
        )

        for variable_name in role_variables:

            if not variable_name:
                continue

            if variable_name in dimensions:
                assigned.add(variable_name)

            assigned.update(variable_dims.get(variable_name, ()))

        vertical_dim = self.manual_vertical_dim.value

        if vertical_dim:
            assigned.add(vertical_dim)

        return assigned

    def _rebuild_manual_extra_dim_widgets(self):
        if self.env_dataset_profile.value != PROFILE_MANUAL:
            return

        metadata = self._manual_structure_metadata or {}

        if not metadata:
            return

        assigned = self._manual_assigned_dimensions()
        used_data_dims = set(metadata.get("used_data_dims", set()))
        extra_dims = sorted(
            dim for dim in used_data_dims if (dim not in assigned and dim.lower() not in MANUAL_HELPER_DIMS)
        )
        self.manual_extra_dim_widgets = {}
        objects = []

        if not extra_dims:
            objects.append(pn.pane.Markdown("*No additional dimensions detected.*"))

        for dim_name in extra_dims:
            values = metadata.get("dim_values", {}).get(dim_name)
            size = metadata.get("dimensions", {}).get(dim_name, 0)
            # ===
            # Very large dimensions: index selector
            # ===
            if values is None:

                widget = pn.widgets.IntInput(name=f"{dim_name} index", value=0, start=0, end=max(0, int(size) - 1))

            # ===
            # ERA5 expver: reserve future Auto mode
            # ===
            elif dim_name.lower() == "expver":

                options = {"Auto / combine valid values": MANUAL_EXPVER_AUTO}

                for value in values:
                    options[str(value)] = value

                widget = pn.widgets.Select(name=dim_name, options=options, value=MANUAL_EXPVER_AUTO)

            # ===
            # Generic additional dimension
            # number, member, realization, band, etc.
            # ===
            else:

                options = {str(value): value for value in values}

                if not options:
                    options = {"— no values —": None}

                first_value = next(iter(options.values()))
                widget = pn.widgets.Select(name=dim_name, options=options, value=first_value)

            self.manual_extra_dim_widgets[dim_name] = widget
            objects.append(widget)

        objects.append(
            pn.pane.Markdown(
                "*Additional-dimension selections are "
                "prepared for the future backend descriptor "
                "but are not applied yet.*"
            )
        )

        self._manual_extra_dims_panel.objects = objects

    def _on_manual_vertical_dim_changed(self, event):
        if self._manual_ui_updating:
            return

        if self.env_dataset_profile.value != PROFILE_MANUAL:
            return

        self._update_manual_vertical_level_options()
        self._rebuild_manual_extra_dim_widgets()

    def _on_manual_structure_role_changed(self, event):
        if self._manual_ui_updating:
            return

        if self.env_dataset_profile.value != PROFILE_MANUAL:
            return

        if not self._manual_structure_metadata:
            return

        self._rebuild_manual_extra_dim_widgets()

    def _apply_env_spatial_mode(self, event=None):
        """Enable coordinate selectors required by the selected grid structure."""

        mode = self.env_spatial_mode.value
        is_manual = self.env_dataset_profile.value == PROFILE_MANUAL
        is_projected = mode == "Projected rectilinear (x/y)"
        is_curvilinear = mode == "Curvilinear geographic (2D lat/lon)"

        # ===
        # Labels
        # ===
        if is_projected:
            self.nc_lat_var.name = "Latitude auxiliary coordinate (optional)"
            self.nc_lon_var.name = "Longitude auxiliary coordinate (optional)"

        elif is_curvilinear:
            self.nc_lat_var.name = "Latitude 2D coordinate"
            self.nc_lon_var.name = "Longitude 2D coordinate"

        else:
            self.nc_lat_var.name = "Latitude coordinate"
            self.nc_lon_var.name = "Longitude coordinate"

        # ===
        # Custom/manual:
        # keep optional auxiliary lat/lon visible even on
        # projected grids.
        # ===
        if is_manual:
            self.nc_lat_var.disabled = False
            self.nc_lon_var.disabled = False

        else:
            self.nc_lat_var.disabled = is_projected
            self.nc_lon_var.disabled = is_projected

        # X/Y are needed for projected or curvilinear structures.
        self.env_x_select.disabled = not (is_projected or is_curvilinear)
        self.env_y_select.disabled = not (is_projected or is_curvilinear)

    def _invalidate_loaded_environment(self, message="Environmental configuration changed. Reload the NetCDF file."):
        self.env_descriptor = None
        self.env_descriptors_by_file = {}
        self.env_variable_sources = {}
        self.env_loaded_paths = []
        self.env_continuous_selector.options = []
        self.env_continuous_selector.value = []
        self.env_categorical_selector.options = []
        self.env_categorical_selector.value = []
        for widget in (self.nc_time_var, self.nc_lat_var, self.nc_lon_var, self.env_x_select, self.env_y_select):
            widget.options = []
            widget.value = None
        self._reset_manual_structure_ui()
        self.make_annotation_button.disabled = True
        self.env_profile_info.object = (
            f"Profile: {self.env_dataset_profile.value} <br>"
            "Grid type: - <br>Coordinates: - <br>Supported interpolation: - <br>"
            f"Validation: {message}"
        )

    def _invalidate_loaded_tif_environment(self, message="TIF selection changed; press Load TIF environmental data."):
        self.tif_nc_path = None
        self.tif_env_var_map = {}
        self.tif_env_data_multiselect.options = []
        self.tif_env_data_multiselect.value = []
        self.tif_continuous_vars.options = []
        self.tif_continuous_vars.value = []
        self.tif_categorical_vars.options = []
        self.tif_categorical_vars.value = []
        self.tif_env_info.object = (
            "File: not selected <br>" "Environment parameters: - <br>" "Time range: - <br>" "Spatial range: - <br>"
        )

        self.status_text = message
        self.alert.object = self.status_text

    def _on_tif_env_file_changed(self, event):
        self._invalidate_loaded_tif_environment()

    def _on_env_profile_changed(self, event):
        self._invalidate_loaded_environment()
        self._apply_env_profile_ui()

    def _on_env_file_changed(self, event):
        """
        When the user selects any NetCDF file in the existing
        FileSelector, populate the multi-file list with all .nc
        files from the same folder.
        The selected file becomes the initial selection.
        """

        self._invalidate_loaded_environment(
            "File selection changed; select NetCDF files and " "press Load environmental data."
        )

        raw = self.env_data_selector.value

        if not raw:
            self.env_files_multiselect.options = {}
            self.env_files_multiselect.value = []
            return

        # Compatibility in case FileSelector ever returns
        # a one-element list/tuple/set.
        if isinstance(raw, (list, tuple, set)):

            values = list(raw)

            if not values:
                self.env_files_multiselect.options = {}
                self.env_files_multiselect.value = []
                return

            raw = values[0]

        selected_path = Path(str(raw)).expanduser()

        # ===
        # Determine current folder.
        # ===
        if selected_path.is_file():
            folder = selected_path.parent

        elif selected_path.is_dir():
            folder = selected_path

        else:
            # During navigation the FileSelector may temporarily
            # contain a path that is not a valid file.
            parent = selected_path.parent

            if parent.is_dir():
                folder = parent
            else:
                self.env_files_multiselect.options = {}
                self.env_files_multiselect.value = []
                return

        # ===
        # Find NetCDF files in this folder only.
        # ===
        nc_files = sorted(
            [path for path in folder.iterdir() if (path.is_file() and path.suffix.lower() == ".nc")],
            key=lambda path: path.name.lower(),
        )

        options = {path.name: str(path) for path in nc_files}

        self.env_files_multiselect.options = options

        # ===
        # Initially select only the file chosen in FileSelector.
        #
        # intentionally do NOT select all files automatically.
        # This prevents accidental loading of dozens of large NCs.
        # ===
        selected_value = []

        if selected_path.is_file() and selected_path.suffix.lower() == ".nc":

            selected_resolved = str(selected_path.resolve())

            for path in nc_files:

                if str(path.resolve()) == selected_resolved:
                    selected_value = [str(path)]
                    break

        self.env_files_multiselect.value = selected_value

        self.status_text = (
            f"Found {len(nc_files)} NetCDF file(s) in "
            f"{folder}. Select one or more files below, "
            "then press Load environmental data."
        )

        self.alert.object = self.status_text

    def _on_env_files_changed(self, event):
        """
        Invalidate an already loaded environmental configuration
        whenever the multi-file selection changes.

        The MultiSelect itself is intentionally preserved.
        """
        self._invalidate_loaded_environment("NetCDF file selection changed; " "press Load environmental data.")
        selected = list(self.env_files_multiselect.value or [])

        if selected:
            self.status_text = f"Selected {len(selected)} NetCDF file(s). " "Press Load environmental data."
        else:
            self.status_text = "No NetCDF files selected."

        self.alert.object = self.status_text

    def _apply_env_profile_ui(self):
        profile = self.env_dataset_profile.value
        manual = profile == PROFILE_MANUAL
        projected = profile == PROFILE_PROJECTED
        curvilinear = profile == PROFILE_CURVILINEAR

        if hasattr(self, "_manual_coordinate_panel"):
            self._manual_coordinate_panel.visible = manual

        # Automatic profiles show a detected, read-only spatial type.
        # Custom/manual lets the user choose it.
        self.env_spatial_mode.disabled = not manual

        if manual:
            self.env_spatial_mode.name = "Spatial coordinate mode"
        else:
            self.env_spatial_mode.name = "Detected spatial coordinate mode"

        if projected:
            self.env_spatial_mode.value = "Projected rectilinear (x/y)"
            self.interpolation_method.options = ["Bilinear (projected x/y, time-linear)"]
            self.interpolation_method.value = "Bilinear (projected x/y, time-linear)"
        elif curvilinear:
            self.env_spatial_mode.value = "Curvilinear geographic (2D lat/lon)"
            # Curvilinear geographic supports spherical nearest-neighbour and IDW sampling.
            self.interpolation_method.options = [
                "Nearest neighbor (time-linear)",
                "Inverse Distance Weighting (time-linear)",
            ]
            self.interpolation_method.value = "Nearest neighbor (time-linear)"
        elif profile == PROFILE_REGULAR:
            self.env_spatial_mode.value = "Regular geographic (lat/lon)"
            self.interpolation_method.options = [
                "Nearest neighbor (time-linear)",
                "Inverse Distance Weighting (time-linear)",
            ]
            if self.interpolation_method.value not in self.interpolation_method.options:
                self.interpolation_method.value = "Inverse Distance Weighting (time-linear)"
        else:
            self.interpolation_method.options = [
                "Nearest neighbor (time-linear)",
                "Inverse Distance Weighting (time-linear)",
                "Bilinear (projected x/y, time-linear)",
            ]

        self._apply_env_spatial_mode()
        self._update_smoothing_options(type("Event", (), {"new": self.interpolation_method.value})())

    def _validate_profile_structure(self, ds, profile):
        """Validate the selected standardized grid profile and return a descriptor."""
        all_vars = set(ds.variables)

        # safe_open_nc_with_time_decoding() normally standardizes
        # the internal coordinate to "time".
        time_name = "time" if "time" in all_vars else detect_time_name(ds)

        # Preserve the name used in the physical source NetCDF.
        source_time_name = ds.attrs.get("_ecodata_source_time_name") or time_name
        lat_name = next((c for c in ("lat", "latitude", "Latitude") if c in all_vars), None)
        lon_name = next((c for c in ("lon", "longitude", "Longitude", "long") if c in all_vars), None)

        if profile == PROFILE_REGULAR:
            if not all((time_name, lat_name, lon_name)):
                raise ValueError("Regular geographic requires time and 1D latitude/longitude coordinates.")
            if ds[lat_name].ndim != 1 or ds[lon_name].ndim != 1:
                raise ValueError("Regular geographic requires 1D latitude and longitude coordinates.")
            return {
                "profile": profile,
                "grid_type": "geographic_rectilinear",
                "time_name": time_name,
                "source_time_name": source_time_name,
                "lat_name": lat_name,
                "lon_name": lon_name,
                "x_name": None,
                "y_name": None,
                "supported_methods": ["nearest", "idw"],
            }

        if profile == PROFILE_PROJECTED:
            x_name = next(
                (c for c in ("x", "X", "projection_x_coordinate", "easting", "eastings") if c in all_vars), None
            )
            y_name = next(
                (c for c in ("y", "Y", "projection_y_coordinate", "northing", "northings") if c in all_vars), None
            )
            if not all((time_name, x_name, y_name, lat_name, lon_name)):
                raise ValueError("Projected rectilinear requires time, 1D x/y and auxiliary 2D lat/lon.")
            if ds[x_name].ndim != 1 or ds[y_name].ndim != 1:
                raise ValueError("Projected rectilinear requires 1D x and y coordinates.")
            if ds[lat_name].ndim != 2 or ds[lon_name].ndim != 2:
                raise ValueError("Projected rectilinear requires auxiliary 2D latitude and longitude.")

            grid_mapping_name = None
            for candidate in ("crs", "Lambert_Conformal", "lambert_conformal_conic", "spatial_ref"):
                if candidate in all_vars:
                    grid_mapping_name = candidate
                    break
            if grid_mapping_name is None:
                for da in ds.data_vars.values():
                    candidate = da.attrs.get("grid_mapping")
                    if candidate and candidate in all_vars:
                        grid_mapping_name = str(candidate)
                        break
            if grid_mapping_name is None:
                raise ValueError("Projected rectilinear requires a CF grid-mapping/CRS variable.")

            return {
                "profile": profile,
                "grid_type": "projected_rectilinear",
                "time_name": time_name,
                "source_time_name": source_time_name,
                "lat_name": lat_name,
                "lon_name": lon_name,
                "x_name": x_name,
                "y_name": y_name,
                "grid_mapping_name": grid_mapping_name,
                "x_units": str(ds[x_name].attrs.get("units", "")),
                "y_units": str(ds[y_name].attrs.get("units", "")),
                "supported_methods": ["bilinear"],
            }

        if profile == PROFILE_CURVILINEAR:
            if not all((time_name, lat_name, lon_name)):
                raise ValueError("Curvilinear geographic requires time and 2D latitude/longitude coordinates.")
            if ds[lat_name].ndim != 2 or ds[lon_name].ndim != 2:
                raise ValueError("Curvilinear geographic requires 2D latitude and longitude coordinates.")
            if ds[lat_name].dims != ds[lon_name].dims:
                raise ValueError("Curvilinear latitude and longitude must use the same y/x dimensions.")
            y_name, x_name = ds[lat_name].dims
            return {
                "profile": profile,
                "grid_type": "curvilinear_geographic",
                "time_name": time_name,
                "source_time_name": source_time_name,
                "lat_name": lat_name,
                "lon_name": lon_name,
                "x_name": x_name,
                "y_name": y_name,
                "supported_methods": ["nearest", "idw"],
            }

        # Custom/manual: only inspect; user chooses mapping.
        return {
            "profile": profile,
            "grid_type": "manual",
            "time_name": time_name,
            "source_time_name": source_time_name,
            "lat_name": lat_name,
            "lon_name": lon_name,
            "x_name": None,
            "y_name": None,
            "supported_methods": ["nearest", "idw", "bilinear"],
        }

    def _inspect_selected_env_profile(self, ds, nc_path, profile):
        """Inspect using existing adapters, with standardized UI-name aliases."""
        if profile == PROFILE_CURVILINEAR:
            return self._validate_profile_structure(ds, profile)

        adapter_profile = ADAPTER_PROFILE_ALIASES.get(profile, profile)
        try:
            descriptor = inspect_open_dataset_dict(ds, nc_path, adapter_profile)
        except Exception:
            # Generic profile validation is a safe fallback for standardized
            # NCBuilder files when the installed adapter is product-specific.
            descriptor = self._validate_profile_structure(ds, profile)

        descriptor = dict(descriptor or {})
        descriptor["profile"] = profile
        descriptor["source_time_name"] = (
            ds.attrs.get("_ecodata_source_time_name")
            or descriptor.get("source_time_name")
            or descriptor.get("time_name")
        )

        # After safe_open_nc_with_time_decoding(),
        # all ECODATA processing uses the standardized internal name "time".
        if "time" in ds.coords or "time" in ds.variables:
            descriptor["time_name"] = "time"

        return descriptor

    def _is_annotatable_env_variable(self, ds, da, descriptor):
        """
        Check whether a data variable has the dimensions required
        by the selected NetCDF grid profile.
        """
        dims = set(da.dims)
        grid_type = str(descriptor.get("grid_type") or "").strip().lower()
        time_name = descriptor.get("time_name") or "time"

        # All currently supported environmental variables
        # must have a time dimension.
        if time_name not in dims:
            return False

        # ===
        # Regular geographic:
        # variable(time, lat, lon)
        # variable(time, level, lat, lon)
        # ===
        if grid_type == "geographic_rectilinear":

            lat_name = descriptor.get("lat_name")
            lon_name = descriptor.get("lon_name")

            if not lat_name or not lon_name:
                return False

            if lat_name not in ds or lon_name not in ds:
                return False

            if ds[lat_name].ndim != 1 or ds[lon_name].ndim != 1:
                return False

            lat_dim = ds[lat_name].dims[0]
            lon_dim = ds[lon_name].dims[0]

            return lat_dim in dims and lon_dim in dims

        # ===
        # Projected rectilinear:
        # variable(time, y, x)
        # variable(time, level, y, x)
        # ===
        if grid_type == "projected_rectilinear":

            x_name = descriptor.get("x_name")
            y_name = descriptor.get("y_name")

            if not x_name or not y_name:
                return False

            if x_name not in ds or y_name not in ds:
                return False

            if ds[x_name].ndim != 1 or ds[y_name].ndim != 1:
                return False

            x_dim = ds[x_name].dims[0]
            y_dim = ds[y_name].dims[0]

            return x_dim in dims and y_dim in dims

        # ===
        # Curvilinear geographic:
        # variable(time, y, x)
        # variable(time, level, y, x)
        # lat(y, x), lon(y, x)
        # ===
        if grid_type == "curvilinear_geographic":

            x_dim = descriptor.get("x_name")
            y_dim = descriptor.get("y_name")

            if not x_dim or not y_dim:
                return False

            return x_dim in dims and y_dim in dims

        # ===
        # Custom/manual:
        # keep this intentionally flexible.
        # At minimum require time and >= 3 dimensions.
        # ===
        if grid_type == "manual":

            return time_name in dims and da.ndim >= 3

        return False

    @try_catch("Error loading environmental data")
    def load_env_data(self, *events):
        """
        Load one or more selected NetCDF files.

        Multi-file workflow:
        1. Get file paths from env_files_multiselect.
        2. Validate every file using the selected grid profile.
        3. Require compatible grid structure between files.
        4. Collect environmental variables from all files.
        5. Build:

                self.env_variable_sources = {
                    variable_label: physical_nc_path
                }

        6. Store one common structural descriptor for annotation.

        Important:
        This supports DIFFERENT variables stored in different files.

        Example:
            air  -> air.201401.nc
            uwnd -> uwnd.201401.nc
            vwnd -> vwnd.201401.nc

        It does NOT yet concatenate the same variable split across
        several time files.
        """

        self.status_text = "Loading environmental data..."
        self.alert.object = self.status_text

        # ====
        # 1. GET SELECTED NETCDF FILES
        # ====

        selected_paths = list(self.env_files_multiselect.value or [])

        # Backward-compatible fallback:
        # if nothing was selected in the new MultiSelect,
        # use the old FileSelector's current .nc file.
        if not selected_paths:

            raw = self.env_data_selector.value

            if raw:

                if isinstance(raw, (list, tuple, set)):
                    raw_values = list(raw)

                    if raw_values:
                        raw = raw_values[0]

                path = Path(str(raw)).expanduser()

                if path.is_file() and path.suffix.lower() == ".nc":
                    selected_paths = [str(path)]

        if not selected_paths:

            self.status_text = "No NetCDF files selected."
            self.alert.object = self.status_text
            return

        # ====
        # 2. NORMALIZE / VALIDATE PATHS
        # ====

        nc_paths = []
        seen_paths = set()

        for raw_path in selected_paths:

            path = Path(str(raw_path)).expanduser()

            if not path.is_file():

                self.status_text = f"NetCDF file not found: {path}"
                self.alert.object = self.status_text
                return

            if path.suffix.lower() != ".nc":

                self.status_text = f"Only .nc files are supported: " f"{path.name}"
                self.alert.object = self.status_text
                return

            resolved = str(path.resolve())

            if resolved in seen_paths:
                continue

            seen_paths.add(resolved)

            nc_paths.append(path)

        profile = self.env_dataset_profile.value

        # Custom/manual currently has one common set of manual
        # coordinate widgets, so multi-file manual mode would be
        # ambiguous.
        if profile == PROFILE_MANUAL and len(nc_paths) > 1:

            self.status_text = "Custom/manual currently supports only one " "NetCDF file per annotation run."
            self.alert.object = self.status_text
            return

        # ===
        # 3. ACCUMULATORS
        # ===

        var_file_map = {}

        descriptors_by_file = {}
        reference_descriptor = None
        reference_path = None
        # Union time range
        global_tmin = None
        global_tmax = None
        # Union spatial range
        global_lat_min = None
        global_lat_max = None
        global_lon_min = None
        global_lon_max = None
        time_candidates = ("time", "Time", "datetime", "date", "valid_time", "forecast_time", "verification_time")
        lat_candidates = ("lat", "latitude", "Latitude")
        lon_candidates = ("lon", "longitude", "Longitude", "long")
        x_candidates = ("x", "X", "projection_x_coordinate", "easting", "eastings")
        y_candidates = ("y", "Y", "projection_y_coordinate", "northing", "northings")
        level_dim_candidates = (
            "isobaricInhPa",
            "isobaric_in_hPa",
            "level",
            "lev",
            "plev",
            "pressure",
            "pressure_level",
        )

        # ====
        # LOCAL HELPERS
        # ====

        def _register_variable(label, nc_path):
            """
            Register one environmental variable/level.
            One UI variable may be backed by one or several NetCDF files.
            Examples
            --------
            air_850 -> "air.201403.nc"

            air_850 -> [
                "air.201403.nc",
                "air.201404.nc",
            ]
            """

            new_path = str(Path(nc_path))
            existing = var_file_map.get(label)

            # First occurrence: keep the old single-path representation.
            if existing is None:
                var_file_map[label] = new_path
                return

            # Convert existing source to a list only when necessary.
            if isinstance(existing, (list, tuple, set)):
                paths = list(existing)
            else:
                paths = [existing]

            existing_resolved = {str(Path(path).resolve()) for path in paths}
            new_resolved = str(Path(new_path).resolve())

            if new_resolved not in existing_resolved:
                paths.append(new_path)

            var_file_map[label] = paths

        def _check_descriptor_compatibility(ref, current, ref_path, current_path):
            """
            The current backend receives one common dataset descriptor.

            Therefore selected files must currently use compatible
            grid-coordinate naming.
            """

            ref_grid = str(ref.get("grid_type") or "").strip().lower()
            cur_grid = str(current.get("grid_type") or "").strip().lower()

            if ref_grid != cur_grid:

                raise ValueError(
                    "Selected NetCDF files use different grid types:\n"
                    f"{Path(ref_path).name}: {ref_grid}\n"
                    f"{Path(current_path).name}: {cur_grid}"
                )

            if ref_grid == "geographic_rectilinear":
                keys = ("lat_name", "lon_name")

            elif ref_grid == "projected_rectilinear":
                keys = ("x_name", "y_name", "lat_name", "lon_name", "grid_mapping_name")

            elif ref_grid == "curvilinear_geographic":
                keys = ("x_name", "y_name", "lat_name", "lon_name")

            else:
                keys = ()
            differences = []
            for key in keys:
                ref_value = ref.get(key)
                current_value = current.get(key)
                if ref_value != current_value:
                    differences.append(
                        f"{key}: "
                        f"{Path(ref_path).name}={ref_value!r}, "
                        f"{Path(current_path).name}={current_value!r}"
                    )

            if differences:
                raise ValueError(
                    "Selected NetCDF files have incompatible " "coordinate structures:\n" + "\n".join(differences)
                )

        # ====
        # 4. OPEN EVERY SELECTED NETCDF
        # ====

        for file_number, nc_path in enumerate(nc_paths, start=1):
            ds = None
            try:
                print(f"[INFO] Loading NetCDF " f"{file_number}/{len(nc_paths)}: " f"{nc_path}")
                ds = safe_open_nc_with_time_decoding(str(nc_path))
                all_vars = sorted(ds.variables.keys())
                descriptor = self._inspect_selected_env_profile(ds, str(nc_path), profile)
                descriptor = dict(descriptor or {})
                descriptor["path"] = str(nc_path)

                # ====
                # FIRST FILE = REFERENCE STRUCTURE
                # ====

                if reference_descriptor is None:
                    reference_descriptor = dict(descriptor)
                    reference_path = str(nc_path)
                    # 
                    # Populate coordinate widgets from reference
                    # file.
                    # 

                    self.nc_time_var.options = all_vars
                    self.nc_lat_var.options = all_vars
                    self.nc_lon_var.options = all_vars
                    self.env_x_select.options = all_vars
                    self.env_y_select.options = all_vars

                    def _pick(candidates):

                        return next((name for name in candidates if name in all_vars), None)

                    if profile == PROFILE_MANUAL:
                        self.nc_time_var.value = _pick(time_candidates)
                        self.nc_lat_var.value = _pick(lat_candidates)
                        self.nc_lon_var.value = _pick(lon_candidates)
                        self.env_x_select.value = _pick(x_candidates)
                        self.env_y_select.value = _pick(y_candidates)
                        self._populate_manual_structure_ui(ds, str(nc_path), descriptor)

                    else:
                        self.nc_time_var.value = descriptor.get("time_name")
                        self.nc_lat_var.value = descriptor.get("lat_name")
                        self.nc_lon_var.value = descriptor.get("lon_name")
                        self.env_x_select.value = descriptor.get("x_name")
                        self.env_y_select.value = descriptor.get("y_name")

                # ====
                # NEXT FILES = CHECK AGAINST REFERENCE
                # ====

                else:
                    _check_descriptor_compatibility(reference_descriptor, descriptor, reference_path, str(nc_path))

                descriptors_by_file[str(nc_path.resolve())] = descriptor

                # ====
                # 5. TIME RANGE
                # ====
                time_name = descriptor.get("time_name") or "time"
                if time_name in ds.coords or time_name in ds.variables:
                    try:
                        tvals = pd.to_datetime(ds[time_name].values)
                        if len(tvals) > 0:
                            file_tmin = pd.Timestamp(tvals.min())
                            file_tmax = pd.Timestamp(tvals.max())
                            if global_tmin is None or file_tmin < global_tmin:
                                global_tmin = file_tmin
                            if global_tmax is None or file_tmax > global_tmax:
                                global_tmax = file_tmax

                    except Exception as e:
                        print(f"[WARNING] Could not determine " f"time range for {nc_path.name}: {e}")

                # ====
                # 6. SPATIAL RANGE
                # ====
                lat_name = descriptor.get("lat_name")
                lon_name = descriptor.get("lon_name")
                if lat_name and lon_name and lat_name in ds and lon_name in ds:

                    try:
                        file_lat_min = float(ds[lat_name].min())
                        file_lat_max = float(ds[lat_name].max())
                        lon_values = normalize_longitude_values(ds[lon_name].values)
                        file_lon_min = float(np.nanmin(lon_values))
                        file_lon_max = float(np.nanmax(lon_values))

                        if global_lat_min is None or file_lat_min < global_lat_min:
                            global_lat_min = file_lat_min

                        if global_lat_max is None or file_lat_max > global_lat_max:
                            global_lat_max = file_lat_max

                        if global_lon_min is None or file_lon_min < global_lon_min:
                            global_lon_min = file_lon_min

                        if global_lon_max is None or file_lon_max > global_lon_max:
                            global_lon_max = file_lon_max

                    except Exception as e:

                        print(f"[WARNING] Could not determine " f"spatial range for {nc_path.name}: {e}")

                # ====
                # 7. FIND ENVIRONMENTAL VARIABLES
                # ====

                for var in ds.data_vars:
                    da = ds[var]
                    if not self._is_annotatable_env_variable(ds, da, descriptor):
                        continue

                    dims = list(da.dims)
                    level_dim = next((dim for dim in level_dim_candidates if dim in dims), None)

                    # 
                    # Variable without vertical levels
                    # 

                    if level_dim is None:
                        _register_variable(var, nc_path)
                        continue

                    # 
                    # Variable with vertical levels
                    # 

                    try:
                        level_vals = ds[level_dim].values
                        level_units = str(ds[level_dim].attrs.get("units", "")).strip().lower()

                    except Exception:
                        level_vals = []
                        level_units = ""

                    for lv in level_vals:
                        try:
                            level_value = float(lv)
                            # Internal UI pressure convention = hPa
                            if level_units in ("pa", "pascal", "pascals"):
                                level_value /= 100.0
                            lv_int = int(round(level_value))
                            label = f"{var}_{lv_int}"
                            _register_variable(label, nc_path)

                        except Exception:
                            continue

            except Exception as e:
                self.status_text = f"Failed to load {nc_path.name}: {e}"
                self.alert.object = self.status_text
                return

            finally:
                if ds is not None:
                    try:
                        ds.close()
                    except Exception:
                        pass

        # ====
        # 9. CHECK THAT VARIABLES WERE FOUND
        # ====

        if not var_file_map:

            self.env_continuous_selector.options = []
            self.env_categorical_selector.options = []
            self.env_continuous_selector.value = []
            self.env_categorical_selector.value = []
            self.status_text = (
                "No environmental variables compatible " "with the selected NetCDF grid profile " "were found."
            )

            self.alert.object = self.status_text
            return

        # ====
        # 10. MULTI-FILE CONFIGURATION
        # ====

        reference_descriptor = dict(reference_descriptor or {})
        reference_descriptor["path"] = str(nc_paths[0])
        reference_descriptor["paths"] = [str(path) for path in nc_paths]
        reference_descriptor["file_count"] = len(nc_paths)
        self.env_descriptor = reference_descriptor
        self.env_descriptors_by_file = descriptors_by_file
        self.env_variable_sources = var_file_map
        self.env_loaded_paths = [str(path) for path in nc_paths]

        # ====
        # 11. UPDATE INFO PANEL
        # ====

        file_names = [path.name for path in nc_paths]

        if len(file_names) <= 4:
            file_text = f"{len(file_names)} files: " + ", ".join(file_names)
        else:
            file_text = f"{len(file_names)} files: " + ", ".join(file_names[:4]) + f", ... (+{len(file_names) - 4})"

        if global_tmin is not None and global_tmax is not None:
            time_text = f"{global_tmin.date()} — " f"{global_tmax.date()}"
        else:
            time_text = "-"

        if all(value is not None for value in (global_lat_min, global_lat_max, global_lon_min, global_lon_max)):
            spatial_text = (
                f"lat[{global_lat_min:.3f}.."
                f"{global_lat_max:.3f}], "
                f"lon[{global_lon_min:.3f}.."
                f"{global_lon_max:.3f}]"
            )
        else:
            spatial_text = "-"

        self._update_info_lines(
            self.env_info, {"File:": file_text, "Time range:": time_text, "Spatial range:": spatial_text}
        )
        self._auto_height(self.env_info)

        # =====
        # 12. PROFILE INFORMATION
        # =====

        descriptor = self.env_descriptor
        source_time = descriptor.get("source_time_name") or descriptor.get("time_name")
        if source_time and source_time != "time":
            profile_time_text = f"{source_time} → time"
        else:
            profile_time_text = "time"
        grid_type = descriptor.get("grid_type")

        if grid_type == "geographic_rectilinear":
            coords_text = (
                f"time={profile_time_text}, " f"lat={descriptor.get('lat_name')}, " f"lon={descriptor.get('lon_name')}"
            )
        elif grid_type == "projected_rectilinear":
            coords_text = (
                f"time={profile_time_text}, "
                f"x={descriptor.get('x_name')}, "
                f"y={descriptor.get('y_name')}, "
                f"aux lat/lon="
                f"{descriptor.get('lat_name')}/"
                f"{descriptor.get('lon_name')}"
            )
        elif grid_type == "curvilinear_geographic":
            coords_text = (
                f"time={profile_time_text}, "
                f"logical y/x="
                f"{descriptor.get('y_name')}/"
                f"{descriptor.get('x_name')}, "
                f"2D lat/lon="
                f"{descriptor.get('lat_name')}/"
                f"{descriptor.get('lon_name')}"
            )
        else:
            coords_text = "manual mapping"

        methods_text = ", ".join(descriptor.get("supported_methods", []))
        self.env_profile_info.object = (
            f"Profile: {descriptor.get('profile')} <br>"
            f"Grid type: {grid_type} <br>"
            f"Coordinates: {coords_text} <br>"
            f"Supported interpolation: {methods_text} <br>"
            f"Validation: passed for "
            f"{len(nc_paths)} file(s)"
        )

        # =====
        # 13. ENVIRONMENTAL VARIABLE SELECTORS
        # =====

        all_labels = list(var_file_map.keys())
        self.env_continuous_selector.options = all_labels
        self.env_categorical_selector.options = all_labels
        self.env_continuous_selector.value = []
        self.env_categorical_selector.value = []
        self.make_annotation_button.disabled = False
        self.status_text = (
            f"Loaded {len(nc_paths)} NetCDF file(s) "
            f"with {len(all_labels)} environmental "
            "variable/level option(s). "
            "Now split variables into Continuous "
            "vs Categorical/QC."
        )
        self.alert.object = self.status_text
        self._sync_nc_column_heights()

    @try_catch("Error loading boundary data")
    def load_boundary_data(self, *events):
        self.status_text = "Loading boundary data..."
        self.alert.object = self.status_text
        file_input = self.bound_data_selector.value
        if not file_input:
            self.status_text = "Please select one vector file (.shp or .geojson)."
            self.alert.object = self.status_text
            return

        # If multiple files are selected
        if isinstance(file_input, list):
            if len(file_input) != 1:
                self.status_text = "Please select exactly one vector file (.shp or .geojson)."
                self.alert.object = self.status_text
                return
            file_path = file_input[0]
        else:
            file_path = file_input

        try:
            path, S, N, W, E = load_vector_extent_info(file_path)
            self.nc_boundary_path = path
            self.nc_boundary_extent = {"S": S, "N": N, "W": W, "E": E}
            self.boundary_info_str.object = (
                f"Boundary file: {Path(path).name} <br>" f"Spatial range: lat[{S:.3f}..{N:.3f}], lon[{W:.3f}..{E:.3f}]"
            )
            self.status_text = (
                f"Boundary loaded from {Path(path).name}: " f"lat[{S:.3f}..{N:.3f}], lon[{W:.3f}..{E:.3f}]"
            )
        except Exception as e:
            self.status_text = f"Failed to read vector file: {e}"
        self.alert.object = self.status_text
        self._sync_nc_column_heights()

    @try_catch("Error loading movement data")
    def load_movement_data(self, *events):
        self.status_text = "Loading movement data..."
        self.alert.object = self.status_text
        file_path = self.movement_data_selector.value
        if not file_path:
            self.status_text = "No movement file selected."
            self.alert.object = self.status_text
            return

        custom_format = self.movement_csv_type.value == CSV_FORMAT_CUSTOM

        if custom_format:
            id_column = self.movement_id_column.value
            taxon_column = self.movement_taxon_column.value
            time_column = self.movement_time_column.value
            lat_column = self.movement_lat_column.value
            lon_column = self.movement_lon_column.value
            required = {
                "Animal ID column": id_column,
                "Time column": time_column,
                "Latitude column": lat_column,
                "Longitude column": lon_column,
            }
            missing = [name for name, value in required.items() if not value]

            if missing:
                self.status_text = "Please select: " + ", ".join(missing)
                self.alert.object = self.status_text
                return

        else:
            id_column = None
            taxon_column = None
            time_column = None
            lat_column = None
            lon_column = None

        df, taxa, ids, err = load_taxa_and_ids_from_csv(
            file_path,
            id_column=id_column,
            taxon_column=taxon_column,
            time_column=time_column,
            lat_column=lat_column,
            lon_column=lon_column,
        )

        if err:
            self.status_text = f"Error: {err}"
            self.alert.object = self.status_text
            return

        self.nc_movement_df = df
        self.id_multiselect.options = ids
        self.id_multiselect.disabled = False
        self.taxon_multiselect.options = taxa
        self.taxon_multiselect.disabled = False
        self.status_text = f"Loaded {len(ids)} IDs and {len(taxa)} taxon names."
        cols = set(df.columns)
        # TIME
        time_col = next((c for c in ("timestamp", "time", "datetime", "date") if c in cols), None)
        ts = pd.to_datetime(df[time_col], errors="coerce") if time_col else None
        time_text = "-"
        if ts is not None and ts.notna().any():
            tmin, tmax = ts.min(), ts.max()
            time_text = f"Time range: {tmin:%Y-%m-%d %H:%M:%S} — {tmax:%Y-%m-%d %H:%M:%S}"

        # SPATIAL
        lat_col = next((c for c in ("location_lat", "latitude", "lat", "y") if c in cols), None)
        lon_col = next((c for c in ("location_lon", "longitude", "lon", "x") if c in cols), None)
        spatial_text = "-"
        if lat_col and lon_col:
            lat = pd.to_numeric(df[lat_col], errors="coerce")
            lon = pd.to_numeric(df[lon_col], errors="coerce")
            if lat.notna().any() and lon.notna().any():
                spatial_text = (
                    f"Spatial range: "
                    f"lat[{float(lat.min()):.3f}..{float(lat.max()):.3f}], "
                    f"lon[{float(lon.min()):.3f}..{float(lon.max()):.3f}]"
                )

        lines = (
            self.movement_info.object
            or "File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: - <br>"
        ).split("<br>")
        for i, line in enumerate(lines):
            if line.startswith("Time range:"):
                lines[i] = time_text
            if line.startswith("Spatial range:"):
                lines[i] = spatial_text
        self.movement_info.object = "<br>".join(lines)
        self.alert.object = self.status_text
        self._sync_nc_column_heights()

    def _get_selected_env_vars(self):
        cont = list(getattr(self.env_continuous_selector, "value", []) or [])
        cat = list(getattr(self.env_categorical_selector, "value", []) or [])
        seen = set()
        out = []
        for v in cont + cat:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    @try_catch("Error during annotation")
    def run_annotation(self, *events):
        self.status_text = "Running annotation..."
        self.alert.object = self.status_text
        try:
            continuous_vars = list(getattr(self.env_continuous_selector, "value", []) or [])
            categorical_vars = list(getattr(self.env_categorical_selector, "value", []) or [])
            # Preserve variable order without duplicates
            seen = set()
            selected_vars = []
            for v in continuous_vars + categorical_vars:
                if v not in seen:
                    seen.add(v)
                    selected_vars.append(v)

            selected_ids = self.id_multiselect.value
            env_var_map = getattr(self, "env_variable_sources", {})
            movebank_path = self.movement_data_selector.value
            movement_column_map = None
            if self.movement_csv_type.value == CSV_FORMAT_CUSTOM:
                movement_column_map = {
                    "taxon": self.movement_taxon_column.value,
                    "id": self.movement_id_column.value,
                    "time": self.movement_time_column.value,
                    "lat": self.movement_lat_column.value,
                    "lon": self.movement_lon_column.value,
                }

                required = {
                    "Animal ID column": movement_column_map["id"],
                    "Time column": movement_column_map["time"],
                    "Latitude column": movement_column_map["lat"],
                    "Longitude column": movement_column_map["lon"],
                }

                missing = [name for name, value in required.items() if not value]

                if missing:
                    self.status_text = "Please select: " + ", ".join(missing)
                    self.alert.object = self.status_text
                    return
            boundary_path = None
            bbox = None

            if self.boundary_mode.value == BOUNDARY_MODE_BBOX:
                try:
                    bbox = self._get_nc_manual_bbox()
                except Exception as e:
                    self.status_text = f"Invalid bbox: {e}"
                    self.alert.object = self.status_text
                    return

            else:
                boundary_path = self.nc_boundary_path
                if boundary_path and not Path(boundary_path).is_file():
                    self.status_text = f"Boundary file not found: {boundary_path}"
                    self.alert.object = self.status_text
                    return

            interpolation_method = self._normalize_interp_key(self.interpolation_method.value)
            descriptor = getattr(self, "env_descriptor", None)
            if not descriptor:
                self.status_text = "Environmental file is not validated. Press Load environmental data."
                self.alert.object = self.status_text
                return

            spatial_mode = self.env_spatial_mode.value

            if spatial_mode == "Projected rectilinear (x/y)" and interpolation_method != "bilinear":
                self.status_text = (
                    "Projected rectilinear mode currently supports only "
                    "Bilinear (projected x/y, time-linear) interpolation."
                )
                self.alert.object = self.status_text
                return

            if spatial_mode == "Regular geographic (lat/lon)" and interpolation_method == "bilinear":
                self.status_text = "Bilinear projected interpolation requires Projected rectilinear mode."
                self.alert.object = self.status_text
                return
            smoothing_points = int(self.control_smoothing.value)

            if not selected_vars:
                self.status_text = "No environmental variables selected."
            elif not selected_ids:
                self.status_text = "No individual IDs selected."
            elif not movebank_path:
                self.status_text = "No movement  data file selected."
            else:
                if boundary_path is None and bbox is None:
                    first_var = selected_vars[0]
                    nc_path = env_var_map.get(first_var)
                    if isinstance(nc_path, (list, tuple, set)):
                        nc_paths = list(nc_path)
                        nc_path = nc_paths[0] if nc_paths else None
                    if not nc_path:
                        self.status_text = "Cannot derive boundary: missing .nc path for selected variable."
                        self.alert.object = self.status_text
                        return

                    if self.env_spatial_mode.value in (
                        "Regular geographic (lat/lon)",
                        "Curvilinear geographic (2D lat/lon)",
                    ):
                        try:
                            bounds = get_nc_bounds(
                                nc_path,
                                env_coord_names={
                                    "env_time": self.nc_time_var.value,
                                    "env_lat": self.nc_lat_var.value,
                                    "env_lon": self.nc_lon_var.value,
                                    "env_x": None,
                                    "env_y": None,
                                },
                            )
                            bbox = bounds
                            self.boundary_info_str.object = (
                                "Boundary file: not selected (auto from .nc) <br>"
                                f"Spatial range: lat[{bounds['S']:.3f}..{bounds['N']:.3f}], "
                                f"lon[{bounds['W']:.3f}..{bounds['E']:.3f}]"
                            )
                        except Exception as e:
                            self.status_text = f"Failed to derive boundary from .nc: {e}"
                            self.alert.object = self.status_text
                            return
                    else:
                        bbox = None
                        self.boundary_info_str.object = (
                            "Boundary file: not selected <br>"
                            "Spatial range: using projected grid extent (x/y); bbox cropping disabled."
                        )

                self.status_text = "Annotation started."

                if self.env_spatial_mode.value == "Projected rectilinear (x/y)":
                    coord_spec = None
                    env_coord_names = {
                        "env_time": (
                            descriptor.get("time_name")
                            if descriptor.get("profile") != PROFILE_MANUAL
                            else self.nc_time_var.value
                        ),
                        "env_lat": None,
                        "env_lon": None,
                        "env_x": (
                            descriptor.get("x_name")
                            if descriptor.get("profile") != PROFILE_MANUAL
                            else self.env_x_select.value
                        ),
                        "env_y": (
                            descriptor.get("y_name")
                            if descriptor.get("profile") != PROFILE_MANUAL
                            else self.env_y_select.value
                        ),
                    }

                    if not (self.nc_time_var.value and self.env_x_select.value and self.env_y_select.value):
                        self.status_text = "Please select Time, X and Y variables from the NetCDF file."
                        self.alert.object = self.status_text
                        return

                    if interpolation_method == "bilinear" and categorical_vars:
                        self.status_text = (
                            "Bilinear projected interpolation is only valid for continuous variables. "
                            "Please remove categorical/QC variables or use Nearest/IDW mode."
                        )
                        self.alert.object = self.status_text
                        return

                else:
                    time_value = (
                        descriptor.get("time_name")
                        if descriptor.get("profile") != PROFILE_MANUAL
                        else self.nc_time_var.value
                    )
                    lat_value = (
                        descriptor.get("lat_name")
                        if descriptor.get("profile") != PROFILE_MANUAL
                        else self.nc_lat_var.value
                    )
                    lon_value = (
                        descriptor.get("lon_name")
                        if descriptor.get("profile") != PROFILE_MANUAL
                        else self.nc_lon_var.value
                    )
                    coord_spec = {"time": time_value, "lat": lat_value, "lon": lon_value}
                    env_coord_names = {
                        "env_time": time_value,
                        "env_lat": lat_value,
                        "env_lon": lon_value,
                        "env_x": None,
                        "env_y": None,
                    }

                    if not (self.nc_time_var.value and self.nc_lat_var.value and self.nc_lon_var.value):
                        self.status_text = "Please select Time, Latitude and Longitude variables from the NetCDF file."
                        self.alert.object = self.status_text
                        return

                saved_path = start_annotation_process(
                    env_var_map,
                    selected_vars,
                    movebank_path,
                    selected_ids,
                    boundary_path,
                    interpolation_method,
                    bbox=bbox,
                    smoothing_k=smoothing_points,
                    out_csv_path=self.output_path.value,
                    coord_spec=coord_spec,
                    env_coord_names=env_coord_names,
                    continuous_vars=continuous_vars,
                    categorical_vars=categorical_vars,
                    dataset_descriptor=descriptor,
                    movement_column_map=movement_column_map,
                    time_range_mode=self.time_range_mode.value,
                )
                if saved_path and Path(saved_path).is_file():
                    self.status_text = f"Annotation finished. File saved to: {saved_path}"
                else:
                    self.status_text = (
                        "Annotation stopped before saving. Check selected IDs, time range, "
                        "spatial coverage, and the server console."
                    )

        except Exception as e:
            self.status_text = f"Annotation failed: {e}"

        self.alert.object = self.status_text

    ####TIF
    @try_catch("Error loading TIF environmental data")
    def load_env_data_tif(self, *events):
        """
        Load environmental data from an AppEEARS GeoTIFF folder, convert it to a
        single multi-variable NetCDF, and populate the TIF tab UI.

        Workflow:
        1) Validate that the user selected any *.tif in the target folder.
        2) Use the TIF folder as the output directory for the generated temporary NetCDF.
        3) Convert the set of TIFs in that folder → one NetCDF via
        `convert_tif_to_nc_before_annotation` (each parsed variable = separate DataArray).
        4) Open the produced NetCDF with `safe_open_nc_with_time_decoding` and:
        - extract Time range and Spatial extent,
        - build `tif_env_var_map` ONLY for variables that are 3D and have a time dimension.
        5) Update the UI:
        - Info panel (File/Time/Spatial),
        - Multiselect options/values,
        - Status text.

        Notes:
        - The resulting `self.tif_env_var_map` is later used by `run_annotation_tif()` directly,
        so we avoid re-reading all `data_vars` again.
        - `self.tif_nc_path` is stored for fallbacks (e.g., bbox from nc if no boundary).
        """
        #  0) Initial UI/status
        self.status_text = "Loading TIF environmental data..."
        self.alert.object = self.status_text

        #  1) Validate a sample TIF and collect folder
        tif_sample_path = Path(getattr(self.tif_env_data_selector, "value", "") or "")
        if (not tif_sample_path.is_file()) or (tif_sample_path.suffix.lower() != ".tif"):
            self.status_text = f"Selected path is not a .tif file: {tif_sample_path}"
            self.alert.object = self.status_text
            return

        folder_path = tif_sample_path.parent
        tif_files = sorted([str(p) for p in folder_path.glob("*.tif") if p.is_file()])
        if not tif_files:
            self.status_text = f"No .tif files found in: {folder_path}"
            self.alert.object = self.status_text
            return

        # 2) Write the temporary NetCDF next to the source TIF files.
        # Movebank data is not required at this stage.
        # The temporary NetCDF is always saved next to the input TIF files.
        output_dir = str(folder_path)

        #  3) Convert TIF to NetCDF
        try:
            nc_path = convert_tif_to_nc_before_annotation(tif_files, output_dir)
        except Exception as e:
            self.status_text = f"Failed to convert TIF to NetCDF: {e}"
            self.alert.object = self.status_text
            return

        # Cache for later (bbox fallback, re-open, etc.)
        self.tif_nc_path = nc_path

        #  4) Inspect NetCDF and keep ONLY 3D variables with a time dimension
        var_file_map: dict[str, str] = {}
        time_text = "Time range: -"
        spatial_text = "Spatial range: -"

        try:
            ds = safe_open_nc_with_time_decoding(nc_path)

            # Time range (if present)
            if ("time" in ds.coords) or ("time" in ds.variables):
                try:
                    tmin = pd.to_datetime(ds["time"].values.min())
                    tmax = pd.to_datetime(ds["time"].values.max())
                    time_text = f"Time range: {tmin.strftime('%Y-%m-%d')} — {tmax.strftime('%Y-%m-%d')}"
                except Exception:
                    # Keep default if something goes wrong
                    pass

            # Spatial extent (lat/lon candidates can vary)
            lat_name = next((c for c in ("lat", "latitude", "y") if c in ds.coords or c in ds.variables), None)
            lon_name = next((c for c in ("lon", "longitude", "x", "long") if c in ds.coords or c in ds.variables), None)
            if lat_name and lon_name:
                try:
                    lat_min = float(ds[lat_name].min())
                    lat_max = float(ds[lat_name].max())
                    lon_values = normalize_longitude_values(ds[lon_name].values)
                    lon_min = float(np.nanmin(lon_values))
                    lon_max = float(np.nanmax(lon_values))
                    spatial_text = (
                        f"Spatial range: lat[{lat_min:.3f}..{lat_max:.3f}], " f"lon[{lon_min:.3f}..{lon_max:.3f}]"
                    )
                except Exception:
                    pass

            # Build map: ONLY variables that (a) have a 'time' dim and (b) are 3D or higher
            var_names: list[str] = []
            for v in ds.data_vars:
                da = ds[v]
                if ("time" in da.dims) and (da.ndim >= 3):
                    var_file_map[v] = nc_path
                    var_names.append(v)

        except Exception as e:
            self.status_text = f"Failed to open/inspect NetCDF: {e}"
            self.alert.object = self.status_text
            return
        finally:
            try:
                ds.close()
            except Exception:
                pass

        #  5) Update UI: info panel, multiselect, status
        # Info panel (use common helper to insert/replace rows)
        self._update_info_lines(
            self.tif_env_info,
            {
                "File:": Path(nc_path).name,
                "Time range:": time_text.replace("Time range: ", ""),
                "Spatial range:": spatial_text.replace("Spatial range: ", ""),
            },
        )

        if not var_file_map:
            # No valid 3D variables (time/lat/lon) found
            self.tif_env_var_map = {}
            self.tif_env_data_multiselect.options = []
            self.tif_env_data_multiselect.value = []
            self.tif_continuous_vars.options = []
            self.tif_continuous_vars.value = []
            self.tif_categorical_vars.options = []
            self.tif_categorical_vars.value = []
            self.status_text = "No 3D (time/lat/lon) variables found in the generated NetCDF."
            self.alert.object = self.status_text
            return

        # Save valid TIF variables for annotation.
        self.tif_env_var_map = var_file_map
        self.tif_env_data_multiselect.options = var_names
        self.tif_env_data_multiselect.value = []

        # Populate TIF variable type selectors.
        # This is an initial guess only; the user can manually change it.
        continuous_guess, categorical_guess = self._guess_tif_variable_types(var_names)
        self.tif_continuous_vars.options = var_names
        self.tif_categorical_vars.options = var_names
        self.tif_continuous_vars.value = continuous_guess
        self.tif_categorical_vars.value = categorical_guess

        # Update info panel using the actual selected split
        selected_for_info = continuous_guess + [v for v in categorical_guess if v not in continuous_guess]
        self.update_env_info_text_tif(selected_for_info)

        # Final status
        self.status_text = (
            f"Converted {len(tif_files)} TIF files to NetCDF. "
            f"Variables (3D/time): {', '.join(var_names)}. "
            "Please check Continuous vs Categorical/QC selection."
        )
        self.alert.object = self.status_text

    @try_catch("Error running TIF annotation")
    def run_annotation_tif(self, *events):
        """
        Run annotation workflow for environmental data sourced from AppEEARS GeoTIFFs.

        TIF workflow:
        1) Validate user selections:
        - Movement CSV is required.
        - A sample .tif file is required to identify the target TIF folder.
        - Boundary file is optional; if it is not provided, the NetCDF extent is used.

        2) Gather all *.tif files from the selected TIF folder.

        3) Convert the TIF stack to a temporary NetCDF via
        `convert_tif_to_nc_before_annotation(...)`.

        - The temporary NetCDF is written to the same folder as the input TIF files.
        - The conversion keeps raw raster values.
        - No scale factor, add_offset, or automatic 0.0001 heuristic is applied during
            TIF -> NetCDF conversion.

        4) Build `env_var_map` for variables that are valid for annotation:
        - variables must have a time dimension;
        - variables must be at least 3D, typically variable(time, lat, lon).

        5) Determine variables to annotate from the explicit type selectors:
        - `self.tif_continuous_vars`
        - `self.tif_categorical_vars`

        The same variable must not be selected in both lists.

        6) Run annotation through `start_annotation_process(...)`.

        Continuous variables:
        - use the selected spatial interpolation method;
        - use linear temporal interpolation;
        - may optionally receive post-sampling value correction:
            corrected_value = sampled_value * scale_factor + add_offset.

        Categorical/QC variables:
        - are sampled using nearest spatial grid cell and nearest available timestep;
        - are not IDW-averaged;
        - are not linearly interpolated in time;
        - are not scaled or offset;
        - remain raw category/flag/QC codes.

        7) Save the annotated output CSV and per-individual CSV files through the backend.

        Required UI widgets:
        - `self.tif_movement_data_selector`:
           Movement CSV path.
        - `self.tif_env_data_selector`:
            one sample .tif file inside the target TIF folder.
        - `self.tif_continuous_vars`:
            continuous environmental variables selected for annotation.
        - `self.tif_categorical_vars`:
            categorical/QC variables selected for annotation.
        - `self.tif_id_multiselect`:
            selected individual IDs.
        - `self.tif_bound_data_selector`:
            optional boundary file.
        - `self.tif_interpolation_method`:
            spatial interpolation method for continuous variables.
        - `self.tif_control_smoothing`:
            number of nearest grid points for IDW.
        - `self.tif_apply_scale`, `self.tif_scale_factor`, `self.tif_add_offset`:
            optional post-sampling correction for continuous variables only.
        - `self.tif_output_path`:
            output CSV path.

        Status messages are written to `self.status_text` and mirrored in `self.alert.object`.
        """
        self.status_text = "Starting annotation (TIF)…"
        self.alert.object = self.status_text

        # 0) Validate inputs
        # Movement CSV (required)
        movebank_path = getattr(self.tif_movement_data_selector, "value", None)
        if not movebank_path or not Path(str(movebank_path)).is_file():
            self.status_text = "Please load movement data before running TIF annotation."
            self.alert.object = self.status_text
            return
        tif_movement_column_map = None

        if self.tif_movement_csv_type.value == CSV_FORMAT_CUSTOM:
            tif_movement_column_map = {
                "taxon": self.tif_movement_taxon_column.value,
                "id": self.tif_movement_id_column.value,
                "time": self.tif_movement_time_column.value,
                "lat": self.tif_movement_lat_column.value,
                "lon": self.tif_movement_lon_column.value,
            }

            required = {
                "Animal ID column": tif_movement_column_map["id"],
                "Time column": tif_movement_column_map["time"],
                "Latitude column": tif_movement_column_map["lat"],
                "Longitude column": tif_movement_column_map["lon"],
            }

            missing = [name for name, value in required.items() if not value]

            if missing:
                self.status_text = "Please select: " + ", ".join(missing)
                self.alert.object = self.status_text
                return
        output_dir = str(Path(str(movebank_path)).parent)

        # Sample TIF file (to infer the target folder)
        tif_sample = getattr(self, "tif_env_data_selector", None)
        tif_sample = getattr(tif_sample, "value", None)
        if not tif_sample or Path(tif_sample).suffix.lower() != ".tif":
            self.status_text = "Please select a sample .tif file in the folder you want to annotate."
            self.alert.object = self.status_text
            return

        # Selected animal IDs (optional)
        id_widget = getattr(self, "tif_id_multiselect", None)  # or getattr(self, "id_multiselect", None)
        selected_ids = list(getattr(id_widget, "value", [])) if id_widget else []
        if not selected_ids:
            self.status_text = "Please select at least one individual ID before running TIF annotation."
            self.alert.object = self.status_text
            return

        # Optional boundary
        boundary_path = None
        bbox = None

        if self.tif_boundary_mode.value == BOUNDARY_MODE_BBOX:
            try:
                bbox = self._get_tif_manual_bbox()
            except Exception as e:
                self.status_text = f"Invalid bbox: {e}"
                self.alert.object = self.status_text
                return

        else:
            boundary_path = self.tif_boundary_path

            if boundary_path and not Path(boundary_path).is_file():
                self.status_text = f"Boundary file not found: {boundary_path}"
                self.alert.object = self.status_text
                return

        # Interpolation and time-fit options (prefer TIF-tab widgets; fallback to NC-tab)
        interp_widget = getattr(self, "tif_interpolation_method", None)
        # interp_method = getattr(interp_widget, "value", "Nearest neighbor (time-linear)")
        ui_method = getattr(interp_widget, "value", "Nearest neighbor (time-linear)")
        interp_method = self._normalize_interp_key(ui_method)
        # Output CSV path (optional)
        out_widget = getattr(self, "tif_output_path", None)
        output_csv_path = getattr(out_widget, "value", None)

        #  1) Collect TIFs from the selected folder
        folder_path = Path(tif_sample).parent
        tif_paths = sorted(p for p in folder_path.glob("*.tif") if p.is_file())
        if not tif_paths:
            self.status_text = f"No .tif files found in: {folder_path}"
            self.alert.object = self.status_text
            return

        # 2) Convert TIF to NetCDF (multi-variable, raw values only)
        #  Scale/offset is not applied here; optional correction is applied after sampling.
        output_dir = str(folder_path)
        nc_path = convert_tif_to_nc_before_annotation([str(p) for p in tif_paths], output_dir)
        self.tif_nc_path = nc_path

        # 3) Read valid variables from NetCDF and build env_var_map
        if getattr(self, "tif_env_var_map", None):
            env_var_map = dict(self.tif_env_var_map)
            var_names = list(env_var_map.keys())
        else:
            # Fallback: inspect the .nc and keep only 3D with a time dim
            env_var_map, var_names = {}, []
            try:
                ds = safe_open_nc_with_time_decoding(nc_path)
                try:
                    for v in ds.data_vars:
                        da = ds[v]
                        if ("time" in da.dims) and (da.ndim >= 3):
                            env_var_map[v] = nc_path
                            var_names.append(v)
                finally:
                    ds.close()
            except Exception as e:
                self.status_text = f"Failed to read variables from NetCDF: {e}"
                self.alert.object = self.status_text
                return

        if not var_names:
            self.status_text = "No 3D (time/lat/lon) variables found in the generated NetCDF."
            self.alert.object = self.status_text
            return

        #  4) Which variables to annotate?
        continuous_vars = list(getattr(self.tif_continuous_vars, "value", []) or [])
        categorical_vars = list(getattr(self.tif_categorical_vars, "value", []) or [])

        overlap = set(continuous_vars) & set(categorical_vars)
        if overlap:
            self.status_text = (
                "The same variable cannot be selected as both Continuous and Categorical/QC: "
                + ", ".join(sorted(overlap))
            )
            self.alert.object = self.status_text
            return

        selected_vars = continuous_vars + [v for v in categorical_vars if v not in continuous_vars]

        if not selected_vars:
            self.status_text = "Please select at least one Continuous or Categorical/QC variable."
            self.alert.object = self.status_text
            return

        # 5) Kick off annotation
        scale_msg = (
            f"scale={self.tif_scale_factor.value}, offset={self.tif_add_offset.value}"
            if self.tif_apply_scale.value
            else "off"
        )

        self.status_text = (
            f"Annotating variables: {', '.join(selected_vars)} | "
            f"Continuous: {', '.join(continuous_vars) if continuous_vars else '-'} | "
            f"Categorical/QC: {', '.join(categorical_vars) if categorical_vars else '-'} | "
            f"Scale/offset: {scale_msg} | "
            f"IDs: {len(selected_ids) if selected_ids else 'all/unspecified'} | "
            f"Interpolation: {interp_method}"
        )
        self.alert.object = self.status_text

        try:
            start_loading_spinner()
        except Exception:
            pass

        try:
            # Auto-bbox from .nc only when neither vector nor manual bbox is provided
            if boundary_path is None and bbox is None:
                try:
                    bounds = get_nc_bounds(self.tif_nc_path)
                    bbox = bounds

                    self.tif_boundary_info_str.object = (
                        "Boundary file: not selected (auto from .nc) <br>"
                        f"Spatial range: "
                        f"lat[{bounds['S']:.3f}..{bounds['N']:.3f}], "
                        f"lon[{bounds['W']:.3f}..{bounds['E']:.3f}]"
                    )

                except Exception as e:
                    self.status_text = f"Failed to derive boundary from .nc: {e}"
                    self.alert.object = self.status_text
                    return
            start_annotation_process(
                env_var_map=env_var_map,
                selected_env_vars=selected_vars,
                movebank_path=str(movebank_path),
                selected_ids=selected_ids,
                boundary_path=str(boundary_path) if boundary_path else None,
                interpolation_method=interp_method,
                bbox=bbox,
                smoothing_k=int(self.tif_control_smoothing.value),
                out_csv_path=output_csv_path,
                continuous_vars=continuous_vars,
                categorical_vars=categorical_vars,
                # TIF value correction is applied after sampling,
                # and only to continuous variables.
                apply_value_correction=bool(self.tif_apply_scale.value),
                value_scale_factor=float(self.tif_scale_factor.value),
                value_add_offset=float(self.tif_add_offset.value),
                value_correction_vars=continuous_vars,
                movement_column_map=tif_movement_column_map,
                time_range_mode=self.tif_time_range_mode.value,
            )
            self.status_text = "Annotation finished successfully (TIF)."
            self.alert.object = self.status_text
        except Exception as e:
            self.status_text = f"Annotation failed (TIF): {e}"
            self.alert.object = self.status_text
            print("[ERROR] Annotation failed (TIF):", e)
        finally:
            try:
                stop_loading_spinner()
            except Exception:
                pass

    @try_catch("Error loading TIF boundary data")
    def load_boundary_data_tif(self, *events):
        self.status_text = "Loading TIF boundary data..."
        self.alert.object = self.status_text

        file_input = self.tif_bound_data_selector.value
        if not file_input:
            self.status_text = "Please select one vector file (.shp or .geojson)."
            self.alert.object = self.status_text
            return

        if isinstance(file_input, list):
            if len(file_input) != 1:
                self.status_text = "Please select exactly one vector file (.shp or .geojson)."
                self.alert.object = self.status_text
                return
            file_path = file_input[0]
        else:
            file_path = file_input

        try:
            path, S, N, W, E = load_vector_extent_info(file_path)
            self.tif_boundary_path = path
            self.tif_boundary_extent = {"S": S, "N": N, "W": W, "E": E}
            self.tif_boundary_info_str.object = (
                f"Boundary file: {Path(path).name} <br>" f"Spatial range: lat[{S:.3f}..{N:.3f}], lon[{W:.3f}..{E:.3f}]"
            )
            self.status_text = f"TIF Boundary loaded: " f"lat[{S:.3f}..{N:.3f}], lon[{W:.3f}..{E:.3f}]"
        except Exception as e:
            self.status_text = f"Failed to read vector file: {e}"
        self.alert.object = self.status_text

    @try_catch("Error loading TIF movement data")
    def load_movement_data_tif(self, *events):
        self.status_text = "Loading TIF movement data..."
        self.alert.object = self.status_text
        file_path = self.tif_movement_data_selector.value
        if not file_path:
            self.status_text = "No TIF movement file selected."
            self.alert.object = self.status_text
            return

        custom_format = self.tif_movement_csv_type.value == CSV_FORMAT_CUSTOM

        if custom_format:
            id_column = self.tif_movement_id_column.value
            taxon_column = self.tif_movement_taxon_column.value
            time_column = self.tif_movement_time_column.value
            lat_column = self.tif_movement_lat_column.value
            lon_column = self.tif_movement_lon_column.value

            required = {
                "Animal ID column": id_column,
                "Time column": time_column,
                "Latitude column": lat_column,
                "Longitude column": lon_column,
            }

            missing = [name for name, value in required.items() if not value]

            if missing:
                self.status_text = "Please select: " + ", ".join(missing)
                self.alert.object = self.status_text
                return

        else:
            id_column = None
            taxon_column = None
            time_column = None
            lat_column = None
            lon_column = None

        df, taxa, ids, err = load_taxa_and_ids_from_csv(
            file_path,
            id_column=id_column,
            taxon_column=taxon_column,
            time_column=time_column,
            lat_column=lat_column,
            lon_column=lon_column,
        )

        if err:
            self.status_text = f"Error: {err}"
            self.alert.object = self.status_text
            return

        self.tif_movement_df = df
        self.tif_id_multiselect.options = ids
        self.tif_id_multiselect.disabled = False
        self.tif_taxon_multiselect.options = taxa
        self.tif_taxon_multiselect.disabled = False
        self.status_text = f"TIF: Loaded {len(ids)} IDs and " f"{len(taxa)} taxon names."
        mv_current = (
            self.tif_movement_info.object
            or "File: not selected <br>" "Taxons: - <br>" "IDs: - <br>" "Time range: - <br>" "Spatial range: -"
        )
        lines = mv_current.split("<br>")

        if lines:
            lines[0] = f"File: {Path(file_path).name}"

        try:
            ts = pd.to_datetime(df["timestamp"], errors="coerce")
            lat = pd.to_numeric(df["location_lat"], errors="coerce")
            lon = pd.to_numeric(df["location_lon"], errors="coerce")
            if ts.notna().any():
                tmin = ts.min().strftime("%Y-%m-%d %H:%M:%S")
                tmax = ts.max().strftime("%Y-%m-%d %H:%M:%S")

                for i, line in enumerate(lines):
                    if line.startswith("Time range:"):
                        lines[i] = f"Time range: {tmin} — {tmax}"

            if lat.notna().any() and lon.notna().any():
                lat_min = float(lat.min())
                lat_max = float(lat.max())
                lon_min = float(lon.min())
                lon_max = float(lon.max())

                for i, line in enumerate(lines):
                    if line.startswith("Spatial range:"):
                        lines[i] = (
                            "Spatial range: "
                            f"lat[{lat_min:.3f}..{lat_max:.3f}], "
                            f"lon[{lon_min:.3f}..{lon_max:.3f}]"
                        )

        except Exception:
            pass

        self.tif_movement_info.object = "<br>".join(lines)
        self.alert.object = self.status_text


    def update_annotation_ids_by_taxon_tif(self, event):
        if self.tif_movement_df is None:
            return

        selected_taxa = event.new

        if not selected_taxa:
            ids = sorted(self.tif_movement_df["individual_local_identifier"].dropna().astype(str).unique())
        else:
            filtered = self.tif_movement_df[self.tif_movement_df["individual_taxon_canonical_name"].isin(selected_taxa)]
            ids = sorted(filtered["individual_local_identifier"].dropna().astype(str).unique())

        self.tif_id_multiselect.options = ids
        self.tif_id_multiselect.value = ids

    def update_env_info_text(self, selected_vars):
        current = self.env_info.object or ""
        lines = current.split("<br>")
        updated_lines = []
        found = False
        for line in lines:
            if "Environment parameters" in line:
                updated_lines.append(f"Environment parameters: {', '.join(selected_vars) if selected_vars else '-'}")
                found = True
            else:
                updated_lines.append(line)
        if not found:
            updated_lines.insert(1, f"Environment parameters: {', '.join(selected_vars) if selected_vars else '-'}")
        self.env_info.object = "<br>".join(updated_lines)

    def update_movement_info_text(self, section, new_values):
        current = self.movement_info.object or ""
        lines = current.split("<br>")
        updated_lines = []
        for line in lines:
            if section == "Taxons" and "Taxons" in line:
                updated_lines.append(f"Taxons: {', '.join(new_values) if new_values else '-'}")
            elif section == "IDs" and "IDs" in line:
                updated_lines.append(f"IDs: {', '.join(new_values) if new_values else '-'}")
            else:
                updated_lines.append(line)
        self.movement_info.object = "<br>".join(updated_lines)

    def update_env_info_text_tif(self, selected_vars):
        current = self.tif_env_info.object or ""
        if not current:
            current = "File: not selected <br>Environment parameters: - <br>Time range: - <br>Spatial range: - <br>"
        lines = current.split("<br>")
        updated = []
        found = False
        for line in lines:
            if "Environment parameters" in line:
                updated.append(f"Environment parameters: {', '.join(selected_vars) if selected_vars else '-'}")
                found = True
            else:
                updated.append(line)
        if not found:
            updated.insert(1, f"Environment parameters: {', '.join(selected_vars) if selected_vars else '-'}")
        self.tif_env_info.object = "<br>".join(updated)

    def _guess_tif_variable_types(self, variables):
        """
        Return initial (continuous, categorical) split for TIF-derived variables.
        This is only a first guess. The user can manually change the selection.
        """
        categorical_keywords = [
            "qc",
            "quality",
            "flag",
            "mask",
            "class",
            "category",
            "categorical",
            "landcover",
            "land_cover",
            "classification",
            "type",
        ]

        categorical = [v for v in variables if any(key in str(v).lower() for key in categorical_keywords)]
        continuous = [v for v in variables if v not in categorical]

        return continuous, categorical

    def _sync_tif_variable_type_selection(self, event=None):
        """
        Ensure that the same TIF-derived variable cannot be selected
        as both continuous and categorical/QC.
        """
        if getattr(self, "_syncing_tif_var_types", False):
            return

        self._syncing_tif_var_types = True
        try:
            continuous = set(self.tif_continuous_vars.value or [])
            categorical = set(self.tif_categorical_vars.value or [])

            overlap = continuous & categorical
            if not overlap:
                return

            # If the user changed Continuous, remove overlap from Categorical/QC.
            if event is not None and event.obj is self.tif_continuous_vars:
                self.tif_categorical_vars.value = [
                    v for v in (self.tif_categorical_vars.value or []) if v not in overlap
                ]

            # If the user changed Categorical/QC, remove overlap from Continuous.
            elif event is not None and event.obj is self.tif_categorical_vars:
                self.tif_continuous_vars.value = [v for v in (self.tif_continuous_vars.value or []) if v not in overlap]

        finally:
            self._syncing_tif_var_types = False

    def update_movement_info_text_tif(self, section, new_values):
        current = self.tif_movement_info.object or ""
        if not current:
            current = "File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: - <br>"
        lines = current.split("<br>")
        updated = []
        for line in lines:
            if section == "Taxons" and "Taxons" in line:
                updated.append(f"Taxons: {', '.join(new_values) if new_values else '-'}")
            elif section == "IDs" and "IDs" in line:
                updated.append(f"IDs: {', '.join(new_values) if new_values else '-'}")
            else:
                updated.append(line)
        self.tif_movement_info.object = "<br>".join(updated)

    def _update_info_lines(self, pane, changes: dict):
        """
        Safely updates rows in pane.object by tags:
        changes = {"File:": "...", "Time range:": "...", "Spatial range:": "...", "Environment parameters:": "..."}
        If the row with the tag does not exist, it is added.
        """
        default = "File: not selected <br>Environment parameters: - <br>Time range: - <br>Spatial range: - <br>"
        current = pane.object or default
        lines = current.split("<br>")
        idx = {}
        for i, line in enumerate(lines):
            for key in changes.keys():
                if line.strip().startswith(key):
                    idx[key] = i

        for key, val in changes.items():
            if key in idx:
                lines[idx[key]] = f"{key} {val}"
            else:
                # insert at the end before the empty last one, if there is one
                insert_pos = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
                lines.insert(insert_pos, f"{key} {val}")

        pane.object = "<br>".join(lines)

    def _section(self, title, *items, height=None):
        body = pn.Column(*items, sizing_mode="stretch_width")

        return pn.Card(
            body, title=title, collapsible=False, margin=(0, 0, 10, 0), sizing_mode="stretch_width", height=height
        )

    def _auto_height(self, pane, line_px=22, padding=8):
        lines = [l for l in (pane.object or "").split("<br>") if l.strip()]
        pane.height = line_px * max(1, len(lines)) + padding

    def _update_smoothing_options(self, event):
        key = self._normalize_interp_key(event.new)

        if key in ("nearest", "bilinear"):
            self.control_smoothing.options = ["1"]
            self.control_smoothing.value = "1"
            self.control_smoothing.disabled = key == "bilinear"
        else:
            self.control_smoothing.disabled = False
            self.control_smoothing.options = ["2", "4", "6", "8"]
            if self.control_smoothing.value == "1":
                self.control_smoothing.value = "4"

    def _update_tif_scale_widgets(self, event=None):
        """
        Enable scale factor / offset inputs only when post-sampling value correction is enabled.
        """
        enabled = bool(self.tif_apply_scale.value)
        self.tif_scale_factor.disabled = not enabled
        self.tif_add_offset.disabled = not enabled

    def _update_smoothing_options_tif(self, event):
        key = self._normalize_interp_key(event.new)
        if key == "nearest":
            self.tif_control_smoothing.options = ["1"]
            self.tif_control_smoothing.value = "1"
        else:
            self.tif_control_smoothing.options = ["2", "4", "6", "8"]
            if self.tif_control_smoothing.value == "1":
                self.tif_control_smoothing.value = "4"

    def _sync_nc_column_heights(self):
        """Adjusts the height of the 2nd and 3rd columns to the 1st."""
        first = getattr(self, "_nc_col1", None)
        second = getattr(self, "_nc_col2", None)
        third = getattr(self, "_nc_col3", None)
        if not first or not second or not third:
            return

        if first.height is None:
            pn.state.onload(lambda: self._apply_nc_height_from_first())
        else:
            self._apply_nc_height_from_first()

    def _apply_nc_height_from_first(self):
        first = self._nc_col1
        if not first:
            return
        h = first.height
        if h is None:
            return
        self._nc_col2.height = h
        self._nc_col3.height = h

    def reset_boundary_data(self, *events):
        if self.boundary_mode.value == BOUNDARY_MODE_BBOX:
            self.boundary_south.value = None
            self.boundary_north.value = None
            self.boundary_west.value = None
            self.boundary_east.value = None
        else:
            self.nc_boundary_path = None
            self.nc_boundary_extent = None

        self._apply_boundary_mode_ui()

        self.status_text = "NC boundary reset. Environmental data extent " "will be used if no boundary is specified."
        self.alert.object = self.status_text
        self._sync_nc_column_heights()

    def reset_boundary_data_tif(self, *events):
        if self.tif_boundary_mode.value == BOUNDARY_MODE_BBOX:
            self.tif_boundary_south.value = None
            self.tif_boundary_north.value = None
            self.tif_boundary_west.value = None
            self.tif_boundary_east.value = None
        else:
            self.tif_boundary_path = None
            self.tif_boundary_extent = None

        self._apply_tif_boundary_mode_ui()

        self.status_text = "TIF boundary reset. Environmental data extent " "will be used if no boundary is specified."
        self.alert.object = self.status_text

    def _apply_movement_csv_type_ui(self):
        custom = self.movement_csv_type.value == CSV_FORMAT_CUSTOM

        if hasattr(self, "_movement_custom_columns_panel"):
            self._movement_custom_columns_panel.visible = custom

        if custom:
            self._populate_custom_movement_columns()

    def _get_nc_manual_bbox(self):
        return validate_bbox(
            {
                "S": self.boundary_south.value,
                "N": self.boundary_north.value,
                "W": self.boundary_west.value,
                "E": self.boundary_east.value,
            }
        )

    def _get_tif_manual_bbox(self):
        return validate_bbox(
            {
                "S": self.tif_boundary_south.value,
                "N": self.tif_boundary_north.value,
                "W": self.tif_boundary_west.value,
                "E": self.tif_boundary_east.value,
            }
        )

    def _apply_boundary_mode_ui(self):
        use_bbox = self.boundary_mode.value == BOUNDARY_MODE_BBOX
        self._nc_boundary_file_panel.visible = not use_bbox
        self._nc_bbox_panel.visible = use_bbox

        if use_bbox:
            self._update_nc_bbox_info()
        else:
            if self.nc_boundary_path and self.nc_boundary_extent:
                b = self.nc_boundary_extent
                self.boundary_info_str.object = (
                    f"Boundary file: {Path(self.nc_boundary_path).name} <br>"
                    f"Spatial range: "
                    f"lat[{b['S']:.3f}..{b['N']:.3f}], "
                    f"lon[{b['W']:.3f}..{b['E']:.3f}]"
                )
            else:
                self.boundary_info_str.object = (
                    "Boundary file: not selected <br>" "Spatial range: = environment data boundary"
                )

    def _on_boundary_mode_changed(self, event):
        self._apply_boundary_mode_ui()

    def _on_boundary_bbox_changed(self, event):
        if self.boundary_mode.value == BOUNDARY_MODE_BBOX:
            self._update_nc_bbox_info()

    def _update_nc_bbox_info(self):
        values = (
            self.boundary_south.value,
            self.boundary_north.value,
            self.boundary_west.value,
            self.boundary_east.value,
        )

        if any(value is None for value in values):
            self.boundary_info_str.object = "Boundary: bbox <br>" "Spatial range: enter S, N, W and E"
            return

        try:
            bbox = self._get_nc_manual_bbox()

            self.boundary_info_str.object = (
                "Boundary: bbox <br>"
                f"Spatial range: "
                f"lat[{bbox['S']:.3f}..{bbox['N']:.3f}], "
                f"lon[{bbox['W']:.3f}..{bbox['E']:.3f}]"
            )

        except Exception as e:
            self.boundary_info_str.object = "Boundary: bbox <br>" f"Validation: {e}"

    def _apply_tif_boundary_mode_ui(self):
        use_bbox = self.tif_boundary_mode.value == BOUNDARY_MODE_BBOX

        self._tif_boundary_file_panel.visible = not use_bbox
        self._tif_bbox_panel.visible = use_bbox

        if use_bbox:
            self._update_tif_bbox_info()
        else:
            if self.tif_boundary_path and self.tif_boundary_extent:
                b = self.tif_boundary_extent

                self.tif_boundary_info_str.object = (
                    f"Boundary file: {Path(self.tif_boundary_path).name} <br>"
                    f"Spatial range: "
                    f"lat[{b['S']:.3f}..{b['N']:.3f}], "
                    f"lon[{b['W']:.3f}..{b['E']:.3f}]"
                )
            else:
                self.tif_boundary_info_str.object = (
                    "Boundary file: not selected <br>" "Spatial range: = environment data boundary"
                )

    def _on_tif_boundary_mode_changed(self, event):
        self._apply_tif_boundary_mode_ui()

    def _on_tif_boundary_bbox_changed(self, event):
        if self.tif_boundary_mode.value == BOUNDARY_MODE_BBOX:
            self._update_tif_bbox_info()

    def _update_tif_bbox_info(self):
        values = (
            self.tif_boundary_south.value,
            self.tif_boundary_north.value,
            self.tif_boundary_west.value,
            self.tif_boundary_east.value,
        )

        if any(value is None for value in values):
            self.tif_boundary_info_str.object = "Boundary: bbox <br>" "Spatial range: enter S, N, W and E"
            return

        try:
            bbox = self._get_tif_manual_bbox()

            self.tif_boundary_info_str.object = (
                "Boundary: bbox <br>"
                f"Spatial range: "
                f"lat[{bbox['S']:.3f}..{bbox['N']:.3f}], "
                f"lon[{bbox['W']:.3f}..{bbox['E']:.3f}]"
            )

        except Exception as e:
            self.tif_boundary_info_str.object = "Boundary: bbox <br>" f"Validation: {e}"

    def _invalidate_loaded_movement(self):
        self.nc_movement_df = None
        self.taxon_multiselect.options = []
        self.taxon_multiselect.value = []
        self.taxon_multiselect.disabled = True
        self.id_multiselect.options = []
        self.id_multiselect.value = []
        self.id_multiselect.disabled = True
        self.movement_info.object = (
            "File: not selected <br>" "Taxons: - <br>" "IDs: - <br>" "Time range: - <br>" "Spatial range: - <br>"
        )

    def _on_movement_csv_type_changed(self, event):
        self._invalidate_loaded_movement()
        self._apply_movement_csv_type_ui()

    def _on_movement_file_changed(self, event):
        self._invalidate_loaded_movement()

        if self.movement_csv_type.value == CSV_FORMAT_CUSTOM:
            self._populate_custom_movement_columns()

    def _invalidate_loaded_tif_movement(self):
        self.tif_movement_df = None
        self.tif_taxon_multiselect.options = []
        self.tif_taxon_multiselect.value = []
        self.tif_taxon_multiselect.disabled = True
        self.tif_id_multiselect.options = []
        self.tif_id_multiselect.value = []
        self.tif_id_multiselect.disabled = True
        self.tif_movement_info.object = (
            "File: not selected <br>" "Taxons: - <br>" "IDs: - <br>" "Time range: - <br>" "Spatial range: - <br>"
        )

    def _populate_custom_movement_columns(self):
        raw = self.movement_data_selector.value
        empty_options = {"— select column —": None}
        widgets = (
            self.movement_taxon_column,
            self.movement_id_column,
            self.movement_time_column,
            self.movement_lat_column,
            self.movement_lon_column,
        )

        if not raw:
            for widget in widgets:
                widget.options = empty_options
                widget.value = None
            return

        if isinstance(raw, (list, tuple, set)):
            if len(raw) != 1:
                return
            file_path = str(list(raw)[0])
        else:
            file_path = str(raw)

        if Path(file_path).suffix.lower() != ".csv":
            return

        try:
            columns = list(pd.read_csv(file_path, nrows=0).columns)
        except Exception:
            return

        options = {"— select column —": None, **{str(column): str(column) for column in columns}}

        for widget in widgets:
            widget.options = options
            widget.value = None

    def _apply_tif_movement_csv_type_ui(self):
        custom = self.tif_movement_csv_type.value == CSV_FORMAT_CUSTOM

        if hasattr(self, "_tif_movement_custom_columns_panel"):
            self._tif_movement_custom_columns_panel.visible = custom

        if custom:
            self._populate_custom_tif_movement_columns()

    def _on_tif_movement_csv_type_changed(self, event):
        self._invalidate_loaded_tif_movement()
        self._apply_tif_movement_csv_type_ui()

    def _on_tif_movement_file_changed(self, event):
        self._invalidate_loaded_tif_movement()

        if self.tif_movement_csv_type.value == CSV_FORMAT_CUSTOM:
            self._populate_custom_tif_movement_columns()

    def _populate_custom_tif_movement_columns(self):
        raw = self.tif_movement_data_selector.value

        empty_options = {"— select column —": None}

        widgets = (
            self.tif_movement_taxon_column,
            self.tif_movement_id_column,
            self.tif_movement_time_column,
            self.tif_movement_lat_column,
            self.tif_movement_lon_column,
        )

        if not raw:
            for widget in widgets:
                widget.options = empty_options
                widget.value = None
            return

        if isinstance(raw, (list, tuple, set)):
            if len(raw) != 1:
                return
            file_path = str(list(raw)[0])
        else:
            file_path = str(raw)

        if Path(file_path).suffix.lower() != ".csv":
            return

        try:
            columns = list(pd.read_csv(file_path, nrows=0).columns)
        except Exception:
            return

        options = {"— select column —": None, **{str(column): str(column) for column in columns}}

        for widget in widgets:
            widget.options = options
            widget.value = None


@register_view()
def view():
    viewer = movebank_annotation_engine()
    template = DEFAULT_TEMPLATE(main=[viewer.alert, viewer.view])
    return template


if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})

if __name__.startswith("bokeh"):
    view()
