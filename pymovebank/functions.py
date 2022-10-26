"""Python functions for data subsetting and file conversion.
See the notebooks in the examples section for demos of how these are used."""

import glob
import fiona
import geopandas as gpd
import pandas as pd
import xarray as xr
import numpy as np
import rioxarray  # noqa
import matplotlib.pyplot as plt
import spatialpandas as spd
from pathlib import Path
from shapely.geometry import Polygon
from pyproj.crs import CRS
import cartopy.crs as ccrs


import warnings

warnings.filterwarnings("ignore", message="Geometry is in a geographic CRS")


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
    fileout : str
        Output filename where the netcdf will be written


    Returns
    -------
    xarray.DataArray
        DataArray of the
    """

    # Get a list of the tif files in the data directory
    filenames = glob.glob(str(Path(data_dir) / '*.tif'))

    # Create the time index from the filenames
    time = xr.Variable('time', time_index_from_filenames(filenames))

    # Concatenate to one dataset, and make sure it's sorted by time
    ds = xr.concat([xr.open_rasterio(f) for f in filenames], dim=time)
    ds = ds.sortby('time')

    # Save dataset to netcdf
    ds.to_netcdf(fileout)

    return ds



def time_index_from_filenames(filenames):
    '''Helper function to create a pandas DatetimeIndex from MODIS filenames
       Note: this currently only works for MODIS filenames in the format:
       MOD13A1.006__500m_16_days_NDVI_doy2021017_aid0001.tif'''
    return pd.DatetimeIndex([pd.to_datetime(f[-19:-12], format="%Y%j") for f in filenames])


def subset_data(
    filename,
    bbox=None,
    track_points=None,
    bounding_geom=None,
    boundary_type="rectangular",
    buffer=0,
    clip=False,
    outfile=None,
):
    """
    Subsets a spatial dataset to an area of interest.

    Allowable data formats are those supported by GeoPandas; for more information
    see `Reading spatial data with GeoPandas <https://geopandas.org/en/stable/docs/user_guide/io.html>`_

    There are three subsetting options:
        - **Specify a bounding box**: Provide coordinates for a bounding box.
          (Use the ``bbox`` argument.)
        - **Provide animal track data**: Provide a csv file of Movebank animal
          track data, and a boundary will be drawn that encompasses all of the
          track points.(Use the ``track_points`` argument).
        - **Provide another shapefile for subsetting**: A boundary will be drawn
          around the features in this shapefile. For example, you could a provide
          a shapefile with a bounding polygon for a region of interest. (Use the
          ``bounding_geom`` argument)


    If using ``track_points`` or ``bounding_geom``, you can also specify:
        - Whether the bounding shape should be rectangular, a convex hull, or an exact geometry (Use
          the ``boundary_type`` argument.)
        - A buffer size around the track points or shape of interest (Use the
          ``buffer`` argument.)


    The newly subsetted shapefile is returned as a GeoDataFrame, and is optionally
    written out to a new shapefile.

    Parameters
    ----------
    filename : str
        Path to data file to subset
    bbox : list or tuple, optional
        Bounding box coordinates for the subset. Should be specified in the format
        ``(long_min, lat_min, long_max, lat_max)``.
    track_points : str, optional
        Path to csv file with animal track points. Latitude and longitude must be
        labeled as "location-lat" and "location-long".
    bounding_geom : str, optional
        Path to shapefile with bounding geometry.
    boundary_type : str, optional
        Specifies whether the bounding shape should be rectangular (``boundary_type=
        'rectangular'``), convex hull(``boundary_type = 'convex_hull'``), or the exact bounding geometry
        (``boundary_type='mask'``). ``boundary_type='mask'`` can only be used if ``bounding_geom`` is provided.
        By default 'rectangular'.
    buffer : float, optional
        Buffer size around the track points or bounding geometry, relative to the
        extent of the track points or bounding geometry. By default 0. Note that
        using a buffer will slow down processing.
    clip : bool, optional
        Whether or not to clip the subsetted data to the specified boundary (i.e., cut off
        intersected features at the boundary edge). By default False.
    outfile : str, optional
        Path to write the subsetted .shp file, if specified. Use ``.shp.zip`` as the extension to write a zipped
        shapefile. If no path is specified, the subsetted data won't be written out to a file.

    Returns
    -------
    geopandas GeoDataFrame
        GeoDataFrame with the subsetted data
    geopandas GeoDataFrame
        GeoDataFrame with the bounding geometry


    .. todo::
        - Add option to subset from an exact boundary, rather than a convex hull
        - Add examples section
    """

    # Check that one and only one of the subsetting options was specified
    if sum([item is not None for item in [bbox, track_points, bounding_geom]]) != 1:
        raise TypeError(
            "subset_data: Must specify one and only one of the subsetting options bbox, track_points, or bounding_shp")

    # Check that if "mask" option was used, then bounding_geom is not None
    if boundary_type == "mask" and bounding_geom is None:
        raise TypeError("subset_data: bounding_geom must be provided if boundary_type is mask")

    dataset_crs = get_crs(filename)

    # Subset for bbox case
    if bbox is not None:
        gdf = gpd.read_file(filename, bbox=bbox)
        boundary = bbox2poly(bbox)

    # Subset for track_points and bounding_geom case
    else:

        # Get feature geometry for track_points case
        track_crs = "EPSG:4326"
        if track_points is not None:
            df = pd.read_csv(track_points)
            gdf_track = gpd.GeoDataFrame(
                df,
                geometry=gpd.points_from_xy(df["location-long"], df["location-lat"]),
                crs=track_crs,
            )
            feature_geom = gdf_track.dissolve()  # Dissolve points to a single geometry

        # Get feature geometry for bounding_geom case
        elif bounding_geom is not None:
            # Read shapefile
            gdf_features = gpd.read_file(bounding_geom)
            feature_geom = gdf_features.dissolve()  # Dissolve features to one geometry

        # Get boundary for envelope, convex hull, or mask
        if boundary_type == "rectangular":
            boundary = feature_geom.geometry.to_crs(dataset_crs).envelope
        elif boundary_type == "convex_hull":
            boundary = feature_geom.geometry.to_crs(dataset_crs).convex_hull
        elif boundary_type == "mask":
            boundary = feature_geom.to_crs(dataset_crs)

        # Adjust boundary with the buffer
        if buffer != 0:
            tot_bounds = boundary.geometry.total_bounds
            buffer_scale = max(
                [abs(tot_bounds[2] - tot_bounds[0]), abs(tot_bounds[3] - tot_bounds[1])]
            )
            boundary = boundary.buffer(buffer * buffer_scale)

        # Read and subset
        if boundary_type == "rectangular":
            gdf = gpd.read_file(filename, bbox=boundary)
        elif boundary_type == "convex_hull" or boundary_type == "mask":
            gdf = gpd.read_file(filename, mask=boundary)

    if clip:
        gdf = gdf.clip(boundary.to_crs(gdf.crs))

    # Write new data to file if output path was specified
    if outfile is not None:
        if Path(outfile).suffix == '.shp':
            Path(outfile).mkdir(exist_ok=True)
        gdf.to_file(outfile)

    return gdf, boundary


def plot_subset_interactive(subset, boundary, bounding_geom=None, track_points=None,
                            datashade_tracks=False, projection = ccrs.PlateCarree()):
    """
    Plots the results of the subset_data function in an interactive plot using
    hvplot/geoviews.

    Parameters
    ----------
    subset_data : geopandas.GeoDataFrame
        Subset data
    boundary : geopandas.GeoSeries
        Subsetting boundary
    bounding_geom : geopandas.GeoSeries, optional
        Bounding geometry used for subsetting, by default None
    track_points : geopandas.GeoDataFrame, optional
        Track points used for subsetting, by default None

    Returns
    -------
    holoviews.core.overlay.Overlay
        Figure showing the subset results, along with the bounding geometry or track points provided.
    """

    # Plot for boundary
    if isinstance(boundary, gpd.GeoSeries):
        _boundary = gpd.GeoDataFrame(geometry=boundary).to_crs(subset.crs)
    else:
        _boundary = boundary.to_crs(subset.crs)

    boundary_plot = _boundary.to_crs(subset.crs).hvplot(geo=True, alpha=0.15)

    # Check the geometry type of the subset
    subset_geom_type = set(pd.unique(subset.geom_type))

    # Use hvplot for points
    if subset_geom_type.issubset({'Point', 'MultiPoint'}):
        subset_plot = subset.hvplot.points(hover=False, geo=True,
                                 projection=projection)
    # Use geoviews for paths
    if subset_geom_type.issubset({'LineString', 'MultiLineString'}):
        subset_plot = gv.Path(subset).opts(projection=projection, color='k')
    # TODO: Add polygon option
    else:
        raise TypeError(
            "plot_subset_interactive: Geometry type of the subset is not supported."
        )

    plot = boundary_plot * subset_plot


    # Plot for bounding_geom
    if bounding_geom is not None:
        bounding_geom_plot = bounding_geom.to_crs(subset.crs).hvplot(geo=True, alpha=0.3)
        plot = bounding_geom_plot * plot

    #Plot for track points
    if track_points is not None:
        track_plot = track_points.hvplot.points('location-long', 'location-lat',
                                 hover=False, geo=True, datashade = datashade_tracks, dynspread=True,
                                 projection=projection, color = 'r', cmap='Reds', project=True)
        plot = plot * track_plot

    return plot


def plot_subset(subset_data, boundary, bounding_geom=None, track_points=None):
    """
    Plots the results of the subset_data function in a static plot using matplotlib.

    Parameters
    ----------
    subset_data : geopandas.GeoDataFrame
        Subset data
    boundary : geopandas.GeoSeries
        Subsetting boundary
    bounding_geom : geopandas.GeoSeries, optional
        Bounding geometry used for subsetting, by default None
    track_points : geopandas.GeoDataFrame, optional
        Track points used for subsetting, by default None

    Returns
    -------
    matplotlib.pyplot.figure
        Figure showing the subset results, along with the bounding geometry or track points provided.
    """
    plt.ioff()
    fig, ax = plt.subplots()
    boundary.plot(ax=ax, color='c', alpha=0.4)
    subset_data.plot(ax=ax, color='b', linewidth=0.75)
    if bounding_geom is not None:
        bounding_geom.to_crs(subset_data.crs).plot(ax=ax, color='r', alpha=0.4)
    if track_points is not None:
        track_points.to_crs(subset_data.crs).plot(ax = ax, color = 'r', marker='.', alpha = 0.4)
    return fig


def bbox2poly(bbox):
    long_min = bbox[0]
    lat_min = bbox[1]
    long_max = bbox[2]
    lat_max = bbox[3]

    polygon = Polygon(
        [
            [long_min, lat_min],
            [long_min, lat_max],
            [long_max, lat_max],
            [long_max, lat_min],
        ]
    )

    return gpd.GeoSeries(polygon, crs="EPSG:4326")


def read_track_data(filein, dissolve=False):
    # read track csv
    track_df = pd.read_csv(filein)
    track_gdf = gpd.GeoDataFrame(
        track_df,
        geometry=gpd.points_from_xy(
            track_df["location-long"], track_df["location-lat"]
        ),
        crs="EPSG:4326",
    )
    if dissolve:
        track_gdf = track_gdf.dissolve()
    return track_gdf


def get_extent(filepath):
    """
    Get the extent of a spatial dataset, without reading the dataset into memory.

    Parameters
    ----------
    filepath : str
        Path to dataset

    Returns
    -------
    _type_ # TODO
        Extent of dataset
    """
    with fiona.open(filepath) as f:
        extent = f.bounds
    return extent


def get_crs(filepath):
    """
    Get the coordinate reference system (crs) of a spatial dataset, without reading
    the dataset into memory.

    Parameters
    ----------
    filepath : str
        Path to dataset

    Returns
    -------
    _type_ # TODO
        crs of dataset
    """
    with fiona.Env():
        with fiona.open(filepath) as f:
            crs = f.crs["init"] if f.crs and "init" in f.crs else f.crs_wkt
    return crs


def get_file_info(filepath):
    """
    Get metadata from a spatial dataset, without reading the dataset into memory.

    Parameters
    ----------
    filepath : str
        Path to dataset

    Returns
    -------
    _type_ # TODO
        Dataset metadata
    """
    with fiona.open(filepath) as f:
        info = f.meta
    return info


def get_geometry(filepath):
    """
    Get geometry of a spatial dataset, without reading the dataset into memory.

    Parameters
    ----------
    filepath : str
        Path to dataset

    Returns
    -------
    _type_ # TODO
        Dataset geometry
    """
    with fiona.open(filepath) as f:
        geom = f.geometry
    return geom


def get_file_len(filepath):
    """
    Get the length (number of records) in a spatial dataset, without reading the
    dataset into memory.

    Parameters
    ----------
    filepath : str
        Path to dataset

    Returns
    -------
    _type_ # TODO
        length (number of records) in dataset
    """
    with fiona.open(filepath) as f:
        flen = len(f)
    return flen


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