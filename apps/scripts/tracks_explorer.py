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

import pymovebank as pmv
from pymovebank.plotting import map_tile_options, plot_tracks_with_tiles
from pymovebank.panel_utils import select_file, select_output, param_widget

from holoviews.operation.datashader import datashade, shade, dynspread, spread

pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")


# %%
class TracksExplorer(param.Parameterized):

    file_selector_button = param_widget(pn.widgets.Button(button_type='primary', name='Choose file'))
    load_tracks_button = param_widget(pn.widgets.Button(button_type='primary', name='Load data'))
    tracksfile = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Track file'))

    tracks = param.ClassSelector(class_=gpd.GeoDataFrame, precedence=-1)
    tracks_extent = param.ClassSelector(class_=gpd.GeoDataFrame, precedence=-1)
    tracks_boundary_shape = param_widget(pn.widgets.Select(
        options={'Rectangular': 'rectangular', 'Convex hull': 'convex_hull'},
        value='rectangular', name='Boundary shape'))
    tracks_buffer = param_widget(pn.widgets.EditableFloatSlider(name='Buffer size',
                                                                start=0, end=1, step=0.01, value=0.1))
    boundary_update = param_widget(pn.widgets.Button(button_type='primary', value=False, name='Update boundary'))

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
        self.tracks_boundary_shape.name = 'Boundary shape'
        self.boundary_update.name = 'Create boundary'
        self.tracks_buffer.name = 'Buffer size'
        self.ds_checkbox.name = 'Datashade tracks'
        self.map_tile.name = 'Map tile'

        # Add widgets to widgetbox
        self.widgets = pn.WidgetBox(self.file_selector_button,
                                    self.tracksfile,
                                    self.load_tracks_button,
                                    self.tracks_boundary_shape,
                                    self.tracks_buffer,
                                    self.boundary_update,
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
        if self.tracksfile.value:
            self.status_text = "Loading data..."
            tracks = pmv.read_track_data(self.tracksfile.value)
            self.status_text = "Track file loaded"
            self.tracks_extent = pmv.get_tracks_extent(tracks, boundary_shape=self.tracks_boundary_shape.value,
                                                       buffer=self.tracks_buffer.value)
            self.tracks = tracks

        else:
            self.status_text = "File path must be selected first!"


    @param.depends("boundary_update.value", watch=True)
    def update_tracks_extent(self):
        self.tracks_extent = pmv.get_tracks_extent(self.tracks, boundary_shape=self.tracks_boundary_shape.value,
                                                       buffer=self.tracks_buffer.value)


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
        #TODO check that tracks/extent exists
        if self.tracks_extent is not None:
            self.tracks_extent.to_file(outfile, driver='GeoJSON')
            self.status_text = (f'File saved to: {outfile}')
        else:
            self.status_text = "Tracks data must be added before a tracks extent file can be saved!"

    @param.depends("status_text", watch=True)
    def update_status_view(self):
        self.view[2] = pn.pane.Alert(self.status_text)

    @param.depends("tracks", "boundary_update.value", "ds_checkbox.value", "map_tile.value", watch=True)
    def update_view(self):
        print("calling update view")
        self.status_text = "Creating plot..."
        plot = pn.pane.HoloViews(plot_tracks_with_tiles(self.tracks, tiles=self.map_tile.value,
                                                        datashade=self.ds_checkbox.value, cmap='fire', c='r',
                                                        marker='circle', alpha=0.3)
         * self.tracks_extent.hvplot(alpha=0.2, geo=True, project=True).opts(responsive=True, frame_height=800, frame_width=800,
                                                                             active_tools=['wheel_zoom']))

        self.view[0] = pn.Column(pn.Row(self.ds_checkbox, self.map_tile), plot)
        self.status_text = "Plot created!"




# %%
intro_text = """
# Welcome to the Movebank Data Explorer!
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
).servable(title="Movebank Data Aggregator")

# %%
tabs

# %%
