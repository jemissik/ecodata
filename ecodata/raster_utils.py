"""
Utilities for reading and converting raster data.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import rioxarray  # noqa
import xarray as xr
from datetime import datetime
import calendar
import os
import io

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


def geotif2nc(data_dir, fileout, filename_format):
    """
    Convert a stack of geotif files to an xarray object and saves to a netcdf file. Returns the xarray Dataset.
    Note: Currently only set up to handle MODIS geotif files.

    Parameters
    ----------
    data_dir : str or pathlib.Path
        Directory containing the tif files
    fileout : str or pathlib.Path
        Output filename where the netcdf will be written
    filename_format : str
        Format of the filenames, used to extract the time index
        #TODO needs details about how how this formatter needs to be specified, e.g.
        example MODIS filenames are specified as MOD13A1.006__500m_16_days_NDVI_doy####%%%_aid0001.tif
        explain which characters are used to specify the year, day, etc in the filename


    Returns
    -------
    xarray.DataArray
        DataArray of the converted geotif data
    """

    # Get a list of the tif files in the data directory
    filenames = [str(f) for f in Path(data_dir).glob("*.tif")]
    
    # Create the time index from the filenames
    time = xr.Variable("time", time_index_from_filenames(filenames, filename_format))

    # Concatenate to one dataset, and make sure it's sorted by time
    ds = xr.concat([rioxarray.open_rasterio(f) for f in filenames], dim=time)
    ds = ds.sortby("time")

    # Save dataset to netcdf
    ds.to_netcdf(fileout)

    return ds

def time_index_from_filenames(filenames,  fileFormat):

    """
    Helper function to create a pandas DatetimeIndex from filenames

    .. todo::
    - Needs explanation of how the fileFormat parameter works

    Parameters
    ----------
    filenames : list[str]
        List of .tif files to create the time index from
    fileFormart : str
        Used to find the placement of date in the filenames

    Returns
    -------
    pandas.DatetimeIndex
        Time index parsed from the filenames
    """

    finalFiles = []

    for file_path in filenames:
        fileNameSplit = file_path.split("\\")
        finalFiles.append(fileNameSplit[len(fileNameSplit) - 1])

    dateTime = []

    def extract_date_time(originalFileName):
        user_input = fileFormat
        dateString = ""
        finalDate = ""
        finalMonth = ""
        finalYear = ""
        finalTime = ""
        if user_input.count('#') > 0:
            first_index = user_input.find('#')
            last_index = user_input.rfind('#')
            year = originalFileName[first_index:last_index+1]
            if user_input.count('#') == 4:
                actualYear = datetime.strptime(year, "%Y").year
                finalYear = str(actualYear)
            elif user_input.count('#') == 2:
                if int(year) >= 0 and int(year) <= 29:
                    year = "20" + year
                else:
                    year = "19" + year
                actualYear = datetime.strptime(year, "%Y")
                finalYear = actualYear.strftime("%Y")

        if user_input.count('%') > 0:
            first_index = user_input.find('%')
            last_index = user_input.rfind('%')
            date = originalFileName[first_index:last_index+1]
            # print(date)
            actualDay = datetime.strptime(date, "%j")
            if(user_input.count('%') == 3):
                finalDate = actualDay.strftime("-%d")
                finalMonth = actualDay.strftime("-%m")

        if user_input.count('&') > 0:
            first_index = user_input.find('&')
            last_index = user_input.rfind('&')
            month_str = originalFileName[first_index:last_index+1]
            month_int = int(month_str)
            finalMonth = "-"+month_str

        if user_input.count('$') > 0:
            first_index = user_input.find('$')
            last_index = user_input.rfind('$')
            day_str = originalFileName[first_index:last_index+1]
            finalDate = "-"+day_str

        dateString = finalYear + finalMonth + finalDate
        dateTime.append(pd.to_datetime(dateString))

    def process_files():
        text_format = fileFormat
        if text_format:
            if finalFiles:
                for index, value in enumerate(finalFiles):
                    extract_date_time(value)
            else:
                print("No files selected.")
        else:
            print("Fill in the text format field.")

    process_files()
    return pd.DatetimeIndex(dateTime)
