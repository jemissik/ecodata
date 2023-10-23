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


    Returns
    -------
    xarray.DataArray
        DataArray of the converted geotif data
    """
    print("In the method")
    tif_filenames = []
    
    # Iterate through the files in the directory
    for filename in os.listdir(data_dir):
        if filename.endswith(".tif"):
            # If the file has a ".tif" extension, add its name to the list
            tif_filenames.append(filename)
    
    # Print the list of ".tif" filenames
    for filename in tif_filenames:
        print(filename)

    time_index_from_filenames(tif_filenames, fileFormat, fileout);


def time_index_from_filenames(filenames,  fileFormat, outputFile):
    #array of file names. - filenames
    #fileFormat - users file format
    #output - file you write to.

    dateTime = []

    def extract_date_time(originalFileName):
        user_input = fileFormat #combac to this
        dateString = ""
        if user_input.count('#') > 0:
            first_index = user_input.find('#')
            last_index = user_input.rfind('#')
            year = originalFileName[first_index:last_index+1]
            if user_input.count('#') == 4:
                actualYear = datetime.strptime(year, "%Y").year
                print(actualYear)
                dateString += str(actualYear) + " "
            elif fileFormat.count('#') == 2:
                if int(year) >= 0 and int(year) <= 29:
                    year = "20" + year
                else:
                    year = "19" + year
                actualYear = datetime.strptime(year, "%Y")
                print(actualYear.strftime("%Y"))
                # adds tothe string
                dateString += actualYear.strftime("%Y")

        if user_input.count('%') > 0:
            first_index = user_input.find('%')
            last_index = user_input.rfind('%')
            date = originalFileName[first_index:last_index+1]
            actualDay = datetime.strptime(date, "%j")
            print(actualDay.strftime("%B %d"))
            dateString += actualDay.strftime("%B %d")

        if user_input.count('&') > 0:
            first_index = user_input.find('&')
            last_index = user_input.rfind('&')
            month_str = originalFileName[first_index:last_index+1]
            month_int = int(month_str)
            month_abbreviation = calendar.month_abbr[month_int]
            print(f"Month is {month_abbreviation}")
            dateString += month_abbreviation

        if user_input.count('$') > 0:
            first_index = user_input.find('$')
            last_index = user_input.rfind('$')
            day_str = originalFileName[first_index:last_index+1]
            print(f"day is {day_str}")
            dateString += day_str

        dateTime.append(dateString)

    def process_files():
        text_format = fileFormat
        if text_format:
            if filenames:
                for index, value in enumerate(filenames):

                    print(f"Index {index}: {value}")
                    extract_date_time(value)

                print(dateTime)
            else:
                print("No files selected.")
        else:
            print("Fill in the text format field.")



    