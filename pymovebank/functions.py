"""Python functions for data subsetting and file conversion.
See the notebooks in the examples section for demos of how these are used."""

import glob
import fiona
import geopandas as gpd
import pandas as pd
import xarray as xr
import matplotlib.pyplot as plt
from pathlib import Path
from shapely.geometry import Polygon

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
        - Whether the bounding shape should be rectangular or a convex hull (Use
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
        'rectangular'``) or convex hull(``boundary_type = 'convex_hull'``), by
        default 'rectangular'
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
    assert (
        sum([item is not None for item in [bbox, track_points, bounding_geom]]) == 1
    ), "subset_data: Must specify one and only one of the subsetting options bbox, track_points, or bounding_shp "

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

def plot_subset(subset_data, boundary, bounding_geom=None, track_points=None):
    """
    Plots the results of the subset_data function.

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


def thin_dataset(dataset, n_thin):
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

    return ds_thinned
