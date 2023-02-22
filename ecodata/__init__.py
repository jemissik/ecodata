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
from ecodata.functions import bbox2poly  # noqa
from ecodata.functions import clip_tracks_timerange  # noqa
from ecodata.functions import combine_studies  # noqa
from ecodata.functions import geotif2nc  # noqa
from ecodata.functions import get_crs  # noqa
from ecodata.functions import get_extent  # noqa
from ecodata.functions import get_file_info  # noqa
from ecodata.functions import get_file_len  # noqa
from ecodata.functions import get_geometry  # noqa
from ecodata.functions import get_tracks_extent  # noqa
from ecodata.functions import grib2nc  # noqa
from ecodata.functions import merge_tracks_ref  # noqa
from ecodata.functions import plot_subset  # noqa
from ecodata.functions import plot_subset_interactive  # noqa
from ecodata.functions import read_ref_data  # noqa
from ecodata.functions import read_track_data  # noqa
from ecodata.functions import subset_data  # noqa
from ecodata.xr_tools import coarsen_dataset  # noqa
from ecodata.xr_tools import detect_varnames  # noqa
from ecodata.xr_tools import get_time_res  # noqa
from ecodata.xr_tools import groupby_multi_time  # noqa
from ecodata.xr_tools import groupby_poly_time  # noqa
from ecodata.xr_tools import resample_time  # noqa
from ecodata.xr_tools import select_spatial  # noqa
from ecodata.xr_tools import select_time_cond  # noqa
from ecodata.xr_tools import select_time_range  # noqa
from ecodata.xr_tools import thin_dataset  # noqa
