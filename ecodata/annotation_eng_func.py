import xarray as xr
import geopandas as gpd
from pathlib import Path
import gc
import time
import pandas as pd
import re
from shapely.geometry import Point
import numpy as np
from datetime import datetime
import rasterio
from pyproj import CRS, Transformer
import tempfile

try:
    from scipy.spatial import cKDTree
except Exception:  # Optional dependency; a NumPy fallback is provided.
    cKDTree = None

LEVEL_DIM_CANDIDATES = ("isobaricInhPa", "isobaric_in_hPa", "level", "lev", "plev", "pressure", "pressure_level")


def parse_movement_timestamps(values):
    """Parse movement timestamps with mixed fractional-second formats safely."""
    try:
        return pd.to_datetime(values, format="mixed", dayfirst=False, errors="coerce")
    except (TypeError, ValueError):
        # Compatibility with older pandas versions without format="mixed".
        return pd.to_datetime(values, dayfirst=False, errors="coerce")


def _normalise_selected_ids(selected_ids):
    """Return selected IDs as stripped strings for stable CSV/UI comparison."""
    return {str(value).strip() for value in (selected_ids or []) if pd.notna(value)}


def normalize_longitude_values(values):
    """
    Normalize longitude values to the internal ECODATA convention:

        [-180, 180)

    Examples:
        280.0  -> -80.0
        359.0  -> -1.0
        -80.0  -> -80.0
    """

    values = np.asarray(values, dtype="float64")

    return ((values + 180.0) % 360.0) - 180.0


def safe_open_nc_with_time_decoding(path, time_name: str | None = None):
    """
    Open a NetCDF file and standardize its time coordinate.

    The original time variable may have names such as:
        time
        valid_time
        forecast_time
        Time
        XTIME
        ...

    After opening, ECODATA always uses:
        time

    The original source name is preserved in:
        ds.attrs["_ecodata_source_time_name"]
    """

    ds = None

    try:
        # Prefer lazy opening when dask is available.
        try:
            ds = xr.open_dataset(path, decode_times=False, chunks=None)
        except Exception as chunk_exc:
            message = str(chunk_exc).lower()

            if "chunk manager" not in message and "dask" not in message:
                raise

            ds = xr.open_dataset(path, decode_times=False)

        # Detect the actual variable in the ORIGINAL file.
        #
        # Important:
        # if time_name="time" was supplied but the physical file
        # contains only "valid_time", detection falls back safely.
        source_time_name = detect_time_name(ds, preferred=time_name)

        if source_time_name is None:
            raise ValueError(
                "No time coordinate/variable could be detected. "
                "Expected CF time metadata or a known time variable name."
            )

        if source_time_name in ds.variables and source_time_name not in ds.coords:
            ds = ds.set_coords(source_time_name)

        time_var = ds[source_time_name]

        if time_var.ndim != 1:
            raise ValueError(f"Time variable '{source_time_name}' must be 1D. " f"Found dimensions: {time_var.dims}")

        values = np.asarray(time_var.values)
        units = str(time_var.attrs.get("units", "")).strip()
        calendar = str(time_var.attrs.get("calendar", "standard")).strip() or "standard"

        # ---------------------------------------------------------
        # Case 1: already datetime64
        # ---------------------------------------------------------
        if np.issubdtype(time_var.dtype, np.datetime64):
            decoded = pd.to_datetime(values)

        # ---------------------------------------------------------
        # Case 2: normal CF numeric time
        # e.g. "hours since 1900-01-01"
        # ---------------------------------------------------------
        elif "since" in units.lower():
            decoded_raw = xr.coding.times.decode_cf_datetime(values, units, calendar)
            decoded_array = np.asarray(decoded_raw)

            if np.issubdtype(decoded_array.dtype, np.datetime64):
                decoded = pd.to_datetime(decoded_array)

            else:
                # cftime-like objects
                decoded = pd.to_datetime([str(value) for value in decoded_array], errors="raise")

        # ---------------------------------------------------------
        # Case 3: textual datetime values
        # ---------------------------------------------------------
        elif time_var.dtype.kind in ("U", "S", "O"):
            decoded = pd.to_datetime(values, errors="coerce")

            if pd.isna(decoded).any():
                raise ValueError(f"Could not parse all values from " f"time variable '{source_time_name}'.")

        # ---------------------------------------------------------
        # Case 4: numeric time without CF units
        # ---------------------------------------------------------
        else:
            raise ValueError(
                f"Numeric time variable '{source_time_name}' "
                f"has no CF-compatible 'units since ...' metadata. "
                "ECODATA will not guess an epoch because this could "
                "produce incorrect dates."
            )

        decoded_values = pd.DatetimeIndex(decoded).to_numpy(dtype="datetime64[ns]")
        source_dim = time_var.dims[0]

        # ---------------------------------------------------------
        # Standardize to the internal ECODATA coordinate "time"
        # ---------------------------------------------------------

        # Typical:
        # valid_time(valid_time)
        if source_dim == source_time_name:
            ds = ds.assign_coords({source_time_name: decoded_values})
            if source_time_name != "time":
                ds = ds.rename({source_time_name: "time"})

        # Example:
        # valid_time(time)
        elif source_dim == "time":
            ds = ds.assign_coords(time=("time", decoded_values))

        # Example:
        # valid_time(step)
        else:
            ds = ds.assign_coords({source_time_name: (source_dim, decoded_values)})
            ds = ds.swap_dims({source_dim: source_time_name})
            if source_time_name != "time":
                ds = ds.rename({source_time_name: "time"})

        # Preserve source metadata for later re-opening.
        ds.attrs["_ecodata_source_time_name"] = source_time_name
        ds.attrs["_ecodata_internal_time_name"] = "time"

        return ds

    except Exception as e:
        if ds is not None:
            try:
                ds.close()
            except Exception:
                pass

        raise RuntimeError(f"[ERROR] Failed to decode time for {path}: {e}")


def normalize_env_source_paths(file_spec):
    """
    Normalize an environmental source specification.
    Supported:
        variable -> "file.nc"
    and:
        variable -> ["file1.nc", "file2.nc", ...]
    Returns
    -------
    list[str]
    """

    if file_spec is None:
        return []

    if isinstance(file_spec, (str, Path)):
        values = [file_spec]

    elif isinstance(file_spec, (list, tuple, set)):
        values = list(file_spec)

    else:
        raise TypeError(
            "Environmental source must be a file path " "or a list of file paths. " f"Got {type(file_spec).__name__}."
        )

    result = []
    seen = set()

    for value in values:
        if value is None:
            continue

        path = Path(str(value)).expanduser()
        resolved = str(path.resolve())

        if resolved in seen:
            continue

        seen.add(resolved)
        result.append(str(path))

    return result


def get_nc_timerange_for_selected(env_var_map: dict, selected_env_vars: list[str], time_name: str | None = None):
    """
    Return the union time range across all unique physical
    NetCDF files used by the selected environmental variables.
    env_var_map values may be either:
        variable -> path
    or:
        variable -> [path1, path2, ...]
    """

    nc_start = None
    nc_end = None
    processed_files = set()

    for variable in selected_env_vars or []:
        file_spec = env_var_map.get(variable)
        for nc_path in normalize_env_source_paths(file_spec):
            path_key = str(Path(nc_path).resolve())
            if path_key in processed_files:
                continue

            processed_files.add(path_key)
            ds = safe_open_nc_with_time_decoding(nc_path, time_name=time_name)
            try:
                if "time" not in ds.coords and "time" not in ds.variables:
                    continue
                values = pd.to_datetime(ds["time"].values)
                if len(values) == 0:
                    continue

                tmin = pd.Timestamp(values.min())
                tmax = pd.Timestamp(values.max())

                if nc_start is None or tmin < nc_start:
                    nc_start = tmin

                if nc_end is None or tmax > nc_end:
                    nc_end = tmax

            finally:

                ds.close()

    return nc_start, nc_end


def build_environmental_time_batches(env_var_map, selected_vars, time_name=None):
    """
    Build sequential environmental annotation batches.
    The variable with the largest number of physical source
    files is used as the temporal batch driver.
    Example
    -------
    air_850:
        March.nc
        April.nc
        May.nc

    t2m:
        whole_year.nc

    creates:

        March batch:
            air_850 -> March.nc
            t2m     -> whole_year.nc

        April batch:
            air_850 -> April.nc
            t2m     -> whole_year.nc

        May batch:
            air_850 -> May.nc
            t2m     -> whole_year.nc

    No NetCDF files are concatenated.
    """

    selected_vars = list(selected_vars or [])

    if not selected_vars:
        return []

    # ===
    # 1. Normalize sources per variable.
    # ===
    sources_by_var = {}

    for variable in selected_vars:
        paths = normalize_env_source_paths(env_var_map.get(variable))
        if not paths:
            raise ValueError(f"No NetCDF source found for " f"environmental variable '{variable}'.")

        sources_by_var[variable] = paths

    # ===
    # 2. Determine actual time range of every physical file.
    # ===
    file_ranges = {}

    for paths in sources_by_var.values():
        for nc_path in paths:
            path_key = str(Path(nc_path).resolve())
            if path_key in file_ranges:
                continue

            ds = safe_open_nc_with_time_decoding(nc_path, time_name=time_name)

            try:
                if "time" not in ds.coords and "time" not in ds.variables:
                    raise ValueError(f"No standardized time coordinate " f"in {nc_path}")

                values = pd.to_datetime(ds["time"].values)

                if len(values) == 0:
                    raise ValueError(f"Empty time coordinate in {nc_path}")

                tmin = pd.Timestamp(values.min())
                tmax = pd.Timestamp(values.max())
                file_ranges[path_key] = {"path": nc_path, "start": tmin, "end": tmax}

            finally:

                ds.close()

    # ===
    # 3. Pick temporal driver.
    #
    # The variable with the largest number of files normally
    # represents the finest temporal partition.
    # ===
    driver_var = max(selected_vars, key=lambda variable: len(sources_by_var[variable]))
    driver_sources = sources_by_var[driver_var]
    driver_ranges = [file_ranges[str(Path(path).resolve())] for path in driver_sources]
    driver_ranges = sorted(driver_ranges, key=lambda item: (item["start"], item["end"]))

    # ===
    # 4. Driver batches must not overlap.
    # ===
    previous_end = None

    for item in driver_ranges:

        if previous_end is not None and item["start"] <= previous_end:
            raise ValueError(
                "Temporal NetCDF batches overlap. "
                "Batch processing currently requires "
                "non-overlapping temporal chunks. "
                f"Conflict near {item['start']}."
            )

        previous_end = item["end"]

    # ---------------------------------------------------------
    # 5. For every driver interval, find exactly one source
    #    file for every selected variable that fully covers it.
    # ---------------------------------------------------------
    batches = []

    for batch_number, driver in enumerate(driver_ranges, start=1):
        batch_start = driver["start"]
        batch_end = driver["end"]
        batch_map = {}

        for variable in selected_vars:
            candidates = []

            for source_path in sources_by_var[variable]:
                source = file_ranges[str(Path(source_path).resolve())]

                if source["start"] <= batch_start and source["end"] >= batch_end:
                    candidates.append(source["path"])

            if not candidates:
                raise ValueError(
                    f"No NetCDF source for '{variable}' " f"fully covers batch " f"{batch_start} — {batch_end}."
                )

            if len(candidates) > 1:

                raise ValueError(
                    f"More than one NetCDF source for "
                    f"'{variable}' covers batch "
                    f"{batch_start} — {batch_end}. "
                    "The source is ambiguous."
                )

            batch_map[variable] = candidates[0]

        batches.append({"number": batch_number, "start": batch_start, "end": batch_end, "env_var_map": batch_map})

    print("[INFO] Built " f"{len(batches)} environmental time batch(es). " f"Driver variable: {driver_var}")

    for batch in batches:
        print(f"[INFO] Batch {batch['number']}: " f"{batch['start']} -> {batch['end']}")

    return batches


def get_nc_bounds(nc_path: str, env_coord_names: dict | None = None):
    """
    Return geographic NetCDF bounds in WGS84-like lon/lat coordinates.
    Time decoding is intentionally NOT performed here because
    spatial bounds do not depend on the time coordinate.
    """

    env_coord_names = env_coord_names or {}

    ds = xr.open_dataset(nc_path, decode_times=False)

    try:
        lat_name = env_coord_names.get("env_lat")
        lon_name = env_coord_names.get("env_lon")

        if not lat_name or not lon_name:
            lat_candidates = ("lat", "latitude", "Latitude")
            lon_candidates = ("lon", "longitude", "Longitude", "long")
            lat_name = next((name for name in lat_candidates if (name in ds.coords or name in ds.variables)), None)
            lon_name = next((name for name in lon_candidates if (name in ds.coords or name in ds.variables)), None)

        if lat_name is None or lon_name is None:
            raise ValueError("Could not detect latitude/longitude " "coordinates in NetCDF.")

        lat_min = float(ds[lat_name].min())
        lat_max = float(ds[lat_name].max())
        lon_values = normalize_longitude_values(ds[lon_name].values)
        lon_min = float(np.nanmin(lon_values))
        lon_max = float(np.nanmax(lon_values))
        return {"S": lat_min, "N": lat_max, "W": lon_min, "E": lon_max}

    finally:
        ds.close()


def load_vector_extent_info(path):
    """
    Load a .shp/.geojson boundary and return its geographic
    extent in WGS84 longitude/latitude coordinates.
    """

    try:
        ext = Path(path).suffix.lower()

        if ext not in (".shp", ".geojson"):
            raise ValueError("Unsupported file format. " "Please select a .shp or .geojson file.")

        gdf = gpd.read_file(path)

        if gdf.empty:
            raise ValueError("Boundary file contains no geometries.")

        if gdf.crs is None:
            raise ValueError("Boundary file has no defined CRS. " "Please define the correct CRS before using it.")

        # UI and movement data use geographic WGS84 coordinates.
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        bounds = gdf.total_bounds
        west, south, east, north = bounds

        return (path, float(south), float(north), float(west), float(east))

    except Exception as e:
        raise RuntimeError(f"Failed to load vector file: {e}")


def _normalize_csv_column_name(name):
    return re.sub(r"[-._\s]+", "_", str(name).strip().lower())


def _prepare_movement_dataframe(
    df, id_column=None, taxon_column=None, time_column=None, lat_column=None, lon_column=None
):
    """
    Normalize CSV headings and map custom movement columns to the
    canonical internal names used by Annotation Engine.
    """
    df = df.copy()

    df.columns = [_normalize_csv_column_name(column) for column in df.columns]

    mappings = [
        (id_column, "individual_local_identifier", "Animal ID"),
        (taxon_column, "individual_taxon_canonical_name", "Taxon"),
        (time_column, "timestamp", "Time"),
        (lat_column, "location_lat", "Latitude"),
        (lon_column, "location_lon", "Longitude"),
    ]

    for selected_column, canonical_name, label in mappings:
        if selected_column is None:
            continue

        selected_column = _normalize_csv_column_name(selected_column)

        if selected_column not in df.columns:
            raise ValueError(f"Selected {label} column not found: {selected_column}")

        if selected_column == canonical_name:
            continue

        if canonical_name in df.columns:
            raise ValueError(f"CSV already contains '{canonical_name}', " f"but another {label} column was selected.")

        df = df.rename(columns={selected_column: canonical_name})

    # Compatibility with files using location_long.
    if "location_lon" not in df.columns and "location_long" in df.columns:
        df = df.rename(columns={"location_long": "location_lon"})

    # Compatibility with files using eobs_start_timestamp.
    if "timestamp" not in df.columns and "eobs_start_timestamp" in df.columns:
        df["timestamp"] = df["eobs_start_timestamp"]

    return df


def load_taxa_and_ids_from_csv(
    file_path, id_column=None, taxon_column=None, time_column=None, lat_column=None, lon_column=None
):
    """
    Read a movement CSV.
    Movebank-compatible mode:
        canonical columns are detected directly.

    Custom mode:
        user-selected columns are internally mapped to canonical
        Annotation Engine names.
    """
    try:
        df = pd.read_csv(file_path)
        df = _prepare_movement_dataframe(
            df,
            id_column=id_column,
            taxon_column=taxon_column,
            time_column=time_column,
            lat_column=lat_column,
            lon_column=lon_column,
        )

        id_col = "individual_local_identifier"
        taxon_col = "individual_taxon_canonical_name"

        if id_col not in df.columns:
            return (None, [], [], "No Animal ID column found.")

        if "timestamp" not in df.columns:
            return (None, [], [], "No time column found.")

        if "location_lat" not in df.columns:
            return (None, [], [], "No latitude column found.")

        if "location_lon" not in df.columns:
            return (None, [], [], "No longitude column found.")

        unique_ids = sorted(df[id_col].dropna().astype(str).unique())
        unique_taxa = sorted(df[taxon_col].dropna().astype(str).unique()) if taxon_col in df.columns else []

        return df, unique_taxa, unique_ids, None

    except Exception as e:
        return None, [], [], str(e)


def start_annotation_process(
    env_var_map,
    selected_env_vars,
    movebank_path,
    selected_ids,
    boundary_path,
    interpolation_method,
    bbox=None,
    smoothing_k: int = 2,
    out_csv_path=None,
    coord_spec=None,
    env_coord_names: dict | None = None,
    continuous_vars=None,
    categorical_vars=None,
    apply_value_correction: bool = False,
    value_scale_factor: float = 1.0,
    value_add_offset: float = 0.0,
    value_correction_vars=None,
    dataset_descriptor: dict | None = None,
    movement_column_map: dict | None = None,
    time_range_mode: str = "preserve",
):
    """
    env_var_map: dict[str, str] — variable → file path
    selected_env_vars: list[str] — selected variables
    movebank_path: str — path to the Movebank CSV
    selected_ids: list[str] — IDs for annotation
    boundary_path: str — path to .shp or .geojson
    """
    print("[DEBUG] Annotation started")
    print("Selected variables:", selected_env_vars)
    print("From files:", [env_var_map.get(v) for v in selected_env_vars])
    print("Selected IDs:", selected_ids)
    print("Movebank file:", movebank_path)
    print("Boundary file:", boundary_path)
    print("Interpolation method:", interpolation_method)
    env_coord_names = env_coord_names or {}
    dataset_descriptor = dict(dataset_descriptor or {})

    # Name used in the ORIGINAL NetCDF file.
    source_time_name = (
        dataset_descriptor.get("source_time_name")
        or dataset_descriptor.get("time_name")
        or env_coord_names.get("env_time")
    )

    # Prefer validated adapter metadata over duplicated UI inference.
    if dataset_descriptor:
        env_coord_names = {
            "env_time": dataset_descriptor.get("time_name") or env_coord_names.get("env_time"),
            "env_lat": (
                dataset_descriptor.get("lat_name")
                if dataset_descriptor.get("grid_type") != "projected_rectilinear"
                else None
            ),
            "env_lon": (
                dataset_descriptor.get("lon_name")
                if dataset_descriptor.get("grid_type") != "projected_rectilinear"
                else None
            ),
            "env_x": dataset_descriptor.get("x_name") or env_coord_names.get("env_x"),
            "env_y": dataset_descriptor.get("y_name") or env_coord_names.get("env_y"),
        }

    # bridge from the current coord_spec logic to the #191 commit's env_coord_names naming.

    if not env_coord_names and coord_spec:
        env_coord_names = {
            "env_time": coord_spec.get("time"),
            "env_lat": coord_spec.get("lat"),
            "env_lon": coord_spec.get("lon"),
            "env_x": None,
            "env_y": None,
        }

    # === Step 1: Spatial filtering ===
    df_filtered = filter_points_within_boundary(
        movebank_path, selected_ids, boundary_path, bbox=bbox, movement_column_map=movement_column_map
    )

    if df_filtered.empty:
        print("[WARNING] No points remained " "after ID/spatial filtering.")
        return None

    # === Time-range policy ===
    nc_start, nc_end = get_nc_timerange_for_selected(env_var_map, selected_env_vars, time_name=source_time_name)

    time_range_mode = str(time_range_mode or "preserve").strip().lower()

    if time_range_mode not in {"delete", "preserve"}:
        raise ValueError("Unknown time_range_mode: " f"{time_range_mode!r}. " "Expected 'delete' or 'preserve'.")

    if time_range_mode == "delete":

        df_filtered = filter_points_within_timerange(df_filtered, nc_start, nc_end)

        if df_filtered.empty:
            print("[WARNING] No points remained " "inside the NetCDF time window.")
            return None

        print("[INFO] Time-range mode: delete. " "Movement records outside the NetCDF " "time range were removed.")

    else:

        print(
            "[INFO] Time-range mode: preserve. "
            "Movement records outside the NetCDF "
            "time range are retained; environmental "
            "values outside available time coverage "
            "will remain NaN."
        )

    # === Step 2: Loading and interpolation of environmental data ===
    result = load_selected_environmental_data_batched(
        df_filtered,
        env_var_map,
        selected_env_vars,
        movebank_path,
        interpolation_method,
        smoothing_k=smoothing_k,
        coord_spec=coord_spec,
        env_coord_names=env_coord_names,
        continuous_vars=continuous_vars,
        categorical_vars=categorical_vars,
        dataset_descriptor=dataset_descriptor,
    )
    if result is None:
        print("[ERROR] Environmental data was not loaded.")
        return None

    df_annotated, ann_nc_start, ann_nc_end = result
    # Optional post-sampling value correction
    # Apply only to continuous variables after sampling/interpolation.
    # for linear scale/offset:
    # physical_value = raw_value * scale_factor + add_offset.
    # Categorical/QC variables must remain as raw category/flag codes.
    if apply_value_correction:
        if value_correction_vars is None:
            correction_vars = list(continuous_vars or [])
        else:
            correction_vars = list(value_correction_vars or [])

        try:
            scale = float(value_scale_factor)
            offset = float(value_add_offset)
        except Exception as e:
            raise ValueError(f"Invalid scale factor / offset: {e}")

        for v in correction_vars:
            if v not in df_annotated.columns:
                print(f"[WARNING] Scale/offset skipped for '{v}': column not found.")
                continue

            # Convert only the annotated continuous column.
            # Non-numeric values become NaN, which is acceptable for continuous variables.
            df_annotated[v] = pd.to_numeric(df_annotated[v], errors="coerce") * scale + offset

        print(
            "[INFO] Applied post-sampling scale/offset to continuous variables: "
            f"{correction_vars}; scale={scale}, offset={offset}"
        )
    # Keep the real union NC range computed before annotation,
    # unless an annotator explicitly returns a valid range in the future.
    if not pd.isna(ann_nc_start):
        nc_start = ann_nc_start
    if not pd.isna(ann_nc_end):
        nc_end = ann_nc_end

    #### diagnostic
    var = selected_env_vars[0] if selected_env_vars else None
    if var in df_annotated.columns:
        in_nc = df_annotated["timestamp"].between(
            pd.to_datetime(df_annotated["timestamp"]).min() if pd.isna(nc_start) else nc_start,
            pd.to_datetime(df_annotated["timestamp"]).max() if pd.isna(nc_end) else nc_end,
        )
        filled_total = df_annotated[var].notna().sum()
        filled_in_nc = df_annotated.loc[in_nc, var].notna().sum()
        print(f"[DEBUG] Filled '{var}': total={filled_total}, within-NC-window={filled_in_nc}")
    else:
        print(f"[WARNING] Column '{var}' not found in annotated DataFrame.")

    # === Step 3: Apply output time-range policy ===
    df_time_filtered = df_annotated.copy()

    if time_range_mode == "delete":
        print("[INFO] Records outside the NetCDF " "time range were deleted.")
    else:
        print("[INFO] Full timestamp range preserved. " "Outside-NC environmental values are NaN.")

    # === Step 4: Saving the final result ===
    if out_csv_path:
        out_path = Path(out_csv_path).expanduser()
    else:
        out_path = Path(movebank_path).parent / "annotated_env.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_time_filtered = df_time_filtered.drop(columns=["geometry", "nc_lat", "nc_lon", "x", "y"], errors="ignore")
    df_time_filtered.to_csv(out_path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d %H:%M:%S")
    print(f"[INFO] Final filtered annotation saved to {out_path}")

    # === Step 5: Saving by individual ID ===
    output_folder = out_path.parent / "annotated_individuals"
    output_folder.mkdir(parents=True, exist_ok=True)

    id_col = "individual_local_identifier"
    if id_col in df_time_filtered.columns:
        unique_ids = df_time_filtered[id_col].dropna().unique()
        for uid in unique_ids:
            df_id = df_time_filtered[df_time_filtered[id_col] == uid]
            safe_uid = re.sub(r"[^\w\-]", "_", str(uid))
            out_file = output_folder / f"annotated_env_{safe_uid}.csv"
            df_id.to_csv(out_file, index=False)
        print(f"[INFO] Saved {len(unique_ids)} individual files to {output_folder}")
    else:
        print("[WARNING] Column " "'individual_local_identifier' not found. " "Skipping per-ID export.")

    return str(out_path)


def validate_bbox(bbox):
    """
    Validate and normalize a geographic bounding box.

    Expected format:
        {
            "S": south latitude,
            "N": north latitude,
            "W": west longitude,
            "E": east longitude,
        }
    """
    if bbox is None:
        return None

    required = ("S", "N", "W", "E")

    missing = [key for key in required if key not in bbox or bbox[key] is None]

    if missing:
        raise ValueError("Missing bbox value(s): " + ", ".join(missing))

    try:
        S = float(bbox["S"])
        N = float(bbox["N"])
        W = float(bbox["W"])
        E = float(bbox["E"])
    except (TypeError, ValueError):
        raise ValueError("bbox coordinates must be numeric.")

    if not all(np.isfinite(v) for v in (S, N, W, E)):
        raise ValueError("bbox coordinates must be finite numbers.")

    if not (-90.0 <= S <= 90.0):
        raise ValueError("South latitude must be between -90 and 90.")

    if not (-90.0 <= N <= 90.0):
        raise ValueError("North latitude must be between -90 and 90.")

    if not (-180.0 <= W <= 180.0):
        raise ValueError("West longitude must be between -180 and 180.")

    if not (-180.0 <= E <= 180.0):
        raise ValueError("East longitude must be between -180 and 180.")

    if S >= N:
        raise ValueError("South latitude must be smaller than North latitude.")

    if W >= E:
        raise ValueError("West longitude must be smaller than East longitude.")

    return {"S": S, "N": N, "W": W, "E": E}


def filter_points_within_boundary(movebank_path, selected_ids, boundary_path=None, bbox=None, movement_column_map=None):
    print("[DEBUG] Filtering is started")

    df = pd.read_csv(movebank_path)

    movement_column_map = movement_column_map or {}

    df = _prepare_movement_dataframe(
        df,
        id_column=movement_column_map.get("id"),
        taxon_column=movement_column_map.get("taxon"),
        time_column=movement_column_map.get("time"),
        lat_column=movement_column_map.get("lat"),
        lon_column=movement_column_map.get("lon"),
    )

    required_cols = {"location_lat", "location_lon", "individual_local_identifier", "timestamp"}

    if not required_cols.issubset(df.columns):
        raise ValueError(
            "Required columns are missing in Movebank file. " f"Missing: {required_cols - set(df.columns)}"
        )

    # 
    # Filter selected individual IDs
    # 
    df["individual_local_identifier"] = df["individual_local_identifier"].astype("string").str.strip()

    selected_ids_normalized = _normalise_selected_ids(selected_ids)

    df = df[df["individual_local_identifier"].isin(selected_ids_normalized)].copy()

    print(f"[INFO] Rows after ID filtering: {len(df)}; " f"selected IDs: {sorted(selected_ids_normalized)}")

    # 
    # Interpolate missing movement coordinates
    # 
    df = interpolate_missing_coordinates(df)

    # 
    # ECODATA internal longitude convention: [-180, 180)
    # 
    df["location_lon"] = normalize_longitude_values(pd.to_numeric(df["location_lon"], errors="coerce").to_numpy())

    df["location_lat"] = pd.to_numeric(df["location_lat"], errors="coerce")

    # Remove rows where coordinates are still unavailable.
    df = df.dropna(subset=["location_lat", "location_lon"]).copy()

    # 
    # Manual BBOX
    # 

    bbox = validate_bbox(bbox)

    if bbox is not None:

        S = bbox["S"]
        N = bbox["N"]
        W = bbox["W"]
        E = bbox["E"]

        mask = df["location_lat"].between(S, N) & df["location_lon"].between(W, E)

        df = df.loc[mask].copy()

        df["geometry"] = [Point(lon, lat) for lon, lat in zip(df["location_lon"], df["location_lat"])]

        gdf_filtered = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")

        print(f"[INFO] Rows after bbox filtering: " f"{len(gdf_filtered)}")

        return gdf_filtered

    # 
    # Movement points as WGS84 GeoDataFrame
    # 

    df["geometry"] = [Point(lon, lat) for lon, lat in zip(df["location_lon"], df["location_lat"])]
    gdf_points = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")

    #
    # No spatial boundary
    #

    if boundary_path is None:

        print("[INFO] No boundary provided. " "Skipping spatial clipping " "(all selected IDs kept).")

        return gdf_points

    # 
    # SHP / GeoJSON boundary
    # 

    gdf_boundary = gpd.read_file(boundary_path)

    if gdf_boundary.empty:
        raise ValueError("Boundary file contains no geometries.")

    if gdf_boundary.crs is None:
        raise ValueError("Boundary file has no defined CRS. " "Please define the correct CRS before using it.")

    # Movement points are EPSG:4326.
    if gdf_boundary.crs != gdf_points.crs:
        gdf_boundary = gdf_boundary.to_crs(gdf_points.crs)

    gdf_filtered = gpd.sjoin(gdf_points, gdf_boundary[["geometry"]], predicate="within", how="inner").drop(
        columns="index_right"
    )
    print(f"[INFO] Rows after boundary filtering: " f"{len(gdf_filtered)}")

    return gdf_filtered


def filter_points_within_timerange(df: pd.DataFrame, nc_start: pd.Timestamp, nc_end: pd.Timestamp) -> pd.DataFrame:
    df = df.copy()
    if nc_start is None or nc_end is None:
        print("[INFO] NC union time range unavailable. Skipping time prefilter.")
        return df
    df["timestamp"] = parse_movement_timestamps(df["timestamp"])
    before = len(df)
    filtered_df = df[(df["timestamp"] >= nc_start) & (df["timestamp"] <= nc_end)]
    print(f"[INFO] Time-prefiltered rows: {len(filtered_df)} / {before} within [{nc_start} .. {nc_end}]")
    return filtered_df


def interpolate_missing_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Interpolate missing location_lat/location_lon values separately
    for each individual_local_identifier using timestamp.
    """

    required_cols = {"individual_local_identifier", "timestamp", "location_lat", "location_lon"}

    if not required_cols.issubset(df.columns):
        missing = required_cols - set(df.columns)
        raise ValueError(f"DataFrame must contain columns: {required_cols}. " f"Missing: {missing}")

    df = df.copy()

    # Preserve the original row order.
    df["_ecodata_row_order"] = np.arange(len(df))

    # Parse timestamps.
    df["timestamp"] = parse_movement_timestamps(df["timestamp"])

    n_missing = df["timestamp"].isna().sum()

    if n_missing > 0:
        print(f"[INFO] {n_missing} rows with missing or invalid " "timestamps were removed before interpolation.")

    df = df.dropna(subset=["timestamp"]).copy()

    # Convert coordinates to numeric values.
    for coord in ("location_lat", "location_lon"):
        df[coord] = pd.to_numeric(df[coord], errors="coerce")

    groups = []

    # Interpolate each animal independently.
    for individual_id, group in df.groupby("individual_local_identifier", sort=False):
        group = group.copy()
        group = group.sort_values("timestamp")
        group = group.set_index("timestamp")
        group[["location_lat", "location_lon"]] = group[["location_lat", "location_lon"]].interpolate(
            method="time", limit_direction="both"
        )
        group = group.reset_index()
        groups.append(group)

    if not groups:
        return df.drop(columns=["_ecodata_row_order"], errors="ignore").reset_index(drop=True)

    result = pd.concat(groups, ignore_index=True)
    # Restore original row order.
    result = result.sort_values("_ecodata_row_order").drop(columns=["_ecodata_row_order"]).reset_index(drop=True)
    print("[INFO] Missing coordinates were interpolated " "separately for each individual ID.")

    return result


def load_selected_environmental_data(
    df,
    env_var_map,
    selected_vars,
    movebank_path,
    interpolation_method="Nearest neighbour",
    smoothing_k: int = 2,
    coord_spec=None,
    env_coord_names: dict | None = None,
    continuous_vars=None,
    categorical_vars=None,
    dataset_descriptor: dict | None = None,
):
    """
    Route annotation to the sampler required by the validated grid geometry.

    Grid types
    ----------
    geographic_rectilinear
        Independent 1D latitude and longitude coordinates. Existing nearest
        and IDW implementations are preserved.
    projected_rectilinear
        Independent 1D projected x/y coordinates with a CF grid mapping.
        Existing projected bilinear implementation is preserved.
    curvilinear_geographic
        Two-dimensional latitude/longitude coordinates on logical y/x
        dimensions. Nearest and IDW use a spherical KDTree.

    Variable semantics
    ------------------
    Continuous variables use linear temporal interpolation. Categorical/QC
    variables use nearest-time sampling and are never spatially averaged.
    """
    method = (interpolation_method or "").strip().lower()
    method = method.replace("neighbor", "neighbour")
    is_nearest = "nearest" in method
    is_idw = ("idw" in method) or ("inverse distance" in method)
    is_bilinear = "bilinear" in method
    descriptor = dict(dataset_descriptor or {})
    grid_type = str(descriptor.get("grid_type") or "").strip().lower()
    source_time_name = descriptor.get("source_time_name") or descriptor.get("time_name")

    # Accept the stable ECODATA key plus a few harmless legacy aliases.
    is_curvilinear_grid = grid_type in {"curvilinear_geographic", "geographic_curvilinear", "curvilinear"}
    cont = list(continuous_vars or [])
    cat = list(categorical_vars or [])

    # ------------------------------------------------------------------
    # Curvilinear geographic: dedicated 2D lat/lon sampler.
    # ------------------------------------------------------------------
    if is_curvilinear_grid:
        if is_bilinear:
            raise ValueError(
                "Bilinear interpolation is not available for a general curvilinear "
                "latitude/longitude grid. Use Nearest or IDW."
            )

        # Backward-compatible fallback when no explicit variable split was supplied.
        if not cont and not cat:
            if is_nearest:
                return annotate_env_nearest_curvilinear(
                    df,
                    env_var_map,
                    selected_vars,
                    movebank_path,
                    dataset_descriptor=descriptor,
                    temporal_method="linear",
                )
            if is_idw:
                return annotate_env_IDW_curvilinear(
                    df,
                    env_var_map,
                    selected_vars,
                    movebank_path,
                    smoothing_k=smoothing_k,
                    dataset_descriptor=descriptor,
                    temporal_method="linear",
                )
            raise ValueError(f"Unknown interpolation method: {interpolation_method}")

        out_df = df
        nc_start = pd.NaT
        nc_end = pd.NaT
        if is_nearest:
            if cont:
                out_df, nc_start, nc_end = annotate_env_nearest_curvilinear(
                    out_df, env_var_map, cont, movebank_path, dataset_descriptor=descriptor, temporal_method="linear"
                )
            if cat:
                out_df, nc_start2, nc_end2 = annotate_env_nearest_curvilinear(
                    out_df, env_var_map, cat, movebank_path, dataset_descriptor=descriptor, temporal_method="nearest"
                )
                if pd.isna(nc_start) and not pd.isna(nc_start2):
                    nc_start = nc_start2
                if pd.isna(nc_end) and not pd.isna(nc_end2):
                    nc_end = nc_end2
            return out_df, nc_start, nc_end

        if is_idw:
            if cont:
                out_df, nc_start, nc_end = annotate_env_IDW_curvilinear(
                    out_df,
                    env_var_map,
                    cont,
                    movebank_path,
                    smoothing_k=smoothing_k,
                    dataset_descriptor=descriptor,
                    temporal_method="linear",
                )
            # Category/flag codes must not be averaged. Use one nearest node and
            # the nearest native timestep even when IDW was selected globally.
            if cat:
                out_df, nc_start2, nc_end2 = annotate_env_nearest_curvilinear(
                    out_df, env_var_map, cat, movebank_path, dataset_descriptor=descriptor, temporal_method="nearest"
                )
                if pd.isna(nc_start) and not pd.isna(nc_start2):
                    nc_start = nc_start2
                if pd.isna(nc_end) and not pd.isna(nc_end2):
                    nc_end = nc_end2
            return out_df, nc_start, nc_end

        raise ValueError(f"Unknown interpolation method: {interpolation_method}")

    # 
    # Existing regular/projected behaviour below is intentionally retained.
    # 

    if not cont and not cat:
        if is_nearest:
            return annotate_env_nearest(
                df,
                env_var_map,
                selected_vars,
                movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                source_time_name=source_time_name,
            )

        if is_idw:
            return annotate_env_IDW(
                df,
                env_var_map,
                selected_vars,
                movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                source_time_name=source_time_name,
            )

        if is_bilinear:
            return annotate_env_bilinear_projected(
                df,
                env_var_map,
                selected_vars,
                movebank_path,
                env_coord_names=env_coord_names,
                dataset_descriptor=descriptor,
            )

        raise ValueError(f"Unknown interpolation method: {interpolation_method}")

    if is_nearest:
        out_df = df
        nc_start = pd.NaT
        nc_end = pd.NaT

        if cont:
            out_df, nc_start, nc_end = annotate_env_nearest(
                out_df,
                env_var_map,
                cont,
                movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                temporal_method="linear",
                source_time_name=source_time_name,
            )

        if cat:
            out_df, nc_start2, nc_end2 = annotate_env_nearest(
                out_df,
                env_var_map,
                cat,
                movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                temporal_method="nearest",
                source_time_name=source_time_name,
            )
            if pd.isna(nc_start) and not pd.isna(nc_start2):
                nc_start = nc_start2
            if pd.isna(nc_end) and not pd.isna(nc_end2):
                nc_end = nc_end2

        return out_df, nc_start, nc_end

    if is_idw:
        out_df = df
        nc_start = pd.NaT
        nc_end = pd.NaT

        if cont:
            out_df, nc_start, nc_end = annotate_env_IDW(
                out_df,
                env_var_map,
                cont,
                movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                temporal_method="linear",
                source_time_name=source_time_name,
            )

        if cat:
            out_df, nc_start2, nc_end2 = annotate_env_nearest(
                out_df,
                env_var_map,
                cat,
                movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                temporal_method="nearest",
                source_time_name=source_time_name,
            )
            if pd.isna(nc_start) and not pd.isna(nc_start2):
                nc_start = nc_start2
            if pd.isna(nc_end) and not pd.isna(nc_end2):
                nc_end = nc_end2

        return out_df, nc_start, nc_end

    if is_bilinear:
        if cat:
            raise ValueError(
                "Bilinear projected interpolation is only valid for continuous variables. "
                "Please remove categorical/QC variables or use Nearest/IDW mode."
            )

        bilinear_vars = cont if cont else list(selected_vars or [])
        if not bilinear_vars:
            raise ValueError("No continuous variables selected for bilinear projected interpolation.")

        return annotate_env_bilinear_projected(
            df,
            env_var_map,
            bilinear_vars,
            movebank_path,
            env_coord_names=env_coord_names,
            dataset_descriptor=descriptor,
        )

    raise ValueError(f"Unknown interpolation method: {interpolation_method}")


def load_selected_environmental_data_batched(
    df,
    env_var_map,
    selected_vars,
    movebank_path,
    interpolation_method="Nearest neighbour",
    smoothing_k: int = 2,
    coord_spec=None,
    env_coord_names: dict | None = None,
    continuous_vars=None,
    categorical_vars=None,
    dataset_descriptor: dict | None = None,
):
    """
    Multi-file wrapper around the existing single-batch
    environmental annotation router.

    Important:
    - does NOT concatenate NetCDF datasets;
    - each temporal batch is processed independently;
    - existing sampler functions remain unchanged;
    - batch CSV files are temporary and automatically removed;
    - final result is reconstructed in original movement-row order.
    """

    selected_vars = list(selected_vars or [])

    continuous_vars = list(continuous_vars or [])

    categorical_vars = list(categorical_vars or [])

    # 
    # 1. Preserve old single-NC behaviour exactly.
    # 

    has_multifile_source = any(
        len(normalize_env_source_paths(env_var_map.get(variable))) > 1 for variable in selected_vars
    )

    if not has_multifile_source:

        return load_selected_environmental_data(
            df,
            env_var_map,
            selected_vars,
            movebank_path,
            interpolation_method,
            smoothing_k=smoothing_k,
            coord_spec=coord_spec,
            env_coord_names=env_coord_names,
            continuous_vars=continuous_vars,
            categorical_vars=categorical_vars,
            dataset_descriptor=dataset_descriptor,
        )

    descriptor = dict(dataset_descriptor or {})
    source_time_name = (
        descriptor.get("source_time_name") or descriptor.get("time_name") or ((env_coord_names or {}).get("env_time"))
    )

    # 
    # 2. Build temporal batches.
    # 
    batches = build_environmental_time_batches(env_var_map, selected_vars, time_name=source_time_name)

    if not batches:
        return (df.copy(), pd.NaT, pd.NaT)

    # 
    # 3. Stable row key allows batch CSVs to be reconstructed
    #    into the original movement-row order.
    # 
    base_df = df.copy()
    base_df["_ecodata_batch_row"] = np.arange(len(base_df), dtype="int64")
    base_df["timestamp"] = parse_movement_timestamps(base_df["timestamp"])
    batch_start_union = min(batch["start"] for batch in batches)
    batch_end_union = max(batch["end"] for batch in batches)
    covered_row_ids = set()

    # 
    # 4. Internal temporary CSVs.
    # 
    with tempfile.TemporaryDirectory(prefix="ecodata_nc_batches_") as temp_dir:
        temp_dir = Path(temp_dir)
        batch_csv_paths = []
        for batch in batches:
            batch_number = batch["number"]
            batch_start = batch["start"]
            batch_end = batch["end"]
            batch_map = batch["env_var_map"]
            mask = base_df["timestamp"].between(batch_start, batch_end, inclusive="both")
            batch_df = base_df.loc[mask].copy()
            if batch_df.empty:
                print(f"[INFO] Batch {batch_number}: " "no movement rows in this time range; skipped.")
                continue

            print(
                f"[INFO] Processing batch "
                f"{batch_number}/{len(batches)}: "
                f"{batch_start} -> {batch_end}; "
                f"rows={len(batch_df)}"
            )

            result = load_selected_environmental_data(
                batch_df,
                batch_map,
                selected_vars,
                movebank_path,
                interpolation_method,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                env_coord_names=env_coord_names,
                continuous_vars=continuous_vars,
                categorical_vars=categorical_vars,
                dataset_descriptor=descriptor,
            )

            if result is None:
                raise RuntimeError(f"Environmental annotation failed " f"for batch {batch_number}.")

            batch_annotated, _, _ = result

            if "_ecodata_batch_row" not in batch_annotated.columns:
                raise RuntimeError("Internal batch row identifier was lost " "during environmental annotation.")

            covered_row_ids.update(
                pd.to_numeric(batch_annotated["_ecodata_batch_row"], errors="coerce").dropna().astype("int64").tolist()
            )
            batch_csv = temp_dir / f"batch_{batch_number:04d}.csv"
            batch_annotated.to_csv(batch_csv, index=False, encoding="utf-8", date_format="%Y-%m-%d %H:%M:%S")
            batch_csv_paths.append(batch_csv)
            print(f"[INFO] Temporary batch CSV written: " f"{batch_csv.name}")

            # release the batch DataFrame reference.
            del batch_annotated
            del batch_df
            gc.collect()

        # 
        # 5. Read completed batches and concatenate them.
        # 
        parts = []

        for batch_csv in batch_csv_paths:
            part = pd.read_csv(batch_csv)
            if "timestamp" in part.columns:
                part["timestamp"] = parse_movement_timestamps(part["timestamp"])

            parts.append(part)

        if parts:
            combined = pd.concat(parts, ignore_index=True, sort=False)

        else:
            combined = base_df.iloc[0:0].copy()

    # 
    # 6. Preserve movement rows not covered by any batch.
    #
    # This keeps the existing ECODATA semantics:
    # timestamps without available environmental coverage remain
    # in the result and environmental columns are NaN.
    # 
    uncovered = base_df[~base_df["_ecodata_batch_row"].isin(covered_row_ids)].copy()

    for variable in selected_vars:
        if variable not in uncovered.columns:
            uncovered[variable] = np.nan

    if not uncovered.empty:

        combined = pd.concat([combined, uncovered], ignore_index=True, sort=False)

    # 
    # 7. Restore original row order.
    # 

    combined["_ecodata_batch_row"] = pd.to_numeric(combined["_ecodata_batch_row"], errors="coerce")
    combined = (
        combined.sort_values("_ecodata_batch_row")
        .drop(columns=["_ecodata_batch_row"], errors="ignore")
        .reset_index(drop=True)
    )
    combined["timestamp"] = parse_movement_timestamps(combined["timestamp"])
    print(
        "[INFO] Environmental batch annotation complete: " f"{len(batches)} batch(es), " f"{len(combined)} output rows."
    )
    return (combined, batch_start_union, batch_end_union)


def standardize_time_lat_lon(ds, coord_spec):
    mapping = {}
    if coord_spec:
        for std in ("time", "lat", "lon"):
            chosen = coord_spec.get(std)
            if chosen and chosen in ds.variables and chosen != std:
                mapping[chosen] = std

    if mapping:
        ds = ds.rename(mapping)

    for req in ("time", "lat", "lon"):
        if req not in ds.variables:
            raise ValueError(
                f"Missing required '{req}' variable after user selection. "
                f"Selected: {coord_spec}. Available: {list(ds.variables.keys())}"
            )
    return ds


def annotate_env_nearest(
    df,
    env_var_map,
    selected_vars,
    movebank_path,
    smoothing_k: int = 4,
    coord_spec=None,
    temporal_method: str = "linear",
    source_time_name=None,
):
    """
    Annotate movement points with environmental values using:
     - Spatial: nearest grid node
     - Temporal:
      * "linear"  -> vectorised linear interpolation in time, for continuous variables
      * "nearest" -> nearest available timestep, for categorical/QC variables

    This version supports "expanded" variable labels that include a pressure/vertical level,
    e.g. "v_1000", "v_975", ... For such labels, the base variable ("v") is taken from the
    NetCDF, and the closest level to the requested value (e.g. 1000 hPa) is selected along
    the appropriate vertical dimension (e.g. isobaricInhPa/level/lev/plev/...).

    Parameters
    ----------
    df : pandas.DataFrame
        Movebank-like table with columns: timestamp, location_lat, location_lon, etc.
    env_var_map : dict[str, str]
        Mapping from UI label to NetCDF path, e.g. {"v_1000": "/path/file.nc"}.
    selected_vars : list[str]
        Labels picked in the UI; labels may be plain vars ("t2m") or var+level ("v_850").
    movebank_path : str
        Used only for output file placement upstream in the pipeline.
    smoothing_k : int
        Unused in the nearest-neighbour branch (kept for signature symmetry).

    Returns
    -------
    (out_df, nc_start, nc_end)
        `out_df` includes new columns for each selected label; nc_* are placeholders here.

    Notes
    -----
    - Assumes `safe_open_nc_with_time_decoding` and `_ensure_sorted` are available in scope.
    - Column names in the result exactly match `selected_vars` (e.g. "v_1000").
    """

    def _nearest_indices_vectorized(arr, vals):
        """
        Fast nearest-index for a (monotonic) 1D array `arr`
        against multiple query values `vals` (vectorised).
        """
        idx = np.searchsorted(arr, vals)
        idx = np.clip(idx, 0, len(arr) - 1)
        left = np.maximum(idx - 1, 0)
        take_left = (idx > 0) & (np.abs(arr[left] - vals) <= np.abs(arr[idx] - vals))
        return np.where(take_left, left, idx)

    def _nearest_longitude_indices_vectorized(arr, vals):
        """
        Nearest longitude index on a periodic [-180, 180) axis.

        This correctly handles the antimeridian, where for example
        179.9° and -180° are geographically adjacent.
        """
        arr = np.asarray(arr, dtype="float64")
        vals = normalize_longitude_values(vals)
        idx = np.searchsorted(arr, vals)
        right = idx % len(arr)
        left = (idx - 1) % len(arr)
        dist_left = np.abs(((arr[left] - vals + 180.0) % 360.0) - 180.0)
        dist_right = np.abs(((arr[right] - vals + 180.0) % 360.0) - 180.0)
        return np.where(dist_left <= dist_right, left, right)

    # input prep
    out = df.copy()
    out["timestamp"] = parse_movement_timestamps(out["timestamp"])
    out = out.dropna(subset=["timestamp", "location_lat", "location_lon"])
    temporal_method = (temporal_method or "linear").strip().lower()
    if temporal_method not in ("linear", "nearest"):
        temporal_method = "linear"

    # Placeholders for nearest grid coords (one set; overwritten by last variable)
    nc_latitudes = np.full(len(out), np.nan, dtype="float64")
    nc_longitudes = np.full(len(out), np.nan, dtype="float64")

    # Target times as int64 ns, used for either np.interp or nearest-time lookup.
    tgt_times = out["timestamp"].to_numpy("datetime64[ns]").astype("int64")

    #  main loop over requested labels 
    for label in selected_vars:
        file_path = env_var_map.get(label)
        if temporal_method == "nearest":
            # Categorical/QC-safe column: preserve integer codes or labels if present.
            out[label] = pd.Series([pd.NA] * len(out), index=out.index, dtype="object")
        else:
            out[label] = np.nan  # continuous numeric column
        if not file_path or not Path(file_path).is_file():
            print(f"[WARNING] File for {label} not found: {file_path}")
            continue

        # Split the UI label into (base_var, requested_level)
        base_var, target_level = _split_var_and_level(label)
        ds = None
        try:
            ds = safe_open_nc_with_time_decoding(file_path, time_name=source_time_name)
            ds = standardize_time_lat_lon(ds, coord_spec)
            if base_var not in ds:
                print(f"[WARNING] Base variable '{base_var}' not found in {file_path}")
                ds.close()
                continue

            da = ds[base_var]
            dims = list(da.dims)

            # Detect lat/lon names
            lat_dim = "lat" if "lat" in dims else "latitude"
            lon_dim = "lon" if "lon" in dims else "longitude"

            # 
            # Normalize regular-grid longitude to ECODATA convention:
            # [-180, 180)
            #
            # Nearest-neighbour uses np.searchsorted(), so longitude
            # must also remain sorted after normalization.
            # 
            lon_values = np.asarray(ds[lon_dim].values, dtype="float64")

            lon_normalized = normalize_longitude_values(lon_values)

            # Only rebuild/sort the longitude coordinate when needed.
            if not np.allclose(lon_values, lon_normalized, equal_nan=True):
                lon_attrs = dict(ds[lon_dim].attrs)
                lon_coord_dim = ds[lon_dim].dims[0]
                ds = ds.assign_coords({lon_dim: (lon_coord_dim, lon_normalized)})
                ds[lon_dim].attrs.update(lon_attrs)

                # xarray reorders environmental data together
                # with the longitude coordinate.
                ds = ds.sortby(lon_dim)

            # Keep latitude sorted as before.
            ds = _ensure_sorted(ds, lat_dim, lon_dim)
            da = ds[base_var]
            dims = list(da.dims)

            # Unify/ensure time dimension is named 'time'
            time_dim = (
                "time"
                if "time" in dims
                else next(
                    (d for d in ("valid_time", "forecast_time", "verification_time", "t", "Time") if d in dims), None
                )
            )
            if time_dim is None:
                ds.close()
                raise ValueError(f"No time-like dimension in '{base_var}': dims={dims}")
            if time_dim != "time":
                ds = ds.rename({time_dim: "time"})
                da = ds[base_var]
                dims = list(da.dims)

            # Resolve extra dimensions (pressure level, ensemble, expver, etc.)
            # For the "level" dim: pick closest to `target_level` (or 1000 hPa by default).
            extra = [d for d in dims if d not in ("time", lat_dim, lon_dim)]
            if extra:
                sel = {}
                for d in extra:
                    if d in LEVEL_DIM_CANDIDATES:
                        sel[d] = _pick_level_index(ds, d, target_level)
                    else:
                        sel[d] = 0  # deterministic default for non-level extra dims
                da = da.isel(**sel).squeeze()  # now expected shape: (time, lat, lon)

            # Grid coordinate vectors
            glat = ds[lat_dim].values
            glon = ds[lon_dim].values
            gtime = pd.to_datetime(ds["time"].values).to_numpy("datetime64[ns]").astype("int64")
            # Vectorised nearest grid-node indices for all points
            lat_idx = _nearest_indices_vectorized(glat, out["location_lat"].to_numpy(dtype="float64"))
            lon_idx = _nearest_longitude_indices_vectorized(glon, out["location_lon"].to_numpy(dtype="float64"))

            # Store the matched grid coordinates (useful for QA)
            nc_latitudes[:] = glat[lat_idx]
            nc_longitudes[:] = glon[lon_idx]

            # Group points by grid cell (to read each per-cell time series only once)
            cell_code = (lat_idx.astype(np.int64) * len(glon)) + lon_idx.astype(np.int64)
            unique_cells, inverse = np.unique(cell_code, return_inverse=True)

            # Cache of per-cell series: (ii, jj) -> 1D array over time.
            #  For continuous variables this is float64; for categorical/QC variables the original dtype is preserved.
            series_cache: dict[tuple[int, int], np.ndarray] = {}
            col_idx = out.columns.get_loc(label)

            for g, code in enumerate(unique_cells):
                ii = int(code // len(glon))
                jj = int(code % len(glon))

                pos = np.nonzero(inverse == g)[0]  # row indices in `out` for this cell
                xi = tgt_times[pos]  # target times (int64 ns)

                key = (ii, jj)
                if key not in series_cache:
                    raw_series = da.isel({lat_dim: ii, lon_dim: jj}).values

                    if temporal_method == "nearest":
                        # Keep original dtype for categorical/QC variables.
                        # This avoids converting category codes to float and also supports non-numeric labels.
                        series_cache[key] = raw_series
                    else:
                        # Continuous variables: cast to float64 for np.interp.
                        series_cache[key] = raw_series.astype("float64")

                y = series_cache[key]

                if temporal_method == "nearest":
                    # Categorical/QC-safe temporal sampling:
                    # take the value from the nearest available timestep, no interpolation.
                    m = pd.notna(y)
                    if m.sum() < 1:
                        out.iloc[pos, col_idx] = np.nan
                        continue

                    x = gtime[m]  # source times, int64 ns
                    yy = y[m]  # source values, may be integer/category codes

                    # Ensure time is sorted
                    order = np.argsort(x)
                    x = x[order]
                    yy = yy[order]
                    idx = np.searchsorted(x, xi)
                    right = np.clip(idx, 0, len(x) - 1)
                    left = np.clip(idx - 1, 0, len(x) - 1)
                    use_left = (idx > 0) & ((idx == len(x)) | (np.abs(xi - x[left]) <= np.abs(x[right] - xi)))
                    nearest_idx = np.where(use_left, left, right)
                    vals = yy[nearest_idx]

                    # Keep existing "no extrapolation" behaviour:
                    # points outside the native NC time range remain NaN.
                    vals = vals.astype("object")
                    vals[(xi < x.min()) | (xi > x.max())] = np.nan
                    out.iloc[pos, col_idx] = vals

                else:
                    # Continuous variables: existing linear temporal interpolation.
                    y_float = y.astype("float64")
                    m = np.isfinite(y_float)
                    if m.sum() < 2:
                        out.iloc[pos, col_idx] = np.nan
                        continue

                    x = gtime[m]
                    yy = y_float[m]
                    order = np.argsort(x)
                    x = x[order]
                    yy = yy[order]
                    vals = np.interp(xi, x, yy)

                    # Outside native time range → NaN
                    vals[(xi < x.min()) | (xi > x.max())] = np.nan
                    out.iloc[pos, col_idx] = vals

        except Exception as e:
            print(f"[ERROR] {label}: {e}")
            continue

        finally:
            if ds is not None:
                try:
                    ds.close()
                except Exception:
                    pass

    # Final QA columns
    out["nc_lat"] = nc_latitudes
    out["nc_lon"] = nc_longitudes
    out["geometry"] = [Point(lon, lat) for lon, lat in zip(out["nc_lon"], out["nc_lat"])]

    # Harmonise return signature with the rest of your pipeline
    return out, pd.NaT, pd.NaT


def annotate_env_IDW(
    df,
    env_var_map,
    selected_vars,
    movebank_path,
    smoothing_k: int = 2,
    coord_spec=None,
    temporal_method: str = "linear",
    source_time_name=None,
):
    """
    Annotate movement points with environmental values using:
    - Spatial: Inverse Distance Weighting (IDW) over k nearest grid nodes
    - Temporal:
        * "linear"  -> 1D linear interpolation in time per grid node
        * "nearest" -> nearest available timestep per grid node

    Important:
    IDW is suitable for continuous numeric variables. Even with temporal_method="nearest",
    spatial IDW still averages values across neighbouring grid nodes, so it is not
    recommended for true categorical/QC variables.

    This version understands expanded variable labels that include a pressure/vertical level,
    e.g. "v_1000", "v_975". It will:
      1) parse the UI label into (base_var, target_level),
      2) find a known vertical dimension (isobaricInhPa/level/lev/plev/...),
      3) slice the DataArray to the closest level to `target_level` (or 1000 hPa by default).

    Parameters
    ----------
    df : pandas.DataFrame
        Movebank-like table with columns: timestamp, location_lat, location_lon, etc.
    env_var_map : dict[str, str]
        Mapping from UI label to NetCDF path, e.g. {"v_1000": "/path/file.nc"}.
    selected_vars : list[str]
        Labels picked in the UI; each label becomes a column in the output.
    movebank_path : str
        Kept for signature symmetry with the rest of the pipeline (output path handled upstream).
    smoothing_k : int
        Number of nearest grid nodes for IDW (>=2).

    Returns
    -------
    (out_df, nc_start, nc_end)
        `out_df` contains new columns with the same names as `selected_vars`.
        `nc_start`, `nc_end` are placeholders here (NaT).
    """
    # input prep 
    k = max(2, int(smoothing_k))
    out = df.copy()
    out["timestamp"] = parse_movement_timestamps(out["timestamp"])
    out = out.dropna(subset=["timestamp", "location_lat", "location_lon"])

    temporal_method = (temporal_method or "linear").strip().lower()
    if temporal_method not in ("linear", "nearest"):
        temporal_method = "linear"

    # Keep nc_lat/nc_lon semantics consistent with prior implementation (copy of point coords)
    out["nc_lat"] = out["location_lat"].values
    out["nc_lon"] = out["location_lon"].values

    # Vectorised numeric targets for temporal interpolation
    tgt_times = out["timestamp"].to_numpy("datetime64[ns]").astype("int64")
    lat_vals = out["location_lat"].to_numpy(dtype="float64")
    lon_vals = out["location_lon"].to_numpy(dtype="float64")

    # Cache spherical spatial indexes for regular geographic grids.
    # The same grid may be reused by several variables / pressure levels.
    regular_grid_index_cache = {}

    # main loop over labels 
    for label in selected_vars:
        file_path = env_var_map.get(label)
        if temporal_method == "nearest":
            # Nearest-time mode: preserve raw values before spatial handling.
            # Note: spatial IDW is still numeric and is not recommended for true categorical/QC variables.
            out[label] = pd.Series([pd.NA] * len(out), index=out.index, dtype="object")
        else:
            out[label] = np.nan  # continuous numeric column

        if not file_path or not Path(file_path).is_file():
            print(f"[WARNING] File for {label} not found: {file_path}")
            continue

        # Split label into base variable and optional requested level
        base_var, target_level = _split_var_and_level(label)

        ds = None

        try:
            ds = safe_open_nc_with_time_decoding(file_path, time_name=source_time_name)
            ds = standardize_time_lat_lon(ds, coord_spec)
            if base_var not in ds:
                print(f"[WARNING] Base variable '{base_var}' not in {file_path}")
                ds.close()
                continue

            da = ds[base_var]
            dims = list(da.dims)

            # Detect coordinate names and sort dataset (required by nearest/k-nearest search)
            lat_dim = "lat" if "lat" in dims else "latitude"
            lon_dim = "lon" if "lon" in dims else "longitude"
            ds = _ensure_sorted(ds, lat_dim, lon_dim)
            da = ds[base_var]
            dims = list(da.dims)

            # Unify time dimension name to 'time'
            time_dim = (
                "time"
                if "time" in dims
                else next(
                    (d for d in ("valid_time", "forecast_time", "verification_time", "t", "Time") if d in dims), None
                )
            )
            if time_dim is None:
                ds.close()
                raise ValueError(f"No time-like dimension in '{base_var}': dims={dims}")
            if time_dim != "time":
                ds = ds.rename({time_dim: "time"})
                da = ds[base_var]
                dims = list(da.dims)

            # Resolve extra dimensions (pressure level, ensemble, expver, etc.)
            extra_dims = [d for d in dims if d not in ("time", lat_dim, lon_dim)]
            if extra_dims:
                sel = {}
                for d in extra_dims:
                    if d in LEVEL_DIM_CANDIDATES:
                        sel[d] = _pick_level_index(ds, d, target_level)
                    else:
                        sel[d] = 0  # deterministic default for non-level dims
                da = da.isel(**sel).squeeze()  # -> (time, lat, lon)

            # Coordinate vectors
            glat = np.asarray(ds[lat_dim].values, dtype="float64")

            glon = np.asarray(ds[lon_dim].values, dtype="float64")

            gtime_int = pd.to_datetime(ds["time"].values).to_numpy("datetime64[ns]").astype("int64")

            # 
            # Build/reuse a spherical spatial index for the regular
            # latitude/longitude grid.
            #
            # Coordinates remain in degrees in the dataset and output.
            # Only neighbour search and IDW distances use spherical
            # great-circle geometry.
            # 
            grid_cache_key = (str(Path(file_path).resolve()), lat_dim, lon_dim, len(glat), len(glon))

            if grid_cache_key not in regular_grid_index_cache:
                lon2d, lat2d = np.meshgrid(glon, glat)
                regular_grid_index_cache[grid_cache_key] = _build_curvilinear_spatial_index(lat2d, lon2d)

            spatial_index = regular_grid_index_cache[grid_cache_key]

            # Query all animal locations at once.
            # Returned distances are great-circle distances in km.
            neighbor_positions, neighbor_distances_km = _query_curvilinear_spatial_index(
                spatial_index, lon_vals, lat_vals, k=k
            )

            # Cache per-grid-node time series
            series_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
            col_idx = out.columns.get_loc(label)

            # Row-wise IDW over k nearest grid nodes
            for i in range(len(out)):
                t_i = tgt_times[i]

                # If outside the native time span → keep NaN
                if t_i < gtime_int.min() or t_i > gtime_int.max():
                    continue

                # Neighbours were already selected using spherical geometry.
                # Distances are great-circle distances in kilometres.
                positions_i = neighbor_positions[i]
                dists = np.asarray(neighbor_distances_km[i], dtype="float64")
                vals = np.empty(len(positions_i), dtype="float64")

                for j, position in enumerate(positions_i):
                    position = int(position)
                    # Convert flattened spherical-index position
                    # back to the original regular-grid indices.
                    ii = int(spatial_index["iy"][position])
                    jj = int(spatial_index["ix"][position])
                    key = (ii, jj)

                    if key not in series_cache:
                        raw_y = da.isel({lat_dim: ii, lon_dim: jj}).values

                        if temporal_method == "nearest":
                            m = pd.notna(raw_y)

                            if m.sum() >= 1:
                                x = gtime_int[m]
                                yy = raw_y[m]
                                order = np.argsort(x)
                                x = x[order]
                                yy = yy[order]

                            else:
                                x = np.empty(0, dtype="int64")
                                yy = np.empty(0, dtype=raw_y.dtype)

                        else:
                            y = raw_y.astype("float64")
                            m = np.isfinite(y)

                            if m.sum() >= 2:
                                x = gtime_int[m]
                                yy = y[m]
                                order = np.argsort(x)
                                x = x[order]
                                yy = yy[order]

                            else:
                                x = np.empty(0, dtype="int64")
                                yy = np.empty(0, dtype="float64")

                        series_cache[key] = (x, yy)

                    x, yy = series_cache[key]

                    if temporal_method == "nearest":

                        if x.size < 1:
                            vals[j] = np.nan

                        else:
                            idx = np.searchsorted(x, t_i)
                            right = np.clip(idx, 0, len(x) - 1)
                            left = np.clip(idx - 1, 0, len(x) - 1)
                            use_left = (idx > 0) and ((idx == len(x)) or (abs(t_i - x[left]) <= abs(x[right] - t_i)))
                            nearest_idx = left if use_left else right
                            v = yy[nearest_idx]
                            if t_i < x.min() or t_i > x.max():
                                v = np.nan

                            vals[j] = v

                    else:
                        if x.size < 2:
                            vals[j] = np.nan

                        else:
                            v = np.interp(t_i, x, yy)
                            if t_i < x.min() or t_i > x.max():
                                v = np.nan

                            vals[j] = v

                out.iloc[i, col_idx] = _idw(vals, dists, p=2)

        except Exception as e:
            print(f"[ERROR] {label}: {e}")
            continue

        finally:
            if ds is not None:
                try:
                    ds.close()
                except Exception:
                    pass

    # Geometry for QA/exports
    out["geometry"] = [Point(lon, lat) for lon, lat in zip(out["nc_lon"], out["nc_lat"])]
    return out, pd.NaT, pd.NaT


def _lonlat_to_unit_xyz(lon, lat):
    """Convert longitude/latitude in degrees to Cartesian unit-sphere vectors."""
    lon = np.deg2rad(np.asarray(lon, dtype="float64"))
    lat = np.deg2rad(np.asarray(lat, dtype="float64"))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def _chord_to_great_circle_km(chord_distance):
    """Convert unit-sphere chord distance to great-circle distance in kilometres."""
    chord = np.asarray(chord_distance, dtype="float64")
    half = np.clip(chord / 2.0, 0.0, 1.0)
    return 6371.0088 * (2.0 * np.arcsin(half))


def _build_curvilinear_spatial_index(lat2d, lon2d):
    """
    Build a spatial index for valid nodes of a 2D geographic grid.
    Returns a dictionary containing flattened valid y/x indices, coordinates,
    Cartesian unit vectors, and an optional scipy cKDTree.
    """
    lat2d = np.asarray(lat2d, dtype="float64")

    lon2d = normalize_longitude_values(lon2d)

    if lat2d.ndim != 2 or lon2d.ndim != 2:
        raise ValueError("Curvilinear sampling requires two-dimensional latitude and longitude arrays.")
    if lat2d.shape != lon2d.shape:
        raise ValueError(f"Curvilinear latitude/longitude shapes differ: {lat2d.shape} vs {lon2d.shape}.")

    valid = np.isfinite(lat2d) & np.isfinite(lon2d)
    valid &= (lat2d >= -90.0) & (lat2d <= 90.0)
    if not valid.any():
        raise ValueError("Curvilinear grid has no valid latitude/longitude nodes.")

    iy, ix = np.nonzero(valid)
    flat_lat = lat2d[iy, ix]
    flat_lon = lon2d[iy, ix]
    xyz = _lonlat_to_unit_xyz(flat_lon, flat_lat)
    tree = cKDTree(xyz) if cKDTree is not None else None

    return {
        "iy": iy.astype("int64"),
        "ix": ix.astype("int64"),
        "lat": flat_lat,
        "lon": flat_lon,
        "xyz": xyz,
        "tree": tree,
    }


def _query_curvilinear_spatial_index(index, query_lon, query_lat, k=1):
    """
    Query k nearest curvilinear nodes for each lon/lat point.

    scipy's cKDTree is used when available. A chunked NumPy fallback avoids a
    hard scipy dependency, although it is slower for very large grids.
    """
    query_xyz = _lonlat_to_unit_xyz(query_lon, query_lat)
    n_nodes = len(index["iy"])
    k_eff = max(1, min(int(k), n_nodes))

    if index.get("tree") is not None:
        distances, positions = index["tree"].query(query_xyz, k=k_eff)
    else:
        positions_rows = []
        distances_rows = []
        grid_xyz = index["xyz"]
        for q in query_xyz:
            d = np.sqrt(np.sum((grid_xyz - q) ** 2, axis=1))
            if k_eff == 1:
                pos = np.array([int(np.argmin(d))], dtype="int64")
            else:
                pos = np.argpartition(d, k_eff - 1)[:k_eff]
                pos = pos[np.argsort(d[pos])]
            positions_rows.append(pos)
            distances_rows.append(d[pos])
        positions = np.asarray(positions_rows)
        distances = np.asarray(distances_rows)

    distances = np.asarray(distances, dtype="float64")
    positions = np.asarray(positions, dtype="int64")
    if distances.ndim == 1:
        distances = distances[:, None]
        positions = positions[:, None]

    return positions, _chord_to_great_circle_km(distances)


def _resolve_curvilinear_layout(ds, da, dataset_descriptor):
    """Resolve time, y, x, and 2D latitude/longitude names for a data variable."""
    descriptor = dict(dataset_descriptor or {})
    time_name = descriptor.get("time_name") or "time"
    lat_name = descriptor.get("lat_name") or "lat"
    lon_name = descriptor.get("lon_name") or "lon"

    if time_name not in ds.variables and time_name not in ds.coords:
        detected = _detect_time_name(ds)
        if detected is None:
            raise ValueError("No time coordinate found for curvilinear dataset.")
        time_name = detected

    if time_name != "time":
        ds = ds.rename({time_name: "time"})
        time_name = "time"
        da = ds[da.name]

    if lat_name not in ds.variables or lon_name not in ds.variables:
        raise ValueError(f"Curvilinear coordinates not found: lat={lat_name!r}, lon={lon_name!r}.")

    lat_da = ds[lat_name]
    lon_da = ds[lon_name]
    if lat_da.ndim != 2 or lon_da.ndim != 2 or lat_da.dims != lon_da.dims:
        raise ValueError("Curvilinear latitude and longitude must be 2D and use identical y/x dimensions.")

    inferred_y, inferred_x = lat_da.dims
    y_dim = descriptor.get("y_name") or inferred_y
    x_dim = descriptor.get("x_name") or inferred_x

    if y_dim not in da.dims or x_dim not in da.dims:
        # Descriptor x/y are logical dimension names. Fall back to the actual
        # dimensions carried by the 2D latitude/longitude variables.
        y_dim, x_dim = inferred_y, inferred_x

    if "time" not in da.dims:
        raise ValueError(f"Variable {da.name!r} has no time dimension: {da.dims}")
    if y_dim not in da.dims or x_dim not in da.dims:
        raise ValueError(f"Variable {da.name!r} does not use curvilinear dimensions " f"{y_dim!r}/{x_dim!r}: {da.dims}")

    return ds, da, "time", y_dim, x_dim, lat_name, lon_name


def _slice_curvilinear_extra_dims(ds, da, time_dim, y_dim, x_dim, target_level):
    """Select requested vertical level and deterministic first index for legacy extras."""
    extra_dims = [d for d in da.dims if d not in (time_dim, y_dim, x_dim)]
    if extra_dims:
        selectors = {}
        for dim in extra_dims:
            if dim in LEVEL_DIM_CANDIDATES:
                selectors[dim] = _pick_level_index(ds, dim, target_level)
            else:
                # Preserve the established backend behaviour for raw legacy
                # files. NCBuilder-standardized files should normally have no
                # unresolved extra dimensions.
                selectors[dim] = 0
        da = da.isel(selectors, drop=True)

    missing = [d for d in (time_dim, y_dim, x_dim) if d not in da.dims]
    if missing:
        raise ValueError(f"Variable {da.name!r} lost required dimensions after slicing: {missing}.")
    return da.transpose(time_dim, y_dim, x_dim)


def _prepare_time_axis(ds):
    """Return sorted int64-nanosecond time values and the corresponding order."""
    values = pd.to_datetime(ds["time"].values).to_numpy("datetime64[ns]").astype("int64")
    order = np.argsort(values)
    return values[order], order


def _sample_time_series_at_targets(source_times, source_values, target_times, temporal_method):
    """Sample one node's time series without extrapolation."""
    temporal_method = str(temporal_method or "linear").lower()
    y = np.asarray(source_values)

    if temporal_method == "nearest":
        valid = pd.notna(y)
        if valid.sum() < 1:
            return np.full(len(target_times), np.nan, dtype="object")
        x = source_times[valid]
        yy = y[valid]
        order = np.argsort(x)
        x = x[order]
        yy = yy[order]

        idx = np.searchsorted(x, target_times)
        right = np.clip(idx, 0, len(x) - 1)
        left = np.clip(idx - 1, 0, len(x) - 1)
        choose_left = (idx > 0) & (
            (idx == len(x)) | (np.abs(target_times - x[left]) <= np.abs(x[right] - target_times))
        )
        nearest = np.where(choose_left, left, right)
        result = np.asarray(yy[nearest], dtype="object")
        result[(target_times < x.min()) | (target_times > x.max())] = np.nan
        return result

    y_float = np.asarray(y, dtype="float64")
    valid = np.isfinite(y_float)
    if valid.sum() < 2:
        return np.full(len(target_times), np.nan, dtype="float64")
    x = source_times[valid]
    yy = y_float[valid]
    order = np.argsort(x)
    x = x[order]
    yy = yy[order]
    result = np.interp(target_times, x, yy)
    result[(target_times < x.min()) | (target_times > x.max())] = np.nan
    return result


def _annotate_env_curvilinear(
    df,
    env_var_map,
    selected_vars,
    movebank_path,
    spatial_method="nearest",
    smoothing_k=4,
    dataset_descriptor=None,
    temporal_method="linear",
):
    """Shared implementation for nearest and IDW on a 2D geographic grid."""
    spatial_method = str(spatial_method or "nearest").lower()
    if spatial_method not in {"nearest", "idw"}:
        raise ValueError(f"Unsupported curvilinear spatial method: {spatial_method}")
    if temporal_method == "nearest" and spatial_method == "idw":
        raise ValueError("Categorical/QC values cannot be IDW-averaged.")

    out = df.copy()
    out["timestamp"] = parse_movement_timestamps(out["timestamp"])
    out = out.dropna(subset=["timestamp", "location_lat", "location_lon"])

    target_times = out["timestamp"].to_numpy("datetime64[ns]").astype("int64")
    target_lat = pd.to_numeric(out["location_lat"], errors="coerce").to_numpy("float64")
    target_lon = pd.to_numeric(out["location_lon"], errors="coerce").to_numpy("float64")
    valid_targets = np.isfinite(target_lat) & np.isfinite(target_lon)

    # QA columns reflect the nearest actual grid node, including in IDW mode.
    out["nc_lat"] = np.nan
    out["nc_lon"] = np.nan

    descriptor = dict(dataset_descriptor or {})
    grid_index_cache = {}

    for label in selected_vars:
        file_path = env_var_map.get(label)
        if temporal_method == "nearest":
            out[label] = pd.Series([pd.NA] * len(out), index=out.index, dtype="object")
        else:
            out[label] = np.nan

        if not file_path or not Path(file_path).is_file():
            print(f"[WARNING] File for {label} not found: {file_path}")
            continue

        base_var, target_level = _split_var_and_level(label)
        ds = None
        try:
            ds = safe_open_nc_with_time_decoding(
                file_path, time_name=(descriptor.get("source_time_name") or descriptor.get("time_name"))
            )
            if base_var not in ds:
                print(f"[WARNING] Base variable '{base_var}' not found in {file_path}")
                continue

            da = ds[base_var]
            ds, da, time_dim, y_dim, x_dim, lat_name, lon_name = _resolve_curvilinear_layout(ds, da, descriptor)
            da = _slice_curvilinear_extra_dims(ds, da, time_dim, y_dim, x_dim, target_level)

            cache_key = (str(Path(file_path).resolve()), lat_name, lon_name, tuple(ds[lat_name].shape))
            if cache_key not in grid_index_cache:
                grid_index_cache[cache_key] = _build_curvilinear_spatial_index(ds[lat_name].values, ds[lon_name].values)
            spatial_index = grid_index_cache[cache_key]

            k = 1 if spatial_method == "nearest" else max(2, int(smoothing_k))
            positions = np.zeros((len(out), k), dtype="int64")
            distances_km = np.full((len(out), k), np.nan, dtype="float64")
            if valid_targets.any():
                pos_valid, dist_valid = _query_curvilinear_spatial_index(
                    spatial_index, target_lon[valid_targets], target_lat[valid_targets], k=k
                )
                k_actual = pos_valid.shape[1]
                if k_actual != k:
                    positions = np.zeros((len(out), k_actual), dtype="int64")
                    distances_km = np.full((len(out), k_actual), np.nan, dtype="float64")
                    k = k_actual
                positions[valid_targets, :] = pos_valid
                distances_km[valid_targets, :] = dist_valid

            nearest_pos = positions[:, 0]
            nearest_lat = spatial_index["lat"][nearest_pos]
            nearest_lon = spatial_index["lon"][nearest_pos]
            nearest_lat[~valid_targets] = np.nan
            nearest_lon[~valid_targets] = np.nan
            out["nc_lat"] = nearest_lat
            out["nc_lon"] = nearest_lon

            source_times, time_order = _prepare_time_axis(ds)
            if not np.array_equal(time_order, np.arange(len(time_order))):
                da = da.isel({time_dim: time_order})

            # Cache temporally sampled values per native node. Values are
            # computed only for nodes actually required by the track points.
            node_cache = {}
            sampled_neighbors = np.full((len(out), k), np.nan, dtype="float64")
            if temporal_method == "nearest" and spatial_method == "nearest":
                sampled_object = np.full(len(out), np.nan, dtype="object")

            for neighbour in range(k):
                node_positions = positions[:, neighbour]
                for pos in np.unique(node_positions[valid_targets]):
                    rows = np.where(valid_targets & (node_positions == pos))[0]
                    pos_int = int(pos)
                    iy = int(spatial_index["iy"][pos_int])
                    ix = int(spatial_index["ix"][pos_int])
                    cache_node_key = (iy, ix, temporal_method)
                    if cache_node_key not in node_cache:
                        series = da.isel({y_dim: iy, x_dim: ix}).values
                        node_cache[cache_node_key] = _sample_time_series_at_targets(
                            source_times, series, target_times, temporal_method
                        )
                    node_values = node_cache[cache_node_key]
                    if temporal_method == "nearest" and spatial_method == "nearest":
                        sampled_object[rows] = node_values[rows]
                    else:
                        sampled_neighbors[rows, neighbour] = np.asarray(node_values[rows], dtype="float64")

            if spatial_method == "nearest":
                if temporal_method == "nearest":
                    out[label] = sampled_object
                else:
                    out[label] = sampled_neighbors[:, 0]
            else:
                result = np.full(len(out), np.nan, dtype="float64")
                for row in np.where(valid_targets)[0]:
                    result[row] = _idw(sampled_neighbors[row, :], distances_km[row, :], p=2)
                out[label] = result

        except Exception as exc:
            print(f"[ERROR] {label}: {exc}")
            continue
        finally:
            if ds is not None:
                try:
                    ds.close()
                except Exception:
                    pass

    out["geometry"] = [
        Point(lon, lat) if np.isfinite(lat) and np.isfinite(lon) else None
        for lon, lat in zip(out["nc_lon"], out["nc_lat"])
    ]
    return out, pd.NaT, pd.NaT


def annotate_env_nearest_curvilinear(
    df, env_var_map, selected_vars, movebank_path, dataset_descriptor=None, temporal_method="linear"
):
    """Nearest native node on a curvilinear 2D latitude/longitude grid."""
    return _annotate_env_curvilinear(
        df,
        env_var_map,
        selected_vars,
        movebank_path,
        spatial_method="nearest",
        smoothing_k=1,
        dataset_descriptor=dataset_descriptor,
        temporal_method=temporal_method,
    )


def annotate_env_IDW_curvilinear(
    df, env_var_map, selected_vars, movebank_path, smoothing_k=4, dataset_descriptor=None, temporal_method="linear"
):
    """Spherical k-nearest-node IDW on a curvilinear geographic grid."""
    return _annotate_env_curvilinear(
        df,
        env_var_map,
        selected_vars,
        movebank_path,
        spatial_method="idw",
        smoothing_k=smoothing_k,
        dataset_descriptor=dataset_descriptor,
        temporal_method=temporal_method,
    )


def _coordinate_unit_scale_to_native(coord_da) -> float:
    """Return divisor for projected coordinates produced by pyproj (metres)."""
    units = str(getattr(coord_da, "attrs", {}).get("units", "")).strip().lower()
    if units in {"km", "kilometer", "kilometers", "kilometre", "kilometres"}:
        return 1000.0
    return 1.0


def _validate_projected_points(x_pts, y_pts, gx, gy):
    """Return a mask for points inside the native projected grid extent."""
    x_min, x_max = float(np.nanmin(gx)), float(np.nanmax(gx))
    y_min, y_max = float(np.nanmin(gy)), float(np.nanmax(gy))
    return (x_pts >= x_min) & (x_pts <= x_max) & (y_pts >= y_min) & (y_pts <= y_max)


def annotate_env_bilinear_projected(
    df,
    env_var_map,
    selected_vars,
    movebank_path,
    env_coord_names: dict | None = None,
    dataset_descriptor: dict | None = None,
):
    """
    Annotate movement points with environmental values using:
      - Spatial: bilinear interpolation on a 1D projected grid (x/y)
      - Temporal: linear interpolation in time (xarray interp)

    Tracks input:
      - requires lon/lat columns: location_lon, location_lat
      - projects lon/lat -> x/y into the env dataset's native CRS using CF metadata

    Env input:
      - dataset has 1D x and y coordinate vectors (projected grid)
      - dataset provides CF projection metadata so `read_crs_from_cf()` can infer CRS

    Returns: (out_df, pd.NaT, pd.NaT) for signature compatibility.
    """
    out = df.copy()
    out["timestamp"] = parse_movement_timestamps(out["timestamp"])

    # Require lon/lat (your code already normalizes movement columns sometimes)
    required = ["timestamp", "location_lat", "location_lon"]
    out = out.dropna(subset=required)

    env_coord_names = env_coord_names or {}
    dataset_descriptor = dict(dataset_descriptor or {})
    time_name = (
        dataset_descriptor.get("source_time_name")
        or dataset_descriptor.get("time_name")
        or env_coord_names.get("env_time")
    )
    x_name = dataset_descriptor.get("x_name") or env_coord_names.get("env_x")
    y_name = dataset_descriptor.get("y_name") or env_coord_names.get("env_y")

    if not x_name or not y_name:
        raise ValueError(
            "Bilinear (projected) requires env_coord_names['env_x'] and ['env_y'] " "(Projected (x/y) mode)."
        )
    if env_coord_names.get("env_lat") or env_coord_names.get("env_lon"):
        raise ValueError("Bilinear (projected) requires Projected (x/y) spatial mode, not Geographic (lat/lon).")

    # Target time values (vectorized)
    tgt_t = out["timestamp"].to_numpy("datetime64[ns]")

    # Track lon/lat arrays
    lon = pd.to_numeric(out["location_lon"], errors="coerce").to_numpy(dtype="float64")
    lat = pd.to_numeric(out["location_lat"], errors="coerce").to_numpy(dtype="float64")

    # Drop any rows with bad numeric lon/lat
    good = np.isfinite(lon) & np.isfinite(lat) & out["timestamp"].notna().to_numpy()
    if not good.all():
        out = out.loc[good].copy()
        tgt_t = tgt_t[good]
        lon = lon[good]
        lat = lat[good]

    # QA columns
    out["x"] = np.nan
    out["y"] = np.nan

    # Cache CRS/transformer per file path (since you may have multiple labels/files)
    crs_cache: dict[str, "CRS"] = {}

    for label in selected_vars:
        file_path = env_var_map.get(label)
        out[label] = np.nan

        if not file_path or not Path(file_path).is_file():
            print(f"[WARNING] File for {label} not found: {file_path}")
            continue

        base_var, target_level = _split_var_and_level(label)
        ds = None
        try:
            ds = safe_open_nc_with_time_decoding(file_path, time_name=time_name)

            if base_var not in ds:
                print(f"[WARNING] Base variable '{base_var}' not found in {file_path}")
                ds.close()
                continue

            da = ds[base_var]
            dims = list(da.dims)

            # Must be able to interpolate along x/y dims
            x_dim = x_name if x_name in dims else None
            y_dim = y_name if y_name in dims else None
            if x_dim is None or y_dim is None:
                ds.close()
                raise ValueError(
                    f"Bilinear requires x/y to be dims of {base_var!r}.\n"
                    f"  Requested x dim: {x_name!r} (is_dim={x_name in dims})\n"
                    f"  Requested y dim: {y_name!r} (is_dim={y_name in dims})\n"
                    f"  Available dims: {dims}"
                )

            # Sort for interpolation stability
            ds = _ensure_sorted(ds, y_dim, x_dim)
            da = ds[base_var]
            dims = list(da.dims)

            if "time" not in dims:
                ds.close()
                raise ValueError(f"No 'time' dim after decoding for '{base_var}'. dims={dims}")

            # Validate 1D x/y coordinate vectors
            gx = np.asarray(ds[x_dim].values)
            gy = np.asarray(ds[y_dim].values)
            if gx.ndim != 1 or gy.ndim != 1:
                ds.close()
                raise ValueError(
                    f"Bilinear method requires 1D coordinate vectors for '{y_dim}' and '{x_dim}'. "
                    f"Got shapes: {y_dim}={gy.shape}, {x_dim}={gx.shape}."
                )

            # Handle extra dims (pressure level, ensemble, expver, etc.)
            extra_dims = [d for d in dims if d not in ("time", y_dim, x_dim)]
            if extra_dims:
                sel = {}
                for d in extra_dims:
                    if d in LEVEL_DIM_CANDIDATES:
                        sel[d] = _pick_level_index(ds, d, target_level)
                    else:
                        sel[d] = 0
                da = da.isel(**sel).squeeze()  # -> (time, y, x)

            # --- CRS inference + projection lon/lat -> x/y -------------------------
            if file_path not in crs_cache:
                # Prefer variable-specific grid_mapping lookup by passing base_var
                crs_cache[file_path] = read_crs_from_cf(
                    ds, var_name=base_var, preferred_grid_mapping=dataset_descriptor.get("grid_mapping_name")
                )

            target_crs = crs_cache[file_path]
            x_pts, y_pts = project_tracks_lonlat_to_xy(lon, lat, target_crs=target_crs)

            # pyproj normally returns metres. Convert to the native x/y units used by
            # the NetCDF grid (NARR commonly stores projected coordinates in km).
            x_pts = x_pts / _coordinate_unit_scale_to_native(ds[x_dim])
            y_pts = y_pts / _coordinate_unit_scale_to_native(ds[y_dim])

            inside_grid = _validate_projected_points(x_pts, y_pts, gx, gy)
            print(
                f"[INFO] Projected points inside grid for '{label}': " f"{int(inside_grid.sum())} / {len(inside_grid)}"
            )

            # Store for QA
            out["x"] = x_pts
            out["y"] = y_pts

            # --- vectorized xarray interpolation -----------------------------------
            pts = xr.Dataset(
                coords={"points": np.arange(len(out))},
                data_vars={"time": ("points", tgt_t), x_dim: ("points", x_pts), y_dim: ("points", y_pts)},
            )

            sampled = da.interp({x_dim: pts[x_dim], y_dim: pts[y_dim], "time": pts["time"]})
            sampled_values = np.asarray(sampled.to_numpy(), dtype="float64")
            sampled_values[~inside_grid] = np.nan
            out[label] = sampled_values

        except Exception as e:
            print(f"[ERROR] {label}: {e}")
            continue

        finally:
            if ds is not None:
                try:
                    ds.close()
                except Exception:
                    pass

    # If you want: geometry in projected CRS (x,y). Comment out if not needed.
    out["geometry"] = [Point(x, y) for x, y in zip(out["x"], out["y"])]

    return out, pd.NaT, pd.NaT


def read_crs_from_cf(ds: xr.Dataset, var_name: str | None = None, preferred_grid_mapping: str | None = None) -> CRS:
    """
    Infer the projected coordinate reference system (CRS) of a gridded
    environmental dataset using CF-convention metadata.

    The function attempts, in order:
    1) to read a CF-compliant ``grid_mapping`` attribute from a data variable,
    2) to construct a CRS from global dataset attributes (e.g. WKT or PROJ),
    3) to read CRS information from a standalone ``crs`` variable.

    This is intended for datasets on projected grids (e.g. NARR, ERA5-Land,
    regional climate models) where track data in WGS84 lon/lat must be
    transformed to native x/y coordinates before spatial interpolation.

    Parameters
    ----------
    ds : xarray.Dataset
        Environmental dataset containing projected horizontal coordinates
        and CF-compliant projection metadata.
    var_name : str or None, optional
        Name of a data variable whose ``grid_mapping`` attribute should be
        inspected first. If None, variable-specific metadata are skipped.

    Returns
    -------
    pyproj.CRS
        Coordinate reference system describing the dataset's native
        horizontal projection.

    Raises
    ------
    ValueError
        If no usable CRS information can be inferred from the dataset.
    """

    # 1) Prefer the grid mapping already validated by the selected adapter.
    grid_mapping_name = preferred_grid_mapping
    if not grid_mapping_name and var_name is not None and var_name in ds:
        grid_mapping_name = ds[var_name].attrs.get("grid_mapping")

    # 2) If we have a grid mapping variable, parse it as CF
    if grid_mapping_name and grid_mapping_name in ds.variables:
        gm = ds[grid_mapping_name]
        # xarray keeps attrs as dict; pyproj can build CRS from CF dict
        try:
            return CRS.from_cf(gm.attrs)
        except Exception:
            pass

    # 3) Known/likely grid-mapping variables. NARR files often expose
    # Lambert_Conformal as a standalone scalar variable even when the selected
    # data variable has no explicit grid_mapping attribute.
    mapping_candidates = ["Lambert_Conformal", "lambert_conformal_conic", "crs", "spatial_ref", "projection"]
    for name in mapping_candidates:
        if name in ds.variables:
            attrs = dict(ds[name].attrs or {})
            try:
                return CRS.from_cf(attrs)
            except Exception:
                pass
            for key in ("crs_wkt", "spatial_ref"):
                wkt = attrs.get(key)
                if isinstance(wkt, str) and wkt.strip():
                    return CRS.from_wkt(wkt)

    # 4) Common alternate places: global attrs
    # Try "crs_wkt", "spatial_ref" (GDAL), "proj4", "proj"
    for key in ("crs_wkt", "spatial_ref", "proj_wkt", "wkt"):
        wkt = ds.attrs.get(key)
        if isinstance(wkt, str) and wkt.strip():
            return CRS.from_wkt(wkt)

    for key in ("proj4", "proj4text", "proj", "projection"):
        proj = ds.attrs.get(key)
        if isinstance(proj, str) and proj.strip():
            return CRS.from_string(proj)

    # 4) Sometimes there is a standalone "crs" variable with WKT in attrs
    if "crs" in ds.variables:
        crs_var = ds["crs"]
        for key in ("crs_wkt", "spatial_ref"):
            wkt = crs_var.attrs.get(key)
            if isinstance(wkt, str) and wkt.strip():
                return CRS.from_wkt(wkt)
        # Or CF attrs
        try:
            return CRS.from_cf(crs_var.attrs)
        except Exception:
            pass

    # Last-resort NARR fallback. This is used only when the file clearly exposes
    # the conventional NARR Lambert_Conformal variable but incomplete CF attrs.
    if "Lambert_Conformal" in ds.variables:
        attrs = dict(ds["Lambert_Conformal"].attrs or {})
        try:
            lat_1 = float(
                attrs.get("standard_parallel", [50.0, 50.0])[0]
                if isinstance(attrs.get("standard_parallel"), (list, tuple, np.ndarray))
                else attrs.get("standard_parallel", 50.0)
            )
            lat_2_raw = attrs.get("standard_parallel", [50.0, 50.0])
            lat_2 = float(
                lat_2_raw[1] if isinstance(lat_2_raw, (list, tuple, np.ndarray)) and len(lat_2_raw) > 1 else lat_1
            )
            lat_0 = float(attrs.get("latitude_of_projection_origin", 50.0))
            lon_0 = float(
                attrs.get("longitude_of_central_meridian", attrs.get("longitude_of_projection_origin", -107.0))
            )
            return CRS.from_proj4(
                f"+proj=lcc +lat_1={lat_1} +lat_2={lat_2} +lat_0={lat_0} "
                f"+lon_0={lon_0} +datum=WGS84 +units=m +no_defs"
            )
        except Exception:
            pass

    raise ValueError("Could not infer CRS from dataset (no usable CF grid_mapping / WKT / proj string found).")


def project_tracks_lonlat_to_xy(lon: np.ndarray, lat: np.ndarray, target_crs: CRS) -> tuple[np.ndarray, np.ndarray]:
    """
    Project track locations from geographic coordinates (longitude, latitude)
    to the native x/y coordinate system of a projected environmental grid.

    This function is used to transform animal tracking locations
    (WGS84 lon/lat) into the coordinate system of gridded datasets such as
    NARR before spatial interpolation using xarray.

    Parameters
    ----------
    lon : array-like
        Longitudes of track locations in degrees east (EPSG:4326).
    lat : array-like
        Latitudes of track locations in degrees north (EPSG:4326).
    target_crs : pyproj.CRS
        Target projected CRS describing the environmental dataset grid.

    Returns
    -------
    x : numpy.ndarray
        Projected x-coordinates of track locations in the target CRS.
    y : numpy.ndarray
        Projected y-coordinates of track locations in the target CRS.
    """

    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)

    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    x, y = transformer.transform(lon, lat)
    return np.asarray(x, dtype=float), np.asarray(y, dtype=float)


def _safe_remove_existing_file(path, retries: int = 5, delay: float = 0.5):
    """
    Remove an existing file before overwriting it.

    This is mainly needed on Windows, where NetCDF files can remain locked
    for a short time after being opened by xarray/netCDF4/h5netcdf.
    """
    path = Path(path)

    if not path.exists():
        return

    last_error = None

    for _ in range(retries):
        try:
            gc.collect()
            path.unlink()
            return
        except PermissionError as e:
            last_error = e
            time.sleep(delay)

    raise PermissionError(
        f"Could not remove existing file because it is still locked: {path}. "
        f"Close any open dataset/viewer using this file and try again. "
        f"Original error: {last_error}"
    )


def convert_tif_to_nc_before_annotation(tif_paths, output_dir):
    """
    Converts a list of .tif files into a single NetCDF, creating a separate DataArray per variable.
    For each variable, builds a data(time, lat, lon) array.
    Returns the path to the generated .nc file.
    """
    tif_paths = [str(Path(p)) for p in tif_paths]
    if not tif_paths:
        raise ValueError("No .tif files provided")

    # 1) Group files by variable
    by_var = {}
    for tif in tif_paths:
        vname = parse_appeears_variable_name(tif)
        by_var.setdefault(vname, []).append(tif)

    lat = lon = None
    data_vars = {}

    for vname, files in by_var.items():
        times = []
        planes = []
        first_geo = True

        for tif in sorted(files):
            tif_name = Path(tif).name
            t = parse_time_from_filename(tif_name)
            times.append(t)

            with rasterio.open(tif) as src:
                arr = src.read(1).astype("float32")
                nodata = src.nodata
                if nodata is not None:
                    arr = np.where(arr == nodata, np.nan, arr)

                # IMPORTANT:
                # Do not apply scale_factor / add_offset during TIF -> NetCDF conversion.
                # The NetCDF stores raw raster values.
                #
                # Optional scale/offset correction is applied later after sampling,
                # and only to user-selected continuous variables.
                #
                # This avoids corrupting categorical/QC layers such as masks, flags,
                # land-cover classes, or quality codes.

                planes.append(arr)

                if first_geo:
                    transform = src.transform
                    h, w = src.height, src.width
                    lon = np.array([transform * (i, 0) for i in range(w)])[:, 0]
                    lat = np.array([transform * (0, j) for j in range(h)])[:, 1]
                    first_geo = False

        data_array = np.stack(planes)  # (time, lat, lon)
        time_index = np.array(times)

        da = xr.DataArray(
            data_array, dims=["time", "lat", "lon"], coords={"time": time_index, "lat": lat, "lon": lon}, name=vname
        )
        data_vars[vname] = da

    ds = xr.Dataset(data_vars)
    base = Path(tif_paths[0]).name.split("_")[0]
    safe_base = re.sub(r"[^\w\-]", "_", base)
    out = Path(output_dir) / f"{safe_base}_nc_output.nc"
    _safe_remove_existing_file(out)

    try:
        ds.to_netcdf(out)
    finally:
        try:
            ds.close()
        except Exception:
            pass

    return str(out)


def parse_time_from_filename(filename):
    """
    Example: MOD13A1.061__500m_16_days_NDVI_doy2014145000000_aid0001.tif
    Parses date using "doyYYYYDDD", where DDD is the day of year.
    """
    match = re.search(r"doy(\d{4})(\d{3})", filename)
    if match:
        year, doy = int(match.group(1)), int(match.group(2))
        return datetime.strptime(f"{year}{doy}", "%Y%j")
    else:
        raise ValueError(f"Cannot parse time from filename: {filename}")


# --- AppEEARS variable-name parser --- #
def parse_appeears_variable_name(tif_path: str) -> str:
    """
    Returns the variable/layer name for an AppEEARS GeoTIFF.
    Order:
    (A) try reading tags (long_name, DESCRIPTION, Layer...)
    (B) if not available — parse the filename:
        - token before 'doyYYYYDDD' (typical: ..._NDVI_doy2014145_...)
        - or one of the known tokens in KNOWN_TOKENS
    (C) fallback -> "data"
    """
    p = Path(tif_path)
    name = p.name

    # A) read TIF tags
    try:
        with rasterio.open(tif_path) as src:
            tags = src.tags()
            for key in ("long_name", "DESCRIPTION", "Description", "Layer", "LAYER", "BAND_NAME"):
                if key in tags and str(tags[key]).strip():
                    raw = str(tags[key]).strip()
                    var = re.sub(r"[^\w\-]+", "_", raw)
                    return var
    except Exception:
        pass

    # B1) token before "doyYYYYDDD"
    m = re.search(r"_([A-Za-z0-9][A-Za-z0-9_]+)_doy\d{7}", name)
    if m:
        return m.group(1)

    # B2) known tokens (common AppEEARS layers; list is incomplete but useful)
    KNOWN_TOKENS = {
        "NDVI",
        "EVI",
        "LST_Day_1km",
        "LST_Night_1km",
        "LST_Day_1KM",
        "LST_Night_1KM",
        "QC_Day",
        "QC_Night",
        "Lai_500m",
        "Fpar_500m",
        "FparLai_QC",
        "Nadir_Reflectance_Band1",
        "Nadir_Reflectance_Band2",
        "Nadir_Reflectance_Band3",
        "Nadir_Reflectance_Band4",
        "Nadir_Reflectance_Band5",
        "Nadir_Reflectance_Band6",
        "Nadir_Reflectance_Band7",
        "SurfReflect_Band1",
        "SurfReflect_Band2",
        "SurfReflect_Band3",
        "SurfReflect_Band4",
        "SurfReflect_Band5",
        "SurfReflect_Band6",
        "SurfReflect_Band7",
        "NDSI_Snow_Cover",
        "VIIRS_NDVI",
        "VIIRS_EVI",
        "BurnDate",
        "BurnDate_Uncertainty",
        "LAI",
        "FPAR",
        "QC",
    }
    candidates = sorted([t for t in KNOWN_TOKENS if t in name], key=len, reverse=True)
    if candidates:
        return candidates[0]

    parts = re.split(r"[_.]", name)
    parts = [t for t in parts if t and t.lower() != "tif"]
    parts = [t for t in parts if not t.lower().startswith("aid")]
    parts = [t for t in parts if not re.fullmatch(r"\d{7,8}", t) and not t.startswith("doy")]
    if parts:
        parts.sort(key=len, reverse=True)
        return parts[0]

    return "data"


def _ensure_sorted(ds, lat_dim, lon_dim):
    if (np.diff(ds[lat_dim].values) < 0).all():
        ds = ds.sortby(lat_dim)
    if (np.diff(ds[lon_dim].values) < 0).all():
        ds = ds.sortby(lon_dim)
    return ds


def _nearest_index(arr, x):
    # array arr growing: fast via searchsorted + local check
    idx = np.searchsorted(arr, x)
    if idx == 0:
        return 0
    if idx >= len(arr):
        return len(arr) - 1
    return idx if abs(arr[idx] - x) < abs(arr[idx - 1] - x) else idx - 1


def _idw(values, distances, p=2):
    """IDW average for already interpolated values. distances > 0 (add eps)."""
    vals = np.array(values, dtype=float)
    d = np.array(distances, dtype=float) + 1e-12
    w = 1.0 / (d**p)
    # ignore NaN in vals
    mask = ~np.isnan(vals)
    if not mask.any():
        return np.nan
    w_sel = w[mask]
    v_sel = vals[mask]
    return np.sum(w_sel * v_sel) / np.sum(w_sel)


def detect_time_name(ds, preferred=None):
    """
    Detect the source time coordinate/variable in a NetCDF dataset.

    Priority:
    1. Explicitly requested name, if it exists.
    2. CF standard_name='time'.
    3. CF axis='T'.
    4. Common time variable names.
    5. CF-style units containing 'since'.

    Returns the ORIGINAL variable name from the source dataset.
    """

    # 1. Explicitly requested variable
    if preferred and (preferred in ds.coords or preferred in ds.variables):
        return preferred

    # 2. CF standard_name
    for name, var in ds.variables.items():
        standard_name = str(var.attrs.get("standard_name", "")).strip().lower()

        if standard_name == "time":
            return name

    # 3. CF axis
    for name, var in ds.variables.items():
        axis = str(var.attrs.get("axis", "")).strip().upper()

        if axis == "T":
            return name

    # 4. Common names
    name_candidates = (
        "time",
        "valid_time",
        "forecast_time",
        "verification_time",
        "forecast_reference_time",
        "initial_time",
        "analysis_time",
        "datetime",
        "date",
        "Time",
        "TIME",
        "t",
        "XTIME",
        "time_counter",
    )

    for name in name_candidates:
        if name in ds.coords or name in ds.variables:
            return name

    # 5. CF time units
    for name, var in ds.variables.items():
        units = str(var.attrs.get("units", "")).strip().lower()

        if "since" in units:
            return name

    return None


def _detect_time_name(ds):
    """
    Backward-compatible private alias.
    """
    return detect_time_name(ds)


def _split_var_and_level(label: str):
    """
    If the name is in the format <var>_<level>, returns ('var', target_level_float).
    Otherwise ('label', None).
    """
    m = re.match(r"^([A-Za-z_]\w*)_(\d{2,4})$", str(label))
    if m:
        base = m.group(1)
        try:
            lvl = float(m.group(2))
        except Exception:
            lvl = None
        return base, lvl
    return label, None


def _pick_level_index(ds, level_dim: str, target_level: float | None):
    """
    Return the index of the pressure level closest to target_level.
    ECODATA pressure-level labels are expressed in hPa
    (for example: temperature_850).

    If the native NetCDF pressure coordinate is stored in Pa,
    the requested hPa value is converted to Pa before matching.
    """

    try:
        vals = np.asarray(ds[level_dim].values, dtype=float)

        if vals.size == 0:
            return 0

        # ECODATA/UI convention: pressure levels are expressed in hPa.
        ref_hpa = 1000.0 if target_level is None else float(target_level)
        units = str(ds[level_dim].attrs.get("units", "")).strip().lower()

        # Convert requested hPa value to the native coordinate units.
        if units in ("pa", "pascal", "pascals"):
            ref_native = ref_hpa * 100.0
        else:
            # hPa, mb, mbar, millibar, or datasets without explicit units:
            # preserve the established behaviour.
            ref_native = ref_hpa

        return int(np.nanargmin(np.abs(vals - ref_native)))

    except Exception:
        return 0
