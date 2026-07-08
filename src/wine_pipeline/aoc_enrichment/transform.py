"""Attach regional classification, display names, and colours to AOC packages."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

import geopandas as gpd
import pandas as pd

from ..config import OUTPUT_LAYER, TARGET_CRS
from ..validation import Check, WinePipelineError, geometry_profile, validate_and_repair_geometry
from .mappings import FALLBACK_REGIONS_BY_DT, REGION_OVERRIDES_BY_ID, WINE_REGION_COLORS
from .validate import ENRICHED_COLUMNS, validate_enriched_artifact, validate_regional_source


def prepare_regional_polygons(region_data: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, list[Check], dict[str, object]]:
    checks = validate_regional_source(region_data)
    regions = region_data.to_crs(TARGET_CRS).copy()
    regions["region"] = (
        regions["region"]
        .astype("string")
        .str.replace(r"^region\s+", "", case=False, regex=True)
        .str.strip()
    )
    regions = regions[["region", "geometry"]].copy()
    null_names = int(regions["region"].isna().sum())
    checks.append(Check("regional_source_region_names_non_null", null_names == 0, observed=null_names, expected=0))
    if null_names:
        raise WinePipelineError("Regional polygons contain null names")
    repaired, repair_counts = validate_and_repair_geometry(regions, "Regional polygons")
    bad_types = sorted(set(repaired.geometry.geom_type) - {"Polygon", "MultiPolygon"})
    checks.append(Check("regional_source_repaired_polygonal", not bad_types, observed=bad_types, expected=["Polygon", "MultiPolygon"]))
    if bad_types:
        raise WinePipelineError(f"Unexpected regional geometry types after repair: {bad_types}")
    return repaired, checks, {"regional_geometry_repair_counts": repair_counts, "regional_profile": geometry_profile(repaired)}


def _majority_overlap(
    aoc_data: gpd.GeoDataFrame,
    regions: gpd.GeoDataFrame,
    progress: Callable[[str], None] | None = None,
) -> gpd.GeoDataFrame:
    progress = progress or (lambda message: None)
    progress(f"running polygon overlay for {len(aoc_data)} AOCs and {len(regions)} regional polygons")
    overlap_candidates = gpd.overlay(
        aoc_data[["aoc_key", "app", "id_app", "dt", "aoc_area", "categorie", "geometry"]],
        regions[["region", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    progress(f"overlay produced {len(overlap_candidates)} raw intersection rows")
    if overlap_candidates.empty:
        return gpd.GeoDataFrame(columns=["aoc_key", "region", "overlap_area", "overlap_ratio"], geometry=[])
    overlap_candidates["overlap_area"] = overlap_candidates.geometry.area
    overlap_candidates["overlap_ratio"] = overlap_candidates["overlap_area"] / overlap_candidates["aoc_area"]
    overlap_candidates = overlap_candidates[overlap_candidates["overlap_area"] > 0].copy()
    progress(f"kept {len(overlap_candidates)} positive-area intersection rows")
    majority_region = (
        overlap_candidates.sort_values(
            ["aoc_key", "overlap_area", "region"],
            ascending=[True, False, True],
            kind="stable",
        )
        .drop_duplicates(subset="aoc_key", keep="first")
        [["aoc_key", "region", "overlap_area", "overlap_ratio"]]
    )
    progress(f"selected majority-overlap regions for {len(majority_region)} AOCs")
    return majority_region


def _apply_overrides(aoc_enriched: gpd.GeoDataFrame, overrides_by_id: dict[str, str]) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    result = aoc_enriched.copy()
    duplicate_override_rows = result[result["aoc_key"].isin(overrides_by_id)].duplicated(subset="aoc_key", keep=False)
    if duplicate_override_rows.any():
        duplicated = sorted(result.loc[duplicate_override_rows, "aoc_key"].unique().tolist())
        raise WinePipelineError(f"Override IDs occur more than once: {duplicated}")
    present_ids = set(result["aoc_key"])
    missing_ids = sorted(set(overrides_by_id) - present_ids)
    if missing_ids:
        raise WinePipelineError(f"Configured override IDs are absent: {missing_ids}")

    override_region = result["aoc_key"].map(overrides_by_id)
    override_mask = override_region.notna()
    result.loc[override_mask, "region"] = override_region[override_mask]
    result.loc[override_mask, "region_method"] = "explicit_override"
    result.loc[override_mask, "overlap_ratio"] = pd.NA
    used_ids = set(result.loc[override_mask, "aoc_key"])
    unused_ids = sorted(set(overrides_by_id) - used_ids)
    if unused_ids:
        raise WinePipelineError(f"Configured override IDs were not applied: {unused_ids}")
    return result, {"configured_override_ids": sorted(overrides_by_id), "used_override_ids": sorted(used_ids)}


def _apply_fallbacks(aoc_enriched: gpd.GeoDataFrame, fallbacks_by_dt: dict[str, str]) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    result = aoc_enriched.copy()
    unmatched_mask = result["region"].isna()
    fallback_region = result.loc[unmatched_mask, "dt"].map(fallbacks_by_dt)
    fallback_indexes = fallback_region[fallback_region.notna()].index
    result.loc[fallback_indexes, "region"] = fallback_region.loc[fallback_indexes]
    result.loc[fallback_indexes, "region_method"] = "delegation_fallback"
    result.loc[fallback_indexes, "overlap_ratio"] = pd.NA
    unmatched = result[result["region"].isna()]
    if not unmatched.empty:
        details = unmatched[["id_app", "app", "dt"]].to_dict(orient="records")
        raise WinePipelineError(f"{len(unmatched)} AOCs remain unmatched: {details}")
    used_dt = sorted(result.loc[result["region_method"] == "delegation_fallback", "dt"].dropna().unique().tolist())
    unused_dt = sorted(set(fallbacks_by_dt) - set(used_dt))
    return result, {
        "fallback_entries_used": used_dt,
        "configured_fallback_entries_not_used": unused_dt,
        "fallback_assignment_count": int((result["region_method"] == "delegation_fallback").sum()),
    }


def enrich_aoc_regions(
    aoc_candidate: gpd.GeoDataFrame,
    region_data: gpd.GeoDataFrame,
    *,
    overrides_by_id: dict[str, str] = REGION_OVERRIDES_BY_ID,
    fallbacks_by_dt: dict[str, str] = FALLBACK_REGIONS_BY_DT,
    colors: dict[str, str] = WINE_REGION_COLORS,
    progress: Callable[[str], None] | None = None,
) -> tuple[gpd.GeoDataFrame, list[Check], dict[str, object]]:
    progress = progress or (lambda message: None)
    checks: list[Check] = []
    required = {"app", "id_app", "dt", "categorie", "geometry"}
    missing = sorted(required - set(aoc_candidate.columns))
    checks.append(Check("aoc_enrichment_input_columns", not missing, observed=missing, expected=sorted(required)))
    if missing:
        raise WinePipelineError(f"Missing AOC columns for enrichment: {missing}")

    aoc = aoc_candidate.to_crs(TARGET_CRS).copy()
    progress("validating and repairing packaged AOC geometry")
    aoc, aoc_repair_counts = validate_and_repair_geometry(aoc, "AOC package")
    duplicate_id = int(aoc["id_app"].astype("string").duplicated().sum())
    checks.append(Check("aoc_enrichment_unique_id_app", duplicate_id == 0, observed=duplicate_id, expected=0))
    if duplicate_id:
        raise WinePipelineError("AOC package contains duplicate id_app values")

    progress("validating and repairing regional polygon geometry")
    regions, region_checks, region_metadata = prepare_regional_polygons(region_data)
    checks.extend(region_checks)
    aoc_data = aoc.to_crs(regions.crs).copy()
    aoc_data["aoc_area"] = aoc_data.geometry.area
    aoc_data["aoc_key"] = aoc_data["id_app"].astype("string").str.strip()

    majority_region = _majority_overlap(aoc_data, regions, progress=progress)
    progress("joining majority-overlap regions back to complete AOC geometries")
    aoc_enriched = aoc_data.merge(majority_region, on="aoc_key", how="left", validate="one_to_one")
    aoc_enriched["region_method"] = pd.NA
    aoc_enriched.loc[aoc_enriched["region"].notna(), "region_method"] = "spatial_majority"

    progress("applying reviewed explicit region overrides")
    aoc_enriched, override_metadata = _apply_overrides(aoc_enriched, overrides_by_id)
    progress("applying delegation fallback mappings for unmatched AOCs")
    aoc_enriched, fallback_metadata = _apply_fallbacks(aoc_enriched, fallbacks_by_dt)

    if len(aoc_enriched) != len(aoc_candidate):
        raise WinePipelineError("Regional mapping changed row count")
    if aoc_enriched["aoc_key"].duplicated().any():
        raise WinePipelineError("Regional mapping produced duplicate AOCs")

    progress("deriving display names and colour assignments")
    aoc_enriched["display_name"] = (
        aoc_enriched["app"]
        .astype("string")
        .str.split(" ou ", n=1)
        .str[0]
        .str.replace(r"^Alsace grand cru\s+", "", case=False, regex=True)
        .str.strip()
    )
    aoc_enriched["colour"] = aoc_enriched["region"].map(colors)
    missing_colours = sorted(aoc_enriched.loc[aoc_enriched["colour"].isna(), "region"].dropna().unique().tolist())
    checks.append(Check("aoc_enrichment_colour_mapping_complete", not missing_colours, observed=missing_colours, expected="all final regions configured"))
    if missing_colours:
        raise WinePipelineError(f"Regions missing colours: {missing_colours}")

    final = gpd.GeoDataFrame(aoc_enriched[ENRICHED_COLUMNS].copy(), geometry="geometry", crs=aoc_enriched.crs)
    final = final.sort_values(["region", "display_name", "id_app"], kind="stable").reset_index(drop=True)
    progress("validating final enriched candidate geometry")
    final, final_repair_counts = validate_and_repair_geometry(final, "Final AOC-region package")
    method_counts = final["region_method"].value_counts(dropna=False).to_dict()
    region_counts = final["region"].value_counts(dropna=False).to_dict()
    checks.extend(
        [
            Check("aoc_enrichment_row_count_unchanged", len(final) == len(aoc_candidate), observed=len(final), expected=len(aoc_candidate)),
            Check("aoc_enrichment_all_regions_assigned", final["region"].notna().all(), observed=int(final["region"].isna().sum()), expected=0),
            Check("aoc_enrichment_all_colours_assigned", final["colour"].notna().all(), observed=int(final["colour"].isna().sum()), expected=0),
        ]
    )
    failed = [check for check in checks if not check.passed]
    if failed:
        raise WinePipelineError(f"AOC enrichment failed: {[check.name for check in failed]}")

    metadata = {
        **region_metadata,
        "aoc_geometry_repair_counts": aoc_repair_counts,
        "final_geometry_repair_counts": final_repair_counts,
        "regional_assignment": {
            "primary_method": "geopandas.overlay intersection; select largest overlap area per id_app",
            "zero_area_intersections_removed": True,
            "explicit_overrides_by_id": overrides_by_id,
            "fallback_regions_by_dt": fallbacks_by_dt,
            "method_counts": method_counts,
            "region_counts": region_counts,
            "unmatched_count": int(final["region"].isna().sum()),
            **override_metadata,
            **fallback_metadata,
        },
        "presentation_metadata": {
            "display_name_rule": "text before first ' ou ', then remove leading Alsace grand cru case-insensitively",
            "colour_mapping": colors,
        },
        "output_profile": geometry_profile(final),
    }
    return final, checks, metadata


def write_enriched_candidate(
    aoc_candidate: gpd.GeoDataFrame,
    region_data: gpd.GeoDataFrame,
    output_path: Path,
    *,
    overrides_by_id: dict[str, str] = REGION_OVERRIDES_BY_ID,
    fallbacks_by_dt: dict[str, str] = FALLBACK_REGIONS_BY_DT,
    colors: dict[str, str] = WINE_REGION_COLORS,
    progress: Callable[[str], None] | None = None,
) -> tuple[gpd.GeoDataFrame, list[Check], dict[str, object]]:
    progress = progress or (lambda message: None)
    final, checks, metadata = enrich_aoc_regions(
        aoc_candidate,
        region_data,
        overrides_by_id=overrides_by_id,
        fallbacks_by_dt=fallbacks_by_dt,
        colors=colors,
        progress=progress,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    progress(f"writing enriched GeoPackage: {output_path}")
    final.to_file(output_path, layer=OUTPUT_LAYER, driver="GPKG", index=False)
    progress("validating enriched GeoPackage round trip")
    written, artifact_checks = validate_enriched_artifact(output_path, len(aoc_candidate), layer=OUTPUT_LAYER)
    checks.extend(artifact_checks)
    metadata["serialized_profile"] = geometry_profile(written)
    return written, checks, metadata
