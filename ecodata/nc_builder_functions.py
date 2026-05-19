"""
Backend functions for NCBuilder_App.

This module is intentionally UI-free:
- no Panel imports
- no ECODATA template imports
- no register_view imports

It can be imported safely from ecodata.__init__ or from nc_builder_app.py.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import json
import re

import numpy as np
import pandas as pd
import xarray as xr


NETCDF_EXTENSIONS = (".nc", ".nc4", ".cdf", ".netcdf")


@dataclass
class NCBuildConfig:
    files: List[str]
    combine_mode: str
    target_variable: str
    output_variable_name: str
    lat_variable: str
    lon_variable: str
    # Optional multi-variable mode
    target_variables: Optional[List[str]] = None
    time_source: str = "From NetCDF time coordinate"
    time_variable: Optional[str] = None
    time_regex: str = r"(\d{8})"
    time_format: str = "%Y%m%d"
    time_table_path: Optional[str] = None
    level_source: str = "From NetCDF coordinate"
    level_variable: Optional[str] = None
    level_regex: str = r"level(\d+)"
    level_table_path: Optional[str] = None
    output_level_coord_name: str = "level"
    level_units: str = "hPa"

    bbox: Optional[Dict[str, float]] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    output_path: str = "standardized_output.nc"
    use_dask_chunks: bool = True
    chunking_mode: str = "auto"
    manual_chunks: Optional[Dict[str, int]] = None
    enable_compression: bool = True
    convert_longitude_to_180: bool = True
    # "auto" means: xarray default -> h5netcdf -> netcdf4 -> scipy.
    open_engine: str = "auto"
    #! for ECODATA/MATLAB compatibility
    use_modis_time_encoding: bool = True


def list_netcdf_files(folder: str | Path) -> List[Path]:
    folder = Path(folder).expanduser()
    if not folder.exists() or not folder.is_dir():
        return []

    allowed = {ext.lower() for ext in NETCDF_EXTENSIONS}

    files = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in allowed
    ]

    return sorted(files, key=lambda p: p.name.lower())


def _guess_name(candidates: Sequence[str], preferred: Sequence[str]) -> Optional[str]:
    if not candidates:
        return None
    lower_map = {str(c).lower(): str(c) for c in candidates}
    for p in preferred:
        if p.lower() in lower_map:
            return lower_map[p.lower()]
    for c in candidates:
        cl = str(c).lower()
        if any(p.lower() in cl for p in preferred):
            return str(c)
    return None


def _safe_open_for_scan(
    path: str | Path,
    preferred_engine: Optional[str] = None,
) -> Tuple[xr.Dataset, str]:
    """
    Open dataset for metadata scanning.

    For scanning, avoid chunks="auto" because it can fail when dask is not installed
    and is unnecessary for reading names, dimensions, and coordinate ranges.
    """
    return _open_dataset_auto(
        path,
        {"decode_times": True},
        preferred_engine=preferred_engine,
    )


def scan_netcdf_files(
    files: Sequence[str | Path],
    max_scan: int = 10,
    use_dask_chunks: bool = False,
    chunking_mode: str = "auto",
    manual_chunks: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    existing = [Path(f).expanduser() for f in files if Path(f).expanduser().exists()]
    warnings: List[str] = []
    engine_by_file: Dict[str, str] = {}

    if not existing:
        return {
            "files": [],
            "variables": [],
            "coords": [],
            "dims": [],
            "all_names": [],
            "suggested_time": None,
            "suggested_lat": None,
            "suggested_lon": None,
            "suggested_level": None,
            "time_min": None,
            "time_max": None,
            "scanned_count": 0,
            "engine_by_file": {},
            "warnings": ["No existing NetCDF files were found."],
        }

    variables = set()
    coords = set()
    dims = set()
    time_min = None
    time_max = None

    scanned_count = 0
    for path in existing[:max_scan]:
        try:
            ds, engine_used = _safe_open_for_scan(path)
            engine_by_file[path.name] = engine_used
            with ds:
                scanned_count += 1
                variables.update(map(str, ds.data_vars))
                coords.update(map(str, ds.coords))
                dims.update(map(str, ds.dims))

                all_vars = list(map(str, ds.variables))
                tname = _guess_name(
                    all_vars,
                    ["time", "valid_time", "forecast_time", "verification_time", "datetime", "date", "t", "Time"],
                )
                if tname and tname in ds.variables:
                    cur_min, cur_max, calendar_info = _safe_time_range(ds[tname].values)
                    if cur_min is not None and cur_max is not None:
                        if time_min is None or str(cur_min) < str(time_min):
                            time_min = cur_min
                        if time_max is None or str(cur_max) > str(time_max):
                            time_max = cur_max

                    if calendar_info:
                        warnings.append(
                            f"Time in {path.name} uses non-pandas calendar/time type "
                            f"`{calendar_info}`; preview time range is shown as string."
                        )
        except Exception as exc:
            warnings.append(f"Could not scan {path.name}: {exc}")

    all_names = sorted(variables | coords | dims)

    suggested_time = _guess_name(
        all_names,
        ["time", "valid_time", "forecast_time", "verification_time", "datetime", "date", "t", "Time"],
    )
    suggested_lat = _guess_name(all_names, ["lat", "latitude", "Latitude", "y"])
    suggested_lon = _guess_name(all_names, ["lon", "longitude", "Longitude", "long", "x"])
    suggested_level = _guess_name(
        all_names,
        ["level", "pressure_level", "isobaricInhPa", "isobaric_in_hPa", "plev", "lev", "height", "altitude"],
    )

    return {
        "files": [str(f) for f in existing],
        "variables": sorted(variables),
        "coords": sorted(coords),
        "dims": sorted(dims),
        "all_names": all_names,
        "suggested_time": suggested_time,
        "suggested_lat": suggested_lat,
        "suggested_lon": suggested_lon,
        "suggested_level": suggested_level,
        "time_min": str(time_min) if time_min is not None else None,
        "time_max": str(time_max) if time_max is not None else None,
        "scanned_count": scanned_count,
        "warnings": warnings,
        "engine_by_file": engine_by_file
    }

def _open_dataset_auto(
    path: str | Path,
    open_kwargs: Optional[Dict[str, Any]] = None,
    preferred_engine: Optional[str] = None,
) -> Tuple[xr.Dataset, str]:
    """
    Open a NetCDF file with automatic engine fallback.

    Engine strategy:
    - preferred_engine, if explicitly provided and not "auto"/"default";
    - xarray default engine;
    - h5netcdf;
    - netcdf4;
    - scipy.

    Returns
    -------
    ds : xr.Dataset
        Opened dataset.
    engine_used : str
        Engine name used for opening. "default" means xarray default engine.
    """
    path = Path(path).expanduser()
    open_kwargs = dict(open_kwargs or {})

    preferred_engine = preferred_engine or "auto"

    engines: List[Optional[str]] = []

    if preferred_engine not in ("auto", "default", None):
        engines.append(str(preferred_engine))

    engines.extend([None, "h5netcdf", "netcdf4", "scipy"])

    tried: List[str] = []
    last_exc: Optional[Exception] = None

    for engine in engines:
        engine_label = engine or "default"
        if engine_label in tried:
            continue
        tried.append(engine_label)

        kwargs = dict(open_kwargs)
        if engine is not None:
            kwargs["engine"] = engine
        else:
            kwargs.pop("engine", None)

        try:
            ds = xr.open_dataset(path, **kwargs)
            return ds, engine_label
        except Exception as exc:
            last_exc = exc

    raise OSError(
        f"Could not open NetCDF file {path.name!r}. "
        f"Tried engines: {tried}. Last error: {last_exc}"
    )

def validate_build_config(config: NCBuildConfig) -> Tuple[bool, List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    files = [Path(f).expanduser() for f in config.files]
    existing = [f for f in files if f.exists()]
    if not existing:
        errors.append("No existing NetCDF files were selected.")

    selected_vars = _selected_target_variables(config)
    if not selected_vars:
        errors.append("Target variable is not selected.")

    # output_variable_name is required only in single-variable mode.
    # In multi-variable mode original source variable names are preserved.
    if len(selected_vars) == 1 and not config.output_variable_name:
        errors.append("Output variable name is empty.")

    if not config.lat_variable:
        errors.append("Latitude variable is not selected.")

    if not config.lon_variable:
        errors.append("Longitude variable is not selected.")

    if config.time_source == "From NetCDF time coordinate" and not config.time_variable:
        errors.append("Time source is NetCDF coordinate, but no time variable is selected.")

    if config.time_source == "From filename":
        if not config.time_regex:
            errors.append("Time source is filename, but time regex is empty.")
        if not config.time_format:
            errors.append("Time source is filename, but time format is empty.")

    if config.time_source == "Manual table" and not config.time_table_path:
        errors.append("Time source is manual table, but no time table file is selected.")

    if config.combine_mode in ("By level", "By time and level"):
        if config.level_source == "From NetCDF coordinate" and not config.level_variable:
            errors.append("Combine mode requires level handling, but no level variable is selected.")
        if config.level_source == "From filename" and not config.level_regex:
            errors.append("Level source is filename, but level regex is empty.")
        if config.level_source == "Manual table" and not config.level_table_path:
            errors.append("Level source is manual table, but no level table file is selected.")

    if config.bbox is not None:
        try:
            south = float(config.bbox["south"])
            north = float(config.bbox["north"])
            west = float(config.bbox["west"])
            east = float(config.bbox["east"])
            if south >= north:
                errors.append("Bounding box is invalid: South must be smaller than North.")
            if west >= east:
                errors.append("Bounding box is invalid: West must be smaller than East.")
        except Exception:
            errors.append("Bounding box is enabled but contains invalid values.")

    output_path = Path(config.output_path).expanduser()
    if not output_path.name:
        errors.append("Output filename is empty.")
    if output_path.suffix.lower() not in (".nc", ".nc4"):
        warnings.append("Output file does not end with .nc or .nc4.")

    valid_engines = {"auto", "default", "h5netcdf", "netcdf4", "scipy"}
    if config.open_engine not in valid_engines:
        errors.append(
            f"Invalid open_engine={config.open_engine!r}. "
            f"Expected one of: {sorted(valid_engines)}."
        )

    if config.open_engine == "auto":
        warnings.append(
            "NetCDF open engine will be selected automatically: default -> h5netcdf -> netcdf4 -> scipy."
        )
    else:
        warnings.append(f"NetCDF open engine preference: {config.open_engine}.")

    if config.convert_longitude_to_180:
        warnings.append("Longitudes will be converted to the -180..180 convention when possible.")
    if config.use_modis_time_encoding:
        warnings.append("Time coordinate will use MATLAB/MODIS-compatible encoding when possible.")
    return len(errors) == 0, errors, warnings


def _load_lookup_table(path: Optional[str], value_col: str) -> Dict[str, Any]:
    if not path:
        return {}
    table_path = Path(path).expanduser()
    if not table_path.exists():
        raise FileNotFoundError(f"Manual table not found: {table_path}")

    df = pd.read_csv(table_path)
    if "name" not in df.columns or value_col not in df.columns:
        raise ValueError(f"Manual table must contain columns: name, {value_col}")

    return {str(row["name"]): row[value_col] for _, row in df.iterrows()}


def _lookup_by_filename(path: Path, lookup: Dict[str, Any]) -> Optional[Any]:
    if not lookup:
        return None
    name = path.name
    for key, value in lookup.items():
        if str(key) == name or str(key) in name:
            return value
    return None


def _parse_from_filename(path: Path, regex: str, cast=float, time_format: Optional[str] = None) -> Any:
    m = re.search(regex, path.name)
    if not m:
        raise ValueError(f"Pattern {regex!r} did not match file name {path.name!r}")
    token = m.group(1) if m.groups() else m.group(0)
    if time_format:
        return pd.to_datetime(token, format=time_format)
    return cast(token)


def _rename_if_needed(ds: xr.Dataset, old: Optional[str], new: str) -> xr.Dataset:
    if not old or old == "None":
        return ds
    if old == new:
        return ds
    if old in ds.variables or old in ds.dims or old in ds.coords:
        return ds.rename({old: new})
    return ds

def _normalize_time_coord_if_possible(ds: xr.Dataset) -> xr.Dataset:
    """
    Convert the time coordinate to pandas datetime only when this is safe.

    Standard calendars are usually convertible to pandas datetime64.
    Non-standard calendars, such as julian, noleap, or 360_day, may be decoded
    by xarray as cftime objects. pandas.to_datetime() cannot convert them
    reliably, so they are preserved unchanged.
    """
    if "time" not in ds.coords:
        return ds

    values = ds["time"].values

    try:
        converted = pd.to_datetime(values)
    except Exception:
        return ds

    return ds.assign_coords(time=converted)

def _safe_time_range(values):
    """
    Return time min/max for preview without failing on cftime calendars.

    pandas can handle standard datetime-like values, but not all cftime calendars
    such as Julian, noleap, or 360_day. For cftime objects, use native min/max
    and convert to string for display.
    """
    if values is None or len(values) == 0:
        return None, None, None

    try:
        vals = pd.to_datetime(values)
        if len(vals) == 0:
            return None, None, None
        return pd.Timestamp(vals.min()), pd.Timestamp(vals.max()), None
    except Exception:
        try:
            cur_min = min(values)
            cur_max = max(values)
            calendar_type = type(values[0]).__name__
            return cur_min, cur_max, calendar_type
        except Exception as exc:
            return None, None, f"unreadable time values: {exc}"

def _subset_bbox_1d_coords(
    ds: xr.Dataset,
    bbox: Dict[str, float],
    source_name: str = "dataset",
) -> xr.Dataset:
    """
    Subset a dataset by bbox using 1D lat/lon coordinate values.

    This does not require lat/lon to be xarray index coordinates.
    It works when lat and lon are 1D coordinates, even if their dimension names
    are not exactly 'lat' and 'lon'.
    """
    if "lat" not in ds.coords or "lon" not in ds.coords:
        return ds

    lat = ds["lat"]
    lon = ds["lon"]

    if lat.ndim != 1 or lon.ndim != 1:
        raise ValueError(
            f"Bounding box subset currently supports only 1D lat/lon coordinates in {source_name}. "
            f"Got lat.ndim={lat.ndim}, lon.ndim={lon.ndim}."
        )

    south = float(bbox["south"])
    north = float(bbox["north"])
    west = float(bbox["west"])
    east = float(bbox["east"])

    lat_dim = lat.dims[0]
    lon_dim = lon.dims[0]

    lat_values = np.asarray(lat.values)
    lon_values = np.asarray(lon.values)

    lat_mask = (lat_values >= south) & (lat_values <= north)
    lon_mask = (lon_values >= west) & (lon_values <= east)

    lat_idx = np.where(lat_mask)[0]
    lon_idx = np.where(lon_mask)[0]

    if lat_idx.size == 0:
        raise ValueError(
            f"Bounding box produced an empty latitude subset in {source_name}. "
            f"Requested south/north=({south}, {north}); "
            f"available lat range=({float(np.nanmin(lat_values))}, {float(np.nanmax(lat_values))})."
        )

    if lon_idx.size == 0:
        raise ValueError(
            f"Bounding box produced an empty longitude subset in {source_name}. "
            f"Requested west/east=({west}, {east}); "
            f"available lon range=({float(np.nanmin(lon_values))}, {float(np.nanmax(lon_values))})."
        )

    return ds.isel({
        lat_dim: lat_idx,
        lon_dim: lon_idx,
    })

def _json_safe(value: Any) -> Any:
    """
    Convert common numpy/pandas/xarray/cftime objects to JSON-safe values.

    This is mainly used for writing the manifest file. NetCDF encodings may
    contain numpy dtypes or other objects that json.dump cannot serialize.
    """
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]

    if isinstance(value, np.generic):
        return value.item()

    if isinstance(value, np.dtype):
        return str(value)

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    # Handles pandas/numpy extension dtypes such as Float64DType.
    if hasattr(value, "name") and value.__class__.__name__.endswith("DType"):
        return str(value)

    # Handles cftime objects and any remaining non-JSON-native objects.
    return str(value)        

def _selected_target_variables(config: NCBuildConfig) -> List[str]:
    """
    Return target variables selected for output.

    New multi-variable mode uses config.target_variables.
    Legacy single-variable mode uses config.target_variable.
    """
    selected = [
        str(v) for v in (config.target_variables or [])
        if v not in (None, "", "None")
    ]

    if selected:
        # Preserve order and remove duplicates.
        unique: List[str] = []
        seen = set()
        for v in selected:
            if v not in seen:
                seen.add(v)
                unique.append(v)
        return unique

    if config.target_variable not in (None, "", "None"):
        return [str(config.target_variable)]

    return []

def _standardize_one_dataset(
    path: Path,
    config: NCBuildConfig,
    time_lookup: Dict[str, Any],
    level_lookup: Dict[str, Any],
) -> xr.Dataset:
    open_kwargs = {"decode_times": True}
    if config.use_dask_chunks:
        if config.chunking_mode == "auto":
            open_kwargs["chunks"] = "auto"
        elif config.manual_chunks:
            # Only keep positive chunks; xarray will ignore unknown dims poorly,
            # so this is applied later after dims are known if needed.
            open_kwargs["chunks"] = {k: int(v) for k, v in config.manual_chunks.items() if int(v) > 0}

    try:
        ds, engine_used = _open_dataset_auto(
            path,
            open_kwargs,
            preferred_engine=config.open_engine,
        )
    except Exception:
        # Fallback without dask/chunks.
        ds, engine_used = _open_dataset_auto(
            path,
            {"decode_times": True},
            preferred_engine=config.open_engine,
        )

    selected_vars = _selected_target_variables(config)
    missing_vars = [
        var for var in selected_vars
        if var not in ds.data_vars and var not in ds.variables
    ]

    if missing_vars:
        ds.close()
        raise ValueError(
            f"Target variable(s) {missing_vars!r} not found in {path.name}"
        )

    # Rename coordinates/dims to ECODATA/CF-style names.
    ds = _rename_if_needed(ds, config.lat_variable, "lat")
    ds = _rename_if_needed(ds, config.lon_variable, "lon")

    if config.time_source == "From NetCDF time coordinate":
        ds = _rename_if_needed(ds, config.time_variable, "time")

    if config.level_source == "From NetCDF coordinate" and config.level_variable not in (None, "", "None"):
        ds = _rename_if_needed(ds, config.level_variable, "level")

    # Build output dataset.
    # Single-variable mode preserves the output_variable_name behaviour.
    # Multi-variable mode keeps original variable names to avoid ambiguous renaming.
    if len(selected_vars) == 1:
        old_name = selected_vars[0]
        new_name = config.output_variable_name or old_name

        da = ds[old_name]
        if new_name != old_name:
            da = da.rename(new_name)

        out = da.to_dataset()
    else:
        out = ds[selected_vars].copy()

    out.attrs["source_open_engine"] = engine_used
    out.attrs["source_file"] = str(path)

    # Add time if it comes from filename/table and is not already a dimension.
    if config.time_source == "From filename":
        t = _parse_from_filename(path, config.time_regex, time_format=config.time_format)
        if "time" not in out.dims:
            out = out.expand_dims(time=[pd.Timestamp(t)])
        else:
            out = out.assign_coords(time=pd.to_datetime(out["time"].values))
    elif config.time_source == "Manual table":
        value = _lookup_by_filename(path, time_lookup)
        if value is None:
            raise ValueError(f"No DateTime entry found in time table for {path.name}")
        t = pd.to_datetime(value)
        if "time" not in out.dims:
            out = out.expand_dims(time=[pd.Timestamp(t)])
    elif "time" in out.coords:
        out = _normalize_time_coord_if_possible(out)

    # Add level if it comes from filename/table and is not already a dimension.
    if config.level_source == "From filename":
        level_value = _parse_from_filename(path, config.level_regex, cast=float)
        if "level" not in out.dims:
            out = out.expand_dims(level=[level_value])
    elif config.level_source == "Manual table":
        value = _lookup_by_filename(path, level_lookup)
        if value is None:
            raise ValueError(f"No level entry found in level table for {path.name}")
        level_value = float(value)
        if "level" not in out.dims:
            out = out.expand_dims(level=[level_value])

    # Keep only expected data + standard coords where possible.
    if "lat" not in out.variables and "lat" not in out.coords:
        raise ValueError(f"Could not standardize latitude coordinate in {path.name}")
    if "lon" not in out.variables and "lon" not in out.coords:
        raise ValueError(f"Could not standardize longitude coordinate in {path.name}")

    # Convert lon 0..360 to -180..180 when lon is 1D.
    if config.convert_longitude_to_180 and "lon" in out.coords:
        lon = out["lon"]
        try:
            if lon.ndim == 1 and float(lon.max()) > 180:
                new_lon = ((lon + 180) % 360) - 180
                out = out.assign_coords(lon=new_lon).sortby("lon")
        except Exception:
            pass

    # Sort common dims.
    for dim in ("time", "level", "lat", "lon"):
        if dim in out.coords:
            try:
                out = out.sortby(dim)
            except Exception:
                pass

    # Spatial subset by bbox.
    if config.bbox:
        out = _subset_bbox_1d_coords(
            out,
            config.bbox,
            source_name=path.name,
        )

    # Time subset.
    # In "By time" mode, the selected input files define the time range.
    # This avoids unsafe pandas Timestamp slicing for cftime calendars.
    if (
        config.combine_mode != "By time"
        and "time" in out.coords
        and (config.start_time or config.end_time)
    ):
        time_values = out["time"].values
        first_time = time_values[0] if len(time_values) else None

        if first_time is not None and first_time.__class__.__module__.startswith("cftime"):
            # Skip cftime slicing until a dedicated cftime-aware subset is implemented.
            pass
        else:
            start = pd.to_datetime(config.start_time) if config.start_time else None
            end = pd.to_datetime(config.end_time) if config.end_time else None
            out = out.sel(time=slice(start, end))

    return out


def _check_grid_compatibility(datasets: Sequence[xr.Dataset]) -> None:
    if not datasets:
        raise ValueError("No datasets to combine.")

    ref = datasets[0]
    for coord in ("lat", "lon"):
        if coord not in ref.coords:
            continue
        ref_vals = ref[coord].values
        for i, ds in enumerate(datasets[1:], start=2):
            if coord not in ds.coords:
                raise ValueError(f"Dataset #{i} is missing coordinate {coord!r}")
            vals = ds[coord].values
            if ref_vals.shape != vals.shape or not np.allclose(ref_vals, vals, equal_nan=True):
                raise ValueError(
                    f"Grid incompatibility for coordinate {coord!r}: "
                    f"dataset #1 shape {ref_vals.shape}, dataset #{i} shape {vals.shape}"
                )


def _combine_datasets(datasets: Sequence[xr.Dataset], config: NCBuildConfig) -> xr.Dataset:
    _check_grid_compatibility(datasets)

    try:
        combined = xr.combine_by_coords(list(datasets), combine_attrs="override")
    except Exception:
        # Fallback based on selected mode.
        if config.combine_mode == "By time":
            combined = xr.concat(list(datasets), dim="time", combine_attrs="override")
        elif config.combine_mode == "By level":
            combined = xr.concat(list(datasets), dim="level", combine_attrs="override")
        else:
            # combine_by_coords is the safer option for time+level;
            # if it failed, the layouts are probably ambiguous.
            raise

    for dim in ("time", "level", "lat", "lon"):
        if dim in combined.coords:
            try:
                combined = combined.sortby(dim)
            except Exception:
                pass

    return combined


def _apply_cf_metadata(ds: xr.Dataset, config: NCBuildConfig) -> xr.Dataset:
    if "lat" in ds.coords:
        ds["lat"].attrs.update({
            "standard_name": "latitude",
            "long_name": "latitude",
            "units": "degrees_north",
            "axis": "Y",
        })

    if "lon" in ds.coords:
        ds["lon"].attrs.update({
            "standard_name": "longitude",
            "long_name": "longitude",
            "units": "degrees_east",
            "axis": "X",
        })

    if "time" in ds.coords:
        ds["time"].attrs.update({
            "standard_name": "time",
            "long_name": "time",
            "axis": "T",
        })

    if "level" in ds.coords:
        attrs = {
            "long_name": "vertical level",
            "axis": "Z",
            "units": config.level_units,
        }
        if config.level_units in ("hPa", "Pa"):
            attrs.update({
                "standard_name": "air_pressure",
                "positive": "down",
            })
        elif config.level_units == "m":
            attrs.update({
                "standard_name": "height",
                "positive": "up",
            })
        ds["level"].attrs.update(attrs)

    ds.attrs.update({
        "title": "ECODATA standardized NetCDF",
        "Conventions": "CF-1.8",
        "history": f"Created by ECODATA NCBuilder",
        "source_files_count": len(config.files),
        "combine_mode": config.combine_mode,
    })

    return ds

def _apply_time_encoding(ds: xr.Dataset, config: NCBuildConfig) -> xr.Dataset:
    """
    Apply optional time encoding for ECODATA/MATLAB compatibility.

    This does not change the actual time coordinate values in memory.
    It only controls how the time coordinate is written to the NetCDF file.
    """
    if not config.use_modis_time_encoding:
        return ds

    if "time" not in ds.coords:
        return ds

    ds["time"].encoding.update({
        "units": "days since 2000-01-01",
        "calendar": "julian",
    })

    return ds

def _encoding_for(ds: xr.Dataset, config: NCBuildConfig) -> Dict[str, Dict[str, Any]]:
    encoding: Dict[str, Dict[str, Any]] = {}

    # Preserve explicit time encoding if it was set by _apply_time_encoding().
    if "time" in ds.coords and ds["time"].encoding:
        time_encoding = {}
        for key in ("units", "calendar", "dtype"):
            if key in ds["time"].encoding:
                time_encoding[key] = ds["time"].encoding[key]
        if time_encoding:
            encoding["time"] = time_encoding

    if not config.enable_compression:
        return encoding

    for var in ds.data_vars:
        encoding[var] = {
            "zlib": True,
            "complevel": 4,
        }

    return encoding


def build_standardized_netcdf(config: NCBuildConfig) -> Dict[str, Any]:
    ok, errors, warnings = validate_build_config(config)
    if not ok:
        raise ValueError("Invalid NCBuildConfig: " + "; ".join(errors))

    output_path = Path(config.output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    time_lookup = _load_lookup_table(config.time_table_path, "DateTime") if config.time_source == "Manual table" else {}
    level_lookup = _load_lookup_table(config.level_table_path, "level") if config.level_source == "Manual table" else {}

    datasets: List[xr.Dataset] = []
    processed_files: List[str] = []

    for f in config.files:
        path = Path(f).expanduser()
        if not path.exists():
            continue
        ds = _standardize_one_dataset(path, config, time_lookup, level_lookup)
        datasets.append(ds)
        processed_files.append(str(path))

    if not datasets:
        raise ValueError("No datasets were successfully opened.")

    combined = _combine_datasets(datasets, config)
    combined = _apply_cf_metadata(combined, config)
    combined = _apply_time_encoding(combined, config)

    encoding = _encoding_for(combined, config)
    combined.to_netcdf(output_path, encoding=encoding)

    engine_by_file = {}
    for ds in datasets:
        source_file = ds.attrs.get("source_file")
        source_engine = ds.attrs.get("source_open_engine")
        if source_file and source_engine:
            engine_by_file[Path(source_file).name] = source_engine

    # Close source datasets to release file handles.
    for ds in datasets:
        try:
            ds.close()
        except Exception:
            pass

    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest = {
        "output_path": str(output_path),
        "manifest_path": str(manifest_path),
        "processed_files": _json_safe(processed_files),
        "engine_by_file": _json_safe(engine_by_file),
        "config": _json_safe(asdict(config)),
        "warnings": _json_safe(warnings),
        "output_dims": _json_safe({k: int(v) for k, v in combined.sizes.items()}),
        "output_variables": _json_safe(list(map(str, combined.data_vars))),
        "output_coords": _json_safe(list(map(str, combined.coords))),
        "time_encoding": _json_safe(dict(combined["time"].encoding)) if "time" in combined.coords else {},
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)

    try:
        combined.close()
    except Exception:
        pass

    return manifest
