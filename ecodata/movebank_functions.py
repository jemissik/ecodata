"""
movebank_functions.py

Processes Movebank CSV datasets using "timestamp" or "eobs:start-timestamp" column, 
filter data by "individual-taxon-canonical-name" and "individual-local-identifier".

Interpolation is always performed first using a 1-minute interval. This produces a regularly spaced time series.

After interpolation, optional averaging is performed over a user-defined interval (e.g. 30 minutes). 
Only numeric columns (such as 'eobs:temperature', 'ground-speed', 'height-above-ellipsoid') are averaged.

All non-numeric columns (e.g. metadata or identifiers) are forward-filled from the last known value 
during interpolation and retained without modification during averaging.
"""

import csv
from datetime import datetime, timedelta
import pandas as pd
from pathlib import Path
import numpy as np
import re

TIME_COLUMN = 'timestamp' # Set to "eobs:start-timestamp" or "timestamp" as needed

def parse_timestamp(s: str) -> datetime:
    """
    Robust timestamp parser:
    - Keeps backward compatibility with ISO-like strings: 'YYYY-MM-DD HH:MM:SS[.ffffff]'
    - Supports day-first formats: 'DD.MM.YYYY HH:MM', 'DD.MM.YYYY HH:MM:SS[.ffffff]'
    - Accepts 'T' separator and 'Z' / timezone offsets (drops tzinfo → naive)
    - Pads/truncates fractional seconds to 6 digits when present
    """
    if s is None:
        raise ValueError("Timestamp is None")

    s = str(s).strip()
    if not s:
        raise ValueError("Empty timestamp")

    # --- Fast path: ISO with optional 'Z' or offset ---
    # Example: 2020-01-02T03:04:05.123Z, 2020-01-02 03:04:05.123456+02:00
    iso_candidate = s
    if iso_candidate.endswith("Z"):
        iso_candidate = iso_candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso_candidate.replace("T", " "))
        # Drop tzinfo to keep backward-compatible naive datetimes
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        pass

    # --- Normalize fractional seconds to <= 6 digits (microseconds) ---
    # Works for both 'YYYY-MM-DD ...' and 'DD.MM.YYYY ...'
    def _normalize_frac(text: str) -> str:
        # split timezone if any to avoid touching the offset part
        tz_match = re.search(r'([+-]\d{2}:\d{2}|[+-]\d{4})$', text)
        tz = tz_match.group(0) if tz_match else ""
        core = text[: -len(tz)] if tz else text

        if '.' in core:
            head, frac = core.split('.', 1)
            # cut off any trailing timezone-like part accidentally captured
            frac = re.split(r'([+-]\d{2}:\d{2}|[+-]\d{4})', frac)[0]
            frac = re.sub(r'\D', '', frac)  # keep only digits
            if len(frac) > 6:
                frac = frac[:6]
            elif 0 < len(frac) < 6:
                frac = frac.ljust(6, '0')
            core = f"{head}.{frac}"
        return core + tz

    s_norm = _normalize_frac(s)

    # --- Try explicit known formats (old + new) ---
    fmts = [
        # legacy ISO-like (kept first for backward compatibility)
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        # day-first variants
        "%d.%m.%Y %H:%M:%S.%f",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        # allow 'T' separator explicitly
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(s_norm, fmt)
        except Exception:
            continue

    # --- Last resort: pandas inference (dayfirst=True) ---
    dt = pd.to_datetime(s_norm, dayfirst=True, errors="coerce", utc=False)
    if pd.isna(dt):
        raise ValueError(f"Unparsable timestamp: {s}")
    # Convert pandas Timestamp to naive datetime (drop tz if any)
    py_dt = dt.to_pydatetime()
    if hasattr(py_dt, "tzinfo") and py_dt.tzinfo is not None:
        py_dt = py_dt.replace(tzinfo=None)
    return py_dt


def safe_float(value):
    """Safely converts a value to float.
    Handles None, empty strings, and strips whitespace.

    Args:
        value (str or float): Input value.

    Returns:
        float or None: Parsed float or None if conversion fails.
    """
    if isinstance(value, float) or value is None:
        return value
    try:
        return float(value.strip()) if value.strip() else None
    except ValueError:
        return None

# --- Interpolation ---
def interpolate_points(start, end, interval, columns_to_interpolate):
    """Generates linearly interpolated points between two observations.

    Args:
        start (dict): The first observation row.
        end (dict): The second observation row.
        interval (timedelta): Interval at which to interpolate (e.g. 1 minute).
        columns_to_interpolate (list): List of column names to interpolate.

    Returns:
        list: A list of interpolated rows (dicts) between start and end.
    """

    start_time = parse_timestamp(start["timestamp"])
    end_time = parse_timestamp(end["timestamp"])

    if start_time >= end_time:
        return []

    total_seconds = (end_time - start_time).total_seconds()
    step_seconds = interval.total_seconds()
    steps = int(total_seconds // step_seconds)

    if steps < 1:
        return []

    timestamps = [
        (start_time + timedelta(seconds=i * step_seconds)).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
        for i in range(1, steps + 1)
    ]

    alphas = np.linspace(1 / steps, 1.0, num=steps)

    interpolated_rows = []
    for idx, alpha in enumerate(alphas):
        point = dict(start)
        point["timestamp"] = timestamps[idx]
        for col in columns_to_interpolate:
            v_start = safe_float(start.get(col))
            v_end = safe_float(end.get(col))
            if v_start is not None and v_end is not None:
                point[col] = v_start + alpha * (v_end - v_start)
            else:
                point[col] = None
        interpolated_rows.append(point)

    return interpolated_rows

# --- Fill Missing Data ---
def fill_missing_data(data):
    """Fill missing lon/lat via linear interpolation between bounding points.
    Uses actual lon/lat column names resolved from the data header.
    Writes results back into the *same* lon/lat columns.
    """
    if not data:
        return data

    # derive header from the first row and resolve actual lon/lat keys
    fieldnames = list(data[0].keys())
    lon_key, lat_key = resolve_lon_lat_keys(fieldnames)
    id_key_in = resolve_id_key(fieldnames)
    # if cannot resolve — nothing to do safely
    if not lon_key or not lat_key:
        return data

    i = 0
    while i < len(data):
        # seek a block of rows where either lon or lat is missing
        if data[i].get(lon_key) is None or data[i].get(lat_key) is None:
            start_idx = i - 1
            while i < len(data) and (data[i].get(lon_key) is None or data[i].get(lat_key) is None):
                i += 1
            end_idx = i

            # interpolate only if both ends exist
            if 0 <= start_idx < len(data) and end_idx < len(data):
                start = data[start_idx]
                end   = data[end_idx]
                start_time = parse_timestamp(start["timestamp"])
                end_time   = parse_timestamp(end["timestamp"])
                total_seconds = (end_time - start_time).total_seconds() or 0.0
                if total_seconds <= 0:
                    continue

                for j in range(start_idx + 1, end_idx):
                    current_time = parse_timestamp(data[j]["timestamp"])
                    alpha = (current_time - start_time).total_seconds() / total_seconds
                    if start.get(lon_key) is not None and end.get(lon_key) is not None:
                        data[j][lon_key] = start[lon_key] + alpha * (end[lon_key] - start[lon_key])
                    if start.get(lat_key) is not None and end.get(lat_key) is not None:
                        data[j][lat_key] = start[lat_key] + alpha * (end[lat_key] - start[lat_key])
        else:
            i += 1

    return data

# --- Averaging ---
def average_by_time_interval(data, interval, columns_to_interpolate, actual_start_time, actual_end_time, allow_single=True):
    """Averages numeric values over fixed time intervals.

    Args:
        data (list of dict): Interpolated time series.
        interval (timedelta): Averaging interval (e.g. 30 minutes).
        columns_to_interpolate (list): Numeric columns to average.
        actual_start_time (datetime): Start of valid time window.
        actual_end_time (datetime): End of valid time window.
        allow_single (bool): Whether to keep single-record intervals.

    Returns:
        list of dict: Averaged records by time interval.
    """

    if not data:
        return []

    df = pd.DataFrame(data).copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce')
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp")

    interval_minutes = int(interval.total_seconds() // 60)
    df["interval_start"] = df["timestamp"].dt.floor(f"{interval_minutes}T")
    grouped = df.groupby("interval_start")

    numeric_cols = []
    for col in columns_to_interpolate:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            numeric_cols.append(col)

    result = grouped[numeric_cols].mean() if numeric_cols else pd.DataFrame(index=grouped.size().index)
    metadata_cols = [col for col in df.columns if col not in numeric_cols + ["timestamp", "interval_start"]]
    for col in metadata_cols:
        result[col] = grouped[col].first()

    result = result.reset_index()
    result = result.rename(columns={"interval_start": "timestamp"})

    if not allow_single:
        group_sizes = grouped.size()
        valid_groups = group_sizes[group_sizes > 1].index
        result = result[result["timestamp"].isin(valid_groups)]

    result["timestamp"] = result["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S.%f").str[:23]
    return result.to_dict(orient="records")

# --- Validation ---
def validate_and_process_csv(file_path):
    """
    Inspect a Movebank CSV header and return a list of ORIGINAL column names
    that are suitable for interpolation/averaging.

    - Robust to header variations: '-', '_', '.', ':' are treated equally.
    - Picks synonyms for lon/lat and common numeric fields (e.g., eobs:temperature).
    - Time/ID columns are detected but EXCLUDED from the returned list.
    - Returns ORIGINAL header names (exactly as in the file).

    Returns
    -------
    list[str]
        Ordered list of present columns to be used as numeric candidates for
        interpolation/averaging (e.g., ['location_lon', 'location_lat', 'eobs:temperature', ...]).
    """

    def _norm(s: str) -> str:
        # normalize header keys: "EOBS:Temperature" -> "eobs_temperature"
        return re.sub(r"[-:.\s]+", "_", str(s).lower()).strip("_")

    # 1) read header
    try:
        with open(Path(file_path), "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            raw_fields = reader.fieldnames or []
    except Exception as e:
        print(f"[validate_and_process_csv] Failed to read header: {e}")
        return []

    if not raw_fields:
        return []

    # 2) build normalized->original map
    norm_to_orig = {}
    for col in raw_fields:
        nk = _norm(col)
        # keep the first occurrence to preserve a stable, human header where possible
        if nk not in norm_to_orig:
            norm_to_orig[nk] = col

    present = set(norm_to_orig.keys())

    # 3) define synonym groups
    time_syns = [
        "timestamp", "eobs_start_timestamp", "eobs:start-timestamp",
        "datetime", "date_time", "date", "time"
    ]
    id_syns = [
        "individual_local_identifier", "individual-local-identifier"
    ]
    lon_syns = [
        "location_long", "location_lon", "location-long", "location-lon",
        "longitude", "lon", "location_longitude", "location.longitude"
    ]
    lat_syns = [
        "location_lat", "location-lat",
        "latitude", "lat", "location_latitude", "location.latitude"
    ]
    # common numeric fields you typically interpolate/average
    temp_syns = ["eobs_temperature", "eobs:temperature", "temperature"]
    gspeed_syns = ["ground_speed", "ground-speed", "speed_2d", "speed"]
    hae_syns = [
        "height_above_ellipsoid", "height-above-ellipsoid",
        "gps_altitude", "altitude", "altitude_above_sea_level"
    ]

    def _pick_first(syns):
        for s in syns:
            nk = _norm(s)
            if nk in present:
                return norm_to_orig[nk]
        return None

    # 4) choose actual originals (if present)
    time_col = _pick_first(time_syns)  # not returned, for info/exclusion only
    id_col   = _pick_first(id_syns)    # not returned

    lon_col  = _pick_first(lon_syns)
    lat_col  = _pick_first(lat_syns)
    temp_col = _pick_first(temp_syns)
    gs_col   = _pick_first(gspeed_syns)
    hae_col  = _pick_first(hae_syns)

    # 5) build the result list (keep a sensible order: coords first)
    result = []
    for c in (lon_col, lat_col, temp_col, gs_col, hae_col):
        if c and c not in result:
            result.append(c)

    # You may also include any additional numeric columns here if you wish:
    # e.g., any column whose normalized name starts with "eobs_" and is present.
    # Just make sure to exclude time/id-like names:
    time_like = {_norm(x) for x in time_syns}
    id_like   = {_norm(x) for x in id_syns}

    for nk, orig in norm_to_orig.items():
        if nk in time_like or nk in id_like:
            continue
        # already included?
        if orig in result:
            continue
        # optional heuristic: include other eobs:* numeric-looking fields
        if nk.startswith("eobs_"):
            result.append(orig)

    return result
    
# --- Main Processing ---
def process_csv_interp_or_averaging(start_time_str, end_time_str, interval_minutes,
                                    csv_file, output_csv, local_identifier,
                                    columns_to_interpolate=None, allow_single=True,
                                    deployment_time_gap=60, min_expected_obs=1,
                                    start_from_midnight=False):
    """Processes a single individual's movement data with interpolation and optional averaging.
    Includes filtering by time and ID, interpolation, averaging, session splitting, and final cleanup.

    Args:
        start_time_str (str): Start datetime string.
        end_time_str (str): End datetime string.
        interval_minutes (int): Time step for averaging.
        csv_file (Path): Path to input Movebank CSV.
        output_csv (str): Output file path template.
        local_identifier (str): Individual ID to process.
        columns_to_interpolate (list): Columns to interpolate.
        allow_single (bool): Keep intervals with one record.
        deployment_time_gap (int): Maximum gap (min) to split sessions.
        min_expected_obs (int): Minimum points required to keep a session.
        start_from_midnight (bool): If True, truncate to 00:00 and start from it.

    Returns:
        list: List of generated CSV file paths.
    """
    if columns_to_interpolate is None:
        columns_to_interpolate = []
    columns_to_interpolate = [col for col in columns_to_interpolate if col not in ("timestamp", "eobs:start-timestamp")]

    start_time = parse_timestamp(start_time_str)
    end_time = parse_timestamp(end_time_str)
    interval = timedelta(minutes=interval_minutes)
    min_interval = timedelta(minutes=1)

    data = []
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        time_key_in = resolve_time_column(fieldnames)
        lon_key, lat_key = resolve_lon_lat_keys(fieldnames)
        id_key_in = resolve_id_key(fieldnames)
        for row in reader:
            try:
                ts_raw = row.get(time_key_in)
                if not ts_raw:
                    continue
                row_time = parse_timestamp(ts_raw)
            except Exception:
                continue

            row_id = row.get(id_key_in) if id_key_in else None
            if row_id is None:
                continue

            row_id_str = str(row_id).strip()
            expected_id_str = str(local_identifier).strip()

            if row_id_str != expected_id_str:
                continue

            if start_time <= row_time <= end_time:   
                row["timestamp"] = ts_raw
                data.append(row)

    if len(data) < 2:
        print("Not enough data after filtering.")
        return []

    data.sort(key=lambda x: parse_timestamp(x["timestamp"]))

    # Cut off at midnight and insert 00:00:00
    if start_from_midnight and data:
        first_time = parse_timestamp(data[0]["timestamp"])
        midnight = first_time.replace(hour=0, minute=0, second=0, microsecond=0)

        # Cut off points to 00:00:00
        data = [row for row in data if parse_timestamp(row["timestamp"]) >= midnight]

        # If there is no exact point 00:00:00 — insert an artificial one
        if data and parse_timestamp(data[0]["timestamp"]) > midnight:
            clone = dict(data[0])
            clone["timestamp"] = midnight.strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
            for col in columns_to_interpolate:
                if col in clone:
                    clone[col] = clone[col] # copy the value from the first real point
            data.insert(0, clone)

        if len(data) < 2:
            print("Not enough data after start_from_midnight filtering.")
            return []

    for col in ["timestamp"] + columns_to_interpolate:
        if col not in fieldnames:
            fieldnames.append(col)

    data = fill_missing_data(data)

    def split_into_sessions(data, max_gap_minutes):
        max_gap = timedelta(minutes=max_gap_minutes)
        sessions = []
        current_session = []

        for i, row in enumerate(data):
            if i == 0:
                current_session.append(row)
                continue

            prev_time = parse_timestamp(data[i-1]['timestamp'])
            curr_time = parse_timestamp(row['timestamp'])
            if curr_time - prev_time > max_gap:
                if current_session:
                    sessions.append(current_session)
                current_session = [row]
            else:
                current_session.append(row)

        if current_session:
            sessions.append(current_session)
        return sessions

    sessions = split_into_sessions(data, deployment_time_gap)
    result_paths = []

    for idx, session in enumerate(sessions):
        if len(session) < min_expected_obs:
            print(f"Skipping session {idx+1} with only {len(session)} observations (less than min_expected_obs={min_expected_obs})")
            continue

        interpolated_rows = []
        for i in range(len(session) - 1):
            interpolated_rows.append(session[i])
            interpolated_rows.extend(interpolate_points(session[i], session[i + 1], min_interval, columns_to_interpolate))
        if session:
            interpolated_rows.append(session[-1])

        if interval.total_seconds() > 60:
            result_rows = average_by_time_interval(
                interpolated_rows, interval, columns_to_interpolate,
                actual_start_time=parse_timestamp(session[0]['timestamp']),
                actual_end_time=parse_timestamp(session[-1]['timestamp']),
                allow_single=allow_single
            )
        else:
            result_rows = interpolated_rows

        start_str = session[0]['timestamp'].replace(":", "-").replace(" ", "T")[:16]
        end_str = session[-1]['timestamp'].replace(":", "-").replace(" ", "T")[:16]
        session_output_path = output_csv.replace(".csv", f"__{start_str}_to_{end_str}.csv")

        with open(session_output_path, "w", newline='', encoding="utf-8") as f:
            if "individual-local-identifier-deployment-time" not in fieldnames:
                fieldnames.append("individual-local-identifier-deployment-time")
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in result_rows:
                ts = row.get("timestamp")
                if ts is None:
                    continue
                row["timestamp"] = str(ts)[:23]
                row["individual-local-identifier-deployment-time"] = Path(session_output_path).stem
                writer.writerow(row)
                result_paths.append(session_output_path)
                try:
                    df_check = normalize_column_names(pd.read_csv(session_output_path, low_memory=False))
                    ...
                    if numeric_cols_to_fix:
                        ...
                        df_check.to_csv(session_output_path, index=False)
                        print(f"Interpolated missing values in: {numeric_cols_to_fix} for file {session_output_path}")
                except Exception as e:
                    print(f"Interpolation post-check failed for {session_output_path}: {e}")

    #  Checking and interpolating NaN after writing
    cols_to_check_for_nan = [
        "timestamp", "location_long", "location_lat",
        "eobs_start_timestamp", "eobs_temperature",
        "ground_speed", "height_above_ellipsoid"
    ]
    if result_paths:  # перевірка, що є створені файли
        last_file = result_paths[-1]
        try:
            df_check = normalize_column_names(pd.read_csv(last_file, low_memory=False))
            numeric_cols_to_fix = [
                col for col in cols_to_check_for_nan
                if col in df_check.columns
                and df_check[col].dtype in ["float64", "int64"]
                and df_check[col].isna().any()
            ]

            if numeric_cols_to_fix:
                df_check["timestamp"] = pd.to_datetime(df_check["timestamp"], errors="coerce")
                df_check = df_check.set_index("timestamp")
                df_check[numeric_cols_to_fix] = df_check[numeric_cols_to_fix].interpolate(
                    method="time", limit_direction="both"
                )
                df_check = df_check.reset_index()
                df_check.to_csv(last_file, index=False)
                print(f"Interpolated missing values in: {numeric_cols_to_fix} for file {last_file}")
        except Exception as e:
            print(f"Interpolation post-check failed for {last_file}: {e}")

    return result_paths

# --- Merging ---
def merge_csv_files_from_folder(folder_path: Path, delete_empty_columns: bool) -> (pd.DataFrame, list):
    """Merges multiple CSV files into one DataFrame.
    Optionally deletes columns that are not shared across files.

    Args:
        folder_path (Path): Directory containing CSV files.
        delete_empty_columns (bool): If True, remove non-overlapping columns.

    Returns:
        tuple: (merged DataFrame, list of removed column names, list of source CSV file paths)
    """
    csv_files = sorted(folder_path.glob("*.csv"))
    if not csv_files:
        raise ValueError("No CSV files found in the selected folder.")
    dataframes = [normalize_column_names(pd.read_csv(f)) for f in csv_files]
    all_columns = set()
    for df in dataframes:
        all_columns.update(df.columns)
    missing_columns = {col for col in all_columns if any(col not in df.columns for df in dataframes)}
    if delete_empty_columns and missing_columns:
        cleaned_dataframes = [df.drop(columns=list(missing_columns), errors='ignore') for df in dataframes]
        merged_df = pd.concat(cleaned_dataframes, ignore_index=True)
    else:
        merged_df = pd.concat(dataframes, ignore_index=True)
    return merged_df, sorted(missing_columns), [str(p) for p in csv_files]

# --- Filename ---
def safe_filename(name: str, replacement: str = "_") -> str:
    """Generates a filesystem-safe filename by replacing invalid characters.

    Args:
        name (str): Original filename string.
        replacement (str): Replacement for invalid characters.

    Returns:
        str: Sanitized filename.
    """
    return re.sub(r'[\\/:*?"<>| ]+', replacement, name).strip()

# --- Batch ---
def generate_individual_csvs_for_local_ids(csv_file: Path, ids: list,
                                         start_time, end_time, interval_minutes: int,
                                         output_path_template: str, columns_to_interpolate: list, 
                                         deployment_time_gap: int = 60,
                                         min_expected_obs: int = 100,
                                         start_from_midnight = False) -> list:
    """Processes movement data for multiple individuals into separate files.

    Calls process_csv_interp_or_averaging for each ID and aggregates results.

    Args:
        csv_file (Path): Input CSV file path.
        ids (list of str): List of local identifiers (tags).
        start_time (str): Start datetime string.
        end_time (str): End datetime string.
        interval_minutes (int): Time step in minutes.
        output_path_template (str): Base output path for naming files.
        columns_to_interpolate (list): Columns for interpolation.
        deployment_time_gap (int): Max gap in minutes to split sessions.
        min_expected_obs (int): Minimum observations per session.
        start_from_midnight (bool): If True, truncate sessions to start at 00:00.

    Returns:
        list: List of output file paths.
    """
    output_files = []
    for id in ids:
        save_name_by_ID = safe_filename(id)
        output = output_path_template.replace(".csv", f"_{save_name_by_ID}.csv")
        result_paths = process_csv_interp_or_averaging(
            start_time_str=start_time,
            end_time_str=end_time,
            interval_minutes=interval_minutes,
            csv_file=csv_file,
            output_csv=output,
            local_identifier=id,
            columns_to_interpolate=columns_to_interpolate,
            deployment_time_gap=deployment_time_gap,
            min_expected_obs=min_expected_obs,
            start_from_midnight=start_from_midnight
        )
        output_files.extend(result_paths)
    return output_files

def interpolate_missing_values_only(start_time_str: str,
                                    end_time_str: str,
                                    csv_file: Path,
                                    ids: list,
                                    columns_to_interpolate: list,
                                    output_path_template: str,
                                    max_gap_minutes: int = 24*60) -> list:
    """
    Fill-in missing numeric values *within existing rows only* (no new rows created),
    using time-based interpolation limited to gaps ≤ `max_gap_minutes` between two
    known observations. Interpolation is performed independently per Individual ID.

    This function is designed for the "Simple interpolation (missing ≤ 1 day)" button:
    - It does NOT build a regular 1-minute timeline.
    - It only fills NaNs that lie strictly between two valid values where the total
      time span between those two values is ≤ `max_gap_minutes`.
    - It preserves original column names and writes timestamps back into the original
      time column (if it exists), otherwise creates one.

    Parameters
    ----------
    start_time_str : str
        Start of the time window (string; parsed by `parse_timestamp`).
    end_time_str : str
        End of the time window (string; parsed by `parse_timestamp`).
    csv_file : Path
        Path to the input Movebank CSV.
    ids : list
        List of `individual-local-identifier` values to process independently.
    columns_to_interpolate : list
        Candidate columns for interpolation (original headers as in CSV).
        Time-like columns (e.g., 'timestamp', 'eobs:start-timestamp') are ignored.
    output_path_template : str
        Template for output CSV path; per-ID files are created by appending
        `_{safe_id}__interp_inplace_le1d.csv` before the ".csv" suffix.
    max_gap_minutes : int, default 24*60
        Maximum allowed gap (in minutes) between two valid values to fill NaNs inside.

    Returns
    -------
    list of str
        Paths to the created per-ID CSV files.

    Notes
    -----
    - Time parsing relies on `parse_timestamp`, which should support both ISO-like
      and 'DD.MM.YYYY HH:MM[:SS[.fff]]' formats (and possibly 'T'/'Z'/offsets).
    - The function matches the time & ID columns via *normalized* header keys,
      but preserves original headers in the written output.
    """

    # --- Helpers (scoped locally to avoid polluting the module namespace) ----------
    def _norm_key(s: str) -> str:
        """Normalize a single header key to a canonical form."""
        return re.sub(r"[-:.\s]+", "_", str(s).lower()).strip("_")

    def _norm_keys(d: dict) -> dict:
        """Normalize all keys in a row (dict) for robust lookup; values unchanged."""
        return {_norm_key(k): v for k, v in d.items()}

    def _pick_time_col_from_df(df: pd.DataFrame) -> str:
        """
        Choose which original column in df should store timestamps in the output.
        Preference order:
          1) TIME_COLUMN (global) if present (matching by normalized name),
          2) 'timestamp',
          3) 'eobs_start_timestamp',
          4) 'time', 'datetime', 'date'.
        Returns the *original* column name if found; otherwise returns TIME_COLUMN
        (creating it later if missing).
        """
        # Map normalized -> original
        colmap = {_norm_key(c): c for c in df.columns}

        # TIME_COLUMN may be 'timestamp' or 'eobs:start-timestamp', etc.
        time_key_norm = _norm_key(TIME_COLUMN)
        candidates_norm = [
            time_key_norm,
            "timestamp",
            "eobs_start_timestamp",
            "time",
            "datetime",
            "date",
        ]
        for nk in candidates_norm:
            if nk in colmap:
                return colmap[nk]
        # fallback: use the global TIME_COLUMN string as-is
        return TIME_COLUMN

    # --- Parse time window ---------------------------------------------------------
    start_time = parse_timestamp(start_time_str)
    end_time   = parse_timestamp(end_time_str)
    created_paths: list[str] = []

    # --- Build a set of time-like normalized names to exclude from interpolation ---
    time_like_norm = {"timestamp", "eobs_start_timestamp", "time", "datetime", "date"}

    # Prepare normalized view of the interpolation column list (but we will keep
    # original names when writing to CSV)
    
    for local_id in ids:
        rows = []
        dts  = []  # parsed datetimes aligned with `rows`
        fieldnames = None

        # --- Read only the current ID and time range ------------------------------
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            time_key_in = resolve_time_column(fieldnames)
            #lon_key, lat_key = resolve_lon_lat_keys(fieldnames)
            for row in reader:
                norm = _norm_keys(row)

                # Time value: prefer TIME_COLUMN, then common alternates
                time_key_norm = _norm_key(TIME_COLUMN)
                ts_str = norm.get(_norm_key(time_key_in)) \
                    or norm.get("timestamp") \
                    or norm.get("eobs_start_timestamp") \
                    or norm.get("time") \
                    or norm.get("datetime") \
                    or norm.get("date")
                if not ts_str:
                    continue
                try:
                    t = parse_timestamp(ts_str)
                except Exception:
                    # Skip rows with unparsable timestamps
                    continue

                # ID filter
                rid = (norm.get("individual_local_identifier") or "").strip()
                if rid != str(local_id).strip():
                    continue

                # Time window filter
                if not (start_time <= t <= end_time):
                    continue

                rows.append(row)  # keep original headers/values
                dts.append(t)

        # If nothing matched for this ID: skip
        if not rows:
            continue

        # --- Build DataFrame preserving original headers --------------------------
        df = pd.DataFrame(rows)
        df["__dt"] = pd.to_datetime(dts)  # already parsed, but ensure dtype
        df = df.sort_values("__dt").set_index("__dt")

        # --- Interpolate each numeric column within allowed gaps -------------------
        # Keep only columns explicitly requested AND present in df, excluding any time-like
        cols_to_fill = []
        for c in (columns_to_interpolate or []):
            if c not in df.columns:
                continue
            if _norm_key(c) in time_like_norm:
                continue
            cols_to_fill.append(c)

        if cols_to_fill:
            idx = df.index
            max_gap = pd.Timedelta(minutes=max_gap_minutes)

            for col in cols_to_fill:
                # Convert to numeric; non-numeric -> NaN
                s = pd.to_numeric(df[col], errors="coerce")
                if s.isna().all():
                    # Nothing to interpolate in this column
                    df[col] = s
                    continue

                # Identify rows that are NaN between two valid values
                orig_na = s.isna()

                # Timestamps of previous/next valid values
                prev_t = pd.Series(idx.where(s.notna(), pd.NaT), index=idx).ffill()
                next_t = pd.Series(idx.where(s.notna(), pd.NaT), index=idx).bfill()

                # Total gap length between surrounding valid values
                total_gap = next_t - prev_t
                allowed = (
                    orig_na
                    & prev_t.notna()
                    & next_t.notna()
                    & (total_gap <= max_gap)
                )

                # Time-based interpolation only *inside* valid spans
                s_interp = s.interpolate(method="time", limit_area="inside")
                s_filled = s.copy()
                s_filled[allowed] = s_interp[allowed]

                df[col] = s_filled

        time_col_out = resolve_time_column(df.columns)
        # --- Prepare output: restore a string time column and drop helper ----------
        out = df.reset_index(drop=False)
        # format time once
        out_ts = out["__dt"].dt.strftime("%Y-%m-%d %H:%M:%S.%f").str[:23]
        # write back into BOTH the chosen original time column and the canonical 'timestamp'
        out[time_col_out] = out_ts
        out["timestamp"]  = out_ts
        out = out.drop(columns=["__dt"])

        # --- Write per-ID CSV ------------------------------------------------------
        id_safe = re.sub(r'[\\/:*?"<>| ]+', "_", str(local_id)).strip("_")
        out_path = output_path_template.replace(".csv", f"_{id_safe}__interp_inplace_le1d.csv")
        out.to_csv(out_path, index=False)
        created_paths.append(out_path)

    return created_paths


def normalize_column_names(df):
    """
    Normalizes DataFrame column names:
    - converts to lower-case
    - replaces '-', ':', '.', spaces with '_'
    - removes extra underscores at the beginning and end
    """
    df = df.copy()
    df.columns = [
        re.sub(r"[_]+", "_", re.sub(r"[-:.\s]+", "_", str(col).lower())).strip("_")
        for col in df.columns
    ]
    return df

def resolve_lon_lat_keys(fieldnames):
    """
    Resolve actual longitude/latitude column names from a CSV header.
    Returns (lon_key, lat_key) as *original* header strings.
    Falls back to 'location-long' / 'location-lat' if present.
    """
    import re

    def _norm(s: str) -> str:
        return re.sub(r"[-:._\s]+", "_", str(s).lower()).strip("_")

    norm_map = {_norm(c): c for c in fieldnames}

    lon_syn = ["location_long", "location_lon", "location-long", "location-lon",
               "longitude", "lon", "location_longitude", "location.longitude"]
    lat_syn = ["location_lat", "location-lat",
               "latitude", "lat", "location_latitude", "location.latitude"]

    lon_key = next((norm_map[_norm(c)] for c in lon_syn if _norm(c) in norm_map), None)
    lat_key = next((norm_map[_norm(c)] for c in lat_syn if _norm(c) in norm_map), None)

    # soft fallback to legacy dash-style names if present
    if lon_key is None and "location-long" in fieldnames:
        lon_key = "location-long"
    if lat_key is None and "location-lat" in fieldnames:
        lat_key = "location-lat"

    return lon_key, lat_key



def _norm_key(s: str) -> str:
    """Normalize a header key: lower-case and replace - : . space with _."""
    return re.sub(r"[-:._\s]+", "_", str(s).lower()).strip("_")

def resolve_time_column(fieldnames) -> str:
    """
    Pick the ORIGINAL header name that stores timestamps.
    Preference order:
      1) TIME_COLUMN (normalized)
      2) 'timestamp'
      3) 'eobs:start-timestamp' / 'eobs_start_timestamp'
      4) 'time', 'datetime', 'date'
    Returns: original header name if present; otherwise returns TIME_COLUMN.
    """
    # map normalized -> original header
    norm_to_orig = {_norm_key(c): c for c in fieldnames}

    candidates = [
        _norm_key(TIME_COLUMN),        # whatever the module-level TIME_COLUMN is
        "timestamp",
        "eobs:start-timestamp",
        "eobs_start_timestamp",
        "time", "datetime", "date",
    ]
    for cand in candidates:
        nk = _norm_key(cand)
        if nk in norm_to_orig:
            return norm_to_orig[nk]
    return TIME_COLUMN  # fallback

def resolve_id_key(fieldnames) -> str | None:
    """
    Return ORIGINAL header name that stores the individual ID.
    Supports hyphens/underscores/colons/dots variants.
    """
    norm_to_orig = {_norm_key(c): c for c in fieldnames}
    candidates = [
        "individual_local_identifier",
        "individual-local-identifier",
        "individual:local-identifier",
        "individual.local.identifier",
    ]
    for cand in candidates:
        nk = _norm_key(cand)
        if nk in norm_to_orig:
            return norm_to_orig[nk]
    return None

def delete_files(paths: list[str], keep: list[str] | None = None) -> list[str]:
    """
    Delete files by absolute/relative paths.
    Returns a list of successfully deleted paths.
    """
    from pathlib import Path
    keep_set = {str(Path(k).resolve()) for k in (keep or [])}
    deleted = []
    for p in paths:
        try:
            rp = str(Path(p).resolve())
            if rp in keep_set:
                continue
            Path(rp).unlink(missing_ok=True)
            deleted.append(rp)
        except Exception as e:
            print(f"[delete_files] Failed to delete {p}: {e}")
    return deleted