"""Transform INAO parcel rows into one packaged geometry per AOC."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.ops import unary_union

from ..config import AOC_SIMPLIFICATION_TOLERANCE, OUTPUT_LAYER
from ..validation import Check, WinePipelineError, geometry_profile
from .validate import PACKAGE_COLUMNS, validate_packaged_artifact, validate_source_aoc


GROUP_FIELDS = ["app", "id_app"]


def _distinct_non_null_counts(frame: gpd.GeoDataFrame, column: str):
    return frame.groupby(GROUP_FIELDS, sort=True, dropna=False)[column].nunique(dropna=True)


def package_aoc_geometries(raw_aoc: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, list[Check], dict[str, object]]:
    checks = validate_source_aoc(raw_aoc)
    working = raw_aoc[PACKAGE_COLUMNS].copy()
    for column in ("app", "id_app", "dt", "categorie"):
        working[column] = working[column].astype("string").str.strip()

    working["geometry"] = working.geometry.simplify(
        tolerance=AOC_SIMPLIFICATION_TOLERANCE,
        preserve_topology=True,
    ).buffer(0)

    null_after = int(working.geometry.isna().sum())
    empty_after = int(working.geometry.is_empty.sum())
    invalid_after = int((~working.geometry.is_valid).sum())
    checks.extend(
        [
            Check("aoc_package_geometry_processing_non_null", null_after == 0, observed=null_after, expected=0),
            Check("aoc_package_geometry_processing_non_empty", empty_after == 0, observed=empty_after, expected=0),
            Check("aoc_package_geometry_processing_valid", invalid_after == 0, observed=invalid_after, expected=0),
        ]
    )
    if null_after or empty_after or invalid_after:
        raise WinePipelineError("AOC geometry processing produced null, empty, or invalid geometries")

    dt_counts = _distinct_non_null_counts(working, "dt")
    categorie_counts = _distinct_non_null_counts(working, "categorie")
    inconsistent_dt = dt_counts[dt_counts != 1]
    inconsistent_categorie = categorie_counts[categorie_counts != 1]
    checks.append(Check("aoc_package_group_dt_cardinality", inconsistent_dt.empty, observed=inconsistent_dt.to_dict(), expected="one distinct non-null dt per group"))
    checks.append(Check("aoc_package_group_categorie_cardinality", inconsistent_categorie.empty, observed=inconsistent_categorie.to_dict(), expected="one distinct non-null categorie per group"))
    if not inconsistent_dt.empty:
        raise WinePipelineError(f"Inconsistent dt values within AOC groups: {inconsistent_dt.to_dict()}")
    if not inconsistent_categorie.empty:
        raise WinePipelineError(f"Inconsistent categorie values within AOC groups: {inconsistent_categorie.to_dict()}")

    grouped = working.groupby(GROUP_FIELDS, sort=True, dropna=False)
    merged_geometries = grouped["geometry"].apply(unary_union)
    packaged = gpd.GeoDataFrame(
        {
            "app": merged_geometries.index.get_level_values("app"),
            "id_app": merged_geometries.index.get_level_values("id_app"),
            "dt": grouped["dt"].first().to_numpy(),
            "categorie": grouped["categorie"].first().to_numpy(),
            "geometry": merged_geometries.to_numpy(),
        },
        crs=working.crs,
    )
    packaged = packaged.sort_values(["id_app", "app"], kind="stable").reset_index(drop=True)
    packaged = packaged[PACKAGE_COLUMNS]

    expected_groups = working[GROUP_FIELDS].drop_duplicates().shape[0]
    duplicate_keys = int(packaged.duplicated(subset=GROUP_FIELDS).sum())
    bounds_difference = abs(raw_aoc.total_bounds - packaged.total_bounds)
    checks.extend(
        [
            Check("aoc_package_group_count", len(packaged) == expected_groups, observed=len(packaged), expected=expected_groups),
            Check("aoc_package_no_duplicate_keys", duplicate_keys == 0, observed=duplicate_keys, expected=0),
            Check("aoc_package_bounds_stable", float(bounds_difference.max()) <= 1.0, observed=bounds_difference.tolist(), expected="max <= 1.0"),
        ]
    )
    failed = [check for check in checks if not check.passed]
    if failed:
        raise WinePipelineError(f"AOC packaging validation failed: {[check.name for check in failed]}")

    metadata = {
        "source_profile": geometry_profile(raw_aoc),
        "output_profile": geometry_profile(packaged),
        "transformation_parameters": {
            "selected_columns": PACKAGE_COLUMNS,
            "simplification_tolerance": AOC_SIMPLIFICATION_TOLERANCE,
            "simplification_preserve_topology": True,
            "geometry_repair": "buffer(0)",
            "grouping_fields": GROUP_FIELDS,
            "geometry_aggregation": "shapely.ops.unary_union",
            "dt_aggregation_validation": "one distinct non-null value per app/id_app group",
            "categorie_aggregation_validation": "one distinct non-null value per app/id_app group",
        },
    }
    return packaged, checks, metadata


def write_packaged_candidate(raw_aoc: gpd.GeoDataFrame, output_path: Path) -> tuple[gpd.GeoDataFrame, list[Check], dict[str, object]]:
    packaged, checks, metadata = package_aoc_geometries(raw_aoc)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    packaged.to_file(output_path, layer=OUTPUT_LAYER, driver="GPKG", index=False)
    written, artifact_checks = validate_packaged_artifact(output_path, packaged, layer=OUTPUT_LAYER)
    checks.extend(artifact_checks)
    metadata["serialized_profile"] = geometry_profile(written)
    return written, checks, metadata

