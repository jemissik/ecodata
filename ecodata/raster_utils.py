"""
Utilities for reading and converting raster data.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import rioxarray  # noqa
import xarray as xr



def grib2nc(filein, fileout):
    """
    Converts .grib files from ECMWF to .nc format.

    Parameters
    ----------
    filein : str
        .grib file to convert
    fileout : str
        Output filename where the .nc file will be written
    """

    # Read the .grib file using xarray and the cfgrib engine
    ds = xr.load_dataset(filein, engine="cfgrib")

    # Write the dataset to a netcdf file
    ds.to_netcdf(fileout)


def geotif2nc(data_dir, fileout):
    """
    Convert a stack of geotif files to an xarray object and saves to a netcdf file. Returns the xarray Dataset.
    Note: Currently only set up to handle MODIS geotif files.

    Parameters
    ----------
    data_dir : str or pathlib.Path
        Directory containing the tif files
    fileout : str or pathlib.Path
        Output filename where the netcdf will be written


    Returns
    -------
    xarray.DataArray
        DataArray of the converted geotif data
    """

    # Get a list of the tif files in the data directory
    filenames = [str(f) for f in Path(data_dir).glob("*.tif")]

    # Create the time index from the filenames
    time = xr.Variable("time", time_index_from_filenames(filenames))

    # Concatenate to one dataset, and make sure it's sorted by time
    ds = xr.concat([rioxarray.open_rasterio(f) for f in filenames], dim=time)
    ds = ds.sortby("time")

    # Save dataset to netcdf
    ds.to_netcdf(fileout)

    return ds


def time_index_from_filenames(filenames):
    """
    Helper function to create a pandas DatetimeIndex from MODIS filenames
    Note: this is a specific test example that currently only works for MODIS filenames in the format:
    MOD13A1.006__500m_16_days_NDVI_doy2021017_aid0001.tif.

    .. todo::
    - Needs to be generalized to take other filename formats.

    Parameters
    ----------
    filenames : list[str]
        List of .tif files to create the time index from

    Returns
    -------
    pandas.DatetimeIndex
        Time index parsed from the filenames
    """
    return pd.DatetimeIndex([pd.to_datetime(f[-19:-12], format="%Y%j") for f in filenames])