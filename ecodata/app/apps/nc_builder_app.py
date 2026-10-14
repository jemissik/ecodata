import logging
from pathlib import Path
from typing import List, Optional

import panel as pn

from ecodata.app.config import DEFAULT_TEMPLATE
from ecodata.app.models import FileSelector
from ecodata.panel_utils import register_view

logger = logging.getLogger(__file__)
BACKEND_IMPORT_ERROR = None

try:
    from ecodata.nc_builder_functions import (
        NCBuildConfig,
        combine_netcdf_files,
        inspect_compatibility,
        scan_netcdf_files,
        validate_build_config,
    )
except Exception as exc:
    BACKEND_IMPORT_ERROR = str(exc)
    NCBuildConfig = None
    combine_netcdf_files = None
    inspect_compatibility = None
    scan_netcdf_files = None
    validate_build_config = None


class NCBuilder_App:
    def __init__(self):
        self.name = "NetCDF Builder"
        self._scanned_files: List[Path] = []

        # 1. Input files
        # Kept intentionally identical to the previous NCBuilder UI.
        self.input_folder = FileSelector(name="Input folder", constrain_path=False, expanded=True, size=10)
        self.input_files = pn.widgets.MultiSelect(
            name="Select files from current folder", options={}, value=[], size=12, sizing_mode="stretch_width"
        )
        self.combine_mode = pn.widgets.RadioButtonGroup(
            name="Combine mode",
            options=["By time", "By level", "By time and level", "Multivariable"],
            value="By time and level",
            button_type="primary",
            sizing_mode="stretch_width",
        )

        # 2. Physical data-variable selection. The list is populated only by
        # Scan variables and intentionally excludes coordinates and technical
        # auxiliary variables such as expver/number/CRS fields.
        self.data_variables = pn.widgets.MultiSelect(
            name="Select data variables to include",
            options={},
            value=[],
            size=12,
            sizing_mode="stretch_width",
            disabled=True,
        )
        self.variable_selection_status = pn.pane.Markdown(
            "Press **Scan variables** in section 1 to populate this list. ",
            sizing_mode="stretch_width",
        )

        # 3. Spatial subset + unified validation
        # Validation checks the selected NetCDF files from section 1, the
        # physical-variable selection from section 2, and the optional spatial
        # subset configuration from section 3.
        self.compatibility_status = pn.pane.Markdown(
            "Configure the optional spatial subset, then press **Validate**. "
            "The same validation checks both file compatibility and spatial subset settings.",
            sizing_mode="stretch_width",
        )

        # 3. Output settings
        self.output_folder = pn.widgets.TextInput(
            name="Output folder",
            placeholder="Path to output folder",
            value=str(Path.home() / "Downloads"),
            sizing_mode="stretch_width",
        )
        self.output_filename = pn.widgets.TextInput(
            name="Output filename", value="combined.nc", sizing_mode="stretch_width"
        )

        # Optional spatial subset applied only after the selected files have
        # been combined/merged. It is configured in section 2 and validated
        # together with the input-file compatibility checks. BBOX is always
        # entered in WGS84 lon/lat.
        self.subset_mode = pn.widgets.Select(
            name="Spatial subset after combine",
            options=["None", "BBOX", "GeoJSON / SHP extent"],
            value="None",
            sizing_mode="stretch_width",
        )
        self.bbox_west = pn.widgets.TextInput(
            name="West longitude (WGS84)", placeholder="e.g. 22.0", sizing_mode="stretch_width"
        )
        self.bbox_east = pn.widgets.TextInput(
            name="East longitude (WGS84)", placeholder="e.g. 40.0", sizing_mode="stretch_width"
        )
        self.bbox_south = pn.widgets.TextInput(
            name="South latitude (WGS84)", placeholder="e.g. 44.0", sizing_mode="stretch_width"
        )
        self.bbox_north = pn.widgets.TextInput(
            name="North latitude (WGS84)", placeholder="e.g. 53.0", sizing_mode="stretch_width"
        )
        self.boundary_file = FileSelector(
            name="Boundary GeoJSON / SHP",
            constrain_path=False,
            expanded=False,
            size=8,
        )
        self.bbox_panel = pn.Column(
            pn.pane.Markdown(
                "BBOX is interpreted as **WGS84 longitude/latitude**. "
                "The NetCDF CRS and native coordinate values are not changed."
            ),
            self.bbox_west,
            self.bbox_east,
            self.bbox_south,
            self.bbox_north,
            sizing_mode="stretch_width",
            visible=False,
        )
        self.boundary_panel = pn.Column(
            pn.pane.Markdown(
                "The vector file is used only for its **spatial extent**. "
                "If its CRS is projected, the extent is transformed to WGS84 before cropping."
            ),
            self.boundary_file,
            sizing_mode="stretch_width",
            visible=False,
        )

        # Preview / validation / log — same visual style as the previous Builder.
        pane_style = {"border": "1px solid #ddd", "padding": "10px", "border-radius": "6px"}
        self.preview = pn.pane.Markdown(
            "### Preview\nNo files scanned yet.", sizing_mode="stretch_width", styles=pane_style
        )
        self.validation_panel = pn.pane.Markdown(
            "### Validation\nNot validated yet.", sizing_mode="stretch_width", styles=pane_style
        )
        self.log = pn.pane.Markdown(
            "### Log\nReady.", sizing_mode="stretch_width", styles=pane_style
        )

        # Buttons. Input-file buttons and their placement are retained.
        self.load_files_button = pn.widgets.Button(
            name="Load file list", button_type="primary", sizing_mode="stretch_width"
        )
        self.scan_variables_button = pn.widgets.Button(
            name="Scan variables", button_type="primary", sizing_mode="stretch_width"
        )
        self.validate_button = pn.widgets.Button(
            name="Validate", button_type="primary", sizing_mode="stretch_width"
        )
        self.build_button = pn.widgets.Button(
            name="Build combined NetCDF", button_type="primary", sizing_mode="stretch_width"
        )

        self.load_files_button.on_click(self._on_load_file_list)
        self.scan_variables_button.on_click(self._on_scan_variables)
        self.validate_button.on_click(self._on_validate)
        self.build_button.on_click(self._on_build)
        self.combine_mode.param.watch(self._on_inputs_changed, "value")
        self.input_files.param.watch(self._on_selected_files_changed, "value")
        self.data_variables.param.watch(self._on_inputs_changed, "value")
        self.subset_mode.param.watch(self._on_subset_mode_changed, "value")
        for widget in (self.bbox_west, self.bbox_east, self.bbox_south, self.bbox_north):
            widget.param.watch(self._on_inputs_changed, "value")
        try:
            self.boundary_file.param.watch(self._on_inputs_changed, "value")
        except Exception:
            pass
        self._on_subset_mode_changed()

    def _append_log(self, message: str) -> None:
        old = self.log.object or "### Log\n"
        if old.strip() == "### Log\nReady.":
            old = "### Log\n"
        self.log.object = old + f"\n- {message}"

    def _on_inputs_changed(self, *_events) -> None:
        self.validation_panel.object = "### Validation\nNot validated yet."
        self.compatibility_status.object = (
            "Press **Validate** to check all."
        )

    def _on_selected_files_changed(self, *_events) -> None:
        # A changed file set invalidates the scan-derived physical-variable list.
        # This prevents stale variable names from being silently used for a new
        # collection of source files.
        self.data_variables.options = {}
        self.data_variables.value = []
        self.data_variables.disabled = True
        self.variable_selection_status.object = (
            "Input-file selection changed. Press **Scan variables** to refresh the physical data-variable list."
        )
        self.preview.object = "### Preview\nInput-file selection changed. Scan variables again."
        self._on_inputs_changed()

    def _on_subset_mode_changed(self, *_events) -> None:
        mode = self.subset_mode.value
        self.bbox_panel.visible = mode == "BBOX"
        self.boundary_panel.visible = mode == "GeoJSON / SHP extent"
        self._on_inputs_changed()

    @staticmethod
    def _selector_file_value(selector) -> Optional[str]:
        raw = getattr(selector, "value", None)
        if isinstance(raw, (list, tuple, set)):
            raw = next(iter(raw), None)
        if not raw:
            return None
        path = Path(str(raw)).expanduser()
        return str(path)

    @staticmethod
    def _parse_bbox_value(text: str, label: str) -> float:
        raw = str(text or "").strip().replace(",", ".")
        if not raw:
            raise ValueError(f"{label} is required for BBOX spatial subset.")
        try:
            return float(raw)
        except Exception as exc:
            raise ValueError(f"{label} must be numeric: {text!r}") from exc

    def _current_input_directory(self) -> Optional[Path]:
        """
        Return the input folder represented by the custom FileSelector.
        The custom selector is used only to define the folder.
        If the selector value is a file, NCBuilder uses its parent folder.
        The actual file list for scan/validate/build is controlled by self.input_files.
        """
        candidates = [getattr(self.input_folder, "value", None), getattr(self.input_folder, "directory", None)]

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
        folder = self._current_input_directory()
        if folder is None:
            return []

        extensions = {".nc", ".nc4", ".cdf", ".netcdf"}
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions]
        return sorted(files, key=lambda p: p.name.lower())

    def _refresh_input_file_options(self) -> None:
        files = self._list_netcdf_files_in_selected_folder()
        options = {f.name: str(f) for f in files if f.exists() and f.is_file()}
        self.input_files.options = options
        self.input_files.value = list(options.values())

    def _on_load_file_list(self, event=None) -> None:
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
        selected_values = list(self.input_files.value or [])
        files: List[Path] = []
        seen = set()

        for value in selected_values:
            path = Path(str(value)).expanduser()
            if path.exists() and path.is_file():
                key = str(path.resolve())
                if key not in seen:
                    seen.add(key)
                    files.append(path)
        return files

    def _sync_selected_files(self) -> List[Path]:
        files = self._collect_input_files()
        self._scanned_files = [Path(f).expanduser() for f in files if Path(f).expanduser().exists()]
        return self._scanned_files

    def _make_output_path(self) -> str:
        folder = Path(self.output_folder.value or ".").expanduser()
        filename = self.output_filename.value or "combined.nc"
        return str(folder / filename)

    def _make_build_config(self) -> NCBuildConfig:
        if NCBuildConfig is None:
            raise RuntimeError(
                f"NCBuilder backend functions are not available. Import error: {BACKEND_IMPORT_ERROR}"
            )
        self._sync_selected_files()

        # Collect spatial-subset values without validating them here.
        # Validation belongs to the backend so the Validate button can report
        # all problems consistently instead of failing while the config object
        # is being created.
        bbox = None
        boundary_path = None
        if self.subset_mode.value == "BBOX":
            bbox = {
                "west": str(self.bbox_west.value or "").strip() or None,
                "east": str(self.bbox_east.value or "").strip() or None,
                "south": str(self.bbox_south.value or "").strip() or None,
                "north": str(self.bbox_north.value or "").strip() or None,
            }
        elif self.subset_mode.value == "GeoJSON / SHP extent":
            boundary_path = self._selector_file_value(self.boundary_file)

        return NCBuildConfig(
            files=[str(p) for p in self._scanned_files],
            combine_mode=self.combine_mode.value,
            output_path=self._make_output_path(),
            open_engine="auto",
            selected_variables=list(self.data_variables.value or []),
            subset_mode=self.subset_mode.value,
            bbox=bbox,
            boundary_path=boundary_path,
        )

    @staticmethod
    def _source_text(source_identity: dict) -> str:
        if not source_identity:
            return "-"
        return ", ".join(f"{k}={v}" for k, v in source_identity.items())

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

        if scan_netcdf_files is None:
            self.preview.object = (
                "### Preview\nBackend scan function is not available.\n\n"
                f"Import error: `{BACKEND_IMPORT_ERROR}`"
            )
            self._append_log("Backend scan function is not available.")
            return

        try:
            meta = scan_netcdf_files(self._scanned_files)
        except Exception as exc:
            self.preview.object = f"### Preview\nScan failed: `{exc}`"
            self._append_log(f"Scan failed: {exc}")
            return

        first = (meta.get("summaries") or [{}])[0]


        if self.combine_mode.value == "Multivariable":
            preview_variables = list(meta.get("physical_variables", []))
            preview_coords = list(meta.get("coords", []))
            auxiliary_variables = list(meta.get("auxiliary_variables", []))
        else:
            preview_variables = list(first.get("physical_variables", first.get("variables", [])))
            preview_coords = list(first.get("coords", []))
            auxiliary_variables = list(first.get("auxiliary_variables", []))

        selectable_variables = list(meta.get("physical_variables", []))
        self.data_variables.options = {name: name for name in selectable_variables}
        self.data_variables.value = list(selectable_variables)
        self.data_variables.disabled = not bool(selectable_variables)
        if selectable_variables:
            self.variable_selection_status.object = (
                f"**{len(selectable_variables)} physical data variable(s) found.** "
            )
        else:
            self.variable_selection_status.object = (
                "No selectable physical data variables were detected in the scanned files."
            )

        lines = [
            "### Preview",
            f"- **Selected files:** {len(self._scanned_files)}",
            f"- **Scanned files:** {meta.get('scanned_count', 0)}",
            f"- **Combine mode:** {self.combine_mode.value}",
            f"- **Grid type:** {first.get('grid_type') or '-'}",
            f"- **Time coordinate:** {first.get('time_name') or '-'}",
            f"- **Level coordinate:** {first.get('level_name') or '-'}",
            f"- **Data variables:** {', '.join(preview_variables) or '-'}",
            f"- **Coordinates:** {', '.join(preview_coords) or '-'}",
            f"- **Source metadata:** {self._source_text(first.get('source_identity', {}))}",
        ]
        if auxiliary_variables:
            lines.append(
                f"- **Auxiliary variables:** {', '.join(auxiliary_variables)}"
            )
        if meta.get("warnings"):
            lines += ["", "**Warnings:**", *[f"- {w}" for w in meta["warnings"]]]
        self.preview.object = "\n".join(lines)
        self._append_log("Scan complete.")

    def _on_validate(self, event=None) -> None:
        """Run one unified validation for sections 1 and 2.

        The backend report keeps file compatibility and spatial-subset
        validation logically separate, but this UI presents them together
        under one Validation action.
        """
        if inspect_compatibility is None:
            self.validation_panel.object = (
                "### Validation\nBackend validation function is not available.\n\n"
                f"Import error: `{BACKEND_IMPORT_ERROR}`"
            )
            self._append_log("Backend validation function is not available.")
            return

        try:
            config = self._make_build_config()
            report = inspect_compatibility(config)
        except Exception as exc:
            self.validation_panel.object = f"### Validation\nValidation failed: `{exc}`"
            self._append_log(f"Validation failed: {exc}")
            return

        file_ok = bool(report.get("file_compatible", False))
        variable_ok = bool(report.get("variable_selection_ok", False))
        subset_ok = bool(report.get("subset_ok", False))
        subset_mode = report.get("subset_mode") or "None"
        overall_ok = bool(report.get("ok", False))

        file_status = "OK" if file_ok else "Issues found"
        if subset_mode == "None":
            subset_status = "Not requested" if subset_ok else "Issues found"
        else:
            subset_status = "OK" if subset_ok else "Issues found"

        lines = [
            "### Validation",
            f"**Overall status:** {'OK' if overall_ok else 'Issues found — build will not run'}",
            "",
            f"- **1. Input files:** {file_status}",
            f"- **2. Data variables:** {'OK' if variable_ok else 'Issues found'} "
            f"({', '.join(report.get('physical_variables', [])) or 'none selected'})",
            f"- **3. Spatial subset:** {subset_status} ({subset_mode})",
        ]

        if file_ok:
            lines += [
                "",
                "**Input-file compatibility:**",
                f"- Files: {len(report.get('files', []))}",
                f"- Combine mode: {config.combine_mode}",
                f"- Grid type: {report.get('grid_type') or '-'}",
                f"- Time coordinate: {report.get('time_name') or '-'}",
                f"- Level coordinate: {report.get('level_name') or '-'}",
                f"- Selected physical variables: {', '.join(report.get('physical_variables', [])) or '-'}",
                f"- Output data/auxiliary variables after filtering: {', '.join(report.get('data_variables', [])) or '-'}",
            ]

        if subset_ok and subset_mode != "None":
            lines += ["", "**Spatial-subset validation:**"]
            if report.get("subset_bbox_wgs84"):
                lines.append(f"- BBOX (WGS84): `{report.get('subset_bbox_wgs84')}`")
            if report.get("subset_info", {}).get("preview_dims"):
                lines.append(
                    f"- Estimated cropped dimensions: `{report['subset_info']['preview_dims']}`"
                )

        if report.get("errors"):
            lines += ["", "**Errors:**", *[f"- {e}" for e in report.get("errors", [])]]

        if report.get("warnings"):
            lines += ["", "**Warnings:**", *[f"- {w}" for w in report.get("warnings", [])]]

        if overall_ok:
            self.compatibility_status.object = (
                "**Validation passed.** "
            )
            self._append_log("Unified validation completed successfully.")
        elif file_ok and not subset_ok:
            self.compatibility_status.object = (
                "**Input files are compatible.** Correct the spatial subset settings before building."
            )
            self._append_log("Input files are compatible, but spatial subset validation failed.")
        else:
            self.compatibility_status.object = (
                "**Validation failed.** Check the input-file and spatial-subset messages below."
            )
            self._append_log(f"Unified validation found {len(report.get('errors', []))} issue(s).")

        self.validation_panel.object = "\n".join(lines)

    def _on_build(self, event=None) -> None:
        if combine_netcdf_files is None or validate_build_config is None:
            self._append_log(f"Backend build function is not available. Import error: {BACKEND_IMPORT_ERROR}")
            return

        try:
            config = self._make_build_config()
            ok, errors, _warnings = validate_build_config(config)
            if not ok:
                self.validation_panel.object = (
                    "### Validation\n**Status:** Issues found\n\n" + "\n".join(f"- {e}" for e in errors)
                )
                self._append_log("Build stopped because configuration validation failed.")
                return

            report = inspect_compatibility(config)
            if not report.get("ok"):
                self.validation_panel.object = (
                    "### Validation\n**Status:** incompatible — build stopped\n\n"
                    + "\n".join(f"- {e}" for e in report.get("errors", []))
                )
                self.compatibility_status.object = "**Incompatible.** Output was not created."
                self._append_log("Build stopped because selected files are incompatible.")
                return

            self._append_log("Build started.")
            manifest = combine_netcdf_files(config)
            self.compatibility_status.object = "**Compatible. Build completed.**"
            self.validation_panel.object = "### Validation\n**Status:** compatible"
            self.preview.object = (
                "### Build result\n"
                f"- **Output file:** `{manifest['output_path']}`\n"
                f"- **Manifest:** `{manifest['manifest_path']}`\n"
                f"- **Combine mode:** {manifest['combine_mode']}\n"
                f"- **Selected physical variables:** {', '.join(manifest.get('selected_physical_variables', [])) or '-'}\n"
                f"- **Grid type:** {manifest.get('grid_type') or '-'}\n"
                f"- **Spatial subset:** {manifest.get('spatial_subset_mode') or 'None'}\n"
                f"- **Subset details:** `{manifest.get('spatial_subset') or '-'}`\n"
                f"- **Output dimensions:** `{manifest['output_dims']}`\n"
                f"- **Output variables:** {', '.join(manifest['output_variables'])}\n"
                f"- **Output coordinates:** {', '.join(manifest['output_coords'])}"
            )
            self._append_log(f"Build complete: `{manifest['output_path']}`")
        except Exception as exc:
            logger.exception("NetCDF build failed")
            self.validation_panel.object = f"### Validation / Build error\n`{exc}`"
            self.compatibility_status.object = "**Build failed.**"
            self._append_log(f"Build failed: {exc}")

    def view(self):
        # 1. Input files — intentionally retained from the previous UI.
        input_col = pn.Column(
            "## 1. Input files",
            self.input_folder,
            self.load_files_button,
            self.input_files,
            self.combine_mode,
            self.scan_variables_button,
            sizing_mode="stretch_width",
        )

        variable_col = pn.Column(
            "## 2. Data variables",
            self.data_variables,
            self.variable_selection_status,
            sizing_mode="stretch_width",
        )

        compatibility_col = pn.Column(
            "## 3. Spatial subset and validation",
            self.subset_mode,
            self.bbox_panel,
            self.boundary_panel,
            pn.layout.Divider(),
            pn.pane.Markdown("#### Validation"),
            self.compatibility_status,
            self.validate_button,
            sizing_mode="stretch_width",
        )

        output_col = pn.Column(
            "## 4. Output settings",
            self.output_folder,
            self.output_filename,
            self.build_button,
            sizing_mode="stretch_width",
        )

        main = pn.Column(
            "# NetCDF Builder",
            pn.Row(input_col, variable_col, compatibility_col, output_col, sizing_mode="stretch_width"),
            pn.Row(self.preview, self.validation_panel, self.log, sizing_mode="stretch_width"),
            sizing_mode="stretch_width",
        )
        return main


@register_view(ext_args=["floatpanel"])
def view():
    app = NCBuilder_App()
    template = DEFAULT_TEMPLATE(main=[app.view()], sidebar=[])
    return template


if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})
