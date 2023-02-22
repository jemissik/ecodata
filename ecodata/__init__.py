"""
ecodata package
"""

try:
    from ecodata.__version__ import version

    __version__ = version
except ImportError:
    # Package not installed
    __version__ = "0.0.0"

import ecodata.panel_utils
import ecodata.plotting
from ecodata.datasets import *
from ecodata.functions import (
    bbox2poly,
    clip_tracks_timerange,
    combine_studies,
    geotif2nc,
    get_crs,
    get_extent,
    get_file_info,
    get_file_len,
    get_geometry,
    get_tracks_extent,
    grib2nc,
    merge_tracks_ref,
    plot_subset,
    plot_subset_interactive,
    read_ref_data,
    read_track_data,
    subset_data,
)
from ecodata.xr_tools import (
    coarsen_dataset,
    detect_varnames,
    get_time_res,
    groupby_multi_time,
    groupby_poly_time,
    resample_time,
    select_spatial,
    select_time_cond,
    select_time_range,
    thin_dataset,
)
