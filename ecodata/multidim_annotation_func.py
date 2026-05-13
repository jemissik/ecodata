"""
Multidimensional annotation backend for ECODATA-Prepare.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple, Union
import logging
import math
import re

import numpy as np
import pandas as pd
import xarray as xr

try:
    import geopandas as gpd
    from shapely.geometry import Point
except Exception:  # pragma: no cover
    gpd = None
    Point = None

try:
    from ecodata.annotation_eng_func import (
        safe_open_nc_with_time_decoding,
        get_nc_bounds,
        load_vector_extent_info,
        _k_nearest_indices as ae_k_nearest_indices,
        _idw as ae_idw,
    )
except Exception:  # pragma: no cover
    safe_open_nc_with_time_decoding = None
    get_nc_bounds = None
    load_vector_extent_info = None
    ae_k_nearest_indices = None
    ae_idw = None

LOGGER = logging.getLogger(__name__)
G0 = 9.80665
_GEOID_MODEL = None

TIME_CANDIDATES = ("time", "valid_time", "forecast_time", "verification_time", "datetime", "date")
LAT_CANDIDATES = ("lat", "latitude", "y")
LON_CANDIDATES = ("lon", "longitude", "long", "x")
LEVEL_CANDIDATES = ("level", "lev", "plev", "pressure", "pressure_level", "isobaricInhPa", "isobaric_in_hPa")

VerticalMethod = Literal["nearest", "linear"]
HorizontalMethod = Literal["nearest", "idw"]
HeightReference = Literal["already_orthometric", "already_msl", "ellipsoidal", "agl"]
GeoidMode = Literal["none", "constant", "geographiclib", "pyproj_grid"]
VariableType = Literal["continuous", "categorical"]


@dataclass
class DatasetSpec:
    path: Union[str, Path]
    variables: List[str]
    continuous: List[str] = field(default_factory=list)
    categorical: List[str] = field(default_factory=list)
    label_prefix: str = ""

    @classmethod
    def from_single(cls, path: Union[str, Path], variable: Optional[str], label_prefix: str = "") -> Optional["DatasetSpec"]:
        if not path or not variable:
            return None
        return cls(path=path, variables=[variable], continuous=[variable], categorical=[], label_prefix=label_prefix)


@dataclass
class OptionalComponentSpec:
    path: Optional[Union[str, Path]] = None
    variable: Optional[str] = None
    label: str = ""

    def is_enabled(self) -> bool:
        return bool(self.path and self.variable)


@dataclass
class MultidimAnnotationConfig:
    movement_csv: Union[str, Path]
    output_csv: Union[str, Path]

    id_col: str
    time_col: str
    lat_col: str
    lon_col: str
    height_col: str

    geopotential_file: Union[str, Path]
    geopotential_variable: str
    multilevel: DatasetSpec

    selected_ids: Optional[List[str]] = None
    boundary_path: Optional[Union[str, Path]] = None
    bbox: Optional[Dict[str, float]] = None

    coord_spec: Optional[Dict[str, Optional[str]]] = None
    geopotential_units: Optional[str] = "m2 s-2"
    convert_geopotential_to_height: bool = True
    gravity_constant: float = G0

    spatial_method: HorizontalMethod = "nearest"
    smoothing_k: int = 1
    vertical_method: VerticalMethod = "nearest"
    keep_diagnostics: bool = True
    save_per_individual: bool = False

    height_reference: HeightReference = "ellipsoidal"
    geoid_mode: GeoidMode = "geographiclib"
    constant_geoid_undulation_m: float = 0.0
    geoid_grid_path: Optional[Union[str, Path]] = None

    surface: Optional[DatasetSpec] = None
    use_surface_as_lower_anchor: bool = True
    surface_height_agl_m: float = 2.0

    dem_file: Optional[Union[str, Path]] = None
    dem_units: str = "m"
    dem_reference: str = "orthometric"

    u_component: OptionalComponentSpec = field(default_factory=OptionalComponentSpec)
    v_component: OptionalComponentSpec = field(default_factory=OptionalComponentSpec)
    w_component: OptionalComponentSpec = field(default_factory=OptionalComponentSpec)
    temperature_component: OptionalComponentSpec = field(default_factory=OptionalComponentSpec)

    derive_wind_speed_direction: bool = False
    derive_wind_support_crosswind: bool = False
    derive_vertical_motion: bool = False
    derive_thermal_proxy: bool = False
    derive_orographic_uplift: bool = False

    heading_col: Optional[str] = None
    heading_source: Literal["compute", "column"] = "compute"

    allow_vertical_extrapolation: bool = False


def _as_path(path: Union[str, Path]) -> Path:
    return Path(path).expanduser().resolve()


def _require_file(path: Union[str, Path], label: str) -> Path:
    p = _as_path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"{label} not found or is not a file: {p}")
    return p


def _normalise_name(name: str) -> str:
    return re.sub(r"[-:.\s]+", "_", str(name).lower())


def _unique(*values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for seq in values:
        for val in list(seq or []):
            if val not in seen:
                seen.add(val)
                out.append(val)
    return out


def _normalize_vertical_method(value: str) -> VerticalMethod:
    v = str(value or "").strip().lower()
    return "linear" if ("linear" in v or "interpol" in v) else "nearest"


def _normalize_spatial_method(value: str) -> HorizontalMethod:
    v = str(value or "").strip().lower()
    if "idw" in v or "inverse" in v:
        return "idw"
    return "nearest"


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return np.nan


def parse_movebank_timestamp_series(series: pd.Series, col_name: str = "timestamp") -> pd.Series:
    raw = series.copy()
    attempts: List[pd.Series] = []

    for kwargs in (
        {"errors": "coerce", "utc": False},
        {"errors": "coerce", "utc": False, "dayfirst": True},
        {"errors": "coerce", "utc": False, "format": "mixed"},
        {"errors": "coerce", "utc": False, "format": "ISO8601"},
    ):
        try:
            attempts.append(pd.to_datetime(raw, **kwargs))
        except Exception:
            pass

    out = attempts[0] if attempts else pd.to_datetime(raw, errors="coerce")
    for parsed in attempts[1:]:
        out = out.fillna(parsed)

    if out.isna().any():
        numeric = pd.to_numeric(raw, errors="coerce")
        numeric_attempts = []
        for unit in ("s", "ms", "us", "ns"):
            try:
                numeric_attempts.append(pd.to_datetime(numeric, errors="coerce", unit=unit, utc=False))
            except Exception:
                pass
        if numeric_attempts:
            best = max(numeric_attempts, key=lambda x: int(x.notna().sum()))
            out = out.fillna(best)

    if out.isna().any():
        bad_mask = out.isna()
        examples = raw[bad_mask].astype(str).head(10).tolist()
        raise ValueError(
            f"Timestamp column '{col_name}' contains {int(bad_mask.sum())} unparsable value(s). "
            f"Examples: {examples}"
        )

    try:
        if getattr(out.dt, "tz", None) is not None:
            out = out.dt.tz_convert(None)
    except Exception:
        pass
    return out


def _find_name(obj: Union[xr.Dataset, xr.DataArray], candidates: Sequence[str]) -> Optional[str]:
    names: List[str] = []
    if isinstance(obj, xr.Dataset):
        names.extend([str(x) for x in obj.coords])
        names.extend([str(x) for x in obj.dims])
        names.extend([str(x) for x in obj.variables])
    else:
        names.extend([str(x) for x in obj.coords])
        names.extend([str(x) for x in obj.dims])
    lower = {n.lower(): n for n in names}
    for cand in candidates:
        if cand in names:
            return cand
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def _coord_names(ds: xr.Dataset, coord_spec: Optional[Dict[str, Optional[str]]] = None, require_level: bool = False) -> Dict[str, Optional[str]]:
    spec = coord_spec or {}
    names = {
        "time": spec.get("time") or _find_name(ds, TIME_CANDIDATES),
        "lat": spec.get("lat") or _find_name(ds, LAT_CANDIDATES),
        "lon": spec.get("lon") or _find_name(ds, LON_CANDIDATES),
        "level": spec.get("level") or _find_name(ds, LEVEL_CANDIDATES),
    }
    for key, val in list(names.items()):
        if val and val not in ds.variables and val not in ds.coords and val not in ds.dims:
            names[key] = None
    missing = [k for k in ("time", "lat", "lon") if names[k] is None]
    if require_level and names["level"] is None:
        missing.append("level")
    if missing:
        raise ValueError(f"Dataset is missing required coordinate(s): {', '.join(missing)}")
    return names


def _rename_standard_coords(ds: xr.Dataset, coord_spec: Optional[Dict[str, Optional[str]]] = None) -> xr.Dataset:
    names = _coord_names(ds, coord_spec, require_level=False)
    mapping = {}
    for std in ("time", "lat", "lon", "level"):
        src = names.get(std)
        if src and src != std and src in ds.variables:
            mapping[src] = std
        elif src and src != std and src in ds.dims:
            mapping[src] = std
    if mapping:
        ds = ds.rename(mapping)
    if "lat" in ds:
        try:
            vals = np.asarray(ds["lat"].values, dtype=float)
            if vals.ndim == 1 and vals.size > 1 and vals[0] > vals[-1]:
                ds = ds.sortby("lat")
        except Exception:
            pass
    if "lon" in ds:
        try:
            vals = np.asarray(ds["lon"].values, dtype=float)
            if vals.ndim == 1 and vals.size > 1 and vals[0] > vals[-1]:
                ds = ds.sortby("lon")
        except Exception:
            pass
    return ds


def open_dataset(path: Union[str, Path], coord_spec: Optional[Dict[str, Optional[str]]] = None) -> xr.Dataset:
    p = _require_file(path, "NetCDF file")
    if safe_open_nc_with_time_decoding is not None:
        try:
            ds = safe_open_nc_with_time_decoding(str(p))
        except Exception:
            ds = xr.open_dataset(p, decode_times=True)
    else:
        try:
            ds = xr.open_dataset(p, decode_times=True)
        except Exception:
            ds = xr.open_dataset(p, decode_times=False)
    return _rename_standard_coords(ds, coord_spec)


def _wrap_lon(lon: float, lon_values: np.ndarray) -> float:
    vals = np.asarray(lon_values, dtype=float)
    finite = vals[np.isfinite(vals)]
    if finite.size == 0:
        return float(lon)
    mn, mx = float(np.nanmin(finite)), float(np.nanmax(finite))
    if mn >= 0 and mx > 180 and lon < 0:
        return float(lon) % 360.0
    if mn < 0 and mx <= 180 and lon > 180:
        return ((float(lon) + 180.0) % 360.0) - 180.0
    return float(lon)


def _time_value(t: pd.Timestamp) -> Any:
    return np.datetime64(pd.Timestamp(t).to_datetime64())


def _nearest_time_index(values: np.ndarray, t: pd.Timestamp) -> int:
    times = pd.to_datetime(values)
    arr = times.to_numpy(dtype="datetime64[ns]").astype("int64")
    target = np.datetime64(pd.Timestamp(t).to_datetime64()).astype("datetime64[ns]").astype("int64")
    return int(np.nanargmin(np.abs(arr - target)))


def _select_time_space(
    da: xr.DataArray,
    t: pd.Timestamp,
    lat: float,
    lon: float,
    *,
    time_method: Literal["nearest", "linear"] = "linear",
    spatial_method: Literal["nearest", "linear"] = "linear",
    fixed_lat: Optional[float] = None,
    fixed_lon: Optional[float] = None,
) -> xr.DataArray:
    lat_q = float(lat if fixed_lat is None else fixed_lat)
    lon_q = float(lon if fixed_lon is None else fixed_lon)
    lon_q = _wrap_lon(lon_q, np.asarray(da["lon"].values)) if "lon" in da.coords else lon_q

    out = da

    if spatial_method == "nearest":
        isel_indexers: Dict[str, int] = {}
        if "lat" in out.dims and "lat" in out.coords:
            isel_indexers["lat"] = _nearest_index(np.asarray(out["lat"].values, dtype=float), lat_q)
        if "lon" in out.dims and "lon" in out.coords:
            isel_indexers["lon"] = _nearest_index(np.asarray(out["lon"].values, dtype=float), lon_q)
        if isel_indexers:
            out = out.isel(isel_indexers)
    else:
        spatial_indexers = {}
        if "lat" in out.dims or "lat" in out.coords:
            spatial_indexers["lat"] = lat_q
        if "lon" in out.dims or "lon" in out.coords:
            spatial_indexers["lon"] = lon_q
        if spatial_indexers:
            try:
                out = out.interp(spatial_indexers, method="linear")
            except Exception:
                out = out.sel(spatial_indexers, method="nearest")

    if "time" in out.dims or "time" in out.coords:
        if time_method == "nearest":
            try:
                if "time" in out.dims:
                    out = out.isel({"time": _nearest_time_index(out["time"].values, pd.Timestamp(t))})
                else:
                    out = out.sel({"time": _time_value(pd.Timestamp(t))}, method="nearest")
            except Exception:
                out = out.sel({"time": _time_value(pd.Timestamp(t))}, method="nearest")
        else:
            try:
                out = out.interp({"time": _time_value(pd.Timestamp(t))}, method="linear")
            except Exception:
                out = out.sel({"time": _time_value(pd.Timestamp(t))}, method="nearest")

    return out.squeeze(drop=True)


def geopotential_to_height_m(
    da: xr.DataArray,
    units_override: Optional[str] = None,
    convert_geopotential_to_height: bool = True,
    gravity_constant: float = G0,
) -> xr.DataArray:
    units = (units_override or da.attrs.get("units") or "").lower()
    norm = units.replace("**", "^").replace("/", " ")
    is_height = norm.strip() in {"m", "meter", "meters", "metre", "metres"} or "geopotential metre" in norm or "gpm" in norm
    is_geopotential = any(x in norm for x in ("m^2 s^-2", "m2 s-2", "m2 s^-2", "m2 s**-2"))
    if convert_geopotential_to_height and (is_geopotential or not is_height):
        out = da / float(gravity_constant)
        out.attrs["units"] = "m"
        return out.rename("geopotential_height_m")
    out = da.copy()
    out.attrs["units"] = "m"
    return out.rename("geopotential_height_m")


def sample_level_profile(
    ds: xr.Dataset,
    variable: str,
    *,
    t: pd.Timestamp,
    lat: float,
    lon: float,
    time_method: Literal["nearest", "linear"] = "linear",
    spatial_method: Literal["nearest", "linear"] = "linear",
    fixed_lat: Optional[float] = None,
    fixed_lon: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    if variable not in ds.data_vars:
        raise ValueError(f"Variable '{variable}' not found in dataset.")
    if "level" not in ds[variable].dims and "level" not in ds[variable].coords:
        raise ValueError(f"Variable '{variable}' has no level dimension.")
    prof = _select_time_space(
        ds[variable], t, lat, lon,
        time_method=time_method,
        spatial_method=spatial_method,
        fixed_lat=fixed_lat,
        fixed_lon=fixed_lon,
    )
    if "level" not in prof.dims and "level" in prof.coords:
        prof = prof.expand_dims({"level": prof["level"]})
    prof = prof.transpose("level", ...).squeeze(drop=True)
    levels = np.asarray(prof["level"].values)
    values = np.asarray(prof.values).astype(float).reshape(-1)
    return levels, values


def sample_geopotential_profile(
    ds: xr.Dataset,
    variable: str,
    *,
    t: pd.Timestamp,
    lat: float,
    lon: float,
    units_override: Optional[str],
    convert_geopotential_to_height: bool,
    gravity_constant: float,
    time_method: Literal["nearest", "linear"] = "linear",
    spatial_method: Literal["nearest", "linear"] = "linear",
    fixed_lat: Optional[float] = None,
    fixed_lon: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    z = geopotential_to_height_m(ds[variable], units_override, convert_geopotential_to_height, gravity_constant)
    tmp = z.to_dataset(name="geopotential_height_m")
    return sample_level_profile(
        tmp, "geopotential_height_m", t=t, lat=lat, lon=lon,
        time_method=time_method, spatial_method=spatial_method,
        fixed_lat=fixed_lat, fixed_lon=fixed_lon,
    )


def sample_surface_value(
    ds: xr.Dataset,
    variable: str,
    *,
    t: pd.Timestamp,
    lat: float,
    lon: float,
    variable_type: VariableType = "continuous",
    spatial_method: HorizontalMethod = "nearest",
    fixed_lat: Optional[float] = None,
    fixed_lon: Optional[float] = None,
) -> Any:
    if variable not in ds.data_vars:
        raise ValueError(f"Variable '{variable}' not found in surface dataset.")
    time_method: Literal["nearest", "linear"] = "nearest" if variable_type == "categorical" else "linear"
    spatial_xr: Literal["nearest", "linear"] = "nearest" if (variable_type == "categorical" or spatial_method == "nearest") else "linear"
    da = _select_time_space(
        ds[variable], t, lat, lon,
        time_method=time_method,
        spatial_method=spatial_xr,
        fixed_lat=fixed_lat,
        fixed_lon=fixed_lon,
    )
    arr = np.asarray(da.values)
    return arr.squeeze().item() if arr.size else np.nan


def _prepare_vertical_nodes(levels: Sequence[Any], heights: Sequence[float], values: Sequence[float], surface_value=None, surface_height=None) -> pd.DataFrame:
    rows = []
    for lev, z, val in zip(levels, heights, values):
        zf, vf = _safe_float(z), _safe_float(val)
        if np.isfinite(zf) and np.isfinite(vf):
            rows.append({"level": lev, "height_m": zf, "value": vf, "is_surface": False})
    if surface_value is not None and surface_height is not None:
        sv, sh = _safe_float(surface_value), _safe_float(surface_height)
        if np.isfinite(sv) and np.isfinite(sh):
            rows.append({"level": "surface", "height_m": sh, "value": sv, "is_surface": True})
    if not rows:
        return pd.DataFrame(columns=["level", "height_m", "value", "is_surface"])
    return pd.DataFrame(rows).sort_values("height_m", kind="mergesort").reset_index(drop=True)


def vertical_sample(
    levels: Sequence[Any],
    heights_m: Sequence[float],
    values: Sequence[float],
    target_height_m: float,
    *,
    method: VerticalMethod = "nearest",
    variable_type: VariableType = "continuous",
    surface_value: Optional[float] = None,
    surface_height_m: Optional[float] = None,
    allow_extrapolation: bool = False,
) -> Tuple[float, Dict[str, Any]]:
    if variable_type == "categorical":
        method = "nearest"
    H = _safe_float(target_height_m)
    nodes = _prepare_vertical_nodes(levels, heights_m, values, surface_value, surface_height_m)
    diag: Dict[str, Any] = {
        "vertical_method": method,
        "target_height_msl_m": H,
        "matched_level": np.nan,
        "matched_level_height_m": np.nan,
        "height_difference_m": np.nan,
        "lower_level": np.nan,
        "upper_level": np.nan,
        "lower_height_m": np.nan,
        "upper_height_m": np.nan,
        "vertical_weight_upper": np.nan,
        "surface_anchor_used": False,
        "vertical_out_of_range": False,
        "vertical_warning": "",
    }
    if not np.isfinite(H):
        diag["vertical_warning"] = "invalid_target_height"
        return np.nan, diag
    if nodes.empty:
        diag["vertical_warning"] = "empty_vertical_profile"
        return np.nan, diag
    z = nodes["height_m"].to_numpy(dtype=float)
    v = nodes["value"].to_numpy(dtype=float)
    if method == "nearest" or len(nodes) == 1:
        idx = int(np.nanargmin(np.abs(z - H)))
        diag.update({
            "matched_level": nodes.loc[idx, "level"],
            "matched_level_height_m": float(z[idx]),
            "height_difference_m": float(H - z[idx]),
            "surface_anchor_used": bool(nodes.loc[idx, "is_surface"]),
        })
        return float(v[idx]), diag
    if H < z[0]:
        diag["vertical_out_of_range"] = True
        if not allow_extrapolation:
            diag["vertical_warning"] = "below_lowest_vertical_node"
            return np.nan, diag
        lo, hi = 0, min(1, len(z) - 1)
    elif H > z[-1]:
        diag["vertical_out_of_range"] = True
        if not allow_extrapolation:
            diag["vertical_warning"] = "above_highest_vertical_node"
            return np.nan, diag
        lo, hi = max(0, len(z) - 2), len(z) - 1
    else:
        hi = int(np.searchsorted(z, H, side="left"))
        if hi == 0:
            lo = hi = 0
        elif hi < len(z) and np.isclose(z[hi], H):
            lo = hi
        else:
            lo, hi = hi - 1, min(hi, len(z) - 1)
    if lo == hi or np.isclose(z[lo], z[hi]):
        val, w = float(v[lo]), 0.0
    else:
        w = float((H - z[lo]) / (z[hi] - z[lo]))
        val = float(v[lo] * (1 - w) + v[hi] * w)
    nearest = lo if abs(H - z[lo]) <= abs(H - z[hi]) else hi
    diag.update({
        "lower_level": nodes.loc[lo, "level"],
        "upper_level": nodes.loc[hi, "level"],
        "lower_height_m": float(z[lo]),
        "upper_height_m": float(z[hi]),
        "vertical_weight_upper": float(w),
        "matched_level": nodes.loc[nearest, "level"],
        "matched_level_height_m": float(z[nearest]),
        "height_difference_m": float(H - z[nearest]),
        "surface_anchor_used": bool(nodes.loc[lo, "is_surface"] or nodes.loc[hi, "is_surface"]),
    })
    return val, diag


def sample_dem_elevation(dem_file: Optional[Union[str, Path]], lat: float, lon: float) -> Tuple[float, str]:
    if not dem_file:
        return np.nan, "dem_not_provided"
    try:
        import rasterio
        from pyproj import Transformer
    except Exception:
        return np.nan, "rasterio_or_pyproj_not_available"
    try:
        path = _require_file(dem_file, "DEM file")
        with rasterio.open(path) as src:
            x, y = float(lon), float(lat)
            if src.crs is not None and str(src.crs).upper() not in {"EPSG:4326", "OGC:CRS84"}:
                x, y = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True).transform(x, y)
            row, col = src.index(x, y)
            if row < 0 or col < 0 or row >= src.height or col >= src.width:
                return np.nan, "point_outside_dem"
            arr = src.read(1, window=((row, row + 1), (col, col + 1)), masked=True)
            if np.ma.is_masked(arr) and bool(np.ma.getmaskarray(arr).squeeze()):
                return np.nan, "dem_nodata"
            val = float(np.asarray(arr).squeeze())
            if src.nodata is not None and np.isclose(val, float(src.nodata), equal_nan=True):
                return np.nan, "dem_nodata"
            return val, ""
    except Exception as exc:
        return np.nan, f"dem_sampling_failed:{exc}"

def compute_dem_slope_aspect(
    dem_file: Union[str, Path],
    lat: float,
    lon: float,
    sample_radius_px: int = 1,
) -> Tuple[float, float]:
    """
    Compute terrain slope and aspect at a given point from a DEM raster.

    Uses central-difference finite differences on the surrounding pixel
    neighbourhood to estimate first-order partial derivatives of elevation.

    Args:
        dem_file:         Path to the DEM raster (GeoTIFF or similar).
        lat, lon:         Geographic coordinates of the query point (degrees).
        sample_radius_px: Half-size of the pixel window used for finite
                          differences (default 1 = 3x3 window).

    Returns:
        (slope_rad, aspect_rad) where
        slope_rad  -- terrain slope angle from horizontal (radians, 0..π/2).
        aspect_rad -- upslope direction measured clockwise from North (radians,
                      0..2π), i.e. the direction the slope faces.
        Both values are NaN on error or where the DEM has no data.
    """
    try:
        import rasterio
        from pyproj import Transformer
    except Exception:
        return np.nan, np.nan

    try:
        path = _require_file(dem_file, "DEM file")
        with rasterio.open(path) as src:
            x, y = float(lon), float(lat)
            if src.crs is not None and str(src.crs).upper() not in {"EPSG:4326", "OGC:CRS84"}:
                x, y = Transformer.from_crs(
                    "EPSG:4326", src.crs, always_xy=True
                ).transform(x, y)

            row_c, col_c = src.index(x, y)
            r0 = max(0, row_c - sample_radius_px)
            r1 = min(src.height, row_c + sample_radius_px + 1)
            c0 = max(0, col_c - sample_radius_px)
            c1 = min(src.width,  col_c + sample_radius_px + 1)
            if r1 - r0 < 2 or c1 - c0 < 2:
                return np.nan, np.nan

            patch = src.read(1, window=((r0, r1), (c0, c1)), masked=True).astype(float)
            if src.nodata is not None:
                patch[patch == float(src.nodata)] = np.nan

            # Pixel size in metres
            res_x = abs(src.transform.a)
            res_y = abs(src.transform.e)
            if src.crs is not None and src.crs.is_geographic:
                # Convert arc-degrees to metres at this latitude
                lat_rad = math.radians(float(lat))
                res_x = res_x * math.pi / 180.0 * 6_371_000.0 * math.cos(lat_rad)
                res_y = res_y * math.pi / 180.0 * 6_371_000.0

            rows, cols = patch.shape
            cr, cc = rows // 2, cols // 2

            # East-west gradient (positive = elevation increases eastward)
            if 0 < cc < cols - 1:
                dz_dx = (patch[cr, cc + 1] - patch[cr, cc - 1]) / (2.0 * res_x)
            elif cc < cols - 1:
                dz_dx = (patch[cr, cc + 1] - patch[cr, cc]) / res_x
            else:
                dz_dx = (patch[cr, cc] - patch[cr, cc - 1]) / res_x

            # North-south gradient (positive = elevation increases northward).
            # Rasterio row index increases southward, so north = smaller row index.
            if 0 < cr < rows - 1:
                dz_dy = (patch[cr - 1, cc] - patch[cr + 1, cc]) / (2.0 * res_y)
            elif cr < rows - 1:
                dz_dy = (patch[cr, cc] - patch[cr + 1, cc]) / res_y
            else:
                dz_dy = (patch[cr - 1, cc] - patch[cr, cc]) / res_y

            if not (np.isfinite(dz_dx) and np.isfinite(dz_dy)):
                return np.nan, np.nan

            slope  = math.atan(math.sqrt(dz_dx**2 + dz_dy**2))
            # Aspect: direction the slope faces, clockwise from North.
            # atan2(dz_dx, dz_dy) maps (east gradient, north gradient)
            # to the bearing of the upslope direction.
            aspect = (math.atan2(dz_dx, dz_dy) + 2.0 * math.pi) % (2.0 * math.pi)
            return float(slope), float(aspect)

    except Exception:
        return np.nan, np.nan
    
def compute_orographic_uplift(
    u10_ms: float,
    v10_ms: float,
    slope_rad: float,
    aspect_rad: float,
) -> float:
    """
    Orographic updraft velocity Wo (m/s) following Bohrer et al. (2012).

        Wo = V_surface * sin(slope) * cos(wind_from - aspect)

    where wind_from is the direction the wind is blowing *from*
    (meteorological convention: easterly wind -> 90°).

    Positive Wo = wind blowing onto the upslope face -> updraft.
    Negative Wo = wind blowing off the slope (lee side) -> downdraft.

    Args:
        u10_ms:    ERA5 10-metre U-component of wind (m/s, eastward positive).
        v10_ms:    ERA5 10-metre V-component of wind (m/s, northward positive).
        slope_rad: Terrain slope angle in radians, from compute_dem_slope_aspect().
        aspect_rad: Upslope-facing direction clockwise from North (radians).

    Returns:
        Wo in m/s, or NaN if any input is missing.
    """
    u, v       = float(u10_ms), float(v10_ms)
    slope      = float(slope_rad)
    aspect     = float(aspect_rad)

    if not all(np.isfinite([u, v, slope, aspect])):
        return np.nan

    V = math.sqrt(u * u + v * v)
    if V < 1e-6:
        return 0.0

    # Direction the wind is blowing FROM, clockwise from North (radians).
    # atan2(u, v): u=east component, v=north component gives bearing from North.
    wind_from = (math.atan2(u, v) + 2.0 * math.pi) % (2.0 * math.pi)

    return float(V * math.sin(slope) * math.cos(wind_from - aspect))

def geoid_undulation_geographiclib(lat: float, lon: float) -> Optional[float]:
    global _GEOID_MODEL
    try:
        from geographiclib.geoid import Geoid
        if _GEOID_MODEL is None:
            _GEOID_MODEL = Geoid("egm2008")
        llon = ((float(lon) + 180.0) % 360.0) - 180.0
        return float(_GEOID_MODEL.Height(float(lat), llon))
    except Exception:
        return None


def geoid_undulation_pyproj(lat: float, lon: float, grid_path: Optional[Union[str, Path]]) -> Optional[float]:
    if not grid_path:
        return None
    try:
        from pyproj import CRS, Transformer
        crs_geog_3d = CRS.from_epsg(4979)
        pipeline = f"+proj=pipeline +step +proj=vgridshift +grids={_as_path(grid_path)} +multiplier=1"
        transformer = Transformer.from_crs(crs_geog_3d, CRS.from_pipeline(pipeline), always_xy=True)
        h0 = 100.0
        H = transformer.transform(float(lon), float(lat), h0)[2]
        return float(h0 - H)
    except Exception:
        return None


def compute_orthometric_height(
    raw_height_m: float,
    lat: float,
    lon: float,
    *,
    height_reference: HeightReference,
    geoid_mode: GeoidMode,
    constant_geoid_undulation_m: float,
    geoid_grid_path: Optional[Union[str, Path]] = None,
    terrain_elevation_m: Optional[float] = None,
) -> Tuple[float, Dict[str, Any]]:
    h = _safe_float(raw_height_m)
    diag: Dict[str, Any] = {
        "height_input_m": h,
        "height_reference": height_reference,
        "geoid_mode": geoid_mode,
        "geoid_undulation_m": np.nan,
        "height_conversion_warning": "",
    }
    if not np.isfinite(h):
        diag["height_conversion_warning"] = "invalid_height"
        return np.nan, diag
    if height_reference in ("already_orthometric", "already_msl"):
        return h, diag
    if height_reference == "agl":
        terrain = np.nan if terrain_elevation_m is None else float(terrain_elevation_m)
        if not np.isfinite(terrain):
            diag["height_conversion_warning"] = "agl_height_without_valid_dem"
            return np.nan, diag
        return terrain + h, diag
    N: Optional[float] = None
    if geoid_mode == "geographiclib":
        N = geoid_undulation_geographiclib(lat, lon)
    elif geoid_mode == "pyproj_grid":
        N = geoid_undulation_pyproj(lat, lon, geoid_grid_path)
    elif geoid_mode == "constant":
        N = float(constant_geoid_undulation_m)
    elif geoid_mode == "none":
        N = 0.0
    if N is None:
        N = float(constant_geoid_undulation_m)
        diag["height_conversion_warning"] = "geoid_lookup_failed_used_constant_N"
    diag["geoid_undulation_m"] = float(N)
    return h - float(N), diag


def _nearest_index(arr: np.ndarray, x: float) -> int:
    arr = np.asarray(arr, dtype=float)
    idx = int(np.searchsorted(arr, x))
    if idx <= 0:
        return 0
    if idx >= len(arr):
        return len(arr) - 1
    return idx if abs(arr[idx] - x) < abs(arr[idx - 1] - x) else idx - 1


def _k_nearest_indices(glat: np.ndarray, glon: np.ndarray, lat: float, lon: float, k: int) -> List[Tuple[int, int]]:
    if ae_k_nearest_indices is not None:
        try:
            return list(ae_k_nearest_indices(glat, glon, lat, lon, k))
        except Exception:
            pass
    i0 = _nearest_index(glat, lat)
    j0 = _nearest_index(glon, lon)
    r = int(np.ceil(max(1, np.sqrt(k))))
    candidates = []
    for ii in range(max(0, i0 - r), min(len(glat) - 1, i0 + r) + 1):
        for jj in range(max(0, j0 - r), min(len(glon) - 1, j0 + r) + 1):
            d = float(np.hypot(glat[ii] - lat, glon[jj] - lon))
            candidates.append((d, ii, jj))
    candidates.sort(key=lambda x: x[0])
    return [(ii, jj) for _, ii, jj in candidates[:k]]


def _idw(values: Sequence[float], distances: Sequence[float], p: float = 2.0) -> float:
    if ae_idw is not None:
        try:
            return float(ae_idw(values, distances, p=p))
        except Exception:
            pass
    vals = np.asarray(values, dtype=float)
    d = np.asarray(distances, dtype=float) + 1e-12
    mask = np.isfinite(vals)
    if not mask.any():
        return np.nan
    w = 1.0 / (d[mask] ** p)
    return float(np.sum(vals[mask] * w) / np.sum(w))


def _filter_boundary(df: pd.DataFrame, lat_col: str, lon_col: str, boundary_path: Optional[Union[str, Path]], bbox: Optional[Dict[str, float]]) -> pd.DataFrame:
    out = df.copy()
    if bbox:
        S = float(bbox.get("S", bbox.get("south")))
        N = float(bbox.get("N", bbox.get("north")))
        W = float(bbox.get("W", bbox.get("west")))
        E = float(bbox.get("E", bbox.get("east")))
        return out[out[lat_col].between(S, N) & out[lon_col].between(W, E)].copy()
    if not boundary_path:
        return out
    if gpd is None or Point is None:
        raise RuntimeError("geopandas/shapely are required for boundary filtering.")
    boundary = gpd.read_file(_require_file(boundary_path, "Boundary file"))
    points = gpd.GeoDataFrame(out, geometry=[Point(xy) for xy in zip(out[lon_col], out[lat_col])], crs="EPSG:4326")
    if boundary.crs != points.crs:
        boundary = boundary.to_crs(points.crs)
    clipped = gpd.sjoin(points, boundary[["geometry"]], predicate="within", how="inner").drop(columns=["index_right", "geometry"], errors="ignore")
    return pd.DataFrame(clipped)


def _dataset_bounds(ds: xr.Dataset) -> Optional[Dict[str, float]]:
    try:
        return {"S": float(ds["lat"].min()), "N": float(ds["lat"].max()), "W": float(ds["lon"].min()), "E": float(ds["lon"].max())}
    except Exception:
        return None


def _load_movement(config: MultidimAnnotationConfig, ds_for_bbox: Optional[xr.Dataset] = None) -> pd.DataFrame:
    path = _require_file(config.movement_csv, "Movement CSV")
    df = pd.read_csv(path)
    required = [config.id_col, config.time_col, config.lat_col, config.lon_col, config.height_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Movement CSV is missing required column(s): {', '.join(missing)}")
    if config.selected_ids:
        ids = {str(x) for x in config.selected_ids}
        df = df[df[config.id_col].astype(str).isin(ids)].copy()
    df[config.time_col] = parse_movebank_timestamp_series(df[config.time_col], config.time_col)
    df[config.lat_col] = pd.to_numeric(df[config.lat_col], errors="coerce")
    df[config.lon_col] = pd.to_numeric(df[config.lon_col], errors="coerce")
    df[config.height_col] = pd.to_numeric(df[config.height_col], errors="coerce")
    df = df.dropna(subset=[config.time_col, config.lat_col, config.lon_col, config.height_col]).copy()
    bbox = config.bbox or (_dataset_bounds(ds_for_bbox) if ds_for_bbox is not None and not config.boundary_path else None)
    df = _filter_boundary(df, config.lat_col, config.lon_col, config.boundary_path, bbox)
    return df.reset_index(drop=True)


def _time_range(ds: xr.Dataset) -> Tuple[pd.Timestamp, pd.Timestamp]:
    vals = pd.to_datetime(ds["time"].values)
    return pd.Timestamp(vals.min()), pd.Timestamp(vals.max())


def _prefilter_time(df: pd.DataFrame, config: MultidimAnnotationConfig, required_datasets: Sequence[xr.Dataset]) -> pd.DataFrame:
    starts, ends = [], []
    for ds in required_datasets:
        if ds is None or "time" not in ds:
            continue
        try:
            start, end = _time_range(ds)
            starts.append(start)
            ends.append(end)
        except Exception:
            pass
    if not starts or not ends:
        return df
    start, end = max(starts), min(ends)
    if start > end:
        raise ValueError(f"Input NetCDF files have no overlapping time range: latest start={start}, earliest end={end}")
    return df[df[config.time_col].between(start, end)].copy()


def _surface_height(config: MultidimAnnotationConfig, terrain: float) -> float:
    if np.isfinite(terrain):
        return float(terrain) + float(config.surface_height_agl_m)
    return float(config.surface_height_agl_m)


def _slice_coord(ds: xr.Dataset, coord: str, low: float, high: float) -> xr.Dataset:
    if coord not in ds.coords and coord not in ds.variables:
        return ds
    vals = np.asarray(ds[coord].values, dtype=float)
    if vals.size < 2:
        return ds
    lo, hi = float(min(low, high)), float(max(low, high))
    try:
        if vals[0] <= vals[-1]:
            return ds.sel({coord: slice(lo, hi)})
        return ds.sel({coord: slice(hi, lo)})
    except Exception:
        return ds

def _slice_time_with_bracket(ds: xr.Dataset, tmin: pd.Timestamp, tmax: pd.Timestamp) -> xr.Dataset:
    """
    Subset dataset by movement time range, but keep one neighbouring NetCDF
    timestep before and after the movement range when possible.

    This is important for linear time interpolation: if movement timestamps fall
    between two NetCDF timesteps, a strict slice(tmin, tmax) may remove the
    required bracketing timesteps.
    """
    if "time" not in ds.coords and "time" not in ds.variables:
        return ds

    try:
        times = pd.to_datetime(ds["time"].values)
        if len(times) == 0:
            return ds

        arr = times.to_numpy(dtype="datetime64[ns]")
        start = np.datetime64(pd.Timestamp(tmin).to_datetime64()).astype("datetime64[ns]")
        end = np.datetime64(pd.Timestamp(tmax).to_datetime64()).astype("datetime64[ns]")

        # Assumes time is sorted ascending, which should normally be true after open_dataset().
        left = int(np.searchsorted(arr, start, side="left"))
        right = int(np.searchsorted(arr, end, side="right")) - 1

        left = max(0, left - 1)
        right = min(len(arr) - 1, right + 1)

        if right < left:
            return ds

        return ds.isel({"time": slice(left, right + 1)})
    except Exception:
        try:
            return ds.sel({"time": slice(tmin, tmax)})
        except Exception:
            return ds

def subset_dataset_to_movement(ds: Optional[xr.Dataset], movement: pd.DataFrame, config: MultidimAnnotationConfig, buffer_deg: float = 1.0) -> Optional[xr.Dataset]:
    if ds is None or movement.empty:
        return ds
    out = ds
    try:
        if "time" in out.coords and config.time_col in movement.columns:
            tmin = pd.Timestamp(movement[config.time_col].min())
            tmax = pd.Timestamp(movement[config.time_col].max())
            out = _slice_time_with_bracket(out, tmin, tmax)
    except Exception:
        pass
    try:
        if "lat" in out.coords and config.lat_col in movement.columns:
            lat_min = float(movement[config.lat_col].min()) - buffer_deg
            lat_max = float(movement[config.lat_col].max()) + buffer_deg
            out = _slice_coord(out, "lat", lat_min, lat_max)
    except Exception:
        pass
    try:
        if "lon" in out.coords and config.lon_col in movement.columns:
            lon_vals = np.asarray(out["lon"].values, dtype=float)
            lon_series = movement[config.lon_col].astype(float).map(lambda x: _wrap_lon(float(x), lon_vals))
            lon_min = float(lon_series.min()) - buffer_deg
            lon_max = float(lon_series.max()) + buffer_deg
            if lon_max - lon_min < 350:
                out = _slice_coord(out, "lon", lon_min, lon_max)
    except Exception:
        pass
    return out


def _geopotential_cache_key(t: pd.Timestamp, lat: float, lon: float, time_method: str, fixed_lat: Optional[float], fixed_lon: Optional[float]) -> Tuple[Any, ...]:
    lat_key = round(float(lat if fixed_lat is None else fixed_lat), 6)
    lon_key = round(float(lon if fixed_lon is None else fixed_lon), 6)
    t_key = pd.Timestamp(t).to_datetime64()
    return (t_key, lat_key, lon_key, str(time_method))


def _get_geopotential_profile_cached(
    ds_geo: xr.Dataset,
    geo_name: str,
    row: pd.Series,
    config: MultidimAnnotationConfig,
    variable_type: VariableType,
    cache: Optional[Dict[Tuple[Any, ...], Tuple[np.ndarray, np.ndarray]]] = None,
    fixed_lat: Optional[float] = None,
    fixed_lon: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    t = pd.Timestamp(row[config.time_col])
    lat = float(row[config.lat_col])
    lon = float(row[config.lon_col])
    time_method: Literal["nearest", "linear"] = "nearest" if variable_type == "categorical" else "linear"
    key = _geopotential_cache_key(t, lat, lon, time_method, fixed_lat, fixed_lon)
    if cache is not None and key in cache:
        return cache[key]
    levels_geo, heights = sample_geopotential_profile(
        ds_geo,
        geo_name,
        t=t,
        lat=lat,
        lon=lon,
        units_override=config.geopotential_units,
        convert_geopotential_to_height=config.convert_geopotential_to_height,
        gravity_constant=config.gravity_constant,
        time_method=time_method,
        spatial_method="nearest",
        fixed_lat=fixed_lat,
        fixed_lon=fixed_lon,
    )
    if cache is not None:
        cache[key] = (levels_geo, heights)
    return levels_geo, heights


def _sample_var_at_cell(
    ds_var: xr.Dataset,
    var_name: str,
    ds_geo: xr.Dataset,
    geo_name: str,
    row: pd.Series,
    config: MultidimAnnotationConfig,
    target_height: float,
    terrain: float,
    variable_type: VariableType,
    surface_value: Optional[float] = None,
    fixed_lat: Optional[float] = None,
    fixed_lon: Optional[float] = None,
    geo_cache: Optional[Dict[Tuple[Any, ...], Tuple[np.ndarray, np.ndarray]]] = None,
) -> Tuple[float, Dict[str, Any]]:
    t = pd.Timestamp(row[config.time_col])
    lat = float(row[config.lat_col])
    lon = float(row[config.lon_col])
    time_method: Literal["nearest", "linear"] = "nearest" if variable_type == "categorical" else "linear"
    spatial_xr: Literal["nearest", "linear"] = "nearest"

    levels_geo, heights = _get_geopotential_profile_cached(
        ds_geo,
        geo_name,
        row,
        config,
        variable_type,
        geo_cache,
        fixed_lat=fixed_lat,
        fixed_lon=fixed_lon,
    )
    levels_var, values = sample_level_profile(
        ds_var,
        var_name,
        t=t,
        lat=lat,
        lon=lon,
        time_method=time_method,
        spatial_method=spatial_xr,
        fixed_lat=fixed_lat,
        fixed_lon=fixed_lon,
    )
    if len(levels_geo) != len(levels_var) or not np.array_equal(np.asarray(levels_geo), np.asarray(levels_var)):
        geo_map = {str(k): v for k, v in zip(levels_geo, heights)}
        heights_for_values = np.asarray([geo_map.get(str(lev), np.nan) for lev in levels_var], dtype=float)
    else:
        heights_for_values = np.asarray(heights, dtype=float)
    sv = surface_value if (config.use_surface_as_lower_anchor and variable_type == "continuous") else None
    sh = _surface_height(config, terrain) if sv is not None else None
    return vertical_sample(
        levels_var,
        heights_for_values,
        values,
        target_height,
        method=config.vertical_method,
        variable_type=variable_type,
        surface_value=sv,
        surface_height_m=sh,
        allow_extrapolation=config.allow_vertical_extrapolation,
    )


def _sample_multilevel(
    ds_var: xr.Dataset,
    var_name: str,
    ds_geo: xr.Dataset,
    geo_name: str,
    row: pd.Series,
    config: MultidimAnnotationConfig,
    target_height: float,
    terrain: float,
    variable_type: VariableType,
    surface_value: Optional[float] = None,
    geo_cache: Optional[Dict[Tuple[Any, ...], Tuple[np.ndarray, np.ndarray]]] = None,
) -> Tuple[float, Dict[str, Any]]:
    if variable_type == "categorical" or config.spatial_method == "nearest":
        return _sample_var_at_cell(
            ds_var,
            var_name,
            ds_geo,
            geo_name,
            row,
            config,
            target_height,
            terrain,
            variable_type,
            surface_value,
            geo_cache=geo_cache,
        )

    lat = float(row[config.lat_col])
    lon = float(row[config.lon_col])
    glat = np.asarray(ds_var["lat"].values, dtype=float)
    glon = np.asarray(ds_var["lon"].values, dtype=float)
    lon_adj = _wrap_lon(lon, glon)
    k = max(2, int(config.smoothing_k))
    samples, dists, last_diag = [], [], {}
    for ii, jj in _k_nearest_indices(glat, glon, lat, lon_adj, k):
        flat, flon = float(glat[ii]), float(glon[jj])
        val, diag = _sample_var_at_cell(
            ds_var,
            var_name,
            ds_geo,
            geo_name,
            row,
            config,
            target_height,
            terrain,
            variable_type,
            surface_value,
            fixed_lat=flat,
            fixed_lon=flon,
            geo_cache=geo_cache,
        )
        samples.append(val)
        dists.append(float(np.hypot(flat - lat, flon - lon_adj)))
        last_diag = diag
    return _idw(samples, dists), last_diag


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    if not all(np.isfinite([lat1, lon1, lat2, lon2])):
        return np.nan
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def add_track_bearing(df: pd.DataFrame, id_col: str, time_col: str, lat_col: str, lon_col: str, heading_col: Optional[str], heading_source: str) -> pd.DataFrame:
    out = df.copy()
    if heading_source == "column" and heading_col and heading_col in out.columns:
        out["track_bearing_deg"] = pd.to_numeric(out[heading_col], errors="coerce")
        return out
    out["track_bearing_deg"] = np.nan
    sort_cols = [id_col, time_col] if id_col in out.columns else [time_col]
    work = out.sort_values(sort_cols)
    groups = work.groupby(id_col, dropna=False, sort=False) if id_col in work.columns else [(None, work)]
    for _, group in groups:
        idxs = list(group.index)
        for i, idx in enumerate(idxs):
            if i < len(idxs) - 1:
                nxt = idxs[i + 1]
                b = _bearing_deg(out.at[idx, lat_col], out.at[idx, lon_col], out.at[nxt, lat_col], out.at[nxt, lon_col])
            elif i > 0:
                prv = idxs[i - 1]
                b = _bearing_deg(out.at[prv, lat_col], out.at[prv, lon_col], out.at[idx, lat_col], out.at[idx, lon_col])
            else:
                b = np.nan
            out.at[idx, "track_bearing_deg"] = b
    return out


def add_wind_metrics(df: pd.DataFrame, u_col: str = "td_u_at_height", v_col: str = "td_v_at_height") -> pd.DataFrame:
    out = df.copy()
    u = pd.to_numeric(out[u_col], errors="coerce") if u_col in out.columns else pd.Series(np.nan, index=out.index)
    v = pd.to_numeric(out[v_col], errors="coerce") if v_col in out.columns else pd.Series(np.nan, index=out.index)
    out["wind_speed_ms"] = np.sqrt(u * u + v * v)
    wind_to = (np.degrees(np.arctan2(u, v)) + 360.0) % 360.0
    out["wind_to_direction_deg"] = wind_to
    out["wind_from_direction_deg"] = (wind_to + 180.0) % 360.0
    if "track_bearing_deg" in out.columns:
        theta = np.radians(pd.to_numeric(out["track_bearing_deg"], errors="coerce"))
        out["wind_support_ms"] = u * np.sin(theta) + v * np.cos(theta)
        out["crosswind_ms"] = u * np.cos(theta) - v * np.sin(theta)
    return out


def _var_type(var: str, spec: DatasetSpec) -> VariableType:
    return "categorical" if var in set(spec.categorical or []) else "continuous"

def _fast_required_dims(da: xr.DataArray, required: Sequence[str], variable: str) -> xr.DataArray:
    """
    Prepare a DataArray for fast numpy sampling.

    Only singleton non-standard dimensions are dropped. If a variable has
    genuinely extra dimensions, fast mode refuses it and the caller can fall
    back to the old xarray-based algorithm.
    """
    out = da

    for dim in list(out.dims):
        if dim not in {"time", "level", "lat", "lon"}:
            if int(out.sizes.get(dim, 0)) == 1:
                out = out.isel({dim: 0}, drop=True)
            else:
                raise ValueError(
                    f"Fast mode does not support variable '{variable}' with extra dimension '{dim}'."
                )

    missing = [d for d in required if d not in out.dims]
    if missing:
        raise ValueError(
            f"Fast mode requires variable '{variable}' to have dimensions: {required}. "
            f"Missing: {missing}. Actual dims: {list(out.dims)}"
        )

    return out


def _fast_open_4d(ds: xr.Dataset, variable: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Return variable as numpy array with shape:
    time, level, lat, lon
    """
    if variable not in ds.data_vars:
        raise ValueError(f"Variable '{variable}' not found in dataset.")

    da = _fast_required_dims(ds[variable], ("time", "level", "lat", "lon"), variable)
    da = da.transpose("time", "level", "lat", "lon")

    arr = np.asarray(da.load().values, dtype=float)
    times = pd.to_datetime(da["time"].values).to_numpy(dtype="datetime64[ns]")
    levels = np.asarray(da["level"].values)
    lat = np.asarray(da["lat"].values, dtype=float)
    lon = np.asarray(da["lon"].values, dtype=float)

    return arr, times, levels, lat, lon


def _fast_open_3d(ds: xr.Dataset, variable: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Return surface variable as numpy array with shape:
    time, lat, lon
    """
    if variable not in ds.data_vars:
        raise ValueError(f"Variable '{variable}' not found in dataset.")

    da = _fast_required_dims(ds[variable], ("time", "lat", "lon"), variable)
    da = da.transpose("time", "lat", "lon")

    arr = np.asarray(da.load().values, dtype=float)
    times = pd.to_datetime(da["time"].values).to_numpy(dtype="datetime64[ns]")
    lat = np.asarray(da["lat"].values, dtype=float)
    lon = np.asarray(da["lon"].values, dtype=float)

    return arr, times, lat, lon


def _fast_time_index_weight(
    times: np.ndarray,
    target: pd.Timestamp,
    *,
    method: Literal["nearest", "linear"],
) -> Tuple[int, int, float]:
    """
    Return t0, t1, weight for fast temporal sampling.

    For nearest:
        value = arr[t0]
    For linear:
        value = arr[t0] * (1 - w) + arr[t1] * w
    """
    if len(times) == 0:
        raise ValueError("Cannot sample dataset with empty time coordinate.")

    arr = np.asarray(times).astype("datetime64[ns]")
    target64 = np.datetime64(pd.Timestamp(target).to_datetime64()).astype("datetime64[ns]")

    if method == "nearest" or len(arr) == 1:
        diffs = np.abs(arr.astype("int64") - target64.astype("int64"))
        idx = int(np.nanargmin(diffs))
        return idx, idx, 0.0

    right = int(np.searchsorted(arr, target64, side="left"))

    if right <= 0:
        return 0, 0, 0.0
    if right >= len(arr):
        last = len(arr) - 1
        return last, last, 0.0
    if arr[right] == target64:
        return right, right, 0.0

    left = right - 1
    t0 = arr[left].astype("int64")
    t1 = arr[right].astype("int64")
    tt = target64.astype("int64")

    if t1 == t0:
        return left, right, 0.0

    w = float((tt - t0) / (t1 - t0))
    return left, right, w


def _fast_nearest_lat_lon_indices(
    lat_values: np.ndarray,
    lon_values: np.ndarray,
    lat: float,
    lon: float,
) -> Tuple[int, int]:
    lon_adj = _wrap_lon(float(lon), lon_values)
    yi = _nearest_index(np.asarray(lat_values, dtype=float), float(lat))
    xi = _nearest_index(np.asarray(lon_values, dtype=float), lon_adj)
    return int(yi), int(xi)


def _fast_sample_4d_profile(
    arr: np.ndarray,
    times: np.ndarray,
    lat_values: np.ndarray,
    lon_values: np.ndarray,
    *,
    t: pd.Timestamp,
    lat: float,
    lon: float,
    variable_type: VariableType,
) -> np.ndarray:
    time_method: Literal["nearest", "linear"] = "nearest" if variable_type == "categorical" else "linear"
    t0, t1, w = _fast_time_index_weight(times, t, method=time_method)
    yi, xi = _fast_nearest_lat_lon_indices(lat_values, lon_values, lat, lon)

    v0 = arr[t0, :, yi, xi]
    if t1 == t0 or w == 0.0:
        return np.asarray(v0, dtype=float)

    v1 = arr[t1, :, yi, xi]
    return np.asarray(v0 * (1.0 - w) + v1 * w, dtype=float)


def _fast_sample_3d_value(
    arr: np.ndarray,
    times: np.ndarray,
    lat_values: np.ndarray,
    lon_values: np.ndarray,
    *,
    t: pd.Timestamp,
    lat: float,
    lon: float,
    variable_type: VariableType,
) -> float:
    time_method: Literal["nearest", "linear"] = "nearest" if variable_type == "categorical" else "linear"
    t0, t1, w = _fast_time_index_weight(times, t, method=time_method)
    yi, xi = _fast_nearest_lat_lon_indices(lat_values, lon_values, lat, lon)

    v0 = float(arr[t0, yi, xi])
    if t1 == t0 or w == 0.0:
        return v0

    v1 = float(arr[t1, yi, xi])
    return float(v0 * (1.0 - w) + v1 * w)


def _fast_heights_for_variable_levels(
    geo_levels: np.ndarray,
    geo_heights: np.ndarray,
    var_levels: np.ndarray,
) -> np.ndarray:
    """
    Match geopotential-derived heights to variable levels.

    If levels are identical and in the same order, return heights directly.
    Otherwise match by string representation of the level coordinate.
    """
    if len(geo_levels) == len(var_levels) and np.array_equal(np.asarray(geo_levels), np.asarray(var_levels)):
        return np.asarray(geo_heights, dtype=float)

    geo_map = {str(k): v for k, v in zip(geo_levels, geo_heights)}
    return np.asarray([geo_map.get(str(lev), np.nan) for lev in var_levels], dtype=float)

_RHO_AIR = 1.225   # kg/m³, standard sea-level air density
_CP_AIR  = 1005.0  # J/(kg·K), specific heat of dry air at constant pressure
_G       = 9.80665     # m/s², gravitational acceleration
_R_DRY   = 287.05  # J/(kg·K), specific gas constant for dry air

def compute_thermal_updraft_w_star(
    surface_heat_flux_wm2: float,
    boundary_layer_height_m: float,
    temperature_2m_K: float,
) -> float:
    """
    Deardorff convective velocity scale w* (m/s).

    Standard measure of thermal updraft intensity used by Movebank ENV-DATA
    and described in Bohrer et al. (2012, Ecology Letters).

        w* = (g/T * (H / (rho * cp)) * zi) ^ (1/3)

    Args:
        surface_heat_flux_wm2:   ERA5 surface sensible heat flux (W/m²).
                                  Positive = surface heating the atmosphere = uplift.
        boundary_layer_height_m: ERA5 planetary boundary layer height (m).
        temperature_2m_K:        ERA5 2-metre temperature (K), used as a proxy
                                  for surface potential temperature.

    Returns:
        w* in m/s. Returns 0.0 when heat flux <= 0 (no convection).
        Returns NaN when any input is missing or physically invalid.
    """
    H  = float(surface_heat_flux_wm2)
    zi = float(boundary_layer_height_m)
    T  = float(temperature_2m_K)

    if not (np.isfinite(H) and np.isfinite(zi) and np.isfinite(T)):
        return np.nan
    if T <= 0.0 or zi <= 0.0:
        return np.nan
    if H <= 0.0:
        return 0.0  # stable or neutral atmosphere: no convective uplift

    H_kinematic   = H / (_RHO_AIR * _CP_AIR)   # kinematic heat flux (K·m/s)
    w_star_cubed  = (_G / T) * H_kinematic * zi
    return float(w_star_cubed ** (1.0 / 3.0))

def _finalize_and_save_annotation_output(out: pd.DataFrame, config: MultidimAnnotationConfig) -> pd.DataFrame:
    """Finalize derived metrics, save CSV output, and return the annotated DataFrame."""

    if config.derive_wind_speed_direction or config.derive_wind_support_crosswind:
        if "td_u_at_height" in out.columns and "td_v_at_height" in out.columns:
            if config.derive_wind_support_crosswind:
                out = add_track_bearing(
                    out,
                    config.id_col,
                    config.time_col,
                    config.lat_col,
                    config.lon_col,
                    config.heading_col,
                    config.heading_source,
                )
            out = add_wind_metrics(out)

    # --- Vertical motion: convert ERA5 omega (Pa/s) to geometric w (m/s) ---
    if config.derive_vertical_motion and "td_w_at_height" in out.columns:
        omega = pd.to_numeric(out["td_w_at_height"], errors="coerce")

        has_temp  = "td_temperature_at_height" in out.columns
        # The matched pressure level is stored in hPa by vertical_sample diagnostics.
        # Column name pattern: <var>_matched_level_height_m is the height;
        # we need the pressure level itself which vertical_sample stores as matched_level.
        level_col = next(
            (c for c in out.columns if c.endswith("_matched_level") and "height" not in c),
            None,
        )
        has_level = level_col is not None

        if has_temp and has_level:
            T_K  = pd.to_numeric(out["td_temperature_at_height"], errors="coerce")
            P_Pa = pd.to_numeric(out[level_col], errors="coerce") * 100.0  # hPa -> Pa
            rho  = P_Pa / (_R_DRY * T_K)
            out["vertical_motion_ms"]        = -omega / (rho * _G)
            out["vertical_motion_omega_Pa_s"] = omega
            out["vertical_motion_note"] = (
                "vertical_motion_ms: ERA5 omega (Pa/s) converted to geometric "
                "vertical velocity (m/s) via w = -omega / (rho * g), "
                "rho = P / (R_dry * T). Positive = upward."
            )
        else:
            # Fall back to a standard-atmosphere approximation (rho ~ 1.0 kg/m³)
            # valid roughly between 1 and 10 km altitude.
            out["vertical_motion_ms"]        = -omega / (1.0 * _G)
            out["vertical_motion_omega_Pa_s"] = omega
            out["vertical_motion_note"] = (
                "WARNING: vertical_motion_ms estimated with rho=1.0 kg/m3 "
                "(standard atmosphere approximation). For accurate conversion "
                "provide temperature and pressure level data. Positive = upward."
            )

    # --- Thermal updraft: Deardorff w* ---
    if config.derive_thermal_proxy:
        has_shf  = "surface_surface_sensible_heat_flux" in out.columns
        has_blh  = "surface_boundary_layer_height" in out.columns
        has_t2m  = "surface_2m_temperature" in out.columns

        if has_shf and has_blh and has_t2m:
            out["thermal_updraft_w_star_ms"] = [
                compute_thermal_updraft_w_star(
                    row["surface_surface_sensible_heat_flux"],
                    row["surface_boundary_layer_height"],
                    row["surface_2m_temperature"],
                )
                for _, row in out.iterrows()
            ]
            out["thermal_updraft_note"] = (
                "Deardorff convective velocity scale w* (m/s). "
                "Positive = convective uplift available. "
                "Method: Bohrer et al. 2012 / Movebank ENV-DATA."
            )
        elif "td_temperature_at_height" in out.columns:
            out["temperature_at_height_K"]  = out["td_temperature_at_height"]
            out["thermal_updraft_note"] = (
                "WARNING: w* not computed. Requires surface variables: "
                "surface_sensible_heat_flux, boundary_layer_height, 2m_temperature. "
                "Storing raw temperature at flight height instead."
            )

    # --- Orographic uplift: Bohrer et al. 2012 ---
    if config.derive_orographic_uplift and config.dem_file:
        has_u10 = "surface_u_component_of_wind_10m" in out.columns
        has_v10 = "surface_v_component_of_wind_10m" in out.columns

        if has_u10 and has_v10:
            slopes_aspects = [
                compute_dem_slope_aspect(
                    config.dem_file,
                    float(row[config.lat_col]),
                    float(row[config.lon_col]),
                )
                for _, row in out.iterrows()
            ]
            out["orographic_uplift_ms"] = [
                compute_orographic_uplift(
                    float(row["surface_u_component_of_wind_10m"]),
                    float(row["surface_v_component_of_wind_10m"]),
                    sa[0],
                    sa[1],
                )
                for (_, row), sa in zip(out.iterrows(), slopes_aspects)
            ]
            out["orographic_uplift_note"] = (
                "Wo = V_surface * sin(slope) * cos(wind_from - aspect) (m/s). "
                "Method: Bohrer et al. 2012 / Movebank ENV-DATA. "
                "Positive = updraft on windward slope."
            )
        else:
            out["orographic_uplift_ms"]   = np.nan
            out["orographic_uplift_note"] = (
                "WARNING: orographic uplift not computed. "
                "Add surface_u_component_of_wind_10m and "
                "surface_v_component_of_wind_10m as surface variables."
            )

    output_path = _as_path(config.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d %H:%M:%S")

    if config.save_per_individual and config.id_col in out.columns:
        per_dir = output_path.with_suffix("").parent / f"{output_path.stem}_by_individual"
        per_dir.mkdir(parents=True, exist_ok=True)
        for ident, group in out.groupby(config.id_col, dropna=False):
            safe = re.sub(r"[^\w\-]", "_", str(ident).strip()) or "unknown"
            group.to_csv(
                per_dir / f"{safe}.csv",
                index=False,
                encoding="utf-8-sig",
                date_format="%Y-%m-%d %H:%M:%S",
            )

    return out


class _FastSampler:
    """
    Samples pre-loaded numpy arrays directly.
    Used for spatial nearest-neighbour mode.
    """

    def __init__(
        self,
        ds_geo: xr.Dataset,
        ds_main: xr.Dataset,
        ds_surface: Optional[xr.Dataset],
        ds_u: Optional[xr.Dataset],
        ds_v: Optional[xr.Dataset],
        ds_w: Optional[xr.Dataset],
        ds_t: Optional[xr.Dataset],
        config: MultidimAnnotationConfig,
    ) -> None:
        self._config = config
        self._geo_arr, self._geo_times, self._geo_levels, self._geo_lat, self._geo_lon = (
            _fast_open_4d(ds_geo, config.geopotential_variable)
        )
        
        if config.convert_geopotential_to_height:
            units = (
                config.geopotential_units
                or ds_geo[config.geopotential_variable].attrs.get("units")
                or ""
            ).lower().replace("**", "^").replace("/", " ")
            is_height = units.strip() in {"m", "meter", "meters", "metre", "metres"}
            if not is_height:
                self._geo_arr = self._geo_arr / float(config.gravity_constant)

        main_vars = _unique(
            config.multilevel.continuous,
            config.multilevel.categorical,
            config.multilevel.variables,
        )
        self._main_arrays: Dict[str, tuple] = {
            var: _fast_open_4d(ds_main, var) for var in main_vars
        }

        self._surface_arrays: Dict[str, tuple] = {}
        if config.surface and ds_surface is not None:
            surface_vars = _unique(
                config.surface.continuous,
                config.surface.categorical,
                config.surface.variables,
            )
            for var in surface_vars:
                self._surface_arrays[var] = _fast_open_3d(ds_surface, var)

        self._component_arrays: List[Tuple[str, OptionalComponentSpec, tuple]] = []
        for label, ds, spec in (
            ("u", ds_u, config.u_component),
            ("v", ds_v, config.v_component),
            ("w", ds_w, config.w_component),
            ("temperature", ds_t, config.temperature_component),
        ):
            if ds is not None and spec.variable:
                self._component_arrays.append((label, spec, _fast_open_4d(ds, spec.variable)))

    def geo_profile(self, t: pd.Timestamp, lat: float, lon: float, vtype: VariableType) -> np.ndarray:
        return _fast_sample_4d_profile(
            self._geo_arr, self._geo_times, self._geo_lat, self._geo_lon,
            t=t, lat=lat, lon=lon, variable_type=vtype,
        )

    def var_profile(self, var: str, t: pd.Timestamp, lat: float, lon: float, vtype: VariableType) -> Tuple[np.ndarray, np.ndarray]:
        arr, times, levels, lat_vals, lon_vals = self._main_arrays[var]
        values = _fast_sample_4d_profile(
            arr, times, lat_vals, lon_vals,
            t=t, lat=lat, lon=lon, variable_type=vtype,
        )
        return levels, values

    def component_profile(self, label: str, t: pd.Timestamp, lat: float, lon: float) -> Tuple[np.ndarray, np.ndarray]:
        for lbl, _spec, arr_info in self._component_arrays:
            if lbl == label:
                arr, times, levels, lat_vals, lon_vals = arr_info
                values = _fast_sample_4d_profile(
                    arr, times, lat_vals, lon_vals,
                    t=t, lat=lat, lon=lon, variable_type="continuous",
                )
                return levels, values
        raise KeyError(label)

    def surface_value(self, var: str, t: pd.Timestamp, lat: float, lon: float, vtype: VariableType) -> float:
        s_arr, s_times, s_lat, s_lon = self._surface_arrays[var]
        return _fast_sample_3d_value(
            s_arr, s_times, s_lat, s_lon,
            t=t, lat=lat, lon=lon, variable_type=vtype,
        )

    @property
    def geo_levels(self) -> np.ndarray:
        return self._geo_levels

    @property
    def component_specs(self) -> List[Tuple[str, OptionalComponentSpec]]:
        return [(label, spec) for label, spec, _ in self._component_arrays]


class _XarraySampler:
    """
    Samples via xarray .sel/.interp on every point.
    Used as fallback or for IDW spatial mode.
    """

    def __init__(
        self,
        ds_geo: xr.Dataset,
        ds_main: xr.Dataset,
        ds_surface: Optional[xr.Dataset],
        ds_u: Optional[xr.Dataset],
        ds_v: Optional[xr.Dataset],
        ds_w: Optional[xr.Dataset],
        ds_t: Optional[xr.Dataset],
        config: MultidimAnnotationConfig,
    ) -> None:
        self._config = config
        self._ds_geo = ds_geo
        self._ds_main = ds_main
        self._ds_surface = ds_surface
        self._ds_components: Dict[str, Tuple[xr.Dataset, OptionalComponentSpec]] = {}
        for label, ds, spec in (
            ("u", ds_u, config.u_component),
            ("v", ds_v, config.v_component),
            ("w", ds_w, config.w_component),
            ("temperature", ds_t, config.temperature_component),
        ):
            if ds is not None and spec.variable:
                self._ds_components[label] = (ds, spec)

        self._geo_cache: Dict[Tuple[Any, ...], Tuple[np.ndarray, np.ndarray]] = {}

    def geo_profile(self, t: pd.Timestamp, lat: float, lon: float, vtype: VariableType) -> np.ndarray:
        _, heights = self._get_geo_cached(t, lat, lon, vtype)
        return heights

    def _get_geo_cached(self, t, lat, lon, vtype) -> Tuple[np.ndarray, np.ndarray]:
        key = _geopotential_cache_key(t, lat, lon, "nearest" if vtype == "categorical" else "linear", None, None)
        if key not in self._geo_cache:
            levels, heights = sample_geopotential_profile(
                self._ds_geo,
                self._config.geopotential_variable,
                t=t, lat=lat, lon=lon,
                units_override=self._config.geopotential_units,
                convert_geopotential_to_height=self._config.convert_geopotential_to_height,
                gravity_constant=self._config.gravity_constant,
                time_method="nearest" if vtype == "categorical" else "linear",
                spatial_method="nearest",
            )
            self._geo_cache[key] = (levels, heights)
        return self._geo_cache[key]

    def var_profile(self, var: str, t: pd.Timestamp, lat: float, lon: float, vtype: VariableType) -> Tuple[np.ndarray, np.ndarray]:
        return sample_level_profile(
            self._ds_main, var,
            t=t, lat=lat, lon=lon,
            time_method="nearest" if vtype == "categorical" else "linear",
            spatial_method="nearest",
        )

    def component_profile(self, label: str, t: pd.Timestamp, lat: float, lon: float) -> Tuple[np.ndarray, np.ndarray]:
        ds, spec = self._ds_components[label]
        return sample_level_profile(
            ds, spec.variable,
            t=t, lat=lat, lon=lon,
            time_method="linear",
            spatial_method="nearest",
        )

    def surface_value(self, var: str, t: pd.Timestamp, lat: float, lon: float, vtype: VariableType) -> float:
        return sample_surface_value(
            self._ds_surface, var,
            t=t, lat=lat, lon=lon,
            variable_type=vtype,
            spatial_method=self._config.spatial_method,
        )

    @property
    def geo_levels(self) -> np.ndarray:
        return np.asarray(self._ds_geo["level"].values)

    @property
    def component_specs(self) -> List[Tuple[str, OptionalComponentSpec]]:
        return [(label, spec) for label, (_, spec) in self._ds_components.items()]
    
    def sample_at_height(
        self,
        var: str,
        t: pd.Timestamp,
        lat: float,
        lon: float,
        vtype: VariableType,
        target_height: float,
        terrain: float,
        surface_value: Optional[float],
        config: MultidimAnnotationConfig,
    ) -> Tuple[float, Dict[str, Any]]:
        """IDW spatial sampling: delegates to _sample_multilevel which handles
        k-nearest neighbours and inverse distance weighting internally."""
        row = pd.Series({
            config.time_col: t,
            config.lat_col: lat,
            config.lon_col: lon,
        })
        return _sample_multilevel(
            self._ds_main,
            var,
            self._ds_geo,
            config.geopotential_variable,
            row,
            config,
            target_height,
            terrain,
            vtype,
            surface_value,
            geo_cache=self._geo_cache,
        )
    
def _process_single_point(
    idx: Any,
    row: pd.Series,
    sampler: Union[_FastSampler, _XarraySampler],
    config: MultidimAnnotationConfig,
    out: pd.DataFrame,
    main_vars: List[str],
    surface_vars: List[str],
    surface_anchor_var: Optional[str],
) -> Dict[str, Any]:
    """
    Annotate one movement point. Writes results into `out` in-place.
    Returns diagnostics dict for this point.
    """
    warnings_for_point: List[str] = []
    t = pd.Timestamp(row[config.time_col])
    lat = float(row[config.lat_col])
    lon = float(row[config.lon_col])

    # --- Terrain ---
    if config.dem_file:
        terrain, dem_warning = sample_dem_elevation(config.dem_file, lat, lon)
        if dem_warning and dem_warning != "dem_not_provided":
            warnings_for_point.append(dem_warning)
    else:
        terrain, dem_warning = np.nan, ""
    out.at[idx, "terrain_elevation_m"] = terrain

    # --- Height conversion ---
    height_msl, hdiag = compute_orthometric_height(
        row[config.height_col], lat, lon,
        height_reference=config.height_reference,
        geoid_mode=config.geoid_mode,
        constant_geoid_undulation_m=config.constant_geoid_undulation_m,
        geoid_grid_path=config.geoid_grid_path,
        terrain_elevation_m=terrain,
    )
    out.at[idx, "height_msl_m"] = height_msl
    if np.isfinite(height_msl) and np.isfinite(terrain):
        out.at[idx, "height_agl_m"] = height_msl - terrain
    if hdiag.get("height_conversion_warning"):
        warnings_for_point.append(str(hdiag["height_conversion_warning"]))

    row_diag: Dict[str, Any] = {**hdiag, "dem_warning": dem_warning}

    # --- Surface variables ---
    surface_values: Dict[str, Any] = {}
    for var in surface_vars:
        if config.surface is None:
            continue
        vtype = _var_type(var, config.surface)
        try:
            sval = sampler.surface_value(var, t, lat, lon, vtype)
            out.at[idx, f"surface_{var}"] = sval
            surface_values[var] = sval
        except Exception as exc:
            warnings_for_point.append(f"surface_{var}_failed:{exc}")

    #  Geopotential profile
    geo_profile_cache: Dict[VariableType, np.ndarray] = {}

    def _get_geo_heights(vtype: VariableType) -> np.ndarray:
        if vtype not in geo_profile_cache:
            geo_profile_cache[vtype] = sampler.geo_profile(t, lat, lon, vtype)
        return geo_profile_cache[vtype]

    # --- Main multilevel variables ---
    
    for var in main_vars:
        vtype = _var_type(var, config.multilevel)
        try:
            anchor = surface_values.get(surface_anchor_var) if (
                surface_anchor_var and vtype == "continuous" and config.use_surface_as_lower_anchor
            ) else None

            if hasattr(sampler, "sample_at_height") and config.spatial_method != "nearest":
                # IDW: horizontal and vertical sampling together
                val, diag = sampler.sample_at_height(
                    var, t, lat, lon, vtype, height_msl, terrain, anchor, config,
                )
            else:
                # Nearest: first profile, then vertical interpolation
                var_levels, values = sampler.var_profile(var, t, lat, lon, vtype)
                geo_heights = _get_geo_heights(vtype)
                heights_for_values = _fast_heights_for_variable_levels(
                    sampler.geo_levels, geo_heights, var_levels,
                )
                sh = _surface_height(config, terrain) if anchor is not None else None
                val, diag = vertical_sample(
                    var_levels, heights_for_values, values, height_msl,
                    method=config.vertical_method,
                    variable_type=vtype,
                    surface_value=anchor,
                    surface_height_m=sh,
                    allow_extrapolation=config.allow_vertical_extrapolation,
                )

            out.at[idx, f"td_{var}_at_height"] = val
            for k, v in diag.items():
                row_diag[f"{var}_{k}"] = v
            if diag.get("vertical_warning"):
                warnings_for_point.append(f"{var}:{diag['vertical_warning']}")

        except Exception as exc:
            warnings_for_point.append(f"{var}_sampling_failed:{exc}")

    # --- Wind/temperature components ---
    for label, _spec in sampler.component_specs:
        try:
            comp_levels, values = sampler.component_profile(label, t, lat, lon)
            geo_heights = _get_geo_heights("continuous")
            heights_for_values = _fast_heights_for_variable_levels(
                sampler.geo_levels, geo_heights, comp_levels,
            )
            val, diag = vertical_sample(
                comp_levels, heights_for_values, values, height_msl,
                method=config.vertical_method,
                variable_type="continuous",
                allow_extrapolation=config.allow_vertical_extrapolation,
            )
            out.at[idx, f"td_{label}_at_height"] = val
            if config.keep_diagnostics:
                for k, v in diag.items():
                    row_diag[f"{label}_{k}"] = v
            if diag.get("vertical_warning"):
                warnings_for_point.append(f"{label}:{diag['vertical_warning']}")

        except Exception as exc:
            warnings_for_point.append(f"{label}_sampling_failed:{exc}")

    out.at[idx, "annotation_warning"] = ";".join(w for w in warnings_for_point if w)
    return row_diag

def run_multidimensional_annotation(config: MultidimAnnotationConfig) -> pd.DataFrame:
    config.vertical_method = _normalize_vertical_method(config.vertical_method)  # type: ignore[assignment]
    config.spatial_method = _normalize_spatial_method(config.spatial_method)  # type: ignore[assignment]
    if config.spatial_method == "nearest":
        config.smoothing_k = 1

    ds_geo = open_dataset(config.geopotential_file, config.coord_spec)
    ds_main = open_dataset(config.multilevel.path, config.coord_spec)
    ds_surface = open_dataset(config.surface.path, config.coord_spec) if config.surface else None
    ds_u = open_dataset(config.u_component.path, config.coord_spec) if config.u_component.is_enabled() else None
    ds_v = open_dataset(config.v_component.path, config.coord_spec) if config.v_component.is_enabled() else None
    ds_w = open_dataset(config.w_component.path, config.coord_spec) if config.w_component.is_enabled() else None
    ds_t = open_dataset(config.temperature_component.path, config.coord_spec) if config.temperature_component.is_enabled() else None

    datasets_to_close = [ds_geo, ds_main, ds_surface, ds_u, ds_v, ds_w, ds_t]
    try:
        movement = _load_movement(config, ds_main)
        movement = _prefilter_time(movement, config, [ds_geo, ds_main])

        ds_geo = subset_dataset_to_movement(ds_geo, movement, config)
        ds_main = subset_dataset_to_movement(ds_main, movement, config)
        ds_surface = subset_dataset_to_movement(ds_surface, movement, config) if ds_surface is not None else None
        ds_u = subset_dataset_to_movement(ds_u, movement, config) if ds_u is not None else None
        ds_v = subset_dataset_to_movement(ds_v, movement, config) if ds_v is not None else None
        ds_w = subset_dataset_to_movement(ds_w, movement, config) if ds_w is not None else None
        ds_t = subset_dataset_to_movement(ds_t, movement, config) if ds_t is not None else None
        main_vars = _unique(
            config.multilevel.continuous,
            config.multilevel.categorical,
            config.multilevel.variables,
        )
        surface_vars = _unique(
            config.surface.continuous,
            config.surface.categorical,
            config.surface.variables,
        ) if config.surface else []
        surface_anchor_var = (
            config.surface.continuous[0]
            if (config.surface and config.surface.continuous)
            else None
        )

        if config.spatial_method == "nearest":
            try:
                sampler = _FastSampler(
                    ds_geo, ds_main, ds_surface,
                    ds_u, ds_v, ds_w, ds_t, config,
                )
            except Exception as exc:
                LOGGER.warning(
                    "FastSampler init failed (%s), falling back to XarraySampler.",
                    exc,
                    exc_info=True,
                )
                sampler = _XarraySampler(
                    ds_geo, ds_main, ds_surface,
                    ds_u, ds_v, ds_w, ds_t, config,
                )
        else:
            sampler = _XarraySampler(
                ds_geo, ds_main, ds_surface,
                ds_u, ds_v, ds_w, ds_t, config,
            )

        out = movement.copy()
        for col in ("terrain_elevation_m", "height_msl_m", "height_agl_m"):
            out[col] = np.nan
        out["annotation_warning"] = ""
        for var in main_vars:
            out[f"td_{var}_at_height"] = np.nan
        for var in surface_vars:
            out[f"surface_{var}"] = np.nan
        for label, _spec in sampler.component_specs:
            out[f"td_{label}_at_height"] = np.nan

        diag_rows: List[Dict[str, Any]] = []
        for idx, row in out.iterrows():
            row_diag = _process_single_point(
                idx, row, sampler, config, out,
                main_vars, surface_vars, surface_anchor_var,
            )
            diag_rows.append(row_diag)

        if config.keep_diagnostics and diag_rows:
            diag_df = pd.DataFrame(diag_rows, index=out.index)
            for col in diag_df.columns:
                if col not in out.columns:
                    out[col] = diag_df[col]

        return _finalize_and_save_annotation_output(out, config)
    finally:
        for ds in datasets_to_close:
            try:
                if ds is not None:
                    ds.close()
            except Exception:
                pass


def _list_or_empty(values: Optional[Sequence[str]]) -> List[str]:
    return list(values or [])


def run_multidimensional_annotation_from_paths(
    *,
    movement_csv: Union[str, Path],
    output_csv: Union[str, Path],
    id_col: str,
    time_col: str,
    lat_col: str,
    lon_col: str,
    height_col: str,
    geopotential_file: Union[str, Path],
    geopotential_variable: str,
    multilevel_var_file: Union[str, Path],
    multilevel_variable: Optional[str] = None,
    multilevel_continuous_vars: Optional[Sequence[str]] = None,
    multilevel_categorical_vars: Optional[Sequence[str]] = None,
    surface_var_file: Optional[Union[str, Path]] = None,
    surface_variable: Optional[str] = None,
    surface_continuous_vars: Optional[Sequence[str]] = None,
    surface_categorical_vars: Optional[Sequence[str]] = None,
    selected_ids: Optional[Sequence[str]] = None,
    boundary_path: Optional[Union[str, Path]] = None,
    bbox: Optional[Dict[str, float]] = None,
    coord_spec: Optional[Dict[str, Optional[str]]] = None,
    nc_time_var: Optional[str] = None,
    nc_lat_var: Optional[str] = None,
    nc_lon_var: Optional[str] = None,
    nc_level_var: Optional[str] = None,
    spatial_interpolation_method: str = "Nearest neighbor",
    smoothing_k: int = 1,
    vertical_matching_method: str = "Nearest geopotential-height level",
    geopotential_units: Optional[str] = "m2 s-2",
    convert_geopotential_to_height: bool = True,
    use_surface_as_lower_anchor: bool = True,
    surface_anchor_height_agl_m: float = 2.0,
    dem_file: Optional[Union[str, Path]] = None,
    save_per_individual: bool = False,
    keep_diagnostics: bool = True,
    height_reference: HeightReference = "ellipsoidal",
    geoid_mode: GeoidMode = "geographiclib",
    constant_geoid_undulation_m: float = 0.0,
    geoid_grid_path: Optional[Union[str, Path]] = None,
    u_file: Optional[Union[str, Path]] = None,
    u_variable: Optional[str] = None,
    v_file: Optional[Union[str, Path]] = None,
    v_variable: Optional[str] = None,
    w_file: Optional[Union[str, Path]] = None,
    w_variable: Optional[str] = None,
    temperature_file: Optional[Union[str, Path]] = None,
    temperature_variable: Optional[str] = None,
    derive_wind_speed_direction: bool = False,
    derive_wind_support_crosswind: bool = False,
    derive_vertical_motion: bool = False,
    derive_thermal_proxy: bool = False,
    derive_orographic_uplift: bool = False,
    heading_col: Optional[str] = None,
    heading_source: Literal["compute", "column"] = "compute",
) -> pd.DataFrame:
    if coord_spec is None:
        coord_spec = {"time": nc_time_var, "lat": nc_lat_var, "lon": nc_lon_var, "level": nc_level_var}
    coord_spec = {k: v for k, v in (coord_spec or {}).items() if v}

    ml_cont = _list_or_empty(multilevel_continuous_vars)
    ml_cat = _list_or_empty(multilevel_categorical_vars)
    if not ml_cont and not ml_cat and multilevel_variable:
        ml_cont = [multilevel_variable]
    ml_vars = _unique(ml_cont, ml_cat)
    if not ml_vars:
        raise ValueError("No multilevel variables selected.")

    surf_cont = _list_or_empty(surface_continuous_vars)
    surf_cat = _list_or_empty(surface_categorical_vars)
    if not surf_cont and not surf_cat and surface_variable:
        surf_cont = [surface_variable]
    surf_vars = _unique(surf_cont, surf_cat)

    surface = None
    if surface_var_file and surf_vars:
        surface = DatasetSpec(surface_var_file, variables=surf_vars, continuous=surf_cont, categorical=surf_cat, label_prefix="surface")

    config = MultidimAnnotationConfig(
        movement_csv=movement_csv,
        output_csv=output_csv,
        id_col=id_col,
        time_col=time_col,
        lat_col=lat_col,
        lon_col=lon_col,
        height_col=height_col,
        selected_ids=list(selected_ids or []) if selected_ids is not None else None,
        boundary_path=boundary_path,
        bbox=bbox,
        coord_spec=coord_spec,
        geopotential_file=geopotential_file,
        geopotential_variable=geopotential_variable,
        geopotential_units=geopotential_units,
        convert_geopotential_to_height=convert_geopotential_to_height,
        multilevel=DatasetSpec(multilevel_var_file, variables=ml_vars, continuous=ml_cont, categorical=ml_cat, label_prefix="td"),
        surface=surface,
        spatial_method=_normalize_spatial_method(spatial_interpolation_method),
        smoothing_k=int(smoothing_k or 1),
        vertical_method=_normalize_vertical_method(vertical_matching_method),
        use_surface_as_lower_anchor=use_surface_as_lower_anchor,
        surface_height_agl_m=float(surface_anchor_height_agl_m),
        dem_file=dem_file,
        save_per_individual=save_per_individual,
        keep_diagnostics=keep_diagnostics,
        height_reference=height_reference,
        geoid_mode=geoid_mode,
        constant_geoid_undulation_m=float(constant_geoid_undulation_m or 0.0),
        geoid_grid_path=geoid_grid_path,
        u_component=OptionalComponentSpec(u_file, u_variable, "u"),
        v_component=OptionalComponentSpec(v_file, v_variable, "v"),
        w_component=OptionalComponentSpec(w_file, w_variable, "w"),
        temperature_component=OptionalComponentSpec(temperature_file, temperature_variable, "temperature"),
        derive_wind_speed_direction=derive_wind_speed_direction,
        derive_wind_support_crosswind=derive_wind_support_crosswind,
        derive_vertical_motion=derive_vertical_motion,
        derive_thermal_proxy=derive_thermal_proxy,
        derive_orographic_uplift=derive_orographic_uplift,
        heading_col=heading_col,
        heading_source=heading_source,
    )
    return run_multidimensional_annotation(config)


def run_three_dim_annotation(*args: Any, **kwargs: Any) -> pd.DataFrame:
    return run_multidimensional_annotation(*args, **kwargs)


def sample_era5_at_height(*args: Any, **kwargs: Any) -> pd.DataFrame:
    raise NotImplementedError("Use run_multidimensional_annotation_from_paths() instead.")


__all__ = [
    "G0",
    "DatasetSpec",
    "OptionalComponentSpec",
    "MultidimAnnotationConfig",
    "parse_movebank_timestamp_series",
    "open_dataset",
    "sample_surface_value",
    "sample_level_profile",
    "sample_geopotential_profile",
    "vertical_sample",
    "sample_dem_elevation",
    "compute_orthometric_height",
    "add_track_bearing",
    "add_wind_metrics",
    "run_multidimensional_annotation",
    "run_multidimensional_annotation_from_paths",
    "run_three_dim_annotation",
]