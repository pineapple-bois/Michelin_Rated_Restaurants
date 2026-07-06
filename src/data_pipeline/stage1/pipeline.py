"""Stage 1 transformation from a local raw snapshot to country partitions."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile

import pandas as pd
from pandas.testing import assert_frame_equal

from .schema import LOCATION_SPECIAL_CASES, Stage1Spec, spec_for_year
from .validation import (
    PartitionValidation,
    validate_cleaned,
    validate_partitions,
    validate_source_columns,
)


@dataclass(frozen=True)
class Stage1Result:
    year: int
    source_rows: int
    partitions: dict[str, pd.DataFrame]
    validation: dict[str, PartitionValidation]
    paths: dict[str, Path]


class Stage1PublicationError(RuntimeError):
    """Raised when a complete validated partition set cannot be published."""


def parse_locations(locations: pd.Series) -> pd.DataFrame:
    """Parse Michelin location strings into city, optional state, and country."""

    normalized = locations.replace(LOCATION_SPECIAL_CASES).astype("string").str.strip()
    parts = normalized.str.rsplit(",", n=2, expand=True)
    for column in range(parts.shape[1], 3):
        parts[column] = pd.NA

    two_part_rows = parts[2].isna() & parts[1].notna()
    parts.loc[two_part_rows, 2] = parts.loc[two_part_rows, 1]
    parts.loc[two_part_rows, 1] = pd.NA
    parts.columns = ["city", "state", "country"]

    for column in parts.columns:
        parts[column] = parts[column].astype("string").str.strip()
    return parts


def clean_snapshot(raw: pd.DataFrame, spec: Stage1Spec) -> pd.DataFrame:
    """Normalize one raw snapshot without filtering or changing row order."""

    validate_source_columns(raw, spec)
    cleaned = raw.loc[:, spec.source_columns].copy()
    cleaned.columns = cleaned.columns.str.lower()
    cleaned.rename(columns=spec.rename_columns, inplace=True)

    parsed = parse_locations(cleaned.pop("location"))
    cleaned = cleaned.join(parsed)
    cleaned.drop(columns=["state", "currency"], errors="ignore", inplace=True)

    cleaned["stars"] = cleaned["award"].map(spec.award_values).astype(float)
    cleaned = cleaned.loc[:, spec.output_columns]
    validate_cleaned(cleaned, source_rows=len(raw), spec=spec)
    return cleaned


def prepare_partitions(
    raw: pd.DataFrame,
    *,
    year: int,
) -> tuple[dict[str, pd.DataFrame], dict[str, PartitionValidation]]:
    """Create and validate annual France, Monaco, and UK partitions in memory."""

    spec = spec_for_year(year)
    cleaned = clean_snapshot(raw, spec)

    france_mask = cleaned["country"].eq("France") & ~cleaned["city"].eq("Monaco")
    if spec.monaco_country == "France":
        monaco_mask = cleaned["country"].eq("France") & cleaned["city"].eq("Monaco")
    else:
        monaco_mask = cleaned["country"].eq(spec.monaco_country)
    uk_mask = cleaned["country"].eq("United Kingdom")

    expected_indexes = {
        "france": cleaned.index[france_mask],
        "monaco": cleaned.index[monaco_mask],
        "uk": cleaned.index[uk_mask],
    }
    partitions = {
        country: cleaned.loc[index].copy()
        for country, index in expected_indexes.items()
    }
    validation = validate_partitions(
        partitions,
        cleaned=cleaned,
        expected_indexes=expected_indexes,
        spec=spec,
    )
    return partitions, validation


def partition_paths(year: int, output_root: Path) -> dict[str, Path]:
    return {
        country: output_root / country / f"{country}_{year}.csv"
        for country in ("france", "monaco", "uk")
    }


def _write_staged_partitions(
    partitions: dict[str, pd.DataFrame],
    *,
    year: int,
    staging_root: Path,
) -> dict[str, Path]:
    """Serialize and reload every output before any canonical path is changed."""

    paths = partition_paths(year, staging_root)
    for country, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="") as output:
            partitions[country].to_csv(output, index=False, lineterminator="\n")

        reloaded = pd.read_csv(path)
        try:
            assert_frame_equal(
                partitions[country].reset_index(drop=True),
                reloaded,
                check_dtype=False,
                check_like=False,
            )
        except AssertionError as error:
            raise Stage1PublicationError(
                f"Serialized {country} output failed reload validation"
            ) from error
    return paths


def publish_partitions(
    partitions: dict[str, pd.DataFrame],
    *,
    year: int,
    output_root: Path,
    replace: bool = False,
) -> dict[str, Path]:
    """Publish all three files transactionally, with rollback on failure.

    Each file replacement is atomic. Existing files are copied into the private
    staging directory before publication so an exception during the three-file
    transaction can restore the complete previous set.
    """

    final_paths = partition_paths(year, output_root)
    existing = {country: path for country, path in final_paths.items() if path.exists()}
    if existing and not replace:
        raise FileExistsError(
            "Refusing to replace existing partitions without --replace: "
            + ", ".join(str(path) for path in existing.values())
        )

    output_root.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".stage1-{year}-", dir=output_root)
    )
    backup_root = staging_root / "backups"
    published: list[str] = []
    cleanup_staging = True

    try:
        staged_paths = _write_staged_partitions(
            partitions,
            year=year,
            staging_root=staging_root / "candidate",
        )

        for country, path in existing.items():
            backup = backup_root / country / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)

        for country in ("france", "monaco", "uk"):
            final = final_paths[country]
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_paths[country], final)
            published.append(country)
    except Exception as error:
        rollback_errors: list[str] = []
        for country in reversed(published):
            final = final_paths[country]
            backup = backup_root / country / final.name
            try:
                if backup.exists():
                    os.replace(backup, final)
                else:
                    final.unlink(missing_ok=True)
            except Exception as rollback_error:  # pragma: no cover - catastrophic filesystem failure
                rollback_errors.append(f"{country}: {rollback_error}")

        detail = f"Stage 1 publication failed and was rolled back: {error}"
        if rollback_errors:
            cleanup_staging = False
            detail += (
                "; rollback errors: "
                + ", ".join(rollback_errors)
                + f"; recovery files retained at {staging_root}"
            )
        raise Stage1PublicationError(detail) from error
    finally:
        if cleanup_staging:
            shutil.rmtree(staging_root, ignore_errors=True)

    return final_paths


def validate_stage1(
    *,
    year: int,
    raw_root: Path = Path("data/raw/michelin"),
) -> Stage1Result:
    """Validate an accepted local raw snapshot without publishing files."""

    raw_path = raw_root / f"michelin_data_{year}.csv"
    if not raw_path.is_file():
        raise FileNotFoundError(f"Raw Michelin snapshot not found: {raw_path}")

    raw = pd.read_csv(raw_path)
    partitions, validation = prepare_partitions(raw, year=year)
    return Stage1Result(
        year=year,
        source_rows=len(raw),
        partitions=partitions,
        validation=validation,
        paths={},
    )


def run_stage1(
    *,
    year: int,
    raw_root: Path = Path("data/raw/michelin"),
    output_root: Path = Path("data/partitions"),
    replace: bool = False,
) -> Stage1Result:
    """Load, transform, validate, and write one local accepted raw snapshot."""

    prepared = validate_stage1(year=year, raw_root=raw_root)
    written_paths = publish_partitions(
        prepared.partitions,
        year=year,
        output_root=output_root,
        replace=replace,
    )
    return Stage1Result(
        year=year,
        source_rows=prepared.source_rows,
        partitions=prepared.partitions,
        validation=prepared.validation,
        paths=written_paths,
    )
