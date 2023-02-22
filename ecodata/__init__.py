"""
ecodata package
"""

try:
    from ecodata.__version__ import version

    __version__ = version
except ImportError:
    # Package not installed
    __version__ = "0.0.0"

from ecodata.datasets import *
from ecodata.functions import (
    get_crs,
    get_extent,
    get_file_info,
    get_file_len,
    get_geometry,
    grib2nc,
    geotif2nc,
    subset_data,
    get_tracks_extent,
    plot_subset_interactive,
    plot_subset,
    read_track_data,
    read_ref_data,
    merge_tracks_ref,
    combine_studies,
    clip_tracks_timerange,
    bbox2poly,
)

from ecodata.xr_tools import (
    detect_varnames,
    get_time_res,
    thin_dataset,
    coarsen_dataset,
    select_spatial,
    select_time_range,
    select_time_cond,
    resample_time,
    groupby_multi_time,
    groupby_poly_time,
)

import ecodata.plotting
import ecodata.panel_utils