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
POST_REPROJECTION_NEGLIGIBLE_ABSOLUTE_M2 = 10.0
POST_REPROJECTION_NEGLIGIBLE_RELATIVE = 1e-8
POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2 = 100.0
POST_REPROJECTION_RELATIVE_TOLERANCE = 1e-6


class SerializationCleanupError(ValueError):
    """A final-geometry cleanup failure with machine-readable context."""

    def __init__(self, message: str, diagnostic: dict[str, Any]):
        self.diagnostic = diagnostic
        details = ", ".join(f"{key}={value!r}" for key, value in diagnostic.items())
        super().__init__(f"{message}: {details}")


def _polygon_component_count(geometry) -> int:
    if geometry is None or geometry.is_empty:
        return 0
    if geometry.geom_type == "Polygon":
        return 1
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        return sum(_polygon_component_count(component) for component in geometry.geoms)
    return 0


def _all_polygon_components(geometry) -> list:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        components = []
        for component in geometry.geoms:
            components.extend(_all_polygon_components(component))
        return components
    return []


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


def _cleanup_diagnostic(
    *,
    region: str,
    app: str,
    original_geometry,
    repaired_geometry,
    source_area: float,
    retained_count: int,
    rejected_count: int,
    rejected_area: float,
    absolute_tolerance_m2: float,
    relative_tolerance: float,
    whole_appellation_empty: bool,
) -> dict[str, Any]:
    return {
        "region": region,
        "app": app,
        "original_geometry_type": None if original_geometry is None else original_geometry.geom_type,
        "repaired_geometry_type": None if repaired_geometry is None else repaired_geometry.geom_type,
        "validity_reason": "Null geometry" if original_geometry is None else explain_validity(original_geometry),
        "original_polygon_component_count": _polygon_component_count(original_geometry),
        "retained_component_count": retained_count,
        "rejected_component_count": rejected_count,
        "rejected_area_m2": rejected_area,
        "rejected_area_fraction_of_source": (
            rejected_area / source_area if source_area > 0 else float("inf")
        ),
        "absolute_tolerance_m2": absolute_tolerance_m2,
        "relative_tolerance": relative_tolerance,
        "whole_appellation_empty": whole_appellation_empty,
    }


def _geometry_area_m2(geometry) -> float:
    polygons = _all_polygon_components(geometry)
    footprint = unary_union(polygons) if polygons else geometry
    return float(
        gpd.GeoSeries([footprint], crs=OUTPUT_CRS)
        .to_crs(WORKING_CRS)
        .area.iloc[0]
    )


def repair_post_reprojection_geometry(
    geometry,
    *,
    region: str,
    app: str,
    source_area_m2: float,
    absolute_tolerance_m2: float = POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2,
    relative_tolerance: float = POST_REPROJECTION_RELATIVE_TOLERANCE,
) -> tuple[Any, dict[str, Any]]:
    """Repair topology exposed by EPSG:4326 reprojection within strict tolerances."""
    geometry_type_before = None if geometry is None else geometry.geom_type
    validity_reason_before = "Null geometry" if geometry is None else explain_validity(geometry)
    component_count_before = _polygon_component_count(geometry)
    area_before = 0.0 if geometry is None or geometry.is_empty else _geometry_area_m2(geometry)
    diagnostic = {
        "region": region,
        "app": app,
        "post_reprojection_validity_reason_before_repair": validity_reason_before,
        "post_reprojection_geometry_type_before_repair": geometry_type_before,
        "post_reprojection_geometry_type_after_repair": geometry_type_before,
        "post_reprojection_component_count_before_repair": component_count_before,
        "post_reprojection_component_count_after_repair": component_count_before,
        "post_reprojection_area_before_m2": area_before,
        "post_reprojection_area_after_m2": area_before,
        "post_reprojection_absolute_area_change_m2": 0.0,
        "post_reprojection_relative_area_change": 0.0,
        "post_reprojection_negligible_absolute_threshold_m2": POST_REPROJECTION_NEGLIGIBLE_ABSOLUTE_M2,
        "post_reprojection_negligible_relative_threshold": POST_REPROJECTION_NEGLIGIBLE_RELATIVE,
        "post_reprojection_absolute_tolerance_m2": absolute_tolerance_m2,
        "post_reprojection_relative_tolerance": relative_tolerance,
        "post_reprojection_cleanup_action": "post_reprojection_unchanged",
        "post_reprojection_review_classification": "none",
    }
    if geometry is None or geometry.is_empty:
        diagnostic["post_reprojection_review_classification"] = "fatal"
        raise SerializationCleanupError("Post-reprojection geometry is empty", diagnostic)
    if geometry.is_valid and geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry, diagnostic

    repaired = make_valid(geometry)
    polygons, _ = _polygon_components(repaired)
    diagnostic["post_reprojection_geometry_type_after_repair"] = repaired.geom_type
    diagnostic["post_reprojection_component_count_after_repair"] = len(polygons)
    diagnostic["post_reprojection_cleanup_action"] = "post_reprojection_topology_repair"
    if not polygons:
        diagnostic["post_reprojection_review_classification"] = "fatal"
        raise SerializationCleanupError(
            "Post-reprojection repair produced empty or non-polygonal geometry",
            diagnostic,
        )

    try:
        final_geometry = _polygonal_union(polygons)
    except Exception as error:
        diagnostic["post_reprojection_review_classification"] = "fatal"
        raise SerializationCleanupError(
            "Post-reprojection polygon extraction failed",
            diagnostic,
        ) from error
    diagnostic["post_reprojection_geometry_type_after_repair"] = final_geometry.geom_type
    diagnostic["post_reprojection_component_count_after_repair"] = _polygon_component_count(final_geometry)
    if (
        final_geometry.is_empty
        or final_geometry.geom_type not in {"Polygon", "MultiPolygon"}
        or not final_geometry.is_valid
    ):
        diagnostic["post_reprojection_review_classification"] = "fatal"
        raise SerializationCleanupError(
            "Post-reprojection repair remains empty, non-polygonal, or invalid",
            diagnostic,
        )

    area_after = _geometry_area_m2(final_geometry)
    area_change = abs(area_after - area_before)
    relative_change = area_change / source_area_m2 if source_area_m2 > 0 else float("inf")
    diagnostic.update(
        {
            "post_reprojection_area_after_m2": area_after,
            "post_reprojection_absolute_area_change_m2": area_change,
            "post_reprojection_relative_area_change": relative_change,
        }
    )
    if area_change > absolute_tolerance_m2 and relative_change > relative_tolerance:
        diagnostic["post_reprojection_review_classification"] = "fatal"
        raise SerializationCleanupError(
            "Post-reprojection repair exceeds serialization cleanup tolerances",
            diagnostic,
        )
    if (
        area_change <= POST_REPROJECTION_NEGLIGIBLE_ABSOLUTE_M2
        or relative_change <= POST_REPROJECTION_NEGLIGIBLE_RELATIVE
    ):
        diagnostic["post_reprojection_review_classification"] = "negligible"
    else:
        diagnostic["post_reprojection_review_classification"] = "review"
    return final_geometry, diagnostic


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
        region = str(row.get("region", ""))
        app = str(row.get("app", ""))
        source_area = float(row["source_area_m2"])
        if geometry is None or geometry.is_empty:
            diagnostic = _cleanup_diagnostic(
                region=region,
                app=app,
                original_geometry=geometry,
                repaired_geometry=geometry,
                source_area=source_area,
                retained_count=0,
                rejected_count=0,
                rejected_area=0.0,
                absolute_tolerance_m2=absolute_tolerance_m2,
                relative_tolerance=relative_tolerance,
                whole_appellation_empty=True,
            )
            raise SerializationCleanupError("Serialization cleanup input is empty", diagnostic)

        original_type = geometry.geom_type
        original_reason = explain_validity(geometry)
        try:
            repaired = make_valid(geometry)
        except Exception as error:
            diagnostic = _cleanup_diagnostic(
                region=region,
                app=app,
                original_geometry=geometry,
                repaired_geometry=None,
                source_area=source_area,
                retained_count=0,
                rejected_count=0,
                rejected_area=0.0,
                absolute_tolerance_m2=absolute_tolerance_m2,
                relative_tolerance=relative_tolerance,
                whole_appellation_empty=False,
            )
            raise SerializationCleanupError("Serialization geometry repair failed", diagnostic) from error
        polygons, rejected = _polygon_components(repaired)
        rejected_area = sum(max(float(component.area), 0.0) for component in rejected if not component.is_empty)
        retained_area = sum(float(component.area) for component in polygons)
        removed_area = (
            max(rejected_area, max(float(geometry.area) - retained_area, 0.0))
            if rejected
            else 0.0
        )
        removed_fraction = removed_area / source_area if source_area > 0 else float("inf")
        diagnostic = _cleanup_diagnostic(
            region=region,
            app=app,
            original_geometry=geometry,
            repaired_geometry=repaired,
            source_area=source_area,
            retained_count=len(polygons),
            rejected_count=len(rejected),
            rejected_area=removed_area,
            absolute_tolerance_m2=absolute_tolerance_m2,
            relative_tolerance=relative_tolerance,
            whole_appellation_empty=not polygons,
        )

        if not polygons:
            raise SerializationCleanupError("Serialization cleanup removed the whole appellation", diagnostic)
        if rejected and (
            removed_area > absolute_tolerance_m2
            or removed_fraction > relative_tolerance
        ):
            raise SerializationCleanupError("Serialization cleanup exceeds removal tolerances", diagnostic)

        try:
            final_geometry = _polygonal_union(polygons)
        except Exception as error:
            raise SerializationCleanupError("Serialization cleanup polygon union failed", diagnostic) from error
        if final_geometry.is_empty:
            diagnostic["whole_appellation_empty"] = True
            diagnostic["retained_component_count"] = 0
            raise SerializationCleanupError("Serialization cleanup produced an empty appellation", diagnostic)
        if not final_geometry.is_valid:
            diagnostic["repaired_geometry_type"] = final_geometry.geom_type
            diagnostic["validity_reason"] = explain_validity(final_geometry)
            raise SerializationCleanupError("Serialization cleanup remains invalid", diagnostic)

        removed_count = len(rejected)
        if removed_count:
            action = "removed_negligible_invalid_or_degenerate_components"
        elif not geometry.is_valid or not geometry.equals(final_geometry):
            action = "repaired_without_component_removal"
        else:
            action = "unchanged"
        diagnostics.append(
            {
                **diagnostic,
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
    post_reprojection_geometries = []
    for position, (_, row) in enumerate(cleaned.iterrows()):
        geometry = row.geometry
        try:
            final_geometry, post_diagnostic = repair_post_reprojection_geometry(
                geometry,
                region=str(row.get("region", "")),
                app=str(row.get("app", "")),
                source_area_m2=float(row["source_area_m2"]),
            )
        except SerializationCleanupError as error:
            error.diagnostic.update(
                {
                    key: value
                    for key, value in diagnostics[position].items()
                    if key not in error.diagnostic
                }
            )
            raise
        diagnostics[position].update(post_diagnostic)
        post_reprojection_geometries.append(final_geometry)
    cleaned.geometry = post_reprojection_geometries
    for position, (_, row) in enumerate(cleaned.iterrows()):
        if row.geometry is not None and not row.geometry.is_empty and row.geometry.is_valid:
            continue
        diagnostic = dict(diagnostics[position])
        diagnostic["post_reprojection_validity_reason_before_repair"] = (
            "Null geometry" if row.geometry is None else explain_validity(row.geometry)
        )
        raise SerializationCleanupError(
            "Post-reprojection validation failed after topology repair",
            diagnostic,
        )
    return cleaned, diagnostics
