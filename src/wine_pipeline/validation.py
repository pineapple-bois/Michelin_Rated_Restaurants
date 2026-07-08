"""Validation primitives shared by the wine pipeline stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import geopandas as gpd
from shapely import make_valid


class WinePipelineError(ValueError):
    """Raised when the wine pipeline cannot produce a valid candidate."""


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    observed: object = None
    expected: object = None
    message: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def require_check(check: Check) -> Check:
    if not check.passed:
        raise WinePipelineError(f"{check.name} failed: {check.message or check.observed}")
    return check


def validate_and_repair_geometry(gdf: gpd.GeoDataFrame, dataset_name: str) -> tuple[gpd.GeoDataFrame, dict[str, int]]:
    """Fail closed on null/empty geometry and repair invalid geometry with make_valid."""

    result = gdf.copy()
    null_count = int(result.geometry.isna().sum())
    if null_count:
        raise WinePipelineError(f"{dataset_name} contains {null_count} null geometries")

    empty_before = int(result.geometry.is_empty.sum())
    if empty_before:
        raise WinePipelineError(f"{dataset_name} contains {empty_before} empty geometries")

    invalid_before_mask = ~result.geometry.is_valid
    invalid_before = int(invalid_before_mask.sum())
    if invalid_before:
        result.loc[invalid_before_mask, "geometry"] = result.loc[invalid_before_mask, "geometry"].map(make_valid)

    empty_after = int(result.geometry.is_empty.sum())
    invalid_after = int((~result.geometry.is_valid).sum())
    counts = {
        "null": null_count,
        "empty_before_repair": empty_before,
        "invalid_before_repair": invalid_before,
        "empty_after_repair": empty_after,
        "invalid_after_repair": invalid_after,
    }

    if empty_after:
        raise WinePipelineError(f"{dataset_name} contains {empty_after} empty geometries after repair")
    if invalid_after:
        raise WinePipelineError(f"{dataset_name} still contains {invalid_after} invalid geometries after repair")
    return result, counts


def geometry_profile(gdf: gpd.GeoDataFrame) -> dict[str, object]:
    return {
        "rows": len(gdf),
        "crs": gdf.crs.to_string() if gdf.crs is not None else None,
        "columns": gdf.columns.tolist(),
        "geometry_types": gdf.geometry.geom_type.value_counts(dropna=False).to_dict(),
        "bounds": gdf.total_bounds.tolist() if len(gdf) else None,
    }

