"""Transform INAO parcel rows into one packaged geometry per AOC."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

import geopandas as gpd
from shapely.ops import unary_union

from ..config import AOC_SIMPLIFICATION_TOLERANCE, OUTPUT_LAYER
from ..validation import Check, WinePipelineError, geometry_profile
from .validate import (
    PACKAGE_COLUMNS,
    WINE_CATEGORY_PATTERN,
    validate_packaged_artifact,
    validate_source_aoc,
)


GROUP_FIELDS = ["app", "id_app"]


def _distinct_non_null_counts(frame: gpd.GeoDataFrame, column: str):
    return frame.groupby(GROUP_FIELDS, sort=True, dropna=False)[column].nunique(dropna=True)


def package_aoc_geometries(raw_aoc: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, list[Check], dict[str, object]]:
    checks = validate_source_aoc(raw_aoc)
    working = raw_aoc[PACKAGE_COLUMNS].copy()
    working["_source_order"] = range(len(working))
    for column in ("app", "id_app", "dt", "categorie"):
        working[column] = working[column].astype("string").str.strip()

    wine_category_mask = working["categorie"].str.contains(
        WINE_CATEGORY_PATTERN,
        case=False,
        na=False,
        regex=True,
    )
    excluded_non_wine = (
        working.loc[~wine_category_mask, ["app", "id_app", "categorie"]]
        .drop_duplicates()
        .sort_values(["categorie", "app", "id_app"], kind="stable")
        .reset_index(drop=True)
    )
    excluded_source_indexes = working.index[~wine_category_mask]
    working = working.loc[wine_category_mask].copy()

    retained_non_wine_count = int(
        (
            ~working["categorie"].str.contains(
                WINE_CATEGORY_PATTERN,
                case=False,
                na=False,
                regex=True,
            )
        ).sum()
    )
    checks.extend(
        [
            Check(
                "aoc_package_non_wine_source_rows_excluded",
                not working.index.isin(excluded_source_indexes).any(),
                observed=excluded_non_wine.to_dict(orient="records"),
                expected=f"all source rows whose categorie does not match {WINE_CATEGORY_PATTERN} are excluded",
            ),
            Check(
                "aoc_package_retained_categories_are_wine",
                retained_non_wine_count == 0,
                observed=retained_non_wine_count,
                expected=0,
            ),
        ]
    )
    if working.empty:
        raise WinePipelineError("AOC source filtering removed every row; no wine categories remain")
    if retained_non_wine_count:
        raise WinePipelineError("AOC source filtering retained non-wine categories")

    filtered_source_profile = geometry_profile(working)

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
    checks.append(Check("aoc_package_group_dt_cardinality", inconsistent_dt.empty, observed=inconsistent_dt.to_dict(), expected="one distinct non-null dt per group"))
    if not inconsistent_dt.empty:
        raise WinePipelineError(f"Inconsistent dt values within AOC groups: {inconsistent_dt.to_dict()}")
    mixed_categorie = categorie_counts[categorie_counts > 1]
    mixed_categorie_details = {
        f"{app}|{id_app}": sorted(
            working.loc[(working["app"] == app) & (working["id_app"] == id_app), "categorie"]
            .dropna()
            .unique()
            .tolist()
        )
        for app, id_app in mixed_categorie.index
    }
    checks.append(
        Check(
            "aoc_package_group_categorie_mixed_values_reported",
            True,
            observed=mixed_categorie_details,
            expected="reported; representative value retained from first source row",
        )
    )

    grouped = working.groupby(GROUP_FIELDS, sort=True, dropna=False)
    merged_geometries = grouped["geometry"].apply(unary_union)
    representative_rows = (
        working.sort_values("_source_order", kind="stable")
        .drop_duplicates(subset=GROUP_FIELDS, keep="first")
        .set_index(GROUP_FIELDS)
        .loc[merged_geometries.index]
    )
    packaged = gpd.GeoDataFrame(
        {
            "app": merged_geometries.index.get_level_values("app"),
            "id_app": merged_geometries.index.get_level_values("id_app"),
            "dt": representative_rows["dt"].to_numpy(),
            "categorie": representative_rows["categorie"].to_numpy(),
            "geometry": merged_geometries.to_numpy(),
        },
        crs=working.crs,
    )
    packaged = packaged.sort_values(["id_app", "app"], kind="stable").reset_index(drop=True)
    packaged = packaged[PACKAGE_COLUMNS]

    expected_groups = working[GROUP_FIELDS].drop_duplicates().shape[0]
    duplicate_keys = int(packaged.duplicated(subset=GROUP_FIELDS).sum())
    bounds_difference = abs(working.total_bounds - packaged.total_bounds)
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
        "filtered_source_profile": filtered_source_profile,
        "output_profile": geometry_profile(packaged),
        "transformation_parameters": {
            "selected_columns": PACKAGE_COLUMNS,
            "wine_category_filter": {
                "column": "categorie",
                "pattern": WINE_CATEGORY_PATTERN,
                "case_sensitive": False,
                "excluded_rows": int((~wine_category_mask).sum()),
                "excluded_records": excluded_non_wine.to_dict(orient="records"),
            },
            "simplification_tolerance": AOC_SIMPLIFICATION_TOLERANCE,
            "simplification_preserve_topology": True,
            "geometry_repair": "buffer(0)",
            "grouping_fields": GROUP_FIELDS,
            "geometry_aggregation": "shapely.ops.unary_union",
            "dt_aggregation_validation": "one distinct non-null value per app/id_app group",
            "categorie_aggregation_validation": (
                "parcel-level mixed categorie values are reported; packaged value is retained "
                "deterministically from the first source row in the INAO shapefile"
            ),
            "mixed_categorie_groups": mixed_categorie_details,
        },
    }
    return packaged, checks, metadata


def write_packaged_candidate(
    raw_aoc: gpd.GeoDataFrame,
    output_path: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[gpd.GeoDataFrame, list[Check], dict[str, object]]:
    progress = progress or (lambda message: None)
    packaged, checks, metadata = package_aoc_geometries(raw_aoc)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    progress(f"writing packaged GeoPackage: {output_path}")
    packaged.to_file(output_path, layer=OUTPUT_LAYER, driver="GPKG", index=False)
    progress("validating packaged GeoPackage round trip")
    written, artifact_checks = validate_packaged_artifact(output_path, packaged, layer=OUTPUT_LAYER)
    checks.extend(artifact_checks)
    metadata["serialized_profile"] = geometry_profile(written)
    return written, checks, metadata
