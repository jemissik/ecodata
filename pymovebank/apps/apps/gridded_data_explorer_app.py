# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.14.0
#   kernelspec:
#     display_name: Python 3.9.13 ('pmv-dev')
#     language: python
#     name: python3
# ---

# %%

import geopandas as gpd
import pymovebank as pmv
import xarray as xr
import hvplot.xarray  # noqa
import hvplot.pandas  # noqa
import geoviews as gv
import panel as pn
import pandas as pd
import param
import datetime as dt
from pathlib import Path

from panel.reactive import ReactiveHTML, Viewable

from pymovebank.plotting import plot_gridded_data, plot_avg_timeseries
from pymovebank.panel_utils import param_widget, try_catch
from pymovebank.xr_tools import detect_varnames
from pymovebank.apps.models import config


class HTML_WidgetBox(ReactiveHTML):
    object = param.ClassSelector(class_=Viewable)
    _template = """

    <div id="clickable" style="overflow: scroll" >
    ${object}
    </div>
    """
    def __init__(self, object, **params):
        super().__init__(object=object, **params)


# %%
class GriddedDataExplorer(param.Parameterized):

    # TODO replace with new file selector
    filein = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Data file'))
    load_data_button = param_widget(pn.widgets.Button(button_type='primary', name='Load data', align='end', sizing_mode='fixed'))
    timevar = param_widget(pn.widgets.Select(options=[], name='Time', sizing_mode='fixed'))
    latvar = param_widget(pn.widgets.Select(options=[], name='Latitude', sizing_mode='fixed'))
    lonvar = param_widget(pn.widgets.Select(options=[], name='Longitude', sizing_mode='fixed'))
    zvar = param_widget(pn.widgets.Select(options=[], name='Variable of interest', sizing_mode='fixed'))
    update_varnames = param_widget(pn.widgets.Button(button_type='primary', name='Update variable names', align='end', sizing_mode='fixed'))

    polyfile = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Polygon file'))
    load_polyfile = param_widget(pn.widgets.Button(button_type='primary', name='Load data', sizing_mode='fixed', align='end'))


    status_text = param.String('Ready...')

    ds_raw = param.ClassSelector(class_=xr.Dataset, precedence=-1)
    ds = param.ClassSelector(class_=xr.Dataset, precedence=-1)
    poly = param.ClassSelector(class_=gpd.GeoDataFrame, precedence=-1)

    # Time selection widgets
    date_range = param_widget(pn.widgets.DateRangeSlider(name="Date range",
                                                         start=dt.date.today() - dt.timedelta(1), end=dt.date.today()))

    # year_range = param_widget(pn.widgets.EditableRangeSlider(name='Year range', step=1, format='0',))
    # month_range = param_widget(pn.widgets.EditableRangeSlider(name='Month range', step=1, format='0', ))
    # dayofyear_range = param_widget(pn.widgets.EditableRangeSlider( step=1, format='0',))
    # hour_range = param_widget(pn.widgets.EditableRangeSlider(name='Hour range', step=1, format='0'))
    # year_selection = param_widget(pn.widgets.MultiChoice(name='Years'))
    # month_selection = param_widget(pn.widgets.MultiChoice(name='Months'))
    # dayofyear_selection = param_widget(pn.widgets.MultiChoice())
    # hour_selection = param_widget(pn.widgets.MultiChoice(name='Hours'))

    # add_year_range = param_widget(pn.widgets.Button(button_type='primary', sizing_mode='fixed', align='end'))
    # reset_year = param_widget(pn.widgets.Button(button_type='primary', sizing_mode='fixed', align='end'))
    # add_month_range = param_widget(pn.widgets.Button(button_type='primary', sizing_mode='fixed', align='end'))
    # reset_month = param_widget(pn.widgets.Button(button_type='primary', sizing_mode='fixed', align='end'))
    # add_doy_range = param_widget(pn.widgets.Button(button_type='primary', sizing_mode='fixed', align='end'))
    # reset_doy = param_widget(pn.widgets.Button(button_type='primary', sizing_mode='fixed', align='end'))
    # add_hour_range = param_widget(pn.widgets.Button(button_type='primary', sizing_mode='fixed', align='end'))
    # reset_hour = param_widget(pn.widgets.Button(button_type='primary', sizing_mode='fixed', align='end'))

    # Polygon selection
    selection_type = param_widget(pn.widgets.RadioBoxGroup(name='Selection options',
                                                           options={'Select within boundary':False,
                                                                    'Mask within boundary':True}))

    update_filters = param_widget(pn.widgets.Button(button_type='primary', name='Update filters', align='end', sizing_mode='fixed'))
    revert_filters = param_widget(pn.widgets.Button(button_type='primary', name='Revert filters', align='end', sizing_mode='fixed'))

    # Time resampling
    rs_time_quantity = param_widget(pn.widgets.FloatInput(name='Amount', value=1., step=1e-1, start=0, end=100, sizing_mode='fixed'))
    rs_time_unit = param_widget(pn.widgets.Select(options={'Days':'D', 'Hours':'H'}, name='Time unit', sizing_mode='fixed'))
    rs_time = param_widget(pn.widgets.Button(button_type='primary', name='Resample time', align='end', sizing_mode='fixed'))

    # Spatial resolution
    space_coarsen_factor = param_widget(pn.widgets.FloatInput(name='Window size', value=2., step=1, start=2, end=100, sizing_mode='fixed'))
    rs_space = param_widget(pn.widgets.Button(button_type='primary', name='Resample space', align='end', sizing_mode='fixed'))

    # Group selection for aggregation
    group_selector = param_widget(pn.widgets.CrossSelector(name='Groupings',
        options=['polygon', 'year', 'month', 'day', 'hour'], definition_order=False,sizing_mode='fixed'))
    calculate_stats = param_widget(pn.widgets.Button(button_type='primary', name='Calculate statistics', align='start', sizing_mode='fixed'))

    stats = param.ClassSelector(class_=pd.DataFrame)
    # stats_widget = param.Parameter()
    # stats_widget = param_widget(pn.widgets.Tabulator(None, name='DataFrame', hierarchical=True))

    # Save dataset
    output_fname = param_widget(pn.widgets.TextInput(placeholder='Output filename...',
                                                     value='processed_dataset.nc',name='Output file'))
    save_ds = param_widget(pn.widgets.Button(name='Save dataset', button_type='primary', align='end', sizing_mode='fixed'))

    # Save statistics
    stats_fname = param_widget(pn.widgets.TextInput(placeholder='Output filename...',
                                                     value='statistics.csv',name='Output file for statistics'))
    save_stats = param_widget(pn.widgets.Button(name='Save statistics', button_type='primary', align='end', sizing_mode='fixed'))

    save_stats_widgets = param.ClassSelector(class_=pn.Card, default=pn.Card(title='Statistics', sizing_mode="stretch_both"))

    # stats = param.DataFrame()

    # view

    # view = param.ClassSelector(class_=pn.Column, default=pn.Column(sizing_mode="stretch_both"))

    def __init__(self, **params):
        super().__init__(**params)
        # self.stats_widget = pn.widgets.Tabulator(
        #     pd.DataFrame(),
        #     name='DataFrame', hierarchical=True,
        #
        #     # height=400,
        #     sizing_mode="stretch_both",
        # )
        # Reset names for panel widgets
        self.filein.name = "File path"
        self.load_data_button.name = 'Load data'
        self.timevar.name = 'Time'
        self.latvar.name = 'Latitude'
        self.lonvar.name = 'Longitude'
        self.zvar.name = 'Variable of interest'
        self.update_varnames.name = "Update variable names"

        self.polyfile.name = "Polygon file"
        self.load_polyfile.name = 'Load file'
        self.date_range.name = "Date range selection"

        self.year_range = pn.widgets.EditableRangeSlider(step=1, format='0', )
        self.month_range = pn.widgets.EditableRangeSlider(step=1, format='0', )
        self.dayofyear_range = pn.widgets.EditableRangeSlider(step=1, format='0', )
        self.hour_range = pn.widgets.EditableRangeSlider(step=1, format='0')
        self.year_selection = pn.widgets.MultiChoice()
        self.month_selection = pn.widgets.MultiChoice()
        self.dayofyear_selection = pn.widgets.MultiChoice()
        self.hour_selection = pn.widgets.MultiChoice()

        self.selection_type.name = 'Polygon selection options'
        self.update_filters.name = 'Update filters'
        self.revert_filters.name = 'Revert filters'
        self.rs_time_quantity.name = 'Amount'
        self.rs_time_unit.name = 'Time unit'
        self.rs_time.name = 'Resample time'
        self.space_coarsen_factor.name = "Window size"
        self.rs_space.name = "Coarsen dataset"
        self.group_selector.name = 'Groupings'
        self.calculate_stats.name = "Calculate statistics"
        self.output_fname.name = 'Output file'
        self.save_ds.name = "Save dataset"
        self.stats_fname.name = 'Output file for statistics'
        self.save_stats.name = 'Save statistics'


        # Widget groups
        self.filein_widgets = pn.Row(self.filein, self.load_data_button)
        # self.file_card = pn.Card(self.filein,
        #                          self.load_data_button,
        #                          title="Select File",
        #                          )
        self.varname_widgets = pn.Column(
            pn.Row( self.latvar, self.lonvar),
            pn.Row(self.timevar, self.zvar, ),
            pn.Row(self.update_varnames)
        )
        self.file_input_card = pn.Card(self.filein_widgets, self.varname_widgets,
                                       title="Input environmental dataset file", width_policy="max")

        self.polyfile_widgets = pn.Card(self.polyfile, self.load_polyfile, title='Input polygon file')

        self.rs_time_widgets = pn.Card(pn.Row(self.rs_time_quantity, self.rs_time_unit, self.rs_time), title='Time resampling')
        self.rs_space_widgets = pn.Card(pn.Row(self.space_coarsen_factor, self.rs_space), title="Spatial resolution")

        self.groupby_widgets = pn.Card(pn.Row(self.group_selector), self.calculate_stats, title='Calculate statistics')

        self.outfile_widgets = pn.Card(pn.Row(self.output_fname, self.save_ds), title='Output file')

        self.save_stats_widgets[:] = [pn.Row(self.stats_fname, self.save_stats), self.stats]

        self.alert = pn.pane.Alert(self.status_text)

        # View

        self.sidebar = pn.Accordion(
            ("Selection Options",
             pn.Column(
                self.selection_type,
                self.update_filters,
                self.revert_filters,)),
            ("Processing Options",
            pn.Column(
                self.rs_time_quantity,
                self.rs_time_unit,
                self.rs_time,
                self.space_coarsen_factor,
                self.rs_space)),
            active=[0]
        )

        self.plot_col = pn.Column(height_policy="max")
        self.ts_pane = pn.pane.HoloViews(sizing_mode="stretch_both")
        self.ds_pane = pn.pane.HTML(sizing_mode="stretch_both", style={"overflow": "auto"})
        self.ts_widget = pn.pane.Markdown("")
        self.figs_with_widget = pn.Tabs()

        self.view_objects = {
                             'file_input_card':1,
                             'polyfile_widgets':2,
                             'outfile_widgets':5,
                             'groupby_widgets':6,
                             'stats':7,
                             'status':8}

        self.view = pn.Column(
            self.file_input_card,
            self.polyfile_widgets,
            self.outfile_widgets,
            self.groupby_widgets,
            self.save_stats_widgets,
            self.alert,
            sizing_mode="stretch_both")

    @try_catch()
    @param.depends("update_varnames.value", watch=True)
    def update_selection_widgets(self):
        if self.ds_raw is not None:
            self.status_text = 'Updating widgets'


            self.date_range = pn.widgets.DateRangeSlider(name="Date range selection",
                                                         start=self.ds_raw[self.timevar.value].min().values,
                                                         end=self.ds_raw[self.timevar.value].max().values)

            self.time_cond_args =[]
            tabs = pn.Tabs()
            def update_time_selection(time_str, range_widget, selection_widget):
                time_unit = time_str.replace(" ", "")
                time_values = pd.unique(getattr(self.ds_raw[self.timevar.value].dt, time_unit))
                if len(time_values)>1:

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
                    tabs.append((time_str.title(), pn.Column(range_check, range_widget,
                                                             disc_check, selection_widget),))


            time_units = ['year', 'day of year', 'month', 'hour']
            range_widgets = [self.year_range, self.dayofyear_range, self.month_range, self.hour_range]
            selection_widgets = [self.year_selection, self.dayofyear_selection, self.month_selection, self.hour_selection]

            for time_unit, range_widget, selection_widget in zip(time_units, range_widgets, selection_widgets):
                update_time_selection(time_unit, range_widget, selection_widget)

            self.sidebar[0].objects = [
                "## Selection options",
                self.selection_type,
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
            ds_raw = xr.load_dataset(self.filein.value)
            matched_vars, ds_vars, unmatched_vars = detect_varnames(ds_raw)
            self.timevar.options = list(ds_vars)
            self.latvar.options = list(ds_vars)
            self.lonvar.options = list(ds_vars)
            self.timevar.value = matched_vars['timevar']
            self.latvar.value = matched_vars['latvar']
            self.lonvar.value = matched_vars['lonvar']
            self.zvar.options = [None] + list(unmatched_vars)
            self.zvar.value = None

            # Convert to datetime if necessary
            if ds_raw[self.timevar.value].dtype=='O':
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
            _ds = pmv.select_time_range(self.ds_raw, time_var=self.timevar.value,
                                        start_time=self.date_range.value[0], end_time=self.date_range.value[1])
            kwargs = {}
            if 'year' in self.time_cond_args:
                kwargs['years'] = self.year_selection.value
                kwargs['year_range'] = self.year_range.value
            if 'month' in self.time_cond_args:
                kwargs['months'] = self.month_selection.value
                kwargs['month_range'] = self.month_range.value
            if 'dayofyear' in self.time_cond_args:
                kwargs['daysofyear'] = self.dayofyear_selection.value
                kwargs['dayofyear_range'] = self.dayofyear_range.value
            if 'hour' in self.time_cond_args:
                kwargs['hours'] = self.hour_selection.value
                kwargs['hour_range'] = self.hour_range.value
            _ds = pmv.select_time_cond(_ds, time_var=self.timevar.value, **kwargs)

            if self.poly is not None:
                _ds = pmv.select_spatial(_ds, boundary=self.poly, invert=self.selection_type.value)
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
        self.ds = pmv.resample_time(self.ds, timevar=self.timevar.value,
                                    time_quantity=self.rs_time_quantity.value, time_unit=self.rs_time_unit.value)
        self.status_text = "Dataset resampled"

    @try_catch(msg="Aggregation failed.")
    @param.depends("rs_space.value", watch=True)
    def resample_space(self):
        self.status_text = "Calculating..."
        self.ds = pmv.coarsen_dataset(self.ds, n_window={self.latvar.value:self.space_coarsen_factor.value,
                                                        self.lonvar.value:self.space_coarsen_factor.value})
        self.status_text = "Aggregation completed."

    @try_catch()
    @param.depends("status_text", watch=True)
    def update_status_view(self):
        self.alert.object = self.status_text

    @try_catch()
    @param.depends("ds", "update_varnames.value", "poly", watch=True)
    def update_plot_view(self):
        if all([self.timevar.value, self.latvar.value, self.lonvar.value, self.zvar.value]):
            self.status_text = "Creating plot"
            width = 500
            ds_plot = plot_gridded_data(self.ds, x=self.lonvar.value, y=self.latvar.value, z=self.zvar.value,
                                        time=self.timevar.value).opts(
                frame_width=width,
            )
            #.opts(frame_width=width)
            if self.poly is not None:
                ds_plot = ds_plot * gv.Path(self.poly).opts(line_color='k', line_width=2)
            plot = pn.pane.HoloViews(ds_plot)
            widget = plot.widget_box.objects[0]
            widget.width = width
            widget.align = 'center'
            # widget.orientation = "vertical"
            # label = widget.name
            # print(repr(widget))
            # print(dir(widget))
            ds_ts_plot = plot_avg_timeseries(self.ds, x=self.lonvar.value, y=self.latvar.value,
                                z=self.zvar.value, time=self.timevar.value)#.opts(width=width)
            # vertical_label = pn.pane.HTML()
            # @pn.depends(widget, watch=True)
            # def update_widget_label(val):
            #     vertical_label.object = f"<p style='writing-mode: vertical-rl; text-orientation: mixed;'>{label}: {val}</p>"


            # vertical_label = pn.pane.HTML(f"<p style='writing-mode: vertical-rl; text-orientation: mixed;'>{label}</p>")

            ts_plot = pn.pane.HoloViews(ds_ts_plot, sizing_mode="stretch_both")
            # figs_with_widget = pn.Column(pn.Card(pn.Column(plot, widget), align='center'), pn.Card(ts_plot),
                                         # pn.Card(self.ds)
                                         # )
            # figs_with_widget = pn.Card(plot, widget, ts_plot, self.ds, background='whitesmoke')
            self.plot_col.objects = [plot, widget, ds_ts_plot]
            # self.plot_col.objects = [pn.Row(plot, widget), ds_ts_plot]
            # self.ts_pane.object = ds_ts_plot
            self.ds_pane.object = self.ds
            # self.ts_widget = widget


            self.figs_with_widget[:] = [
                ("Charts", self.plot_col),
                ("Data", self.ds_pane),
                ]

            self.status_text = "Plot created!"

        else:
            self.status_text = 'Please specify variable names'

    @try_catch()
    @param.depends("calculate_stats.value", watch=True)
    def groupby_apply(self):
        self.status_text = "Calculating..."

        select_list = self.group_selector.value

        # Check if grouping by polygon
        if 'polygon' in select_list:
            time_list = select_list.copy()
            time_list.remove('polygon')
            poly = self.poly.reset_index()
            result = pmv.groupby_poly_time(vector_data=poly,
                                            vector_var='index',
                                            ds=self.ds,
                                            ds_var=self.zvar.value,
                                            latvar=self.latvar.value,
                                            lonvar=self.lonvar.value,
                                            timevar=self.timevar.value,
                                            groupby_vars=time_list)
            self.stats = result

        else:
            result = pmv.groupby_multi_time(ds=self.ds,
                                            var=self.zvar.value,
                                            time=self.timevar.value,
                                            groupby_vars=select_list)
            result = result.to_dataframe().reorder_levels(select_list).sort_index()
            result = result[['count', 'mean', 'std', 'min', '25%', '50%', '75%', 'max']]
            self.stats = result
            # self.stats_widget.value = result

        self.status_text = "Calculations completed"

        self.save_stats_widgets.objects = [pn.Row(self.stats_fname, self.save_stats), self.stats]

    @try_catch(msg="File couldn't be saved.")
    @param.depends("save_stats.value", watch=True)
    def save_stats_results(self):
        outfile = Path(self.stats_fname.value).resolve()
        self.stats.to_csv(outfile)
        self.status_text = f'File saved to: {outfile}'


    @try_catch(msg="File couldn't be saved.")
    @param.depends("save_ds.value", watch=True)
    def save_dataset(self):
        outfile = Path(self.output_fname.value).resolve()
        self.ds.to_netcdf(outfile)
        self.status_text = f'File saved to: {outfile}'


if __name__ == "__main__" or __name__.startswith("bokeh"):
    config.extension('tabulator', url="gridded_data_explorer_app")
    viewer = GriddedDataExplorer()
    viewer.sidebar.servable(area="sidebar")
    viewer.figs_with_widget.servable()
    # viewer.ts_widget.servable()
    # viewer.plot_col.servable()
    # viewer.ts_pane.servable()
    # viewer.ds_pane.servable()

    viewer.view.servable()
    # template = pn.template.FastGridTemplate(
    #     theme_toggle=False,
    #     prevent_collision=True,
    #     save_layout=True,
    #     # pylint: disable=line-too-long
    # )
    # template.sidebar[:] = [viewer.sidebar]
    #
    # # template.main[:, 0:1] = viewer.ts_widget
    # template.main[:, 1:4] = viewer.plot_pane
    # template.main[:, 4:7] = viewer.ts_pane
    # # template.main[:, 7:10] = viewer.ds_pane
    #
    # # template.main[:, 0:6] = viewer.figs_with_widget
    # print(viewer.view)
    # # template.main[:, 6:12] = viewer.view
    # template.servable()
