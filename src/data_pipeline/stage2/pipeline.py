"""France departmental transformation and application-product publication."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import tempfile

import geopandas as gpd
from geopandas.testing import assert_geodataframe_equal
import pandas as pd
from pandas.testing import assert_frame_equal

from .schema import (
    LACAVE_ADDRESS_OVERRIDES,
    REGION_TRANSLATIONS,
    STATS_COLUMNS,
    departmental_property_columns,
    regional_property_columns,
    restaurant_input_columns,
    restaurant_output_columns,
    star_categories,
)
from .validation import (
    Stage2Validation,
    Stage2ValidationError,
    require_columns,
    validate_department_output,
    validate_reference_data,
    validate_region_geometry,
    validate_region_output,
    validate_restaurant_output,
)


POSTAL_CODE_PATTERN = re.compile(r"\b\d{5}\b")


class Stage2PublicationError(RuntimeError):
    """Raised when validated Stage 2 products cannot be published safely."""


@dataclass(frozen=True)
class Stage2Result:
    year: int
    restaurants: pd.DataFrame
    departments: gpd.GeoDataFrame
    regions: gpd.GeoDataFrame
    validation: Stage2Validation
    paths: dict[str, Path]


def product_paths(year: int, output_root: Path) -> dict[str, Path]:
    year_root = output_root / "france" / str(year)
    return {
        "restaurants": year_root / "all_restaurants.csv",
        "departments": year_root / "geodata" / "department_restaurants.geojson",
        "regions": year_root / "geodata" / "region_restaurants.geojson",
    }


def _strip_nbsp(value: object) -> object:
    return value.replace("\xa0", "") if isinstance(value, str) else value


def _parse_address(address: object) -> tuple[str, str, str]:
    if not isinstance(address, str):
        raise Stage2ValidationError(f"Restaurant address is not text: {address!r}")
    cleaned = address.replace("\xa0", "")
    postal_codes = POSTAL_CODE_PATTERN.findall(cleaned)
    if len(postal_codes) != 1:
        raise Stage2ValidationError(
            f"Restaurant address must contain exactly one postal code: {address!r}"
        )
    parts = [part.strip() for part in cleaned.split(",")]
    if len(parts) < 4:
        raise Stage2ValidationError(
            f"Restaurant address cannot be split into address, city, postal code, country: {address!r}"
        )
    main_address = ", ".join(parts[:-3])
    city, postal_code, country = parts[-3:]
    if not main_address or not city or postal_code != postal_codes[0] or country != "France":
        raise Stage2ValidationError(f"Ambiguous restaurant address: {address!r}")
    return main_address, city, postal_code


def _parse_restaurant_address(row: pd.Series, year: int) -> tuple[str, str, str]:
    override = LACAVE_ADDRESS_OVERRIDES.get((year, row["name"], row["address"]))
    if override is not None:
        return override
    return _parse_address(row["address"])


def _department_code(postal_code: str) -> str:
    if postal_code.startswith(("200", "201")):
        return "2A"
    if postal_code.startswith("202"):
        return "2B"
    return postal_code[:2]


def enrich_restaurants(
    partition: pd.DataFrame,
    departments: pd.DataFrame,
    *,
    year: int,
) -> pd.DataFrame:
    """Normalize addresses and attach one authoritative department assignment."""

    require_columns(partition, restaurant_input_columns(year), "France partition")
    if not partition["country"].eq("France").all():
        invalid = sorted(partition.loc[~partition["country"].eq("France"), "country"].unique())
        raise Stage2ValidationError(f"France partition contains other countries: {invalid}")

    working = partition.loc[:, restaurant_input_columns(year)].copy()
    working = working.map(_strip_nbsp)
    parsed = working.apply(_parse_restaurant_address, axis=1, year=year)
    parsed_frame = pd.DataFrame(
        parsed.tolist(),
        columns=["normalized_address", "parsed_city", "postal_code"],
        index=working.index,
    )
    working["address"] = parsed_frame["normalized_address"]
    working["location"] = (
        parsed_frame["parsed_city"] + ", " + parsed_frame["postal_code"]
    )
    working["department_num"] = parsed_frame["postal_code"].map(_department_code)

    reference = departments.copy()
    reference["department_num"] = reference["department_num"].astype("string")
    try:
        enriched = working.merge(
            reference,
            on="department_num",
            how="left",
            validate="many_to_one",
            sort=False,
        )
    except pd.errors.MergeError as error:
        raise Stage2ValidationError("Department reference join is not many-to-one") from error

    unmatched = enriched.loc[enriched["department"].isna(), ["name", "department_num"]]
    if not unmatched.empty:
        raise Stage2ValidationError(
            f"Unmatched restaurant departments: {unmatched.to_dict('records')}"
        )

    enriched.drop(columns=["city", "country", "postal_code"], errors="ignore", inplace=True)
    enriched = enriched.loc[:, restaurant_output_columns(year)]
    validate_restaurant_output(enriched, source_rows=len(partition), year=year)
    return enriched


def aggregate_departments(
    restaurants: pd.DataFrame,
    statistics: pd.DataFrame,
    geometry: gpd.GeoDataFrame,
    *,
    year: int,
) -> gpd.GeoDataFrame:
    """Build counts, coordinate groups, accepted statistics, and geometry."""

    working = restaurants.copy()
    count_columns: list[str] = []
    for star_value, _label, column in star_categories(year):
        working[column] = working["stars"].eq(star_value).astype(int)
        count_columns.append(column)
    if year >= 2025:
        working["green_stars"] = working["greenstar"].eq(1).astype(int)
        count_columns.append("green_stars")

    grouped = (
        working.groupby("department_num", sort=True)[count_columns]
        .sum()
        .reset_index()
    )

    accepted = statistics.copy()
    accepted["department_num"] = accepted["department_num"].astype("string")
    departmental = accepted.merge(
        grouped,
        on="department_num",
        how="left",
        validate="one_to_one",
        sort=False,
    )
    departmental[count_columns] = departmental[count_columns].fillna(0).astype(int)
    departmental["total_stars"] = (
        departmental["1_star"]
        + departmental["2_star"] * 2
        + departmental["3_star"] * 3
    )
    departmental["starred_restaurants"] = (
        departmental["1_star"] + departmental["2_star"] + departmental["3_star"]
    )

    coordinate_groups = (
        working.groupby(["department_num", "stars"], sort=False)[["latitude", "longitude"]]
        .apply(lambda frame: list(zip(frame["latitude"], frame["longitude"])))
        .to_dict()
    )

    def location_payload(department_num: str) -> str:
        payload = {
            label: coordinate_groups.get((department_num, star_value))
            for star_value, label, _column in star_categories(year)
        }
        return str(payload)

    departmental["locations"] = departmental["department_num"].map(location_payload)

    category_columns = [category[2] for category in star_categories(year)]
    ordered = ["department_num", "department", "capital", "region"]
    ordered.extend(category_columns)
    ordered.extend(("total_stars", "starred_restaurants"))
    if year >= 2025:
        ordered.append("green_stars")
    ordered.extend(STATS_COLUMNS)
    ordered.append("locations")
    departmental = departmental.loc[:, ordered]

    base = geometry.copy()
    base["code"] = base["code"].astype("string")
    product = base.merge(
        departmental,
        left_on="code",
        right_on="department_num",
        how="left",
        validate="one_to_one",
        sort=False,
    )
    product.drop(columns=["nom", "department_num"], inplace=True)
    product["region"] = product["region"].replace(REGION_TRANSLATIONS)
    property_order = list(departmental_property_columns(year))
    product = gpd.GeoDataFrame(
        product.loc[:, [*property_order, "geometry"]],
        geometry="geometry",
        crs=geometry.crs,
    )
    validate_department_output(product, expected_codes=set(base["code"]), year=year)
    return product


def aggregate_regions(
    restaurants: pd.DataFrame,
    statistics: pd.DataFrame,
    geometry: gpd.GeoDataFrame,
    *,
    year: int,
) -> gpd.GeoDataFrame:
    """Aggregate restaurant and population-weighted statistics by region."""

    working = restaurants.copy()
    count_columns: list[str] = []
    for star_value, _label, column in star_categories(year):
        working[column] = working["stars"].eq(star_value).astype(int)
        count_columns.append(column)
    if year >= 2025:
        working["green_stars"] = working["greenstar"].eq(1).astype(int)
        count_columns.append("green_stars")

    counts = working.groupby("region", sort=True)[count_columns].sum().reset_index()
    counts["total_stars"] = counts["1_star"] + 2 * counts["2_star"] + 3 * counts["3_star"]
    counts["starred_restaurants"] = counts["1_star"] + counts["2_star"] + counts["3_star"]

    stats = statistics.copy()
    population = "municipal_population"
    weighted = (
        "poverty_rate(%)",
        "average_annual_unemployment_rate(%)",
        "average_net_hourly_wage(€)",
    )
    for column in weighted:
        stats[f"_{column}"] = stats[column] * stats[population]
    aggregations = {
        "GDP_millions(€)": "sum",
        population: "sum",
        "area(sq_km)": "sum",
        **{f"_{column}": "sum" for column in weighted},
    }
    regional_stats = stats.groupby("region", sort=True).agg(aggregations).reset_index()
    regional_stats["GDP_per_capita(€)"] = (
        regional_stats["GDP_millions(€)"] * 1_000_000 / regional_stats[population]
    )
    regional_stats["population_density(inhabitants/sq_km)"] = (
        regional_stats[population] / regional_stats["area(sq_km)"]
    ).round(2)
    for column in weighted:
        regional_stats[column] = regional_stats.pop(f"_{column}") / regional_stats[population]

    regional = counts.merge(regional_stats, on="region", how="left", validate="one_to_one")
    coordinate_groups = (
        working.groupby(["region", "stars"], sort=False)[["latitude", "longitude"]]
        .apply(lambda frame: list(zip(frame["latitude"], frame["longitude"])))
        .to_dict()
    )
    regional["locations"] = regional["region"].map(
        lambda region: str({
            label: coordinate_groups.get((region, star_value))
            for star_value, label, _column in star_categories(year)
        })
    )
    regional["region"] = regional["region"].replace(REGION_TRANSLATIONS)

    base = geometry.copy()
    product = base.merge(
        regional,
        left_on="nom",
        right_on="region",
        how="left",
        validate="one_to_one",
        sort=False,
    )
    product.drop(columns=["code", "nom"], inplace=True)
    product = gpd.GeoDataFrame(
        product.loc[:, [*regional_property_columns(year), "geometry"]],
        geometry="geometry",
        crs=geometry.crs,
    )
    validate_region_output(product, expected_regions=set(base["nom"]), year=year)
    return product


def _load_inputs(
    *,
    year: int,
    partition_root: Path,
    departments_path: Path,
    statistics_path: Path,
    geometry_path: Path,
    region_geometry_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    paths = {
        "France partition": partition_root / "france" / f"france_{year}.csv",
        "department reference": departments_path,
        "departmental statistics": statistics_path,
        "department geometry": geometry_path,
        "region geometry": region_geometry_path,
    }
    missing = [f"{label}: {path}" for label, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing Stage 2 inputs: " + ", ".join(missing))

    partition = pd.read_csv(paths["France partition"])
    departments = pd.read_csv(departments_path, dtype={"department_num": "string"})
    statistics = pd.read_csv(statistics_path, dtype={"department_num": "string"})
    geometry = gpd.read_file(geometry_path)
    region_geometry = gpd.read_file(region_geometry_path)
    geometry["code"] = geometry["code"].astype("string")
    validate_reference_data(departments, statistics, geometry)
    validate_region_geometry(region_geometry)
    return partition, departments, statistics, geometry, region_geometry


def validate_stage2(
    *,
    year: int,
    partition_root: Path = Path("data/partitions"),
    departments_path: Path = Path("data/raw/demographics/departments.csv"),
    statistics_path: Path = Path("data/raw/demographics/departmental_stats_2023.csv"),
    geometry_path: Path = Path("data/raw/geodata/departments.geojson"),
    region_geometry_path: Path = Path("data/raw/geodata/regions.geojson"),
) -> Stage2Result:
    if year < 2025:
        raise Stage2ValidationError(
            "France departmental Stage 2 is supported from 2025; earlier outputs "
            "used different demographic snapshots and require a separate fidelity contract"
        )
    partition, departments, statistics, geometry, region_geometry = _load_inputs(
        year=year,
        partition_root=partition_root,
        departments_path=departments_path,
        statistics_path=statistics_path,
        geometry_path=geometry_path,
        region_geometry_path=region_geometry_path,
    )
    restaurants = enrich_restaurants(partition, departments, year=year)
    department_product = aggregate_departments(
        restaurants,
        statistics,
        geometry,
        year=year,
    )
    region_product = aggregate_regions(restaurants, statistics, region_geometry, year=year)
    validation = Stage2Validation(
        restaurant_rows=len(restaurants),
        department_rows=len(department_product),
        region_rows=len(region_product),
    )
    return Stage2Result(
        year=year,
        restaurants=restaurants,
        departments=department_product,
        regions=region_product,
        validation=validation,
        paths={},
    )


def _write_staged_products(result: Stage2Result, staging_root: Path) -> dict[str, Path]:
    paths = product_paths(result.year, staging_root)
    paths["restaurants"].parent.mkdir(parents=True, exist_ok=True)
    result.restaurants.to_csv(paths["restaurants"], index=False, lineterminator="\n")
    paths["departments"].parent.mkdir(parents=True, exist_ok=True)
    result.departments.to_file(paths["departments"], driver="GeoJSON")
    result.regions.to_file(paths["regions"], driver="GeoJSON")

    reloaded_restaurants = pd.read_csv(
        paths["restaurants"], dtype={"department_num": "string"}
    )
    try:
        assert_frame_equal(
            result.restaurants.reset_index(drop=True),
            reloaded_restaurants,
            check_dtype=False,
            check_like=False,
        )
        reloaded_departments = gpd.read_file(paths["departments"])
        assert_geodataframe_equal(
            result.departments.reset_index(drop=True),
            reloaded_departments,
            check_dtype=False,
            check_like=False,
        )
        reloaded_regions = gpd.read_file(paths["regions"])
        assert_geodataframe_equal(
            result.regions.reset_index(drop=True),
            reloaded_regions,
            check_dtype=False,
            check_like=False,
            check_less_precise=True,
        )
    except AssertionError as error:
        raise Stage2PublicationError("Serialized Stage 2 products failed reload validation") from error
    return paths


def _publish_products(
    result: Stage2Result,
    *,
    output_root: Path,
    replace: bool,
) -> dict[str, Path]:
    final_paths = product_paths(result.year, output_root)
    existing = {name: path for name, path in final_paths.items() if path.exists()}
    if existing and not replace:
        raise FileExistsError(
            "Refusing to replace existing Stage 2 products without --replace: "
            + ", ".join(str(path) for path in existing.values())
        )

    output_root.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".stage2-{result.year}-", dir=output_root)
    )
    backup_root = staging_root / "backups"
    published: list[str] = []
    cleanup_staging = True
    try:
        staged = _write_staged_products(result, staging_root / "candidate")
        for name, path in existing.items():
            backup = backup_root / name / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)

        for name in ("restaurants", "departments", "regions"):
            final = final_paths[name]
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged[name], final)
            published.append(name)
    except Exception as error:
        rollback_errors: list[str] = []
        for name in reversed(published):
            final = final_paths[name]
            backup = backup_root / name / final.name
            try:
                if backup.exists():
                    os.replace(backup, final)
                else:
                    final.unlink(missing_ok=True)
            except Exception as rollback_error:  # pragma: no cover
                rollback_errors.append(f"{name}: {rollback_error}")
        detail = f"Stage 2 publication failed and was rolled back: {error}"
        if rollback_errors:
            cleanup_staging = False
            detail += (
                "; rollback errors: "
                + ", ".join(rollback_errors)
                + f"; recovery files retained at {staging_root}"
            )
        raise Stage2PublicationError(detail) from error
    finally:
        if cleanup_staging:
            shutil.rmtree(staging_root, ignore_errors=True)
    return final_paths


def run_stage2(
    *,
    year: int,
    partition_root: Path = Path("data/partitions"),
    departments_path: Path = Path("data/raw/demographics/departments.csv"),
    statistics_path: Path = Path("data/raw/demographics/departmental_stats_2023.csv"),
    geometry_path: Path = Path("data/raw/geodata/departments.geojson"),
    region_geometry_path: Path = Path("data/raw/geodata/regions.geojson"),
    output_root: Path = Path("data/products"),
    replace: bool = False,
) -> Stage2Result:
    prepared = validate_stage2(
        year=year,
        partition_root=partition_root,
        departments_path=departments_path,
        statistics_path=statistics_path,
        geometry_path=geometry_path,
        region_geometry_path=region_geometry_path,
    )
    paths = _publish_products(prepared, output_root=output_root, replace=replace)
    return Stage2Result(
        year=year,
        restaurants=prepared.restaurants,
        departments=prepared.departments,
        regions=prepared.regions,
        validation=prepared.validation,
        paths=paths,
    )
