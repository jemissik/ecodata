# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.14.0
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %%
import geopandas as gpd 
import pymovebank as pmv 
import xarray as xr
import hvplot.xarray  # noqa
import hvplot.pandas  # noqa
import spatialpandas as spd
import numpy as np
import panel as pn
import pandas as pd
import panel.widgets as pnw
import holoviews as hv
import time
import param
import cartopy.crs as ccrs
from pyproj.crs import CRS
from io import BytesIO
import datetime as dt 
from pathlib import Path

from holoviews.operation.datashader import datashade, shade, dynspread, spread


pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")

# %%
# def get_plot_t2m(ds, time):
#     return ds.loc[dict(time=time)].hvplot(x='longitude', y='latitude', z = 't2m', cmap='coolwarm')

# def get_plot_shp(shp):
#     return shp.hvplot(alpha=0.5)
     

# class SelectorViewer(param.Parameterized):
#     file_in = pn.widgets.FileSelector()
    
#     # start_time = param.Date(dt.datetime.fromisoformat('2008-02-01 05:00'))
#     # end_time = param.Date(dt.datetime.fromisoformat('2008-05-01 05:00'))
    
#     action = param.Action(lambda x: x.param.trigger('action'), label='Create Figure!')
    
#     widgets = param.ClassSelector(class_=pn.Column)
    
#     def __init__(self, *args, **params):
#         self.view = None
#         self.ds = xr.Dataset()
#         super().__init__(*args, **params)

#         self._init_view()
    
    
#     def load_data(self, file_in):
#         file_in = Path(file_in)
#         if file_in.suffix.lower() == ".nc":
#             return xr.load_dataset(file_in)
#         if file_in.suffix.lower() == ".shp":
#             return gpd.read_file(file_in)

#     def _init_view(self):
#         info = """## Select file to plot! ##### http://localhost:5006/subsetter_gui"""
        
#         self.widgets = pn.Column(
#             self.file_in,
#             # self.param.start_time,
#             # self.param.end_time,
#             self.param.action,
#         )
        
#         self.view = pn.Row(
#             self.widgets, 
#             pn.pane.Markdown(info)
#         )
    
#     def get_plot(self, ds):
#         if isinstance(ds, xr.Dataset):
#             vals = ds.time.values
#             options = {label: val for label, val in zip(pd.to_datetime(vals), vals)}
#             time_slider = pn.widgets.DiscreteSlider(options=options, name="Date Time Slider")
#             fig = pn.bind(get_plot_t2m, ds=ds, time=time_slider)
#             return fig, time_slider
#         if isinstance(ds, gpd.GeoDataFrame):
#             return get_plot_shp(shp=ds), None        

#     @param.depends('action', watch=True)
#     def update_view(self):
#         file_in = self.file_in.value[0]
#         ds = self.load_data(file_in)
#         fig, time_widget = self.get_plot(ds)
              
        
#         action_button = self.widgets.pop(-1)
#         if isinstance(self.widgets[-1], pn.widgets.DiscreteSlider):
#             self.widgets.remove(self.widgets[-1])
        
#         if time_widget:
#             self.widgets.append(time_widget)
#         self.widgets.append(action_button)

#         self.view[:] = [
#             self.widgets, 
#             fig
#         ]
        

# %%
# selector = SelectorViewer(name='Selector App')
# pn.Row(selector.view).servable()

# %%
def get_plot_t2m(ds, time):
    return ds.loc[dict(time=time)].hvplot(x='longitude', y='latitude', z = 't2m', cmap='coolwarm')

def get_plot_shp(shp):
    return shp.hvplot(alpha=0.5)

class A:
    
    file_selector = pn.widgets.FileSelector()

    action = pn.widgets.Button(name='Create Figure!', button_type='primary')

    widgets = pn.Column(
        file_selector,
        action
    )

    view = pn.Row(
        widgets, 
        pn.pane.Markdown("## Select file to plot!")
    )
    
    def __init__(self):
        pn.bind(self.update_view, action=self.action, watch=True)

    @staticmethod
    def load_data(file_in):
        file_in = Path(file_in)
        if file_in.suffix.lower() == ".nc":
            return xr.load_dataset(file_in)
        if file_in.suffix.lower() == ".shp":
            return gpd.read_file(file_in)
    
    @staticmethod
    def get_plot(ds):
        if isinstance(ds, xr.Dataset):
            vals = ds.time.values
            options = {label: val for label, val in zip(pd.to_datetime(vals), vals)}
            time_slider = pn.widgets.DiscreteSlider(options=options, name="Date Time Slider")
            fig = pn.bind(get_plot_t2m, ds=ds, time=time_slider)
            return fig, time_slider
        if isinstance(ds, gpd.GeoDataFrame):
            return get_plot_shp(shp=ds), None   

    def update_view(self, action):
        file_in = self.file_selector.value[0]
        ds = self.load_data(file_in)
        fig, time_widget = self.get_plot(ds)


        action_button = self.widgets.pop(-1)
        if isinstance(self.widgets[-1], pn.widgets.DiscreteSlider):
            self.widgets.remove(self.widgets[-1])

        if time_widget:
            self.widgets.append(time_widget)
        self.widgets.append(action_button)

        self.view[:] = [
            self.widgets, 
            fig
        ]


# %%
a = A()

# %%
pn.Row(a.view).servable()

# %%
df = pd.DataFrame(dict(a=[1,2,3,4,5], b=[6,3,5,1,6], time=[5,6,2,7,5]))

# %%
pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")
hv.extension("bokeh")
pn.Row(df.hvplot(x='a', y='b',  groupby='time'))

# %%

# %%
