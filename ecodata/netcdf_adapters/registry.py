from __future__ import annotations

from pathlib import Path

import xarray as xr

from .base import NetCDFProfileAdapter
from .models import DatasetDescriptor
from .profiles import CustomManualAdapter, NARRProjectedAdapter, RegularGeographicAdapter


ADAPTER_REGISTRY: dict[str, type[NetCDFProfileAdapter]] = {
    RegularGeographicAdapter.profile_name: RegularGeographicAdapter,
    NARRProjectedAdapter.profile_name: NARRProjectedAdapter,
    CustomManualAdapter.profile_name: CustomManualAdapter,
}


def available_profiles() -> list[str]:
    return list(ADAPTER_REGISTRY.keys())


def get_adapter(profile_name: str) -> NetCDFProfileAdapter:
    try:
        adapter_cls = ADAPTER_REGISTRY[profile_name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown NetCDF profile '{profile_name}'. Available: {', '.join(available_profiles())}"
        ) from exc
    return adapter_cls()


def inspect_netcdf(path: str | Path, profile_name: str) -> DatasetDescriptor:
    return get_adapter(profile_name).inspect(path)


def inspect_netcdf_dict(path: str | Path, profile_name: str) -> dict:
    return inspect_netcdf(path, profile_name).to_dict()


def inspect_open_dataset(ds: xr.Dataset, path: str | Path, profile_name: str) -> DatasetDescriptor:
    """Inspect an already-open Dataset without taking ownership of or closing it."""
    return get_adapter(profile_name).inspect_dataset(ds, path)


def inspect_open_dataset_dict(ds: xr.Dataset, path: str | Path, profile_name: str) -> dict:
    return inspect_open_dataset(ds, path, profile_name).to_dict()
