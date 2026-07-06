"""Monaco Stage 2 transformation and application-product publication."""

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

from .pipeline import Stage2PublicationError
from .schema import STATS_COLUMNS, departmental_property_columns, star_categories
from .validation import Stage2ValidationError, require_columns


MONACO_CODE = "98"
MONACO_REGION = "Provence-Alpes-Côte d'Azur"
MONACO_COUNTRIES = {"France", "Principality of Monaco"}
POSTAL_CODE_PATTERN = re.compile(r"^98\d{3}$")


@dataclass(frozen=True)
class MonacoValidation:
    restaurant_rows: int
    aggregate_rows: int
    malformed_addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class MonacoResult:
    year: int
    restaurants: pd.DataFrame
    aggregate: gpd.GeoDataFrame
    validation: MonacoValidation
    paths: dict[str, Path]


def monaco_product_paths(year: int, output_root: Path) -> dict[str, Path]:
    year_root = output_root / "france" / str(year)
    return {
        "restaurants": year_root / "monaco_restaurants.csv",
        "aggregate": year_root / "geodata" / "monaco_restaurants.geojson",
    }


def monaco_restaurant_columns(year: int) -> tuple[str, ...]:
    columns = [
        "name", "address", "location", "arrondissement", "department_num",
        "department", "capital", "region", "price", "cuisine", "url", "award",
    ]
    if year >= 2025:
        columns.append("greenstar")
    columns.extend(("stars", "longitude", "latitude"))
    return tuple(columns)


def _parse_monaco_address(address: object) -> tuple[str, str]:
    if not isinstance(address, str):
        raise Stage2ValidationError(f"Monaco restaurant address is not text: {address!r}")
    parts = [part.strip() for part in address.replace("\xa0", "").split(",")]
    if len(parts) < 4:
        raise Stage2ValidationError(
            f"Monaco address cannot be split into address, city, postal code, country: {address!r}"
        )
    main_address = ", ".join(parts[:-3])
    city, postal_code, country = parts[-3:]
    if (
        not main_address
        or city != "Monaco"
        or not POSTAL_CODE_PATTERN.fullmatch(postal_code)
        or country not in MONACO_COUNTRIES
    ):
        raise Stage2ValidationError(f"Ambiguous Monaco restaurant address: {address!r}")
    return main_address, f"{city}, {postal_code}"


def prepare_monaco_restaurants(partition: pd.DataFrame, *, year: int) -> pd.DataFrame:
    required = [
        "name", "address", "city", "country", "price", "cuisine", "url",
        "award", "stars", "longitude", "latitude",
    ]
    if year >= 2025:
        required.append("greenstar")
    require_columns(partition, tuple(required), "Monaco partition")
    if not partition["country"].isin(MONACO_COUNTRIES).all():
        invalid = sorted(partition.loc[~partition["country"].isin(MONACO_COUNTRIES), "country"].unique())
        raise Stage2ValidationError(f"Monaco partition contains unexpected countries: {invalid}")

    working = partition.loc[:, required].copy()
    parsed = working["address"].map(_parse_monaco_address)
    working["address"] = parsed.map(lambda value: value[0])
    working["location"] = parsed.map(lambda value: value[1])
    working["arrondissement"] = "Monaco"
    working["department_num"] = MONACO_CODE
    working["department"] = "Monaco"
    working["capital"] = "Monaco"
    working["region"] = MONACO_REGION
    working = working.loc[:, monaco_restaurant_columns(year)]

    if len(working) != len(partition):
        raise Stage2ValidationError("Monaco preparation lost restaurant rows")
    required_output = [
        "name", "address", "location", "department_num", "department", "capital",
        "region", "award", "stars", "longitude", "latitude",
    ]
    if working[required_output].isna().any().any():
        raise Stage2ValidationError("Monaco restaurant output contains required nulls")
    if not working["department_num"].eq(MONACO_CODE).all():
        raise Stage2ValidationError("Monaco restaurant output must use synthetic code 98")
    if not working["region"].eq(MONACO_REGION).all():
        raise Stage2ValidationError("Monaco restaurant output has an unexpected application region")
    if not working["longitude"].between(-180, 180).all() or not working["latitude"].between(-90, 90).all():
        raise Stage2ValidationError("Monaco restaurant output contains invalid coordinates")
    if working.duplicated().any():
        raise Stage2ValidationError("Monaco restaurant output contains exact duplicates")
    return working


def _validate_monaco_geometry(geometry: gpd.GeoDataFrame) -> None:
    require_columns(geometry, ("geometry",), "Monaco geometry")
    if len(geometry) != 1:
        raise Stage2ValidationError(f"Monaco geometry must contain one feature, found {len(geometry)}")
    if geometry.crs is None or geometry.crs.to_epsg() != 4326:
        raise Stage2ValidationError(f"Monaco geometry must use EPSG:4326, found {geometry.crs}")
    if geometry.geometry.isna().any() or geometry.geometry.is_empty.any():
        raise Stage2ValidationError("Monaco geometry is null or empty")


def aggregate_monaco(
    restaurants: pd.DataFrame,
    geometry: gpd.GeoDataFrame,
    *,
    year: int,
) -> gpd.GeoDataFrame:
    _validate_monaco_geometry(geometry)
    working = restaurants.copy()
    allowed_stars = {category[0] for category in star_categories(year)}
    unexpected_stars = sorted(set(working["stars"]) - allowed_stars)
    if unexpected_stars:
        raise Stage2ValidationError(
            f"Monaco restaurants contain unexpected stars values: {unexpected_stars}"
        )
    if year >= 2025 and not working["greenstar"].isin((0, 1)).all():
        raise Stage2ValidationError("Monaco restaurants contain unexpected Green Star values")
    count_columns: list[str] = []
    for star_value, _label, column in star_categories(year):
        working[column] = working["stars"].eq(star_value).astype(int)
        count_columns.append(column)
    if year >= 2025:
        working["green_stars"] = working["greenstar"].eq(1).astype(int)
        count_columns.append("green_stars")

    grouped = working.groupby(["department_num", "department"], sort=True)[count_columns].sum().reset_index()
    if len(grouped) != 1:
        raise Stage2ValidationError("Monaco aggregation must produce exactly one administrative row")
    if int(grouped.loc[0, [category[2] for category in star_categories(year)]].sum()) != len(working):
        raise Stage2ValidationError("Monaco Michelin category counts do not reconcile to restaurant rows")
    grouped["capital"] = "Monaco"
    grouped["region"] = MONACO_REGION
    grouped["total_stars"] = grouped["1_star"] + 2 * grouped["2_star"] + 3 * grouped["3_star"]
    grouped["starred_restaurants"] = grouped["1_star"] + grouped["2_star"] + grouped["3_star"]
    for column in STATS_COLUMNS:
        grouped[column] = 0.0

    coordinate_groups = (
        working.groupby("stars", sort=False)[["latitude", "longitude"]]
        .apply(lambda frame: list(zip(frame["latitude"], frame["longitude"])))
        .to_dict()
    )
    grouped["locations"] = str({
        label: coordinate_groups.get(star_value)
        for star_value, label, _column in star_categories(year)
    })
    grouped.rename(columns={"department_num": "code"}, inplace=True)
    grouped["geometry"] = geometry.geometry.iloc[0]
    product = gpd.GeoDataFrame(
        grouped.loc[:, [*departmental_property_columns(year), "geometry"]],
        geometry="geometry",
        crs=geometry.crs,
    )
    properties = [column for column in product.columns if column != "geometry"]
    if product[properties].isna().any().any():
        raise Stage2ValidationError("Monaco aggregate contains property nulls")
    if product.loc[0, "code"] != MONACO_CODE:
        raise Stage2ValidationError("Monaco aggregate must use synthetic code 98")
    return product


def validate_monaco_stage2(
    *,
    year: int,
    partition_root: Path = Path("data/partitions"),
    geometry_path: Path = Path("data/raw/geodata/monaco.geojson"),
) -> MonacoResult:
    if year < 2025:
        raise Stage2ValidationError(
            "Monaco Stage 2 is supported from 2025; earlier years have no application-product fidelity baselines"
        )
    partition_path = partition_root / "monaco" / f"monaco_{year}.csv"
    missing = [path for path in (partition_path, geometry_path) if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing Monaco Stage 2 inputs: " + ", ".join(map(str, missing)))
    partition = pd.read_csv(partition_path)
    geometry = gpd.read_file(geometry_path)
    restaurants = prepare_monaco_restaurants(partition, year=year)
    aggregate = aggregate_monaco(restaurants, geometry, year=year)
    return MonacoResult(
        year=year,
        restaurants=restaurants,
        aggregate=aggregate,
        validation=MonacoValidation(len(restaurants), len(aggregate)),
        paths={},
    )


def _write_monaco_products(result: MonacoResult, root: Path) -> dict[str, Path]:
    paths = monaco_product_paths(result.year, root)
    paths["restaurants"].parent.mkdir(parents=True, exist_ok=True)
    paths["aggregate"].parent.mkdir(parents=True, exist_ok=True)
    result.restaurants.to_csv(paths["restaurants"], index=False, lineterminator="\n")
    result.aggregate.to_file(paths["aggregate"], driver="GeoJSON")
    try:
        assert_frame_equal(
            result.restaurants.reset_index(drop=True),
            pd.read_csv(paths["restaurants"], dtype={"department_num": "string"}),
            check_dtype=False,
        )
        assert_geodataframe_equal(
            result.aggregate.reset_index(drop=True),
            gpd.read_file(paths["aggregate"]),
            check_dtype=False,
            check_less_precise=True,
        )
    except AssertionError as error:
        raise Stage2PublicationError("Serialized Monaco products failed reload validation") from error
    return paths


def run_monaco_stage2(
    *,
    year: int,
    partition_root: Path = Path("data/partitions"),
    geometry_path: Path = Path("data/raw/geodata/monaco.geojson"),
    output_root: Path = Path("data/products"),
    replace: bool = False,
) -> MonacoResult:
    prepared = validate_monaco_stage2(
        year=year, partition_root=partition_root, geometry_path=geometry_path
    )
    final = monaco_product_paths(year, output_root)
    existing = {name: path for name, path in final.items() if path.exists()}
    if existing and not replace:
        raise FileExistsError(
            "Refusing to replace existing Monaco products without --replace: "
            + ", ".join(map(str, existing.values()))
        )
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".stage2-monaco-{year}-", dir=output_root))
    backups = staging / "backups"
    published: list[str] = []
    try:
        staged = _write_monaco_products(prepared, staging / "candidate")
        for name, path in existing.items():
            backup = backups / name / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        for name in ("restaurants", "aggregate"):
            final[name].parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged[name], final[name])
            published.append(name)
    except Exception as error:
        for name in reversed(published):
            backup = backups / name / final[name].name
            if backup.exists():
                os.replace(backup, final[name])
            else:
                final[name].unlink(missing_ok=True)
        raise Stage2PublicationError(f"Monaco publication failed and was rolled back: {error}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return MonacoResult(year, prepared.restaurants, prepared.aggregate, prepared.validation, final)
