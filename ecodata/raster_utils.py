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


def geotif2nc(data_dir, fileout, fileFormat):
    """
    Convert a stack of geotif files to an xarray object and saves to a netcdf file. Returns the xarray Dataset.
    Note: Currently only set up to handle MODIS geotif files.

    Parameters
    ----------
    data_dir : str or pathlib.Path
        Directory containing the tif files
    fileout : str or pathlib.Path
        Output filename where the netcdf will be written
    fileFormat : str (added this)
        Holds the file format that is used to extract date

    Returns
    -------
    xarray.DataArray
        DataArray of the converted geotif data
    """
    # print("In the method", flush=True)
    filenamesTIF = []
    
    filenamesTIF = [str(f) for f in Path(data_dir).glob("*.tif")]

    time = xr.Variable("time", time_index_from_filenames(filenamesTIF, fileFormat, fileout))
    
    ds = xr.concat([rioxarray.open_rasterio(f) for f in filenamesTIF], dim=time)
    ds = ds.sortby("time")
    ds.to_netcdf(fileout)
    
    return ds

def time_index_from_filenames(filenames,  fileFormat):
    
    """
    Helper function to create a pandas DatetimeIndex from MODIS filenames
    Note: this is a specific test example that currently only works for MODIS filenames in the format:
    MOD13A1.006__500m_16_days_NDVI_doy2021017_aid0001.tif.

    .. todo::
    - Needs to extract the time as well

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
        # fileNameSplit = file_path.split("\\")
        if(file_path.count('/') > 0):
            fileNameSplit = file_path.split("/")
        elif(file_path.count('\\') > 0):
            fileNameSplit = file_path.split("\\")
        # fileNameSplit = file_path.split("/") #this doesn't work?
        finalFiles.append(fileNameSplit[len(fileNameSplit) - 1])
        # result_label.config(text=f"Selected File: {fileNames}")
        
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
                print(actualYear)
                # dateString += str(actualYear)
                finalYear = str(actualYear)
            elif file_format.count('#') == 2:
                if int(year) >= 0 and int(year) <= 29:
                    year = "20" + year
                else:
                    year = "19" + year
                actualYear = datetime.strptime(year, "%Y")
                print(actualYear.strftime("%Y"))
                # adds tothe string
                # dateString += actualYear.strftime("%Y")
                finalYear = actualYear.strftime("%Y")

        if user_input.count('%') > 0:
            first_index = user_input.find('%')
            last_index = user_input.rfind('%')
            date = originalFileName[first_index:last_index+1]
            print(date)
            actualDay = datetime.strptime(date, "%j")
            if(user_input.count('%') == 3):
                # dateString += actualDay.strftime("-%m-%d")
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
            print(f"day is {day_str}")
            # dateString += day_str
            finalDate = "-"+day_str

        dateString = finalYear + finalMonth + finalDate
        dateTime.append(pd.to_datetime(dateString))

    def process_files():
        text_format = fileFormat
        if text_format:
            if finalFiles:
                for index, value in enumerate(finalFiles):
                    # print(f"Index {index}: {value}")
                    extract_date_time(value)
                # print(dateTime)
            else:
                print("No files selected.")
        else:
            print("Fill in the text format field.")
            
    process_files()
    return pd.DatetimeIndex(dateTime)