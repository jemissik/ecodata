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

# %% [markdown]
# # Tracks explorer GUI
#
# - Read in track data 
# - Exploratory plot of track data 
# - Save extent as geojson
#
# To do:
# - Add buffer for tracks extent 
# - Add option for envelope or hull 
#
#

# %%
import geopandas as gpd 
import pymovebank as pmv 
import xarray as xr
import hvplot.xarray  # noqa
import hvplot.pandas  # noqa
import spatialpandas as spd
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

pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")


# %%
class TracksExplorer(param.Parameterized):
    # file_selector = pn.widgets.FileSelector(str(Path.cwd()))
    # load_tracks_button = pn.widgets.Button(name='Load data', button_type='primary')
    # tracksfile = pn.widgets.TextInput(name='Track file', placeholder='Select a file...')

    
    file_selector = param.ClassSelector(class_=pn.widgets.Widget, 
                                        default =pn.widgets.FileSelector(str(Path.cwd()))) #TODO change default
    load_tracks_button = param.ClassSelector(class_=pn.widgets.Widget,
                                             default=pn.widgets.Button(name='Load data', button_type='primary'))
    tracksfile = param.ClassSelector(class_=pn.widgets.Widget, default=pn.widgets.TextInput(name='Track file', placeholder='Select a file...'))

    tracks = param.ClassSelector(class_=gpd.GeoDataFrame, precedence=-1)
    tracks_extent = param.ClassSelector(class_=gpd.GeoDataFrame, precedence=-1)
    save_tracks_extent_button = pn.widgets.Button(name='Save extent', button_type='primary')
    
    status_text = ' '

    # widgets = pn.WidgetBox(
    #     file_selector,
    #     tracksfile,
    #     load_tracks_button, save_tracks_extent_button
    # )
    widgets = pn.WidgetBox()

    # view = pn.Column(
    #     pn.pane.Markdown("## Select file to plot!"),
    #     # file_selector,
    #     widgets, 
    #     sizing_mode="stretch_both"
    # )
    view = None
    
    def __init__(self, **params):
        super().__init__(**params)
        
        # self.file_selector.name = 'Track file selector'
        self.widgets = pn.WidgetBox(self.file_selector, 
                                    self.tracksfile, 
                                    self.load_tracks_button, 
                                    self.save_tracks_extent_button)
        self.view = pn.Column(
        pn.pane.Markdown("## Select file to plot!"),
        self.widgets, 
        sizing_mode="stretch_both")
        
        
    @param.depends("file_selector.value", watch=True)
    def get_tracksfile(self):
        if len(self.file_selector.value) > 0:
            self.tracksfile.value = self.file_selector.value[0]
        
    @param.depends("load_tracks_button.value", watch=True)#depends on load_tracks_button
    def load_data(self):
        self.tracks = pmv.read_track_data(self.tracksfile.value)
        self.status_text = str(list(self.tracks))
        
    @param.depends("tracks", watch=True)
    def get_tracks_extent(self):
        extent = self.tracks.dissolve().convex_hull
        self.tracks_extent = gpd.GeoDataFrame(geometry=extent)
        
    @param.depends("save_tracks_extent_button.value", watch=True)
    def save_tracks_extent(self):
        outfile = 'tracks_extent.geojson'
        #TODO allow filepath selector 
        #TODO check that tracks/extent exists 
        self.tracks_extent.to_file(outfile, driver='GeoJSON')
        self.status_text = (f'file saved to: {outfile}')
    # @param.depends("tracks")
    # def get_plot(self):
    #     plot = (self.tracks.hvplot.points('location_long', 'location_lat', geo=True, 
    #                                      tiles='StamenTerrain', datashade=True, project=True, 
    #                                      hover=False, cmap='fire').opts(frame_width=500)
    #             * self.tracks_extent.hvplot(alpha=0.2, geo=True, project=True))
    #     return plot
    #     # if isinstance(ds, xr.Dataset):
        #     vals = ds.time.values
        #     options = {label: val for label, val in zip(pd.to_datetime(vals), vals)}
        #     time_slider = pn.widgets.DiscreteSlider(options=options, name="Date Time Slider")
        #     fig = pn.bind(get_plot_t2m, ds=ds, time=time_slider)
        #     return fig, time_slider
        # if isinstance(ds, gpd.GeoDataFrame):
        #     return get_plot_shp(shp=ds), None   
        
    @param.depends("tracks", watch=True)
    def update_view(self):
        self.status_text = "Creating plot..."
        plot = pn.pane.HoloViews(self.tracks.hvplot.points('location_long', 'location_lat', geo=True, 
                                    tiles='StamenTerrain', datashade=True, project=True, 
                                    hover=False, cmap='fire').opts(responsive=True)
         * self.tracks_extent.hvplot(alpha=0.2, geo=True, project=True).opts(responsive=True, frame_height=500, frame_width=500))
        # plot = pn.pane.HoloViews(self.tracks.hvplot.points('location_long', 'location_lat', 
        #                             tiles='StamenTerrain', datashade=True,
        #                             hover=False, cmap='fire')
        #  * self.tracks_extent.hvplot(alpha=0.2), 
        #  sizing_mode="stretch_both")
        self.status_text = "Plot created!"
        self.view[:] = [
            pn.Row(plot, width_policy="max"), self.widgets, 
            pn.pane.Alert(self.status_text)
            # pn.Column()
        ]
        


# %%
intro_pane = pn.pane.Markdown("# Welcome to the Movebank Data Magician!")

# %%
t = TracksExplorer()
pn.Row(t.view)#.servable(title="✨🪄✨Movebank Data Magician✨🪄✨ 🐻 🐺 🦅 🦌 🐉 🦖")

# %%
tabs = pn.Tabs(
    ('Welcome', intro_pane),
    ('Tracks Explorer', t.view),
    tabs_location = 'left', 
    dynamic=True
).servable(title="✨🪄✨Movebank Data Magician✨🪄✨ 🐻 🐺 🦅 🦌 🐉 🦖")

# %%
tabs

# %%

# %%
