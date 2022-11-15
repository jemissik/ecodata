"""
Operations and functions for xarray datasets (gridded environmental datasets)
"""

import pandas as pd
import numpy as np
import xarray as xr
import rioxarray  # noqa
from pyproj.crs import CRS
from pathlib import Path


def detect_varnames(ds):
    matched_vars = dict(timevar = None,
                    latvar = None,
                    lonvar = None)

    label_options = dict(time_options = ['time', 'timestamp', 'Time'],
                        lat_options = ['lat', 'latitude', 'Latitude'],
                        lon_options = ['lon', 'long', 'longitude', 'Longitude'])

    # Variables in dataset
    dataset_vars = set(list(ds.coords) + list(ds))
    unmatched_vars = set(dataset_vars)

    for variable, label_opt in zip(matched_vars, label_options):
        matched_var = dataset_vars.intersection(label_options[label_opt])
        if matched_var:
            matched_var = matched_var.pop()
            matched_vars[variable] = matched_var
            unmatched_vars.remove(matched_var)
    return matched_vars, dataset_vars, unmatched_vars


def get_time_res(ds, timevar='time'):
    """
    Detect time resolution of a dataset, calculated as the average diff of the time array

    Parameters
    ----------
    ds : xr.Dataset
        Dataset with a time dimension
    timevar : str, optional
        Label of the time dimension in the dataset, by default 'time'

    Returns
    -------
    pandas.Timedelta
        Time resolution of the dataset
    """
    return pd.Timedelta(ds[timevar].diff(dim='time').mean().values)


def thin_dataset(dataset, n_thin, outfile=None):
    """
    Thin a dataset by keeping the n-th value across the specified dimensions. Useful for applications such as plotting
    wind data where using the original resolution would result in a crowded and unreadable figure.

    Note: this function is a thin wrapper around xarray.Dataset.thin

    Parameters
    ----------
    dataset : str, Path, or xarray.Dataset
        File path to the dataset, or an xarray.Dataset
    n_thin : int or dict
        An integer value for thinning across all dimensions, or a dictionary with keys matching the dimensions of the
        dataset and the values specifying the thinning value for that dimension.
    outfile : str, optional
        Path to write the thinned .nc file, if specified. If no path is specified, the thinned dataset won't be written
        out to a file.

    Returns
    -------
    xarray.Dataset
        Thinned dataset
    """

    # Check if input is a dataset or the filepath to the dataset, and load dataset if necessary
    if isinstance(dataset, str) | isinstance(dataset, Path):
        ds = xr.load_dataset(dataset)
    elif isinstance(dataset, xr.Dataset):
        ds = dataset.copy()

    ds_thinned = ds.thin(n_thin)

    # Write thinned dataset to file if output path was specified
    if outfile is not None:
        ds_thinned.to_netcdf(outfile)

    return ds_thinned


def coarsen_dataset(dataset, n_window, boundary='trim', outfile=None, **kwargs):
    """
    Coarsen a dataset by performing block aggregation. Supports aggregation along
    multiple dimensions.

    Note: this function is a thin wrapper around xarray.Dataset.coarsen

    Parameters
    ----------
    dataset : str, Path, or xarray.Dataset
        File path to the dataset, or an xarray.Dataset
    n_window : dict
        Dictionary with keys matching the dimensions of the dataset and the values
        specifying the window size for that dimension.
    boundary: ({"trim", "exact", "pad"}, default: "trim")
        How to handle cases where the dimension size is not a multiple of the window size.
        If "trim", the excess is dropped. If "pad", the dataset will be padded with NaN.
        If "exact", an error will be raised.
    outfile : str, optional
        Path to write the .nc file for the new dataset, if specified. If no path is specified,
        the new dataset won't be written out to a file.
    **kwargs :
        Additional arguments to be passed to xarray.Dataset.coarsen

    Returns
    -------
    xarray.Dataset
        Coarsened dataset
    """

    # Check if input is a dataset or the filepath to the dataset, and load dataset if necessary
    if isinstance(dataset, str) | isinstance(dataset, Path):
        ds = xr.load_dataset(dataset)
    elif isinstance(dataset, xr.Dataset):
        ds = dataset.copy()

    ds_coarsen = ds.coarsen(n_window, boundary=boundary, **kwargs).mean()

    # Write thinned dataset to file if output path was specified
    if outfile is not None:
        ds_coarsen.to_netcdf(outfile)

    return ds_coarsen


def select_spatial(ds, boundary, invert=False, crs=None, **kwargs):
    """
    Selects a spatial area from a gridded dataset based on provided bounding geometry.

    .. note::
        This function is a thin wrapper around `rioxarray's clip function
        <https://corteva.github.io/rioxarray/latest/examples/clip_geom.html>`_.


    Parameters
    ----------
    ds : xarray.Dataset
        Gridded dataset
    boundary : geopandas.GeoDataFrame
        Bounding geometry
    invert : bool
        If True, data that falls within the bounding geometry will be masked rather than selected. By default False.
    crs : Any, optional
        CRS of the input gridded dataset. If CRS are not already included in the data file or otherwise specified here,
        EPSG:4326 will be used. Valid inputs are anything accepted by rasterio.crs.CRS.from_user_input.
    **kwargs :
        Additional arguments to be passed to rioxarray.raster_dataset.RasterDataset.clip

    Returns
    -------
    xarray.Dataset
        Dataset containing just the spatial area contained in the boundary


    .. todo::
        Test from_disk options for datasets that can't fit in memory
    """

    ds_subset = ds.copy()

    # Set crs to EPSG:4326 if crs aren't provided
    if crs is None and (ds.rio.crs is None or not ds.rio.crs.is_epsg_code):
        ds_subset = ds_subset.rio.write_crs("EPSG:4326")
    elif crs is not None:
        ds_subset = ds_subset.rio.write_crs(crs)

    # Transform the boundary to the raster dataset's coordinates if needed
    ds_crs = CRS.from_user_input(ds_subset.rio.crs)
    if boundary.crs != ds_crs:
        boundary_clip = boundary.to_crs(ds_crs).geometry
    else:
        boundary_clip = boundary.geometry

    # Clip the dataset
    ds_subset = ds_subset.rio.clip(boundary_clip, invert=invert, **kwargs)

    return ds_subset


def select_time_range(ds, time_var='time', start_time=None, end_time=None):
    """
    Create a subset of a dataset based on a time range.

    Parameters
    ----------
    ds : xarray.Dataset
        Gridded dataset
    time_var : str, optional
        Name of the time coordinate in the dataset. Defaults to 'time' if not specified.
    start_time : str or same type as the time coordinate in the dataset, optional
        Start time of the time range for selection. If not provided, the earliest time in the dataset will be used.
    end_time : str or same type as the time coordinate in the dataset, optional
        End time of the time range for selection. If not provided, the latest time in the dataset will be used.

    Returns
    -------
    xarray.Dataset
        Subset of the input dataset containing only data within the specified time range.
    """

    ds_subset = ds.sel({time_var: slice(start_time, end_time)})

    return ds_subset


def select_time_cond(ds,
                     time_var='time',
                     years=None,
                     months=None,
                     daysofyear=None,
                     hours=None,
                     year_range = None,
                     month_range = None,
                     dayofyear_range = None,
                     hour_range = None
                     ):
    """
    Create a subset of a dataset including certain years, months, days of year, or hours of day.

    Parameters
    ----------
    ds : xarray.Dataset
        Gridded dataset
    time_var : str, optional
        Name of the time coordinate in the dataset. Defaults to 'time' if not specified.
    years : list, optional
        List of years to be selected, by default None
    months : list, optional
        List of months to be selected, by default None
    daysofyear : list, optional
        List of julian days of year to be selected, by default None
    hours : list, optional
        List of hours of day to be selected, by default None
    year_range : list, optional
        Range of years to be selected, given as a list containing the start year and the end year (e.g., [2000,2004]).
        Both endpoints will be included in the selection. By default None.
    month_range : list, optional
        Range of months to be selected, given as a list containing the start month and the end month (e.g., [4,6]).
        Both endpoints will be included in the selection. By default None.
    dayofyear_range : list, optional
        Range of julian days of year to be selected, given as a list containing the start day of year and the end day of
        year (e.g., [144,204]). Both endpoints will be included in the selection. By default None.
    hour_range : list, optional
        Range of hours of day to be selected, given as a list containing the start hour and the end hour (e.g., [11,13]).
        Both endpoints will be included in the selection. By default None.

    Returns
    -------
    xarray.Dataset
        Subset of the input dataset containing only data matching the specified time criteria.
    """

    # List to store boolean arrays for each filter criteria
    filters = []

    # Helper to combine selection list (e.g. years) with range list (e.g. year_range)
    def combine_selection(selection_list, range_list):
        selection_list = selection_list or []
        range_array = np.arange(range_list[0], range_list[1]+1) if range_list else []
        return np.union1d(selection_list, range_array)

    # Create filters for each variable (year, month, dayofyear, hour)
    variables = [(years, year_range), (months, month_range), (daysofyear, dayofyear_range), (hours, hour_range)]
    var_strs = ["year", "month", "dayofyear", "hour"]
    for (var0, var1), var_str in zip(variables, var_strs):
        if var0 or var1:
            selection = combine_selection(var0, var1)
            filters.append(getattr(ds[time_var].dt, var_str).isin(selection).values)

    # Combine all time filters to one boolean array
    time_selection = np.all(filters, axis=0)

    # Apply time filter
    ds_subset = ds.sel({time_var:time_selection})

    return ds_subset


def resample_time(ds, timevar='time', time_quantity=1, time_unit='day', interp_irreg=False):
    """
    Convert a dataset to a new time resolution. Uses interpolation for upsampling and mean for downsampling.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset to sesample
    timevar : str, optional
        Label of time variable in the dataset, by default 'time'
    time_quantity : int, optional
        Quantity of time for the new time resolution, by default 1
    time_unit : str, optional
        Unit of time for the new time resolution, by default 'day'
    interp_irreg : bool, optional
        Determines how downsampling should happen in cases where the new time resolution is not an even multiple
        of the time resolution of the input dataset. If true, the dataset will be interpolated to the greatest common
        timedelta before downsampling is performed. By default False.

    Returns
    -------
    xarray.Dataset
        Resampled dataset
    """

    resample_freq = pd.Timedelta(time_quantity, time_unit)
    ds_freq = get_time_res(ds)

    if resample_freq == ds_freq:
        ds_resampled = ds

    # Use interpolation for upsampling
    elif resample_freq < ds_freq:
        ds_resampled = ds.resample({timevar: resample_freq}).interpolate()

    # Use mean for downsampling
    elif resample_freq > ds_freq:
        if interp_irreg and ((resample_freq % ds_freq) != pd.Timedelta(0)):
            #Find greatest common divisor of the two time frequencies
            gcd = np.gcd(resample_freq, ds_freq)
            ds_resampled = ds.resample({timevar: gcd}).interpolate().resample({timevar: resample_freq}).mean()
        else:
            ds_resampled = ds.resample({timevar: resample_freq}).mean()

    return ds_resampled


def groupby_multi_time(ds, var, time='time', groupby_vars=None):
    """
    Groupby stats for multiple time groupings.
    Returns a dataset with summary statistics (count, min, max, mean, std, quantiles).

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset including a time coordinate
    var : str
        Variable in the dataset to calculate statistics for
    time : str, optional
        Name of the time dimension in the dataset, by default 'time'
    groupby_vars : list, optional
        List of time groupings to group by. Valid grouping variables include:
        ('year', 'month', 'dayofyear', 'hour'). Order or variables in the list determines
        the groupig order.

    Returns
    -------
    xarray.Dataset
        Dataset including summary statistics for the variable of interest.
    """

    da = ds[var]

    grouped_index = pd.MultiIndex.from_arrays(
        [getattr(da[time].dt, groupby_var).values for groupby_var in groupby_vars], names=groupby_vars)

    da.coords['grouped_time_index'] = ('time', grouped_index)

    grouped = da.groupby('grouped_time_index')

    result = xr.Dataset({
        'count': grouped.count(...),
        'mean':grouped.mean(...),
        'std':grouped.std(...),
        'min':grouped.min(...),
        '25%':grouped.quantile(0.25, dim=...).drop_vars('quantile'),
        '50%':grouped.quantile(0.50, dim=...).drop_vars('quantile'),
        '75%':grouped.quantile(0.75, dim=...).drop_vars('quantile'),
        'max':grouped.max(...),
                         })
    return result
