"""
Compatibility-preserving backend for ECODATA NetCDF Builder.

The builder has one job: combine NetCDF files that already belong to the same
product/source structure. It does not standardize source files and does not try
to make incompatible files compatible.

Allowed differences by mode
---------------------------
By time
    Only values along the existing time coordinate may differ.
By level
    Only values along the existing vertical/level coordinate may differ.
    Time coordinate values must be identical.
By time and level
    Only values along the existing time and level coordinates may differ.
Multivariable
    Coordinate/grid structure is preserved. Shared coordinates must be identical,
    while distinct data variables from compatible files are merged into one Dataset.

Everything else must remain compatible: grid type, coordinate names, coordinate
metadata and dtypes, non-combined coordinate values, variable names, variable
dimensions/order, variable dtypes, and variable metadata. Source-identifying
global attributes are also compared when they are available in both files.

No renaming, unit conversion, longitude conversion, calendar conversion,
transposition, regridding, coordinate sorting, time subsetting, or data-variable
renaming is performed. An optional spatial subset may be applied *after* combine/merge
using a WGS84 bounding box or the WGS84 extent of a GeoJSON/Shapefile. The source
coordinate values and CRS are preserved; only grid indices are cropped.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import importlib.util
import json
import os

import numpy as np
import xarray as xr


NETCDF_EXTENSIONS = {".nc", ".nc4", ".cdf", ".netcdf"}
COMBINE_MODES = ("By time", "By level", "By time and level", "Multivariable")
SPATIAL_SUBSET_MODES = ("None", "BBOX", "GeoJSON / SHP extent")
VECTOR_EXTENSIONS = {".shp", ".geojson", ".json"}

TIME_CANDIDATES = (
    "time",
    "valid_time",
    "forecast_time",
    "verification_time",
    "datetime",
    "date",
    "Time",
    "XTIME",
)
LEVEL_CANDIDATES = (
    "level",
    "lev",
    "plev",
    "pressure",
    "pressure_level",
    "isobaricInhPa",
    "isobaric_in_hPa",
    "isobaricInPa",
    "isobaric_in_Pa",
    "model_level",
    "height",
    "altitude",
    "depth",
)
LAT_CANDIDATES = ("lat", "latitude", "Latitude", "XLAT")
LON_CANDIDATES = ("lon", "longitude", "Longitude", "long", "XLONG")
X_CANDIDATES = ("x", "X", "projection_x_coordinate", "easting", "eastings", "west_east")
Y_CANDIDATES = ("y", "Y", "projection_y_coordinate", "northing", "northings", "south_north")

# These attrs are useful for detecting an accidental mix of products/providers.
# They are compared only when present in both files; file-specific history/title
# fields are deliberately excluded.
SOURCE_IDENTITY_ATTRS = (
    "source",
    "institution",
    "institution_id",
    "centre",
    "center",
    "dataset",
    "dataset_id",
    "product",
    "product_id",
    "project",
    "provider",
    "origin",
)


@dataclass
class NCBuildConfig:
    files: List[str]
    combine_mode: str
    output_path: str = "combined.nc"
    open_engine: str = "auto"
    selected_variables: Optional[List[str]] = None
    subset_mode: str = "None"
    bbox: Optional[Dict[str, float]] = None
    boundary_path: Optional[str] = None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if np.isnan(value):
            return "__NaN__"
        if np.isposinf(value):
            return "__Inf__"
        if np.isneginf(value):
            return "__-Inf__"
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def _normalise_attrs(attrs: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k): _json_safe(v) for k, v in attrs.items()}


def _available_engines(preferred: str = "auto") -> List[Optional[str]]:
    candidates: List[Optional[str]] = []
    if preferred not in (None, "", "auto", "default"):
        candidates.append(preferred)
    candidates.extend([None, "h5netcdf", "netcdf4", "scipy"])

    result: List[Optional[str]] = []
    seen = set()
    for engine in candidates:
        key = engine or "default"
        if key in seen:
            continue
        seen.add(key)
        if engine == "h5netcdf" and importlib.util.find_spec("h5netcdf") is None:
            continue
        if engine == "netcdf4" and importlib.util.find_spec("netCDF4") is None:
            continue
        if engine == "scipy" and importlib.util.find_spec("scipy") is None:
            continue
        result.append(engine)
    return result


def _open_dataset_raw(path: str | Path, preferred_engine: str = "auto") -> Tuple[xr.Dataset, str]:
    """
    Open the source without CF decoding or scale/mask conversion.

    This is intentional: the builder must preserve the source numeric time
    representation, packing metadata, units, coordinate names, and dtypes.
    """
    path = Path(path).expanduser()
    tried: List[str] = []
    last_error: Optional[Exception] = None

    for engine in _available_engines(preferred_engine):
        label = engine or "default"
        tried.append(label)
        kwargs: Dict[str, Any] = {
            "decode_cf": False,
            "decode_times": False,
            "mask_and_scale": False,
        }
        if engine is not None:
            kwargs["engine"] = engine
        try:
            ds = xr.open_dataset(path, **kwargs)
            ds = _promote_detected_coordinate_variables(ds)
            return ds, label
        except Exception as exc:
            last_error = exc

    raise OSError(
        f"Could not open NetCDF file {path.name!r}. "
        f"Tried engines: {', '.join(tried)}. Last error: {last_error}"
    )


def _find_name(ds: xr.Dataset, candidates: Sequence[str]) -> Optional[str]:
    names = [str(n) for n in ds.variables] + [str(n) for n in ds.dims]
    lower = {n.lower(): n for n in names}
    for candidate in candidates:
        if candidate in names:
            return candidate
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    return None


def _detect_time_name(ds: xr.Dataset) -> Optional[str]:
    for name in ds.variables:
        var = ds[name]
        if str(var.attrs.get("standard_name", "")).strip().lower() == "time":
            return str(name)
        if str(var.attrs.get("axis", "")).strip().upper() == "T":
            return str(name)
    found = _find_name(ds, TIME_CANDIDATES)
    if found:
        return found
    for name in ds.variables:
        units = str(ds[name].attrs.get("units", "")).lower()
        if " since " in f" {units} ":
            return str(name)
    return None


def _detect_level_name(ds: xr.Dataset) -> Optional[str]:
    found = _find_name(ds, LEVEL_CANDIDATES)
    if found:
        return found
    for name in ds.variables:
        var = ds[name]
        if var.ndim > 1:
            continue
        standard_name = str(var.attrs.get("standard_name", "")).strip().lower()
        axis = str(var.attrs.get("axis", "")).strip().upper()
        positive = str(var.attrs.get("positive", "")).strip().lower()
        if axis == "Z" or standard_name in {
            "air_pressure",
            "height",
            "altitude",
            "depth",
            "model_level_number",
            "atmosphere_hybrid_sigma_pressure_coordinate",
        } or positive in {"up", "down"}:
            return str(name)
    return None


def _detect_lat_name(ds: xr.Dataset) -> Optional[str]:
    for name in ds.variables:
        var = ds[name]
        standard_name = str(var.attrs.get("standard_name", "")).strip().lower()
        units = str(var.attrs.get("units", "")).strip().lower()
        if standard_name == "latitude" or units in {
            "degrees_north", "degree_north", "degree_n", "degrees_n"
        }:
            return str(name)
    return _find_name(ds, LAT_CANDIDATES)


def _detect_lon_name(ds: xr.Dataset) -> Optional[str]:
    for name in ds.variables:
        var = ds[name]
        standard_name = str(var.attrs.get("standard_name", "")).strip().lower()
        units = str(var.attrs.get("units", "")).strip().lower()
        if standard_name == "longitude" or units in {
            "degrees_east", "degree_east", "degree_e", "degrees_e"
        }:
            return str(name)
    return _find_name(ds, LON_CANDIDATES)


def _detect_x_name(ds: xr.Dataset) -> Optional[str]:
    for name in ds.variables:
        if str(ds[name].attrs.get("standard_name", "")).strip().lower() == "projection_x_coordinate":
            return str(name)
    return _find_name(ds, X_CANDIDATES)


def _detect_y_name(ds: xr.Dataset) -> Optional[str]:
    for name in ds.variables:
        if str(ds[name].attrs.get("standard_name", "")).strip().lower() == "projection_y_coordinate":
            return str(name)
    return _find_name(ds, Y_CANDIDATES)


def _detect_grid_mapping_name(ds: xr.Dataset) -> Optional[str]:
    for name in ds.variables:
        if "grid_mapping_name" in ds[name].attrs:
            return str(name)
    for var in ds.data_vars.values():
        candidate = var.attrs.get("grid_mapping")
        if candidate and str(candidate) in ds.variables:
            return str(candidate)
    return None


def _detect_grid(ds: xr.Dataset) -> Dict[str, Optional[str]]:
    time_name = _detect_time_name(ds)
    level_name = _detect_level_name(ds)
    lat_name = _detect_lat_name(ds)
    lon_name = _detect_lon_name(ds)
    x_name = _detect_x_name(ds)
    y_name = _detect_y_name(ds)
    grid_mapping_name = _detect_grid_mapping_name(ds)

    grid_type = "Unknown"
    if lat_name in ds.variables and lon_name in ds.variables:
        lat = ds[lat_name]
        lon = ds[lon_name]
        if lat.ndim == 1 and lon.ndim == 1:
            grid_type = "Regular geographic 1D"
        elif lat.ndim == 2 and lon.ndim == 2:
            if (
                x_name in ds.variables
                and y_name in ds.variables
                and ds[x_name].ndim == 1
                and ds[y_name].ndim == 1
                and grid_mapping_name is not None
            ):
                grid_type = "Projected rectilinear"
            else:
                grid_type = "Curvilinear geographic"

    return {
        "grid_type": grid_type,
        "time_name": time_name,
        "level_name": level_name,
        "lat_name": lat_name,
        "lon_name": lon_name,
        "x_name": x_name,
        "y_name": y_name,
        "grid_mapping_name": grid_mapping_name,
    }


def _promote_detected_coordinate_variables(ds: xr.Dataset) -> xr.Dataset:
    """
    decode_cf=False preserves raw values/attrs but may classify scalar CF
    coordinates as data variables. Restore only their coordinate role in memory;
    names, values, dtypes and metadata remain unchanged.
    """
    roles = _detect_grid(ds)
    names = [
        roles.get("time_name"),
        roles.get("level_name"),
        roles.get("lat_name"),
        roles.get("lon_name"),
        roles.get("x_name"),
        roles.get("y_name"),
    ]
    to_promote = [name for name in names if name and name in ds.data_vars]
    if to_promote:
        ds = ds.set_coords(to_promote)
    return ds


def _source_identity(ds: xr.Dataset) -> Dict[str, Any]:
    return {
        key: _json_safe(ds.attrs.get(key))
        for key in SOURCE_IDENTITY_ATTRS
        if key in ds.attrs and ds.attrs.get(key) not in (None, "")
    }


def _horizontal_dims(ds: xr.Dataset, roles: Dict[str, Optional[str]]) -> set[str]:
    """Return the logical horizontal dimensions used by the detected grid."""
    dims: set[str] = set()
    for role in ("lat_name", "lon_name"):
        name = roles.get(role)
        if name and name in ds.variables:
            dims.update(map(str, ds[name].dims))

    # Projected grids are fundamentally indexed by y/x.  The 2-D auxiliary
    # latitude/longitude arrays normally use the same dimensions, but include
    # the native coordinate dimensions explicitly for robustness.
    if roles.get("grid_type") == "Projected rectilinear":
        for role in ("x_name", "y_name"):
            name = roles.get(role)
            if name and name in ds.variables:
                dims.update(map(str, ds[name].dims))
    return dims


def _is_physical_data_variable(
    ds: xr.Dataset, name: str, roles: Dict[str, Optional[str]]
) -> bool:
    """
    True for gridded environmental fields, False for technical auxiliary
    variables such as ERA5 ``expver``/``number`` or a scalar CRS variable.

    A physical field must depend on all logical horizontal grid dimensions.
    This keeps the rule generic across regular, curvilinear and projected grids
    without hard-coding particular auxiliary-variable names.
    """
    if name not in ds.data_vars:
        return False
    if name == roles.get("grid_mapping_name"):
        return False

    horizontal = _horizontal_dims(ds, roles)
    if not horizontal:
        return False
    return horizontal.issubset(set(map(str, ds[name].dims)))


def _physical_data_variables(
    ds: xr.Dataset, roles: Optional[Dict[str, Optional[str]]] = None
) -> List[str]:
    roles = roles or _detect_grid(ds)
    return [
        str(name)
        for name in ds.data_vars
        if _is_physical_data_variable(ds, str(name), roles)
    ]


def _auxiliary_data_variables(
    ds: xr.Dataset, roles: Optional[Dict[str, Optional[str]]] = None
) -> List[str]:
    roles = roles or _detect_grid(ds)
    physical = set(_physical_data_variables(ds, roles))
    return [str(name) for name in ds.data_vars if str(name) not in physical]


def _selected_physical_variables(config: NCBuildConfig) -> Optional[List[str]]:
    """Return normalized requested physical-variable names.

    ``None`` preserves legacy/programmatic behaviour: all physical data
    variables are kept. An explicit empty list means that the UI/user has not
    selected any physical field and is therefore invalid for building.
    """
    if config.selected_variables is None:
        return None
    out: List[str] = []
    for name in config.selected_variables:
        value = str(name)
        if value and value not in out:
            out.append(value)
    return out


def _filter_physical_data_variables(
    ds: xr.Dataset, selected: Optional[Sequence[str]]
) -> xr.Dataset:
    """Keep selected physical fields plus all coordinates/auxiliary variables.

    This never drops coordinate variables, expver/number-like auxiliary data,
    CRS/grid-mapping variables, or other non-physical service fields.
    """
    if selected is None:
        return ds
    roles = _detect_grid(ds)
    selected_set = set(map(str, selected))
    drop_names = [
        name for name in _physical_data_variables(ds, roles)
        if name not in selected_set
    ]
    return ds.drop_vars(drop_names) if drop_names else ds


def _validate_variable_selection(
    datasets: Sequence[xr.Dataset],
    paths: Sequence[Path],
    config: NCBuildConfig,
) -> Tuple[List[str], List[str], List[str]]:
    """Validate requested physical variables against the selected source files.

    For time/level concatenation every requested field must exist in every
    file. For Multivariable, requested fields may be distributed across files.
    """
    selected = _selected_physical_variables(config)
    errors: List[str] = []
    warnings: List[str] = []

    physical_by_file: List[List[str]] = []
    union: List[str] = []
    for ds in datasets:
        names = _physical_data_variables(ds, _detect_grid(ds))
        physical_by_file.append(names)
        for name in names:
            if name not in union:
                union.append(name)

    if selected is None:
        selected = list(union)

    if not selected:
        errors.append(
            "No physical data variables are selected. Scan variables and select at least one data variable."
        )
        return selected, errors, warnings

    unknown = [name for name in selected if name not in union]
    if unknown:
        errors.append(
            "Selected data variable(s) were not found in the scanned NetCDF files: "
            + ", ".join(unknown)
        )

    if config.combine_mode == "Multivariable":
        for name in selected:
            present = [paths[i].name for i, names in enumerate(physical_by_file) if name in names]
            if not present:
                continue
            if len(present) > 1:
                errors.append(
                    f"Selected physical data variable {name!r} occurs in more than one input file "
                    f"({', '.join(present)}). Multivariable requires each selected physical field to have one source file."
                )
        empty_contributors = [
            paths[i].name for i, names in enumerate(physical_by_file)
            if not set(names).intersection(selected)
        ]
        if empty_contributors:
            warnings.append(
                "These selected files contain none of the requested physical variables and will contribute only shared auxiliary/coordinate metadata: "
                + ", ".join(empty_contributors)
            )
    else:
        for i, names in enumerate(physical_by_file):
            missing = [name for name in selected if name not in names]
            if missing:
                errors.append(
                    f"{paths[i].name} is missing selected physical data variable(s): "
                    + ", ".join(missing)
                    + ". This combine mode requires every selected variable in every input file."
                )

    return selected, errors, warnings


def _shape_without_allowed(var: xr.DataArray, allowed_dims: set[str]) -> Tuple[Tuple[str, int], ...]:
    return tuple((str(dim), int(var.sizes[dim])) for dim in var.dims if dim not in allowed_dims)


def _array_equal_values(a: xr.DataArray, b: xr.DataArray) -> bool:
    if a.shape != b.shape or str(a.dtype) != str(b.dtype):
        return False
    av = np.asarray(a.values)
    bv = np.asarray(b.values)
    try:
        return bool(np.array_equal(av, bv, equal_nan=True))
    except TypeError:
        return bool(np.array_equal(av, bv))


def _axis_values(ds: xr.Dataset, name: str) -> np.ndarray:
    values = np.asarray(ds[name].values)
    return values.reshape(-1)


def _value_key(value: Any) -> Tuple[str, str]:
    arr = np.asarray(value)
    if arr.ndim == 0:
        value = arr.item()
    if isinstance(value, np.generic):
        value = value.item()
    return (type(value).__name__, repr(value))


def _axis_array_key(values: np.ndarray) -> Tuple[str, Tuple[int, ...], Tuple[Tuple[str, str], ...]]:
    arr = np.asarray(values)
    return (
        str(arr.dtype),
        tuple(int(x) for x in arr.shape),
        tuple(_value_key(v) for v in arr.reshape(-1)),
    )


def _strictly_increasing(values: np.ndarray) -> Optional[bool]:
    arr = np.asarray(values).reshape(-1)
    if arr.size < 2:
        return True
    try:
        if np.issubdtype(arr.dtype, np.number):
            return bool(np.all(np.diff(arr.astype("float64")) > 0))
        if np.issubdtype(arr.dtype, np.datetime64):
            return bool(np.all(np.diff(arr.astype("datetime64[ns]").astype("int64")) > 0))
    except Exception:
        return None
    try:
        return all(arr[i] < arr[i + 1] for i in range(len(arr) - 1))
    except Exception:
        return None


def _compare_source_identity(
    ref: xr.Dataset,
    cur: xr.Dataset,
    ref_file: Path,
    cur_file: Path,
) -> List[str]:
    errors: List[str] = []
    for key in SOURCE_IDENTITY_ATTRS:
        if key not in ref.attrs or key not in cur.attrs:
            continue
        rv = _json_safe(ref.attrs.get(key))
        cv = _json_safe(cur.attrs.get(key))
        if rv != cv:
            errors.append(
                f"Source metadata differs for global attribute {key!r}: "
                f"{ref_file.name}={rv!r}, {cur_file.name}={cv!r}. "
                "Do not combine files from different products/sources."
            )
    return errors


def _compare_dataset_structure(
    ref: xr.Dataset,
    cur: xr.Dataset,
    *,
    ref_file: Path,
    cur_file: Path,
    ref_roles: Dict[str, Optional[str]],
    cur_roles: Dict[str, Optional[str]],
    allowed_axes: set[str],
) -> List[str]:
    errors: List[str] = []

    if ref_roles["grid_type"] != cur_roles["grid_type"]:
        errors.append(
            f"Grid type differs: {ref_file.name}={ref_roles['grid_type']!r}, "
            f"{cur_file.name}={cur_roles['grid_type']!r}."
        )

    for role in ("time_name", "level_name", "lat_name", "lon_name", "x_name", "y_name", "grid_mapping_name"):
        if ref_roles.get(role) != cur_roles.get(role):
            errors.append(
                f"Coordinate/grid role {role} differs: "
                f"{ref_file.name}={ref_roles.get(role)!r}, {cur_file.name}={cur_roles.get(role)!r}."
            )

    if set(ref.dims) != set(cur.dims):
        errors.append(
            f"Dimension names differ: {ref_file.name}={list(ref.dims)}, "
            f"{cur_file.name}={list(cur.dims)}."
        )
    else:
        for dim in ref.dims:
            if dim in allowed_axes:
                continue
            if int(ref.sizes[dim]) != int(cur.sizes[dim]):
                errors.append(
                    f"Dimension size differs for {dim!r}: "
                    f"{ref_file.name}={int(ref.sizes[dim])}, {cur_file.name}={int(cur.sizes[dim])}."
                )

    ref_coord_names = set(map(str, ref.coords))
    cur_coord_names = set(map(str, cur.coords))
    if ref_coord_names != cur_coord_names:
        errors.append(
            f"Coordinate names differ: {ref_file.name}={sorted(ref_coord_names)}, "
            f"{cur_file.name}={sorted(cur_coord_names)}."
        )

    for name in sorted(ref_coord_names & cur_coord_names):
        a, b = ref[name], cur[name]
        if tuple(a.dims) != tuple(b.dims):
            errors.append(
                f"Coordinate {name!r} uses different dimensions: "
                f"{ref_file.name}={tuple(a.dims)}, {cur_file.name}={tuple(b.dims)}."
            )
            continue
        if str(a.dtype) != str(b.dtype):
            errors.append(
                f"Coordinate {name!r} dtype differs: "
                f"{ref_file.name}={a.dtype}, {cur_file.name}={b.dtype}."
            )
        if _normalise_attrs(a.attrs) != _normalise_attrs(b.attrs):
            errors.append(
                f"Coordinate {name!r} metadata differs between {ref_file.name} and {cur_file.name}."
            )
        if _shape_without_allowed(a, allowed_axes) != _shape_without_allowed(b, allowed_axes):
            errors.append(
                f"Coordinate {name!r} shape differs outside the allowed combine axis/axes."
            )

        values_may_differ = name in allowed_axes or any(dim in allowed_axes for dim in a.dims)
        if not values_may_differ and not _array_equal_values(a, b):
            if name == ref_roles.get("time_name"):
                errors.append(
                    f"Time coordinate values differ in {cur_file.name}. "
                    "The selected combine mode does not allow time values to differ."
                )
            elif name == ref_roles.get("level_name"):
                errors.append(
                    f"Level coordinate values differ in {cur_file.name}. "
                    "The selected combine mode does not allow level values to differ."
                )
            else:
                errors.append(
                    f"Coordinate {name!r} values differ between {ref_file.name} and {cur_file.name}."
                )

    ref_data_vars = set(map(str, ref.data_vars))
    cur_data_vars = set(map(str, cur.data_vars))
    if ref_data_vars != cur_data_vars:
        errors.append(
            f"Data-variable names differ: {ref_file.name}={sorted(ref_data_vars)}, "
            f"{cur_file.name}={sorted(cur_data_vars)}."
        )

    for name in sorted(ref_data_vars & cur_data_vars):
        a, b = ref[name], cur[name]
        if tuple(a.dims) != tuple(b.dims):
            errors.append(
                f"Variable {name!r} dimension order differs: "
                f"{ref_file.name}={tuple(a.dims)}, {cur_file.name}={tuple(b.dims)}."
            )
        if str(a.dtype) != str(b.dtype):
            errors.append(
                f"Variable {name!r} dtype differs: "
                f"{ref_file.name}={a.dtype}, {cur_file.name}={b.dtype}."
            )
        if _normalise_attrs(a.attrs) != _normalise_attrs(b.attrs):
            errors.append(
                f"Variable {name!r} metadata differs between {ref_file.name} and {cur_file.name}."
            )
        if _shape_without_allowed(a, allowed_axes) != _shape_without_allowed(b, allowed_axes):
            errors.append(
                f"Variable {name!r} shape differs outside the allowed combine axis/axes."
            )

    # Compare non-coordinate auxiliary variables that are not data variables only
    # through the complete variable-name set. Coordinate/data-variable checks above
    # already cover their relevant structure in ordinary CF NetCDF files.
    if set(map(str, ref.variables)) != set(map(str, cur.variables)):
        errors.append(
            f"NetCDF variable names differ between {ref_file.name} and {cur_file.name}."
        )

    errors.extend(_compare_source_identity(ref, cur, ref_file, cur_file))
    return errors



def _compare_multivariable_structure(
    ref: xr.Dataset,
    cur: xr.Dataset,
    *,
    ref_file: Path,
    cur_file: Path,
    ref_roles: Dict[str, Optional[str]],
    cur_roles: Dict[str, Optional[str]],
) -> List[str]:
    """
    Validate two datasets for Multivariable merge.

    Rules
    -----
    * Horizontal grid type/names/values must be identical.
    * Time must exist in both datasets and be identical (name, dtype, attrs, values).
    * Level is optional. If present in both datasets it must be identical. A surface
      file without level may be merged with a pressure-level file.
    * Shared dimensions/coordinates must be identical. Dimensions/coordinates used
      only by one distinct variable are allowed.
    * Environmental data-variable names must be distinct across files. A duplicated
      scalar grid-mapping/CRS variable is allowed only when it is identical.
    * Source-identifying global metadata must not conflict.
    """
    errors: List[str] = []

    if ref_roles.get("grid_type") != cur_roles.get("grid_type"):
        errors.append(
            f"Grid type differs: {ref_file.name}={ref_roles.get('grid_type')!r}, "
            f"{cur_file.name}={cur_roles.get('grid_type')!r}."
        )
        return errors

    # Horizontal coordinate roles are mandatory and must use the same names.
    horizontal_roles = ("lat_name", "lon_name")
    if ref_roles.get("grid_type") == "Projected rectilinear":
        horizontal_roles += ("x_name", "y_name", "grid_mapping_name")

    for role in horizontal_roles:
        if ref_roles.get(role) != cur_roles.get(role):
            errors.append(
                f"Horizontal grid role {role} differs: "
                f"{ref_file.name}={ref_roles.get(role)!r}, {cur_file.name}={cur_roles.get(role)!r}."
            )

    # Multivariable merge uses one common time axis; it never unions time values.
    ref_time = ref_roles.get("time_name")
    cur_time = cur_roles.get("time_name")
    if not ref_time or not cur_time:
        errors.append("Multivariable requires a detectable time coordinate in every selected file.")
    elif ref_time != cur_time:
        errors.append(
            f"Time coordinate name differs: {ref_file.name}={ref_time!r}, {cur_file.name}={cur_time!r}."
        )

    # A level coordinate may be absent in a surface file. If both have one, its
    # source name and full representation must match exactly.
    ref_level = ref_roles.get("level_name")
    cur_level = cur_roles.get("level_name")
    if ref_level and cur_level and ref_level != cur_level:
        errors.append(
            f"Level coordinate name differs: {ref_file.name}={ref_level!r}, {cur_file.name}={cur_level!r}."
        )

    # Any shared dimension must have the same length. Unique dimensions are valid
    # for variables that only occur in one file (e.g. level in a pressure-level file).
    for dim in sorted(set(ref.dims) & set(cur.dims)):
        if int(ref.sizes[dim]) != int(cur.sizes[dim]):
            errors.append(
                f"Shared dimension {dim!r} has different size: "
                f"{ref_file.name}={int(ref.sizes[dim])}, {cur_file.name}={int(cur.sizes[dim])}."
            )

    # Shared coordinates are the common structural contract and must be identical.
    shared_coords = set(map(str, ref.coords)) & set(map(str, cur.coords))
    for name in sorted(shared_coords):
        a, b = ref[name], cur[name]
        if tuple(a.dims) != tuple(b.dims):
            errors.append(
                f"Shared coordinate {name!r} uses different dimensions: "
                f"{ref_file.name}={tuple(a.dims)}, {cur_file.name}={tuple(b.dims)}."
            )
            continue
        if str(a.dtype) != str(b.dtype):
            errors.append(
                f"Shared coordinate {name!r} dtype differs: "
                f"{ref_file.name}={a.dtype}, {cur_file.name}={b.dtype}."
            )
        if _normalise_attrs(a.attrs) != _normalise_attrs(b.attrs):
            errors.append(
                f"Shared coordinate {name!r} metadata differs between {ref_file.name} and {cur_file.name}."
            )
        if not _array_equal_values(a, b):
            errors.append(
                f"Shared coordinate {name!r} values differ between {ref_file.name} and {cur_file.name}. "
                "Multivariable does not align, sort, interpolate, or union coordinate values."
            )

    # Explicitly require the detected common axes/grid coordinates to be shared.
    required_shared = [
        ref_roles.get("time_name"),
        ref_roles.get("lat_name"),
        ref_roles.get("lon_name"),
    ]
    if ref_roles.get("grid_type") == "Projected rectilinear":
        required_shared += [ref_roles.get("x_name"), ref_roles.get("y_name")]
    if ref_level and cur_level:
        required_shared.append(ref_level)

    for name in [n for n in required_shared if n]:
        if name not in ref.variables or name not in cur.variables:
            errors.append(
                f"Required shared coordinate {name!r} is not present in both "
                f"{ref_file.name} and {cur_file.name}."
            )

    # Data variables may differ by design. A duplicated *physical* environmental
    # field is ambiguous and is rejected. Shared technical/auxiliary variables
    # (for example ERA5 expver/number or a CRS variable) are allowed only when
    # they are identical in both files.
    shared_data_vars = set(map(str, ref.data_vars)) & set(map(str, cur.data_vars))
    for name in sorted(shared_data_vars):
        a, b = ref[name], cur[name]
        physical_in_ref = _is_physical_data_variable(ref, name, ref_roles)
        physical_in_cur = _is_physical_data_variable(cur, name, cur_roles)

        if physical_in_ref or physical_in_cur:
            errors.append(
                f"Physical data variable {name!r} occurs in both {ref_file.name} and {cur_file.name}. "
                "Multivariable expects distinct environmental variables; split/choose files so no physical variable is duplicated."
            )
            continue

        if (
            tuple(a.dims) != tuple(b.dims)
            or str(a.dtype) != str(b.dtype)
            or _normalise_attrs(a.attrs) != _normalise_attrs(b.attrs)
            or not _array_equal_values(a, b)
        ):
            errors.append(
                f"Shared auxiliary variable {name!r} differs between {ref_file.name} and {cur_file.name}."
            )

    errors.extend(_compare_source_identity(ref, cur, ref_file, cur_file))
    return errors


def _merge_multivariable(datasets: Sequence[xr.Dataset]) -> xr.Dataset:
    """Merge distinct variables without changing coordinate values or ordering."""
    if not datasets:
        raise ValueError("No datasets supplied for Multivariable merge.")
    if len(datasets) == 1:
        return datasets[0]
    return xr.merge(
        list(datasets),
        compat="identical",
        join="exact",
        combine_attrs="override",
    )



def _validate_wgs84_bbox(bbox: Dict[str, Any]) -> Dict[str, float]:
    required = ("west", "east", "south", "north")
    missing = [key for key in required if key not in bbox or bbox.get(key) is None]
    if missing:
        raise ValueError("BBOX is missing value(s): " + ", ".join(missing))

    out: Dict[str, float] = {}
    for key in required:
        try:
            value = float(bbox[key])
        except Exception as exc:
            raise ValueError(f"BBOX {key!r} must be numeric.") from exc
        if not np.isfinite(value):
            raise ValueError(f"BBOX {key!r} must be finite.")
        out[key] = value

    if not (-90.0 <= out["south"] < out["north"] <= 90.0):
        raise ValueError("BBOX latitude must satisfy -90 <= south < north <= 90.")
    if not (-180.0 <= out["west"] <= 180.0 and -180.0 <= out["east"] <= 180.0):
        raise ValueError("BBOX west/east must be WGS84 longitudes in the range [-180, 180].")
    return out


def _vector_extent_wgs84(path: str | Path) -> Tuple[Dict[str, float], List[str]]:
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Boundary file does not exist: {p}")
    if p.suffix.lower() not in VECTOR_EXTENSIONS:
        raise ValueError("Boundary must be a .shp, .geojson or .json file.")

    try:
        import geopandas as gpd
    except Exception as exc:
        raise RuntimeError(
            "GeoJSON/SHP spatial subsetting requires geopandas, which is not available in the current environment."
        ) from exc

    gdf = gpd.read_file(p)
    if gdf.empty:
        raise ValueError(f"Boundary file contains no features: {p.name}")

    warnings: List[str] = []
    if gdf.crs is None:
        if p.suffix.lower() in {".geojson", ".json"}:
            # RFC 7946 GeoJSON coordinates are WGS84 longitude/latitude.
            gdf = gdf.set_crs("EPSG:4326", allow_override=True)
            warnings.append(
                f"{p.name}: vector CRS was not declared; GeoJSON was interpreted as WGS84 (EPSG:4326)."
            )
        else:
            raise ValueError(
                f"Boundary shapefile {p.name!r} has no CRS. Add a valid .prj/CRS or use BBOX."
            )

    try:
        gdf_wgs84 = gdf.to_crs("EPSG:4326")
    except Exception as exc:
        raise ValueError(f"Could not transform boundary {p.name!r} to WGS84: {exc}") from exc

    minx, miny, maxx, maxy = map(float, gdf_wgs84.total_bounds)
    bbox = _validate_wgs84_bbox(
        {"west": minx, "east": maxx, "south": miny, "north": maxy}
    )
    return bbox, warnings


def _resolve_subset_bbox(config: NCBuildConfig) -> Tuple[Optional[Dict[str, float]], List[str]]:
    mode = str(config.subset_mode or "None")
    if mode == "None":
        return None, []
    if mode == "BBOX":
        if not config.bbox:
            raise ValueError("Spatial subset mode is BBOX, but no BBOX values were provided.")
        return _validate_wgs84_bbox(config.bbox), []
    if mode == "GeoJSON / SHP extent":
        if not config.boundary_path:
            raise ValueError("Spatial subset mode is GeoJSON / SHP extent, but no boundary file was selected.")
        return _vector_extent_wgs84(config.boundary_path)
    raise ValueError(f"Unsupported spatial subset mode: {mode!r}")


def _longitude_mask_wgs84(values: np.ndarray, west: float, east: float) -> np.ndarray:
    """Evaluate a WGS84 longitude interval without modifying the dataset longitude coordinate."""
    lon = np.asarray(values, dtype=float)
    lon_wgs84 = ((lon + 180.0) % 360.0) - 180.0
    finite = np.isfinite(lon_wgs84)
    if west <= east:
        return finite & (lon_wgs84 >= west) & (lon_wgs84 <= east)
    # west > east explicitly means a bbox crossing the antimeridian.
    return finite & ((lon_wgs84 >= west) | (lon_wgs84 <= east))


def _indices_contiguous(indices: np.ndarray) -> bool:
    idx = np.asarray(indices, dtype=int)
    return idx.size <= 1 or bool(np.all(np.diff(idx) == 1))


def _crop_spatial_dataset(
    ds: xr.Dataset,
    roles: Dict[str, Optional[str]],
    bbox: Dict[str, float],
) -> Tuple[xr.Dataset, Dict[str, Any]]:
    """
    Crop only by grid indices; never rename/reproject/reorder coordinates.

    Regular 1-D geographic grids are subset directly by their 1-D lat/lon
    coordinate indices. Curvilinear and projected rectilinear grids are subset
    by the minimal y/x index rectangle whose auxiliary 2-D lat/lon cells
    intersect the requested WGS84 bbox. This intentionally preserves the native
    projected x/y coordinates and CRS.
    """
    bbox = _validate_wgs84_bbox(bbox)
    lat_name = roles.get("lat_name")
    lon_name = roles.get("lon_name")
    if not lat_name or not lon_name or lat_name not in ds.variables or lon_name not in ds.variables:
        raise ValueError(
            "Spatial subsetting requires detectable latitude/longitude coordinates. "
            "NCBuilder will not infer geographic location from unknown/non-georeferenced x/y coordinates."
        )

    lat_da = ds[lat_name]
    lon_da = ds[lon_name]
    south, north = bbox["south"], bbox["north"]
    west, east = bbox["west"], bbox["east"]

    if lat_da.ndim == 1 and lon_da.ndim == 1:
        lat_values = np.asarray(lat_da.values, dtype=float)
        lon_values = np.asarray(lon_da.values, dtype=float)
        lat_idx = np.where(np.isfinite(lat_values) & (lat_values >= south) & (lat_values <= north))[0]
        lon_idx = np.where(_longitude_mask_wgs84(lon_values, west, east))[0]
        if lat_idx.size == 0 or lon_idx.size == 0:
            raise ValueError("Requested spatial subset does not intersect the NetCDF grid.")
        if not _indices_contiguous(lat_idx):
            raise ValueError(
                "Latitude subset is not contiguous in native coordinate order. "
                "NCBuilder does not reorder coordinates."
            )
        if not _indices_contiguous(lon_idx):
            raise ValueError(
                "Requested longitude BBOX crosses the native longitude seam, so a compact crop would require "
                "coordinate reordering. NCBuilder does not reorder coordinates."
            )
        lat_dim = str(lat_da.dims[0])
        lon_dim = str(lon_da.dims[0])
        indexers = {
            lat_dim: slice(int(lat_idx[0]), int(lat_idx[-1]) + 1),
            lon_dim: slice(int(lon_idx[0]), int(lon_idx[-1]) + 1),
        }
        cropped = ds.isel(indexers)
        method = "1D geographic coordinate index crop"
        index_bounds = {
            lat_dim: [int(lat_idx[0]), int(lat_idx[-1])],
            lon_dim: [int(lon_idx[0]), int(lon_idx[-1])],
        }

    elif lat_da.ndim == 2 and lon_da.ndim == 2 and lat_da.shape == lon_da.shape and lat_da.dims == lon_da.dims:
        lat_values = np.asarray(lat_da.values, dtype=float)
        lon_values = np.asarray(lon_da.values, dtype=float)
        mask = (
            np.isfinite(lat_values)
            & (lat_values >= south)
            & (lat_values <= north)
            & _longitude_mask_wgs84(lon_values, west, east)
        )
        rows, cols = np.where(mask)
        if rows.size == 0 or cols.size == 0:
            raise ValueError("Requested spatial subset does not intersect the NetCDF grid.")
        y_dim, x_dim = map(str, lat_da.dims)
        y0, y1 = int(rows.min()), int(rows.max())
        x0, x1 = int(cols.min()), int(cols.max())
        cropped = ds.isel({y_dim: slice(y0, y1 + 1), x_dim: slice(x0, x1 + 1)})
        method = "2D auxiliary lat/lon index-envelope crop"
        index_bounds = {y_dim: [y0, y1], x_dim: [x0, x1]}

    else:
        raise ValueError(
            "Spatial subsetting currently supports 1-D geographic lat/lon or matching 2-D auxiliary lat/lon grids."
        )

    return cropped, {
        "bbox_wgs84": dict(bbox),
        "method": method,
        "index_bounds": index_bounds,
        "input_dims": {str(k): int(v) for k, v in ds.sizes.items()},
        "output_dims": {str(k): int(v) for k, v in cropped.sizes.items()},
    }


def _validate_spatial_subset_config(config: NCBuildConfig) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if config.subset_mode not in SPATIAL_SUBSET_MODES:
        errors.append(f"Unsupported spatial subset mode: {config.subset_mode!r}")
    elif config.subset_mode == "BBOX":
        try:
            _validate_wgs84_bbox(config.bbox or {})
        except Exception as exc:
            errors.append(str(exc))
    elif config.subset_mode == "GeoJSON / SHP extent":
        if not config.boundary_path:
            errors.append("Select a GeoJSON/SHP boundary file for spatial subsetting.")
        else:
            boundary = Path(config.boundary_path).expanduser()
            if not boundary.exists() or not boundary.is_file():
                errors.append(f"Boundary file does not exist: {boundary}")
            elif boundary.suffix.lower() not in VECTOR_EXTENSIONS:
                errors.append("Boundary must be a .shp, .geojson or .json file.")

    return errors, warnings


def validate_build_config(config: NCBuildConfig) -> Tuple[bool, List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    files = [Path(p).expanduser() for p in list(config.files or [])]
    if len(files) < 2:
        errors.append("Select at least two NetCDF files to combine.")

    for path in files:
        if not path.exists() or not path.is_file():
            errors.append(f"Input file does not exist: {path}")
        elif path.suffix.lower() not in NETCDF_EXTENSIONS:
            errors.append(f"Unsupported input extension: {path.name}")

    if config.combine_mode not in COMBINE_MODES:
        errors.append(f"Unsupported combine mode: {config.combine_mode!r}")

    selected = _selected_physical_variables(config)
    if selected == []:
        errors.append("Select at least one physical data variable after scanning the input files.")

    output = Path(config.output_path).expanduser()
    if output.suffix.lower() not in NETCDF_EXTENSIONS:
        errors.append("Output filename must use a NetCDF extension (.nc, .nc4, .cdf, .netcdf).")

    try:
        out_resolved = output.resolve()
        for path in files:
            if path.exists() and path.resolve() == out_resolved:
                errors.append("Output path must not overwrite one of the selected input files.")
                break
    except Exception:
        pass

    subset_errors, subset_warnings = _validate_spatial_subset_config(config)
    errors.extend(subset_errors)
    warnings.extend(subset_warnings)

    return len(errors) == 0, errors, warnings


def inspect_compatibility(config: NCBuildConfig) -> Dict[str, Any]:
    # File compatibility and optional spatial-subset validation are intentionally
    # separated. A malformed/unfinished BBOX must not prevent the Builder from
    # checking whether the selected NetCDF files themselves are compatible.
    base_config = replace(
        config,
        subset_mode="None",
        bbox=None,
        boundary_path=None,
    )
    core_ok, config_errors, warnings = validate_build_config(base_config)
    subset_errors, subset_warnings = _validate_spatial_subset_config(config)

    report: Dict[str, Any] = {
        "ok": False,
        "file_compatible": False,
        "subset_ok": len(subset_errors) == 0,
        "subset_errors": list(subset_errors),
        "variable_selection_ok": False,
        "variable_errors": [],
        "errors": list(config_errors),
        "warnings": list(warnings) + list(subset_warnings),
        "files": list(config.files or []),
        "combine_mode": config.combine_mode,
        "subset_mode": config.subset_mode,
    }
    if not core_ok:
        report["errors"].extend(f"Spatial subset: {e}" for e in subset_errors)
        return report

    paths = [Path(p).expanduser() for p in config.files]
    datasets: List[xr.Dataset] = []
    engines: List[str] = []

    try:
        for path in paths:
            ds, engine = _open_dataset_raw(path, config.open_engine)
            datasets.append(ds)
            engines.append(engine)

        selected_variables, variable_errors, variable_warnings = _validate_variable_selection(
            datasets, paths, config
        )
        report["variable_errors"] = list(variable_errors)
        report["variable_selection_ok"] = len(variable_errors) == 0
        report["errors"].extend(variable_errors)
        report["warnings"].extend(variable_warnings)
        report["selected_variables"] = list(selected_variables)

        datasets = [
            _filter_physical_data_variables(ds, selected_variables) for ds in datasets
        ]

        reference = datasets[0]
        ref_roles = _detect_grid(reference)
        time_name = ref_roles.get("time_name")
        level_name = ref_roles.get("level_name")

        if ref_roles.get("grid_type") == "Unknown":
            report["errors"].append(
                f"Could not identify a supported horizontal grid in {paths[0].name}. "
                "NCBuilder does not transform unknown grids."
            )

        if config.combine_mode == "By time":
            if not time_name:
                report["errors"].append("By time requires a detectable time coordinate.")
            allowed_axes = {time_name} if time_name else set()
        elif config.combine_mode == "By level":
            if not time_name:
                report["errors"].append("By level requires a detectable time coordinate for compatibility checking.")
            if not level_name:
                report["errors"].append("By level requires a detectable vertical/level coordinate.")
            allowed_axes = {level_name} if level_name else set()
        elif config.combine_mode == "By time and level":
            if not time_name:
                report["errors"].append("By time and level requires a detectable time coordinate.")
            if not level_name:
                report["errors"].append("By time and level requires a detectable vertical/level coordinate.")
            allowed_axes = {name for name in (time_name, level_name) if name}
        elif config.combine_mode == "Multivariable":
            if not time_name:
                report["errors"].append("Multivariable requires a detectable time coordinate.")
            allowed_axes = set()
        else:
            allowed_axes = set()

        for path, ds in zip(paths[1:], datasets[1:]):
            cur_roles = _detect_grid(ds)
            if config.combine_mode == "Multivariable":
                report["errors"].extend(
                    _compare_multivariable_structure(
                        reference,
                        ds,
                        ref_file=paths[0],
                        cur_file=path,
                        ref_roles=ref_roles,
                        cur_roles=cur_roles,
                    )
                )
            else:
                report["errors"].extend(
                    _compare_dataset_structure(
                        reference,
                        ds,
                        ref_file=paths[0],
                        cur_file=path,
                        ref_roles=ref_roles,
                        cur_roles=cur_roles,
                        allowed_axes=allowed_axes,
                    )
                )

        # Axis-specific rules.
        if not report["errors"]:
            if config.combine_mode == "By time" and time_name:
                seen = set()
                combined_values: List[Any] = []
                for path, ds in zip(paths, datasets):
                    vals = _axis_values(ds, time_name)
                    for value in vals:
                        key = _value_key(value)
                        if key in seen:
                            report["errors"].append(
                                f"Duplicate time value detected while combining {path.name}. "
                                "NCBuilder does not drop or overwrite duplicate times."
                            )
                            break
                        seen.add(key)
                        combined_values.append(value)
                if not report["errors"]:
                    increasing = _strictly_increasing(np.asarray(combined_values))
                    if increasing is False:
                        report["errors"].append(
                            "Selected file order would produce a non-increasing time coordinate. "
                            "NCBuilder does not sort time values; select chronologically ordered source files."
                        )
                    elif increasing is None:
                        report["warnings"].append(
                            "Time monotonicity could not be verified from the raw coordinate representation."
                        )

            elif config.combine_mode == "By level" and level_name and time_name:
                # Time values must be exactly identical across all files.
                ref_time = reference[time_name]
                for path, ds in zip(paths[1:], datasets[1:]):
                    if not _array_equal_values(ref_time, ds[time_name]):
                        report["errors"].append(
                            f"Time coordinate values differ in {path.name}. "
                            "By level allows only level values to differ."
                        )

                seen_levels = set()
                for path, ds in zip(paths, datasets):
                    for value in _axis_values(ds, level_name):
                        key = _value_key(value)
                        if key in seen_levels:
                            report["errors"].append(
                                f"Duplicate level value detected while adding {path.name}. "
                                "NCBuilder does not drop or overwrite duplicate levels."
                            )
                            break
                        seen_levels.add(key)

            elif config.combine_mode == "By time and level" and time_name and level_name:
                # Files may be split by either axis, but they must form a complete
                # time x level rectangle with no duplicate cells.
                all_times: List[Any] = []
                all_levels: List[Any] = []
                time_seen_order = set()
                level_seen_order = set()
                occupied = set()

                for path, ds in zip(paths, datasets):
                    tvals = list(_axis_values(ds, time_name))
                    lvals = list(_axis_values(ds, level_name))
                    for t in tvals:
                        tk = _value_key(t)
                        if tk not in time_seen_order:
                            time_seen_order.add(tk)
                            all_times.append(t)
                    for lev in lvals:
                        lk = _value_key(lev)
                        if lk not in level_seen_order:
                            level_seen_order.add(lk)
                            all_levels.append(lev)
                    for t in tvals:
                        for lev in lvals:
                            cell = (_value_key(t), _value_key(lev))
                            if cell in occupied:
                                report["errors"].append(
                                    f"Duplicate time/level cell detected in {path.name}. "
                                    "NCBuilder does not overwrite duplicate data cells."
                                )
                                break
                            occupied.add(cell)
                        if report["errors"]:
                            break
                    if report["errors"]:
                        break

                if not report["errors"]:
                    expected = len(all_times) * len(all_levels)
                    if len(occupied) != expected:
                        report["errors"].append(
                            "Selected files do not form a complete time × level grid. "
                            f"Found {len(occupied)} time/level combinations, expected {expected}."
                        )
                    increasing = _strictly_increasing(np.asarray(all_times))
                    if increasing is False:
                        report["errors"].append(
                            "Selected file order would produce a non-increasing time coordinate. "
                            "NCBuilder does not sort time values."
                        )
                    elif increasing is None:
                        report["warnings"].append(
                            "Time monotonicity could not be verified from the raw coordinate representation."
                        )

            elif config.combine_mode == "Multivariable":
                # The pairwise structural checks above already require shared
                # coordinates to be identical. Confirm that the merge contributes
                # at least two distinct physical data variables overall.
                physical_vars = []
                seen_vars = set()
                for ds in datasets:
                    roles = _detect_grid(ds)
                    for name in _physical_data_variables(ds, roles):
                        if name not in seen_vars:
                            seen_vars.add(name)
                            physical_vars.append(name)
                if len(physical_vars) < 2:
                    report["warnings"].append(
                        "Multivariable is selected, but fewer than two distinct physical data variables were found."
                    )

        # At this point report["errors"] contains only core/file-compatibility
        # errors. Preserve that status before validating the optional crop.
        file_compatible = len(report["errors"]) == 0

        subset_bbox = None
        subset_info = None
        if file_compatible and not subset_errors and config.subset_mode != "None":
            try:
                subset_bbox, subset_warnings = _resolve_subset_bbox(config)
                report["warnings"].extend(subset_warnings)
                if subset_bbox is not None:
                    preview_subset, subset_info = _crop_spatial_dataset(reference, ref_roles, subset_bbox)
                    # Only sizes/metadata are needed; preview_subset is a lazy xarray view.
                    subset_info = dict(subset_info)
                    subset_info["preview_dims"] = {str(k): int(v) for k, v in preview_subset.sizes.items()}
            except Exception as exc:
                subset_errors.append(str(exc))

        report["subset_errors"] = list(subset_errors)
        report["subset_ok"] = len(subset_errors) == 0
        report["file_compatible"] = file_compatible
        report["errors"].extend(f"Spatial subset: {e}" for e in subset_errors)

        source_identity = _source_identity(reference)
        identity_dicts = [_source_identity(ds) for ds in datasets]
        common_identity_keys = set(identity_dicts[0]) if identity_dicts else set()
        for identity in identity_dicts[1:]:
            common_identity_keys &= set(identity)
        if not common_identity_keys:
            report["warnings"].append(
                "The files do not expose a common source-identifying global attribute. "
                "Compatibility was verified from NetCDF structure/metadata, but exact product identity cannot be proven automatically."
            )

        report.update(
            {
                "ok": len(report["errors"]) == 0,
                "grid_type": ref_roles.get("grid_type"),
                "time_name": time_name,
                "level_name": level_name,
                "lat_name": ref_roles.get("lat_name"),
                "lon_name": ref_roles.get("lon_name"),
                "x_name": ref_roles.get("x_name"),
                "y_name": ref_roles.get("y_name"),
                "grid_mapping_name": ref_roles.get("grid_mapping_name"),
                "data_variables": (
                    list(dict.fromkeys(name for ds in datasets for name in map(str, ds.data_vars)))
                    if config.combine_mode == "Multivariable"
                    else list(map(str, reference.data_vars))
                ),
                "physical_variables": list(selected_variables),
                "coordinate_names": (
                    list(dict.fromkeys(name for ds in datasets for name in map(str, ds.coords)))
                    if config.combine_mode == "Multivariable"
                    else list(map(str, reference.coords))
                ),
                "dimensions": (
                    {str(k): int(v) for ds in datasets for k, v in ds.sizes.items()}
                    if config.combine_mode == "Multivariable"
                    else {str(k): int(v) for k, v in reference.sizes.items()}
                ),
                "source_identity": source_identity,
                "engines": engines,
                "subset_mode": config.subset_mode,
                "subset_bbox_wgs84": subset_bbox,
                "subset_info": subset_info,
            }
        )
        return report

    finally:
        for ds in datasets:
            try:
                ds.close()
            except Exception:
                pass


def scan_netcdf_files(
    files: Sequence[str | Path],
    max_scan: Optional[int] = None,
    **_ignored: Any,
) -> Dict[str, Any]:
    """Read lightweight structural metadata for the selected files without modifying them."""
    paths = [Path(p).expanduser() for p in files]
    if max_scan is not None:
        paths = paths[: int(max_scan)]
    if not paths:
        return {
            "scanned_count": 0,
            "variables": [],
            "all_names": [],
            "coords": [],
            "dims": [],
            "warnings": ["No files selected."],
        }

    summaries = []
    variables_union: List[str] = []
    physical_variables_union: List[str] = []
    auxiliary_variables_union: List[str] = []
    names_union: List[str] = []
    coords_union: List[str] = []
    dims_union: List[str] = []
    warnings: List[str] = []

    def add_unique(target: List[str], values: Iterable[str]) -> None:
        for value in values:
            value = str(value)
            if value not in target:
                target.append(value)

    for path in paths:
        ds = None
        try:
            ds, engine = _open_dataset_raw(path)
            roles = _detect_grid(ds)
            physical_variables = _physical_data_variables(ds, roles)
            auxiliary_variables = _auxiliary_data_variables(ds, roles)
            add_unique(variables_union, ds.data_vars)
            add_unique(physical_variables_union, physical_variables)
            add_unique(auxiliary_variables_union, auxiliary_variables)
            add_unique(names_union, ds.variables)
            add_unique(coords_union, ds.coords)
            add_unique(dims_union, ds.dims)
            summaries.append(
                {
                    "file": str(path),
                    "engine": engine,
                    "grid_type": roles.get("grid_type"),
                    "time_name": roles.get("time_name"),
                    "level_name": roles.get("level_name"),
                    "lat_name": roles.get("lat_name"),
                    "lon_name": roles.get("lon_name"),
                    "variables": list(map(str, ds.data_vars)),
                    "physical_variables": physical_variables,
                    "auxiliary_variables": auxiliary_variables,
                    "coords": list(map(str, ds.coords)),
                    "dims": {str(k): int(v) for k, v in ds.sizes.items()},
                    "source_identity": _source_identity(ds),
                }
            )
        except Exception as exc:
            warnings.append(f"{path.name}: {exc}")
        finally:
            if ds is not None:
                try:
                    ds.close()
                except Exception:
                    pass

    first = summaries[0] if summaries else {}
    return {
        "scanned_count": len(summaries),
        "variables": variables_union,
        "physical_variables": physical_variables_union,
        "auxiliary_variables": auxiliary_variables_union,
        "all_names": names_union,
        "coords": coords_union,
        "dims": dims_union,
        "suggested_time": first.get("time_name"),
        "suggested_level": first.get("level_name"),
        "grid_type": first.get("grid_type"),
        "source_identity": first.get("source_identity", {}),
        "summaries": summaries,
        "warnings": warnings,
    }




def _restore_static_scalar_coords(combined: xr.Dataset, reference: xr.Dataset, axis_name: str) -> xr.Dataset:
    """Restore non-concatenated scalar coordinates that xarray broadcast along a new axis."""
    out = combined
    for name in reference.coords:
        if name == axis_name:
            continue
        ref_coord = reference[name]
        if ref_coord.ndim != 0:
            continue
        if name in out and out[name].ndim > 0:
            out = out.drop_vars(name)
            out = out.assign_coords({name: ref_coord})
    return out

def _restore_static_scalar_vars(combined: xr.Dataset, reference: xr.Dataset, axis_name: str) -> xr.Dataset:
    """Undo accidental expansion of constant scalar metadata variables during scalar-axis concat."""
    out = combined
    for name in reference.data_vars:
        ref_var = reference[name]
        if ref_var.ndim != 0:
            continue
        if name not in out or axis_name not in out[name].dims:
            continue
        if "grid_mapping_name" in ref_var.attrs or name.lower() in {"crs", "spatial_ref"}:
            out[name] = ref_var
    return out


def _preserve_existing_dim_order_after_scalar_concat(
    combined: xr.Dataset,
    reference: xr.Dataset,
    axis_name: str,
    insert_after: Optional[str] = None,
) -> xr.Dataset:
    """
    When the combine coordinate was scalar in the source, xarray must create a
    new dimension. Keep every pre-existing dimension in its original relative
    order and only insert the new axis. No existing dimensions are reordered.
    """
    out = combined
    for name in reference.data_vars:
        if name not in out or axis_name not in out[name].dims or axis_name in reference[name].dims:
            continue
        original = list(reference[name].dims)
        if insert_after and insert_after in original:
            pos = original.index(insert_after) + 1
        else:
            pos = 0
        desired = original[:pos] + [axis_name] + original[pos:]
        if set(desired) == set(out[name].dims):
            out[name] = out[name].transpose(*desired)
    return out


def _concat_axis(
    datasets: Sequence[xr.Dataset],
    axis_name: str,
    *,
    insert_after: Optional[str] = None,
) -> xr.Dataset:
    if not datasets:
        raise ValueError("No datasets supplied for concatenation.")
    if len(datasets) == 1:
        return datasets[0]

    axis_is_dimension = all(axis_name in ds.dims for ds in datasets)
    data_vars: Any = "minimal" if axis_is_dimension else "all"

    combined = xr.concat(
        list(datasets),
        dim=axis_name,
        data_vars=data_vars,
        coords="minimal",
        compat="identical",
        join="exact",
        combine_attrs="override",
    )

    if not axis_is_dimension:
        combined = _restore_static_scalar_coords(combined, datasets[0], axis_name)
        combined = _restore_static_scalar_vars(combined, datasets[0], axis_name)
        combined = _preserve_existing_dim_order_after_scalar_concat(
            combined, datasets[0], axis_name, insert_after=insert_after
        )
    return combined


def _combine_time_and_level(
    datasets: Sequence[xr.Dataset],
    time_name: str,
    level_name: str,
) -> xr.Dataset:
    """
    Preserve input order without combine_by_coords sorting.

    Datasets are grouped by their complete level-coordinate values. Each group is
    concatenated by time in input-file order. The resulting level blocks must
    have identical time coordinates, after which the blocks are concatenated by
    level in first-occurrence order.
    """
    groups: Dict[Any, List[xr.Dataset]] = {}
    order: List[Any] = []

    for ds in datasets:
        key = _axis_array_key(_axis_values(ds, level_name))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(ds)

    time_combined_blocks: List[xr.Dataset] = []
    for key in order:
        block = _concat_axis(groups[key], time_name)
        time_combined_blocks.append(block)

    if len(time_combined_blocks) == 1:
        return time_combined_blocks[0]

    ref_time = time_combined_blocks[0][time_name]
    for block in time_combined_blocks[1:]:
        if not _array_equal_values(ref_time, block[time_name]):
            raise ValueError(
                "By time and level requires every level block to cover the same time coordinate. "
                "The selected files do not form a complete rectangular time × level dataset."
            )

    return _concat_axis(time_combined_blocks, level_name, insert_after=time_name)


def _write_dataset_atomic(ds: xr.Dataset, output_path: Path, preferred_engine: str) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".tmp")
    if temp_path.exists():
        temp_path.unlink()

    last_error: Optional[Exception] = None
    used_engine = "default"
    for engine in _available_engines(preferred_engine):
        label = engine or "default"
        kwargs: Dict[str, Any] = {}
        if engine is not None:
            kwargs["engine"] = engine
        try:
            ds.to_netcdf(temp_path, **kwargs)
            used_engine = label
            break
        except Exception as exc:
            last_error = exc
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
    else:
        raise OSError(f"Could not write output NetCDF. Last error: {last_error}")

    os.replace(temp_path, output_path)
    return used_engine


def _validate_written_output(
    output_path: Path,
    reference: xr.Dataset,
    combined: xr.Dataset,
    preferred_engine: str,
) -> None:
    check, _engine = _open_dataset_raw(output_path, preferred_engine)
    try:
        if set(map(str, check.data_vars)) != set(map(str, combined.data_vars)):
            raise ValueError("Output data-variable names changed unexpectedly during writing.")
        if set(map(str, check.coords)) != set(map(str, combined.coords)):
            raise ValueError("Output coordinate names changed unexpectedly during writing.")
        for name in combined.variables:
            if name not in check.variables:
                raise ValueError(f"Output is missing variable {name!r}.")
            if str(check[name].dtype) != str(combined[name].dtype):
                raise ValueError(
                    f"Output dtype changed for {name!r}: expected {combined[name].dtype}, got {check[name].dtype}."
                )
            if _normalise_attrs(check[name].attrs) != _normalise_attrs(combined[name].attrs):
                raise ValueError(f"Output metadata changed for variable/coordinate {name!r}.")

        # The output inherits global attrs from the first source file.
        for key, value in reference.attrs.items():
            if key in check.attrs and _json_safe(check.attrs[key]) != _json_safe(value):
                raise ValueError(f"Output global attribute {key!r} changed unexpectedly.")
    finally:
        check.close()


def combine_netcdf_files(config: NCBuildConfig) -> Dict[str, Any]:
    report = inspect_compatibility(config)
    if not report.get("ok"):
        details = "\n".join(f"- {item}" for item in report.get("errors", []))
        raise ValueError("Selected NetCDF files are not compatible:\n" + details)

    paths = [Path(p).expanduser() for p in config.files]
    datasets: List[xr.Dataset] = []
    engines: List[str] = []

    try:
        for path in paths:
            ds, engine = _open_dataset_raw(path, config.open_engine)
            datasets.append(ds)
            engines.append(engine)

        selected_variables = report.get("physical_variables")
        if selected_variables is None:
            selected_variables = _selected_physical_variables(config)
        datasets = [
            _filter_physical_data_variables(ds, selected_variables) for ds in datasets
        ]

        reference = datasets[0]
        roles = _detect_grid(reference)
        time_name = roles.get("time_name")
        level_name = roles.get("level_name")

        if config.combine_mode == "By time":
            assert time_name is not None
            combined = _concat_axis(datasets, time_name)
        elif config.combine_mode == "By level":
            assert level_name is not None
            combined = _concat_axis(datasets, level_name, insert_after=time_name)
        elif config.combine_mode == "By time and level":
            assert time_name is not None and level_name is not None
            combined = _combine_time_and_level(datasets, time_name, level_name)
        elif config.combine_mode == "Multivariable":
            combined = _merge_multivariable(datasets)
        else:
            raise ValueError(f"Unsupported combine mode: {config.combine_mode!r}")

        subset_info = None
        subset_bbox = report.get("subset_bbox_wgs84")
        if config.subset_mode != "None":
            if subset_bbox is None:
                raise ValueError("Spatial subset was requested but no validated WGS84 extent is available.")
            combined_roles = _detect_grid(combined)
            combined, subset_info = _crop_spatial_dataset(combined, combined_roles, subset_bbox)

        output_path = Path(config.output_path).expanduser()
        writer_engine = _write_dataset_atomic(combined, output_path, config.open_engine)
        _validate_written_output(output_path, reference, combined, config.open_engine)

        manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
        manifest = {
            "output_path": str(output_path),
            "manifest_path": str(manifest_path),
            "combine_mode": config.combine_mode,
            "processed_files": [str(p) for p in paths],
            "read_engines": engines,
            "writer_engine": writer_engine,
            "grid_type": roles.get("grid_type"),
            "time_name": time_name,
            "level_name": level_name,
            "source_identity": report.get("source_identity", {}),
            "selected_physical_variables": list(report.get("physical_variables", [])),
            "spatial_subset_mode": config.subset_mode,
            "spatial_subset": subset_info,
            "output_dims": {str(k): int(v) for k, v in combined.sizes.items()},
            "output_variables": list(map(str, combined.data_vars)),
            "output_coords": list(map(str, combined.coords)),
            "warnings": report.get("warnings", []),
            "config": _json_safe(asdict(config)),
            "preservation_policy": (
                "No renaming, unit conversion, calendar conversion, longitude conversion, "
                "transposition, regridding, coordinate sorting, or variable renaming. "
                "Optional spatial subsetting only removes grid indices after combine/merge."
            ),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return manifest

    finally:
        for ds in datasets:
            try:
                ds.close()
            except Exception:
                pass


# Backward-compatible alias used by the older UI/import path.
def build_standardized_netcdf(config: NCBuildConfig) -> Dict[str, Any]:
    return combine_netcdf_files(config)


__all__ = [
    "NCBuildConfig",
    "SPATIAL_SUBSET_MODES",
    "scan_netcdf_files",
    "validate_build_config",
    "inspect_compatibility",
    "combine_netcdf_files",
    "build_standardized_netcdf",
]
