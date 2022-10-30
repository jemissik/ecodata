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
from tkinter import Tk, filedialog

from holoviews.operation.datashader import datashade, shade, dynspread, spread

pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")

# %%
map_tile_options = [None,
                    'CartoDark',
                    'CartoLight',
                    'EsriImagery', 
                    'EsriNatGeo', 
                    'EsriReference',
                    'EsriTerrain', 
                    'EsriUSATopo', 
                    'OSM', 
                    'StamenTerrain', 
                    'StamenTerrainRetina', 
                    'StamenToner', 
                    'StamenTonerBackground', 
                    'StamenWatercolor']


# %%
def select_file():
    root = Tk()
    root.attributes('-topmost', True)
    root.withdraw()     
    f = filedialog.askopenfilename(multiple=False) 

    if f:
        return f
    
def select_output(initial_dir=None, initial_file=None, extension=None):
    root = Tk()
    root.attributes('-topmost', True)
    root.withdraw()     
    f = filedialog.asksaveasfilename(initialdir = initial_dir, 
                                     initialfile=initial_file, defaultextension=extension) 
    if f:
        return f


# %%
def param_widget(panel_widget):
    return param.ClassSelector(class_=pn.widgets.Widget, default=panel_widget)


# %%
class TracksExplorer(param.Parameterized):

    file_selector_button = param_widget(pn.widgets.Button(button_type='primary', name='Choose file'))
    load_tracks_button = param_widget(pn.widgets.Button(button_type='primary', name='Load data'))
    tracksfile = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Track file'))

    tracks = param.ClassSelector(class_=gpd.GeoDataFrame, precedence=-1)
    tracks_extent = param.ClassSelector(class_=gpd.GeoDataFrame, precedence=-1)
    
    output_file_button = param_widget(pn.widgets.Button(button_type='primary', name='Choose output file'))
    output_fname = param_widget(pn.widgets.TextInput(placeholder='Select a file...', 
                                                     value='tracks_extent.geojson',name='Output file'))
    save_tracks_extent_button = param_widget(pn.widgets.Button(name='Save extent', button_type='primary'))
    
    ds_checkbox = param_widget(pn.widgets.Checkbox(name='Datashade tracks', value=True))
    map_tile = param_widget(pn.widgets.Select(options=map_tile_options, value='StamenTerrain', name='Map tile'))

        
    status_text = param.String('Ready...')

    
    def __init__(self, **params):
        super().__init__(**params)
        
        # Reset names for panel widgets 
        self.file_selector_button.name = 'Choose file'
        self.load_tracks_button.name = 'Load data'
        self.tracksfile.name = 'Track file'
        self.output_fname.name = 'Output file'
        self.output_file_button.name = 'Choose output file'
        self.save_tracks_extent_button.name = 'Save extent'
        self.ds_checkbox.name = 'Datashade tracks'
        self.map_tile.name = 'Map tile'
        
        # Add widgets to widgetbox
        self.widgets = pn.WidgetBox(self.file_selector_button, 
                                    self.tracksfile, 
                                    self.load_tracks_button, 
                                    self.output_file_button,
                                    self.output_fname,
                                    self.save_tracks_extent_button)
        
        # Add view
        self.view = pn.Column(
                pn.pane.Markdown("## Select file to plot!"),
                self.widgets, 
                pn.pane.Alert(self.status_text),
                sizing_mode="stretch_both")
        
        
    @param.depends("file_selector_button.value", watch=True)
    def get_tracksfile(self):
        filename = select_file()
        self.tracksfile.value = filename
        
    @param.depends("load_tracks_button.value", watch=True)#depends on load_tracks_button
    def load_data(self):
        tracks = pmv.read_track_data(self.tracksfile.value)
        self.status_text = "Track file loaded"
        self.tracks = tracks
        
    @param.depends("tracks", watch=True)
    def get_tracks_extent(self):
        extent = self.tracks.dissolve().convex_hull
        self.tracks_extent = gpd.GeoDataFrame(geometry=extent)
        
    
    @param.depends("output_file_button.value", watch=True)
    def get_output_fname(self):
        downloads_dir = (Path.home() / "Downloads")
        if downloads_dir.exists():
            default_dir = downloads_dir
        else: 
            default_dir = Path.cwd()
        filename = select_output(initial_dir=default_dir, initial_file='tracks_extent.geojson', extension='.geojson')
        self.output_fname.value = filename
        
    @param.depends("save_tracks_extent_button.value", watch=True)
    def save_tracks_extent(self):
        outfile = Path(self.output_fname.value).resolve()
        #TODO allow filepath selector 
        #TODO check that tracks/extent exists 
        self.tracks_extent.to_file(outfile, driver='GeoJSON')
        self.status_text = (f'File saved to: {outfile}')
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
    
    @param.depends("status_text", watch=True)
    def update_status_view(self):
        self.view[2] = pn.pane.Alert(self.status_text)
        
    @param.depends("tracks", "ds_checkbox.value", "map_tile.value", watch=True)
    # @param.depends("tracks", watch=True)
    def update_view(self):
        self.status_text = "Creating plot..."
        # self.ds_checkbox
        plot = pn.pane.HoloViews(self.tracks.hvplot.points('location_long', 'location_lat', geo=True, 
                                    tiles=self.map_tile.value, datashade=self.ds_checkbox.value, project=True, 
                                    hover=False, cmap='fire', c='r', marker='circle', alpha=0.3).opts(responsive=True)
         * self.tracks_extent.hvplot(alpha=0.2, geo=True, project=True).opts(responsive=True, frame_height=800, frame_width=800))
        # plot = pn.pane.HoloViews(self.tracks.hvplot.points('location_long', 'location_lat', 
        #                             tiles='StamenTerrain', datashade=True,
        #                             hover=False, cmap='fire')
        #  * self.tracks_extent.hvplot(alpha=0.2), 
        #  sizing_mode="stretch_both")
        self.view[0] = pn.Column(pn.Row(self.ds_checkbox, self.map_tile), plot)

        # self.view[0] = plot

        self.status_text = "Plot created!"

        


# %%
intro_text = """
# Welcome to the Movebank Data Magician!
"""
intro_pane = pn.pane.Markdown(intro_text)

# %%
t = TracksExplorer()

# %%
tabs = pn.Tabs(
    ('Welcome', intro_pane),
    ('Tracks Explorer', t.view),
    tabs_location = 'left', 
    dynamic=True
).servable(title="✨🪄✨Movebank Data Magician✨🪄✨ 🐻 🐺 🦅 🦌 🐉 🦖")

# %%
tabs
