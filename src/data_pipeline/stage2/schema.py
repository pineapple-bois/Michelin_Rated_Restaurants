"""Schemas and confirmed historical rules for France departmental products."""

from __future__ import annotations


REGION_TRANSLATIONS = {
    "Brittany": "Bretagne",
    "Corsica": "Corse",
    "Normandy": "Normandie",
}

LACAVE_ADDRESS_OVERRIDES = {
    (year, name, "Lacave, 46200, France"): ("Lacave, 46200", "Lacave", "46200")
    for year in (2023, 2024, 2025)
    for name in ("Château de la Treyne", "Le Pont de l'Ouysse")
}

BASE_STAR_CATEGORIES = (
    (0.5, "Bib", "bib_gourmand"),
    (1.0, "1", "1_star"),
    (2.0, "2", "2_star"),
    (3.0, "3", "3_star"),
)

CURRENT_STAR_CATEGORIES = (
    (0.25, "Selected", "selected"),
    *BASE_STAR_CATEGORIES,
)

STATS_COLUMNS = (
    "GDP_millions(€)",
    "GDP_per_capita(€)",
    "poverty_rate(%)",
    "average_annual_unemployment_rate(%)",
    "average_net_hourly_wage(€)",
    "municipal_population",
    "population_density(inhabitants/sq_km)",
    "area(sq_km)",
)


def star_categories(year: int) -> tuple[tuple[float, str, str], ...]:
    return CURRENT_STAR_CATEGORIES if year >= 2025 else BASE_STAR_CATEGORIES


def restaurant_input_columns(year: int) -> tuple[str, ...]:
    columns = [
        "name", "address", "city", "country", "price", "cuisine", "url",
        "award", "stars",
    ]
    if year >= 2025:
        columns.append("greenstar")
    columns.extend(("longitude", "latitude"))
    return tuple(columns)


def restaurant_output_columns(year: int) -> tuple[str, ...]:
    columns = [
        "name", "address", "location", "department_num", "department",
        "capital", "region", "price", "cuisine", "url", "award", "stars",
    ]
    if year >= 2025:
        columns.append("greenstar")
    columns.extend(("longitude", "latitude"))
    return tuple(columns)


def departmental_property_columns(year: int) -> tuple[str, ...]:
    columns = ["code", "department", "capital", "region"]
    columns.extend(category[2] for category in star_categories(year))
    columns.extend(("total_stars", "starred_restaurants"))
    if year >= 2025:
        columns.append("green_stars")
    columns.extend(STATS_COLUMNS)
    columns.append("locations")
    return tuple(columns)


def regional_property_columns(year: int) -> tuple[str, ...]:
    columns = ["region"]
    columns.extend(category[2] for category in star_categories(year))
    columns.extend(("total_stars", "starred_restaurants"))
    if year >= 2025:
        columns.append("green_stars")
    columns.extend(STATS_COLUMNS)
    columns.append("locations")
    return tuple(columns)
