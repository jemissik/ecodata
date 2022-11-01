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
import numpy as np
import panel as pn
import pandas as pd
import panel.widgets as pnw
import holoviews as hv
import time
import param
import cartopy.crs as ccrs
from pyproj.crs import CRS
import datetime as dt
from pathlib import Path

from holoviews.operation.datashader import datashade, shade, dynspread, spread

from pymovebank.plotting import plot_gridded_data, plot_avg_timeseries
from pymovebank.panel_utils import param_widget, detect_varnames


pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")


# %%
def get_plot_shp(shp):
    return shp.hvplot(alpha=0.5)


# %%
class GriddedDataExplorer(param.Parameterized):

    # TODO replace with new file selector 
    filein = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Data file'))
    load_data_button = param_widget(pn.widgets.Button(button_type='primary', name='Load data', align='end'))
    timevar = param_widget(pn.widgets.Select(options=[], name='Time'))
    latvar = param_widget(pn.widgets.Select(options=[], name='Latitude'))
    lonvar = param_widget(pn.widgets.Select(options=[], name='Longitude'))
    zvar = param_widget(pn.widgets.Select(options=[], name='Variable of interest'))
    update_varnames = param_widget(pn.widgets.Button(button_type='primary', name='Update variable names', align='end'))

    polyfile = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Polygon file'))
    load_polyfile = param_widget(pn.widgets.Button(button_type='primary', name='Load data'))
    

    status_text = param.String('Ready...')
    
    ds_raw = param.ClassSelector(class_=xr.Dataset, precedence=-1)
    ds = param.ClassSelector(class_=xr.Dataset, precedence=-1)
    poly = param.ClassSelector(class_=gpd.GeoDataFrame, precedence=-1)
    
    date_range = param_widget(pn.widgets.DateRangeSlider(name="Date range", 
                                                         start=dt.date.today() - dt.timedelta(1), end=dt.date.today()))
    
    # Polygon selection
    poly_selection_dict = {'Select within boundary':False, 'Mask within boundary':True}
    selection_type = param_widget(pn.widgets.RadioBoxGroup(name='Selection options', options = poly_selection_dict))
    
    update_filters = param_widget(pn.widgets.Button(button_type='primary', name='Update filters', align='end'))
    revert_filters = param_widget(pn.widgets.Button(button_type='primary', name='Revert filters', align='end'))

    # Save dataset
    output_fname = param_widget(pn.widgets.TextInput(placeholder='Output filename...', 
                                                     value='processed_dataset.nc',name='Output file'))
    save_ds = param_widget(pn.widgets.Button(name='Save dataset', button_type='primary', align='end'))

    def __init__(self, **params):
        super().__init__(**params)
        
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
        self.selection_type.name = 'Polygon selection options'
        self.update_filters.name = 'Update filters'
        self.revert_filters.name = 'Revert filters'
        self.output_fname.name = 'Output file'
        self.save_ds.name = "Save dataset"
               
        # Widget groups
        self.filein_widgets = pn.Row(self.filein, self.load_data_button)
        self.varname_widgets = pn.Row(self.timevar, self.latvar, self.lonvar, self.zvar, self.update_varnames)       
        self.file_input_card = pn.Card(self.filein_widgets, self.varname_widgets, 
                                       title="Input environmental dataset file")

        self.selection_options = pn.Card(self.date_range, self.selection_type, 
                                              pn.Row(self.update_filters, self.revert_filters), title="Selection options")
        self.polyfile_widgets = pn.Card(self.polyfile, self.load_polyfile, title='Input polygon file')
        
        self.outfile_widgets = pn.Card(pn.Row(self.output_fname, self.save_ds))
        #Add view
        self.view = pn.Column(
            pn.pane.Markdown("## Select file to plot!"),
            self.file_input_card,
            self.polyfile_widgets,
            self.selection_options,
            self.outfile_widgets,
            pn.Card(pn.pane.Alert(self.status_text), title='Status')           
            )

    @param.depends("update_varnames.value", watch=True)
    def update_selection_widgets(self):
        if self.ds_raw is not None:
            self.status_text = 'Updating widgets'
            self.date_range = pn.widgets.DateRangeSlider(name="Date range selection", 
                                                         start=self.ds_raw[self.timevar.value].min().values, 
                                                         end=self.ds_raw[self.timevar.value].max().values)
            
            # self.selection_options = pn.WidgetBox(self.date_range, self.selection_type, 
            #                                       pn.Row(self.update_filters, self.revert_filters))
            self.selection_options = pn.Card(self.date_range, self.selection_type, 
                                              pn.Row(self.update_filters, self.revert_filters), title="Selection options")
            self.view[3] = self.selection_options
          
    @param.depends("load_data_button.value", watch=True)
    def load_data(self):
        if self.filein.value:
            self.status_text = "Loading data..."
            ds_raw = xr.load_dataset(self.filein.value)
            self.ds_raw = ds_raw
            matched_vars, ds_vars, unmatched_vars = detect_varnames(ds_raw)
            self.timevar.options = list(ds_vars) 
            self.latvar.options = list(ds_vars) 
            self.lonvar.options = list(ds_vars)
            self.timevar.value = matched_vars['timevar']
            self.latvar.value = matched_vars['latvar']
            self.lonvar.value = matched_vars['lonvar']
            self.zvar.options = [None] + list(unmatched_vars) 
            self.zvar.value = None
            self.status_text = "File loaded"
            self.ds = ds_raw.copy()  
        else:
            self.status_text = "File path must be selected first!"        
    
    
    @param.depends("load_polyfile.value", watch=True)
    def load_poly_data(self):
        if self.polyfile.value:
            self.status_text = "Loading file..."
            poly = gpd.read_file(self.polyfile.value)
            self.status_text = "File loaded"
            self.poly = poly
        else:
            self.status_text = "File path must be selected first!"

        
    @param.depends("update_filters.value", watch=True)
    def update_ds(self):
        if self.ds_raw is not None:
            self.status_text = "Updating..."
            _ds = pmv.select_time_range(self.ds_raw, start_time=self.date_range.value[0], end_time=self.date_range.value[1])
            if self.poly is not None:
                _ds = pmv.select_spatial(_ds, boundary=self.poly, invert=self.selection_type.value)
            self.ds = _ds
            self.status_text = "Applied updated filters"
        else:
            self.status_text = "No dataset available"
            
    @param.depends("revert_filters.value", watch=True) 
    def revert_ds(self):
        # self.date_range.values = (self.ds_raw.time.min().values, self.ds_raw.time.max().values)
        self.update_selection_widgets()
        if self.ds_raw is not None:
            self.ds = self.ds_raw.copy()
            self.status_text = "Filters reverted"
            
    @param.depends("status_text", watch=True)
    def update_status_view(self):
        self.view[-1] = pn.Card(pn.pane.Alert(self.status_text), title='Status')

    @param.depends("ds", "update_varnames.value", "poly", watch=True)
    def update_plot_view(self):
        if all([self.timevar.value, self.latvar.value, self.lonvar.value, self.zvar.value]):
            self.status_text = "Creating plot"
            width = 500
            ds_plot = plot_gridded_data(self.ds, x=self.lonvar.value, y=self.latvar.value, z=self.zvar.value, 
                                        time=self.timevar.value).opts(frame_width=width)
            if self.poly is not None:
                ds_plot = ds_plot * gv.Polygons(self.poly).opts(fill_color=None)
            plot = pn.pane.HoloViews(ds_plot)
            widget = plot.widget_box.objects[0]
            widget.width = width
            widget.align = 'center'
            ts_plot = plot_avg_timeseries(self.ds, x=self.lonvar.value, y=self.latvar.value, 
                                          z=self.zvar.value, time=self.timevar.value ).opts(frame_width=width)
            figs_with_widget = pn.Row(plot, pn.Column(widget,ts_plot, align='center'), pn.Column(self.ds, background='whitesmoke'))
            
            self.status_text = "Plot created!"

            self.view[0] = figs_with_widget
        else:
            self.status_text = 'Please specify variable names'
    
    @param.depends("save_ds.value", watch=True)
    def save_dataset(self):
        outfile = Path(self.output_fname.value).resolve()
        self.ds.to_netcdf(outfile)
        self.status_text = f'File saved to: {outfile}'
            

# %%
g = GriddedDataExplorer()
pn.Row(g.view).servable()

# %%
