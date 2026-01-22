import xarray as xr
import geopandas as gpd
from pathlib import Path
import pandas as pd
import re
from shapely.geometry import Point
import numpy as np
from datetime import datetime
import rasterio

LEVEL_DIM_CANDIDATES = ("isobaricInhPa","isobaric_in_hPa","level","lev","plev","pressure","pressure_level")


def open_nc_metadata(path: str) -> xr.Dataset:
    """
    Open a NetCDF dataset for metadata inspection only.

    Notes
    -----
    - This function is intended for UI/metadata purposes (listing variables/coords/dims).
    - It does not decode time and should avoid heavy computation.

    Parameters
    ----------
    path : str
        Path to a NetCDF file.

    Returns
    -------
    xarray.Dataset
        Opened dataset (time not decoded).
    """
    # decode_times=False prevents CF time decoding and avoids cftime edge cases during UI inspection
    return xr.open_dataset(path, decode_times=False, chunks="auto")


def detect_env_coord_names(ds: xr.Dataset) -> dict:
    """
    Detect coordinate names for an environmental dataset.

    Parameters
    ----------
    ds : xarray.Dataset
        Environmental dataset.

    Returns
    -------
    dict
        Dictionary with keys: 'env_time', 'env_x', 'env_y', 'env_lat', 'env_lon'.
        Values may be None if not detected.
    """

    # time
    env_time = _detect_time_name(ds)

    # projected axes
    x_candidates = ["x", "X", "projection_x_coordinate", "eastings", "easting"]
    y_candidates = ["y", "Y", "projection_y_coordinate", "northings", "northing"]

    env_x = next((c for c in x_candidates if c in ds.coords and c in ds.dims), None)
    env_y = next((c for c in y_candidates if c in ds.coords and c in ds.dims), None)

    # geographic coords (can be 1D or 2D)
    lat_candidates = ["lat", "latitude", "Latitude"]
    lon_candidates = ["lon", "longitude", "long", "Longitude"]

    env_lat = next((c for c in lat_candidates if c in ds.coords or c in ds.variables), None)
    env_lon = next((c for c in lon_candidates if c in ds.coords or c in ds.variables), None)

    return {
        "env_time": env_time,
        "env_x": env_x,
        "env_y": env_y,
        "env_lat": env_lat,
        "env_lon": env_lon,
    }


def safe_open_nc_with_time_decoding(path, time_name: str | None = None):
    """
    Opens a NetCDF file with support for non-standard calendars:
    julian, gregorian, 360_day, noleap, etc.
    Always returns the 'time' coordinate as a pd.DatetimeIndex,
    even if it was originally of cftime type.
    """

    try:
        ds = xr.open_dataset(path, decode_times=False, chunks="auto")

        if time_name is None:
            time_name = _detect_time_name(ds)
        if time_name is None:
            raise ValueError("No time-like coordinate/variable found (e.g., 'time', 'valid_time').")


        # if time is in variables but not in coords — make it a coordinate
        if time_name in ds.variables and time_name not in ds.coords:
            ds = ds.set_coords(time_name)

        time_var = ds[time_name]
        units = str(time_var.attrs.get("units",""))
        calendar = str(time_var.attrs.get("calendar","standard")).lower()

        if "since" not in units:
            # sometimes there are "epoch seconds" without 'since'
            # add default: seconds since 1970-01-01
            if units.strip() == "" and pd.api.types.is_integer_dtype(time_var.dtype):
                units = "seconds since 1970-01-01"
                calendar = "proleptic_gregorian"

        decoded = xr.coding.times.decode_cf_datetime(time_var.values, units, calendar)
       # if these are cftime objects — convert via str
        if hasattr(decoded[0], "strftime"):
            decoded = pd.to_datetime([str(d) for d in decoded])
        else:
            decoded = pd.to_datetime(decoded)

       # rename the time coordinate to the unified 'time'
        if time_name != "time":
            ds = ds.assign_coords({time_name: decoded}).rename({time_name: "time"})
        else:
            ds = ds.assign_coords(time=decoded)

        return ds

    except Exception as e:
       raise RuntimeError(f"[ERROR] Failed to decode time using cftime for {path}: {e}")


def get_nc_timerange_for_selected(env_var_map: dict, selected_env_vars: list[str], time_name: str | None = None):
    """
    Return union [nc_start, nc_end] across all selected variables.
    If time is missing for all → (None, None).
    """

    nc_start, nc_end = None, None
    for v in (selected_env_vars or []):
        nc_path = env_var_map.get(v)
        if not nc_path:
            continue
        ds = safe_open_nc_with_time_decoding(nc_path, time_name=time_name)
        try:
            if ("time" in ds.coords) or ("time" in ds.variables):
                tmin = pd.to_datetime(ds["time"].values.min())
                tmax = pd.to_datetime(ds["time"].values.max())
                nc_start = tmin if (nc_start is None or tmin < nc_start) else nc_start
                nc_end   = tmax if (nc_end   is None or tmax > nc_end)   else nc_end
        finally:
            ds.close()
    return nc_start, nc_end


def get_nc_bounds(nc_path: str, env_coord_names: dict | None = None):
    """
    Returns a dictionary of boundaries from .nc in CRS WGS84: {"S": ..., "N": ..., "W": ..., "E": ...}
    """
    env_coord_names = env_coord_names or {}
    time_name = env_coord_names.get("env_time")
    lat_name  = env_coord_names.get("env_lat")
    lon_name  = env_coord_names.get("env_lon")

    ds = safe_open_nc_with_time_decoding(nc_path)
    try:
        if lat_name is None or lon_name is None:
            raise ValueError("Could not determine lat/lon bounds.")

        lat_min = float(ds[lat_name].min())
        lat_max = float(ds[lat_name].max())
        lon_min = float(ds[lon_name].min())
        lon_max = float(ds[lon_name].max())
        return {"S": lat_min, "N": lat_max, "W": lon_min, "E": lon_max}
    finally:
        ds.close()


def load_vector_extent_info(path):
    try:
        ext = Path(path).suffix.lower()
        if ext not in [".shp", ".geojson"]:
            raise ValueError("Unsupported file format. Please select a .shp or .geojson file.")

        gdf = gpd.read_file(path)
        bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
        west, south, east, north = bounds
        return path, south, north, west, east
    except Exception as e:
        raise RuntimeError(f"Failed to load vector file: {e}")


def load_taxa_and_ids_from_csv(file_path):
    """
    Reads a Movebank-style CSV and returns:
    - DataFrame
    - List of unique taxon names
    - List of unique individual IDs
    """
    try:
        df = pd.read_csv(file_path)
        columns = {re.sub(r"[-._\s]+", "_", col.lower()): col for col in df.columns}
        id_key = "individual_local_identifier"
        taxon_key = "individual_taxon_canonical_name"
        id_col = columns.get(id_key)
        taxon_col = columns.get(taxon_key)
        if id_col is None:
            return None, [], [], "No column found for individual-local-identifier"

        unique_ids = sorted(df[id_col].dropna().astype(str).unique())
        unique_taxa = sorted(df[taxon_col].dropna().astype(str).unique()) if taxon_col else []

        return df, unique_taxa, unique_ids, None

    except Exception as e:
        return None, [], [], str(e)


def start_annotation_process(env_var_map, selected_env_vars, movebank_path, selected_ids,
                             boundary_path, interpolation_method, bbox=None, smoothing_k: int = 2,
                             out_csv_path=None, env_coord_names: dict | None = None):
    """
    env_var_map: dict[str, str] — variable → file path
    selected_env_vars: list[str] — selected variables
    movebank_path: str — path to the Movebank CSV
    selected_ids: list[str] — IDs for annotation
    boundary_path: str — path to .shp or .geojson
    env_coord_names: dict — mapping of coordinate names for env datasets
    """
    print("[DEBUG] Annotation started")
    print("Selected variables:", selected_env_vars)
    print("From files:", [env_var_map.get(v) for v in selected_env_vars])
    print("Selected IDs:", selected_ids)
    print("Movebank file:", movebank_path)
    print("Boundary file:", boundary_path)
    print("Interpolation method:", interpolation_method)

     # === Step 1: Spatial filtering ===
    df_filtered, _ = filter_points_within_boundary(movebank_path, selected_ids, boundary_path, bbox=bbox)
    if df_filtered.empty:
        print("[WARNING] No points within the boundary.")
        return

    # ===*** Time prefiltering (union across selected variables) ===
    time_var = env_coord_names.get("env_time") if env_coord_names else None
    nc_start, nc_end = get_nc_timerange_for_selected(env_var_map, selected_env_vars, time_name=time_var)
    df_filtered = filter_points_within_timerange(df_filtered, nc_start, nc_end)
    if df_filtered.empty:
        print("[WARNING] No points within the NC time window after prefiltering.")
        return
    # ===***

    # === Step 2: Loading and interpolation of environmental data ===
    result = load_selected_environmental_data(df_filtered, env_var_map,
                                               selected_env_vars, movebank_path,
                                               interpolation_method, smoothing_k=smoothing_k, env_coord_names=env_coord_names,)
    if result is None:
        print("[ERROR] Environmental data was not loaded.")
        return

    df_annotated, nc_start, nc_end = result

#### diagnostic
    var = selected_env_vars[0] if selected_env_vars else None
    if var in df_annotated.columns:
        in_nc = df_annotated["timestamp"].between(
            pd.to_datetime(df_annotated["timestamp"]).min() if pd.isna(nc_start) else nc_start,
            pd.to_datetime(df_annotated["timestamp"]).max() if pd.isna(nc_end) else nc_end
        )
        filled_total = df_annotated[var].notna().sum()
        filled_in_nc = df_annotated.loc[in_nc, var].notna().sum()
        print(f"[DEBUG] Filled '{var}': total={filled_total}, within-NC-window={filled_in_nc}")
    else:
        print(f"[WARNING] Column '{var}' not found in annotated DataFrame.")
#####

    # === Step 3: Time filtering ===
    df_time_filtered = df_annotated.copy()
    print("[INFO] Full timestamp range preserved. Outside-NC values will be NaN.")

    # === Step 4: Saving the final result ===
    if out_csv_path:
        out_path = Path(out_csv_path)
    else:
        out_path = Path(movebank_path).parent / "annotated_env.csv"
    df_time_filtered = df_time_filtered.drop(columns=["geometry", "nc_lat", "nc_lon"], errors="ignore")
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
        print("[WARNING] Column 'individual_local_identifier' not found. Skipping per-ID export.")


def filter_points_within_boundary(movebank_path, selected_ids, boundary_path=None, bbox=None):
    print("[DEBUG] Filtering is started")
    df = pd.read_csv(movebank_path)
    df.columns = [re.sub(r"[-:.\s]+", "_", col.lower()) for col in df.columns]
    if "location_long" in df.columns and "location_lon" not in df.columns:
        df["location_lon"] = df["location_long"]
    if "timestamp" not in df.columns and "eobs_start_timestamp" in df.columns:
        df["timestamp"] = df["eobs_start_timestamp"]

    required_cols = {"location_lat", "location_lon", "individual_local_identifier", "timestamp"}
    if not required_cols.issubset(df.columns):
        raise ValueError(f"Required columns are missing in Movebank file. Missing: {required_cols - set(df.columns)}")

    # ID-filter
    df = df[df["individual_local_identifier"].isin(selected_ids)]
    df = interpolate_missing_coordinates(df)

    output_path = Path(movebank_path).parent / "trimmed.csv"
    if bbox is not None:
        S, N, W, E = map(float, (bbox["S"], bbox["N"], bbox["W"], bbox["E"]))
        m = df["location_lat"].between(S, N) & df["location_lon"].between(W, E)
        df = df.loc[m].copy()
        df["geometry"] = [Point(lon, lat) for lon, lat in zip(df["location_lon"], df["location_lat"])]
        gdf_filtered = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")

        try:
            if gdf_filtered.empty:
                print("[INFO] No points within bbox. File not saved.")
            else:
                gdf_filtered.drop(columns=["geometry"], errors="ignore").to_csv(output_path, index=False)
                print(f"[INFO] (bbox) Data saved to {output_path}")
        except Exception as e:
            print(f"[ERROR] Failed to save (bbox) data: {e}")
        return gdf_filtered, output_path

    #  case: boundary from shp/geojson
    df["geometry"] = [Point(lon, lat) for lon, lat in zip(df["location_lon"], df["location_lat"])]
    gdf_points = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")

    if boundary_path is None:
        print("[INFO] No boundary provided. Skipping spatial clipping (all selected IDs kept).")
        try:
            gdf_points.drop(columns=["geometry"], errors="ignore").to_csv(output_path, index=False)
            print(f"[INFO] (No-boundary) Data saved to {output_path}")
        except Exception as e:
            print(f"[ERROR] Failed to save (no-boundary) data: {e}")
        return gdf_points, output_path

    gdf_boundary = gpd.read_file(boundary_path)
    if gdf_boundary.crs != gdf_points.crs:
        gdf_boundary = gdf_boundary.to_crs(gdf_points.crs)

    gdf_filtered = gpd.sjoin(gdf_points, gdf_boundary[["geometry"]], predicate="within", how="inner").drop(columns="index_right")

    try:
        if gdf_filtered.empty:
            print("[INFO] No points within boundary. File not saved.")
        else:
            gdf_filtered.drop(columns=["geometry"], errors="ignore").to_csv(output_path, index=False)
            print(f"[INFO] Filtered data saved to {output_path}")
    except Exception as e:
        print(f"[ERROR] Failed to save filtered data: {e}")

    return gdf_filtered, output_path


def filter_points_within_timerange(df: pd.DataFrame, nc_start: pd.Timestamp, nc_end: pd.Timestamp) -> pd.DataFrame:
    df = df.copy()
    if nc_start is None or nc_end is None:
        print("[INFO] NC union time range unavailable. Skipping time prefilter.")
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], dayfirst=True, errors="coerce")
    before = len(df)
    filtered_df = df[(df["timestamp"] >= nc_start) & (df["timestamp"] <= nc_end)]
    print(f"[INFO] Time-prefiltered rows: {len(filtered_df)} / {before} within [{nc_start} .. {nc_end}]")
    return filtered_df


def interpolate_missing_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Interpolates missing values in 'location_lat' and 'location_lon' columns
    based on the 'timestamp'. Removes rows with invalid timestamps.
    """
    required_cols = {"timestamp", "location_lat", "location_lon"}
    if not required_cols.issubset(df.columns):
        raise ValueError(f"DataFrame must contain columns: {required_cols}")

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], dayfirst=True, errors="coerce")

    n_missing = df["timestamp"].isna().sum()
    if n_missing > 0:
        print(f"[INFO] {n_missing} rows with missing or invalid timestamps were removed before interpolation.")

    df = df.dropna(subset=["timestamp"])  # Remove Na before creating the index
    df = df.sort_values("timestamp")
    df.set_index("timestamp", inplace=True)

    for coord in ["location_lat", "location_lon"]:
        df[coord] = pd.to_numeric(df[coord], errors="coerce")

    df[["location_lat", "location_lon"]] = df[["location_lat", "location_lon"]].interpolate(
        method="time", limit_direction="both"
    )

    df = df.reset_index()
    return df


def load_selected_environmental_data(df, env_var_map, selected_vars,
                                      movebank_path, interpolation_method="Nearest neighbour", smoothing_k: int = 2,
                                      env_coord_names: dict | None = None):
    """
    Wrapper that calls the appropriate annotation function depending on the interpolation method.
    Supports:
      - "Nearest neighbour (time-linear)"
      - "IDW (time-linear)"
    """
    label = (interpolation_method or "").strip().lower()
    label = label.replace("neighbor", "neighbour") # Normalise US/UK spelling

    is_nearest = label.startswith("nearest")
    is_idw = ("idw" in label) or ("inverse distance" in label)

    if is_nearest:
        return annotate_env_nearest(df, env_var_map, selected_vars, movebank_path, smoothing_k=smoothing_k, env_coord_names=env_coord_names)
    elif is_idw:
        return annotate_env_IDW(df, env_var_map, selected_vars, movebank_path, smoothing_k=smoothing_k, env_coord_names=env_coord_names)
    else:
        raise ValueError(f"Unknown interpolation method: {interpolation_method}")


def annotate_env_nearest(df, env_var_map, selected_vars, movebank_path, smoothing_k: int = 4, env_coord_names: dict | None = None):
    """
    Annotate movement points with environmental values using:
      - Spatial: nearest grid node
      - Temporal: vectorised linear interpolation in time (per grid cell)

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
    env_coord_names : dict | None
        Mapping of coordinate names for env datasets. Expected keys:
        'env_time', 'env_x', 'env_y', 'env_lat', 'env_lon'.

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

    # --- input prep -----------------------------------------------------------
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], dayfirst=True, errors="coerce")
    out = out.dropna(subset=["timestamp", "location_lat", "location_lon"])

    # Placeholders for nearest grid coords (one set; overwritten by last variable)
    nc_latitudes = np.full(len(out), np.nan, dtype="float64")
    nc_longitudes = np.full(len(out), np.nan, dtype="float64")

    # Target times for np.interp (int64 ns)
    tgt_times = out["timestamp"].to_numpy("datetime64[ns]").astype("int64")

    # --- main loop over requested labels -------------------------------------
    for label in selected_vars:
        file_path = env_var_map.get(label)
        out[label] = np.nan  # ensure column exists even on failures

        if not file_path or not Path(file_path).is_file():
            print(f"[WARNING] File for {label} not found: {file_path}")
            continue

        # Split the UI label into (base_var, requested_level)
        base_var, target_level = _split_var_and_level(label)

        try:
            env_coord_names = env_coord_names or {}
            time_name = env_coord_names.get("env_time")
            lat_name  = env_coord_names.get("env_lat")
            lon_name  = env_coord_names.get("env_lon")
            x_name    = env_coord_names.get("env_x")
            y_name    = env_coord_names.get("env_y")

            ds = safe_open_nc_with_time_decoding(file_path, time_name=time_name)
            if base_var not in ds:
                print(f"[WARNING] Base variable '{base_var}' not found in {file_path}")
                ds.close()
                continue

            da = ds[base_var]
            dims = list(da.dims)

            # coordinate/dimension selection
            lat_dim = lat_name if (lat_name in dims) else None
            lon_dim = lon_name if (lon_name in dims) else None

            if lat_dim is None or lon_dim is None:
                # Optional strict fallback to x/y if user provided them AND they are dims
                x_dim = x_name if (x_name in dims) else None
                y_dim = y_name if (y_name in dims) else None

                if x_dim is not None and y_dim is not None:
                    lat_dim = y_dim
                    lon_dim = x_dim
                else:
                    ds.close()
                    raise ValueError(
                        "Could not resolve spatial dimensions from the provided env_coord_names.\n"
                        f"  Requested lat dim: {lat_name!r} (is_dim={lat_name in dims if lat_name else False})\n"
                        f"  Requested lon dim: {lon_name!r} (is_dim={lon_name in dims if lon_name else False})\n"
                        f"  Requested y dim:   {y_name!r} (is_dim={y_name in dims if y_name else False})\n"
                        f"  Requested x dim:   {x_name!r} (is_dim={x_name in dims if x_name else False})\n"
                        f"  Available dims for {base_var!r}: {dims}"
                    )

            ds = _ensure_sorted(ds, lat_dim, lon_dim)
            da = ds[base_var]
            dims = list(da.dims)

            # Validate that lat/lon dims are 1D coordinate vectors
            glat = np.asarray(ds[lat_dim].values)
            glon = np.asarray(ds[lon_dim].values)
            if glat.ndim != 1 or glon.ndim != 1:
                ds.close()
                raise ValueError(
                    f"Nearest-grid method requires 1D coordinate vectors for '{lat_dim}' and '{lon_dim}'. "
                    f"Got shapes: {lat_dim}={glat.shape}, {lon_dim}={glon.shape}."
    )

            # time dim should already be 'time' because safe_open... renames it, but keep the fallback
            if "time" not in dims:
                ds.close()
                raise ValueError(f"No 'time' dim after decoding for '{base_var}'. dims={dims}")

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
            lon_idx = _nearest_indices_vectorized(glon, out["location_lon"].to_numpy(dtype="float64"))

            # Store the matched grid coordinates (useful for QA)
            nc_latitudes[:] = glat[lat_idx]
            nc_longitudes[:] = glon[lon_idx]

            # Group points by grid cell (to read each per-cell time series only once)
            cell_code = (lat_idx.astype(np.int64) * len(glon)) + lon_idx.astype(np.int64)
            unique_cells, inverse = np.unique(cell_code, return_inverse=True)

            # Cache of per-cell series: (ii, jj) -> 1D float64 array over time
            series_cache: dict[tuple[int, int], np.ndarray] = {}
            col_idx = out.columns.get_loc(label)

            for g, code in enumerate(unique_cells):
                ii = int(code // len(glon))
                jj = int(code % len(glon))

                pos = np.nonzero(inverse == g)[0]       # row indices in `out` for this cell
                xi = tgt_times[pos]                     # target times (int64 ns)

                key = (ii, jj)
                if key not in series_cache:
                    # Read the cell time series once; cast to float64 for np.interp
                    series_cache[key] = da.isel({lat_dim: ii, lon_dim: jj}).values.astype("float64")
                y = series_cache[key]

                # Valid-only mask for temporal interpolation
                m = np.isfinite(y)
                if m.sum() < 2:
                    out.iloc[pos, col_idx] = np.nan
                    continue

                x = gtime[m]   # source times (int64)
                yy = y[m]      # source values

                vals = np.interp(xi, x, yy)
                # Outside native time range → NaN (np.interp would extend)
                vals[(xi < x.min()) | (xi > x.max())] = np.nan

                out.iloc[pos, col_idx] = vals

            ds.close()

        except Exception as e:
            print(f"[ERROR] {label}: {e}")
            continue

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
    env_coord_names: dict | None = None,
):
    """
    Annotate movement points with environmental values using:
      - Spatial: Inverse Distance Weighting (IDW) over k nearest grid nodes
      - Temporal: 1D linear interpolation in time (per grid node), vectorised via np.interp

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
    env_coord_names : dict | None
        Mapping of coordinate names for env datasets. Expected keys:
        'env_time', 'env_x', 'env_y', 'env_lat', 'env_lon'.
    Returns
    -------
    (out_df, nc_start, nc_end)
        `out_df` contains new columns with the same names as `selected_vars`.
        `nc_start`, `nc_end` are placeholders here (NaT).
    """
    # --- input prep ----------------------------------------------------------------
    k = max(2, int(smoothing_k))
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], dayfirst=True, errors="coerce")
    out = out.dropna(subset=["timestamp", "location_lat", "location_lon"])

    # Keep nc_lat/nc_lon semantics consistent with prior implementation (copy of point coords)
    out["nc_lat"] = out["location_lat"].values
    out["nc_lon"] = out["location_lon"].values

    # Vectorised numeric targets for temporal interpolation
    tgt_times = out["timestamp"].to_numpy("datetime64[ns]").astype("int64")
    lat_vals = out["location_lat"].to_numpy(dtype="float64")
    lon_vals = out["location_lon"].to_numpy(dtype="float64")

    # --- main loop over labels -----------------------------------------------------
    for label in selected_vars:
        file_path = env_var_map.get(label)
        out[label] = np.nan  # ensure the column exists even if we skip/err

        if not file_path or not Path(file_path).is_file():
            print(f"[WARNING] File for {label} not found: {file_path}")
            continue

        # Split label into base variable and optional requested level
        base_var, target_level = _split_var_and_level(label)

        try:
            env_coord_names = env_coord_names or {}
            time_name = env_coord_names.get("env_time")
            lat_name = env_coord_names.get("env_lat")
            lon_name = env_coord_names.get("env_lon")
            x_name = env_coord_names.get("env_x")
            y_name = env_coord_names.get("env_y")

            ds = safe_open_nc_with_time_decoding(file_path, time_name=time_name)

            if base_var not in ds:
                print(f"[WARNING] Base variable '{base_var}' not in {file_path}")
                ds.close()
                continue

            da = ds[base_var]
            dims = list(da.dims)

            # coordinate/dimension selection
            lat_dim = lat_name if (lat_name in dims) else None
            lon_dim = lon_name if (lon_name in dims) else None

            if lat_dim is None or lon_dim is None:
                # Strict fallback to x/y only if user provided them AND they are dims
                x_dim = x_name if (x_name in dims) else None
                y_dim = y_name if (y_name in dims) else None

                if x_dim is not None and y_dim is not None:
                    lat_dim = y_dim
                    lon_dim = x_dim
                else:
                    ds.close()
                    raise ValueError(
                        "Could not resolve spatial dimensions from the provided env_coord_names.\n"
                        f"  Requested lat dim: {lat_name!r} (is_dim={lat_name in dims if lat_name else False})\n"
                        f"  Requested lon dim: {lon_name!r} (is_dim={lon_name in dims if lon_name else False})\n"
                        f"  Requested y dim:   {y_name!r} (is_dim={y_name in dims if y_name else False})\n"
                        f"  Requested x dim:   {x_name!r} (is_dim={x_name in dims if x_name else False})\n"
                        f"  Available dims for {base_var!r}: {dims}"
                    )

            ds = _ensure_sorted(ds, lat_dim, lon_dim)
            da = ds[base_var]
            dims = list(da.dims)

            # Validate that lat/lon dims are 1D coordinate vectors
            glat = np.asarray(ds[lat_dim].values)
            glon = np.asarray(ds[lon_dim].values)
            if glat.ndim != 1 or glon.ndim != 1:
                ds.close()
                raise ValueError(
                    f"IDW method requires 1D coordinate vectors for '{lat_dim}' and '{lon_dim}'. "
                    f"Got shapes: {lat_dim}={glat.shape}, {lon_dim}={glon.shape}."
                )

            # time dim should already be 'time' because safe_open... renames it
            if "time" not in dims:
                ds.close()
                raise ValueError(f"No 'time' dim after decoding for '{base_var}'. dims={dims}")

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
            glat = ds[lat_dim].values
            glon = ds[lon_dim].values
            gtime_int = pd.to_datetime(ds["time"].values).to_numpy("datetime64[ns]").astype("int64")

            # Cache per-grid-node time series (to avoid repeated reads for neighbors)
            # key: (ii, jj) -> (x_int64_valid, y_float64_valid)
            series_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
            col_idx = out.columns.get_loc(label)

            # Row-wise IDW over k nearest grid nodes
            for i in range(len(out)):
                t_i = tgt_times[i]
                xlat = lat_vals[i]
                xlon = lon_vals[i]

                # If outside the native time span → keep NaN
                if t_i < gtime_int.min() or t_i > gtime_int.max():
                    continue

                nn_idx = _k_nearest_indices(glat, glon, xlat, xlon, k)  # provided elsewhere
                vals = np.empty(k, dtype="float64")
                dists = np.empty(k, dtype="float64")

                for j, (ii, jj) in enumerate(nn_idx):
                    key = (ii, jj)
                    if key not in series_cache:
                        # Read cell time series once; keep only valid points for interp
                        y = da.isel({lat_dim: ii, lon_dim: jj}).values.astype("float64")
                        m = np.isfinite(y)
                        if m.sum() >= 2:
                            x = gtime_int[m]
                            yy = y[m]
                        else:
                            x = np.empty(0, dtype="int64")
                            yy = np.empty(0, dtype="float64")
                        series_cache[key] = (x, yy)

                    x, yy = series_cache[key]
                    if x.size < 2:
                        vals[j] = np.nan
                    else:
                        v = np.interp(t_i, x, yy)
                        # clamp to NaN if extrapolated
                        if (t_i < x.min()) or (t_i > x.max()):
                            v = np.nan
                        vals[j] = v

                    # Planar Euclidean distance in degrees (consistent with prior code)
                    dists[j] = np.hypot(glat[ii] - xlat, glon[jj] - xlon)

                out.iloc[i, col_idx] = _idw(vals, dists, p=2)  # provided elsewhere

            ds.close()

        except Exception as e:
            print(f"[ERROR] {label}: {e}")
            continue

    # Geometry for QA/exports
    out["geometry"] = [Point(lon, lat) for lon, lat in zip(out["nc_lon"], out["nc_lat"])]
    return out, pd.NaT, pd.NaT


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

                # Read scale_factor from tags (if present); otherwise use a 0.0001 heuristic for int16 NDVI/EVI
                scale = None
                try:
                    tags = src.tags()
                    for k in ("scale_factor", "SCALE", "Scale", "scale"):
                        if k in tags:
                            scale = float(tags[k]); break
                except Exception:
                    pass
                if scale is None and (np.nanmin(arr) >= -10000) and (np.nanmax(arr) <= 10000):
                    scale = 0.0001
                if scale is not None:
                    arr = arr * scale

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
            data_array,
            dims=["time", "lat", "lon"],
            coords={"time": time_index, "lat": lat, "lon": lon},
            name=vname
        )
        data_vars[vname] = da

    ds = xr.Dataset(data_vars)
    base = Path(tif_paths[0]).name.split("_")[0]
    safe_base = re.sub(r"[^\w\-]", "_", base)
    out = Path(output_dir) / f"{safe_base}_nc_output.nc"
    ds.to_netcdf(out)
    return str(out)


def parse_time_from_filename(filename):
    """
    Example: MOD13A1.061__500m_16_days_NDVI_doy2014145000000_aid0001.tif
    Parses date using "doyYYYYDDD", where DDD is the day of year.
    """
    match = re.search(r'doy(\d{4})(\d{3})', filename)
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
        "NDVI", "EVI",
        "LST_Day_1km", "LST_Night_1km", "LST_Day_1KM", "LST_Night_1KM", "QC_Day", "QC_Night",
        "Lai_500m", "Fpar_500m", "FparLai_QC",
        "Nadir_Reflectance_Band1", "Nadir_Reflectance_Band2", "Nadir_Reflectance_Band3",
        "Nadir_Reflectance_Band4", "Nadir_Reflectance_Band5", "Nadir_Reflectance_Band6",
        "Nadir_Reflectance_Band7",
        "SurfReflect_Band1", "SurfReflect_Band2", "SurfReflect_Band3",
        "SurfReflect_Band4", "SurfReflect_Band5", "SurfReflect_Band6", "SurfReflect_Band7",
        "NDSI_Snow_Cover",
        "VIIRS_NDVI", "VIIRS_EVI",
        "BurnDate", "BurnDate_Uncertainty", "LAI", "FPAR", "QC"
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
    return idx if abs(arr[idx] - x) < abs(arr[idx-1] - x) else idx-1


def _k_nearest_indices(glat, glon, xlat, xlon, k):
    """Returns an array of indices (ilat, ilon) of length k among candidates from the local window"""
    # first the shortest path is the nearest grid
    i0 = _nearest_index(glat, xlat)
    j0 = _nearest_index(glon, xlon)

    # form a small window around (i0, j0) sufficient to find k neighbors
    # empirically: radius r = ceil(max(1, sqrt(k))) → (2r+1)^2 >= k
    r = int(np.ceil(max(1, np.sqrt(k))))
    i_min, i_max = max(0, i0 - r), min(len(glat) - 1, i0 + r)
    j_min, j_max = max(0, j0 - r), min(len(glon) - 1, j0 + r)

    # collect candidates in the window
    cand = []
    for ii in range(i_min, i_max + 1):
        for jj in range(j_min, j_max + 1):
            d = np.hypot(glat[ii] - xlat, glon[jj] - xlon)
            cand.append((d, ii, jj))
    cand.sort(key=lambda t: t[0])
    top = cand[:k]
    return [(ii, jj) for _, ii, jj in top]


def _idw(values, distances, p=2):
    """IDW average for already interpolated values. distances > 0 (add eps)."""
    vals = np.array(values, dtype=float)
    d = np.array(distances, dtype=float) + 1e-12
    w = 1.0 / (d ** p)
    # ignore NaN in vals
    mask = ~np.isnan(vals)
    if not mask.any():
        return np.nan
    w_sel = w[mask]
    v_sel = vals[mask]
    return np.sum(w_sel * v_sel) / np.sum(w_sel)


def _detect_time_name(ds):
    # 1)quick candidates by name
    name_candidates = ("time", "timestamp", "Timestamp", "Time", "valid_time", "forecast_time", "verification_time", "t", "Time", "datetime", "date")
    for c in name_candidates:
        if c in ds.coords or c in ds.variables:
            return c

    # 2) CF attributes: standard_name = "time" or units with the word "since"
    for name, var in ds.variables.items():
        stdn = str(var.attrs.get("standard_name","")).lower()
        units = str(var.attrs.get("units",""))
        if stdn == "time":
            return name
        if "since" in units:
            return name
    return None


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
    Returns the level index:
    - if target_level is given, the closest to it;
    - otherwise, the closest to 1000 hPa;
    - if error, 0.
    """
    try:
        vals = np.asarray(ds[level_dim].values, dtype=float)
        if vals.size == 0:
            return 0
        ref = 1000.0 if target_level is None else float(target_level)
        return int(np.nanargmin(np.abs(vals - ref)))
    except Exception:
        return 0