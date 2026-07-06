"""Deterministic France arrondissement enrichment and Paris publication."""

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

from data_pipeline.stage2.schema import REGION_TRANSLATIONS, star_categories
from data_pipeline.stage2.validation import Stage2ValidationError, require_columns


COASTAL_FALLBACK_MAX_METRES = 500.0
NUMERIC_STATS = (
    "municipal_population",
    "population_density(inhabitants/sq_km)",
    "poverty_rate(%)",
    "average_net_hourly_wage(€)",
)


class Stage3PublicationError(RuntimeError):
    """Raised when the validated three-product transaction cannot complete."""


@dataclass(frozen=True)
class Stage3Validation:
    restaurant_rows: int
    arrondissement_rows: int
    paris_rows: int
    coastal_fallbacks: tuple[str, ...]
    unmatched_restaurants: tuple[str, ...] = ()
    unmatched_demographics: tuple[str, ...] = ()
    unmatched_geometries: tuple[str, ...] = ()


@dataclass(frozen=True)
class Stage3Result:
    year: int
    restaurants: pd.DataFrame
    arrondissements: gpd.GeoDataFrame
    paris: gpd.GeoDataFrame
    validation: Stage3Validation
    paths: dict[str, Path]


def stage3_paths(year: int, output_root: Path) -> dict[str, Path]:
    root = output_root / "france" / str(year)
    return {
        "restaurants": root / "all_restaurants(arrondissements).csv",
        "arrondissements": root / "geodata" / "arrondissement_restaurants.geojson",
        "paris": root / "geodata" / "paris_restaurants.geojson",
    }


def _clean_name(value: str) -> str:
    return re.sub(r"^(?:Le |La |Les |L')", "", value).strip()


def load_arrondissement_demographics(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, sep=";", header=None, dtype=str)
    if len(raw) < 4:
        raise Stage2ValidationError("Arrondissement demographics lacks metadata/header rows")
    raw.columns = raw.iloc[2]
    frame = raw.iloc[3:].reset_index(drop=True).rename(columns={
        "Code": "code",
        "Libellé": "arrondissement",
        "Taux de pauvreté 2021": "poverty_rate(%)",
        "Salaire net horaire moyen 2022": "average_net_hourly_wage(€)",
        "Population municipale 2022": "municipal_population",
        "Densité de population (historique depuis 1876) 2021": "population_density(inhabitants/sq_km)",
    })
    columns = ("code", "arrondissement", *NUMERIC_STATS)
    require_columns(frame, columns, "arrondissement demographics")
    frame = frame.loc[:, columns]
    frame = frame[~frame["code"].str.startswith("97")].sort_values("code").reset_index(drop=True)
    if len(frame) != 320 or frame["code"].duplicated().any() or frame["code"].isna().any():
        raise Stage2ValidationError("Expected 320 unique mainland arrondissement demographic codes")
    for column in NUMERIC_STATS:
        frame[column] = pd.to_numeric(
            frame[column].str.replace(r"[^\d,.\-]", "", regex=True).str.replace(",", ".", regex=False),
            errors="raise",
        )
    return frame


def load_arrondissement_geometry(path: Path) -> gpd.GeoDataFrame:
    geometry = gpd.read_file(path)
    require_columns(geometry, ("code", "nom", "geometry"), "arrondissement geometry")
    geometry = geometry[~geometry["code"].str.startswith("97")].sort_values("code").reset_index(drop=True)
    if len(geometry) != 320 or geometry["code"].duplicated().any():
        raise Stage2ValidationError("Expected 320 unique mainland arrondissement geometries")
    if geometry.crs is None or geometry.crs.to_epsg() != 4326:
        raise Stage2ValidationError("Arrondissement geometry must use EPSG:4326")
    if geometry.geometry.isna().any() or geometry.geometry.is_empty.any() or (~geometry.geometry.is_valid).any():
        raise Stage2ValidationError("Arrondissement geometry contains null, empty, or invalid features")
    return geometry


def reconcile_arrondissement_references(
    demographics: pd.DataFrame, geometry: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    stats = demographics.rename(columns={"code": "demographic_code"}).copy()
    stats["join_name"] = stats["arrondissement"].map(_clean_name)
    shapes = geometry.copy()
    shapes["join_name"] = shapes["nom"].replace({"Briey": "Val-de-Briey"}).map(_clean_name)
    if stats["join_name"].duplicated().any() or shapes["join_name"].duplicated().any():
        raise Stage2ValidationError("Normalized arrondissement names are not unique")
    if set(stats["join_name"]) != set(shapes["join_name"]):
        raise Stage2ValidationError("Arrondissement demographic and geometry names do not reconcile")
    product = shapes.merge(stats, on="join_name", how="left", validate="one_to_one")
    product["nom"] = product["arrondissement"]
    product.drop(columns=["join_name", "demographic_code"], inplace=True)
    return gpd.GeoDataFrame(product, geometry="geometry", crs=geometry.crs)


def assign_restaurants(
    restaurants: pd.DataFrame, geometry: gpd.GeoDataFrame
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    require_columns(restaurants, ("name", "department_num", "longitude", "latitude"), "Stage 2 restaurants")
    if restaurants[["longitude", "latitude"]].isna().any().any():
        raise Stage2ValidationError("Stage 2 restaurants contain missing coordinates")
    points = gpd.GeoDataFrame(
        restaurants.copy(),
        geometry=gpd.points_from_xy(restaurants["longitude"], restaurants["latitude"]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(points, geometry[["code", "nom", "geometry"]], predicate="within", how="left")
    if joined.index.duplicated().any():
        raise Stage2ValidationError("A restaurant matched multiple arrondissement geometries")
    fallback_names: list[str] = []
    unmatched = joined["code"].isna()
    if unmatched.any():
        projected_geometry = geometry.to_crs(2154)
        projected_points = points.to_crs(2154)
        for index in joined.index[unmatched]:
            department = str(joined.at[index, "department_num"])
            candidates = projected_geometry[projected_geometry["code"].str.startswith(department)]
            distances = candidates.geometry.distance(projected_points.at[index, "geometry"]).sort_values()
            if distances.empty or distances.iloc[0] > COASTAL_FALLBACK_MAX_METRES:
                continue
            if len(distances) > 1 and distances.iloc[1] <= COASTAL_FALLBACK_MAX_METRES:
                raise Stage2ValidationError(
                    f"Ambiguous coastal arrondissement assignment for {joined.at[index, 'name']!r}"
                )
            match = candidates.loc[distances.index[0]]
            joined.at[index, "code"] = match["code"]
            joined.at[index, "nom"] = match["nom"]
            fallback_names.append(str(joined.at[index, "name"]))
    still_unmatched = joined.loc[joined["code"].isna(), "name"].astype(str).tolist()
    if still_unmatched:
        raise Stage2ValidationError(f"Spatially unmatched restaurants: {still_unmatched}")
    result = pd.DataFrame(joined.drop(columns=["geometry", "index_right", "code"]))
    result.rename(columns={"nom": "arrondissement"}, inplace=True)
    return result, tuple(fallback_names)


def load_paris_reference(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = ("arrondissement_number", "ordinal", "name")
    require_columns(frame, required, "Paris arrondissement reference")
    frame = frame.loc[:, required].sort_values("arrondissement_number").reset_index(drop=True)
    if len(frame) != 20 or set(frame["arrondissement_number"]) != set(range(1, 21)) or frame["arrondissement_number"].duplicated().any():
        raise Stage2ValidationError("Paris reference must contain exactly arrondissements 1 through 20")
    frame["label"] = frame["ordinal"].astype(str) + " (" + frame["name"].astype(str) + ")"
    return frame


def enrich_paris_labels(restaurants: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    result = restaurants.copy()
    paris = result["department_num"].astype(str).eq("75")
    postcodes = result.loc[paris, "location"].astype(str).str.extract(r"Paris, (75\d{3})$", expand=False)
    if postcodes.isna().any():
        raise Stage2ValidationError("Paris restaurant location lacks a recognized postal code")
    numbers = postcodes.str[-2:].astype(int).replace(0, 20)
    # Both 75016 and the special delivery postcode 75116 identify the 16th.
    numbers = numbers.where(postcodes.ne("75116"), 16)
    labels = numbers.map(reference.set_index("arrondissement_number")["label"])
    if labels.isna().any():
        raise Stage2ValidationError("Paris postal codes did not map to all required labels")
    result.loc[paris, "arrondissement"] = labels
    result["region"] = result["region"].replace(REGION_TRANSLATIONS)
    return result


def _add_category_counts(frame: pd.DataFrame, year: int) -> tuple[pd.DataFrame, list[str]]:
    working = frame.copy()
    categories: list[str] = []
    allowed = {value for value, _label, _column in star_categories(year)}
    if set(working["stars"]) - allowed:
        raise Stage2ValidationError("Unexpected Michelin stars value in Stage 3 input")
    for value, _label, column in star_categories(year):
        working[column] = working["stars"].eq(value).astype(int)
        categories.append(column)
    if year >= 2025:
        working["green_stars"] = working["greenstar"].eq(1).astype(int)
    return working, categories


def build_arrondissement_product(
    assigned: pd.DataFrame,
    reference: gpd.GeoDataFrame,
    departments: pd.DataFrame,
    *,
    year: int,
) -> gpd.GeoDataFrame:
    working, categories = _add_category_counts(assigned, year)
    count_columns = [*categories, *( ["green_stars"] if year >= 2025 else [])]
    counts = working.groupby("arrondissement", sort=True)[count_columns].sum().reset_index()
    counts["total_stars"] = counts["1_star"] + 2 * counts["2_star"] + 3 * counts["3_star"]
    counts["starred_restaurants"] = counts["1_star"] + counts["2_star"] + counts["3_star"]
    if int(counts[categories].to_numpy().sum()) != len(working):
        raise Stage2ValidationError("National Michelin category counts do not reconcile to restaurant rows")

    base = reference.copy()
    base["department_num"] = base["code"].str[:2]
    department_reference = departments.loc[:, ["department_num", "department", "capital", "region"]].copy()
    department_reference["department_num"] = department_reference["department_num"].astype(str)
    base = base.merge(department_reference, on="department_num", how="left", validate="many_to_one")
    if base[["department", "capital", "region"]].isna().any().any():
        raise Stage2ValidationError("Arrondissement-to-department reference join was incomplete")
    base = base.merge(counts, on="arrondissement", how="left", validate="one_to_one")
    fill_columns = [*count_columns, "total_stars", "starred_restaurants"]
    base[fill_columns] = base[fill_columns].fillna(0).astype(int)

    coordinate_groups = (
        working.groupby(["arrondissement", "stars"], sort=False)[["latitude", "longitude"]]
        .apply(lambda value: list(zip(value["latitude"], value["longitude"]))).to_dict()
    )
    base["locations"] = base["arrondissement"].map(
        lambda name: str({value: coordinate_groups.get((name, value)) for value in (1, 2, 3)})
    )
    columns = ["code", "arrondissement", "department_num", "department", "capital", "region", *categories,
               "total_stars", "starred_restaurants"]
    if year >= 2025:
        columns.append("green_stars")
    columns.extend((*NUMERIC_STATS, "locations", "geometry"))
    product = gpd.GeoDataFrame(base.loc[:, columns], geometry="geometry", crs=reference.crs)
    if (
        len(product) != 320
        or product.drop(columns="geometry").isna().any().any()
        or product.geometry.isna().any()
        or product.geometry.is_empty.any()
        or (~product.geometry.is_valid).any()
    ):
        raise Stage2ValidationError("National arrondissement product is incomplete")
    return product


def build_paris_product(
    restaurants: pd.DataFrame,
    geometry: gpd.GeoDataFrame,
    reference: pd.DataFrame,
    *,
    year: int,
) -> gpd.GeoDataFrame:
    require_columns(geometry, ("c_ar", "c_arinsee", "geometry"), "Paris geometry")
    if len(geometry) != 20 or set(geometry["c_ar"].astype(int)) != set(range(1, 21)):
        raise Stage2ValidationError("Paris geometry must cover municipal arrondissements 1 through 20")
    if geometry.crs is None or geometry.crs.to_epsg() != 4326 or (~geometry.geometry.is_valid).any():
        raise Stage2ValidationError("Paris geometry must be valid EPSG:4326 geometry")
    paris = restaurants[restaurants["department_num"].astype(str).eq("75")].copy()
    working, categories = _add_category_counts(paris, year)
    count_columns = [*( ["green_stars"] if year >= 2025 else []), *categories]
    counts = working.groupby("arrondissement", sort=True)[count_columns].sum().reset_index()
    counts["total_stars"] = counts["1_star"] + 2 * counts["2_star"] + 3 * counts["3_star"]
    counts["starred_restaurants"] = counts["1_star"] + counts["2_star"] + counts["3_star"]
    if int(counts[categories].to_numpy().sum()) != len(working):
        raise Stage2ValidationError("Paris Michelin category counts do not reconcile to restaurant rows")

    labels = reference.set_index("arrondissement_number")["label"]
    base = geometry.copy()
    base["arrondissement_number"] = base["c_ar"].astype(int)
    base["arrondissement"] = base["arrondissement_number"].map(labels)
    base["code"] = base["arrondissement_number"].map(lambda number: f"750{number:02d}")
    base["department_num"] = "75"
    base["department"] = "Paris"
    base["capital"] = "Paris"
    base["region"] = "Île-de-France"
    base = base.merge(counts, on="arrondissement", how="left", validate="one_to_one")
    fill = [*count_columns, "total_stars", "starred_restaurants"]
    base[fill] = base[fill].fillna(0).astype(int)
    coordinate_groups = (
        working.groupby(["arrondissement", "stars"], sort=False)[["latitude", "longitude"]]
        .apply(lambda value: list(zip(value["latitude"], value["longitude"]))).to_dict()
    )
    base["locations"] = base["arrondissement"].map(lambda name: str({
        label: coordinate_groups.get((name, value))
        for value, label, _column in star_categories(year)
    }))
    columns = ["code", "arrondissement", "department_num", "department", "capital", "region"]
    if year >= 2025:
        columns.append("green_stars")
    columns.extend([*categories, "total_stars", "starred_restaurants", "locations", "geometry"])
    product = gpd.GeoDataFrame(
        base.sort_values("arrondissement_number").loc[:, columns].reset_index(drop=True),
        geometry="geometry", crs=geometry.crs,
    )
    if len(product) != 20 or product.drop(columns="geometry").isna().any().any():
        raise Stage2ValidationError("Paris product is incomplete")
    return product


def validate_stage3(
    *, year: int,
    stage2_root: Path = Path("data/products"),
    demographics_path: Path = Path("data/raw/demographics/arrondissement_stats_2023.csv"),
    paris_reference_path: Path = Path("data/raw/demographics/paris_arrondissements.csv"),
    arrondissement_geometry_path: Path = Path("data/raw/geodata/arrondissements-avec-outre-mer.geojson"),
    department_reference_path: Path = Path("data/raw/demographics/departments.csv"),
    department_geometry_path: Path = Path("data/raw/geodata/departments.geojson"),
    paris_geometry_path: Path = Path("data/raw/geodata/paris_arrondissements.geojson"),
) -> Stage3Result:
    if year < 2025:
        raise Stage2ValidationError("Stage 3 is supported from 2025 under the current Stage 2 schema")
    restaurant_path = stage2_root / "france" / str(year) / "all_restaurants.csv"
    inputs = [restaurant_path, demographics_path, paris_reference_path, arrondissement_geometry_path,
              department_reference_path, department_geometry_path, paris_geometry_path]
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing Stage 3 inputs: " + ", ".join(missing))
    source = pd.read_csv(restaurant_path, dtype={"department_num": "string"})
    demographics = load_arrondissement_demographics(demographics_path)
    geometry = load_arrondissement_geometry(arrondissement_geometry_path)
    reference = reconcile_arrondissement_references(demographics, geometry)
    assigned, fallbacks = assign_restaurants(source, reference)
    paris_reference = load_paris_reference(paris_reference_path)
    enriched = enrich_paris_labels(assigned, paris_reference)
    output_columns = ["name", "address", "location", "arrondissement", "department_num", "department",
                      "capital", "region", "price", "cuisine", "url", "award", "stars", "greenstar",
                      "longitude", "latitude"]
    enriched = enriched.loc[:, output_columns]
    departments = pd.read_csv(department_reference_path, dtype={"department_num": "string"})
    department_geometry = gpd.read_file(department_geometry_path)
    require_columns(department_geometry, ("code", "nom", "geometry"), "department geometry")
    department_geometry["code"] = department_geometry["code"].astype(str)
    department_names = departments[["department_num", "department"]].merge(
        department_geometry[["code", "nom"]],
        left_on="department_num", right_on="code", how="outer", validate="one_to_one",
    )
    if (
        department_names[["department_num", "code"]].isna().any().any()
        or not department_names["department"].eq(department_names["nom"]).all()
    ):
        raise Stage2ValidationError("Department reference and geometry names/codes disagree")
    national = build_arrondissement_product(assigned, reference, departments, year=year)
    paris_geometry = gpd.read_file(paris_geometry_path)
    paris = build_paris_product(enriched, paris_geometry, paris_reference, year=year)
    return Stage3Result(year, enriched, national, paris, Stage3Validation(
        len(enriched), len(national), len(paris), fallbacks
    ), {})


def _write_products(result: Stage3Result, root: Path) -> dict[str, Path]:
    paths = stage3_paths(result.year, root)
    paths["restaurants"].parent.mkdir(parents=True, exist_ok=True)
    paths["arrondissements"].parent.mkdir(parents=True, exist_ok=True)
    result.restaurants.to_csv(paths["restaurants"], index=False, lineterminator="\n")
    result.arrondissements.to_file(paths["arrondissements"], driver="GeoJSON")
    result.paris.to_file(paths["paris"], driver="GeoJSON")
    try:
        assert_frame_equal(result.restaurants, pd.read_csv(paths["restaurants"], dtype={"department_num": "string"}), check_dtype=False)
        assert_geodataframe_equal(result.arrondissements, gpd.read_file(paths["arrondissements"]), check_dtype=False, check_less_precise=True)
        assert_geodataframe_equal(result.paris, gpd.read_file(paths["paris"]), check_dtype=False, check_less_precise=True)
    except AssertionError as error:
        raise Stage3PublicationError("Serialized Stage 3 products failed reload validation") from error
    return paths


def run_stage3(*, year: int, output_root: Path = Path("data/products"), replace: bool = False, **inputs: object) -> Stage3Result:
    prepared = validate_stage3(year=year, **inputs)
    final = stage3_paths(year, output_root)
    existing = {name: path for name, path in final.items() if path.exists()}
    if existing and not replace:
        raise FileExistsError("Refusing to replace existing Stage 3 products without --replace: " + ", ".join(map(str, existing.values())))
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".stage3-{year}-", dir=output_root))
    backups = staging / "backups"
    published: list[str] = []
    try:
        staged = _write_products(prepared, staging / "candidate")
        for name, path in existing.items():
            backup = backups / name / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        for name in ("restaurants", "arrondissements", "paris"):
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
        raise Stage3PublicationError(f"Stage 3 publication failed and was rolled back: {error}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return Stage3Result(year, prepared.restaurants, prepared.arrondissements, prepared.paris, prepared.validation, final)
