"""Fail-closed validation for Stage 1 transformations."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .schema import Stage1Spec


class Stage1ValidationError(ValueError):
    """Raised when a Stage 1 contract is not satisfied."""


@dataclass(frozen=True)
class PartitionValidation:
    rows: int
    duplicates: int
    missing_coordinate_pairs: int
    warnings: tuple[str, ...] = ()


def validate_source_columns(raw: pd.DataFrame, spec: Stage1Spec) -> None:
    missing = [column for column in spec.source_columns if column not in raw.columns]
    if missing:
        raise Stage1ValidationError(
            f"{spec.year} raw snapshot is missing required fields: {missing}"
        )


def validate_cleaned(
    cleaned: pd.DataFrame,
    *,
    source_rows: int,
    spec: Stage1Spec,
) -> None:
    if len(cleaned) != source_rows:
        raise Stage1ValidationError(
            f"Preparation lost rows: expected {source_rows}, found {len(cleaned)}"
        )

    missing_geo = cleaned[cleaned[["city", "country"]].isna().any(axis=1)]
    blank_geo = cleaned[
        cleaned["city"].fillna("").str.strip().eq("")
        | cleaned["country"].fillna("").str.strip().eq("")
    ]
    failed_indexes = missing_geo.index.union(blank_geo.index)
    if len(failed_indexes):
        examples = cleaned.loc[failed_indexes, ["name", "city", "country"]].head(5)
        raise Stage1ValidationError(
            "Location parsing failed for "
            f"{len(failed_indexes)} rows; examples: {examples.to_dict('records')}"
        )

    unexpected_awards = sorted(set(cleaned["award"].dropna()) - set(spec.award_values))
    if cleaned["award"].isna().any() or unexpected_awards:
        raise Stage1ValidationError(
            "Unexpected award values: "
            f"{unexpected_awards}; null awards={int(cleaned['award'].isna().sum())}"
        )

    if tuple(cleaned.columns) != spec.output_columns:
        raise Stage1ValidationError(
            f"Output schema mismatch: expected {spec.output_columns}, "
            f"found {tuple(cleaned.columns)}"
        )


def _coordinate_summary(
    frame: pd.DataFrame,
    *,
    country: str,
    spec: Stage1Spec,
) -> tuple[int, tuple[str, ...]]:
    longitude = pd.to_numeric(frame["longitude"], errors="coerce")
    latitude = pd.to_numeric(frame["latitude"], errors="coerce")

    non_numeric = (
        (frame["longitude"].notna() & longitude.isna())
        | (frame["latitude"].notna() & latitude.isna())
    )
    if non_numeric.any():
        raise Stage1ValidationError(
            f"{country} contains {int(non_numeric.sum())} non-numeric coordinate rows"
        )

    partial = longitude.isna() ^ latitude.isna()
    if partial.any():
        raise Stage1ValidationError(
            f"{country} contains {int(partial.sum())} partial coordinate pairs"
        )

    missing = longitude.isna() & latitude.isna()
    missing_count = int(missing.sum())
    warnings: list[str] = []
    allowed_missing = dict(spec.allowed_missing_coordinate_pairs).get(country, 0)
    if missing_count != allowed_missing:
        raise Stage1ValidationError(
            f"{country} contains {missing_count} missing coordinate pairs; "
            f"expected {allowed_missing} for {spec.year}"
        )
    if missing_count:
        warnings.append(
            f"{spec.year} historical baseline permits {missing_count} paired missing coordinates"
        )

    present = ~missing
    out_of_range = (
        ~longitude[present].between(-180, 180)
        | ~latitude[present].between(-90, 90)
    )
    if out_of_range.any():
        raise Stage1ValidationError(
            f"{country} contains {int(out_of_range.sum())} out-of-range coordinate rows"
        )
    return missing_count, tuple(warnings)


def validate_partitions(
    partitions: dict[str, pd.DataFrame],
    *,
    cleaned: pd.DataFrame,
    expected_indexes: dict[str, pd.Index],
    spec: Stage1Spec,
) -> dict[str, PartitionValidation]:
    expected_countries = {"france", "monaco", "uk"}
    if set(partitions) != expected_countries:
        raise Stage1ValidationError(
            f"Partition set mismatch: expected {expected_countries}, found {set(partitions)}"
        )

    if spec.monaco_country == "Principality of Monaco":
        legacy_monaco = cleaned[
            cleaned["country"].eq("France") & cleaned["city"].eq("Monaco")
        ]
        if not legacy_monaco.empty:
            raise Stage1ValidationError(
                "Modern source contains Monaco rows classified as France"
            )

    memberships = {
        "france": {"France"},
        "monaco": {spec.monaco_country},
        "uk": {"United Kingdom"},
    }
    validation: dict[str, PartitionValidation] = {}

    for country, frame in partitions.items():
        if not frame.index.equals(expected_indexes[country]):
            raise Stage1ValidationError(
                f"{country} partition has missing, extra, or reordered rows"
            )
        if tuple(frame.columns) != spec.output_columns:
            raise Stage1ValidationError(f"{country} partition schema changed")

        actual_membership = set(frame["country"].dropna().unique())
        if not actual_membership.issubset(memberships[country]):
            raise Stage1ValidationError(
                f"Invalid {country} country membership: {sorted(actual_membership)}"
            )

        if country == "france" and frame["city"].eq("Monaco").any():
            raise Stage1ValidationError("Monaco appears in the France partition")

        duplicates = int(frame.duplicated().sum())
        if duplicates:
            raise Stage1ValidationError(
                f"{country} partition contains {duplicates} exact duplicate rows"
            )

        missing_coordinates, warnings = _coordinate_summary(
            frame,
            country=country,
            spec=spec,
        )
        validation[country] = PartitionValidation(
            rows=len(frame),
            duplicates=duplicates,
            missing_coordinate_pairs=missing_coordinates,
            warnings=warnings,
        )

    selected = expected_indexes["france"].append(
        [expected_indexes["monaco"], expected_indexes["uk"]]
    )
    if selected.duplicated().any():
        raise Stage1ValidationError("A source row appears in multiple partitions")

    return validation
