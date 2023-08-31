"""
ecodata package
"""

try:
    from ecodata.__version__ import version

    __version__ = version
except ImportError:
    # Package not installed
    __version__ = "0.0.0"

import ecodata.panel_utils  # noqa
import ecodata.plotting  # noqa
from ecodata.datasets import *  # noqa
from ecodata.raster_utils import (
    grib2nc,  # noqa
    geotif2nc,  # noqa
)
from ecodata.functions import (
    bbox2poly,  # noqa
    clip_tracks_timerange,  # noqa
    combine_studies,  # noqa
    get_crs,  # noqa
    get_extent,  # noqa
    get_file_info,  # noqa
    get_file_len,  # noqa
    get_geometry,  # noqa
    get_tracks_extent,  # noqa
    merge_tracks_ref,  # noqa
    plot_subset,  # noqa
    plot_subset_interactive,  # noqa
    read_ref_data,  # noqa
    read_track_data,  # noqa
    subset_data,  # noqa
)
from ecodata.xr_tools import (
    coarsen_dataset,  # noqa
    detect_varnames,  # noqa
    get_time_res,  # noqa
    groupby_multi_time,  # noqa
    groupby_poly_time,  # noqa
    resample_time,  # noqa
    select_spatial,  # noqa
    select_time_cond,  # noqa
    select_time_range,  # noqa
    thin_dataset,  # noqa
)
