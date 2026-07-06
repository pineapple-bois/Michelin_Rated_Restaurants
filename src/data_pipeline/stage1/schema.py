"""Known Stage 1 schemas and narrowly scoped historical compatibility rules."""

from __future__ import annotations

from dataclasses import dataclass


LEGACY_AWARDS = {
    "3 MICHELIN Stars": 3.0,
    "2 MICHELIN Stars": 2.0,
    "1 MICHELIN Star": 1.0,
    "Bib Gourmand": 0.5,
}

CURRENT_AWARDS = {
    "3 Stars": 3.0,
    "2 Stars": 2.0,
    "1 Star": 1.0,
    "Bib Gourmand": 0.5,
}

CURRENT_AWARDS_WITH_SELECTION = {
    **CURRENT_AWARDS,
    "Selected Restaurants": 0.25,
}

LOCATION_SPECIAL_CASES = {
    "Hong Kong": "Hong Kong, Hong Kong SAR China",
    "Macau": "Macau, Macau SAR China",
    "Singapore": "Singapore, Singapore",
    "Dubai": "Dubai, United Arab Emirates",
    "Luxembourg": "Luxembourg, Luxembourg",
    "Abu Dhabi": "Abu Dhabi, United Arab Emirates",
}


@dataclass(frozen=True)
class Stage1Spec:
    """The small set of known differences needed for historical fidelity."""

    year: int
    source_columns: tuple[str, ...]
    rename_columns: dict[str, str]
    output_columns: tuple[str, ...]
    award_values: dict[str, float]
    monaco_country: str
    allowed_missing_coordinate_pairs: tuple[tuple[str, int], ...] = ()


def spec_for_year(year: int) -> Stage1Spec:
    """Return the known schema for a guide year.

    Years after 2025 use the latest accepted Stage 1 shape. Any future source
    change still fails required-column or award validation rather than being
    guessed here.
    """

    common = ("Name", "Address", "Location")
    tail = ("Cuisine", "WebsiteUrl", "Award", "Longitude", "Latitude")

    if year == 2022:
        source_columns = (*common, "MaxPrice", "Currency", *tail)
        rename_columns = {"websiteurl": "url", "maxprice": "price"}
        output_columns = (
            "name", "address", "city", "country", "price", "cuisine",
            "url", "award", "stars", "longitude", "latitude",
        )
        return Stage1Spec(
            year=year,
            source_columns=source_columns,
            rename_columns=rename_columns,
            output_columns=output_columns,
            award_values=LEGACY_AWARDS,
            monaco_country="France",
        )

    if year == 2023:
        awards = LEGACY_AWARDS
        include_greenstar = False
        allowed_missing_coordinates: tuple[tuple[str, int], ...] = ()
    elif year == 2024:
        awards = CURRENT_AWARDS
        include_greenstar = False
        allowed_missing_coordinates = (("france", 52), ("uk", 12))
    elif year >= 2025:
        awards = CURRENT_AWARDS_WITH_SELECTION
        include_greenstar = True
        allowed_missing_coordinates = ()
    else:
        raise ValueError(f"Unsupported guide year: {year}")

    source_columns = (*common, "Price", *tail)
    if include_greenstar:
        source_columns = (
            "Name", "Address", "Location", "Price", "Cuisine", "WebsiteUrl",
            "Award", "GreenStar", "Longitude", "Latitude",
        )

    output = [
        "name", "address", "city", "country", "price", "cuisine", "url",
        "award",
    ]
    if include_greenstar:
        output.append("greenstar")
    output.extend(("stars", "longitude", "latitude"))

    return Stage1Spec(
        year=year,
        source_columns=source_columns,
        rename_columns={"websiteurl": "url"},
        output_columns=tuple(output),
        award_values=awards,
        monaco_country="Principality of Monaco" if year >= 2026 else "France",
        allowed_missing_coordinate_pairs=allowed_missing_coordinates,
    )
