from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import xarray as xr

from .models import DatasetDescriptor


class NetCDFProfileAdapter(ABC):
    """Base class for short-lived NetCDF structure inspectors."""

    profile_name: str = "Base"

    @abstractmethod
    def inspect_dataset(self, ds: xr.Dataset, path: str | Path) -> DatasetDescriptor:
        raise NotImplementedError

    def inspect(self, path: str | Path) -> DatasetDescriptor:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"NetCDF file not found: {path}")
        if path.suffix.lower() not in {".nc", ".nc4", ".cdf"}:
            raise ValueError(f"Unsupported NetCDF extension: {path.suffix}")

        ds = xr.open_dataset(path, decode_times=False, chunks=None)
        try:
            return self.inspect_dataset(ds, path)
        finally:
            ds.close()


def first_existing(ds: xr.Dataset, candidates: tuple[str, ...]) -> str | None:
    return next((name for name in candidates if name in ds.variables or name in ds.coords), None)


def require_1d(ds: xr.Dataset, name: str, label: str) -> None:
    if ds[name].ndim != 1:
        raise ValueError(f"{label} '{name}' must be one-dimensional; got shape {ds[name].shape}.")


def require_2d(ds: xr.Dataset, name: str, label: str) -> None:
    if ds[name].ndim != 2:
        raise ValueError(f"{label} '{name}' must be two-dimensional; got shape {ds[name].shape}.")
