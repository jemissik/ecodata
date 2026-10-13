from __future__ import annotations

from pathlib import Path

import xarray as xr

from .base import NetCDFProfileAdapter, first_existing, require_1d, require_2d
from .models import DatasetDescriptor

TIME_CANDIDATES = ("time", "valid_time", "Time", "datetime", "date", "forecast_time", "verification_time")
LAT_CANDIDATES = ("lat", "latitude", "Latitude")
LON_CANDIDATES = ("lon", "longitude", "Longitude", "long")
X_CANDIDATES = ("x", "X", "projection_x_coordinate", "easting", "eastings")
Y_CANDIDATES = ("y", "Y", "projection_y_coordinate", "northing", "northings")
LEVEL_CANDIDATES = ("isobaricInhPa", "isobaric_in_hPa", "level", "lev", "plev", "pressure", "pressure_level")


class RegularGeographicAdapter(NetCDFProfileAdapter):
    profile_name = "Regular geographic lat/lon"

    def inspect_dataset(self, ds: xr.Dataset, path: str | Path) -> DatasetDescriptor:
        time_name = first_existing(ds, TIME_CANDIDATES)
        lat_name = first_existing(ds, LAT_CANDIDATES)
        lon_name = first_existing(ds, LON_CANDIDATES)
        if not all((time_name, lat_name, lon_name)):
            raise ValueError(
                "Regular geographic profile requires a time coordinate and 1D latitude/longitude coordinates."
            )
        require_1d(ds, lat_name, "Latitude coordinate")
        require_1d(ds, lon_name, "Longitude coordinate")

        level_names = tuple(name for name in LEVEL_CANDIDATES if name in ds.variables or name in ds.coords)
        warnings: list[str] = []
        lon = ds[lon_name]
        try:
            if float(lon.min()) >= 0.0 and float(lon.max()) > 180.0:
                warnings.append("Longitude convention appears to be 0..360; movement longitudes may need normalization.")
        except Exception:
            pass

        return DatasetDescriptor(
            profile=self.profile_name,
            grid_type="geographic_rectilinear",
            time_name=time_name,
            lat_name=lat_name,
            lon_name=lon_name,
            level_names=level_names,
            supported_methods=("nearest", "idw"),
            warnings=tuple(warnings),
            metadata={"path": str(path)},
        )


class NARRProjectedAdapter(NetCDFProfileAdapter):
    profile_name = "NARR projected grid"

    def inspect_dataset(self, ds: xr.Dataset, path: str | Path) -> DatasetDescriptor:
        time_name = first_existing(ds, ("time", "Time"))
        x_name = first_existing(ds, ("x", "X"))
        y_name = first_existing(ds, ("y", "Y"))
        lat_name = first_existing(ds, ("lat", "latitude"))
        lon_name = first_existing(ds, ("lon", "longitude"))
        missing = [
            label
            for label, value in (
                ("time", time_name), ("x", x_name), ("y", y_name),
                ("lat", lat_name), ("lon", lon_name),
            )
            if value is None
        ]
        if missing:
            raise ValueError(f"NARR profile is missing required variables: {missing}")

        require_1d(ds, x_name, "Projected X coordinate")
        require_1d(ds, y_name, "Projected Y coordinate")
        require_2d(ds, lat_name, "Auxiliary latitude")
        require_2d(ds, lon_name, "Auxiliary longitude")

        expected_dims = {y_name, x_name}
        if set(ds[lat_name].dims) != expected_dims or set(ds[lon_name].dims) != expected_dims:
            raise ValueError(
                f"NARR auxiliary lat/lon must use dimensions ({y_name}, {x_name}); "
                f"got lat{ds[lat_name].dims}, lon{ds[lon_name].dims}."
            )

        grid_mapping_name = None
        for candidate in ("Lambert_Conformal", "lambert_conformal_conic", "crs"):
            if candidate in ds.variables:
                grid_mapping_name = candidate
                break
        if grid_mapping_name is None:
            for variable in ds.data_vars.values():
                candidate = variable.attrs.get("grid_mapping")
                if candidate and candidate in ds.variables:
                    grid_mapping_name = str(candidate)
                    break
        if grid_mapping_name is None:
            raise ValueError("NARR profile requires a Lambert Conformal grid-mapping variable.")

        level_names = tuple(name for name in LEVEL_CANDIDATES if name in ds.variables or name in ds.coords)
        x_units = str(ds[x_name].attrs.get("units", "")).strip()
        y_units = str(ds[y_name].attrs.get("units", "")).strip()
        warnings: list[str] = []
        if not x_units or not y_units:
            warnings.append("X/Y units are missing; the backend will infer metres versus kilometres from coordinate magnitude.")

        return DatasetDescriptor(
            profile=self.profile_name,
            grid_type="projected_rectilinear",
            time_name=time_name,
            lat_name=lat_name,
            lon_name=lon_name,
            x_name=x_name,
            y_name=y_name,
            level_names=level_names,
            grid_mapping_name=grid_mapping_name,
            x_units=x_units,
            y_units=y_units,
            supported_methods=("bilinear",),
            warnings=tuple(warnings),
            metadata={"path": str(path), "family": "NARR"},
        )


class CustomManualAdapter(NetCDFProfileAdapter):
    profile_name = "Custom/manual"

    def inspect_dataset(self, ds: xr.Dataset, path: str | Path) -> DatasetDescriptor:
        return DatasetDescriptor(
            profile=self.profile_name,
            grid_type="manual",
            time_name=first_existing(ds, TIME_CANDIDATES),
            lat_name=first_existing(ds, LAT_CANDIDATES),
            lon_name=first_existing(ds, LON_CANDIDATES),
            x_name=first_existing(ds, X_CANDIDATES),
            y_name=first_existing(ds, Y_CANDIDATES),
            level_names=tuple(name for name in LEVEL_CANDIDATES if name in ds.variables or name in ds.coords),
            supported_methods=("nearest", "idw", "bilinear"),
            warnings=("Manual profile requires the user to verify all coordinate mappings.",),
            metadata={"path": str(path)},
        )
