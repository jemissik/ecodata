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

# %%
import sys
from pathlib import Path
sys.path.insert(0, str(Path("__file__").resolve().parent.parent / "scripts"))


# %%

# %%
from gridded_data_gui import GriddedDataExplorer
from tracks_explorer import TracksExplorer
from subsetter_gui_newest import Subsetter

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
from pymovebank.panel_utils import param_widget
from pymovebank.xr_tools import detect_varnames


# pn.extension(template='bootstrap', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")
pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")

# %%
# Welcome page 

intro_text = """
# Welcome to the Movement Data Explorer!
"""
intro_pane = pn.pane.Markdown(intro_text)

# %%
# Tracks explorer 
# tracks_explorer = TracksExplorer()
t = TracksExplorer()

# %%
gridded_data_explorer = GriddedDataExplorer()
# pn.Row(gridded_data_explorer.view).servable()

# %% [markdown]
#

# %%
subsetter = Subsetter()

# %%


tabs = pn.Tabs(
    ('Welcome', intro_pane),
    ('Tracks Explorer', t.view),
    ('Gridded Data Explorer', gridded_data_explorer.view),
    ('Subsetter', subsetter.view),
    tabs_location = 'left',
    dynamic=True
).servable(title="Movement Data Aggregator")

# %%
# intro_text = """
# # Welcome to the Movement Data Explorer!
# """
# intro_pane = pn.pane.Markdown(intro_text)

# t = TracksExplorer()


# pn.Tabs(
#     ('Welcome', intro_pane),
#     ('Tracks Explorer', t.view),
#     tabs_location = 'left',
#     dynamic=True
# ).servable(title="Movement Data Aggregator")
