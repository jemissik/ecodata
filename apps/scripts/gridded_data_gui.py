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
from pymovebank.panel_utils import param_widget


pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")


# %%
def get_plot_shp(shp):
    return shp.hvplot(alpha=0.5)


# %%
class GriddedDataExplorer(param.Parameterized):

    # TODO replace with new file selector 
    filein = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Data file'))
    load_data_button = param_widget(pn.widgets.Button(button_type='primary', name='Load data'))

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
    
    update_filters = param_widget(pn.widgets.Button(button_type='primary', name='Update filters'))
    revert_filters = param_widget(pn.widgets.Button(button_type='primary', name='Revert filters'))


    def __init__(self, **params):
        super().__init__(**params)
        
        # Reset names for panel widgets 
        self.filein.name = "File path"
        self.load_data_button.name = 'Load data'

        self.polyfile.name = "Polygon file"
        self.load_polyfile.name = 'Load file'
        self.date_range.name = "Date range selection"
        self.selection_type.name = 'Polygon selection options'
        self.update_filters.name = 'Update filters'
        self.revert_filters.name = 'Revert filters'
               
        self.selection_options = pn.WidgetBox(self.date_range, self.selection_type, 
                                              pn.Row(self.update_filters, self.revert_filters))
        # Add widgets to widgetbox 
        self.widgets = pn.Column(
                pn.Row(self.filein, self.load_data_button),
                pn.Row(self.polyfile, self.load_polyfile),
                self.selection_options,
                )
        
        #Add view
        self.view = pn.Column(
            pn.pane.Markdown("## Select file to plot!"),
            self.widgets, 
            pn.pane.Alert(self.status_text)           
            )

    @param.depends("ds_raw", watch=True)
    def update_selection_widgets(self):
        if self.ds_raw is not None:
            self.date_range = pn.widgets.DateRangeSlider(name="Date range selection", 
                                                         start=self.ds_raw.time.min().values, 
                                                         end=self.ds_raw.time.max().values)
            
            self.selection_options = pn.WidgetBox(self.date_range, self.selection_type, 
                                                  pn.Row(self.update_filters, self.revert_filters))
            self.view[1] = pn.Column(
                pn.Row(self.filein, self.load_data_button),
                pn.Row(self.polyfile, self.load_polyfile),
                self.selection_options,
                )
          
    @param.depends("load_data_button.value", watch=True)
    def load_data(self):
        if self.filein.value:
            self.status_text = "Loading data..."
            ds_raw = xr.load_dataset(self.filein.value)
            self.status_text = "File loaded"
            self.ds_raw = ds_raw
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
        self.view[2] = pn.pane.Alert(self.status_text)

    @param.depends("ds", "poly", watch=True)
    def update_plot_view(self):
        self.status_text = "Creating plot"
        width = 500
        plot = pn.pane.HoloViews(plot_gridded_data(self.ds).opts(frame_width=width))
        widget = plot.widget_box.objects[0]
        widget.width = width
        widget.align = 'center'
        ts_plot = plot_avg_timeseries(self.ds).opts(frame_width=width)
        figs_with_widget = pn.Row(plot, pn.Column(widget,ts_plot, align='center'))
        
        self.status_text = "Plot created!"

        self.view[0] = figs_with_widget

# %%
g = GriddedDataExplorer()
pn.Row(g.view).servable()

# %%
