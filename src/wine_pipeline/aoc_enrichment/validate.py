"""Validation for regional AOC enrichment inputs and outputs."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from ..config import OUTPUT_LAYER
from ..validation import Check, WinePipelineError


ENRICHED_COLUMNS = [
    "id_app",
    "app",
    "display_name",
    "dt",
    "region",
    "region_method",
    "overlap_ratio",
    "colour",
    "categorie",
    "geometry",
]

ALLOWED_REGION_METHODS = {"spatial_majority", "explicit_override", "delegation_fallback"}


def validate_regional_source(gdf: gpd.GeoDataFrame) -> list[Check]:
    checks = [
        Check("regional_source_not_empty", not gdf.empty, observed=len(gdf), expected="> 0"),
        Check("regional_source_has_crs", gdf.crs is not None, observed=gdf.crs.to_string() if gdf.crs else None, expected="present"),
        Check("regional_source_has_region_column", "region" in gdf.columns, observed=gdf.columns.tolist(), expected="region column"),
    ]
    bad_types = sorted(set(gdf.geometry.geom_type) - {"Polygon", "MultiPolygon"}) if "geometry" in gdf else ["missing geometry"]
    checks.append(Check("regional_source_polygonal", not bad_types, observed=bad_types, expected=["Polygon", "MultiPolygon"]))
    failed = [check for check in checks if not check.passed]
    if failed:
        raise WinePipelineError(f"Regional source validation failed: {[check.name for check in failed]}")
    return checks


def validate_enriched_artifact(path: Path, expected_rows: int, *, layer: str = OUTPUT_LAYER) -> tuple[gpd.GeoDataFrame, list[Check]]:
    written = gpd.read_file(path, layer=layer)
    method_values = set(written["region_method"].dropna().unique()) if "region_method" in written.columns else set()
    checks = [
        Check("aoc_regions_row_count", len(written) == expected_rows, observed=len(written), expected=expected_rows),
        Check("aoc_regions_schema", list(written.columns) == ENRICHED_COLUMNS, observed=list(written.columns), expected=ENRICHED_COLUMNS),
        Check("aoc_regions_unique_id_app", "id_app" in written.columns and written["id_app"].is_unique, observed=int(written["id_app"].duplicated().sum()) if "id_app" in written.columns else None, expected=0),
        Check("aoc_regions_crs_epsg_2154", written.crs is not None and written.crs.to_epsg() == 2154, observed=written.crs.to_string() if written.crs else None, expected="EPSG:2154"),
        Check("aoc_regions_geometry_non_null", int(written.geometry.isna().sum()) == 0, observed=int(written.geometry.isna().sum()), expected=0),
        Check("aoc_regions_geometry_non_empty", int(written.geometry.is_empty.sum()) == 0, observed=int(written.geometry.is_empty.sum()), expected=0),
        Check("aoc_regions_geometry_valid", bool(written.geometry.is_valid.all()), observed=int((~written.geometry.is_valid).sum()), expected=0),
        Check("aoc_regions_region_present", written["region"].notna().all() if "region" in written.columns else False, observed=int(written["region"].isna().sum()) if "region" in written.columns else None, expected=0),
        Check("aoc_regions_colour_present", written["colour"].notna().all() if "colour" in written.columns else False, observed=int(written["colour"].isna().sum()) if "colour" in written.columns else None, expected=0),
        Check("aoc_regions_method_values", method_values <= ALLOWED_REGION_METHODS, observed=sorted(method_values), expected=sorted(ALLOWED_REGION_METHODS)),
    ]
    failed = [check for check in checks if not check.passed]
    if failed:
        raise WinePipelineError(f"Enriched AOC artifact validation failed: {[check.name for check in failed]}")
    return written, checks

