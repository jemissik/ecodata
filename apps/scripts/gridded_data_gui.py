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

from pymovebank.plotting import plot_gridded_data
from pymovebank.panel_utils import param_widget


pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")


# %%
def get_plot_raster(ds, time, z='t2m', cmap='coolwarm', x='longitude', y='latitude'):
    return ds.loc[dict(time=time)].hvplot(x='longitude', y='latitude', z = z, cmap=cmap)

def get_plot_shp(shp):
    return shp.hvplot(alpha=0.5)


# %%
class GriddedDataExplorer(param.Parameterized):

    # TODO replace with new file selector 
    filein = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Data file'))
    
    load_data_button = param_widget(pn.widgets.Button(button_type='primary', name='Load data'))

    status_text = param.String('Ready...')
    
    ds = param.ClassSelector(class_=xr.Dataset, precedence=-1)


    def __init__(self, **params):
        super().__init__(**params)
        
        # Reset names for panel widgets 
        self.filein.name = "File path"
        self.load_data_button.name = 'Load data'
               
        # Add widgets to widgetbox 
        self.widgets = pn.Column(
                self.filein,
                self.load_data_button,
                )
        
        #Add view
        self.view = pn.Column(
            pn.pane.Markdown("## Select file to plot!"),
            self.widgets, 
            pn.pane.Alert(self.status_text)           
            )

    
    @param.depends("load_data_button.value", watch=True)
    def load_data(self):
        if self.filein.value:
            self.status_text = "Loading data..."
            ds = xr.load_dataset(self.filein.value)
            self.status_text = "File loaded"
            self.ds = ds
        else:
            self.status_text = "File path must be selected first!"
    
        
    @param.depends("status_text", watch=True)
    def update_status_view(self):
        self.view[2] = pn.pane.Alert(self.status_text)

    @param.depends("ds", watch=True)
    def update_plot_view(self):
        self.status_text = "Creating plot"
        plot = pn.pane.HoloViews(plot_gridded_data(self.ds).opts(frame_width=500))
        fig_with_widget = pn.Column(plot, plot.widget_box)
        
        self.status_text = "Plot created!"

        self.view[0] = fig_with_widget

# %%
g = GriddedDataExplorer()
pn.Row(g.view).servable()
