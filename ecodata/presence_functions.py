"""
presence data preparation backend functions.

- VettingOptions
- AggregationOptions
- aggregate_ebird_to_files
- export_tracks_from_aggregated_counts
- read_species_from_agg_counts
"""

from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

try:
    import geopandas as gpd
    from shapely.geometry import Point, box
except Exception:  # pragma: no cover
    gpd = None
    Point = None
    box = None


@dataclass
class VettingOptions:
    """
    Vetting/filter options for eBird EBD + Sampling Event data.

    UI mapping:
    - require_reviewed: filters by REVIEWED when present
    - require_approved: filters by APPROVED when present
    - require_all_species_reported: filters by ALL SPECIES REPORTED when present
    - allowed_protocols: matches PROTOCOL TYPE (preferred) or PROTOCOL CODE if present
    - exclude_incidental_historical: excludes Incidental/Historical when PROTOCOL TYPE present
    - duration/distance bounds: applied when sampling effort fields are present
    - require_valid_coords: removes rows with missing/invalid lat/lon
    - clip_counts_above: clips numeric counts after parsing; 0 disables clipping
    """

    require_reviewed: bool = False
    require_approved: bool = False
    require_all_species_reported: bool = False

    allowed_protocols: Optional[List[str]] = None
    exclude_incidental_historical: bool = True

    duration_min_minutes: int = 0
    duration_max_minutes: int = 600

    distance_min_km: float = 0.0
    distance_max_km: float = 50.0

    require_valid_coords: bool = True

    clip_counts_above: int = 0


@dataclass
class AggregationOptions:
    """
    Time aggregation options.

    Aggregation is performed in bins of N days starting from start_date.

    Spatial aggregation:
    - grid_step_deg == 0: keep original observation coordinates
    - grid_step_deg > 0: assign observations to regular lon/lat grid nodes

    Notes:
    - treat_x_as_one: if True, OBSERVATION COUNT == 'X' is treated as 1.
      If False, 'X' is treated as missing and then filled to 1.0 for presence-like behavior.
    """

    start_date: dt.date
    end_date: dt.date
    step_days: int = 7
    grid_step_deg: float = 0.0
    treat_x_as_one: bool = True


def _truthy(series: pd.Series) -> pd.Series:
    """
    Interpret typical eBird truthy values.
    """
    s = series.fillna("").astype(str).str.strip().str.upper()
    return s.isin(["1", "TRUE", "T", "YES", "Y"])


def _read_bytes_table(file_bytes: bytes) -> pd.DataFrame:
    """
    Read EBD/Sampling tables from bytes.

    Supports:
    - TSV (tab-separated) plain
    - gzip-compressed TSV
    - zip containing a TSV/TXT/CSV

    Drops any 'Unnamed:*' columns.
    """
    if not file_bytes:
        raise ValueError("Empty file bytes.")

    # ZIP container
    if zipfile.is_zipfile(io.BytesIO(file_bytes)):
        with tempfile.TemporaryDirectory() as td:
            zp = os.path.join(td, "f.zip")
            with open(zp, "wb") as f:
                f.write(file_bytes)

            with zipfile.ZipFile(zp, "r") as zf:
                names = zf.namelist()
                cand = [n for n in names if n.lower().endswith((".txt", ".tsv", ".csv"))]
                if not cand:
                    raise ValueError("ZIP does not contain a .txt/.tsv/.csv table.")
                target = cand[0]
                with zf.open(target) as zfh:
                    raw = zfh.read()
        return _read_bytes_table(raw)

    # GZIP container
    if file_bytes[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(file_bytes)
        except Exception as e:
            raise ValueError(f"Failed to decompress gzip content: {e}") from e
        return _read_bytes_table(raw)

    # Plain text table: try TSV then CSV
    bio = io.BytesIO(file_bytes)
    try:
        df = pd.read_csv(bio, sep="\t", dtype=str, low_memory=False)
    except Exception:
        bio.seek(0)
        df = pd.read_csv(bio, sep=",", dtype=str, low_memory=False)

    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed:")]
    return df

def _read_path_table(path: str) -> pd.DataFrame:
    """
    Read EBD/Sampling tables from a local filesystem path.

    Supports:
    - plain TSV/CSV
    - .gz
    - .zip containing a .txt/.tsv/.csv table

    Drops any 'Unnamed:*' columns.
    """
    if not path:
        raise ValueError("Empty file path.")
    if not os.path.exists(path):
        raise ValueError(f"File does not exist: {path}")

    lower = path.lower()

    # ZIP container
    if lower.endswith(".zip"):
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            cand = [n for n in names if n.lower().endswith((".txt", ".tsv", ".csv"))]
            if not cand:
                raise ValueError("ZIP does not contain a .txt/.tsv/.csv table.")
            target = cand[0]
            with zf.open(target) as zfh:
                try:
                    df = pd.read_csv(zfh, sep="\t", dtype=str, low_memory=False)
                except Exception:
                    zfh.close()
                    with zf.open(target) as zfh2:
                        df = pd.read_csv(zfh2, sep=",", dtype=str, low_memory=False)

        df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed:")]
        return df

    # GZIP container
    if lower.endswith(".gz"):
        try:
            df = pd.read_csv(path, sep="\t", dtype=str, low_memory=False, compression="gzip")
        except Exception:
            df = pd.read_csv(path, sep=",", dtype=str, low_memory=False, compression="gzip")
        df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed:")]
        return df

    # Plain text table
    try:
        df = pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
    except Exception:
        df = pd.read_csv(path, sep=",", dtype=str, low_memory=False)

    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed:")]
    return df


def _read_table_input(table_input: Any) -> pd.DataFrame:
    """
    Read table either from bytes (old FileInput workflow) or from local path.
    """
    if isinstance(table_input, (str, os.PathLike)):
        return _read_path_table(os.fspath(table_input))
    return _read_bytes_table(table_input)

def _ensure_cols(df: pd.DataFrame, cols: Sequence[str], label: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in {label}: {missing}")


def _load_polygon(polygon_source: Any, filename_hint: str) -> "gpd.GeoDataFrame":
    """
    Load polygon from a local path or bytes. Supports:
    - zipped shapefile (.zip)
    - GeoJSON / JSON
    Returns GeoDataFrame in EPSG:4326.
    """
    if gpd is None:
        raise ImportError("geopandas is required for polygon operations.")

    if isinstance(polygon_source, (str, os.PathLike)):
        path = os.fspath(polygon_source)
        if not os.path.exists(path):
            raise ValueError(f"Polygon file does not exist: {path}")
        if not os.path.isfile(path):
            raise ValueError(f"Polygon path is not a file: {path}")

        lower = path.lower()
        if lower.endswith(".zip"):
            with tempfile.TemporaryDirectory() as td:
                with zipfile.ZipFile(path, "r") as zf:
                    zf.extractall(td)

                shp = None
                for root, _dirs, files in os.walk(td):
                    for fn in files:
                        if fn.lower().endswith(".shp"):
                            shp = os.path.join(root, fn)
                            break
                    if shp:
                        break
                if not shp:
                    raise ValueError("Polygon ZIP does not contain a .shp file.")
                poly = gpd.read_file(shp)
        else:
            poly = gpd.read_file(path)
    else:
        polygon_bytes = polygon_source
        with tempfile.TemporaryDirectory() as td:
            if (filename_hint or "").lower().endswith(".zip") or zipfile.is_zipfile(io.BytesIO(polygon_bytes)):
                zp = os.path.join(td, "poly.zip")
                with open(zp, "wb") as f:
                    f.write(polygon_bytes)
                with zipfile.ZipFile(zp, "r") as zf:
                    zf.extractall(td)

                shp = None
                for root, _dirs, files in os.walk(td):
                    for fn in files:
                        if fn.lower().endswith(".shp"):
                            shp = os.path.join(root, fn)
                            break
                    if shp:
                        break
                if not shp:
                    raise ValueError("Polygon ZIP does not contain a .shp file.")
                poly = gpd.read_file(shp)
            else:
                fp = os.path.join(td, "poly.geojson")
                with open(fp, "wb") as f:
                    f.write(polygon_bytes)
                poly = gpd.read_file(fp)

    if poly.empty:
        raise ValueError("Polygon contains no features.")

    if poly.crs is None:
        poly = poly.set_crs("EPSG:4326")
    else:
        poly = poly.to_crs("EPSG:4326")

    return poly

def _load_bbox_polygon(bbox: Sequence[float]) -> "gpd.GeoDataFrame":
    """
    Build polygon GeoDataFrame from bbox:
    (west, south, east, north) in EPSG:4326.
    """
    if gpd is None or box is None:
        raise ImportError("geopandas + shapely are required for bbox operations.")

    if bbox is None or len(bbox) != 4:
        raise ValueError("BBox must contain exactly 4 values: west, south, east, north.")

    west, south, east, north = [float(v) for v in bbox]

    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("Invalid bbox: longitude must be between -180 and 180.")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("Invalid bbox: latitude must be between -90 and 90.")
    if west >= east:
        raise ValueError("Invalid bbox: west must be smaller than east.")
    if south >= north:
        raise ValueError("Invalid bbox: south must be smaller than north.")

    geom = box(west, south, east, north)
    return gpd.GeoDataFrame({"name": ["bbox_region"]}, geometry=[geom], crs="EPSG:4326")


def _resolve_spatial_filter(
    polygon_bytes: Optional[Any] = None,
    polygon_filename_hint: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
) -> "gpd.GeoDataFrame":
    """
    Resolve spatial filter source into a single polygon GeoDataFrame in EPSG:4326.

    Exactly one of:
     - polygon_bytes/path
    - bbox
    must be provided.
    """
    has_polygon = bool(polygon_bytes)
    has_bbox = bbox is not None

    if has_polygon and has_bbox:
        raise ValueError("Provide either polygon_bytes or bbox, not both.")
    if not has_polygon and not has_bbox:
        raise ValueError("Provide either polygon_bytes or bbox.")

    if has_polygon:
        return _load_polygon(polygon_bytes, polygon_filename_hint or "")
    return _load_bbox_polygon(bbox)



def _parse_obs_datetime(df: pd.DataFrame) -> pd.Series:
    """
    Parse timestamp from OBSERVATION DATE and TIME OBSERVATIONS STARTED.
    """
    d_str = df["OBSERVATION DATE"].fillna("").astype(str).str.strip()
    t_str = df.get("TIME OBSERVATIONS STARTED", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()
    dt_full = np.where(t_str != "", d_str + " " + t_str, d_str)
    return pd.to_datetime(dt_full, errors="coerce")


def _parse_counts(df: pd.DataFrame, treat_x_as_one: bool) -> pd.Series:
    """
    Parse OBSERVATION COUNT as numeric; supports 'X' for unknown counts.
    """
    raw = df["OBSERVATION COUNT"].fillna("").astype(str).str.strip().str.upper()
    if treat_x_as_one:
        raw = raw.replace({"X": "1"})
    num = pd.to_numeric(raw, errors="coerce")
    return num


def _normalize_protocol_values(values: Optional[List[str]]) -> Optional[set]:
    """
    Normalize protocol values for matching.
    """
    if not values:
        return None
    return {str(v).strip() for v in values if str(v).strip()}


def _apply_vetting(m: pd.DataFrame, vet: VettingOptions) -> pd.DataFrame:
    """
    Apply vetting filters to merged observations+sampling dataframe.
    """
    out = m.copy()

    # REVIEWED / APPROVED / ALL SPECIES REPORTED (AND logic if multiple are True)
    if vet.require_reviewed and "REVIEWED" in out.columns:
        out = out[_truthy(out["REVIEWED"])]

    if vet.require_approved and "APPROVED" in out.columns:
        out = out[_truthy(out["APPROVED"])]

    if vet.require_all_species_reported and "ALL SPECIES REPORTED" in out.columns:
        out = out[_truthy(out["ALL SPECIES REPORTED"])]

    # Protocol filtering (optional)
    allowed = _normalize_protocol_values(vet.allowed_protocols)

    if allowed:
        allowed_norm = {str(a).strip() for a in allowed if str(a).strip()}

        # 1) PROTOCOL TYPE (preferred)
        if "PROTOCOL TYPE" in out.columns:
            out = out[out["PROTOCOL TYPE"].astype(str).str.strip().isin(allowed_norm)]

        # 2) PROTOCOL NAME (common in sampling file)
        elif "PROTOCOL NAME" in out.columns:
            out = out[out["PROTOCOL NAME"].astype(str).str.strip().isin(allowed_norm)]

        # 3) OBSERVATION TYPE (common in EBD)
        elif "OBSERVATION TYPE" in out.columns:
            out = out[out["OBSERVATION TYPE"].astype(str).str.strip().isin(allowed_norm)]

        # 4) Fallback to PROTOCOL CODE only if UI supplies codes
        elif "PROTOCOL CODE" in out.columns:
            allowed_u = {a.upper() for a in allowed_norm}
            out = out[out["PROTOCOL CODE"].astype(str).str.strip().str.upper().isin(allowed_u)]


    # Exclude incidental/historical (optional)
    if vet.exclude_incidental_historical and "PROTOCOL TYPE" in out.columns:
        bad = {"Incidental", "Historical"}
        out = out[~out["PROTOCOL TYPE"].astype(str).str.strip().isin(bad)]

    # Duration bounds (optional)
    if "DURATION MINUTES" in out.columns:
        dur = pd.to_numeric(out["DURATION MINUTES"], errors="coerce")
        out = out[(dur.isna()) | ((dur >= vet.duration_min_minutes) & (dur <= vet.duration_max_minutes))]

    # Distance bounds (optional)
    if "EFFORT DISTANCE KM" in out.columns:
        dist = pd.to_numeric(out["EFFORT DISTANCE KM"], errors="coerce")
        out = out[(dist.isna()) | ((dist >= vet.distance_min_km) & (dist <= vet.distance_max_km))]

    # Coordinates
    if vet.require_valid_coords:
        out = out[out["latitude"].notna() & out["longitude"].notna()]

    # Require valid timestamp
    out = out[out["__dt"].notna()]
    return out

def _write_manifest(path: str, payload: Dict[str, Any]) -> None:
    """
    Write JSON manifest to disk.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def _assign_n_day_bins(
    datetimes: pd.Series,
    start_date: dt.date,
    end_date: dt.date,
    step_days: int,
) -> pd.DataFrame:
    """
    Assign each datetime to an N-day bin starting from start_date.

    Returns a DataFrame with:
    - time_bin_start
    - time_bin_end

    Both are strings in YYYY-MM-DD format.
    """
    if step_days < 1:
        raise ValueError("step_days must be >= 1.")

    ts = pd.to_datetime(datetimes, errors="coerce")
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)

    day_offsets = (ts.dt.normalize() - start_ts).dt.days
    bin_index = (day_offsets // step_days).astype("Int64")

    bin_start = start_ts + pd.to_timedelta(bin_index * step_days, unit="D")
    bin_end = bin_start + pd.to_timedelta(step_days - 1, unit="D")
    bin_end = bin_end.where(bin_end <= end_ts, end_ts)

    return pd.DataFrame(
        {
            "time_bin_start": bin_start.dt.strftime("%Y-%m-%d"),
            "time_bin_end": bin_end.dt.strftime("%Y-%m-%d"),
        },
        index=datetimes.index,
    )

def _assign_grid_nodes(
    df: pd.DataFrame,
    grid_step_deg: float,
    origin_west: float,
    origin_south: float,
) -> pd.DataFrame:
    """
    Assign observations to regular lon/lat grid nodes.

    Grid nodes are anchored at (origin_west, origin_south) and repeated every
    grid_step_deg degrees.

    Each observation is assigned to exactly one nearest node, equivalent to
    belonging to the square cell:
    - lon_node ± 0.5 * grid_step_deg
    - lat_node ± 0.5 * grid_step_deg

    Returns a copy of df with:
    - grid_lon
    - grid_lat
    """
    if grid_step_deg <= 0:
        raise ValueError("grid_step_deg must be > 0 for grid assignment.")

    out = df.copy()

    lon_offset = (out["longitude"] - origin_west) / grid_step_deg
    lat_offset = (out["latitude"] - origin_south) / grid_step_deg

    out["grid_lon"] = origin_west + np.round(lon_offset) * grid_step_deg
    out["grid_lat"] = origin_south + np.round(lat_offset) * grid_step_deg

    out["grid_lon"] = out["grid_lon"].astype(float)
    out["grid_lat"] = out["grid_lat"].astype(float)

    return out

def _safe_divide(num: pd.Series, den: pd.Series) -> pd.Series:
    """
    Safe division returning NaN when denominator is zero or missing.
    """
    n = pd.to_numeric(num, errors="coerce")
    d = pd.to_numeric(den, errors="coerce")
    return n / d.where(d > 0)

def aggregate_ebird_to_files(
    *,
    ebd_bytes: Any,
    sampling_bytes: Any,
    polygon_bytes: Optional[bytes] = None,
    polygon_filename_hint: str = "",
    bbox: Optional[Sequence[float]] = None,
    ebd_filename_hint: str = "",
    sampling_filename_hint: str = "",
    region_id: str,
    agg: AggregationOptions,
    vet: VettingOptions,
    out_counts_csv: str,
    out_presence_csv: str,
    manifest_json: Optional[str] = None,
) -> List[str]:
    """
    Read EBD + Sampling Event data, apply vetting and spatial filters, and aggregate in N-day bins and by species.
    Outputs:
    - counts CSV (A): time_bin_start, time_bin_end, location-lat, location-long, species,
      total_count, n_checklists, n_checklists_all, n_complete_checklists,
      n_detected_complete_checklists, sum_duration_hours_complete,
      sum_party_hours_complete, reporting_rate, count_per_complete_checklist,
      count_per_hour, count_per_party_hour_complete,
      mean_count_when_detected, region_id
    - presence CSV (B): time_bin_start, time_bin_end, location-lat, location-long, species,
      presence, n_checklists, n_checklists_all, n_complete_checklists,
      n_detected_complete_checklists, reporting_rate, region_id
    Returns:
    - sorted list of unique species found in the counts output.
    """
    if gpd is None or Point is None:
        raise ImportError("geopandas + shapely are required for polygon operations.")
    if agg.step_days < 1:
        raise ValueError("Aggregation step_days must be >= 1.")
    if agg.grid_step_deg < 0:
        raise ValueError("Aggregation grid_step_deg must be >= 0.")

    obs = _read_table_input(ebd_bytes)
    samp = _read_table_input(sampling_bytes)

    _ensure_cols(
        obs,
        [
            "SAMPLING EVENT IDENTIFIER",
            "LATITUDE",
            "LONGITUDE",
            "OBSERVATION DATE",
            "SCIENTIFIC NAME",
            "COMMON NAME",
            "OBSERVATION COUNT",
        ],
        "EBD observations",
    )
    _ensure_cols(samp, ["SAMPLING EVENT IDENTIFIER"], "Sampling events")

    key = "SAMPLING EVENT IDENTIFIER"
    merged = obs.merge(
        samp.drop_duplicates(subset=[key]),
        on=key,
        how="left",
        suffixes=("", "_samp"),
    )

    merged["__dt"] = _parse_obs_datetime(merged)
    merged["latitude"] = pd.to_numeric(merged["LATITUDE"], errors="coerce")
    merged["longitude"] = pd.to_numeric(merged["LONGITUDE"], errors="coerce")

    m = _apply_vetting(merged, vet)

    poly = _resolve_spatial_filter(
        polygon_bytes=polygon_bytes,
        polygon_filename_hint=polygon_filename_hint,
        bbox=bbox,
    )
    poly_union = poly.dissolve().geometry.iloc[0]

    minx, miny, maxx, maxy = poly.total_bounds
    if bbox is not None:
        origin_west, origin_south = float(bbox[0]), float(bbox[1])
    else:
        origin_west, origin_south = float(minx), float(miny)

    gdf = gpd.GeoDataFrame(
        m,
        geometry=[Point(xy) for xy in zip(m["longitude"], m["latitude"])],
        crs="EPSG:4326",
    )
    gdf = gdf[gdf.intersects(poly_union)].drop(columns=["geometry"])
    m = pd.DataFrame(gdf)

    # 2) Start/End date limits (inclusive)
    start_dt = pd.Timestamp(agg.start_date)
    end_dt = pd.Timestamp(agg.end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    m = m[(m["__dt"] >= start_dt) & (m["__dt"] <= end_dt)]

    # Parse counts
    cnt = _parse_counts(m, treat_x_as_one=agg.treat_x_as_one)
    # If count is missing (including treat_x_as_one=False), default to 1.0 for presence-like behavior
    m["__count"] = cnt.fillna(1.0)

    # Optional clip
    if vet.clip_counts_above and vet.clip_counts_above > 0:
        m["__count"] = m["__count"].clip(upper=vet.clip_counts_above)

    # Species label for aggregation
    m["species"] = m["SCIENTIFIC NAME"].fillna(m["COMMON NAME"]).astype(str)

    # Time binning: fixed-size bins in N days, anchored at agg.start_date
    bins = _assign_n_day_bins(
        m["__dt"],
        start_date=agg.start_date,
        end_date=agg.end_date,
        step_days=agg.step_days,
    )
    m["time_bin_start"] = bins["time_bin_start"]
    m["time_bin_end"] = bins["time_bin_end"]

        # Spatial aggregation:
    # - grid_step_deg == 0: keep original observation coordinates
    # - grid_step_deg > 0: assign to regular grid nodes and aggregate by node
    if agg.grid_step_deg > 0:
        m = _assign_grid_nodes(
            m,
            grid_step_deg=agg.grid_step_deg,
            origin_west=origin_west,
            origin_south=origin_south,
        )
        loc_lat_col = "grid_lat"
        loc_lon_col = "grid_lon"
    else:
        loc_lat_col = "latitude"
        loc_lon_col = "longitude"

    # Common grouping keys
    spatial_time_keys = ["time_bin_start", "time_bin_end", loc_lat_col, loc_lon_col]
    species_keys = spatial_time_keys + ["species"]

    # Complete checklist flag
    if "ALL SPECIES REPORTED" in m.columns:
        m["__complete_checklist"] = _truthy(m["ALL SPECIES REPORTED"])
    else:
        m["__complete_checklist"] = False

    # Duration in hours
    if "DURATION MINUTES" in m.columns:
        m["__duration_hours"] = pd.to_numeric(m["DURATION MINUTES"], errors="coerce") / 60.0
    else:
        m["__duration_hours"] = np.nan
    # Number of observers
    if "NUMBER OBSERVERS" in m.columns:
        m["__n_observers"] = pd.to_numeric(m["NUMBER OBSERVERS"], errors="coerce")
    else:
        m["__n_observers"] = np.nan
    # ------------------------------------------------------------------
    # 1) Denominator table from unique checklists at time_bin + spatial unit
    # ------------------------------------------------------------------
    checklist_cols = [
        key,
        "time_bin_start",
        "time_bin_end",
        loc_lat_col,
        loc_lon_col,
        "__complete_checklist",
        "__duration_hours",
        "__n_observers",
    ]
    checklist_frame = m[checklist_cols].drop_duplicates(subset=[key])

    checklist_frame["__duration_hours_complete_only"] = checklist_frame["__duration_hours"].where(
        checklist_frame["__complete_checklist"],
        np.nan,
    )

    checklist_frame["__party_hours"] = checklist_frame["__duration_hours"] * checklist_frame["__n_observers"]
    checklist_frame["__party_hours_complete_only"] = checklist_frame["__party_hours"].where(
        checklist_frame["__complete_checklist"],
        np.nan,
    )

    denom = (
        checklist_frame
        .groupby(spatial_time_keys, dropna=False)
        .agg(
            n_checklists_all=(key, pd.Series.nunique),
            n_complete_checklists=("__complete_checklist", "sum"),
            sum_duration_hours_complete=("__duration_hours_complete_only", "sum"),
            sum_party_hours_complete=("__party_hours_complete_only", "sum"),
        )
        .reset_index()
    )

    # ------------------------------------------------------------------
    # 2) Species table from detections
    # ------------------------------------------------------------------
    grp = m.groupby(species_keys, dropna=False)

    counts = grp.agg(
        total_count=("__count", "sum"),
        n_checklists=(key, pd.Series.nunique),
    ).reset_index()

    pres = grp.agg(
        presence=("__count", lambda x: 1),
        n_checklists=(key, pd.Series.nunique),
    ).reset_index()

    # ------------------------------------------------------------------
    # 3) Species table from detections on complete checklists only
    # ------------------------------------------------------------------
    detected_complete = m[m["__complete_checklist"]].copy()

    if len(detected_complete) > 0:
        grp_complete = detected_complete.groupby(species_keys, dropna=False)
        det_complete = grp_complete.agg(
            n_detected_complete_checklists=(key, pd.Series.nunique),
        ).reset_index()
    else:
        det_complete = pd.DataFrame(columns=species_keys + ["n_detected_complete_checklists"])

    # ------------------------------------------------------------------
    # 4) Join denominator + derived metrics
    # ------------------------------------------------------------------
    counts = counts.merge(denom, on=spatial_time_keys, how="left")
    counts = counts.merge(det_complete, on=species_keys, how="left")
    counts["n_detected_complete_checklists"] = counts["n_detected_complete_checklists"].fillna(0)

    counts["reporting_rate"] = _safe_divide(
        counts["n_detected_complete_checklists"],
        counts["n_complete_checklists"],
    )
    counts["count_per_complete_checklist"] = _safe_divide(
        counts["total_count"],
        counts["n_complete_checklists"],
    )
    counts["count_per_hour"] = _safe_divide(
        counts["total_count"],
        counts["sum_duration_hours_complete"],
    )
    counts["count_per_party_hour_complete"] = _safe_divide(
        counts["total_count"],
        counts["sum_party_hours_complete"],
    )
    counts["mean_count_when_detected"] = _safe_divide(
        counts["total_count"],
        counts["n_checklists"],
    )

    counts["region_id"] = region_id
    counts = counts.rename(columns={loc_lat_col: "location-lat", loc_lon_col: "location-long"})

    pres = pres.merge(denom, on=spatial_time_keys, how="left")
    pres = pres.merge(det_complete, on=species_keys, how="left")
    pres["n_detected_complete_checklists"] = pres["n_detected_complete_checklists"].fillna(0)

    pres["reporting_rate"] = _safe_divide(
        pres["n_detected_complete_checklists"],
        pres["n_complete_checklists"],
    )
    pres["count_per_complete_checklist"] = np.nan
    pres["count_per_hour"] = np.nan
    pres["count_per_party_hour_complete"] = np.nan
    pres["mean_count_when_detected"] = np.nan

    pres["region_id"] = region_id
    pres = pres.rename(columns={loc_lat_col: "location-lat", loc_lon_col: "location-long"})

    os.makedirs(os.path.dirname(os.path.abspath(out_counts_csv)), exist_ok=True)
    counts.to_csv(out_counts_csv, index=False, encoding="utf-8")

    os.makedirs(os.path.dirname(os.path.abspath(out_presence_csv)), exist_ok=True)
    pres.to_csv(out_presence_csv, index=False, encoding="utf-8")

    if manifest_json:
        if bbox is not None:
            west, south, east, north = [float(v) for v in bbox]
            spatial_filter = {
                "type": "bbox",
                "west": west,
                "south": south,
                "east": east,
                "north": north,
            }
        else:
            spatial_filter = {
                "type": "polygon",
                "filename_hint": polygon_filename_hint or "",
            }

        payload: Dict[str, Any] = {
            "created_at": dt.datetime.now().isoformat(),
            "region_id": region_id,
            "source_mode": "EBD + Sampling Event",
            "spatial_filter": spatial_filter,
            "time": {
                "start": str(agg.start_date),
                "end": str(agg.end_date),
                "step_days": int(agg.step_days),
            },
            "grid": {
                "grid_step_deg": float(agg.grid_step_deg),
                "origin_west": float(origin_west),
                "origin_south": float(origin_south),
                "mode": "grid" if agg.grid_step_deg > 0 else "original_coordinates",
            },
            "derived_metrics": [
                "reporting_rate",
                "count_per_complete_checklist",
                "n_complete_checklists",
                "count_per_hour",
                "count_per_party_hour_complete",
                "mean_count_when_detected",
            ],
            "vetting": vet.__dict__,
            "outputs": {
                "agg_counts_csv": out_counts_csv,
                "agg_presence_csv": out_presence_csv,
            },
        }
        _write_manifest(manifest_json, payload)

    # 3) Species list for UI
    species_list = sorted(counts["species"].dropna().astype(str).unique().tolist())
    return species_list


def read_species_from_agg_counts(agg_counts_csv: str) -> List[str]:
    """
    Read unique species list from aggregated counts CSV.
    """
    if not os.path.exists(agg_counts_csv):
        return []
    df = pd.read_csv(agg_counts_csv, usecols=["species"])
    return sorted(df["species"].dropna().astype(str).unique().tolist())


def export_tracks_from_aggregated_counts(
    *,
    agg_counts_csv: str,
    tracks_csv: str,
    region_id: str,
    id_mode: str = "species",
    species_filter: Optional[List[str]] = None,
) -> None:
    """
    Convert aggregated counts file into Movebank-like pseudo-tracks CSV for ECODATA-Animate.

    If species_filter is provided and non-empty, export only those species.

    Output columns:
    - timestamp
    - location-long
    - location-lat
    - individual-local-identifier
    - species
    - count
    - bin_id
    - region_id
    """
    if not os.path.exists(agg_counts_csv):
        raise FileNotFoundError(f"Aggregated counts file not found: {agg_counts_csv}")

    df = pd.read_csv(agg_counts_csv)

    if species_filter:
        keep = {str(s).strip() for s in species_filter if str(s).strip()}
        if keep:
            df = df[df["species"].astype(str).isin(keep)]

    ts = pd.to_datetime(df["time_bin_start"], errors="coerce")
    df["timestamp"] = ts.dt.strftime("%Y-%m-%dT%H:%M:%S")
    df["bin_id"] = df["time_bin_start"].astype(str)

    if id_mode == "species|region":
        df["individual-local-identifier"] = df["species"].astype(str) + "|region:" + str(region_id)
    else:
        df["individual-local-identifier"] = df["species"].astype(str)

    out = pd.DataFrame(
    {
        "timestamp": df["timestamp"],
        "location-long": df["location-long"],
        "location-lat": df["location-lat"],
        "individual-local-identifier": df["individual-local-identifier"],
        "species": df["species"],
        "count": df.get("total_count", 1),
        "bin_id": df["bin_id"],
        "region_id": region_id,

        "total_count": df.get("total_count"),
        "n_checklists": df.get("n_checklists"),
        "n_checklists_all": df.get("n_checklists_all"),
        "n_complete_checklists": df.get("n_complete_checklists"),
        "n_detected_complete_checklists": df.get("n_detected_complete_checklists"),
        "sum_duration_hours_complete": df.get("sum_duration_hours_complete"),
        "sum_party_hours_complete": df.get("sum_party_hours_complete"),
        "reporting_rate": df.get("reporting_rate"),
        "count_per_complete_checklist": df.get("count_per_complete_checklist"),
        "count_per_hour": df.get("count_per_hour"),
        "count_per_party_hour_complete": df.get("count_per_party_hour_complete"),
        "mean_count_when_detected": df.get("mean_count_when_detected"),
    }
)

    os.makedirs(os.path.dirname(os.path.abspath(tracks_csv)), exist_ok=True)
    out.to_csv(tracks_csv, index=False, encoding="utf-8")
