"""Validation for the packaged INAO AOC candidate."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from ..config import OUTPUT_LAYER
from ..validation import Check, WinePipelineError


PACKAGE_COLUMNS = ["app", "id_app", "dt", "categorie", "geometry"]
WINE_CATEGORY_PATTERN = r"\bVin\b"


def validate_source_aoc(gdf: gpd.GeoDataFrame) -> list[Check]:
    checks: list[Check] = []
    required = set(PACKAGE_COLUMNS)
    missing = sorted(required - set(gdf.columns))
    checks.append(Check("aoc_source_required_columns", not missing, observed=missing, expected=sorted(required)))
    checks.append(Check("aoc_source_not_empty", not gdf.empty, observed=len(gdf), expected="> 0"))
    epsg = gdf.crs.to_epsg() if gdf.crs is not None else None
    checks.append(Check("aoc_source_crs_epsg_2154", epsg == 2154, observed=epsg, expected=2154))
    if missing or gdf.empty or epsg != 2154:
        raise WinePipelineError("AOC source failed required column, row, or CRS validation")
    null_identity = {column: int(gdf[column].isna().sum()) for column in ("app", "id_app")}
    checks.append(Check("aoc_source_identity_non_null", not any(null_identity.values()), observed=null_identity, expected=0))
    null_geometry = int(gdf.geometry.isna().sum())
    empty_geometry = int(gdf.geometry.is_empty.sum())
    invalid_geometry = int((~gdf.geometry.is_valid).sum())
    checks.append(Check("aoc_source_geometry_non_null", null_geometry == 0, observed=null_geometry, expected=0))
    checks.append(Check("aoc_source_geometry_non_empty", empty_geometry == 0, observed=empty_geometry, expected=0))
    checks.append(Check("aoc_source_invalid_geometry_reported", True, observed=invalid_geometry, expected="reported"))
    failed = [check for check in checks if not check.passed]
    if failed:
        raise WinePipelineError(f"AOC source validation failed: {[check.name for check in failed]}")
    return checks


def validate_packaged_artifact(path: Path, expected: gpd.GeoDataFrame, *, layer: str = OUTPUT_LAYER) -> tuple[gpd.GeoDataFrame, list[Check]]:
    written = gpd.read_file(path, layer=layer)
    non_wine_count = int(
        (
            ~written["categorie"].astype("string").str.contains(
                WINE_CATEGORY_PATTERN,
                case=False,
                na=False,
                regex=True,
            )
        ).sum()
    )
    checks = [
        Check("aoc_package_round_trip_row_count", len(written) == len(expected), observed=len(written), expected=len(expected)),
        Check("aoc_package_schema", list(written.columns) == PACKAGE_COLUMNS, observed=list(written.columns), expected=PACKAGE_COLUMNS),
        Check("aoc_package_crs_epsg_2154", written.crs is not None and written.crs.to_epsg() == 2154, observed=written.crs.to_string() if written.crs else None, expected="EPSG:2154"),
        Check("aoc_package_unique_app_id_app", not written.duplicated(subset=["app", "id_app"]).any(), observed=int(written.duplicated(subset=["app", "id_app"]).sum()), expected=0),
        Check("aoc_package_geometry_non_null", int(written.geometry.isna().sum()) == 0, observed=int(written.geometry.isna().sum()), expected=0),
        Check("aoc_package_geometry_non_empty", int(written.geometry.is_empty.sum()) == 0, observed=int(written.geometry.is_empty.sum()), expected=0),
        Check("aoc_package_geometry_valid", bool(written.geometry.is_valid.all()), observed=int((~written.geometry.is_valid).sum()), expected=0),
        Check("aoc_package_categories_are_wine", non_wine_count == 0, observed=non_wine_count, expected=0),
    ]
    failed = [check for check in checks if not check.passed]
    if failed:
        raise WinePipelineError(f"Packaged AOC artifact validation failed: {[check.name for check in failed]}")
    return written, checks
