import logging
from pathlib import Path
import panel as pn
import param
import pandas as pd
from panel.io.loading import start_loading_spinner, stop_loading_spinner
from ecodata.app.models import FileSelector
from ecodata.panel_utils import param_widget, register_view, try_catch, rename_param_widgets
from ecodata.app.config import DEFAULT_TEMPLATE
from datetime import datetime
import re
from ecodata import validate_and_process_csv, load_vector_extent_info, load_taxa_and_ids_from_csv 
from ecodata.movebank_functions import merge_csv_files_from_folder, generate_individual_csvs_for_local_ids, interpolate_missing_values_only, delete_files 
from ecodata.annotation_eng_func import start_annotation_process,convert_tif_to_nc_before_annotation, get_nc_bounds, safe_open_nc_with_time_decoding

logger = logging.getLogger(__file__)

class movebank_annotation_engine(param.Parameterized):
    local_ID_file = param_widget(FileSelector(constrain_path=False, expanded=True, size=10))
    load_data_button = param_widget(pn.widgets.Button(name="Load data", button_type="primary"))
    taxon_name_val = param_widget(
        pn.widgets.MultiSelect(name="Taxon name (use Ctrl or ⌘ for multiple selection)", options=[], height = 140, disabled=True)
    )
    individual_ID = param_widget(
        pn.widgets.MultiSelect(name="Individual ID (use Ctrl or ⌘ for multiple selection)", options=[], height = 140, disabled=True)
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
    nc_time_var = pn.widgets.Select(name="Time variable", options=[], value=None)
    nc_lat_var  = pn.widgets.Select(name="Latitude variable", options=[], value=None)
    nc_lon_var  = pn.widgets.Select(name="Longitude variable", options=[], value=None)
    env_continuous_selector = pn.widgets.MultiSelect(
    name="Continuous (use Ctrl or ⌘ for multiple selection)",
    options=[], value=[], height=180
    )

    env_categorical_selector = pn.widgets.MultiSelect(
        name="Categorical (use Ctrl or ⌘ for multiple selection)",
        options=[], value=[], height=180
    )

    taxon_multiselect = pn.widgets.MultiSelect(name="Select Taxon (use Ctrl or ⌘ for multiple)", height = 140)
    id_multiselect = pn.widgets.MultiSelect(name="Select ID (use Ctrl or ⌘ for multiple)", height = 140)
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
        options=["Nearest neighbor (time-linear)", "Inverse Distance Weighting (time-linear)"],
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
    tif_env_data_multiselect = pn.widgets.MultiSelect(name="Environmental variables (use Ctrl or ⌘ for multiple)", options=[], height = 140)
    # TIF variable type: continuous vs categorical
    tif_continuous_vars = pn.widgets.MultiSelect(name="Continuous variables (use Ctrl or ⌘ for multiple)", options=[], value=[], size=8)
    tif_categorical_vars = pn.widgets.MultiSelect(name="Categorical/QC variables (use Ctrl or ⌘ for multiple)", options=[], value=[], size=8)
    # prevent recursive watcher updates
    _syncing_tif_var_types = False
    tif_taxon_multiselect = pn.widgets.MultiSelect(name="Select Taxon (use Ctrl or ⌘ for multiple)", height = 140)
    tif_id_multiselect = pn.widgets.MultiSelect(name="Select ID (use Ctrl or ⌘ for multiple)", height = 140)
    tif_env_info = pn.pane.HTML("File: not selected <br>Environment parameters: - <br>Time range: - <br>Spatial range: - <br>",
                            sizing_mode="stretch_width")
    tif_movement_info = pn.pane.HTML("File: not selected <br>Taxons: - <br>IDs: - <br>Time range: - <br>Spatial range: - <br>",
                                 sizing_mode="stretch_width")
    tif_output_path = pn.widgets.TextInput(name="Output path", value=str(Path.home() / "Downloads" / "annotated_env_tif.csv"))
    tif_boundary_info_str = pn.pane.HTML(
        "Boundary file: not selected <br> Spatial range: = environment data boundary",
        sizing_mode="stretch_width"
    )
    # --- TIF scaling (optional) ---
    tif_apply_scale = pn.widgets.Checkbox(name="Apply scale factor / offset", value=False)
    tif_scale_factor = pn.widgets.FloatInput(
        name="Scale factor", value=1.0, step=0.0001, start=None, disabled=True
    )
    tif_add_offset = pn.widgets.FloatInput(
        name="Add offset", value=0.0, step=0.1, start=None, disabled=True
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
        self._wire_env_split_guards()
        self._apply_env_selector_labels()
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
                "load_movement_button", "env_continuous_selector", "env_categorical_selector",
                "taxon_multiselect",  "id_multiselect",
                "boundary_info_str", "interpolation_method",
                "control_smoothing", 
                "env_info", "movement_info" ,"output_path",
                "make_annotation_button",
                 "nc_time_var", "nc_lat_var","nc_lon_var",
                # === TIF Annotation tab ===
                "tif_env_data_selector",
                "tif_movement_data_selector",
                "tif_bound_data_selector", "tif_reset_bound_button",
                "tif_env_data_multiselect",
                "tif_continuous_vars", "tif_categorical_vars",
                "tif_taxon_multiselect",
                "tif_id_multiselect",
                "tif_interpolation_method", "tif_control_smoothing",
                "tif_apply_scale", "tif_scale_factor", "tif_add_offset",
                "tif_env_info", "tif_movement_info",
                "tif_make_annotation_button"
            ]
        )

        self.df = None
        self.alert = pn.pane.Markdown(self.status_text)
        NC_H = 1080 
        # === NC tab  ===
        self._nc_col1 = self._section(
            "1. Environmental data (.nc)",
            pn.Column(self.env_data_selector, sizing_mode="stretch_width"),
            self.load_env_button,
            self.env_continuous_selector,
            self.env_categorical_selector,
            self.env_info,
            self.nc_time_var, self.nc_lat_var, self.nc_lon_var,
            self.interpolation_method,
            self.control_smoothing,
            self.output_path,
            height=NC_H + 400,
        )
        self._nc_col2 = self._section(
            "2. Movebank data (.csv)",
            pn.Column(self.movement_data_selector, sizing_mode="stretch_width"),
            self.load_movement_button,
            self.taxon_multiselect,
            self.id_multiselect,
            self.movement_info,
            height=NC_H + 400,
        )
        self._nc_col3 = self._section(
            "3. Boundary data (.shp/.geojson)",
            pn.Column(self.bound_data_selector, sizing_mode="stretch_width"),
            pn.Row(self.load_bound_button, self.reset_bound_button),
            self.boundary_info_str,
            pn.layout.Divider(),
            pn.pane.Markdown("### 4. Start annotation"),
            self.make_annotation_button,
            height=NC_H + 400,
        )

        # synchronize heights after rendering
        pn.state.onload(self._sync_nc_column_heights)

        self.anotation_engine_tab = pn.Column(
            pn.pane.Markdown("### Annotation engine - .nc", sizing_mode="stretch_width"),
            pn.GridBox(
                self._nc_col1, self._nc_col2, self._nc_col3,
                ncols=3, sizing_mode="stretch_width",
                height=1400, 
                scroll=True,
            ),
        )

        # TIF
        TIF_H = 1500  
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

        self._tif_col2 = self._section(
            "2. Movebank data (.csv)",
            pn.Column(self.tif_movement_data_selector, sizing_mode="stretch_width"), 
            self.tif_load_movement_button,
            self.tif_taxon_multiselect,
            self.tif_id_multiselect,
            self.tif_movement_info,
            height=TIF_H,
        )

        self._tif_col3 = self._section(
            "3. Boundary data (.shp/.geojson)",
            pn.Column(self.tif_bound_data_selector, sizing_mode="stretch_width"), 
            pn.Row(self.tif_load_bound_button, self.tif_reset_bound_button),
            self.tif_boundary_info_str,
            pn.layout.Divider(),
            pn.pane.Markdown("### 4. Start annotation"),
            self.tif_make_annotation_button,
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
        self.env_continuous_selector.param.watch(lambda e: self.update_env_info_text(self._get_selected_env_vars()), "value")
        self.env_categorical_selector.param.watch(lambda e: self.update_env_info_text(self._get_selected_env_vars()), "value")
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
        self.tif_continuous_vars.param.watch(
            lambda e: self.update_env_info_text_tif(
                list(self.tif_continuous_vars.value or []) + [
                    v for v in list(self.tif_categorical_vars.value or [])
                    if v not in list(self.tif_continuous_vars.value or [])
                ]
            ),
            "value"
        )
        self.tif_categorical_vars.param.watch(
            lambda e: self.update_env_info_text_tif(
                list(self.tif_continuous_vars.value or []) + [
                    v for v in list(self.tif_categorical_vars.value or [])
                    if v not in list(self.tif_continuous_vars.value or [])
                ]
            ),
            "value"
        )
        self.tif_taxon_multiselect.param.watch(lambda e: self.update_movement_info_text_tif("Taxons", e.new), "value")
        self.tif_id_multiselect.param.watch(lambda e: self.update_movement_info_text_tif("IDs", e.new), "value")
        self.tif_interpolation_method.param.watch(self._update_smoothing_options_tif, 'value')
        self.tif_apply_scale.param.watch(self._update_tif_scale_widgets, "value")
        self._update_tif_scale_widgets()
        self.tif_continuous_vars.param.watch(self._sync_tif_variable_type_selection, "value")
        self.tif_categorical_vars.param.watch(self._sync_tif_variable_type_selection, "value")
        

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
    
    def _is_categorical_var(self, var_name: str, da) -> bool:
        """
        Heuristic classification:
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
        cat  = list(self.env_categorical_selector.value or [])

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
            lambda e: self._enforce_env_split_unique("cont", list(e.new or [])),
            "value"
        )
        self.env_categorical_selector.param.watch(
            lambda e: self._enforce_env_split_unique("cat", list(e.new or [])),
            "value"
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
        return ui_value  # fallback

    def _apply_env_selector_labels(self):
        """Make selector purposes obvious in UI."""
        self.env_continuous_selector.name = "Continuous (use Ctrl or ⌘ for multiple)"
        self.env_categorical_selector.name = "Categorical/QC (use Ctrl or ⌘ for multiple)"


    @try_catch("Error loading environmental data")
    def load_env_data(self, *events):
        """We select exactly one .nc, update File/Time/Spatial and the list of 3D variables."""
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

        ####################
        var_file_map: dict[str, str] = {}
        time_text = "-"
        spatial_text = "-"

        # Coordinate name candidates
        time_candidates = ["time", "Time", "datetime", "date", "valid_time"]
        lat_candidates  = ["lat", "latitude", "Latitude", "y"]
        lon_candidates  = ["lon", "longitude", "Longitude", "x"]

        try:
            ds = safe_open_nc_with_time_decoding(nc_path)

            all_vars = sorted(list(ds.variables.keys()))

            # Populate dropdowns
            self.nc_time_var.options = all_vars
            self.nc_lat_var.options  = all_vars
            self.nc_lon_var.options  = all_vars

            def pick_first(candidates):
                for c in candidates:
                    if c in all_vars:
                        return c
                return None

            # Preselect defaults (only if user hasn't selected yet)
            if not self.nc_time_var.value:
                self.nc_time_var.value = pick_first(time_candidates)
            if not self.nc_lat_var.value:
                self.nc_lat_var.value = pick_first(lat_candidates)
            if not self.nc_lon_var.value:
                self.nc_lon_var.value = pick_first(lon_candidates)

            # -------- TIME INFO --------
            time_name = self.nc_time_var.value
            if time_name and time_name in ds:
                tvals = pd.to_datetime(ds[time_name].values)
                time_text = f"{tvals.min().date()} — {tvals.max().date()}"

            # ------ SPATIAL INFO -------
            lat_name = self.nc_lat_var.value
            lon_name = self.nc_lon_var.value
            if lat_name in ds and lon_name in ds:
                lat_min = float(ds[lat_name].min())
                lat_max = float(ds[lat_name].max())
                lon_min = float(ds[lon_name].min())
                lon_max = float(ds[lon_name].max())
                spatial_text = f"lat[{lat_min:.3f}..{lat_max:.3f}], lon[{lon_min:.3f}..{lon_max:.3f}]"

        finally:
            ds.close()
        

        def _pick(cands):
            for c in cands:
                if c in all_vars:
                    return c
            return None

        # defalts
        self.nc_time_var.value = _pick(["time","Time","datetime","date","valid_time","forecast_time","verification_time"])
        self.nc_lat_var.value  = _pick(["lat","latitude","y"])
        self.nc_lon_var.value  = _pick(["lon","longitude","x","long"])

        try:
            ds = safe_open_nc_with_time_decoding(nc_path)
            try:
                # ---- TIME ----
                time_name = next((c for c in time_candidates if c in ds.coords or c in ds.variables), None)
                if time_name is not None:
                    tmin = pd.to_datetime(ds[time_name].values.min())
                    tmax = pd.to_datetime(ds[time_name].values.max())
                    time_text = f"{tmin.strftime('%Y-%m-%d')} — {tmax.strftime('%Y-%m-%d')}"

                # ---- SPATIAL ----
                lat_name = next((c for c in lat_candidates if c in ds.coords or c in ds.variables), None)
                lon_name = next((c for c in lon_candidates if c in ds.coords or c in ds.variables), None)
                if lat_name and lon_name:
                    lat_min = float(ds[lat_name].min())
                    lat_max = float(ds[lat_name].max())
                    lon_min = float(ds[lon_name].min())
                    lon_max = float(ds[lon_name].max())
                    spatial_text = f"lat[{lat_min:.3f}..{lat_max:.3f}], lon[{lon_min:.3f}..{lon_max:.3f}]"

                # List of variables with support for vertical levels 
                LEVEL_DIM_CANDIDATES = ("isobaricInhPa", "isobaric_in_hPa", "level", "lev", "plev", "pressure", "pressure_level")

                for var in ds.data_vars:
                    da = ds[var]
                    if da.ndim < 3:
                        continue

                    dims = list(da.dims)

                    level_dim = next((d for d in LEVEL_DIM_CANDIDATES if d in dims), None)

                    if level_dim is None:
                        var_file_map[var] = nc_path
                        continue

                    # options for each level: var_1000, var_975, ...
                    try:
                        level_vals = ds[level_dim].values
                    except Exception:
                        level_vals = []

                    for lv in level_vals:
                        try:
                            # default - hPa (1000, 975, 950 …)
                            lv_int = int(round(float(lv)))
                            label = f"{var}_{lv_int}"
                            var_file_map[label] = nc_path
                        except Exception:
                            # skip if non-numeric
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
            self.env_continuous_selector.options = []
            self.env_categorical_selector.options = []
            self.env_continuous_selector.value = []
            self.env_categorical_selector.value = []
            self.status_text = "No 3D variables (e.g. time/lat/lon) found in the file."
            self.alert.object = self.status_text
            return

        # Store map label -> nc_path
        self.env_variable_sources = var_file_map

        # labels : continuous vs categorical
        all_labels = list(var_file_map.keys())
        # both selectors get ALL variables in options
        self.env_continuous_selector.options = all_labels
        self.env_categorical_selector.options = all_labels
        # reset selections
        self.env_continuous_selector.value = []
        self.env_categorical_selector.value = []
        self.status_text = f"Loaded {len(all_labels)} variable(s). Now split them into Continuous vs Categorical/QC."
        self.alert.object = self.status_text
        self._sync_nc_column_heights()


        self.status_text = f"Loaded {len(var_file_map)} variable(s)."
        self.alert.object = self.status_text
        self._sync_nc_column_heights()
        ####

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

    def _get_selected_env_vars(self):
        cont = list(getattr(self.env_continuous_selector, "value", []) or [])
        cat  = list(getattr(self.env_categorical_selector, "value", []) or [])
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
            boundary_path = getattr(self, "boundary_path", None)
            interpolation_method = self._normalize_interp_key(self.interpolation_method.value)
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

                    try:
                        bounds = get_nc_bounds(nc_path)  # {"S":..., "N":..., "W":..., "E":...}
                        bbox = bounds
                        # Updating the border information panel
                        self.boundary_info_str.object = (
                            "Boundary file: not selected (auto from .nc) <br>"
                            f"Spatial range: lat[{bounds['S']:.3f}..{bounds['N']:.3f}], "
                            f"lon[{bounds['W']:.3f}..{bounds['E']:.3f}]"
                        )
                    except Exception as e:
                        self.status_text = f"Failed to derive boundary from .nc: {e}"
                        self.alert.object = self.status_text
                        return

                self.status_text = "Annotation started."
                # pass bbox (or None, if the user did choose shp)
                coord_spec = {
                    "time": self.nc_time_var.value,
                    "lat":  self.nc_lat_var.value,
                    "lon":  self.nc_lon_var.value,
                }
                if not (self.nc_time_var.value and self.nc_lat_var.value and self.nc_lon_var.value):
                    self.env_info.object = "Please select Time, Latitude and Longitude variables from the NetCDF file."
                    return

                start_annotation_process(
                    env_var_map, selected_vars, movebank_path, selected_ids,
                    boundary_path, interpolation_method, bbox=bbox, smoothing_k=smoothing_points,
                    out_csv_path=self.output_path.value, coord_spec=coord_spec,
                    continuous_vars=continuous_vars,
                    categorical_vars=categorical_vars
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

        #  5) Update UI: info panel, multiselect, status 
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
        selected_for_info = continuous_guess + [
            v for v in categorical_guess
            if v not in continuous_guess
        ]
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

        Current TIF workflow:
        1) Validate user selections:
        - Movebank CSV is required.
        - A sample .tif file is required to identify the target TIF folder.
        - Boundary file is optional; if it is not provided, the NetCDF extent is used.

        2) Gather all *.tif files from the selected TIF folder.

        3) Convert the TIF stack to a temporary NetCDF via
        `convert_tif_to_nc_before_annotation(...)`.

        Important:
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
            Movebank CSV path.
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
        # Movebank CSV (required)
        movebank_path = getattr(self.tif_movement_data_selector, "value", None)
        if not movebank_path or not Path(str(movebank_path)).is_file():
            self.status_text = "Please load Movebank data before running TIF annotation."
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
            self.status_text = "Please select at least one individual ID before running TIF annotation."
            self.alert.object = self.status_text
            return

        # Optional boundary
        bound_widget = getattr(self, "tif_bound_data_selector", None)# or getattr(self, "bound_data_selector", None)
        boundary_path = getattr(bound_widget, "value", None)
        if boundary_path and not Path(boundary_path).is_file():
            print(f"[WARN] Boundary file not found: {boundary_path}. Proceeding without boundary.")
            boundary_path = None

        # Interpolation and time-fit options (prefer TIF-tab widgets; fallback to NC-tab)
        interp_widget = getattr(self, "tif_interpolation_method", None)
        #??? interp_method = getattr(interp_widget, "value", "Nearest neighbor (time-linear)")
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

        # 2) Convert TIF → NetCDF (multi-variable, raw values only)
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

        selected_vars = continuous_vars + [
            v for v in categorical_vars
            if v not in continuous_vars
        ]

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
                out_csv_path=output_csv_path,
                continuous_vars=continuous_vars,
                categorical_vars=categorical_vars,
                # TIF value correction is applied after sampling,
                # and only to continuous variables.
                apply_value_correction=bool(self.tif_apply_scale.value),
                value_scale_factor=float(self.tif_scale_factor.value),
                value_add_offset=float(self.tif_add_offset.value),
                value_correction_vars=continuous_vars,
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

        # 2) Determine the ID: if the user did not choose, take all
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

        # 4) Which columns to interpolate
        columns = validate_and_process_csv(csv_path)

        # 5) Call simplified interpolation
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

        categorical = [
            v for v in variables
            if any(key in str(v).lower() for key in categorical_keywords)
        ]

        continuous = [
            v for v in variables
            if v not in categorical
        ]

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
                    v for v in (self.tif_categorical_vars.value or [])
                    if v not in overlap
                ]

            # If the user changed Categorical/QC, remove overlap from Continuous.
            elif event is not None and event.obj is self.tif_categorical_vars:
                self.tif_continuous_vars.value = [
                    v for v in (self.tif_continuous_vars.value or [])
                    if v not in overlap
                ]

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
        key = self._normalize_interp_key(event.new)
        if key == "nearest":
            self.control_smoothing.options = ["1"]
            self.control_smoothing.value = "1"
        else:
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


@register_view()
def view():
    viewer = movebank_annotation_engine()
    template = DEFAULT_TEMPLATE(main=[viewer.alert, viewer.view])
    return template

if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})

if __name__.startswith("bokeh"):
    view()