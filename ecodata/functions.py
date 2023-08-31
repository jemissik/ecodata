"""Python functions for data subsetting and file conversion.
See the notebooks in the examples section for demos of how these are used."""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import cartopy.crs as ccrs
import fiona
import geopandas as gpd
import geoviews as gv
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rioxarray  # noqa
import xarray as xr
from shapely.geometry import Polygon

warnings.filterwarnings("ignore", message="Geometry is in a geographic CRS")

# Add KML support for fiona
fiona.drvsupport.supported_drivers["kml"] = "rw"
fiona.drvsupport.supported_drivers["KML"] = "rw"
fiona.drvsupport.supported_drivers["LIBKML"] = "rw"

TRACK_CRS = "EPSG:4326"


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
        labeled as "location_lat" and "location_long".
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
            "subset_data: Must specify one and only one of the subsetting options bbox, track_points, or bounding_shp"
        )

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
        if track_points is not None:
            gdf_track = read_track_data(track_points, dissolve=False)
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
            buffer_scale = max([abs(tot_bounds[2] - tot_bounds[0]), abs(tot_bounds[3] - tot_bounds[1])])
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
        outfile = Path(outfile)
        if outfile.suffix == ".shp":
            outdir = outfile.parent / outfile.stem
            outdir.mkdir(exist_ok=True)

            # Drop any datetime columns since this isn't supported in shapefiles
            gdf = gdf.select_dtypes(exclude=["datetime64[ns]"])

            gdf.to_file(outdir / outfile.name)
        else:
            gdf.to_file(outfile)
    output = dict(subset=gdf, boundary=boundary)
    if track_points:
        output["track_points"] = gdf_track
    elif bounding_geom:
        output["bounding_geom"] = gdf_features
    return output


def get_tracks_extent(tracks, boundary_shape="rectangular", buffer=0):
    # get boundary
    if boundary_shape == "rectangular":
        boundary = tracks.dissolve().envelope
    if boundary_shape == "convex_hull":
        boundary = tracks.dissolve().convex_hull

    # apply buffer
    if buffer != 0:
        tot_bounds = boundary.geometry.total_bounds
        buffer_scale = max([abs(tot_bounds[2] - tot_bounds[0]), abs(tot_bounds[3] - tot_bounds[1])])
        boundary = boundary.buffer(buffer * buffer_scale, cap_style=2, join_style=2)
        return gpd.GeoDataFrame(geometry=boundary)


def plot_subset_interactive(
    subset, boundary, bounding_geom=None, track_points=None, datashade_tracks=False, projection=ccrs.PlateCarree()
):
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
    if subset_geom_type.issubset({"Point", "MultiPoint"}):
        subset_plot = subset.hvplot.points(hover=False, geo=True, projection=projection)
    # Use geoviews for paths
    if subset_geom_type.issubset({"LineString", "MultiLineString"}):
        subset_plot = gv.Path(subset).opts(projection=projection, color="k")
    # TODO: Add polygon option
    else:
        raise TypeError("plot_subset_interactive: Geometry type of the subset is not supported.")

    plot = boundary_plot * subset_plot

    # Plot for bounding_geom
    if bounding_geom is not None:
        bounding_geom_plot = bounding_geom.to_crs(subset.crs).hvplot(geo=True, alpha=0.3)
        plot = bounding_geom_plot * plot

    # Plot for track points
    if track_points is not None:
        track_plot = track_points.hvplot.points(
            "location_long",
            "location_lat",
            hover=False,
            geo=True,
            datashade=datashade_tracks,
            dynspread=True,
            projection=projection,
            color="r",
            cmap="Reds",
            project=True,
        )
        plot = plot * track_plot

    return plot


def plot_subset(subset, boundary, bounding_geom=None, track_points=None):
    """
    Plots the results of the subset_data function in a static plot using matplotlib.

    Parameters
    ----------
    subset : geopandas.GeoDataFrame
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
    boundary.plot(ax=ax, color="c", alpha=0.4)
    subset.plot(ax=ax, color="b", linewidth=0.75)
    if bounding_geom is not None:
        bounding_geom.to_crs(subset.crs).plot(ax=ax, color="r")
    if track_points is not None:
        track_points.to_crs(subset.crs).plot(ax=ax, color="r", marker=".", alpha=0.4)
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
    """
    Read Movebank track data.

    Column headers are cleaned to snake case.

    Parameters
    ----------
    filein : str
        File path for track data
    dissolve : bool, optional
        Whether to dissolve track points to one multipoint geometry, by default False

    Returns
    -------
    geopandas.GeoDataFrame
        Geodataframe of track data
    """
    # read track csv
    track_df = clean_headers(pd.read_csv(filein), report=False)
    track_gdf = gpd.GeoDataFrame(
        track_df,
        geometry=gpd.points_from_xy(track_df["location_long"], track_df["location_lat"]),
        crs=TRACK_CRS,
    )
    if dissolve:
        track_gdf = track_gdf.dissolve()
    return track_gdf


def read_ref_data(filein):
    """
    Read Movebank reference data.

    Column headers are cleaned to snake case.

    Parameters
    ----------
    filein : str
        File path for refrence data

    Returns
    -------
    pandas.DataFrame
        Dataframe of reference data
    """
    ref_data = clean_headers(pd.read_csv(filein))
    return ref_data


def merge_tracks_ref(track_data, ref_data):
    """
    Merge track data and reference data on deployment_id.

    Left merge is used.

    Parameters
    ----------
    track_data : geopandas.GeoDataFrame
        Geodataframe of track data. Must include 'deployment_id'
    ref_data : pandas.DataFrame
        Dataframe of reference data. Must include 'deployment_id'

    Returns
    -------
    geopandas.GeoDataFrame
        Merged GeoDataFrame containing track data and reference data

    Raises
    ------
    KeyError
        Raised if track_data and/or reference data do not contain the deployment_id column
    """

    if ("deployment_id" in track_data.columns) and ("deployment_id" in ref_data.columns):
        merged_data = pd.merge(track_data, ref_data, on="deployment_id", how="left", suffixes=(None, "_ref"))
        cols_to_drop = [c for c in merged_data.columns if "_ref" in c]
        merged_data = merged_data.drop(columns=cols_to_drop)
    else:
        raise KeyError("merge_tracks_ref: both track_data and ref_data must contain deployment_id.")
    return merged_data


def combine_studies(studies):

    studies_to_concat = []
    for study in studies:
        if isinstance(study, str) | isinstance(study, Path):
            studies_to_concat.append(read_track_data(study))
        elif isinstance(study, gpd.GeoDataFrame):
            studies_to_concat.append(study)

        all_studies = pd.concat(studies_to_concat)
        all_studies = gpd.GeoDataFrame(all_studies, geometry=all_studies.geometry, crs=TRACK_CRS)

    return all_studies


def clip_tracks_timerange(df, df2):
    """
    Clip tracks dataset to include only points within the time range of another study

    Parameters
    ----------
    df : geopandas.GeoDataFrame
        Track dataset to clip
    df2 : geopandas.GeoDataFrame
        Other study that will be used to determine the time window of interest

    Returns
    -------
    geopandas.GeoDataFrame
        Track dataset containing only points within the time range of the other study
    """
    tmin = df2.timestamp.min()
    tmax = df2.timestamp.max()
    mask = (df.timestamp >= tmin) & (df.timestamp <= tmax)

    return df.loc[mask]


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


def clean_headers(
    df,
    report=True,
):
    """
    Function to clean column headers (column names).

    Read more in the :ref:`User Guide <clean_headers_user_guide>`.

    Parameters
    ----------
    df
        Dataframe from which column names are to be cleaned.
    report
        If True, output the summary report. Otherwise, no report is outputted.

        (default: True)
    """
    # Store original column names for creating cleaning report
    orig_columns = df.columns.astype(str).tolist()

    df = df.rename(columns=lambda col: _convert_case(col))

    df.columns = _rename_duplicates(df.columns)

    # Count the number of changed column names
    new_columns = df.columns.astype(str).tolist()
    cleaned = [1 if new_columns[i] != orig_columns[i] else 0 for i in range(len(orig_columns))]
    stats = {"cleaned": sum(cleaned)}

    # Output a report describing the result of clean_headers
    if report:
        _create_report(stats, len(df.columns))

    return df


def _convert_case(name):
    """
    Convert case style of a column name.

    Parameters
    ----------
    name
        Column name.
    case
        The desired case style of the column name.
    """
    NULL_VALUES = {np.nan, "", None}
    if name in NULL_VALUES:
        name = "header"

    string = re.sub(r"[!()*+\,\-./:;<=>?[\]^_{|}~]", " ", name)
    string = re.sub(r"[\'\"\`]", "", string)

    words = re.sub(r"([A-Z][a-z]+)", r" \1", re.sub(r"([A-Z]+|[0-9]+|\W+)", r" \1", string)).split()

    name = "_".join(words).lower()

    return name


def _rename_duplicates(names):
    """
    Rename duplicated column names to append a number at the end.
    """
    sep = "_"

    names = list(names)
    counts = {}

    for i, col in enumerate(names):
        cur_count = counts.get(col, 0)
        if cur_count > 0:
            names[i] = f"{col}{sep}{cur_count}"
        counts[col] = cur_count + 1

    return names


def _create_report(stats: dict[str, int], ncols: int) -> None:
    """
    Describe what was done in the cleaning process.
    """
    print("Column Headers Cleaning Report:")
    if stats["cleaned"] > 0:
        nclnd = stats["cleaned"]
        pclnd = round(nclnd / ncols * 100, 2)
        print(f"\t{nclnd} values cleaned ({pclnd}%)")
