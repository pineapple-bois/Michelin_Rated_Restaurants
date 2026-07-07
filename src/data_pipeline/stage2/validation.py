"""Validation for France departmental transformations and products."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd

from .schema import (
    FRANCE_INSEE_PRODUCT_COLUMNS,
    france_departmental_property_columns,
    france_regional_property_columns,
    restaurant_output_columns,
)


class Stage2ValidationError(ValueError):
    """Raised when a Stage 2 production invariant is not satisfied."""


@dataclass(frozen=True)
class Stage2Validation:
    restaurant_rows: int
    department_rows: int
    region_rows: int
    unmatched_restaurants: tuple[str, ...] = ()
    unmatched_statistics: tuple[str, ...] = ()
    unmatched_geometries: tuple[str, ...] = ()


def require_columns(frame: pd.DataFrame, columns: tuple[str, ...], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise Stage2ValidationError(f"{label} is missing required columns: {missing}")


def validate_reference_data(
    departments: pd.DataFrame,
    statistics: pd.DataFrame,
    geometry: gpd.GeoDataFrame,
) -> None:
    require_columns(
        departments,
        ("department_num", "department", "capital", "region"),
        "department reference",
    )
    require_columns(
        statistics,
        FRANCE_INSEE_PRODUCT_COLUMNS,
        "INSEE departmental product",
    )
    require_columns(geometry, ("code", "nom", "geometry"), "department geometry")

    for label, frame, key in (
        ("department reference", departments, "department_num"),
        ("INSEE departmental product", statistics, "department_code"),
        ("department geometry", geometry, "code"),
    ):
        if frame[key].isna().any() or frame[key].duplicated().any():
            raise Stage2ValidationError(f"{label} keys must be non-null and unique")

    code_sets = {
        "department reference": set(departments["department_num"]),
        "INSEE departmental product": set(statistics["department_code"]),
        "department geometry": set(geometry["code"]),
    }
    if len({frozenset(codes) for codes in code_sets.values()}) != 1:
        raise Stage2ValidationError(f"Reference department code sets differ: {code_sets}")

    reference = departments.set_index("department_num")
    accepted = statistics.set_index("department_code")
    name_mismatches = reference.index[
        reference["department"].astype(str) != accepted.loc[reference.index, "department_name"].astype(str)
    ].tolist()
    if name_mismatches:
        raise Stage2ValidationError(
            f"Reference department values disagree for codes: {name_mismatches}"
        )
    for column in ("department", "capital", "region"):
        if column == "department":
            continue
        mismatched = reference.index[
            reference[column].astype(str) != accepted.loc[reference.index, column].astype(str)
        ].tolist()
        if mismatched:
            raise Stage2ValidationError(
                f"Reference {column} values disagree for codes: {mismatched}"
            )

    if geometry.crs is None or geometry.crs.to_epsg() != 4326:
        raise Stage2ValidationError(f"Department geometry must use EPSG:4326, found {geometry.crs}")
    if geometry.geometry.isna().any() or geometry.geometry.is_empty.any():
        raise Stage2ValidationError("Department geometry contains null or empty features")
    if (~geometry.geometry.is_valid).any():
        raise Stage2ValidationError("Department geometry contains invalid features")


def validate_region_geometry(geometry: gpd.GeoDataFrame) -> None:
    require_columns(geometry, ("code", "nom", "geometry"), "region geometry")
    if geometry["code"].isna().any() or geometry["code"].duplicated().any():
        raise Stage2ValidationError("Region geometry codes must be non-null and unique")
    if geometry["nom"].isna().any() or geometry["nom"].duplicated().any():
        raise Stage2ValidationError("Region geometry names must be non-null and unique")
    if geometry.crs is None or geometry.crs.to_epsg() != 4326:
        raise Stage2ValidationError(f"Region geometry must use EPSG:4326, found {geometry.crs}")
    if geometry.geometry.isna().any() or geometry.geometry.is_empty.any():
        raise Stage2ValidationError("Region geometry contains null or empty features")
    if (~geometry.geometry.is_valid).any():
        raise Stage2ValidationError("Region geometry contains invalid features")


def validate_restaurant_output(
    restaurants: pd.DataFrame,
    *,
    source_rows: int,
    year: int,
) -> None:
    if len(restaurants) != source_rows:
        raise Stage2ValidationError(
            f"Restaurant processing lost rows: expected {source_rows}, found {len(restaurants)}"
        )
    if tuple(restaurants.columns) != restaurant_output_columns(year):
        raise Stage2ValidationError("Enriched restaurant output schema or ordering changed")
    required = [
        "name", "address", "location", "department_num", "department",
        "capital", "region", "award", "stars", "longitude", "latitude",
    ]
    if restaurants[required].isna().any().any():
        nulls = restaurants[required].isna().sum()
        raise Stage2ValidationError(
            f"Enriched restaurants contain required nulls: {nulls[nulls > 0].to_dict()}"
        )
    if restaurants["department_num"].eq("20").any():
        raise Stage2ValidationError("Corsica contains unresolved department code 20")
    if restaurants.duplicated().any():
        raise Stage2ValidationError("Enriched restaurant output contains exact duplicates")


def validate_department_output(
    departments: gpd.GeoDataFrame,
    *,
    expected_codes: set[str],
    year: int,
) -> None:
    expected_columns = (*france_departmental_property_columns(year), "geometry")
    if tuple(departments.columns) != expected_columns:
        raise Stage2ValidationError(
            f"Department product schema mismatch: expected {expected_columns}, "
            f"found {tuple(departments.columns)}"
        )
    if len(departments) != len(expected_codes):
        raise Stage2ValidationError(
            f"Department product has {len(departments)} rows; expected {len(expected_codes)}"
        )
    if set(departments["code"]) != expected_codes or departments["code"].duplicated().any():
        raise Stage2ValidationError("Department product has missing or duplicate codes")
    property_columns = [column for column in departments.columns if column != "geometry"]
    if departments[property_columns].isna().any().any():
        nulls = departments[property_columns].isna().sum()
        raise Stage2ValidationError(
            f"Department product contains property nulls: {nulls[nulls > 0].to_dict()}"
        )
    if departments.geometry.isna().any() or departments.geometry.is_empty.any():
        raise Stage2ValidationError("Department product contains null or empty geometry")
    if (~departments.geometry.is_valid).any():
        raise Stage2ValidationError("Department product contains invalid geometry")


def validate_region_output(
    regions: gpd.GeoDataFrame,
    *,
    expected_regions: set[str],
    year: int,
) -> None:
    expected_columns = (*france_regional_property_columns(year), "geometry")
    if tuple(regions.columns) != expected_columns:
        raise Stage2ValidationError(
            f"Region product schema mismatch: expected {expected_columns}, "
            f"found {tuple(regions.columns)}"
        )
    if set(regions["region"]) != expected_regions or regions["region"].duplicated().any():
        raise Stage2ValidationError("Region product has missing or duplicate regions")
    properties = [column for column in regions.columns if column != "geometry"]
    if regions[properties].isna().any().any():
        nulls = regions[properties].isna().sum()
        raise Stage2ValidationError(
            f"Region product contains property nulls: {nulls[nulls > 0].to_dict()}"
        )
    if regions.geometry.isna().any() or regions.geometry.is_empty.any():
        raise Stage2ValidationError("Region product contains null or empty geometry")
    if (~regions.geometry.is_valid).any():
        raise Stage2ValidationError("Region product contains invalid geometry")
