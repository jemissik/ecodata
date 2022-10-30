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


def plot_tracks_with_tiles(tracks, tiles='StamenTerrain', datashade=True, cmap='fire', c='r', marker='circle', alpha=0.3):
    """
    Hvplot map of tracks with background map tiles
    """

    plot = tracks.hvplot.points('location_long', 'location_lat', geo=True,
                                tiles=tiles, datashade=datashade, project=True,
                                hover=False, cmap=cmap, c=c, marker=marker, alpha=alpha).opts(responsive=True)
    return plot


def plot_gridded_data(ds, x='longitude', y='latitude', z='t2m', time='time', cmap='coolwarm'):
    return ds.hvplot(x=x, y=y, z=z, cmap=cmap, geo=True)