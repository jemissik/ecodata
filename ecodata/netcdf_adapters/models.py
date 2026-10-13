from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DatasetDescriptor:
    """Serializable description of a NetCDF grid and its supported sampling methods."""

    profile: str
    grid_type: str
    time_name: str | None = None
    lat_name: str | None = None
    lon_name: str | None = None
    x_name: str | None = None
    y_name: str | None = None
    level_names: tuple[str, ...] = ()
    grid_mapping_name: str | None = None
    x_units: str = ""
    y_units: str = ""
    supported_methods: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
