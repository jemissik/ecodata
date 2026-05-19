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

LEVEL_DIM_CANDIDATES = ("isobaricInhPa","isobaric_in_hPa","level","lev","plev","pressure","pressure_level")


def open_nc_metadata(path: str) -> xr.Dataset:
    """
    Open a NetCDF dataset for metadata inspection only.

    This avoids time decoding so the UI can list variables and coordinate
    candidates even when the time coordinate needs to be selected manually.
    """
    return xr.open_dataset(path, decode_times=False, chunks="auto")


def detect_env_coord_names(ds: xr.Dataset) -> dict:
    """
    Detect likely coordinate names for an environmental dataset.

    Returns keys: env_time, env_x, env_y, env_lat, env_lon.
    Values may be None when not detected.
    """
    env_time = _detect_time_name(ds)

    x_candidates = ("x", "X", "projection_x_coordinate", "easting", "eastings")
    y_candidates = ("y", "Y", "projection_y_coordinate", "northing", "northings")
    lat_candidates = ("lat", "latitude", "Latitude")
    lon_candidates = ("lon", "longitude", "Longitude", "long")

    env_x = next((c for c in x_candidates if c in ds.coords and c in ds.dims), None)
    env_y = next((c for c in y_candidates if c in ds.coords and c in ds.dims), None)
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
    

def get_nc_timerange_for_selected(
    env_var_map: dict,
    selected_env_vars: list[str],
    time_name: str | None = None,
):
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
    ds = safe_open_nc_with_time_decoding(nc_path, time_name=env_coord_names.get("env_time"))
    # candidate coordinate names
    try:
        lat_name = env_coord_names.get("env_lat")
        lon_name = env_coord_names.get("env_lon")

        if not lat_name or not lon_name:
            lat_candidates = ("lat", "latitude", "Latitude")
            lon_candidates = ("lon", "longitude", "Longitude", "long")
            lat_name = next((c for c in lat_candidates if c in ds.coords or c in ds.variables), None)
            lon_name = next((c for c in lon_candidates if c in ds.coords or c in ds.variables), None)

        if lat_name is None or lon_name is None:
            raise ValueError("Could not detect lat/lon coordinate names in NetCDF")

        lat_min = float(ds[lat_name].min())
        lat_max = float(ds[lat_name].max())
        lon_min = float(ds[lon_name].min())
        lon_max = float(ds[lon_name].max())
        return {"S": lat_min, "N": lat_max, "W": lon_min, "E": lon_max} 
    finally:
        ds.close()

def remove_temporary_trimmed_file(trimmed_path):
    """Remove temporary trimmed.csv created during spatial filtering."""
    if trimmed_path is None:
        return

    try:
        path = Path(trimmed_path)
        if path.exists() and path.is_file():
            path.unlink()
            print(f"[INFO] Temporary file removed: {path}")
    except Exception as e:
        print(f"[WARNING] Could not remove temporary trimmed.csv: {e}")

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
                             out_csv_path=None, coord_spec=None,
                             env_coord_names: dict | None = None,
                             continuous_vars=None, categorical_vars=None,
                             apply_value_correction: bool = False,
                             value_scale_factor: float = 1.0,
                             value_add_offset: float = 0.0,
                             value_correction_vars=None):
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
    df_filtered, trimmed_path = filter_points_within_boundary(
        movebank_path, selected_ids, boundary_path, bbox=bbox
    )

    if df_filtered.empty:
        print("[WARNING] No points within the boundary.")
        remove_temporary_trimmed_file(trimmed_path)
        return
    
    # ===*** Time prefiltering (union across selected variables) ===
    nc_start, nc_end = get_nc_timerange_for_selected(
        env_var_map,
        selected_env_vars,
        time_name=env_coord_names.get("env_time"),
    )
    df_filtered = filter_points_within_timerange(df_filtered, nc_start, nc_end)
    if df_filtered.empty:
        print("[WARNING] No points within the NC time window after prefiltering.")
        remove_temporary_trimmed_file(trimmed_path)
        return
    # ===*** 

    # === Step 2: Loading and interpolation of environmental data ===
    result = load_selected_environmental_data(
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
    )
    if result is None:
        print("[ERROR] Environmental data was not loaded.")
        remove_temporary_trimmed_file(trimmed_path)
        return

    df_annotated, ann_nc_start, ann_nc_end = result
    # Optional post-sampling value correction 
    # Apply only to continuous variables after sampling/interpolation.
    # This is methodologically safe for linear scale/offset:
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
            pd.to_datetime(df_annotated["timestamp"]).max() if pd.isna(nc_end) else nc_end
        )
        filled_total = df_annotated[var].notna().sum()
        filled_in_nc = df_annotated.loc[in_nc, var].notna().sum()
        print(f"[DEBUG] Filled '{var}': total={filled_total}, within-NC-window={filled_in_nc}")
    else:
        print(f"[WARNING] Column '{var}' not found in annotated DataFrame.")

    # === Step 3: Time filtering ===
    df_time_filtered = df_annotated.copy()
    print("[INFO] Full timestamp range preserved. Outside-NC values will be NaN.")

    # === Step 4: Saving the final result ===
    if out_csv_path:
        out_path = Path(out_csv_path)
    else:
        out_path = Path(movebank_path).parent / "annotated_env.csv"
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
        print("[WARNING] Column 'individual_local_identifier' not found. Skipping per-ID export.")
    remove_temporary_trimmed_file(trimmed_path)


def filter_points_within_boundary(movebank_path, selected_ids, boundary_path=None, bbox=None):
    print("[DEBUG] Filtering is started")
    df = pd.read_csv(movebank_path)
    df.columns = [re.sub(r"[-:.\s]+", "_", col.lower()) for col in df.columns]
    # --- unify longitude column to location_lon ---
    if "location_lon" in df.columns and "location_long" in df.columns:
        # both exist -> keep location_lon (canonical), drop location_long
        df = df.drop(columns=["location_long"])
    elif "location_lon" not in df.columns and "location_long" in df.columns:
        # only location_long -> rename to canonical location_lon
        df = df.rename(columns={"location_long": "location_lon"})
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
                                      movebank_path, interpolation_method="Nearest neighbour",
                                      smoothing_k: int = 2,
                                      coord_spec=None,
                                      env_coord_names: dict | None = None,
                                      continuous_vars=None,
                                      categorical_vars=None):
    """
    Wrapper that calls the appropriate annotation function depending on the interpolation method.

    Current behaviour:
    - Continuous + Nearest neighbour:
        nearest spatial grid node + linear temporal interpolation
    - Continuous + IDW:
        k nearest spatial grid nodes + linear temporal interpolation per node + IDW
    - Categorical/QC + Nearest neighbour:
        nearest spatial grid node + nearest timestep
    - Categorical/QC + IDW selected:
        categorical/QC variables are not IDW-averaged;
        they use nearest spatial grid node + nearest timestep
    - Continuous + Bilinear projected x/y:
        bilinear interpolation on a projected 1D x/y grid + linear temporal interpolation
    - Categorical/QC + Bilinear projected x/y:
        not allowed, because bilinear interpolation is not valid for class/flag codes
    """
    label = (interpolation_method or "").strip().lower()
    label = label.replace("neighbor", "neighbour") # Normalise US/UK spelling

    is_nearest = label.startswith("nearest")
    is_idw = ("idw" in label) or ("inverse distance" in label)

    # Normalize interpolation method
    method = (interpolation_method or "").lower()
    is_nearest = ("nearest" in method)
    is_idw = ("idw" in method)
    is_bilinear = "bilinear" in method

    # If split lists are not provided, treat everything as "selected_vars"
    cont = list(continuous_vars or [])
    cat  = list(categorical_vars or [])

    if not cont and not cat:
        # everything in selected_vars, method applies to all
        if is_nearest:
            return annotate_env_nearest(
                df, env_var_map, selected_vars, movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                env_coord_names=env_coord_names,
            )

        if is_idw:
            return annotate_env_IDW(
                df, env_var_map, selected_vars, movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                env_coord_names=env_coord_names,
            )

        if is_bilinear:
            return annotate_env_bilinear_projected(
                df,
                env_var_map,
                selected_vars,
                movebank_path,
                env_coord_names=env_coord_names,
            )

        raise ValueError(f"Unknown interpolation method: {interpolation_method}")

    # If split lists are provided:
    # 1) Nearest selected:
    #    continuous -> nearest grid node + linear time interpolation
    #    categorical/QC -> nearest grid node + nearest timestep
    if is_nearest:
        out_df = df
        nc_start = pd.NaT
        nc_end = pd.NaT

        # Continuous: nearest grid node + linear time interpolation
        if cont:
            out_df, nc_start, nc_end = annotate_env_nearest(
                out_df, env_var_map, cont, movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                env_coord_names=env_coord_names,
                temporal_method="linear"
            )

        # Categorical/QC: nearest grid node + nearest timestep
        if cat:
            out_df, nc_start2, nc_end2 = annotate_env_nearest(
                out_df, env_var_map, cat, movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                env_coord_names=env_coord_names,
                temporal_method="nearest"
            )

            if pd.isna(nc_start) and not pd.isna(nc_start2):
                nc_start = nc_start2
            if pd.isna(nc_end) and not pd.isna(nc_end2):
                nc_end = nc_end2

        return out_df, nc_start, nc_end

    # 2) IDW selected -> cont=IDW, cat=NN
    if is_idw:
        out_df = df
        nc_start = pd.NaT
        nc_end = pd.NaT

        # continuous via IDW
        if cont:
            out_df, nc_start, nc_end = annotate_env_IDW(
                out_df, env_var_map, cont, movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                env_coord_names=env_coord_names,
                temporal_method="linear"
            )

        # categorical via Nearest neighbour in space + nearest timestep in time
        if cat:
            out_df, nc_start2, nc_end2 = annotate_env_nearest(
                out_df, env_var_map, cat, movebank_path,
                smoothing_k=smoothing_k,
                coord_spec=coord_spec,
                env_coord_names=env_coord_names,
                temporal_method="nearest"
            )
            # keep nc_start/nc_end stable (both annotators return NaT)
            if pd.isna(nc_start) and not pd.isna(nc_start2):
                nc_start = nc_start2
            if pd.isna(nc_end) and not pd.isna(nc_end2):
                nc_end = nc_end2

        return out_df, nc_start, nc_end
    
    # 3) Bilinear projected selected:
    #    continuous -> bilinear projected x/y + linear time
    #    categorical/QC -> not allowed
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
        )
    
    raise ValueError(f"Unknown interpolation method: {interpolation_method}")


    
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


def annotate_env_nearest(df, env_var_map, selected_vars, movebank_path, smoothing_k: int = 4,
                         coord_spec=None, env_coord_names: dict | None = None,
                         temporal_method: str = "linear"):
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

    # --- input prep -----------------------------------------------------------
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], dayfirst=True, errors="coerce")
    out = out.dropna(subset=["timestamp", "location_lat", "location_lon"])

    temporal_method = (temporal_method or "linear").strip().lower()
    if temporal_method not in ("linear", "nearest"):
        temporal_method = "linear"
    env_coord_names = env_coord_names or {}

    # Placeholders for nearest grid coords (one set; overwritten by last variable)
    nc_latitudes = np.full(len(out), np.nan, dtype="float64")
    nc_longitudes = np.full(len(out), np.nan, dtype="float64")

    # Target times as int64 ns, used for either np.interp or nearest-time lookup.
    tgt_times = out["timestamp"].to_numpy("datetime64[ns]").astype("int64")

    # --- main loop over requested labels -------------------------------------
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

        try:
            ds = safe_open_nc_with_time_decoding(
                file_path,
                time_name=env_coord_names.get("env_time"),
            )
            ds = standardize_time_lat_lon(ds, coord_spec)
            if base_var not in ds:
                print(f"[WARNING] Base variable '{base_var}' not found in {file_path}")
                ds.close()
                continue

            da = ds[base_var]
            dims = list(da.dims)

            # Detect lat/lon names; keep dataset sorted in both
            lat_dim = "lat" if "lat" in dims else "latitude"
            lon_dim = "lon" if "lon" in dims else "longitude"
            ds = _ensure_sorted(ds, lat_dim, lon_dim)
            da = ds[base_var]
            dims = list(da.dims)

            # Unify/ensure time dimension is named 'time'
            time_dim = "time" if "time" in dims else next(
                (d for d in ("valid_time", "forecast_time", "verification_time", "t", "Time") if d in dims),
                None
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
            lon_idx = _nearest_indices_vectorized(glon, out["location_lon"].to_numpy(dtype="float64"))

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

                pos = np.nonzero(inverse == g)[0]       # row indices in `out` for this cell
                xi = tgt_times[pos]                     # target times (int64 ns)

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

                    x = gtime[m]   # source times, int64 ns
                    yy = y[m]      # source values, may be integer/category codes

                    # Ensure time is sorted
                    order = np.argsort(x)
                    x = x[order]
                    yy = yy[order]

                    idx = np.searchsorted(x, xi)
                    right = np.clip(idx, 0, len(x) - 1)
                    left = np.clip(idx - 1, 0, len(x) - 1)

                    use_left = (
                        (idx > 0)
                        & (
                            (idx == len(x))
                            | (np.abs(xi - x[left]) <= np.abs(x[right] - xi))
                        )
                    )

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


def annotate_env_IDW(df, env_var_map, selected_vars, movebank_path, smoothing_k: int = 2,
                     coord_spec=None, env_coord_names: dict | None = None,
                     temporal_method: str = "linear"):
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
    # --- input prep ----------------------------------------------------------------
    k = max(2, int(smoothing_k))
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], dayfirst=True, errors="coerce")
    out = out.dropna(subset=["timestamp", "location_lat", "location_lon"])

    temporal_method = (temporal_method or "linear").strip().lower()
    if temporal_method not in ("linear", "nearest"):
        temporal_method = "linear"
    env_coord_names = env_coord_names or {}

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

        try:
            ds = safe_open_nc_with_time_decoding(
                file_path,
                time_name=env_coord_names.get("env_time"),
            )
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
            time_dim = "time" if "time" in dims else next(
                (d for d in ("valid_time", "forecast_time", "verification_time", "t", "Time") if d in dims), None
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
            glat = ds[lat_dim].values
            glon = ds[lon_dim].values
            gtime_int = pd.to_datetime(ds["time"].values).to_numpy("datetime64[ns]").astype("int64")

            # Cache per-grid-node time series (to avoid repeated reads for neighbors)
            # key: (ii, jj) -> (x_int64_valid, y_valid)
            # For linear mode y_valid is float64; for nearest-time mode original dtype is preserved.
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
                        raw_y = da.isel({lat_dim: ii, lon_dim: jj}).values

                        if temporal_method == "nearest":
                            # Keep original values for nearest-time lookup.
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
                            # Linear interpolation requires numeric float values.
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

                            use_left = (
                                (idx > 0)
                                and (
                                    (idx == len(x))
                                    or (abs(t_i - x[left]) <= abs(x[right] - t_i))
                                )
                            )

                            nearest_idx = left if use_left else right
                            v = yy[nearest_idx]

                            # Keep no-extrapolation behaviour.
                            if (t_i < x.min()) or (t_i > x.max()):
                                v = np.nan

                            vals[j] = v

                    else:
                        if x.size < 2:
                            vals[j] = np.nan
                        else:
                            v = np.interp(t_i, x, yy)

                            # Keep no-extrapolation behaviour.
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

def annotate_env_bilinear_projected(
    df,
    env_var_map,
    selected_vars,
    movebank_path,
    env_coord_names: dict | None = None,
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
    out["timestamp"] = pd.to_datetime(out["timestamp"], dayfirst=True, errors="coerce")

    # Require lon/lat (your code already normalizes movement columns sometimes)
    required = ["timestamp", "location_lat", "location_lon"]
    out = out.dropna(subset=required)

    env_coord_names = env_coord_names or {}
    time_name = env_coord_names.get("env_time")  # optional
    x_name    = env_coord_names.get("env_x")
    y_name    = env_coord_names.get("env_y")

    if not x_name or not y_name:
        raise ValueError(
            "Bilinear (projected) requires env_coord_names['env_x'] and ['env_y'] "
            "(Projected (x/y) mode)."
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
                crs_cache[file_path] = read_crs_from_cf(ds, var_name=base_var)

            target_crs = crs_cache[file_path]
            x_pts, y_pts = project_tracks_lonlat_to_xy(lon, lat, target_crs=target_crs)

            # Store for QA
            out["x"] = x_pts
            out["y"] = y_pts

            # --- vectorized xarray interpolation -----------------------------------
            pts = xr.Dataset(
                coords={"points": np.arange(len(out))},
                data_vars={
                    "time": ("points", tgt_t),
                    x_dim: ("points", x_pts),
                    y_dim: ("points", y_pts),
                },
            )

            sampled = da.interp({x_dim: pts[x_dim], y_dim: pts[y_dim], "time": pts["time"]})
            out[label] = sampled.to_numpy()

            ds.close()

        except Exception as e:
            print(f"[ERROR] {label}: {e}")
            continue

    # If you want: geometry in projected CRS (x,y). Comment out if not needed.
    out["geometry"] = [Point(x, y) for x, y in zip(out["x"], out["y"])]

    return out, pd.NaT, pd.NaT

def read_crs_from_cf(ds: xr.Dataset, var_name: str | None = None) -> CRS:
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

    # 1) If a data variable is given, try its grid_mapping attribute
    grid_mapping_name = None
    if var_name is not None and var_name in ds:
        grid_mapping_name = ds[var_name].attrs.get("grid_mapping")

    # 2) If we have a grid mapping variable, parse it as CF
    if grid_mapping_name and grid_mapping_name in ds.variables:
        gm = ds[grid_mapping_name]
        # xarray keeps attrs as dict; pyproj can build CRS from CF dict
        try:
            return CRS.from_cf(gm.attrs)
        except Exception:
            pass

    # 3) Common alternate places: global attrs
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

    raise ValueError("Could not infer CRS from dataset (no usable CF grid_mapping / WKT / proj string found).")


def project_tracks_lonlat_to_xy(
    lon: np.ndarray,
    lat: np.ndarray,
    target_crs: CRS,
) -> tuple[np.ndarray, np.ndarray]:
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
    name_candidates = ("time","valid_time","forecast_time","verification_time","t","Time","datetime","date")
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
