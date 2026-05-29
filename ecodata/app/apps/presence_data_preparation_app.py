"""
eBird data preparation app for ECODATA-Prepare.

Provides UI to:
- Select EBD + Sampling Event tables using local file selectors
- Select region polygon using a local file selector, or use a bounding box
- Configure vetting filters
- Aggregate by time and export files usable by ECODATA-Animate
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import panel as pn
import pandas as pd
import numpy as np

from ecodata.app.config import DEFAULT_TEMPLATE
from ecodata.app.models import FileSelector
from ecodata.panel_utils import register_view
from ecodata.presence_functions import (
    VettingOptions,
    AggregationOptions,
    aggregate_ebird_to_files,
    export_tracks_from_aggregated_counts,
    read_species_from_agg_counts,
)


def _ensure_dir(path: str) -> str:
    """Create directory if missing and return absolute path."""
    path = os.path.abspath(path)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_filename(s: str, default: str = "output") -> str:
    """Return filesystem-safe filename."""
    s = (s or "").strip()
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    return s if s else default


@dataclass
class OutputPaths:
    """Container for output file paths."""
    out_dir: str
    agg_counts_csv: str
    agg_presence_csv: str
    tracks_csv: str
    manifest_json: str


class EbirdPrepareApp:
    """Panel app for preparing eBird data for ECODATA-Animate."""

    def __init__(self):
        self._paths: Optional[OutputPaths] = None
        self._region_id: str = "region_1"

        def make_file_selector(name: str, file_pattern: str = "*") -> FileSelector:
            return FileSelector(
                name=name,
                directory=str(Path.home()),
                file_pattern=file_pattern,
                only_files=True,
                constrain_path=False,
                expanded=True,
                size=10,
                sizing_mode="stretch_width",
            )

        self.source_mode = pn.widgets.RadioButtonGroup(
            name="Data source",
            options=[
                "EBD file",
                "Sampling Event file",
                "Region polygon ",
            ],
            value="EBD file",
            button_type="primary",
        )

        self.ebd_path = make_file_selector(
            "EBD local path",
            "*",
        )
        self.sampling_path = make_file_selector(
            "Sampling local path",
            "*",
        )
        self.polygon_path = make_file_selector(
            "Region polygon local path (shapefile or GeoJSON)",
            "*",
        )

        self.spatial_filter_mode = pn.widgets.RadioButtonGroup(
            name="Spatial filter",
            options=["Region polygon", "Bounding box"],
            value="Region polygon",
            button_type="primary",
        )

        self.bbox_west = pn.widgets.FloatInput(
            name="West / min longitude",
            value=-88.5,
            step=0.1,
        )
        self.bbox_south = pn.widgets.FloatInput(
            name="South / min latitude",
            value=30.1,
            step=0.1,
        )
        self.bbox_east = pn.widgets.FloatInput(
            name="East / max longitude",
            value=-84.8,
            step=0.1,
        )
        self.bbox_north = pn.widgets.FloatInput(
            name="North / max latitude",
            value=35.1,
            step=0.1,
        )

        self.bbox_help = pn.pane.Markdown(
            "Use geographic coordinates in EPSG:4326.  \n"
            "- longitude: -180 … 180  \n"
            "- latitude: -90 … 90  \n"
            "- west < east, south < north",
            sizing_mode="stretch_width",
        )

        
        self.protocols = pn.widgets.MultiChoice(
            name="Allowed protocols (optional)",
            options=["Traveling", "Stationary", "Area", "Incidental", "Historical"],
            value=["Traveling", "Stationary", "Area"],
        )
        self.chk_exclude_incidental = pn.widgets.Checkbox(
            name="Exclude incidental/historical",
            value=True,
        )

        self.chk_reviewed = pn.widgets.Checkbox(
            name="REVIEWED",
            value=False,
        )

        self.chk_approved = pn.widgets.Checkbox(
            name="APPROVED",
            value=False,
        )

        self.chk_all_species_reported = pn.widgets.Checkbox(
            name="ALL SPECIES REPORTED",
            value=False,
        )

        self.duration_min = pn.widgets.IntInput(
            name="Min duration (minutes)",
            value=0,
            start=0,
        )
        self.duration_max = pn.widgets.IntInput(
            name="Max duration (minutes)",
            value=600,
            start=0,
        )
        self.distance_min = pn.widgets.FloatInput(
            name="Min distance (km)",
            value=0.0,
            start=0.0,
            step=0.1,
        )
        self.distance_max = pn.widgets.FloatInput(
            name="Max distance (km)",
            value=50.0,
            start=0.0,
            step=0.1,
        )
        self.chk_require_valid_coords = pn.widgets.Checkbox(
            name="Require valid coordinates",
            value=True,
        )
        self.max_count_clip = pn.widgets.IntInput(
            name="Clip extreme counts above (0=off)",
            value=0,
            start=0,
        )

        today = dt.date.today()
        self.date_start = pn.widgets.DatePicker(
            name="Start date",
            value=today - dt.timedelta(days=30),
        )
        self.date_end = pn.widgets.DatePicker(
            name="End date",
            value=today,
        )
        self.aggregation_days = pn.widgets.IntInput(
            name="Aggregation step (days)",
            value=7,
            start=1,
        )

        self.grid_step_deg = pn.widgets.FloatInput(
            name="Grid step (degrees, 0 = use original coordinates)",
            value=0.0,
            start=0.0,
            step=0.1,
        )

        self.min_reporting_rate = pn.widgets.FloatInput(
            name="Min frequency of detection (reporting_rate)",
            value=0.0,
            start=0.0,
            step=0.01,
        )
        self.min_count_per_complete_checklist = pn.widgets.FloatInput(
            name="Min effort-standardized count",
            value=0.0,
            start=0.0,
            step=0.1,
        )
        self.min_sampling_support = pn.widgets.IntInput(
            name="Min sampling support (n_complete_checklists)",
            value=0,
            start=0,
        )

        self.output_dir = pn.widgets.TextInput(
            name="Output folder",
            value=str(Path.home() / "Downloads"),
        )
        self.run_name = pn.widgets.TextInput(
            name="Run name",
            value="presence_run",
        )

        # output filename for "tracks" export (used only to name the exported file in UI)
        self.output_filename = pn.widgets.TextInput(
            name="Output filename",
            value="presence_points.csv",
            placeholder="presence_points.csv",
        )

        self.btn_aggregate = pn.widgets.Button(
            name="Aggregate",
            button_type="primary",
        )
        self.btn_export_tracks = pn.widgets.Button(
            name="Export file for ECODATA-Animate",
            button_type="primary",
            icon="download",
        )

        self.species_select = pn.widgets.MultiChoice(
            name="Species in results",
            options=[],
            value=[],
        )

        self.status = pn.pane.Alert("Ready.", alert_type="success")
        self.log = pn.pane.Markdown("### Log\n", sizing_mode="stretch_both")
        self.outputs_view = pn.pane.Markdown("### Outputs\nNo outputs yet.", sizing_mode="stretch_width")

        self.spatial_filter_mode.param.watch(self._on_spatial_mode_changed, "value")

        self.btn_aggregate.on_click(self._on_aggregate_clicked)
        self.btn_export_tracks.on_click(self._on_export_tracks_clicked)

        # Rebuild layout (controls column vs results column)
        self.sidebar = pn.Spacer(height=0)
        self.main = pn.Column(self._build_main(), sizing_mode="stretch_both")


    def _append_log(self, msg: str) -> None:
        """Append log line with timestamp."""
        ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log.object += f"\n- `{ts}` {msg}"

    def _set_status(self, msg: str, kind: str = "info") -> None:
        """Set status alert message."""
        self.status.object = msg
        self.status.alert_type = kind

    def _compute_paths(self) -> OutputPaths:
        """Compute output paths from output_dir and run_name."""
        out_dir = _ensure_dir(self.output_dir.value)
        run = _safe_filename(self.run_name.value, default="presence_run")
        return OutputPaths(
            out_dir=out_dir,
            agg_counts_csv=os.path.join(out_dir, f"{run}__agg_counts.csv"),
            agg_presence_csv=os.path.join(out_dir, f"{run}__agg_presence.csv"),
            tracks_csv=os.path.join(out_dir, f"{run}__presence_points.csv"),
            manifest_json=os.path.join(out_dir, f"{run}__manifest.json"),
        )

    def _format_coord_token(self, value: float) -> str:
        """
        Format coordinate for safe use in region_id / filenames.
        Example: -88.4667 -> m88p4667
        """
        s = f"{float(value):.4f}"
        s = s.replace("-", "m").replace(".", "p")
        return s


    def _build_region_id(
        self,
        *,
        bbox: tuple[float, float, float, float] | None,
        polygon_filename_hint: str = "",
    ) -> str:
        """
        Build region_id from bbox coordinates or polygon filename.
        """
        if bbox is not None:
            west, south, east, north = bbox
            return (
                "bbox_"
                f"{self._format_coord_token(west)}_"
                f"{self._format_coord_token(south)}_"
                f"{self._format_coord_token(east)}_"
                f"{self._format_coord_token(north)}"
            )

        name = os.path.basename(polygon_filename_hint or "").strip()
        if name:
            stem = os.path.splitext(name)[0]
            safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
            safe = safe.strip("_")
            if safe:
                return f"poly_{safe}"

        return "poly_region"

    def _resolve_table_source(self, kind: str) -> tuple[str, str]:
        """Resolve EBD or Sampling input as a local filesystem path."""
        kind = str(kind).strip().lower()
        if kind not in {"ebd", "sampling"}:
            raise ValueError(f"Unknown table kind: {kind}")

        selector = self.ebd_path if kind == "ebd" else self.sampling_path
        label = "EBD" if kind == "ebd" else "Sampling"

        path = str(selector.value or "").strip()
        if not path:
            raise ValueError(f"Select {label} file with the file selector.")
        if not os.path.exists(path):
            raise ValueError(f"{label} path does not exist: {path}")
        if not os.path.isfile(path):
            raise ValueError(f"{label} path is not a file: {path}")

        return path, os.path.basename(path)

    def _resolve_polygon_source(self) -> tuple[str, str]:
        """Resolve polygon input as a local filesystem path."""
        path = str(self.polygon_path.value or "").strip()
        if not path:
            raise ValueError("Select a polygon file with the file selector.")
        if not os.path.exists(path):
            raise ValueError(f"Polygon path does not exist: {path}")
        if not os.path.isfile(path):
            raise ValueError(f"Polygon path is not a file: {path}")
        return path, os.path.basename(path)

    def _on_spatial_mode_changed(self, event) -> None:
        """Refresh UI when spatial filter mode changes."""
        self.main[:] = [self._build_main()]

    def _build_sidebar(self) -> pn.Column:
        """Build controls (left column)."""

        if self.spatial_filter_mode.value == "Region polygon":
            spatial_controls = pn.Column(
                self.spatial_filter_mode,
                self.polygon_path,
                sizing_mode="stretch_width",
            )
        else:
            spatial_controls = pn.Column(
                self.spatial_filter_mode,
                pn.Row(
                    self.bbox_west,
                    self.bbox_south,
                    self.bbox_east,
                    self.bbox_north,
                    sizing_mode="stretch_width",
                ),
                self.bbox_help,
                sizing_mode="stretch_width",
            )

        io_box = pn.Column(
            pn.pane.Markdown("#### 1. Inputs"),
            pn.Row(
                pn.Column(
                    pn.pane.Markdown("**EBD file**"),
                    self.ebd_path,
                    sizing_mode="stretch_width",
                ),

                pn.Column(
                    pn.pane.Markdown("**Sampling Event file**"),
                    self.sampling_path,
                    sizing_mode="stretch_width",
                ),

                sizing_mode="stretch_width",
            ),

            pn.layout.Divider(),

            pn.pane.Markdown("#### 2. Spatial subset"),
            spatial_controls,

            pn.layout.Divider(),

            pn.pane.Markdown("#### 3. Outputs"),
            pn.Row(
                self.output_dir,
                self.run_name,
                sizing_mode="stretch_width",
            ),
            sizing_mode="stretch_width",
        )

        vet_box = pn.Column(
            pn.pane.Markdown("#### 4. Vetting / filtering"),

            # 1) all checkboxes in one row
            pn.Row(
                self.chk_reviewed,
                self.chk_approved,
                self.chk_all_species_reported,
                self.chk_exclude_incidental,
                self.chk_require_valid_coords,
                sizing_mode="stretch_width",
            ),

            # protocols + max count clip
            pn.Row(
                self.protocols,
                self.max_count_clip,
                sizing_mode="stretch_width",
            ),

            # Min / Max duration in one row
            pn.Row(
                self.duration_min,
                self.duration_max,
                sizing_mode="stretch_width",
            ),

            # Min / Max distance in next row
            pn.Row(
                self.distance_min,
                self.distance_max,
                sizing_mode="stretch_width",
            ),

            sizing_mode="stretch_width",
        )


        time_box = pn.Column(
            pn.pane.Markdown("#### 5. Time and spatial aggregation"),
            pn.Row(
                self.date_start,
                self.date_end,
                self.aggregation_days,
                self.grid_step_deg,
                sizing_mode="stretch_width",
            ),
            pn.layout.Divider(),
            pn.pane.Markdown("#### 6. Derived-metric filters"),
            pn.Row(
                self.min_reporting_rate,
                self.min_count_per_complete_checklist,
                self.min_sampling_support,
                sizing_mode="stretch_width",
            ),
            sizing_mode="stretch_width",
        )

        actions = pn.Column(
            pn.pane.Markdown("#### 7. Actions"),
            pn.Row(self.btn_aggregate, sizing_mode="stretch_width"),
            pn.layout.Divider(),
            # 6) Species before export + add output filename in same row
            pn.Row(
                self.species_select,
                self.output_filename,
                self.btn_export_tracks,
                sizing_mode="stretch_width",
            ),
            sizing_mode="stretch_width",
        )

        return pn.Column(io_box, vet_box, time_box, actions, sizing_mode="stretch_width")


    def _build_main(self) -> pn.Row:
        """Build 2-column layout: controls (wider) + outputs/log (narrower)."""

        controls = pn.Column(
            pn.pane.Markdown("## Animal presence data preparation (eBird-compatible format)"),
            self._build_sidebar(),
            sizing_mode="stretch_both",
            styles={"flex": "2"},  # 1) first column wider
        )

        results = pn.Column(
            self.status,
            self.outputs_view,
            pn.layout.Divider(),
            self.log,
            sizing_mode="stretch_both",
            styles={"flex": "1"},  # second column narrower
        )

        return pn.Row(controls, results, sizing_mode="stretch_both")

    def _apply_metric_filters_to_counts(self, counts_csv: str) -> List[str]:
        """
        Apply derived-metric filters to aggregated counts CSV in place.

        Returns:
        - updated species list after filtering
        """
        if not counts_csv or not os.path.exists(counts_csv):
            return []

        df = pd.read_csv(counts_csv)

        if "reporting_rate" in df.columns:
            df = df[df["reporting_rate"].fillna(-np.inf) >= float(self.min_reporting_rate.value or 0.0)]

        if "count_per_complete_checklist" in df.columns:
            df = df[
                df["count_per_complete_checklist"].fillna(-np.inf)
                >= float(self.min_count_per_complete_checklist.value or 0.0)
            ]

        if "n_complete_checklists" in df.columns:
            df = df[df["n_complete_checklists"].fillna(0) >= int(self.min_sampling_support.value or 0)]

        df.to_csv(counts_csv, index=False, encoding="utf-8")
        return sorted(df["species"].dropna().astype(str).unique().tolist())

    def _on_aggregate_clicked(self, _event) -> None:
        """Run backend aggregation and update species list."""
        
        try:
            ebd_source, ebd_name = self._resolve_table_source("ebd")
            sampling_source, sampling_name = self._resolve_table_source("sampling")
        except Exception as e:
            self._set_status(str(e), "danger")
            self._append_log(f"Aggregation blocked: {e}")
            return

        polygon_source = None
        polygon_filename_hint = ""
        bbox = None

        if self.spatial_filter_mode.value == "Region polygon":
            try:
                polygon_source, polygon_filename_hint = self._resolve_polygon_source()
            except Exception as e:
                self._set_status(str(e), "danger")
                self._append_log(f"Aggregation blocked: {e}")
                return
        else:
            bbox_values = [
                self.bbox_west.value,
                self.bbox_south.value,
                self.bbox_east.value,
                self.bbox_north.value,
            ]
            if any(v is None for v in bbox_values):
                self._set_status("Fill all four bbox coordinates.", "danger")
                self._append_log("Aggregation blocked: incomplete bbox.")
                return
            bbox = tuple(float(v) for v in bbox_values)
        region_id = self._build_region_id(
            bbox=bbox,
            polygon_filename_hint=polygon_filename_hint,
        )
        start = self.date_start.value
        end = self.date_end.value
        if not start or not end or end < start:
            self._set_status("Check start/end dates.", "danger")
            self._append_log("Aggregation blocked: invalid dates.")
            return
        step_days = int(self.aggregation_days.value or 0)
        if step_days < 1:
            self._set_status("Aggregation step (days) must be >= 1.", "danger")
            self._append_log("Aggregation blocked: invalid aggregation_days.")
            return
        grid_step_deg = float(self.grid_step_deg.value or 0.0)
        if grid_step_deg < 0:
            self._set_status("Grid step (degrees) must be >= 0.", "danger")
            self._append_log("Aggregation blocked: invalid grid_step_deg.")
            return

        self._paths = self._compute_paths()

        vet = VettingOptions(
            require_reviewed=bool(self.chk_reviewed.value),
            require_approved=bool(self.chk_approved.value),
            require_all_species_reported=bool(self.chk_all_species_reported.value),
            allowed_protocols=list(self.protocols.value) if self.protocols.value else None,
            exclude_incidental_historical=bool(self.chk_exclude_incidental.value),
            duration_min_minutes=int(self.duration_min.value or 0),
            duration_max_minutes=int(self.duration_max.value or 600),
            distance_min_km=float(self.distance_min.value or 0.0),
            distance_max_km=float(self.distance_max.value or 50.0),
            require_valid_coords=bool(self.chk_require_valid_coords.value),
            clip_counts_above=int(self.max_count_clip.value or 0),
        )

        agg = AggregationOptions(
            start_date=start,
            end_date=end,
            step_days=step_days,
            grid_step_deg=grid_step_deg,
            treat_x_as_one=True,
        )

        try:
            self._set_status("Aggregating…", "warning")
            self._append_log("Aggregation started.")
            self._append_log(f"EBD source: local path -> {ebd_source}")
            self._append_log(f"Sampling source: local path -> {sampling_source}")
            
            if bbox is not None:
                self._append_log(
                    f"Using bbox: west={bbox[0]}, south={bbox[1]}, east={bbox[2]}, north={bbox[3]}."
                )
            else:
                self._append_log(
                    f"Using polygon: {polygon_filename_hint or '[unknown name]'}."
                )
            self._append_log(f"Region ID: {region_id}")
            self._append_log(f"Aggregation step: {step_days} day(s).")
            if grid_step_deg > 0:
                self._append_log(f"Grid aggregation enabled: {grid_step_deg} degree(s).")
            else:
                self._append_log("Grid aggregation disabled: using original observation coordinates.")
            
            self._append_log(
                "Metric filters: "
                f"reporting_rate >= {float(self.min_reporting_rate.value or 0.0)}, "
                f"count_per_complete_checklist >= {float(self.min_count_per_complete_checklist.value or 0.0)}, "
                f"n_complete_checklists >= {int(self.min_sampling_support.value or 0)}."
            )
            
            species_all = aggregate_ebird_to_files(
                ebd_bytes=ebd_source,
                sampling_bytes=sampling_source,
                polygon_bytes=polygon_source,
                polygon_filename_hint=polygon_filename_hint,
                bbox=bbox,
                ebd_filename_hint=ebd_name or "ebd",
                sampling_filename_hint=sampling_name or "sampling",
                region_id=region_id,
                agg=agg,
                vet=vet,
                out_counts_csv=self._paths.agg_counts_csv,
                out_presence_csv=self._paths.agg_presence_csv,
                manifest_json=self._paths.manifest_json,
            )

            species = self._apply_metric_filters_to_counts(self._paths.agg_counts_csv)

            self.species_select.options = species
            self.species_select.value = []

            self._set_status("Aggregation complete.", "success")
            self._append_log(f"Created: {self._paths.agg_counts_csv}")
            self._append_log(f"Created: {self._paths.agg_presence_csv}")

            self.outputs_view.object = (
                "### Outputs\n"
                f"- **Aggregated counts (A)**: `{self._paths.agg_counts_csv}`\n"
                f"- **Presence/absence (B)**: `{self._paths.agg_presence_csv}`\n"
                f"- **Manifest**: `{self._paths.manifest_json}`\n"
            )

        except Exception as e:
            self._set_status(f"Aggregation failed: {e}", "danger")
            self._append_log(f"Aggregation failed: {e}")

    def _on_export_tracks_clicked(self, _event) -> None:
        """Export Movebank-like pseudo-tracks CSV from aggregated counts."""
        if not self._paths:
            self._paths = self._compute_paths()
        bbox = None
        polygon_filename_hint = ""

        if self.spatial_filter_mode.value == "Region polygon":
            polygon_path = str(self.polygon_path.value or "").strip()
            polygon_filename_hint = os.path.basename(polygon_path) if polygon_path else ""
        else:
            bbox_values = [
                self.bbox_west.value,
                self.bbox_south.value,
                self.bbox_east.value,
                self.bbox_north.value,
            ]
            if not any(v is None for v in bbox_values):
                bbox = tuple(float(v) for v in bbox_values)

        region_id = self._build_region_id(
            bbox=bbox,
            polygon_filename_hint=polygon_filename_hint,
        )
        self._append_log(f"Export region ID: {region_id}")
        try:
            export_tracks_from_aggregated_counts(
                agg_counts_csv=self._paths.agg_counts_csv,
                tracks_csv=self._paths.tracks_csv,
                region_id=region_id,
                id_mode="species",
                species_filter=list(self.species_select.value) if self.species_select.value else None,
            )

            sp = read_species_from_agg_counts(self._paths.agg_counts_csv)
            self.species_select.options = sp

            self._set_status("Export complete.", "success")
            self._append_log(f"Created: {self._paths.tracks_csv}")

            self.outputs_view.object = (
                (self.outputs_view.object or "### Outputs\n")
                + f"\n- **presence_points.csv (for Animate)**: `{self._paths.tracks_csv}`\n"
            )

        except Exception as e:
            self._set_status(f"Export failed: {e}", "danger")
            self._append_log(f"Export failed: {e}")


@register_view(ext_args=["floatpanel"])
def view():
    """Create a fresh app instance and return a template for ECODATA routing."""
    app = EbirdPrepareApp()
    template = DEFAULT_TEMPLATE(
        main=[app.main],
        sidebar=[],
    )
    return template


if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})


if __name__.startswith("bokeh"):
    view()
