import datetime as dt
import time
from pathlib import Path

import geopandas as gpd
import geoviews as gv
import hvplot.pandas  # noqa
import hvplot.xarray  # noqa
import pandas as pd
import panel as pn
import param
import xarray as xr
from panel.reactive import ReactiveHTML, Viewable
from dask.diagnostics import ProgressBar, Callback

import ecodata as eco
from ecodata.panel_utils import param_widget, register_view, try_catch
from ecodata.plotting import plot_avg_timeseries, plot_gridded_data, GriddedPlotWithSlider
from ecodata.xr_tools import detect_varnames, set_time_encoding_modis
from ecodata.app.models import SimpleDashboardCard, FileSelector
from ecodata.app.config import DEFAULT_TEMPLATE

class HTML_WidgetBox(ReactiveHTML):
    object = param.ClassSelector(class_=Viewable)
    _template = """

    <div id="clickable" style="overflow: scroll" >
    ${object}
    </div>
    """

    def __init__(self, object, **params):
        super().__init__(object=object, **params)


class Progress(Callback):
    def __init__(self, *args, parent, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent = parent

    def _start_state(self, dsk, state):
        """runs on start of tasks"""
        self.parent.progress_percent.visible = True
        self.parent.progress_indicator.visible = True

    def _pretask(self, key, dsk, state):
        """runs before every series of tasks"""
        s = state
        ndone = len(s["finished"])
        ntasks = sum(len(s[k]) for k in ["ready", "waiting", "running"]) + ndone
        ratio = min(ndone / ntasks if ntasks else 0, 1)
        self.value = round(ratio * 100)
        # self.parent.view[3][2].value = self.value
        self.parent.progress_indicator.value = self.value
        self.parent.progress_percent.value  = f"{self.value}%"

    def _finish(self, *args, **kwargs):
        """runs after all tasks are done"""
        self.parent.progress_indicator.value = 100
        self.parent.progress_percent.value = "100%"
    #     super()._finish(*args, **kwargs)


class GriddedDataExplorer(param.Parameterized):

    filein = param_widget(FileSelector(constrain_path=False, expanded=True))
    load_data_button = param_widget(
        pn.widgets.Button(button_type="primary", name="Load data")
    )
    timevar = param_widget(pn.widgets.Select(options=[], name="Time"))
    latvar = param_widget(pn.widgets.Select(options=[], name="Latitude"))
    lonvar = param_widget(pn.widgets.Select(options=[], name="Longitude"))
    zvar = param_widget(pn.widgets.Select(options=[], name="Variable of interest"))
    vars_to_save = param_widget(pn.widgets.MultiChoice(options=[], name='Variables to save', sizing_mode='fixed'))
    update_varnames = param_widget(
        pn.widgets.Button(button_type="primary", name="Update variable names")
    )
    disable_plotting_button = param_widget(
        pn.widgets.Toggle(button_type="primary", name="Disable plotting")
    )

    polyfile = param_widget(FileSelector(constrain_path=False, expanded=True))
    load_polyfile = param_widget(
        pn.widgets.Button(button_type="primary", name="Load data")
    )

    status_text = param.String("Ready...")

    ds_raw = param.ClassSelector(class_=xr.Dataset, precedence=-1)
    ds = param.ClassSelector(class_=xr.Dataset, precedence=-1)
    poly = param.ClassSelector(class_=gpd.GeoDataFrame, precedence=-1)

    # Time selection widgets
    date_range = param_widget(
        pn.widgets.DateRangeSlider(
            name="Date range",
            start=dt.date.today() - dt.timedelta(1),
            end=dt.date.today(),
        )
    )

    # Polygon selection
    selection_type = param_widget(
        pn.widgets.RadioBoxGroup(
            name="Selection options", options={"Select within boundary": False, "Mask within boundary": True}
        )
    )

    update_filters = param_widget(
        pn.widgets.Button(button_type="primary", name="Update filters")
    )
    revert_filters = param_widget(
        pn.widgets.Button(button_type="primary", name="Revert filters")
    )

    # Time resampling
    rs_time_quantity = param_widget(
        pn.widgets.FloatInput(name="Amount", value=1.0, step=1e-1, start=0, end=100)
    )
    rs_time_unit = param_widget(
        pn.widgets.Select(options={"Days": "D", "Hours": "H"}, name="Time unit")
    )
    rs_time = param_widget(
        pn.widgets.Button(button_type="primary", name="Resample time", )
    )

    # Spatial resolution
    space_coarsen_factor = param_widget(
        pn.widgets.FloatInput(name="Window size", value=2.0, step=1, start=2, end=100)
    )
    rs_space = param_widget(
        pn.widgets.Button(button_type="primary", name="Resample space",)
    )

    # Group selection for aggregation
    group_selector = param_widget(
        pn.widgets.CrossSelector(
            name="Groupings",
            options=["polygon", "year", "month", "day", "hour"],
            definition_order=False,
        )
    )
    calculate_stats = param_widget(
        pn.widgets.Button(button_type="primary", name="Calculate statistics")
    )

    stats = param.ClassSelector(class_=pd.DataFrame)

    # Save dataset
    output_fname = param_widget(
        pn.widgets.TextInput(placeholder="Output filename...", value="processed_dataset.nc", name="Output file")
    )
    save_ds = param_widget(
        pn.widgets.Button(name="Save dataset", button_type="primary")
    )

    # Progress bar and percent for saving
    progress_indicator = param.ClassSelector(pn.indicators.Progress)
    progress_percent = param.ClassSelector(pn.widgets.StaticText)

    # Save statistics
    stats_fname = param_widget(
        pn.widgets.TextInput(
            placeholder="Output filename...", value="statistics.csv", name="Output file for statistics"
        )
    )
    save_stats = param_widget(
        pn.widgets.Button(name="Save statistics", button_type="primary")
    )

    save_stats_widgets = param.ClassSelector(
        class_=pn.Card, default=pn.Card(title="Statistics", sizing_mode="stretch_both")
    )

    # stats = param.DataFrame()

    # view

    # view = param.ClassSelector(class_=pn.Column, default=pn.Column(sizing_mode="stretch_both"))

    def __init__(self, **params):
        super().__init__(**params)

        # Reset names for panel widgets
        self.load_data_button.name = "Load data"
        self.disable_plotting_button.name = "Disable plotting"
        self.timevar.name = "Time"
        self.latvar.name = "Latitude"
        self.lonvar.name = "Longitude"
        self.zvar.name = "Variable of interest"
        self.vars_to_save.name = "Variables to save"
        self.update_varnames.name = "Update variable selections"

        self.load_polyfile.name = "Load file"

        # commas at the end are necessary for endpoints to be
        self.year_range = pn.widgets.EditableRangeSlider(
            step=1,
            format="0",
        )
        self.month_range = pn.widgets.EditableRangeSlider(
            step=1,
            format="0",
        )
        self.dayofyear_range = pn.widgets.EditableRangeSlider(
            step=1,
            format="0",
        )
        self.hour_range = pn.widgets.EditableRangeSlider(
            step=1,
            format="0",
        )
        self.year_selection = pn.widgets.MultiChoice()
        self.month_selection = pn.widgets.MultiChoice()
        self.dayofyear_selection = pn.widgets.MultiChoice()
        self.hour_selection = pn.widgets.MultiChoice()

        self.selection_type.name = "Polygon selection options"
        self.update_filters.name = "Update filters"
        self.revert_filters.name = "Revert filters"
        self.rs_time_quantity.name = "Amount"
        self.rs_time_unit.name = "Time unit"
        self.rs_time.name = "Resample time"
        self.space_coarsen_factor.name = "Window size"
        self.rs_space.name = "Coarsen dataset"
        self.group_selector.name = "Groupings"
        self.calculate_stats.name = "Calculate statistics"
        self.output_fname.name = "Output file"
        self.save_ds.name = "Save dataset"
        self.stats_fname.name = "Output file for statistics"
        self.save_stats.name = "Save statistics"

        # Progress bar and percent for saving
        self.progress_indicator = pn.indicators.Progress(name='Progress', height=20, bar_color="success", visible=False)
        self.progress_percent = pn.widgets.StaticText(name="Progress", value="0%", visible=False)

        self.file_input_card = pn.Card(
            self.filein,
            pn.Row(self.latvar, self.lonvar),
            pn.Row(self.timevar, self.zvar),
            pn.Row(self.vars_to_save),
            pn.Row(self.load_data_button, self.update_varnames),
            title="Input environmental dataset file",
        )

        self.polyfile_widgets = pn.Card(self.polyfile, self.load_polyfile, title="Input polygon file")

        self.rs_time_widgets = pn.Card(
            pn.Row(self.rs_time_quantity, self.rs_time_unit, self.rs_time), title="Time resampling"
        )
        self.rs_space_widgets = pn.Card(pn.Row(self.space_coarsen_factor, self.rs_space), title="Spatial resolution")

        self.groupby_widgets = pn.Card(pn.Row(self.group_selector), self.calculate_stats, title="Calculate statistics")

        self.outfile_widgets = pn.Card(pn.Row(self.output_fname, self.save_ds, self.progress_percent, self.progress_indicator), title="Output file")

        self.save_stats_widgets[:] = [pn.Row(self.stats_fname, self.save_stats), self.stats]

        self.alert = pn.pane.Markdown(self.status_text)

        # View

        self.sidebar = pn.Accordion(
            (
                "Selection Options",
                pn.Column(
                    self.selection_type,
                    self.update_filters,
                    self.revert_filters,
                ),
            ),
            (
                "Processing Options",
                pn.Column(
                    self.rs_time_quantity, self.rs_time_unit, self.rs_time, self.space_coarsen_factor, self.rs_space
                ),
            ),
            active=[0, 1],
            sizing_mode="stretch_width"
        )

        self.plot_col = pn.Column()
        self.ts_pane = pn.pane.HoloViews(sizing_mode="stretch_both")
        self.ds_pane = pn.pane.HTML(sizing_mode="stretch_both", styles={"overflow": "auto"})
        self.dashboard_pane = pn.pane.HTML(sizing_mode="stretch_both", styles={"overflow": "auto"})

        self.ts_widget = pn.pane.Markdown("")
        # self.figs_with_widget = pn.layout.FloatPanel(
        #     pn.Tabs(("Charts", self.plot_col), ("Data", self.ds_pane)),
        #     name='Basic FloatPanel',
        #     margin=20
        # )
        self.figs_with_widget = pn.Tabs(("Charts", self.plot_col), ("Data", self.ds_pane))

        self.view_objects = {
            "file_input_card": 1,
            "polyfile_widgets": 2,
            "outfile_widgets": 3,
            "groupby_widgets": 4,
            "stats": 5,
        }

        self.view = pn.Column(
            self.file_input_card,
            self.polyfile_widgets,
            self.outfile_widgets,
            self.groupby_widgets,
            self.save_stats_widgets,
            sizing_mode="stretch_both",
        )

    @try_catch()
    @param.depends("update_varnames.value", watch=True)
    def update_ds_varnames(self):
        if self.ds_raw is not None and self.zvar.value is not None:
            self.ds = self.ds_raw[self.vars_to_save.value].copy()
            self.update_selection_widgets()

    @try_catch()
    def update_selection_widgets(self):
        if self.ds_raw is not None:
            self.status_text = "Updating widgets"

            self.date_range = pn.widgets.DateRangeSlider(
                name="",
                start=self.ds_raw[self.timevar.value].min().values,
                end=self.ds_raw[self.timevar.value].max().values,
            )

            self.time_cond_args = []
            tabs = pn.Tabs()

            def update_time_selection(time_str, range_widget, selection_widget):
                time_unit = time_str.replace(" ", "")
                time_values = pd.unique(getattr(self.ds_raw[self.timevar.value].dt, time_unit))
                if len(time_values) > 1:

                    range_check = pn.widgets.Checkbox(value=True, name="Range Values")
                    disc_check = pn.widgets.Checkbox(value=True, name="Discrete Values")

                    # range_widget.visible = False
                    # selection_widget.visible = False

                    range_widget.name = ""
                    selection_widget.name = ""

                    @pn.depends(range_check, watch=True)
                    def range_cb(val):
                        range_widget.visible = val
                        range_widget.min_height = range_widget.min_height + 1 if val else range_widget.min_height - 1

                    @pn.depends(disc_check, watch=True)
                    def disc_cb(val):
                        selection_widget.visible = val

                    range_widget.min_height = 50
                    range_widget.start = time_values.min()
                    range_widget.end = time_values.max()
                    range_widget.value = (time_values.min(), time_values.max())
                    selection_widget.options = list(time_values)
                    self.time_cond_args.append(time_unit)
                    # tabs.append((time_str.title(), pn.Column(pn.WidgetBox(range_check, range_widget),
                    #                                          pn.WidgetBox(disc_check, selection_widget,))))
                    tabs.append(
                        (
                            time_str.title(),
                            pn.Column(range_check, range_widget, disc_check, selection_widget),
                        )
                    )

            self.time_units = ["year", "dayofyear", "month", "hour"]
            self.range_widgets = {
                "year_range": self.year_range,
                "dayofyear_range": self.dayofyear_range,
                "month_range": self.month_range,
                "hour_range": self.hour_range,
            }
            self.selection_widgets = {
                "years": self.year_selection,
                "daysofyear": self.dayofyear_selection,
                "months": self.month_selection,
                "hours": self.hour_selection,
            }

            for time_unit, range_widget, selection_widget in zip(
                self.time_units, self.range_widgets.values(), self.selection_widgets.values()
            ):
                update_time_selection(time_unit, range_widget, selection_widget)

            self.sidebar[0].objects = [
                self.selection_type,
                "date range selection:",
                self.date_range,
                tabs,
                self.update_filters,
                self.revert_filters,
            ]

    @try_catch()
    @param.depends("load_data_button.value", watch=True)
    def load_data(self):
        if self.filein.value:
            self.status_text = "Loading data..."

            ds_raw = xr.open_dataset(self.filein.value, chunks='auto').unify_chunks()
            matched_vars, ds_vars, unmatched_vars = detect_varnames(ds_raw)
            self.timevar.options = list(ds_vars)
            self.latvar.options = list(ds_vars)
            self.lonvar.options = list(ds_vars)
            self.timevar.value = matched_vars["timevar"]
            self.latvar.value = matched_vars["latvar"]
            self.lonvar.value = matched_vars["lonvar"]
            self.zvar.options = [None] + list(unmatched_vars)
            self.zvar.value = None
            self.vars_to_save.options = [None] + list(unmatched_vars)
            self.vars_to_save.value = list(unmatched_vars)

            # Convert to datetime if necessary
            if ds_raw[self.timevar.value].dtype == "O":
                datetimeindex = ds_raw.indexes[self.timevar.value].to_datetimeindex()
                ds_raw[self.timevar.value] = datetimeindex

            self.status_text = "File loaded"
            self.ds_raw = ds_raw
            self.ds = ds_raw.copy()
        else:
            self.status_text = "File path must be selected first!"

    @try_catch()
    @param.depends("load_polyfile.value", watch=True)
    def load_poly_data(self):
        if self.polyfile.value:
            self.status_text = "Loading file..."
            poly = gpd.read_file(self.polyfile.value)
            self.status_text = "File loaded"
            self.poly = poly
        else:
            self.status_text = "File path must be selected first!"

    @try_catch()
    @param.depends("update_filters.value", watch=True)
    def update_ds(self):
        if self.ds_raw is not None:
            self.status_text = "Updating..."
            _ds = eco.select_time_range(
                self.ds_raw,
                time_var=self.timevar.value,
                start_time=self.date_range.value[0],
                end_time=self.date_range.value[1],
            )
            kwargs = {}
            for time_unit, range_widget, selection_widget in zip(
                self.time_units, self.range_widgets, self.selection_widgets
            ):
                if time_unit in self.time_cond_args:
                    if getattr(self.selection_widgets[selection_widget], "visible"):
                        kwargs[selection_widget] = getattr(self.selection_widgets[selection_widget], "value")
                    if getattr(self.range_widgets[range_widget], "visible"):
                        range_values = getattr(self.range_widgets[range_widget], "value")
                        kwargs[range_widget] = (int(range_values[0]), int(range_values[1]))
            _ds = eco.select_time_cond(_ds, time_var=self.timevar.value, **kwargs)

            if self.poly is not None:
                _ds = eco.select_spatial(_ds, boundary=self.poly, invert=self.selection_type.value)
            self.ds = _ds
            self.status_text = "Applied updated filters"
        else:
            self.status_text = "No dataset available"

    @try_catch()
    @param.depends("revert_filters.value", watch=True)
    def revert_ds(self):
        # self.date_range.values = (self.ds_raw.time.min().values, self.ds_raw.time.max().values)
        self.update_selection_widgets()
        if self.ds_raw is not None:
            self.ds = self.ds_raw.copy()
            self.status_text = "Filters reverted"

    @try_catch(msg="Resampling failed.")
    @param.depends("rs_time.value", watch=True)
    def resample_time(self):
        self.status_text = "Resampling..."
        self.ds = eco.resample_time(
            self.ds,
            timevar=self.timevar.value,
            time_quantity=self.rs_time_quantity.value,
            time_unit=self.rs_time_unit.value,
        )
        self.status_text = "Dataset resampled"

    @try_catch(msg="Aggregation failed.")
    @param.depends("rs_space.value", watch=True)
    def resample_space(self):
        self.status_text = "Calculating..."
        self.ds = eco.coarsen_dataset(
            self.ds,
            n_window={
                self.latvar.value: int(self.space_coarsen_factor.value),
                self.lonvar.value: int(self.space_coarsen_factor.value),
            },
        )
        self.status_text = "Aggregation completed."

    @try_catch()
    @param.depends("status_text", watch=True)
    def update_status_view(self):
        self.alert.object = self.status_text


    @try_catch()
    @param.depends("ds", watch=True)
    def update_dataset_view(self):
        self.ds_pane.object = self.ds
        self.figs_with_widget.__setitem__(1, self.ds_pane)

    @try_catch()
    @param.depends("update_varnames.value", "update_filters.value","disable_plotting_button.value", "poly", watch=True)
    def update_plot_view(self):
        # self.ds_pane.object = self.ds
        # if self.disable_plotting_button.value:
        #     self.figs_with_widget[:] = [("Data", self.ds_pane)]

        if not self.disable_plotting_button.value and all([self.timevar.value, self.latvar.value, self.lonvar.value, self.zvar.value]):
            self.status_text = "Creating plot"
            width = 500
            ds_plot = GriddedPlotWithSlider(
                self.ds, timevar=self.timevar.value, zvar=self.zvar.value,
                lonvar=self.lonvar.value, latvar=self.latvar.value, width=width
            )
            # .opts(frame_width=width)
            if self.poly is not None:
                ds_plot.fig.object = ds_plot.fig.object * gv.Path(self.poly).opts(line_color="k", line_width=2)

            ds_ts_plot = plot_avg_timeseries(
                self.ds.reindex_like(self.ds_raw),
                x=self.lonvar.value,
                y=self.latvar.value,
                z=self.zvar.value,
                time=self.timevar.value,
            )
            self.plot_col.objects = [ds_plot.fig_with_widget, ds_ts_plot]

            # self.figs_with_widget[:] = [
            #     ("Charts", self.plot_col),
            #     ("Data", self.ds_pane),
            # ]
            self.figs_with_widget.__setitem__(0, self.plot_col)


            self.status_text = "Plot created!"

        else:
            self.status_text = "Please specify variable names"

    @try_catch()
    @param.depends("calculate_stats.value", watch=True)
    def groupby_apply(self):
        self.status_text = "Calculating..."

        select_list = self.group_selector.value

        # Check if grouping by polygon
        if "polygon" in select_list:
            time_list = select_list.copy()
            time_list.remove("polygon")
            poly = self.poly.reset_index()
            result = eco.groupby_poly_time(
                vector_data=poly,
                vector_var="index",
                ds=self.ds,
                ds_var=self.zvar.value,
                latvar=self.latvar.value,
                lonvar=self.lonvar.value,
                timevar=self.timevar.value,
                groupby_vars=time_list,
            )
            self.stats = result

        else:
            result = eco.groupby_multi_time(
                ds=self.ds, var=self.zvar.value, time=self.timevar.value, groupby_vars=select_list
            )
            result = result.to_dataframe().reorder_levels(select_list).sort_index()
            result = result[["count", "mean", "std", "min", "25%", "50%", "75%", "max"]]
            self.stats = result
            # self.stats_widget.value = result

        self.status_text = "Calculations completed"

        self.save_stats_widgets.objects = [pn.Row(self.stats_fname, self.save_stats), self.stats]

    @try_catch(msg="File couldn't be saved.")
    @param.depends("save_stats.value", watch=True)
    def save_stats_results(self):
        outfile = Path(self.stats_fname.value).resolve()
        self.stats.to_csv(outfile)
        self.status_text = f"File saved to: {outfile}"

    @try_catch(msg="File couldn't be saved.")
    @param.depends("save_ds.value", watch=True)
    def save_dataset(self):
        with Progress(parent=self):
            outfile = Path(self.output_fname.value).resolve()

            # Set the time encoding to match MODIS format
            set_time_encoding_modis(self.ds)
            print(self.ds.time.encoding)

            # Make sure dataset is rechunked before computations are triggered
            self.ds = self.ds.chunk(chunks='auto')

            self.ds.to_netcdf(outfile)
            self.status_text = f"File saved to: {outfile}"
        # self.progress_indicator.value = 100


@register_view('floatpanel')
def view():
    viewer = GriddedDataExplorer()
    template = DEFAULT_TEMPLATE(
        main=[viewer.figs_with_widget, viewer.view],
        sidebar=[viewer.sidebar],
        header=viewer.status_text
    )
    return template


if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})


if __name__.startswith("bokeh"):
    view()
