import logging
from pathlib import Path
from typing import Dict, List, Optional

import panel as pn
import pandas as pd

from ecodata.app.config import DEFAULT_TEMPLATE
from ecodata.app.models import FileSelector
from ecodata.panel_utils import register_view

logger = logging.getLogger(__file__)

BACKEND_IMPORT_ERROR = None

try:
    from ecodata.nc_builder_functions import (
        NCBuildConfig,
        build_standardized_netcdf,
        scan_netcdf_files,
        validate_build_config,
    )
except Exception as exc:
    BACKEND_IMPORT_ERROR = str(exc)
    NCBuildConfig = None
    build_standardized_netcdf = None
    scan_netcdf_files = None
    validate_build_config = None


class NCBuilder_App:
    """
    UI for building a standardized CF-style NetCDF file from multiple ERA5 or generic NetCDF files.
    """

    def __init__(self):
        self.name = "NetCDF Builder"
        self._scanned_files: List[Path] = []
        self._detected_time_min: Optional[pd.Timestamp] = None
        self._detected_time_max: Optional[pd.Timestamp] = None

        # 1. Input files
        self.input_folder = FileSelector(
            name="Input folder",
            constrain_path=False,
            expanded=True,
            size=10,
        )

        self.input_files = pn.widgets.MultiSelect(
            name="Select files from current folder",
            options={},
            value=[],
            size=12,
            sizing_mode="stretch_width",
        )

        self.combine_mode = pn.widgets.RadioButtonGroup(
            name="Combine mode",
            options=["By time", "By level", "By time and level"],
            value="By time and level",
            button_type="primary",
            sizing_mode="stretch_width",
        )

        # 2. Variable and coordinate mapping
        self.target_variable = pn.widgets.MultiSelect(
            name="Target variable(s)",
            options=[],
            value=[],
            size=8,
            sizing_mode="stretch_width",
        )
        self.time_variable = pn.widgets.Select(name="Time variable", options=[], value=None, sizing_mode="stretch_width")
        self.lat_variable = pn.widgets.Select(name="Latitude variable", options=[], value=None, sizing_mode="stretch_width")
        self.lon_variable = pn.widgets.Select(name="Longitude variable", options=[], value=None, sizing_mode="stretch_width")
        self.level_variable = pn.widgets.Select(name="Vertical / level variable", options=["None"], value="None", sizing_mode="stretch_width")

        self.output_variable_name = pn.widgets.TextInput(
            name="Output variable name",
            placeholder="Example: temperature",
            value="",
            sizing_mode="stretch_width",
        )
        self.output_level_coord_name = pn.widgets.TextInput(
            name="Output level coordinate name",
            value="level",
            sizing_mode="stretch_width",
        )
        self.level_units = pn.widgets.Select(
            name="Level units",
            options=["hPa", "m", "Pa", "model_level", "custom"],
            value="hPa",
            sizing_mode="stretch_width",
        )
        self.level_units_custom = pn.widgets.TextInput(
            name="Custom level units",
            placeholder="Example: sigma, hybrid_level, depth_m",
            value="",
            disabled=True,
            sizing_mode="stretch_width",
        )

        self.cf_note = pn.pane.Markdown(
            (
                "**Standard output coordinate names:** `time`, `lat`, `lon`, `level`  \n"
                "The backend writes basic CF-style metadata for coordinate attributes."
            ),
            sizing_mode="stretch_width",
        )

        # 3. Level detection
        self.level_source = pn.widgets.Select(
            name="Level source",
            options=["From NetCDF coordinate", "From filename", "Manual table"],
            value="From NetCDF coordinate",
            sizing_mode="stretch_width",
        )
        self.level_regex = pn.widgets.TextInput(
            name="Level regex",
            value=r"level(\d+)",
            placeholder=r"Example: level(\d+)",
            sizing_mode="stretch_width",
        )
        self.level_table_path = pn.widgets.TextInput(
            name="Level table file",
            placeholder="CSV with columns: name, level",
            value="",
            sizing_mode="stretch_width",
        )
        self.level_table_note = pn.pane.Markdown(
            (
                "**Manual level table format:** CSV with columns `name` and `level`.  \n"
                "`name` should match the input file name or a unique part of it."
            ),
            sizing_mode="stretch_width",
        )

        # 4. Time detection
        self.time_source = pn.widgets.Select(
            name="Time source",
            options=["From NetCDF time coordinate", "From filename", "Manual table"],
            value="From NetCDF time coordinate",
            sizing_mode="stretch_width",
        )
        self.time_regex = pn.widgets.TextInput(
            name="Time regex",
            value=r"(\d{8})",
            placeholder=r"Example: (\d{8}) for YYYYMMDD",
            sizing_mode="stretch_width",
        )
        self.time_format = pn.widgets.TextInput(
            name="Time format",
            value="%Y%m%d",
            placeholder="Example: %Y%m%d or %Y-%m-%d_%H",
            sizing_mode="stretch_width",
        )
        self.time_table_path = pn.widgets.TextInput(
            name="Time table file",
            placeholder="CSV with columns: name, DateTime",
            value="",
            sizing_mode="stretch_width",
        )
        self.time_table_note = pn.pane.Markdown(
            (
                "**Manual time table format:** CSV with columns `name` and `DateTime`.  \n"
                "`name` should match the input file name or a unique part of it.  \n"
                "`DateTime` should be parseable by pandas, e.g. `1994-01-01 00:00:00`."
            ),
            sizing_mode="stretch_width",
        )

        # 5. Spatial subset
        self.use_bbox = pn.widgets.Checkbox(name="Bounding box", value=False, sizing_mode="stretch_width")
        self.bbox_south = pn.widgets.FloatInput(name="South", value=None, step=0.25)
        self.bbox_north = pn.widgets.FloatInput(name="North", value=None, step=0.25)
        self.bbox_west = pn.widgets.FloatInput(name="West", value=None, step=0.25)
        self.bbox_east = pn.widgets.FloatInput(name="East", value=None, step=0.25)
        self.bbox_note = pn.pane.Markdown(
            "If the bounding box is not enabled, the original spatial extent is preserved.",
            sizing_mode="stretch_width",
        )

        # 6. Time subset
        self.detected_time_range = pn.pane.Markdown("**Detected time range:** not scanned yet", sizing_mode="stretch_width")
        self.start_time = pn.widgets.DatetimePicker(name="Start time", value=None, sizing_mode="stretch_width")
        self.end_time = pn.widgets.DatetimePicker(name="End time", value=None, sizing_mode="stretch_width")
        self.time_subset_note = pn.pane.Markdown(
            (
                "If input files do not contain a time coordinate, use **Time source = From filename** "
                "or **Manual table**. If no time information is provided, all files will be used."
            ),
            sizing_mode="stretch_width",
        )

        # 7. Output settings
        self.output_folder = pn.widgets.TextInput(
            name="Output folder",
            placeholder="Path to output folder",
            value=str(Path.home() / "Downloads"),
            sizing_mode="stretch_width",
        )
        self.output_filename = pn.widgets.TextInput(
            name="Output filename",
            value="era5_standardized_temperature.nc",
            sizing_mode="stretch_width",
        )
        self.output_mode = pn.widgets.Select(
            name="Output mode",
            options=["Single NetCDF file"],
            value="Single NetCDF file",
            sizing_mode="stretch_width",
        )
        self.use_dask_chunks = pn.widgets.Checkbox(name="Use chunking when reading", value=False, sizing_mode="stretch_width")
        self.chunking_mode = pn.widgets.Select(name="Chunking mode", options=["auto", "manual"], value="auto", sizing_mode="stretch_width")
        self.chunk_time = pn.widgets.IntInput(name="time chunk", value=24, start=1, step=1, disabled=True)
        self.chunk_level = pn.widgets.IntInput(name="level chunk", value=1, start=1, step=1, disabled=True)
        self.chunk_lat = pn.widgets.IntInput(name="lat chunk", value=200, start=1, step=10, disabled=True)
        self.chunk_lon = pn.widgets.IntInput(name="lon chunk", value=200, start=1, step=10, disabled=True)
        self.enable_compression = pn.widgets.Checkbox(name="Enable NetCDF compression", value=True, sizing_mode="stretch_width")

        # Preview / validation / log
        self.preview = pn.pane.Markdown(
            "### Preview\nNo files scanned yet.",
            sizing_mode="stretch_width",
            styles={"border": "1px solid #ddd", "padding": "10px", "border-radius": "6px"},
        )
        self.validation_panel = pn.pane.Markdown(
            "### Validation\nNot validated yet.",
            sizing_mode="stretch_width",
            styles={"border": "1px solid #ddd", "padding": "10px", "border-radius": "6px"},
        )
        self.log = pn.pane.Markdown(
            "### Log\nReady.",
            sizing_mode="stretch_width",
            styles={"border": "1px solid #ddd", "padding": "10px", "border-radius": "6px"},
        )

        # Buttons
        self.load_files_button = pn.widgets.Button(
            name="Load file list",
            button_type="primary",
            sizing_mode="stretch_width",
        )

        self.scan_variables_button = pn.widgets.Button(
            name="Scan variables",
            button_type="primary",
            sizing_mode="stretch_width",
        )
        self.validate_button = pn.widgets.Button(
            name="Validate",
            button_type="primary",
            sizing_mode="stretch_width",
        )

        self.build_button = pn.widgets.Button(
            name="Build standardized NetCDF",
            button_type="primary",
            sizing_mode="stretch_width",
        )

        self.load_files_button.on_click(self._on_load_file_list)
        self.scan_variables_button.on_click(self._on_scan_variables)
        self.validate_button.on_click(self._on_validate)
        self.build_button.on_click(self._on_build)
        self.target_variable.param.watch(self._on_target_variables_changed, "value")
        self.level_units.param.watch(self._update_widget_states, "value")
        self.level_source.param.watch(self._update_widget_states, "value")
        self.time_source.param.watch(self._update_widget_states, "value")
        self.use_bbox.param.watch(self._update_widget_states, "value")
        self.chunking_mode.param.watch(self._update_widget_states, "value")
        self.use_dask_chunks.param.watch(self._update_widget_states, "value")
        self.combine_mode.param.watch(self._update_widget_states, "value")
        self._update_widget_states()

    def _append_log(self, message: str) -> None:
        old = self.log.object or "### Log\n"
        if old.strip() == "### Log\nReady.":
            old = "### Log\n"
        self.log.object = old + f"\n- {message}"

    def _current_input_directory(self) -> Optional[Path]:
        """
        Return the input folder represented by the custom FileSelector.

        The custom selector is used only to define the folder.
        If the selector value is a file, NCBuilder uses its parent folder.
        The actual file list for scan/validate/build is controlled by self.input_files.
        """
        candidates = [
            getattr(self.input_folder, "value", None),
            getattr(self.input_folder, "directory", None),
        ]

        for raw_value in candidates:
            if not raw_value:
                continue

            if isinstance(raw_value, (list, tuple)):
                if not raw_value:
                    continue
                raw_value = raw_value[0]

            path = Path(str(raw_value)).expanduser()

            if path.exists() and path.is_file():
                return path.parent

            if path.exists() and path.is_dir():
                return path

        return None


    def _list_netcdf_files_in_selected_folder(self) -> List[Path]:
        """
        List supported NetCDF-like files in the current input folder.
        """
        folder = self._current_input_directory()
        if folder is None:
            return []

        extensions = {".nc", ".nc4", ".cdf", ".netcdf"}

        files = [
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in extensions
        ]

        return sorted(files, key=lambda p: p.name.lower())


    def _refresh_input_file_options(self) -> None:
        """
        Load all supported NetCDF files from the current FileSelector directory
        into the MultiSelect.

        This method controls what files are visible in the UI.
        It does not decide what files will be passed to the backend.
        """
        files = self._list_netcdf_files_in_selected_folder()

        options = {
            f.name: str(f)
            for f in files
            if f.exists() and f.is_file()
        }

        self.input_files.options = options

        # When a new folder is opened, select all detected files by default.
        # The user can then deselect files manually.
        self.input_files.value = list(options.values())


    def _on_load_file_list(self, event=None) -> None:
        """
        Load all supported NetCDF files from the current custom FileSelector folder
        into the MultiSelect.

        The custom FileSelector is used only to define the folder.
        The actual files passed to scan/validate/build are controlled by
        self.input_files.value.
        """
        self.log.object = "### Log\n"

        folder = self._current_input_directory()

        if folder is None:
            selector_value = getattr(self.input_folder, "value", None)
            selector_directory = getattr(self.input_folder, "directory", None)

            self.input_files.options = {}
            self.input_files.value = []

            self.preview.object = (
                "### Preview\n"
                "No valid input folder was detected from the custom selector.\n\n"
                f"- `FileSelector.value`: `{selector_value}`\n"
                f"- `FileSelector.directory`: `{selector_directory}`\n\n"
                "Open the target folder or click any file inside that folder, then press **Load file list**."
            )
            self._append_log("No valid input folder detected from FileSelector.")
            return

        self._refresh_input_file_options()

        n_files = len(self.input_files.options or {})

        self.preview.object = (
            "### Preview\n"
            f"- **Input folder:** `{folder}`\n"
            f"- **Files loaded into Select files from current folder:** {n_files}\n"
            "- Deselect files that should not be scanned or built."
        )

        if n_files == 0:
            self._append_log(
                f"No supported NetCDF files found in `{folder}`. "
                "Expected extensions: .nc, .nc4, .cdf, .netcdf."
            )
        else:
            self._append_log(f"Loaded {n_files} NetCDF file(s) from `{folder}`.")

    def _collect_input_files(self) -> List[Path]:
        """
        Collect only files explicitly selected in the MultiSelect.

        MultiSelect options may contain all files from the folder,
        but only MultiSelect value is passed to scan/validate/build.
        """
        selected_values = list(self.input_files.value or [])

        files: List[Path] = []

        for value in selected_values:
            path = Path(str(value)).expanduser()
            if path.exists() and path.is_file():
                files.append(path)

        unique_files: List[Path] = []
        seen = set()

        for f in files:
            key = str(f.resolve()) if f.exists() else str(f)
            if key not in seen:
                seen.add(key)
                unique_files.append(f)

        return unique_files
    
    def _sync_selected_files(self) -> List[Path]:
        """
        Synchronize backend file list with the current MultiSelect selection.
        """
        files = self._collect_input_files()
        self._scanned_files = [
            Path(f).expanduser()
            for f in files
            if Path(f).expanduser().exists()
        ]
        return self._scanned_files

    def _on_target_variables_changed(self, event=None) -> None:
        """
        Update output-name behaviour depending on single-variable or multi-variable mode.

        In multi-variable mode, source variable names are preserved, so the single
        output variable name field is disabled.
        """
        selected_targets = list(self.target_variable.value or [])

        if len(selected_targets) == 1:
            self.output_variable_name.disabled = False
            if not self.output_variable_name.value:
                self.output_variable_name.value = selected_targets[0]
        elif len(selected_targets) > 1:
            self.output_variable_name.value = ""
            self.output_variable_name.disabled = True
        else:
            self.output_variable_name.disabled = False

    def _manual_chunks_dict(self) -> Dict[str, int]:
        return {
            "time": int(self.chunk_time.value),
            "level": int(self.chunk_level.value),
            "lat": int(self.chunk_lat.value),
            "lon": int(self.chunk_lon.value),
        }

    def _update_widget_states(self, *_events) -> None:
        self.level_units_custom.disabled = self.level_units.value != "custom"

        self.level_variable.disabled = self.level_source.value != "From NetCDF coordinate"
        self.level_regex.disabled = self.level_source.value != "From filename"
        self.level_table_path.disabled = self.level_source.value != "Manual table"

        self.time_variable.disabled = self.time_source.value != "From NetCDF time coordinate"
        self.time_regex.disabled = self.time_source.value != "From filename"
        self.time_format.disabled = self.time_source.value != "From filename"
        self.time_table_path.disabled = self.time_source.value != "Manual table"

        bbox_disabled = not self.use_bbox.value
        for widget in (self.bbox_south, self.bbox_north, self.bbox_west, self.bbox_east):
            widget.disabled = bbox_disabled

        manual_chunks = self.use_dask_chunks.value and self.chunking_mode.value == "manual"
        self.chunking_mode.disabled = not self.use_dask_chunks.value
        for widget in (self.chunk_time, self.chunk_level, self.chunk_lat, self.chunk_lon):
            widget.disabled = not manual_chunks
        # In "By time" mode, the selected files already define the time range.
        # Avoid applying an additional pandas-based time subset, especially for
        # cftime calendars such as Julian/noleap/360_day.
        time_subset_disabled = self.combine_mode.value == "By time"

        self.start_time.disabled = time_subset_disabled
        self.end_time.disabled = time_subset_disabled

        if time_subset_disabled:
            self.time_subset_note.object = (
                "In **By time** mode, time subsetting is disabled. "
                "Select the required files in **Select files from current folder** instead. "
                "The detected time range is shown for information only."
            )
        else:
            self.time_subset_note.object = (
                "If input files do not contain a time coordinate, use **Time source = From filename** "
                "or **Manual table**. If no time information is provided, all files will be used."
            )
    def _make_bbox_config(self) -> Optional[Dict[str, float]]:
        if not self.use_bbox.value:
            return None
        return {
            "south": float(self.bbox_south.value),
            "north": float(self.bbox_north.value),
            "west": float(self.bbox_west.value),
            "east": float(self.bbox_east.value),
        }

    def _make_output_path(self) -> str:
        folder = Path(self.output_folder.value or ".").expanduser()
        filename = self.output_filename.value or "standardized_output.nc"
        return str(folder / filename)

    def _make_build_config(self):
        if NCBuildConfig is None:
            raise RuntimeError(f"NCBuilder backend functions are not available. Import error: {BACKEND_IMPORT_ERROR}")

        manual_chunks = None
        if self.use_dask_chunks.value and self.chunking_mode.value == "manual":
            manual_chunks = self._manual_chunks_dict()

        level_units = self.level_units_custom.value if self.level_units.value == "custom" else self.level_units.value

        level_variable = self.level_variable.value
        if level_variable == "None":
            level_variable = None
        target_variables = list(self.target_variable.value or [])
        target_variable = target_variables[0] if target_variables else None
        self._sync_selected_files()

        if self.combine_mode.value == "By time":
            start_time = None
            end_time = None
        else:
            start_time = str(self.start_time.value) if self.start_time.value else None
            end_time = str(self.end_time.value) if self.end_time.value else None

        return NCBuildConfig(
            files=[str(p) for p in self._scanned_files],
            combine_mode=self.combine_mode.value,
            target_variable=target_variable,
            output_variable_name=self.output_variable_name.value or target_variable,
            target_variables=target_variables,
            lat_variable=self.lat_variable.value,
            lon_variable=self.lon_variable.value,
            time_source=self.time_source.value,
            time_variable=self.time_variable.value,
            time_regex=self.time_regex.value,
            time_format=self.time_format.value,
            time_table_path=self.time_table_path.value or None,
            level_source=self.level_source.value,
            level_variable=level_variable,
            level_regex=self.level_regex.value,
            level_table_path=self.level_table_path.value or None,
            output_level_coord_name=self.output_level_coord_name.value or "level",
            level_units=level_units,
            bbox=self._make_bbox_config(),
            start_time=start_time,
            end_time=end_time,
            output_path=self._make_output_path(),
            use_dask_chunks=bool(self.use_dask_chunks.value),
            chunking_mode=self.chunking_mode.value,
            manual_chunks=manual_chunks,
            enable_compression=bool(self.enable_compression.value),
            convert_longitude_to_180=True,
            open_engine="auto",
            use_modis_time_encoding=True,
        )

    def _on_scan_variables(self, event=None) -> None:
        self.log.object = "### Log\n"

        self._sync_selected_files()

        if not self._scanned_files:
            self.preview.object = (
                "### Preview\n"
                "No NetCDF files are selected. First click **Load file list**, "
                "then keep one or more files selected in **Select files from current folder**."
            )
            self._append_log("No NetCDF files selected.")
            return

        self._append_log(f"Found {len(self._scanned_files)} existing NetCDF file(s).")

        if scan_netcdf_files is None:
            self.preview.object = (
                "### Preview\nBackend scan function is not available.\n\n"
                f"Import error: `{BACKEND_IMPORT_ERROR}`"
            )
            self._append_log("Backend scan function is not available.")
            return

        try:
            meta = scan_netcdf_files(
                self._scanned_files,
                max_scan=10,
                use_dask_chunks=bool(self.use_dask_chunks.value),
                chunking_mode=self.chunking_mode.value,
                manual_chunks=self._manual_chunks_dict() if self.chunking_mode.value == "manual" else None,
            )
        except Exception as exc:
            self.preview.object = f"### Preview\nScan failed: `{exc}`"
            self._append_log(f"Scan failed: {exc}")
            return

        variables = meta.get("variables", [])
        all_names = meta.get("all_names", [])

        self.target_variable.options = variables
        self.target_variable.value = [variables[0]] if variables else []

        self.time_variable.options = all_names
        self.lat_variable.options = all_names
        self.lon_variable.options = all_names
        self.level_variable.options = ["None"] + all_names

        self.time_variable.value = meta.get("suggested_time")
        self.lat_variable.value = meta.get("suggested_lat")
        self.lon_variable.value = meta.get("suggested_lon")
        suggested_level = meta.get("suggested_level")
        self.level_variable.value = suggested_level if suggested_level else "None"

        if not self.time_variable.value:
            self.time_source.value = "From filename"
            self._append_log("No obvious time variable detected. Time source was set to 'From filename'.")

        selected_targets = list(self.target_variable.value or [])
        if selected_targets:
            first_target = selected_targets[0]

            if len(selected_targets) == 1:
                self.output_variable_name.value = str(first_target)
                if not self.output_filename.value or self.output_filename.value == "era5_standardized_temperature.nc":
                    self.output_filename.value = f"standardized_{first_target}.nc"
            else:
                # In multi-variable mode the backend keeps original variable names.
                # The output_variable_name field is only meaningful for single-variable mode.
                self.output_variable_name.value = ""
                if not self.output_filename.value or self.output_filename.value == "era5_standardized_temperature.nc":
                    self.output_filename.value = "standardized_multivariable.nc"

        self._detected_time_min = pd.to_datetime(meta.get("time_min")) if meta.get("time_min") else None
        self._detected_time_max = pd.to_datetime(meta.get("time_max")) if meta.get("time_max") else None

        if self._detected_time_min is not None and self._detected_time_max is not None:
            self.start_time.value = self._detected_time_min.to_pydatetime()
            self.end_time.value = self._detected_time_max.to_pydatetime()
            self.detected_time_range.object = (
                f"**Detected time range:** {self._detected_time_min} → {self._detected_time_max}"
            )
        else:
            self.detected_time_range.object = "**Detected time range:** not detected from NetCDF coordinates"

        warnings = meta.get("warnings", [])
        preview_lines = [
            "### Preview",
            f"- **Candidate files:** {len(self._scanned_files)}",
            f"- **Scanned files:** {meta.get('scanned_count', 0)}",
            f"- **Detected variables:** {', '.join(variables) if variables else '-'}",
            f"- **Detected coordinates:** {', '.join(meta.get('coords', [])) if meta.get('coords') else '-'}",
            f"- **Detected dimensions:** {', '.join(meta.get('dims', [])) if meta.get('dims') else '-'}",
            f"- **Combine mode:** {self.combine_mode.value}",
            f"- **Target variable(s):** {', '.join(self.target_variable.value) if self.target_variable.value else '-'}",
            f"- **Time variable:** {self.time_variable.value or '-'}",
            f"- **Latitude variable:** {self.lat_variable.value or '-'}",
            f"- **Longitude variable:** {self.lon_variable.value or '-'}",
            f"- **Level variable:** {self.level_variable.value or 'None'}",
            f"- **Time source:** {self.time_source.value}",
            f"- **Level source:** {self.level_source.value}",
        ]
        if warnings:
            preview_lines.append("\n**Warnings:**")
            preview_lines.extend([f"- {w}" for w in warnings])
        self.preview.object = "\n".join(preview_lines)
        self._append_log("Scan complete.")

    def _on_validate(self, event=None) -> None:
        if validate_build_config is None:
            self.validation_panel.object = (
                "### Validation\nBackend validation function is not available.\n\n"
                f"Import error: `{BACKEND_IMPORT_ERROR}`"
            )
            self._append_log("Backend validation function is not available.")
            return

        try:
            config = self._make_build_config()
            ok, errors, warnings = validate_build_config(config)
        except Exception as exc:
            self.validation_panel.object = f"### Validation\nValidation setup failed: `{exc}`"
            self._append_log(f"Validation setup failed: {exc}")
            return

        if ok:
            lines = [
                "### Validation",
                "**Status:** OK",
                "",
                "- UI settings are sufficient for the backend build step.",
                "- Backend will also check grid compatibility during build.",
            ]
            if warnings:
                lines.append("")
                lines.append("**Warnings:**")
                lines.extend([f"- {w}" for w in warnings])
            self.validation_panel.object = "\n".join(lines)
            self._append_log("Validation completed successfully.")
        else:
            lines = ["### Validation", "**Status:** Issues found", ""]
            lines.extend([f"- {e}" for e in errors])
            if warnings:
                lines.append("")
                lines.append("**Warnings:**")
                lines.extend([f"- {w}" for w in warnings])
            self.validation_panel.object = "\n".join(lines)
            self._append_log(f"Validation completed with {len(errors)} error(s).")

    def _on_build(self, event=None) -> None:
        if build_standardized_netcdf is None:
            self._append_log(f"Backend build function is not available. Import error: {BACKEND_IMPORT_ERROR}")
            return

        try:
            config = self._make_build_config()
            ok, errors, warnings = validate_build_config(config)
            if not ok:
                self.validation_panel.object = (
                    "### Validation\n**Status:** Issues found\n\n"
                    + "\n".join(f"- {e}" for e in errors)
                )
                self._append_log("Build stopped because validation failed.")
                return

            self._append_log("Build started.")
            manifest = build_standardized_netcdf(config)
            self._append_log(f"Build complete: `{manifest['output_path']}`")
            self._append_log(f"Manifest saved: `{manifest['manifest_path']}`")

            self.preview.object = (
                "### Build result\n"
                f"- **Output file:** `{manifest['output_path']}`\n"
                f"- **Manifest:** `{manifest['manifest_path']}`\n"
                f"- **Output dimensions:** `{manifest['output_dims']}`\n"
                f"- **Output variables:** {', '.join(manifest['output_variables'])}\n"
                f"- **Output coordinates:** {', '.join(manifest['output_coords'])}"
            )
        except Exception as exc:
            self._append_log(f"Build failed: {exc}")
            self.validation_panel.object = f"### Validation / Build error\n`{exc}`"

    def view(self):
        input_col = pn.Column(
            "## 1. Input files",
            self.input_folder,
            self.load_files_button,
            self.input_files,
            self.combine_mode,
            self.scan_variables_button,
            sizing_mode="stretch_width",
        )

        mapping_col = pn.Column(
            "## 2. Variables, coordinates and time",
            self.target_variable,
            self.time_variable,
            self.lat_variable,
            self.lon_variable,
            self.level_variable,
            pn.layout.Divider(),
            self.output_variable_name,
            self.output_level_coord_name,
            self.level_units,
            self.level_units_custom,
            self.cf_note,
            pn.layout.Divider(),
            "## 3. Level detection",
            self.level_source,
            self.level_regex,
            self.level_table_path,
            self.level_table_note,
            pn.layout.Divider(),
            "## 4. Time detection",
            self.time_source,
            self.time_regex,
            self.time_format,
            self.time_table_path,
            self.time_table_note,
            sizing_mode="stretch_width",
        )

        subset_output_col = pn.Column(
            "## 5. Spatial subset",
            self.use_bbox,
            pn.Row(self.bbox_south, self.bbox_north, sizing_mode="stretch_width"),
            pn.Row(self.bbox_west, self.bbox_east, sizing_mode="stretch_width"),
            self.bbox_note,
            pn.layout.Divider(),
            "## 6. Time subset",
            self.detected_time_range,
            self.start_time,
            self.end_time,
            self.time_subset_note,
            pn.layout.Divider(),
            "## 7. Output settings",
            self.output_folder,
            self.output_filename,
            self.output_mode,
            self.use_dask_chunks,
            self.chunking_mode,
            pn.Row(self.chunk_time, self.chunk_level, sizing_mode="stretch_width"),
            pn.Row(self.chunk_lat, self.chunk_lon, sizing_mode="stretch_width"),
            self.enable_compression,
            self.validate_button,
            self.build_button,
            sizing_mode="stretch_width",
        )

        main = pn.Column(
            "# NetCDF Builder",
            pn.Row(input_col, mapping_col, subset_output_col, sizing_mode="stretch_width"),
            pn.Row(self.preview, self.validation_panel, self.log, sizing_mode="stretch_width"),
            sizing_mode="stretch_width",
        )
        return main


@register_view(ext_args=["floatpanel"])
def view():
    app = NCBuilder_App()
    template = DEFAULT_TEMPLATE(
        main=[app.view()],
        sidebar=[],
    )
    return template


if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})


if __name__.startswith("bokeh"):
    view()
