"""Stage 2 regional geometry simplification for enriched wine AOC candidates."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from math import isfinite
from typing import Any

import geopandas as gpd
from shapely.geometry import MultiPolygon
from shapely.ops import unary_union

try:
    from shapely import make_valid
except ImportError:  # pragma: no cover
    from shapely.validation import make_valid


WORKING_CRS = "EPSG:2154"
OUTPUT_CRS = "EPSG:4326"
STAGE1_COLUMNS = [
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
OUTPUT_IDENTITY_COLUMNS = ["region", "app", "display_name", "colour", "categorie"]
OUTPUT_COLUMNS = [*OUTPUT_IDENTITY_COLUMNS, "source_area_m2", "geometry"]
CANONICAL_RUN_ID = "close500_simplify150"
CANONICAL_BUFFER_M = 500.0
CANONICAL_SIMPLIFY_M = 150.0
CANONICAL_OVERLAP_STRATEGY = "smallest-wins"
OVERLAP_ABSOLUTE_TOLERANCE_M2 = 1e-6
OVERLAP_RELATIVE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class SimplificationParameters:
    buffer_m: float = CANONICAL_BUFFER_M
    simplify_m: float = CANONICAL_SIMPLIFY_M
    overlap_strategy: str = CANONICAL_OVERLAP_STRATEGY

    @property
    def canonical(self) -> bool:
        return (
            self.buffer_m == CANONICAL_BUFFER_M
            and self.simplify_m == CANONICAL_SIMPLIFY_M
            and self.overlap_strategy == CANONICAL_OVERLAP_STRATEGY
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "buffer_m": self.buffer_m,
            "simplify_m": self.simplify_m,
            "overlap_strategy": self.overlap_strategy,
            "canonical_parameter_set": self.canonical,
            "canonical_parameter_set_name": CANONICAL_RUN_ID if self.canonical else None,
            "parameter_notes": {
                "buffer_m": (
                    "Outward-then-inward morphological closing distance in metres; "
                    "not a permanent expansion."
                ),
                "simplify_m": (
                    "Topology-preserving simplification tolerance in metres; reduces "
                    "boundary detail and payload complexity."
                ),
                "overlap_strategy": (
                    "smallest-wins gives smaller processed appellations priority over "
                    "larger overlapping appellations and can reduce or fully cover "
                    "broader appellations."
                ),
            },
        }


@dataclass(frozen=True)
class OverlapMetrics:
    summed_app_area_m2: float
    union_area_m2: float
    overlap_area_m2: float

    def as_dict(self) -> dict[str, float]:
        return {
            "summed_app_area_m2": self.summed_app_area_m2,
            "union_area_m2": self.union_area_m2,
            "overlap_area_m2": self.overlap_area_m2,
        }


@dataclass(frozen=True)
class PartitionAppDiagnostic:
    region: str
    app: str
    display_name: str
    categorie: str
    priority_rank: int
    priority_area_m2: float
    original_area_m2: float
    final_area_m2: float
    removed_overlap_area_m2: float
    removed_overlap_percent: float
    became_empty: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "app": self.app,
            "display_name": self.display_name,
            "categorie": self.categorie,
            "priority_rank": self.priority_rank,
            "priority_area_m2": self.priority_area_m2,
            "original_area_m2": self.original_area_m2,
            "final_area_m2": self.final_area_m2,
            "removed_overlap_area_m2": self.removed_overlap_area_m2,
            "removed_overlap_percent": self.removed_overlap_percent,
            "became_empty": self.became_empty,
        }


@dataclass(frozen=True)
class PartitionReport:
    strategy: str
    source_app_count: int
    retained_app_count: int
    partially_reduced_app_count: int
    fully_covered_app_count: int
    source_app_names: list[str]
    retained_app_names: list[str]
    partially_reduced_app_names: list[str]
    fully_covered_app_names: list[str]
    overlap_area_before_m2: float
    overlap_area_after_m2: float
    maximum_removed_percent: float
    overlap_tolerance_m2: float
    per_app: list[PartitionAppDiagnostic]

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "source_app_count": self.source_app_count,
            "retained_app_count": self.retained_app_count,
            "partially_reduced_app_count": self.partially_reduced_app_count,
            "fully_covered_app_count": self.fully_covered_app_count,
            "source_app_names": self.source_app_names,
            "retained_app_names": self.retained_app_names,
            "partially_reduced_app_names": self.partially_reduced_app_names,
            "fully_covered_app_names": self.fully_covered_app_names,
            "overlap_area_before_m2": self.overlap_area_before_m2,
            "overlap_area_after_m2": self.overlap_area_after_m2,
            "maximum_removed_percent": self.maximum_removed_percent,
            "overlap_tolerance_m2": self.overlap_tolerance_m2,
            "per_app": [item.as_dict() for item in self.per_app],
        }


@dataclass
class RegionStages:
    raw: gpd.GeoDataFrame
    repaired: gpd.GeoDataFrame
    dissolved: gpd.GeoDataFrame
    closed: gpd.GeoDataFrame
    simplified: gpd.GeoDataFrame
    partitioned: gpd.GeoDataFrame
    removed_overlap: gpd.GeoDataFrame
    final: gpd.GeoDataFrame
    overlap_before: OverlapMetrics
    overlap_after: OverlapMetrics
    overlap_tolerance_m2: float
    partition_report: PartitionReport | None
    parameters: SimplificationParameters


def slugify_region(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value).strip())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = "".join(character.lower() if character.isalnum() else "_" for character in ascii_value)
    return "_".join(part for part in slug.split("_") if part)


def validate_stage1_schema(gdf: gpd.GeoDataFrame) -> None:
    missing = [column for column in STAGE1_COLUMNS if column not in gdf.columns]
    if missing:
        raise ValueError(f"Stage 1 AOC-region input is missing columns: {', '.join(missing)}")
    if gdf.empty:
        raise ValueError("Stage 1 AOC-region input contains no rows.")
    if gdf.crs is None:
        raise ValueError("Stage 1 AOC-region input has no CRS.")
    for column in OUTPUT_IDENTITY_COLUMNS:
        values = gdf[column].fillna("").astype(str).str.strip()
        if values.eq("").any():
            raise ValueError(f"Stage 1 AOC-region input contains blank {column!r} values.")
    if gdf.geometry.isna().any():
        raise ValueError("Stage 1 AOC-region input contains null geometry.")
    if gdf.geometry.is_empty.any():
        raise ValueError("Stage 1 AOC-region input contains empty geometry.")


def select_region(gdf: gpd.GeoDataFrame, region: str) -> gpd.GeoDataFrame:
    validate_stage1_schema(gdf)
    selected = gdf.loc[gdf["region"].astype(str) == region, STAGE1_COLUMNS].copy()
    if selected.empty:
        available = sorted(gdf["region"].dropna().astype(str).unique().tolist())
        raise ValueError(f"Unknown region {region!r}. Available regions: {', '.join(available)}")
    return selected


def _polygon_parts(geom) -> list:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in {"MultiPolygon", "GeometryCollection"}:
        parts = []
        for child in geom.geoms:
            parts.extend(_polygon_parts(child))
        return parts
    return []


def repair_geometry(geom):
    if geom is None or geom.is_empty:
        return None
    repaired = geom
    if not geom.is_valid:
        try:
            repaired = make_valid(geom, method="structure", keep_collapsed=False)
        except TypeError:
            repaired = make_valid(geom)
        except Exception:
            repaired = geom
    if repaired is not None and not repaired.is_empty and not repaired.is_valid:
        try:
            repaired = repaired.buffer(0)
        except Exception:
            return None
    parts = _polygon_parts(repaired)
    if not parts:
        return None
    result = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    if not result.is_valid:
        try:
            result = result.buffer(0)
        except Exception:
            return None
        parts = _polygon_parts(result)
        if not parts:
            return None
        result = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    return result if result.geom_type in {"Polygon", "MultiPolygon"} else None


def repair_frame(gdf: gpd.GeoDataFrame, *, fail_on_loss: bool = False, context: str = "geometry") -> gpd.GeoDataFrame:
    source_rows = len(gdf)
    repaired = gdf.copy()
    repaired.geometry = repaired.geometry.apply(repair_geometry)
    repaired = repaired.loc[repaired.geometry.notna()].copy()
    repaired = repaired.loc[~repaired.geometry.is_empty].copy()
    repaired = repaired.reset_index(drop=True)
    if fail_on_loss and len(repaired) != source_rows:
        raise ValueError(f"{context} repair removed {source_rows - len(repaired)} rows.")
    if not repaired.empty and not repaired.geometry.is_valid.all():
        raise ValueError(f"{context} repair left invalid geometry.")
    return repaired


def project_for_operations(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError("Input geometry has no CRS.")
    return gdf.to_crs(WORKING_CRS)


def reproject_for_output(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError("Processed geometry has no CRS.")
    return gdf.to_crs(OUTPUT_CRS)


def validate_source_area(gdf: gpd.GeoDataFrame, *, context: str) -> None:
    if "source_area_m2" not in gdf.columns:
        raise ValueError(f"{context} is missing source_area_m2.")
    invalid = []
    for _, row in gdf.iterrows():
        try:
            value = float(row["source_area_m2"])
        except (TypeError, ValueError):
            invalid.append(str(row.get("app", "")))
            continue
        if not isfinite(value) or value < 0:
            invalid.append(str(row.get("app", "")))
    if invalid:
        raise ValueError(f"{context} contains invalid source_area_m2 for: {', '.join(sorted(invalid))}")


def dissolve_by_identity(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    dissolved = gdf[OUTPUT_IDENTITY_COLUMNS + ["geometry"]].dissolve(
        by=OUTPUT_IDENTITY_COLUMNS,
        as_index=False,
        sort=True,
    )
    dissolved = repair_frame(dissolved[OUTPUT_IDENTITY_COLUMNS + ["geometry"]], fail_on_loss=True, context="dissolved")
    dissolved["source_area_m2"] = dissolved.geometry.area.astype(float)
    return dissolved[OUTPUT_COLUMNS]


def calculate_overlap_metrics(gdf: gpd.GeoDataFrame) -> OverlapMetrics:
    if gdf.crs is None or gdf.crs.to_epsg() != 2154:
        raise ValueError(f"Overlap metrics require {WORKING_CRS} geometry.")
    geometries = [geometry for geometry in gdf.geometry if geometry is not None and not geometry.is_empty]
    summed_area = float(sum(geometry.area for geometry in geometries))
    union_area = float(unary_union(geometries).area) if geometries else 0.0
    return OverlapMetrics(summed_area, union_area, max(0.0, summed_area - union_area))


def overlap_tolerance_m2(union_area_m2: float) -> float:
    if not isfinite(union_area_m2) or union_area_m2 < 0:
        raise ValueError("Union area must be a finite non-negative value.")
    return max(OVERLAP_ABSOLUTE_TOLERANCE_M2, union_area_m2 * OVERLAP_RELATIVE_TOLERANCE)


def _app_names(gdf: gpd.GeoDataFrame) -> list[str]:
    return sorted(gdf["app"].dropna().astype(str).unique().tolist()) if "app" in gdf.columns else []


def _validate_partition_input(gdf: gpd.GeoDataFrame) -> str:
    if gdf.empty:
        raise ValueError("Partition input contains no appellations.")
    missing = [column for column in OUTPUT_COLUMNS if column not in gdf.columns]
    if missing:
        raise ValueError(f"Partition input is missing columns: {', '.join(missing)}")
    if gdf.crs is None or gdf.crs.to_epsg() != 2154:
        raise ValueError(f"Partition input must use {WORKING_CRS}.")
    regions = gdf["region"].dropna().astype(str).str.strip()
    unique_regions = sorted(name for name in regions.unique() if name)
    if len(regions) != len(gdf) or len(unique_regions) != 1:
        raise ValueError("Partition input must contain exactly one non-empty region.")
    for column in OUTPUT_IDENTITY_COLUMNS:
        values = gdf[column].dropna().astype(str).str.strip()
        if len(values) != len(gdf) or values.eq("").any():
            raise ValueError(f"Partition input column {column!r} contains missing values.")
    duplicates = sorted(
        gdf.loc[gdf[OUTPUT_IDENTITY_COLUMNS].astype(str).duplicated(keep=False), "app"]
        .astype(str)
        .unique()
    )
    if duplicates:
        raise ValueError("Partition input must contain one row per identity; duplicates: " + ", ".join(duplicates))
    if gdf.geometry.isna().any() or gdf.geometry.is_empty.any():
        raise ValueError("Partition input contains null or empty geometry.")
    if not gdf.geometry.is_valid.all():
        raise ValueError("Partition input contains invalid geometry.")
    unexpected_types = sorted(set(gdf.geom_type) - {"Polygon", "MultiPolygon"})
    if unexpected_types:
        raise ValueError("Partition input contains non-polygon geometry: " + ", ".join(unexpected_types))
    validate_source_area(gdf, context="Partition input")
    return unique_regions[0]


def partition_appellations_smallest_first(
    gdf: gpd.GeoDataFrame,
    *,
    tolerance_m2: float | None = None,
) -> tuple[gpd.GeoDataFrame, PartitionReport]:
    region = _validate_partition_input(gdf)
    working = gdf[OUTPUT_COLUMNS].copy().reset_index(drop=True)
    working["_source_order"] = range(len(working))
    working["_priority_area_m2"] = working.geometry.area.astype(float)
    working["_app_sort"] = working["app"].astype(str)
    working = working.sort_values(["_priority_area_m2", "_app_sort", "_source_order"], kind="mergesort")

    overlap_before = calculate_overlap_metrics(working)
    tolerance_m2 = overlap_tolerance_m2(overlap_before.union_area_m2) if tolerance_m2 is None else tolerance_m2
    if not isfinite(tolerance_m2) or tolerance_m2 < 0:
        raise ValueError("Overlap tolerance must be a finite non-negative value.")

    accepted_rows: list[dict[str, Any]] = []
    diagnostics: list[PartitionAppDiagnostic] = []
    claimed_geometry = None
    for priority_rank, (_, row) in enumerate(working.iterrows(), start=1):
        original_geometry = row.geometry
        original_area = float(row["_priority_area_m2"])
        candidate_geometry = original_geometry if claimed_geometry is None else original_geometry.difference(claimed_geometry)
        accepted_geometry = repair_geometry(candidate_geometry)
        final_area = 0.0 if accepted_geometry is None else float(accepted_geometry.area)
        removed_area = max(0.0, original_area - final_area)
        removed_percent = 0.0 if original_area == 0 else removed_area / original_area * 100
        became_empty = accepted_geometry is None or accepted_geometry.is_empty
        diagnostics.append(
            PartitionAppDiagnostic(
                region=region,
                app=str(row["app"]),
                display_name=str(row["display_name"]),
                categorie=str(row["categorie"]),
                priority_rank=priority_rank,
                priority_area_m2=original_area,
                original_area_m2=original_area,
                final_area_m2=final_area,
                removed_overlap_area_m2=removed_area,
                removed_overlap_percent=removed_percent,
                became_empty=became_empty,
            )
        )
        if not became_empty:
            accepted_rows.append({column: row[column] for column in OUTPUT_COLUMNS if column != "geometry"} | {"geometry": accepted_geometry})
        claimed_geometry = original_geometry if claimed_geometry is None else unary_union([claimed_geometry, original_geometry])

    partitioned = gpd.GeoDataFrame(accepted_rows, columns=OUTPUT_COLUMNS, geometry="geometry", crs=gdf.crs)
    partitioned = partitioned.sort_values(OUTPUT_IDENTITY_COLUMNS, kind="mergesort").reset_index(drop=True)
    overlap_after = calculate_overlap_metrics(partitioned)
    if overlap_after.overlap_area_m2 > tolerance_m2:
        raise ValueError(
            f"Residual overlap exceeds tolerance: {overlap_after.overlap_area_m2:.12g} m2 > {tolerance_m2:.12g} m2."
        )

    fully_covered = sorted(item.app for item in diagnostics if item.became_empty)
    partially_reduced = sorted(
        item.app
        for item in diagnostics
        if not item.became_empty and item.removed_overlap_area_m2 > tolerance_m2
    )
    report = PartitionReport(
        strategy="smallest-wins",
        source_app_count=len(working),
        retained_app_count=len(partitioned),
        partially_reduced_app_count=len(partially_reduced),
        fully_covered_app_count=len(fully_covered),
        source_app_names=_app_names(working),
        retained_app_names=_app_names(partitioned),
        partially_reduced_app_names=partially_reduced,
        fully_covered_app_names=fully_covered,
        overlap_area_before_m2=overlap_before.overlap_area_m2,
        overlap_area_after_m2=overlap_after.overlap_area_m2,
        maximum_removed_percent=max((item.removed_overlap_percent for item in diagnostics), default=0.0),
        overlap_tolerance_m2=tolerance_m2,
        per_app=diagnostics,
    )
    return partitioned, report


def removed_overlap_frame(original: gpd.GeoDataFrame, partitioned: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    retained_by_key = {
        tuple(str(row[column]) for column in OUTPUT_IDENTITY_COLUMNS): row.geometry
        for _, row in partitioned.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for _, row in original.iterrows():
        key = tuple(str(row[column]) for column in OUTPUT_IDENTITY_COLUMNS)
        retained = retained_by_key.get(key)
        removed = row.geometry if retained is None else row.geometry.difference(retained)
        removed = repair_geometry(removed)
        if removed is None:
            continue
        rows.append({column: row[column] for column in OUTPUT_COLUMNS if column != "geometry"} | {"geometry": removed})
    return gpd.GeoDataFrame(rows, columns=OUTPUT_COLUMNS, geometry="geometry", crs=original.crs).reset_index(drop=True)


def count_coordinates(geom) -> int:
    if geom is None or geom.is_empty:
        return 0
    if geom.geom_type == "Point":
        return 1
    if geom.geom_type == "MultiPoint":
        return sum(count_coordinates(part) for part in geom.geoms)
    if geom.geom_type in {"LineString", "LinearRing"}:
        return len(geom.coords)
    if geom.geom_type == "MultiLineString":
        return sum(count_coordinates(part) for part in geom.geoms)
    if geom.geom_type == "Polygon":
        exterior = len(geom.exterior.coords) if geom.exterior else 0
        interiors = sum(len(ring.coords) for ring in geom.interiors)
        return exterior + interiors
    if geom.geom_type in {"MultiPolygon", "GeometryCollection"}:
        return sum(count_coordinates(part) for part in geom.geoms)
    return 0


def count_polygon_parts(geom) -> int:
    if geom is None or geom.is_empty:
        return 0
    if geom.geom_type == "Polygon":
        return 1
    if geom.geom_type in {"MultiPolygon", "GeometryCollection"}:
        return sum(count_polygon_parts(part) for part in geom.geoms)
    return 0


def approximate_geojson_size_mb(gdf: gpd.GeoDataFrame) -> float:
    if gdf.empty:
        return 0.0
    return len(reproject_for_output(gdf).to_json().encode("utf-8")) / (1024 * 1024)


def metrics_for_frame(gdf: gpd.GeoDataFrame | None) -> dict[str, Any]:
    if gdf is None or gdf.empty:
        return {
            "feature_count": 0,
            "app_count": 0,
            "coordinate_count": 0,
            "polygon_part_count": 0,
            "invalid_geometry_count": 0,
            "empty_geometry_count": 0,
            "approx_geojson_size_mb": 0.0,
            "area_m2_epsg_2154": 0.0,
        }
    metric = project_for_operations(gdf)
    geometries = gdf.geometry.dropna()
    return {
        "feature_count": int(len(gdf)),
        "app_count": int(gdf["app"].nunique()) if "app" in gdf.columns else 0,
        "coordinate_count": int(geometries.apply(count_coordinates).sum()),
        "polygon_part_count": int(geometries.apply(count_polygon_parts).sum()),
        "invalid_geometry_count": int((~geometries.is_valid).sum()),
        "empty_geometry_count": int(geometries.is_empty.sum() + gdf.geometry.isna().sum()),
        "approx_geojson_size_mb": round(approximate_geojson_size_mb(gdf), 6),
        "area_m2_epsg_2154": float(metric.geometry.area.sum()),
    }


def simplify_region(
    stage1_region: gpd.GeoDataFrame,
    *,
    parameters: SimplificationParameters | None = None,
) -> RegionStages:
    parameters = parameters or SimplificationParameters()
    if parameters.buffer_m < 0 or parameters.simplify_m < 0:
        raise ValueError("Buffer and simplification distances must be non-negative.")
    if parameters.overlap_strategy not in {"none", "smallest-wins"}:
        raise ValueError(f"Unknown overlap strategy: {parameters.overlap_strategy!r}.")
    if stage1_region.empty:
        raise ValueError("Selected region contains no source rows.")

    validate_stage1_schema(stage1_region)
    raw = stage1_region[OUTPUT_IDENTITY_COLUMNS + ["geometry"]].copy().reset_index(drop=True)
    repaired = repair_frame(project_for_operations(raw), fail_on_loss=True, context="source")
    if repaired.empty:
        raise ValueError("No polygon geometry remained before dissolve.")

    dissolved = dissolve_by_identity(repaired)
    closed = dissolved.copy()
    if parameters.buffer_m > 0:
        closed.geometry = closed.geometry.buffer(parameters.buffer_m).buffer(-parameters.buffer_m)
        closed = repair_frame(closed, fail_on_loss=False, context="morphological closing")
    if closed.empty:
        raise ValueError("No polygon geometry remained after morphological closing.")

    simplified = closed.copy()
    if parameters.simplify_m > 0:
        simplified.geometry = simplified.geometry.simplify(parameters.simplify_m, preserve_topology=True)
    simplified = repair_frame(simplified, fail_on_loss=False, context="simplification")[OUTPUT_COLUMNS]
    if simplified.empty:
        raise ValueError("No polygon geometry remained after simplification.")

    overlap_before = calculate_overlap_metrics(simplified)
    tolerance_m2 = overlap_tolerance_m2(overlap_before.union_area_m2)
    if parameters.overlap_strategy == "smallest-wins":
        partitioned, partition_report = partition_appellations_smallest_first(simplified, tolerance_m2=tolerance_m2)
    else:
        partitioned = simplified.copy()
        partition_report = None
    removed_overlap = removed_overlap_frame(simplified, partitioned)
    overlap_after = calculate_overlap_metrics(partitioned)

    final_working = repair_frame(partitioned, fail_on_loss=False, context="final candidate")[OUTPUT_COLUMNS]
    missing_apps = set(_app_names(simplified)) - set(_app_names(final_working))
    reported_empty = set(partition_report.fully_covered_app_names) if partition_report else set()
    if missing_apps != reported_empty:
        raise ValueError(
            "Final repair lost appellations without matching fully-covered diagnostics: "
            + ", ".join(sorted(missing_apps ^ reported_empty))
        )
    if final_working.geometry.isna().any() or final_working.geometry.is_empty.any():
        raise ValueError("Final candidate contains null or empty geometry.")
    if not final_working.geometry.is_valid.all():
        raise ValueError("Final candidate contains invalid geometry.")
    if not set(final_working.geom_type).issubset({"Polygon", "MultiPolygon"}):
        raise ValueError("Final candidate contains non-polygon geometry.")
    validate_source_area(final_working, context="Final candidate")
    final_overlap = calculate_overlap_metrics(final_working)
    if parameters.overlap_strategy == "smallest-wins" and final_overlap.overlap_area_m2 > tolerance_m2:
        raise ValueError("Final candidate residual overlap exceeds tolerance.")

    final = reproject_for_output(final_working)[OUTPUT_COLUMNS]
    validate_source_area(final, context="Reprojected final candidate")
    return RegionStages(
        raw=raw,
        repaired=repaired,
        dissolved=dissolved,
        closed=closed,
        simplified=simplified,
        partitioned=partitioned,
        removed_overlap=removed_overlap,
        final=final.sort_values(OUTPUT_IDENTITY_COLUMNS, kind="mergesort").reset_index(drop=True),
        overlap_before=overlap_before,
        overlap_after=overlap_after,
        overlap_tolerance_m2=tolerance_m2,
        partition_report=partition_report,
        parameters=parameters,
    )

