import logging
from pathlib import Path
from datetime import datetime
import re

import pandas as pd
import panel as pn
import param

from ecodata.app.models import FileSelector
from ecodata.panel_utils import param_widget, register_view, try_catch, rename_param_widgets
from ecodata.app.config import DEFAULT_TEMPLATE
from ecodata import validate_and_process_csv
from ecodata.movebank_functions import (
    merge_csv_files_from_folder,
    generate_individual_csvs_for_local_ids,
    interpolate_missing_values_only,
)

logger = logging.getLogger(__file__)


class csv_processor(param.Parameterized):
    """
    CSV processing tools extracted from Annotation Engine.

    Current functionality:
    - Crop / interpolate movement CSV files
    - Simple interpolation of missing values
    - Merge CSV files from a folder

    The processing logic remains in ecodata.movebank_functions.
    This class contains only the Panel UI and callbacks.
    """

    local_ID_file = param_widget(
        FileSelector(constrain_path=False, expanded=True, size=10)
    )
    load_data_button = param_widget(
        pn.widgets.Button(name="Load data", button_type="primary")
    )
    taxon_name_val = param_widget(
        pn.widgets.MultiSelect(
            name="Taxon name (use Ctrl or ⌘ for multiple selection)",
            options=[],
            height=140,
            disabled=True,
        )
    )
    individual_ID = param_widget(
        pn.widgets.MultiSelect(
            name="Individual ID (use Ctrl or ⌘ for multiple selection)",
            options=[],
            height=140,
            disabled=True,
        )
    )
    simple_interp_button = param_widget(
        pn.widgets.Button(
            name="Simple interpolation (missing ≤ 1 day)",
            button_type="primary",
        )
    )
    deployment_time_gap = param_widget(
        pn.widgets.IntInput(
            name="Deployment time gap (minutes)",
            value=60,
            step=60,
            start=0,
        )
    )
    min_expected_obs = param_widget(
        pn.widgets.IntInput(
            name="Minimum expected number of observations(per deployment)",
            value=100,
            step=50,
            start=10,
        )
    )

    time_selection_ID = param_widget(
        pn.widgets.DatetimeRangeSlider(
            name="Select Time Range",
            start=datetime(2010, 1, 1),
            end=datetime(2025, 12, 31),
            value=(datetime(2016, 6, 13), datetime(2016, 6, 14)),
            step=2_592_000_000,
        )
    )
    time_interval = param_widget(
        pn.widgets.IntInput(
            name="Timestep for Interpolation/Averaging (minutes)",
            value=30,
            step=1,
            start=1,
        )
    )
    start_from_midnight = param_widget(
        pn.widgets.Checkbox(name="First timestamp = 00:00:00", value=False)
    )
    out_csv_name = param_widget(
        pn.widgets.TextInput(
            name="Output CSV",
            value=str(Path.home() / "Downloads" / "subset.csv"),
        )
    )
    make_csv = param_widget(
        pn.widgets.Button(name="Make CSV", button_type="primary")
    )
    merge_files = param_widget(
        pn.widgets.Checkbox(name="Merge files after processing", value=False)
    )
    delete_individual_ID_files = param_widget(
        pn.widgets.Checkbox(
            name="Delete individual files after merge",
            value=True,
        )
    )

    folder_to_merge = param_widget(
        pn.widgets.TextInput(
            name="Folder with CSV files to merge (select folder)",
            value=str(Path.home() / "Downloads"),
        )
    )
    delete_empty_columns = param_widget(
        pn.widgets.Checkbox(
            name="Delete empty columns after merging",
            value=False,
        )
    )
    out_merged_csv_name = param_widget(
        pn.widgets.TextInput(
            name="Output merged CSV",
            value=str(Path.home() / "Downloads" / "merged.csv"),
        )
    )
    merge_files_button = param_widget(
        pn.widgets.Button(
            name="Merge files in folder",
            button_type="primary",
        )
    )

    status_text = param.String("Ready...")

    def __init__(self, **params):
        super().__init__(**params)

        rename_param_widgets(
            self,
            [
                "local_ID_file",
                "load_data_button",
                "taxon_name_val",
                "individual_ID",
                "simple_interp_button",
                "deployment_time_gap",
                "min_expected_obs",
                "time_selection_ID",
                "time_interval",
                "start_from_midnight",
                "out_csv_name",
                "make_csv",
                "merge_files",
                "delete_individual_ID_files",
                "folder_to_merge",
                "delete_empty_columns",
                "out_merged_csv_name",
                "merge_files_button",
            ],
        )

        self.df = None
        self.alert = pn.pane.Markdown(self.status_text)

        self.crop_interpolate_tab = pn.Column(
            pn.pane.Markdown("### Crop files"),
            self.local_ID_file,
            self.load_data_button,
            pn.Row(
                self.taxon_name_val,
                self.individual_ID,
            ),
            self.simple_interp_button,
            pn.Column(
                self.deployment_time_gap,
                self.min_expected_obs,
            ),
            self.time_selection_ID,
            pn.Row(
                self.time_interval,
                self.start_from_midnight,
            ),
            self.out_csv_name,
            self.make_csv,
            self.merge_files,
            self.delete_individual_ID_files,
            self.alert,
        )

        self.merge_tab = pn.Column(
            pn.pane.Markdown(
                "### Merge files (Please select a **folder** with CSV files)"
            ),
            self.folder_to_merge,
            self.delete_empty_columns,
            self.out_merged_csv_name,
            self.merge_files_button,
        )

        self.view = pn.Tabs(
            ("Crop & interpolate csv", self.crop_interpolate_tab),
            ("Merge csv", self.merge_tab),
        )

        self.simple_interp_button.on_click(
            self.run_interpolate_missing_only
        )
        self.load_data_button.on_click(
            self.load_ids_from_file
        )
        self.make_csv.on_click(
            self.run_make_csv
        )
        self.merge_files_button.on_click(
            self.run_merge_files
        )
        self.taxon_name_val.param.watch(
            self.update_individual_ids_by_taxon,
            "value",
        )

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
            df.columns = [
                re.sub(r"[-._\s]+", "_", col.lower())
                for col in df.columns
            ]
            self.df = df
            self._set_time_slider_from_df(df)

            unique_ids = sorted(
                df["individual_local_identifier"]
                .dropna()
                .astype(str)
                .unique()
            )
            self.individual_ID.options = list(unique_ids)
            self.individual_ID.disabled = False

            if "individual_taxon_canonical_name" in df.columns:
                unique_taxa = sorted(
                    df["individual_taxon_canonical_name"]
                    .dropna()
                    .astype(str)
                    .unique()
                )
                self.taxon_name_val.options = list(unique_taxa)
                self.taxon_name_val.disabled = False
                self.status_text = (
                    f"Loaded {len(unique_ids)} Individual IDs "
                    f"and {len(unique_taxa)} Taxon names."
                )
            else:
                self.status_text = (
                    f"Loaded {len(unique_ids)} Individual IDs. "
                    "Column 'individual_taxon_canonical_name' not found."
                )

        except Exception as e:
            logger.exception("Error loading IDs")
            self.status_text = f"Error: {e}"

        self.alert.object = self.status_text

    def update_individual_ids_by_taxon(self, event):
        if self.df is None:
            return

        selected_taxa = event.new

        if not selected_taxa:
            unique_ids = sorted(
                self.df["individual_local_identifier"]
                .dropna()
                .astype(str)
                .unique()
            )
            self.individual_ID.options = list(unique_ids)
            self.individual_ID.value = []
        else:
            filtered_df = self.df[
                self.df["individual_taxon_canonical_name"].isin(selected_taxa)
            ]
            unique_ids = sorted(
                filtered_df["individual_local_identifier"]
                .dropna()
                .astype(str)
                .unique()
            )
            self.individual_ID.options = list(unique_ids)
            self.individual_ID.value = list(unique_ids)

    @try_catch("Error generating CSV")
    def run_make_csv(self, *events):
        try:
            individual_ids = self.individual_ID.value
            csv_path = Path(self.local_ID_file.value)
            interval_minutes = int(self.time_interval.value)
            start_time, end_time = self.time_selection_ID.value

            start_time_str = (
                start_time.strftime("%Y-%m-%d %H:%M:%S.%f")
                if not isinstance(start_time, str)
                else start_time
            )
            end_time_str = (
                end_time.strftime("%Y-%m-%d %H:%M:%S.%f")
                if not isinstance(end_time, str)
                else end_time
            )

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
                start_from_midnight=bool(self.start_from_midnight.value),
            )

            if self.merge_files.value:
                merged_df = pd.concat(
                    [pd.read_csv(f) for f in output_files],
                    ignore_index=True,
                )
                merged_output_path = out_csv.replace(
                    ".csv",
                    "_merged.csv",
                )
                merged_df.to_csv(
                    merged_output_path,
                    index=False,
                )

                if self.delete_individual_ID_files.value:
                    for f in output_files:
                        try:
                            Path(f).unlink()
                        except Exception as e:
                            logger.warning(
                                f"Failed to delete {f}: {e}"
                            )

            self.status_text = (
                "Processing complete. "
                f"Output saved to: {Path(out_csv).parent}"
            )

        except Exception as e:
            logger.exception("Failed to generate CSV")
            self.status_text = f"Failed: {e}"

        self.alert.object = self.status_text

    def _set_time_slider_from_df(self, df: pd.DataFrame):
        candidates = (
            "timestamp",
            "eobs_start_timestamp",
            "time",
            "datetime",
            "date",
        )
        time_col = next(
            (c for c in candidates if c in df.columns),
            None,
        )

        if not time_col:
            return

        ts = pd.to_datetime(
            df[time_col],
            errors="coerce",
        )
        ts = ts[ts.notna()]

        if ts.empty:
            return

        tmin = pd.Timestamp(ts.min()).to_pydatetime()
        tmax = pd.Timestamp(ts.max()).to_pydatetime()

        self.time_selection_ID.start = tmin
        self.time_selection_ID.end = tmax
        self.time_selection_ID.value = (
            tmin,
            tmax,
        )

    @try_catch("Error merging files from folder")
    def run_merge_files(self, *events):
        try:
            folder_path = Path(
                self.folder_to_merge.value
            )
            merged_df, deleted_columns = merge_csv_files_from_folder(
                folder_path,
                self.delete_empty_columns.value,
            )

            merged_output_path = self.out_merged_csv_name.value
            merged_df.to_csv(
                merged_output_path,
                index=False,
            )

            deleted_msg = (
                f"\nDeleted columns: {', '.join(deleted_columns)}"
                if deleted_columns
                else "\nNo columns deleted."
            )

            self.status_text = (
                f"Merged CSV saved: "
                f"{merged_output_path}"
                f"{deleted_msg}"
            )

        except Exception as e:
            logger.exception("Failed to merge files")
            self.status_text = f"Failed: {e}"

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
                df_tmp.columns = [
                    re.sub(r"[-._:\s]+", "_", c.lower())
                    for c in df_tmp.columns
                ]
            except Exception as e:
                self.status_text = f"Failed to read CSV: {e}"
                self.alert.object = self.status_text
                return

            all_ids = sorted(
                df_tmp.get(
                    "individual_local_identifier",
                    pd.Series([], dtype=str),
                )
                .dropna()
                .astype(str)
                .unique()
            )
        else:
            all_ids = sorted(
                self.df.get(
                    "individual_local_identifier",
                    pd.Series([], dtype=str),
                )
                .dropna()
                .astype(str)
                .unique()
            )

        selected_ids = (
            list(self.individual_ID.value)
            if self.individual_ID.value
            else all_ids
        )

        if not selected_ids:
            self.status_text = "No IDs to process."
            self.alert.object = self.status_text
            return

        # 3) Time range
        start_time, end_time = self.time_selection_ID.value
        start_time_str = start_time.strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )
        end_time_str = end_time.strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )

        # 4) Which columns to interpolate
        columns = validate_and_process_csv(csv_path)

        # 5) Call simplified interpolation
        out_template = self.out_csv_name.value
        created = interpolate_missing_values_only(
            start_time_str,
            end_time_str,
            csv_path,
            selected_ids,
            columns,
            out_template,
        )

        # 6) result
        if created:
            self.status_text = (
                "Interpolation complete. "
                f"Files: {len(created)}. "
                f"Example: {created[0]}"
            )
        else:
            self.status_text = (
                "Interpolation complete. "
                "No files created "
                "(no eligible gaps ≤ 1 day)."
            )

        self.alert.object = self.status_text


@register_view()
def view():
    viewer = csv_processor()
    template = DEFAULT_TEMPLATE(
        main=[
            viewer.alert,
            viewer.view,
        ]
    )
    return template


if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})


if __name__.startswith("bokeh"):
    view()
