"""Final polygon cleanup at the Stage 2 GeoJSON serialization boundary."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from shapely.geometry import MultiPolygon
from shapely.ops import unary_union
from shapely.validation import explain_validity

try:
    from shapely import make_valid
except ImportError:  # pragma: no cover
    from shapely.validation import make_valid

from .transform import OUTPUT_COLUMNS, OUTPUT_CRS, WORKING_CRS


SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2 = 1.0
SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE = 1e-9


def _polygon_components(geometry) -> tuple[list, list]:
    polygons = []
    rejected = []
    if geometry is None:
        return polygons, rejected
    if geometry.geom_type == "Polygon":
        (polygons if not geometry.is_empty and geometry.area > 0 and geometry.is_valid else rejected).append(geometry)
    elif geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        for component in geometry.geoms:
            child_polygons, child_rejected = _polygon_components(component)
            polygons.extend(child_polygons)
            rejected.extend(child_rejected)
    else:
        rejected.append(geometry)
    return polygons, rejected


def _polygonal_union(polygons: list):
    combined = make_valid(unary_union(polygons))
    final_polygons, rejected = _polygon_components(combined)
    if rejected:
        raise ValueError("Final serialization repair produced residual non-polygonal or invalid components.")
    if not final_polygons:
        return MultiPolygon()
    if len(final_polygons) == 1:
        return final_polygons[0]
    return MultiPolygon(final_polygons)


def cleanup_final_geometries(
    frame: gpd.GeoDataFrame,
    *,
    absolute_tolerance_m2: float = SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2,
    relative_tolerance: float = SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE,
) -> tuple[gpd.GeoDataFrame, list[dict[str, Any]]]:
    """Repair and prune negligible bad components before GeoJSON serialization.

    Component removal is allowed only when both the removed area is at most
    1 square metre and its fraction of the preserved source area is at most
    1e-9. These deliberately strict defaults target serialization debris, not
    meaningful geometry.
    """
    if frame.crs is None:
        raise ValueError("Final serialization cleanup requires a defined CRS.")
    working = frame.to_crs(WORKING_CRS).copy()
    cleaned_geometries = []
    diagnostics = []

    for _, row in working.iterrows():
        geometry = row.geometry
        app = str(row.get("app", ""))
        source_area = float(row["source_area_m2"])
        if geometry is None or geometry.is_empty:
            raise ValueError(f"Serialization cleanup would leave app={app!r} empty.")

        original_type = geometry.geom_type
        original_reason = explain_validity(geometry)
        repaired = make_valid(geometry)
        polygons, rejected = _polygon_components(repaired)
        rejected_area = sum(max(float(component.area), 0.0) for component in rejected if not component.is_empty)
        retained_area = sum(float(component.area) for component in polygons)
        removed_area = (
            max(rejected_area, max(float(geometry.area) - retained_area, 0.0))
            if rejected
            else 0.0
        )
        removed_fraction = removed_area / source_area if source_area > 0 else float("inf")

        if not polygons:
            raise ValueError(f"Serialization cleanup would leave app={app!r} empty.")
        if rejected and (
            removed_area > absolute_tolerance_m2
            or removed_fraction > relative_tolerance
        ):
            raise ValueError(
                f"Serialization cleanup for app={app!r} would remove {removed_area:.12g} m2 "
                f"({removed_fraction:.12g} of source area), exceeding tolerances "
                f"{absolute_tolerance_m2} m2 and {relative_tolerance}."
            )

        final_geometry = _polygonal_union(polygons)
        if final_geometry.is_empty:
            raise ValueError(f"Serialization cleanup would leave app={app!r} empty.")
        if not final_geometry.is_valid:
            raise ValueError(
                f"Serialization cleanup for app={app!r} remains invalid: {explain_validity(final_geometry)}"
            )

        removed_count = len(rejected)
        if removed_count:
            action = "removed_negligible_invalid_or_degenerate_components"
        elif not geometry.is_valid or not geometry.equals(final_geometry):
            action = "repaired_without_component_removal"
        else:
            action = "unchanged"
        diagnostics.append(
            {
                "app": app,
                "original_geometry_type": original_type,
                "final_geometry_type": final_geometry.geom_type,
                "removed_component_count": removed_count,
                "removed_area_m2": removed_area,
                "removed_area_fraction_of_source": removed_fraction,
                "validity_reason": original_reason,
                "cleanup_action": action,
            }
        )
        cleaned_geometries.append(final_geometry)

    working.geometry = cleaned_geometries
    cleaned = working.to_crs(OUTPUT_CRS)[OUTPUT_COLUMNS]
    if cleaned.geometry.isna().any() or cleaned.geometry.is_empty.any() or not cleaned.geometry.is_valid.all():
        raise ValueError("Final serialization cleanup produced invalid or empty geometry.")
    return cleaned, diagnostics
