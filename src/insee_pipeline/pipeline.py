"""Build the departmental INSEE/OECD demographics candidate tranche."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

import pandas as pd
from pandas.testing import assert_frame_equal
import requests

from .paths import PipelinePaths
from .sources import INSEE_DATASETS, acquire_sources
from .transform import (
    assemble_departmental_table,
    load_department_geometry,
    load_filosofi,
    load_oecd_gdp,
    load_population,
    load_unemployment,
    load_wages,
)
from .validate import Check, InseeValidationError, validate_final_table


@dataclass(frozen=True)
class BuildResult:
    year: int
    rows: int
    paths: dict[str, Path]
    validation: list[Check]
    legacy_comparison: dict[str, object]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")
    reloaded = pd.read_csv(path, dtype={"department_code": str})
    assert_frame_equal(frame.fillna(""), reloaded.fillna(""), check_dtype=False)


def _legacy_comparison(candidate: pd.DataFrame, legacy_path: Path) -> dict[str, object]:
    if not legacy_path.is_file():
        return {"legacy_path": str(legacy_path), "available": False}
    legacy = pd.read_csv(legacy_path, dtype={"department_num": str})
    legacy = legacy.rename(columns={"department_num": "department_code"})
    shared_codes = sorted(set(candidate["department_code"]) & set(legacy["department_code"]))
    return {
        "legacy_path": str(legacy_path),
        "available": True,
        "candidate_rows": len(candidate),
        "legacy_rows": len(legacy),
        "shared_department_codes": len(shared_codes),
        "candidate_columns": candidate.columns.tolist(),
        "legacy_columns": legacy.columns.tolist(),
        "obsolete_or_incompatible_legacy_columns": [
            column for column in legacy.columns
            if column in {
                "GDP_millions(€)",
                "GDP_per_capita(€)",
                "poverty_rate(%)",
                "average_annual_unemployment_rate(%)",
                "average_net_hourly_wage(€)",
                "population_density(inhabitants/sq_km)",
                "area(sq_km)",
            }
        ],
    }


def build(
    *,
    year: int,
    raw_root: Path = Path("tmp/insee_pipeline"),
    candidate_root: Path = Path("data/candidates/insee"),
    geometry_path: Path = Path("data/raw/geodata/departments.geojson"),
    source_cache_root: Path | None = None,
    legacy_statistics_path: Path = Path("data/raw/demographics/departmental_stats_2023.csv"),
    replace: bool = False,
) -> BuildResult:
    paths = PipelinePaths.create(
        year=year,
        raw_root=raw_root,
        candidate_root=candidate_root,
        geometry_path=geometry_path,
        source_cache_root=source_cache_root,
    )
    final_paths = {
        "departmental_table": paths.departmental_table,
        "crosswalk": paths.crosswalk,
        "source_inventory": paths.source_inventory,
        "manifest": paths.manifest,
        "validation_report": paths.validation_report,
    }
    existing = [path for path in final_paths.values() if path.exists()]
    if existing and not replace:
        raise FileExistsError("Refusing to replace existing candidate outputs without --replace: " + ", ".join(map(str, existing)))

    artifacts = acquire_sources(paths)

    geometry = load_department_geometry(paths.geometry_path, year=year)
    department_codes = set(geometry.frame["department_code"])
    wages = load_wages(paths.insee_zip(INSEE_DATASETS["wages"]), year=year, department_codes=department_codes)
    filosofi = load_filosofi(paths.insee_zip(INSEE_DATASETS["filosofi"]), year=year, department_codes=department_codes)
    unemployment = load_unemployment(paths.insee_zip(INSEE_DATASETS["unemployment"]), year=year, department_codes=department_codes)
    population = load_population(paths.insee_zip(INSEE_DATASETS["population"]), year=year, department_codes=department_codes)
    gdp = load_oecd_gdp(paths.oecd_gdp_csv, year=year, department_geometry=geometry.frame)

    candidate = assemble_departmental_table(
        year=year,
        geometry=geometry.frame,
        wages=wages.frame,
        filosofi=filosofi.frame,
        unemployment=unemployment.frame,
        population=population.frame,
        gdp=gdp.frame,
    )
    validation = [
        *geometry.checks,
        *wages.checks,
        *filosofi.checks,
        *unemployment.checks,
        *population.checks,
        *gdp.checks,
        *validate_final_table(candidate),
    ]
    legacy = _legacy_comparison(candidate, legacy_statistics_path)

    paths.candidate_root.mkdir(parents=True, exist_ok=True)
    crosswalk = gdp.frame[
        ["department_code", "department_name", "oecd_tl3_code", "oecd_reference_area_name"]
    ].copy()
    _write_csv(paths.departmental_table, candidate)
    _write_csv(paths.crosswalk, crosswalk)
    _write_json(
        paths.source_inventory,
        {
            "reference_year": year,
            "source_artifact_retention": {
                "policy": "Source ZIP/CSV files are disposable build-cache inputs, not durable repository data.",
                "cache_root": str(paths.raw_insee_root.parent),
                "safe_to_delete": True,
                "reproducibility_note": (
                    "Hashes, sizes, URLs, source identifiers, candidate outputs, "
                    "validation reports, and pipeline code are retained; byte-perfect "
                    "reconstruction requires the upstream source bytes to remain available "
                    "or be archived outside this repository."
                ),
            },
            "artifacts": [artifact.to_json() for artifact in artifacts],
            "sources": {
                "geometry": str(paths.geometry_path),
                "legacy_comparison": str(legacy_statistics_path),
            },
        },
    )
    _write_json(
        paths.validation_report,
        {
            "reference_year": year,
            "checks": [check.to_json() for check in validation],
            "legacy_comparison": legacy,
        },
    )
    _write_json(
        paths.manifest,
        {
            "reference_year": year,
            "rows": len(candidate),
            "schema": candidate.columns.tolist(),
            "source_artifact_retention_policy": (
                "Runtime source ZIP/CSV files are disposable cache files under the source "
                "working root. They are safe to delete and are not promoted with candidate outputs."
            ),
            "outputs": {name: str(path) for name, path in final_paths.items()},
            "source_inventory": str(paths.source_inventory),
            "validation_report": str(paths.validation_report),
            "legacy_comparison": legacy,
        },
    )
    return BuildResult(year, len(candidate), final_paths, validation, legacy)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m insee_pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="build a departmental demographic candidate tranche")
    build_parser.add_argument("--year", required=True, type=int)
    build_parser.add_argument(
        "--raw-root",
        type=Path,
        default=Path("tmp/insee_pipeline"),
        help="disposable source working/cache root (default: tmp/insee_pipeline)",
    )
    build_parser.add_argument("--candidate-root", type=Path, default=Path("data/candidates/insee"))
    build_parser.add_argument("--geometry-path", type=Path, default=Path("data/raw/geodata/departments.geojson"))
    build_parser.add_argument("--source-cache-root", type=Path)
    build_parser.add_argument("--legacy-statistics-path", type=Path, default=Path("data/raw/demographics/departmental_stats_2023.csv"))
    build_parser.add_argument("--replace", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            result = build(
                year=args.year,
                raw_root=args.raw_root,
                candidate_root=args.candidate_root,
                geometry_path=args.geometry_path,
                source_cache_root=args.source_cache_root,
                legacy_statistics_path=args.legacy_statistics_path,
                replace=args.replace,
            )
        else:
            raise AssertionError(args.command)
    except (FileExistsError, FileNotFoundError, InseeValidationError, ValueError, requests.RequestException) as error:
        print(f"INSEE pipeline failed: {error}", file=sys.stderr)
        return 2
    print(f"Built INSEE/OECD departmental candidate for {result.year}: {result.rows} rows")
    for name, path in result.paths.items():
        print(f"  {name}: {path}")
    print(f"  validation checks: {len(result.validation)}")
    if result.legacy_comparison.get("available"):
        print(f"  legacy shared department codes: {result.legacy_comparison['shared_department_codes']}")
    return 0
