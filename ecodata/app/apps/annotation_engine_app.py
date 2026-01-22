import logging
from pathlib import Path
import panel as pn
import param
import pandas as pd
import xarray as xr
from panel.io.loading import start_loading_spinner, stop_loading_spinner
from ecodata.app.models import FileSelector
from ecodata.panel_utils import param_widget, register_view, try_catch, rename_param_widgets
from ecodata.app.config import DEFAULT_TEMPLATE
from datetime import datetime
import re
from ecodata import validate_and_process_csv, load_vector_extent_info, load_taxa_and_ids_from_csv
from ecodata.movebank_functions import merge_csv_files_from_folder, generate_individual_csvs_for_local_ids, interpolate_missing_values_only, delete_files
from ecodata.annotation_eng_func import start_annotation_process,convert_tif_to_nc_before_annotation, get_nc_bounds, open_nc_metadata, detect_env_coord_names, safe_open_nc_with_time_decoding

logger = logging.getLogger(__file__)

class movebank_annotation_engine(param.Parameterized):
    local_ID_file = param_widget(FileSelector(constrain_path=False, expanded=True, size=10))
    load_data_button = param_widget(pn.widgets.Button(name="Load data", button_type="primary"))
    taxon_name_val = param_widget(
        pn.widgets.MultiSelect(name="Taxon name (press Ctrl for multiple selection)", options=[], height = 140, disabled=True)
    )
    individual_ID = param_widget(
        pn.widgets.MultiSelect(name="Individual ID (press Ctrl for multiple selection)", options=[], height = 140, disabled=True)
    )
    simple_interp_button = param_widget(pn.widgets.Button(name="Simple interpolation (missing ≤ 1 day)", button_type="primary"))
    deployment_time_gap = param_widget(
        pn.widgets.IntInput(name="Deployment time gap (minutes)", value=60, step=60, start=0)
    )
    min_expected_obs = param_widget(
    pn.widgets.IntInput(name="Minimum expected number of observations(per deployment)", value=100, step=50, start=10)
    )

    time_selection_ID = param_widget(
        pn.widgets.DatetimeRangeSlider(
            name="Select Time Range",
            start=datetime(2010, 1, 1),
            end=datetime(2025, 12, 31),
            value=(datetime(2016, 6, 13), datetime(2016, 6, 14)),
            step=2_592_000_000
        )
    )
    time_interval = param_widget(pn.widgets.IntInput(name="Timestep for Interpolation/Averaging (minutes)", value=30, step=1, start=1))
    start_from_midnight = param_widget(pn.widgets.Checkbox(name="First timestamp = 00:00:00", value=False))
    out_csv_name = param_widget(pn.widgets.TextInput(name="Output CSV", value=str(Path.home() / "Downloads" / "subset.csv")))
    make_csv = param_widget(pn.widgets.Button(name="Make CSV", button_type="primary"))
    merge_files = param_widget(pn.widgets.Checkbox(name="Merge files after processing", value=False))
    delete_individual_ID_files = param_widget(pn.widgets.Checkbox(name="Delete individual files after merge", value=True))

    folder_to_merge = param_widget(pn.widgets.TextInput(name="Folder with CSV files to merge (select folder)", value=str(Path.home() / "Downloads")))
    delete_empty_columns = param_widget(pn.widgets.Checkbox(name="Delete empty columns after merging", value=False))
    out_merged_csv_name = param_widget(pn.widgets.TextInput(name="Output merged CSV", value=str(Path.home() / "Downloads" / "merged.csv")))
    merge_files_button = param_widget(pn.widgets.Button(name="Merge files in folder", button_type="primary"))

    # === Annotation Engine widgets ===
    env_data_selector = param_widget(
            FileSelector(
                name="Environmental data (.nc)",
                constrain_path=False,
                expanded=True,
                size=10
            )
        )
    bound_data_selector = param_widget(FileSelector(name="Boundary data (.shp)", constrain_path=False, expanded=True, size=10))
    movement_data_selector = param_widget(FileSelector(name="Movebank data (.csv)", constrain_path=False, expanded=True, size=10))
    load_env_button = pn.widgets.Button(name="Load environmental data", button_type="primary")
    load_movement_button = pn.widgets.Button(name="Load movement data", button_type="primary")
    load_bound_button = pn.widgets.Button(name="Load boundary data", button_type="primary")
    reset_bound_button = pn.widgets.Button(name="(!) Reset boundary", button_type="primary")

    # Selections for netcdf variable labels
    env_time_select = pn.widgets.Select(name="Env time coordinate", options=[], value=None)
    env_spatial_mode = pn.widgets.RadioButtonGroup(name="Env spatial coordinate mode",
                                                   options=["Geographic (lat/lon)", "Projected (x/y)"],
                                                   value="Geographic (lat/lon)",
                                                   button_type="default",
                                                   )
    env_lat_select  = pn.widgets.Select(name="Latitude", options=[], value=None)
    env_lon_select  = pn.widgets.Select(name="Longitude", options=[], value=None)
    env_x_select    = pn.widgets.Select(name="X coordinate", options=[], value=None)
    env_y_select    = pn.widgets.Select(name="Y coordinate", options=[], value=None)

    env_data_multiselect = pn.widgets.MultiSelect(name="Environmental variables (use Ctrl for multiple)", options=[], height = 140 )
    taxon_multiselect = pn.widgets.MultiSelect(name="Select Taxon (use Ctrl for multiple)", height = 140)
    id_multiselect = pn.widgets.MultiSelect(name="Select ID (use Ctrl for multiple)", height = 140)
    env_info = pn.pane.HTML("File: not selected <br>Environment parameters: - <br>Time range: - <br>Spatial range: - <br>",
                             sizing_mode="stretch_width")
    movement_info = pn.pane.HTML("File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: - <br>",
                            sizing_mode="stretch_width")
    control_smoothing = pn.widgets.Select(
        name="Number of nearest grid points",
        options=["2", "4", "6", "8"],
        value="4"
    )
    output_path = pn.widgets.TextInput(name="Output path", value=str(Path.home() / "Downloads" / "annotated_env.csv"))
    boundary_info_str = pn.pane.HTML(
        "Boundary file: not selected <br>Spatial range: = environment data boundary",
        name="",
        styles={"white-space": "pre-wrap"},
        sizing_mode="stretch_width"
    )
    interpolation_method = pn.widgets.Select(
        name="Interpolation method (spatial)",
        options=["Nearest neighbor (time-linear)", "Inverse Distance Weighting (time-linear)", "Bilinear (projected x/y, time-linear)"],
        value="Inverse Distance Weighting (time-linear)"
    )
    make_annotation_button = pn.widgets.Button(name="Make annotated file", button_type="primary")


    status_text = param.String("Ready...")
    #TIF widgets
    # === TIF Annotation Engine widgets ===
    tif_env_data_selector = param_widget(
        FileSelector(
            name="Select any .tif file in folder",
            constrain_path=False,
            expanded=True,
            size=10
        )
    )
    tif_movement_data_selector = param_widget(FileSelector(name="Movebank data", constrain_path=False, expanded=True,size=10))
    tif_bound_data_selector = param_widget(FileSelector(name="Boundary data", constrain_path=False, expanded=True, size=10))

    tif_load_env_button = pn.widgets.Button(name="Load TIF environmental data", button_type="primary")
    tif_load_movement_button = pn.widgets.Button(name="Load movement data", button_type="primary")
    tif_load_bound_button = pn.widgets.Button(name="Load boundary data", button_type="primary")
    tif_reset_bound_button = pn.widgets.Button(name="(!) Reset boundary", button_type="primary")
    tif_control_smoothing = pn.widgets.Select(
        name="Number of nearest grid points",
        options=["2", "4", "6", "8"],
        value="4"
    )
    tif_env_data_multiselect = pn.widgets.MultiSelect(name="netCDF Environmental variables", options=[], height = 140)
    tif_taxon_multiselect = pn.widgets.MultiSelect(name="Select Taxon", height = 140)
    tif_id_multiselect = pn.widgets.MultiSelect(name="Select ID", height = 140)
    tif_env_info = pn.pane.HTML("File: not selected <br>Environment parameters: - <br>Time range: - <br>Spatial range: - <br>",
                            sizing_mode="stretch_width")
    tif_movement_info = pn.pane.HTML("File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: - <br>",
                                 sizing_mode="stretch_width")
    tif_output_path = pn.widgets.TextInput(name="Output path", value=str(Path.home() / "Downloads" / "annotated_env_tif.csv"))
    tif_boundary_info_str = pn.pane.HTML(
        "Boundary file: not selected <br> Spatial range: = environment data boundary",
        sizing_mode="stretch_width"
    )

    tif_interpolation_method = pn.widgets.Select(
        name="Interpolation method (spatial)",
        options=["Nearest neighbor (time-linear)", "Inverse Distance Weighting (time-linear)"],
        value="Inverse Distance Weighting (time-linear)"
    )
    tif_make_annotation_button = pn.widgets.Button(name="Make annotated file", button_type="primary")


    def __init__(self, **params):
        super().__init__(**params)

        self.interpolation_method.name = "Spatial interpolation method (.nc)"
        self.tif_interpolation_method.name = "Spatial interpolation method (.tif)"
        rename_param_widgets(
            self,
            [
                "local_ID_file", "load_data_button",
                "taxon_name_val", "individual_ID", "simple_interp_button",
                "deployment_time_gap", "min_expected_obs",
                "time_selection_ID", "time_interval",
                "start_from_midnight", "out_csv_name",
                "make_csv", "merge_files",
                "delete_individual_ID_files","folder_to_merge",
                "delete_empty_columns", "out_merged_csv_name",
                "merge_files_button",
                 # === NC Annotation tab ===
                  "env_data_selector",
                "bound_data_selector", "movement_data_selector",
                "load_env_button", "load_bound_button", "reset_bound_button",
                "load_movement_button", "env_data_multiselect",
                "taxon_multiselect",  "id_multiselect",
                "boundary_info_str", "interpolation_method",
                "control_smoothing",
                "env_info", "movement_info" ,"output_path",
                "make_annotation_button",
                # === TIF Annotation tab ===
                "tif_env_data_selector",
                "tif_movement_data_selector",
                "tif_bound_data_selector","tif_reset_bound_button",
                "tif_env_data_multiselect",
                "tif_taxon_multiselect",
                "tif_id_multiselect",
                "tif_interpolation_method", "tif_control_smoothing",
                "tif_env_info", "tif_movement_info",
                "tif_make_annotation_button"
            ]
        )
        self._latlon_widgets = pn.Column(self.env_lat_select, self.env_lon_select, sizing_mode="stretch_width")
        self._xy_widgets     = pn.Column(self.env_x_select,   self.env_y_select,   sizing_mode="stretch_width")
        self.df = None
        self.alert = pn.pane.Markdown(self.status_text)
        NC_H = 1080
        # === NC tab  ===
        self._nc_col1 = self._section(
            "Environmental data (.nc)",
            pn.Column(self.env_data_selector, sizing_mode="stretch_width"),
            self.load_env_button,
            self.env_time_select,
            self.env_spatial_mode,
            self._latlon_widgets,
            self._xy_widgets,
            self.env_data_multiselect,
            self.env_info,
            self.interpolation_method,
            self.control_smoothing,
            self.output_path,
            self.make_annotation_button,
            height=NC_H,
        )
        self._nc_col2 = self._section(
            "Movebank data (.csv)",
            pn.Column(self.movement_data_selector, sizing_mode="stretch_width"),
            self.load_movement_button,
            self.taxon_multiselect,
            self.movement_info,
            height=NC_H,
        )
        self._nc_col3 = self._section(
            "Boundary data (.shp/.geojson)",
            pn.Column(self.bound_data_selector, sizing_mode="stretch_width"),
            pn.Row(self.load_bound_button, self.reset_bound_button),
            self.id_multiselect,
            self.boundary_info_str,
            height=NC_H,
        )

        # synchronize heights after rendering
        pn.state.onload(self._sync_nc_column_heights)

        self.anotation_engine_tab = pn.Column(
            pn.pane.Markdown("### Annotation engine - .nc", sizing_mode="stretch_width"),
            pn.GridBox(
                self._nc_col1, self._nc_col2, self._nc_col3,
                ncols=3, sizing_mode="stretch_width",
            ),
        )

        # TIF
        TIF_H = 1080
        self._tif_col1 = self._section(
            "Environmental data (.tif) - select one (of)",
            pn.Column(self.tif_env_data_selector, sizing_mode="stretch_width"),
            self.tif_load_env_button,
            self.tif_env_data_multiselect,
            self.tif_env_info,
            self.tif_interpolation_method,
            self.tif_control_smoothing,
            self.tif_output_path,
            self.tif_make_annotation_button,
            height=TIF_H,
        )

        self._tif_col2 = self._section(
            "Movebank data (.csv)",
            pn.Column(self.tif_movement_data_selector, sizing_mode="stretch_width"),
            self.tif_load_movement_button,
            self.tif_taxon_multiselect,
            self.tif_movement_info,
            height=TIF_H,
        )

        self._tif_col3 = self._section(
            "Boundary data (.shp/.geojson)",
            pn.Column(self.tif_bound_data_selector, sizing_mode="stretch_width"),
            pn.Row(self.tif_load_bound_button, self.tif_reset_bound_button),
            self.tif_id_multiselect,
            self.tif_boundary_info_str,
            height=TIF_H,
        )

        self.anotation_engine_tif_tab = pn.Column(
            pn.pane.Markdown("### Annotation engine - .tif", sizing_mode="stretch_width"),
            pn.GridBox(
                self._tif_col1, self._tif_col2, self._tif_col3,
                ncols=3,
                sizing_mode="stretch_width",
            ),
        )

        self.crop_interpolate_tab = pn.Column(
            pn.pane.Markdown("### Crop files"),
            self.local_ID_file,
            self.load_data_button,
            pn.Row(
                self.taxon_name_val,
                self.individual_ID,
            ),
            self.simple_interp_button,
            pn.Column(self.deployment_time_gap, self.min_expected_obs),
            self.time_selection_ID,
            pn.Row(self.time_interval, self.start_from_midnight),
            self.out_csv_name,
            self.make_csv,
            self.merge_files,
            self.delete_individual_ID_files,
            self.alert
        )

        self.merge_tab = pn.Column(
            pn.pane.Markdown("### Merge files (Please select a **folder** with CSV files)"),
            self.folder_to_merge,
            self.delete_empty_columns,
            self.out_merged_csv_name,
            self.merge_files_button,
        )

        self.view = pn.Tabs(
            ("Annotation engine - .nc", self.anotation_engine_tab),
            ("Annotation engine - .tif", self.anotation_engine_tif_tab),
            ("Crop & interpolate csv", self.crop_interpolate_tab),
            ("Merge csv", self.merge_tab),
        )

        self.simple_interp_button.on_click(self.run_interpolate_missing_only)
        self.load_data_button.on_click(self.load_ids_from_file)
        self.make_csv.on_click(self.run_make_csv)
        self.merge_files_button.on_click(self.run_merge_files)
        self.taxon_name_val.param.watch(self.update_individual_ids_by_taxon, 'value')
        self.load_env_button.on_click(self.load_env_data)
        self.load_bound_button.on_click(self.load_boundary_data)
        self.reset_bound_button.on_click(self.reset_boundary_data)
        self.load_movement_button.on_click(self.load_movement_data)
        self.taxon_multiselect.param.watch(self.update_annotation_ids_by_taxon, 'value')
        self.make_annotation_button.on_click(self.run_annotation)
        self.env_data_multiselect.param.watch(lambda e: self.update_env_info_text(e.new), "value")
        self.taxon_multiselect.param.watch(lambda e: self.update_movement_info_text("Taxons", e.new), "value")
        self.id_multiselect.param.watch(lambda e: self.update_movement_info_text("IDs", e.new), "value")
        self.interpolation_method.param.watch(self._update_smoothing_options, 'value')
        ######TIF on click
        self.tif_load_env_button.on_click(self.load_env_data_tif)
        self.tif_load_bound_button.on_click(self.load_boundary_data_tif)
        self.tif_reset_bound_button.on_click(self.reset_boundary_data)
        self.tif_load_movement_button.on_click(self.load_movement_data_tif)
        self.tif_make_annotation_button.on_click(self.run_annotation_tif)
        self.tif_taxon_multiselect.param.watch(self.update_annotation_ids_by_taxon_tif, 'value')
        self.tif_env_data_multiselect.param.watch(lambda e: self.update_env_info_text_tif(e.new), "value")
        self.tif_taxon_multiselect.param.watch(lambda e: self.update_movement_info_text_tif("Taxons", e.new), "value")
        self.tif_id_multiselect.param.watch(lambda e: self.update_movement_info_text_tif("IDs", e.new), "value")
        self.tif_interpolation_method.param.watch(self._update_smoothing_options_tif, 'value')

        # environmental spatial mode
        self.env_spatial_mode.param.watch(lambda e: self._apply_env_spatial_mode(), "value")
        self._apply_env_spatial_mode()  # set initial enabled/disabled state


    @try_catch("Error loading Individual IDs")
    def load_ids_from_file(self, *events):
        self.status_text = "Loading IDs..."
        self.alert.object = self.status_text
        file_path = self.local_ID_file.value

        if not file_path:
            self.status_text = "No file selected."
            self.alert.object = self.status_text
            return

        try:
            df = pd.read_csv(file_path)
            df.columns = [re.sub(r"[-._\s]+", "_", col.lower()) for col in df.columns]  # normalize
            self.df = df
            self._set_time_slider_from_df(df)
            unique_ids = sorted(df["individual_local_identifier"].dropna().astype(str).unique())
            self.individual_ID.options = list(unique_ids)
            self.individual_ID.disabled = False

            if "individual_taxon_canonical_name" in df.columns:
                unique_taxa = sorted(df["individual_taxon_canonical_name"].dropna().astype(str).unique())
                self.taxon_name_val.options = list(unique_taxa)
                self.taxon_name_val.disabled = False
                self.status_text = f"Loaded {len(unique_ids)} Individual IDs and {len(unique_taxa)} Taxon names."
            else:
                self.status_text = f"Loaded {len(unique_ids)} Individual IDs. Column 'individual_taxon_canonical_name' not found."

        except Exception as e:
            logger.exception("Error loading IDs")
            self.status_text = f"Error: {e}"

        self.alert.object = self.status_text

    def update_individual_ids_by_taxon(self, event):
        if self.df is None:
            return

        selected_taxa = event.new

        if not selected_taxa:
            unique_ids = sorted(self.df["individual_local_identifier"].dropna().astype(str).unique())
            self.individual_ID.options = list(unique_ids)
            self.individual_ID.value = []
        else:
            filtered_df = self.df[self.df["individual_taxon_canonical_name"].isin(selected_taxa)]
            unique_ids = sorted(filtered_df["individual_local_identifier"].dropna().astype(str).unique())
            self.individual_ID.options = list(unique_ids)
            self.individual_ID.value = list(unique_ids)

    def update_annotation_ids_by_taxon(self, event):
        if self.df is None:
            return

        selected_taxa = event.new
        if not selected_taxa:
            ids = sorted(self.df["individual_local_identifier"].dropna().astype(str).unique())
        else:
            filtered = self.df[self.df["individual_taxon_canonical_name"].isin(selected_taxa)]
            ids = sorted(filtered["individual_local_identifier"].dropna().astype(str).unique())

        self.id_multiselect.options = ids
        self.id_multiselect.value = ids


    @try_catch("Error generating CSV")
    def run_make_csv(self, *events):
        try:
            individual_ids = self.individual_ID.value
            csv_path = Path(self.local_ID_file.value)
            interval_minutes = int(self.time_interval.value)

            start_time, end_time = self.time_selection_ID.value
            start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S.%f") if not isinstance(start_time, str) else start_time
            end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S.%f") if not isinstance(end_time, str) else end_time

            out_csv = self.out_csv_name.value
            columns = validate_and_process_csv(csv_path)

            output_files = generate_individual_csvs_for_local_ids(
                csv_file=csv_path,
                ids=individual_ids,
                start_time=start_time_str,
                end_time=end_time_str,
                interval_minutes=interval_minutes,
                output_path_template=out_csv,
                columns_to_interpolate=columns,
                deployment_time_gap=int(self.deployment_time_gap.value),
                min_expected_obs=int(self.min_expected_obs.value),
                start_from_midnight=bool(self.start_from_midnight.value)
            )

            if self.merge_files.value:
                merged_df = pd.concat([pd.read_csv(f) for f in output_files], ignore_index=True)
                merged_output_path = out_csv.replace(".csv", "_merged.csv")
                merged_df.to_csv(merged_output_path, index=False)

                if self.delete_individual_ID_files.value:
                    for f in output_files:
                        try:
                            Path(f).unlink()
                        except Exception as e:
                            logger.warning(f"Failed to delete {f}: {e}")

            self.status_text = f"Processing complete. Output saved to: {Path(out_csv).parent}"
        except Exception as e:
            logger.exception("Failed to generate CSV")
            self.status_text = f"Failed: {e}"

        self.alert.object = self.status_text

    def _set_time_slider_from_df(self, df: pd.DataFrame):
        # time column after name normalization
        candidates = ("timestamp", "eobs_start_timestamp", "time", "datetime", "date")
        time_col = next((c for c in candidates if c in df.columns), None)
        if not time_col:
            return

        ts = pd.to_datetime(df[time_col], errors="coerce")
        ts = ts[ts.notna()]
        if ts.empty:
            return

        tmin = pd.Timestamp(ts.min()).to_pydatetime()
        tmax = pd.Timestamp(ts.max()).to_pydatetime()

        # update the slider limits and values
        self.time_selection_ID.start = tmin
        self.time_selection_ID.end = tmax
        self.time_selection_ID.value = (tmin, tmax)


    @try_catch("Error merging files from folder")
    def run_merge_files(self, *events):
        try:
            folder_path = Path(self.folder_to_merge.value)
            merged_df, deleted_columns = merge_csv_files_from_folder(folder_path, self.delete_empty_columns.value)

            merged_output_path = self.out_merged_csv_name.value
            merged_df.to_csv(merged_output_path, index=False)

            deleted_msg = f"\nDeleted columns: {', '.join(deleted_columns)}" if deleted_columns else "\nNo columns deleted."
            self.status_text = f"Merged CSV saved: {merged_output_path}{deleted_msg}"
        except Exception as e:
            logger.exception("Failed to merge files")
            self.status_text = f"Failed: {e}"

        self.alert.object = self.status_text


    @try_catch("Error loading environmental data")
    def load_env_data(self, *events):
        """
        Load a single environmental NetCDF for UI inspection (metadata only).

        This method:
        1) Opens the dataset without time decoding (fast metadata read),
        2) Detects candidate coordinate names (time/x/y/lat/lon),
        3) Populates the variable list (including pressure-level-expanded labels),
        4) Updates the info pane (File/Spatial + optionally Time if readily decodable).

        Notes
        -----
        - This function is intended for UI responsiveness. It avoids CF-time decoding
        unless explicitly needed later (e.g., during annotation).
        """
        self.status_text = "Loading environmental data..."
        self.alert.object = self.status_text

        raw = self.env_data_selector.value
        if not raw:
            self.status_text = "Please select one .nc file."
            self.alert.object = self.status_text
            return

        # If the selector suddenly returns a list, we require exactly 1
        if isinstance(raw, (list, tuple, set)):
            if len(raw) != 1:
                self.status_text = "Select exactly one .nc file."
                self.alert.object = self.status_text
                return
            nc_path = str(list(raw)[0]).strip()
        else:
            nc_path = str(raw).strip()

        if Path(nc_path).suffix.lower() != ".nc":
            self.status_text = "Only .nc is supported on this tab."
            self.alert.object = self.status_text
            return

        # Update "File:" immediately
        self._update_info_lines(self.env_info, {"File:": Path(nc_path).name})
        self._auto_height(self.env_info)

        var_file_map: dict[str, str] = {}
        time_text = "-"  # will remain "-" unless we can decode reliably/cheaply
        spatial_text = "-"

        try:
            ds = open_nc_metadata(nc_path)
            try:
                # Autodetect coordinate names
                coord_guess = detect_env_coord_names(ds)

                # Populate dropdown menus
                self._populate_env_coord_dropdowns(ds, coord_guess)

                # Auto-set spatial mode based on available coordinates
                has_latlon = bool(self.env_lat_select.value and self.env_lon_select.value)
                has_xy = bool(self.env_x_select.value and self.env_y_select.value)

                if has_latlon and not has_xy:
                    self.env_spatial_mode.value = "Geographic (lat/lon)"
                elif has_xy and not has_latlon:
                    self.env_spatial_mode.value = "Projected (x/y)"
                # if both exist, don’t override user choice


                env_time = self.env_time_select.value
                env_lat  = self.env_lat_select.value
                env_lon  = self.env_lon_select.value
                env_x    = self.env_x_select.value
                env_y    = self.env_y_select.value

                # Update spatial_text with range from dataset (prefer lat/lon, fallback to x/y)
                if env_lat and env_lon and (env_lat in ds) and (env_lon in ds):
                    try:
                        lat_min = float(ds[env_lat].min())
                        lat_max = float(ds[env_lat].max())
                        lon_min = float(ds[env_lon].min())
                        lon_max = float(ds[env_lon].max())
                        spatial_text = (
                            f"{env_lat}[{lat_min:.3f}..{lat_max:.3f}], "
                            f"{env_lon}[{lon_min:.3f}..{lon_max:.3f}]"
                        )
                    except Exception:
                        spatial_text = "-"
                elif env_x and env_y and (env_x in ds) and (env_y in ds):
                    # Projected coordinates (units may be meters)
                    try:
                        x_min = float(ds[env_x].min())
                        x_max = float(ds[env_x].max())
                        y_min = float(ds[env_y].min())
                        y_max = float(ds[env_y].max())
                        spatial_text = (
                            f"{env_y}[{y_min:.3f}..{y_max:.3f}], "
                            f"{env_x}[{x_min:.3f}..{x_max:.3f}]"
                        )
                    except Exception:
                        spatial_text = "-"

                # Update time_text with time range if time decoding is cheap
                if env_time and (env_time in ds.coords or env_time in ds.variables):
                    try:
                        # Only attempt lightweight decode when CF-like units are present
                        decoded_times = xr.decode_cf(ds[[env_time]], decode_times=True)[env_time]
                        tmin = decoded_times.min()
                        tmax = decoded_times.max()
                        time_text = f"{tmin.strftime('%Y-%m-%d')} — {tmax.strftime('%Y-%m-%d')}"
                    except Exception:
                        time_text = "-"


                # ---- Перелік змінних з підтримкою вертикальних рівнів ----
                LEVEL_DIM_CANDIDATES = ("isobaricInhPa", "isobaric_in_hPa", "level", "lev", "plev", "pressure", "pressure_level")

                for var in ds.data_vars:
                    da = ds[var]
                    if da.ndim < 3:
                        continue  # нам потрібні щонайменше time/lat/lon

                    dims = list(da.dims)

                    # шукаємо назву координати рівня серед типових для ERA5/ECMWF
                    level_dim = next((d for d in LEVEL_DIM_CANDIDATES if d in dims), None)

                    if level_dim is None:
                        # звичайна 3D-змінна без рівнів — як і раніше
                        var_file_map[var] = nc_path
                        continue

                    # якщо є рівні — додаємо по опції на кожен рівень: var_1000, var_975, ...
                    try:
                        level_vals = ds[level_dim].values
                    except Exception:
                        level_vals = []

                    for lv in level_vals:
                        try:
                            # за замовчуванням показуємо цілими hPa (1000, 975, 950 …)
                            lv_int = int(round(float(lv)))
                            label = f"{var}_{lv_int}"
                            var_file_map[label] = nc_path
                        except Exception:
                            # якщо рівень нечисловий — пропускаємо конкретне значення
                            continue

            finally:
                ds.close()
        except Exception as e:
            self.status_text = f"Failed to open dataset: {e}"
            self.alert.object = self.status_text
            return

        # Update Time/Spatial information block
        self._update_info_lines(self.env_info, {
            "Time range:": time_text,
            "Spatial range:": spatial_text
        })
        self._auto_height(self.env_info)

        # Variable options
        if not var_file_map:
            self.env_data_multiselect.options = []
            self.status_text = "No 3D variables (e.g. time/lat/lon) found in the file."
            self.alert.object = self.status_text
            return

        self.env_variable_sources = var_file_map
        self.env_data_multiselect.options = list(var_file_map.keys())
        self.status_text = f"Loaded {len(var_file_map)} variable(s) from 1 file."
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
            self.boundary_path = path
            self.boundary_info_str.object = (
                f"Boundary file: {Path(path).name} <br>"
                f"Spatial range: lat[{S:.3f}..{N:.3f}], lon[{W:.3f}..{E:.3f}]"
            )
            self.status_text = (
                f"Boundary loaded from {Path(path).name}: "
                f"lat[{S:.3f}..{N:.3f}], lon[{W:.3f}..{E:.3f}]"
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

        df, taxa, ids, err = load_taxa_and_ids_from_csv(file_path)
        if err:
            self.status_text = f"Error: {err}"
            self.alert.object = self.status_text
            return

        # normalize headings
        df.columns = [re.sub(r"[-._\s]+", "_", col.lower()) for col in df.columns]
        if "location_long" in df.columns and "location_lon" not in df.columns:
            df["location_lon"] = df["location_long"]
        self.df = df
        self.id_multiselect.options = ids
        self.id_multiselect.disabled = False
        self.taxon_multiselect.options = taxa
        self.taxon_multiselect.disabled = False
        self.status_text = f"Loaded {len(ids)} IDs and {len(taxa)} taxon names."
        cols = set(df.columns)
        # TIME
        time_col = next((c for c in ("timestamp","time","datetime","date") if c in cols), None)
        ts = pd.to_datetime(df[time_col], errors="coerce") if time_col else None
        time_text = "-"
        if ts is not None and ts.notna().any():
            tmin, tmax = ts.min(), ts.max()
            time_text = f"Time range: {tmin:%Y-%m-%d %H:%M:%S} — {tmax:%Y-%m-%d %H:%M:%S}"

        # SPATIAL
        lat_col = next((c for c in ("location_lat","latitude","lat","y") if c in cols), None)
        lon_col = next((c for c in ("location_lon","longitude","lon","x") if c in cols), None)
        spatial_text = "-"
        if lat_col and lon_col:
            lat = pd.to_numeric(df[lat_col], errors="coerce")
            lon = pd.to_numeric(df[lon_col], errors="coerce")
            if lat.notna().any() and lon.notna().any():
                spatial_text = (f"Spatial range: "
                                f"lat[{float(lat.min()):.3f}..{float(lat.max()):.3f}], "
                                f"lon[{float(lon.min()):.3f}..{float(lon.max()):.3f}]")

        lines = (self.movement_info.object or
                "File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: - <br>").split("<br>")
        for i, line in enumerate(lines):
            if line.startswith("Time range:"):
                lines[i] = time_text
            if line.startswith("Spatial range:"):
                lines[i] = spatial_text
        self.movement_info.object = "<br>".join(lines)

        self.alert.object = self.status_text
        self._sync_nc_column_heights()


    @try_catch("Error during annotation")
    def run_annotation(self, *events):
        self.status_text = "Running annotation..."
        self.alert.object = self.status_text
        try:
            env_coord_names = self._get_env_coord_names_from_ui()
            selected_vars = self.env_data_multiselect.value
            selected_ids = self.id_multiselect.value
            env_var_map = getattr(self, "env_variable_sources", {})
            movebank_path = self.movement_data_selector.value
            boundary_path = getattr(self, "boundary_path", None)
            interpolation_method = self.interpolation_method.value
            smoothing_points = int(self.control_smoothing.value)

            if not selected_vars:
                self.status_text = "No environmental variables selected."
            elif not selected_ids:
                self.status_text = "No individual IDs selected."
            elif not movebank_path:
                self.status_text = "No Movebank data file selected."
            else:
                bbox = None
                if not boundary_path:
                    # building boundaries with .nc
                    first_var = selected_vars[0]
                    nc_path = env_var_map.get(first_var)
                    if not nc_path:
                        self.status_text = "Cannot derive boundary: missing .nc path for selected variable."
                        self.alert.object = self.status_text
                        return

                    # Only attempt lat/lon bbox when we are in Geographic mode
                    if self.env_spatial_mode.value == "Geographic (lat/lon)":
                        try:
                            bounds = get_nc_bounds(nc_path, env_coord_names=env_coord_names)
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
                        # Projected mode: don't attempt lat/lon bbox
                        self.boundary_info_str.object = (
                            "Boundary file: not selected <br>"
                            "Spatial range: using projected grid extent (x/y); bbox cropping disabled."
                        )

                self.status_text = "Annotation started."
                # pass bbox (or None, if the user did choose shp)
                start_annotation_process(
                    env_var_map, selected_vars, movebank_path, selected_ids,
                    boundary_path, interpolation_method, bbox=bbox, smoothing_k=smoothing_points,
                    out_csv_path=self.output_path.value, env_coord_names=env_coord_names
                )
                self.status_text = "Annotation finished."

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
        2) Ensure a Movebank CSV is already selected (used to decide output dir).
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
        # --- 0) Initial UI/status ----------------------------------------------------
        self.status_text = "Loading TIF environmental data..."
        self.alert.object = self.status_text

        # --- 1) Validate a sample TIF and collect folder -----------------------------
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

        # --- 2) Ensure Movebank CSV is loaded (for placing the output NetCDF nearby) -
        movebank_path = getattr(self.tif_movement_data_selector, "value", None)
        if not movebank_path or not Path(str(movebank_path)).is_file():
            self.status_text = "Please load Movebank data before environmental data."
            self.alert.object = self.status_text
            return

        output_dir = str(Path(str(movebank_path)).parent)

        # --- 3) Convert TIF stack → NetCDF ------------------------------------------
        try:
            nc_path = convert_tif_to_nc_before_annotation(tif_files, output_dir)
        except Exception as e:
            self.status_text = f"Failed to convert TIF to NetCDF: {e}"
            self.alert.object = self.status_text
            return

        # Cache for later (bbox fallback, re-open, etc.)
        self.tif_nc_path = nc_path

        # --- 4) Inspect NetCDF and keep ONLY 3D variables with a time dimension ------
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
            lon_name = next((c for c in ("lon", "longitude", "x","long") if c in ds.coords or c in ds.variables), None)
            if lat_name and lon_name:
                try:
                    lat_min = float(ds[lat_name].min())
                    lat_max = float(ds[lat_name].max())
                    lon_min = float(ds[lon_name].min())
                    lon_max = float(ds[lon_name].max())
                    spatial_text = (
                        f"Spatial range: lat[{lat_min:.3f}..{lat_max:.3f}], "
                        f"lon[{lon_min:.3f}..{lon_max:.3f}]"
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

        # --- 5) Update UI: info panel, multiselect, status ---------------------------
        # Info panel (use common helper to insert/replace rows)
        self._update_info_lines(self.tif_env_info, {
            "File:": Path(nc_path).name,
            "Time range:": time_text.replace("Time range: ", ""),
            "Spatial range:": spatial_text.replace("Spatial range: ", "")
        })

        if not var_file_map:
            # No valid 3D variables (time/lat/lon) found
            self.tif_env_var_map = {}
            self.tif_env_data_multiselect.options = []
            self.tif_env_data_multiselect.value = []
            self.status_text = "No 3D (time/lat/lon) variables found in the generated NetCDF."
            self.alert.object = self.status_text
            return

        # Store already filtered variables for later use in run_annotation_tif()
        self.tif_env_var_map = var_file_map

        # Options for the multiselect and a default value
        self.tif_env_data_multiselect.options = var_names
        if not self.tif_env_data_multiselect.value:
            self.tif_env_data_multiselect.value = var_names[:1]

        # Final status
        self.status_text = (
            f"Converted {len(tif_files)} TIF files to NetCDF. "
            f"Variables (3D/time): {', '.join(var_names)}"
        )
        self.alert.object = self.status_text


    @try_catch("Error running TIF annotation")
    def run_annotation_tif(self, *events):
        """
        Run annotation workflow for environmental data sourced from AppEEARS GeoTIFFs.

        Steps:
        1) Validate user selections (Movebank CSV, a sample TIF in the target folder, optional boundary).
        2) Gather all *.tif files from the selected folder.
        3) Convert the TIF stack to a single NetCDF via `convert_tif_to_nc_before_annotation`
            (this function produces a Dataset with one DataArray per parsed variable).
        4) Read actual variable names from the produced NetCDF and construct `env_var_map`
            as {var_name: nc_path}.
        5) Determine which variables to annotate (from the multiselect; default to the first one).
        6) Call `start_annotation_process(...)` with the resolved parameters.

        Notes:
        - This function assumes that `convert_tif_to_nc_before_annotation`, `safe_open_nc_with_time_decoding`,
            and `start_annotation_process` are already imported.
        - It also assumes UI widgets exist on the instance:
            * self.tif_movement_data_selector (file path to Movebank CSV)
            * self.tif_env_data_selector (a sample TIF inside the desired folder)
            * self.tif_env_data_multiselect (variable picker)
            * self.id_multiselect or self.tif_id_multiselect (optional animal IDs)
            * self.tif_bound_data_selector or self.bound_data_selector (optional boundary file)
            * self.tif_interpolation_method or self.interpolation_method (method name)
            * self.tif_output_path or self.output_path (optional output CSV path)
        - Status messages are written to `self.status_text` and mirrored in `self.alert.object`.
        """
        self.status_text = "Starting annotation (TIF)…"
        self.alert.object = self.status_text

        # --- 0) Validate inputs ---
        # Movebank CSV (required)
        movebank_path = getattr(self.tif_movement_data_selector, "value", None)
        if not movebank_path or not Path(str(movebank_path)).is_file():
            self.status_text = "Please load Movebank data before environmental data."
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
        id_widget = getattr(self, "tif_id_multiselect", None)# or getattr(self, "id_multiselect", None)
        selected_ids = list(getattr(id_widget, "value", [])) if id_widget else []
        if not selected_ids:
            # Not critical—downstream may annotate all IDs or handle empty list.
            print("[WARN] No IDs selected; proceeding without explicit ID filtering.")

        # Optional boundary
        bound_widget = getattr(self, "tif_bound_data_selector", None)# or getattr(self, "bound_data_selector", None)
        boundary_path = getattr(bound_widget, "value", None)
        if boundary_path and not Path(boundary_path).is_file():
            print(f"[WARN] Boundary file not found: {boundary_path}. Proceeding without boundary.")
            boundary_path = None

        # Interpolation and time-fit options (prefer TIF-tab widgets; fallback to NC-tab)
        interp_widget = getattr(self, "tif_interpolation_method", None)
        interp_method = getattr(interp_widget, "value", "Nearest neighbor (time-linear)")

        # Output CSV path (optional)
        out_widget = getattr(self, "tif_output_path", None)
        output_csv_path = getattr(out_widget, "value", None)

        # --- 1) Collect TIFs from the selected folder -----------------------------
        folder_path = Path(tif_sample).parent
        tif_paths = sorted(p for p in folder_path.glob("*.tif") if p.is_file())
        if not tif_paths:
            self.status_text = f"No .tif files found in: {folder_path}"
            self.alert.object = self.status_text
            return

        # --- 2) Convert TIF → NetCDF (multi-variable) -----------------------------
        output_dir = str(Path(movebank_path).parent)
        nc_path = convert_tif_to_nc_before_annotation([str(p) for p in tif_paths], output_dir)
        self.tif_nc_path = nc_path  # cache for later use

        # --- 3) Read variables from NetCDF and build env_var_map ------------------
        # Prefer already-filtered map from load_env_data_tif (only 3D with 'time')
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

        # --- 4) Which variables to annotate? --------------------------------------
        ms_widget = getattr(self, "tif_env_data_multiselect", None)
        selected_vars = list(getattr(ms_widget, "value", [])) if ms_widget else []
        if not selected_vars:
            selected_vars = var_names[:1]  # default to the first variable
            if ms_widget:
                ms_widget.value = selected_vars  # sync UI state

        # --- 5) Kick off annotation ------------------------------------------------
        self.status_text = (
            f"Annotating variables: {', '.join(selected_vars)} | "
            f"IDs: {len(selected_ids) if selected_ids else 'all/unspecified'} | "
            f"Interpolation: {interp_method}"
        )
        self.alert.object = self.status_text

        try:
            start_loading_spinner()
        except Exception:
            pass

        try:
            # Auto-bbox from .nc if no boundary file selected
            bbox = None
            if not boundary_path:
                try:
                    bounds = get_nc_bounds(self.tif_nc_path)  # {"S","N","W","E"}
                    bbox = bounds
                    self.tif_boundary_info_str.object = (
                        "Boundary file: not selected (auto from .nc) <br>"
                        f"Spatial range: lat[{bounds['S']:.3f}..{bounds['N']:.3f}], "
                        f"lon[{bounds['W']:.3f}..{bounds['E']:.3f}]"
                    )
                except Exception:
                    pass
            start_annotation_process(
                env_var_map=env_var_map,
                selected_env_vars=selected_vars,
                movebank_path=str(movebank_path),
                selected_ids=selected_ids,
                boundary_path=str(boundary_path) if boundary_path else None,
                interpolation_method=interp_method,
                bbox=bbox,
                smoothing_k=int(self.tif_control_smoothing.value),
                out_csv_path=output_csv_path
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
            self.boundary_path = path
            self.tif_boundary_info_str.object = (
                f"Boundary file: {Path(path).name} <br>"
                f"Spatial range: lat[{S:.3f}..{N:.3f}], lon[{W:.3f}..{E:.3f}]"
            )
            self.status_text = (
                f"TIF Boundary loaded: "
                f"lat[{S:.3f}..{N:.3f}], lon[{W:.3f}..{E:.3f}]"
            )
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

        df, taxa, ids, err = load_taxa_and_ids_from_csv(file_path)
        if err:
            self.status_text = f"Error: {err}"
        else:
            df.columns = [re.sub(r"[-._\s]+", "_", col.lower()) for col in df.columns]
            if "location_long" in df.columns and "location_lon" not in df.columns:
                df["location_lon"] = df["location_long"]
            self.df = df  # shared for both tabs
            self.tif_id_multiselect.options = ids
            self.tif_id_multiselect.disabled = False
            self.tif_taxon_multiselect.options = taxa
            self.tif_taxon_multiselect.disabled = False
            self.status_text = f"TIF: Loaded {len(ids)} IDs and {len(taxa)} taxon names."
            mv_current = self.tif_movement_info.object or "File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: -"
            lines = mv_current.split("<br>")
            if lines:
                lines[0] = f"File: {Path(file_path).name}"

            #
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
                    lat_min, lat_max = float(lat.min()), float(lat.max())
                    lon_min, lon_max = float(lon.min()), float(lon.max())
                    for i, line in enumerate(lines):
                        if line.startswith("Spatial range:"):
                            lines[i] = f"Spatial range: lat[{lat_min:.3f}..{lat_max:.3f}], lon[{lon_min:.3f}..{lon_max:.3f}]"

            except Exception:
                pass

            self.tif_movement_info.object = "<br>".join(lines)
        self.alert.object = self.status_text


    @try_catch("Interpolation (missing only) failed")
    def run_interpolate_missing_only(self, *events):
        # 1) input
        csv_path = Path(self.local_ID_file.value)
        if not csv_path.exists():
            self.status_text = "No file selected."
            self.alert.object = self.status_text
            return

        # 2) Determine the ID: if the user did not choose, we take all
        if self.df is None:
            try:
                df_tmp = pd.read_csv(csv_path)
                df_tmp.columns = [re.sub(r"[-._:\s]+", "_", c.lower()) for c in df_tmp.columns]
            except Exception as e:
                self.status_text = f"Failed to read CSV: {e}"
                self.alert.object = self.status_text
                return
            all_ids = sorted(df_tmp.get("individual_local_identifier", pd.Series([], dtype=str)).dropna().astype(str).unique())
        else:
            all_ids = sorted(self.df.get("individual_local_identifier", pd.Series([], dtype=str)).dropna().astype(str).unique())

        selected_ids = list(self.individual_ID.value) if self.individual_ID.value else all_ids
        if not selected_ids:
            self.status_text = "No IDs to process."
            self.alert.object = self.status_text
            return

        # 3) Time range
        start_time, end_time = self.time_selection_ID.value
        start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S.%f")
        end_time_str   = end_time.strftime("%Y-%m-%d %H:%M:%S.%f")

        # 4) Which columns to interpolate: taken from your validating function
        columns = validate_and_process_csv(csv_path)

        # 5) Call simplified interpolation
        # if you replaced check_missing_values_only -> it now interpolates,
        # otherwise import interpolate_missing_values_only and call it here.
        out_template = self.out_csv_name.value
        created = interpolate_missing_values_only(
            start_time_str, end_time_str, csv_path, selected_ids, columns, out_template
        )
        # or:
        # created = interpolate_missing_values_only(...)

        # 6) result
        if created:
            self.status_text = f"Interpolation complete. Files: {len(created)}. Example: {created[0]}"
        else:
            self.status_text = "Interpolation complete. No files created (no eligible gaps ≤ 1 day)."
        self.alert.object = self.status_text


    def update_annotation_ids_by_taxon_tif(self, event):
        if self.df is None:
            return

        selected_taxa = event.new
        if not selected_taxa:
            ids = sorted(self.df["individual_local_identifier"].dropna().astype(str).unique())
        else:
            filtered = self.df[self.df["individual_taxon_canonical_name"].isin(selected_taxa)]
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
            body,
            title=title,
            collapsible=False,
            margin=(0, 0, 10, 0),
            sizing_mode="stretch_width",
            height=height,
        )


    def _auto_height(self, pane, line_px=22, padding=8):
        lines = [l for l in (pane.object or "").split("<br>") if l.strip()]
        pane.height = line_px * max(1, len(lines)) + padding


    def _update_smoothing_options(self, event):
        """Updates options for control_smoothing depending on interpolation method (.nc)."""
        if event.new.startswith("Nearest neighbor"):
            self.control_smoothing.options = ["1"]
            self.control_smoothing.value = "1"
        else:
            self.control_smoothing.options = ["2", "4", "6", "8"]
            if self.control_smoothing.value == "1":
                self.control_smoothing.value = "4"


    def _update_smoothing_options_tif(self, event):
        """Updates options for control_smoothing depending on interpolation method(.tif)."""
        if event.new.startswith("Nearest neighbor"):
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
        third  = getattr(self, "_nc_col3", None)
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
        """
        Resets boundary to default: no file selected, range = environment boundary (.nc).
        Also clears self.boundary_path so annotation goes back to 'auto from .nc' mode.
        """
        self.boundary_path = None
        default_nc = "Boundary file: not selected <br>Spatial range: = environment data boundary"
        default_tif = "Boundary file: not selected <br> Spatial range: = environment data boundary"
        try:
            self.boundary_info_str.object = default_nc
        except Exception:
            pass
        try:
            self.tif_boundary_info_str.object = default_tif
        except Exception:
            pass

        self.status_text = "Boundary reset to default (auto from .nc)."
        self.alert.object = self.status_text
        self._sync_nc_column_heights()

    def _populate_env_coord_dropdowns(self, ds: xr.Dataset, coord_guess: dict) -> None:
        """
        Populate environmental coordinate dropdown menus from a dataset and autodetection.

        Parameters
        ----------
        ds : xarray.Dataset
            Environmental dataset opened for metadata inspection.
        coord_guess : dict
            Output from `detect_env_coord_names(ds)`. Expected keys:
            'env_time', 'env_x', 'env_y', 'env_lat', 'env_lon'. Values may be None.

        Returns
        -------
        None
            Updates UI widgets in-place.
        """
        # Options: include both coords and variables (some datasets store coords as variables)
        coord_names = list(ds.coords.keys())
        var_names = list(ds.variables.keys())  # includes coords too, but that's fine
        options = sorted(set(coord_names) | set(var_names))

        # Helper to set widget options + default value safely
        def _set_select(widget: pn.widgets.Select, guess_value: str | None) -> None:
            widget.options = options
            if guess_value in options:
                widget.value = guess_value
            else:
                widget.value = widget.value if widget.value in options else None

        _set_select(self.env_time_select, coord_guess.get("env_time"))
        _set_select(self.env_lat_select,  coord_guess.get("env_lat"))
        _set_select(self.env_lon_select,  coord_guess.get("env_lon"))
        _set_select(self.env_x_select,    coord_guess.get("env_x"))
        _set_select(self.env_y_select,    coord_guess.get("env_y"))

    def _get_env_coord_names_from_ui(self) -> dict:
        mode = self.env_spatial_mode.value
        env_time = self.env_time_select.value

        if not env_time:
            raise ValueError("Select Env time coordinate.")

        if mode == "Geographic (lat/lon)":
            if not self.env_lat_select.value or not self.env_lon_select.value:
                raise ValueError("Select both Env latitude coordinate and Env longitude coordinate.")
            return {
                "env_time": env_time,
                "env_lat": self.env_lat_select.value,
                "env_lon": self.env_lon_select.value,
                "env_x": None,
                "env_y": None,
            }

        # Projected (x/y)
        if not self.env_x_select.value or not self.env_y_select.value:
            raise ValueError("Select both Env x coordinate and Env y coordinate.")

        return {
            "env_time": env_time,
            "env_lat": None,
            "env_lon": None,
            "env_x": self.env_x_select.value,
            "env_y": self.env_y_select.value,
        }


    def _apply_env_spatial_mode(self) -> None:
        mode = self.env_spatial_mode.value

        use_latlon = (mode == "Geographic (lat/lon)")

        self._latlon_widgets.visible = use_latlon
        self._xy_widgets.visible     = not use_latlon


@register_view()
def view():
    viewer = movebank_annotation_engine()
    template = DEFAULT_TEMPLATE(main=[viewer.alert, viewer.view])
    return template

if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})

if __name__.startswith("bokeh"):
    view()