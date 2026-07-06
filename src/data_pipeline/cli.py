"""Command-line interface for local Michelin transformations."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .stage1.fidelity import compare_partition_roots
from .stage1.pipeline import Stage1PublicationError, run_stage1, validate_stage1
from .stage1.validation import Stage1ValidationError
from .stage2.pipeline import Stage2PublicationError, run_stage2, validate_stage2
from .stage2.monaco import run_monaco_stage2, validate_monaco_stage2
from .stage2.validation import Stage2ValidationError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m data_pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    partition = subparsers.add_parser(
        "partition",
        help="create validated annual country-partition candidates",
    )
    partition.add_argument("--year", required=True, type=int)
    partition.add_argument(
        "--raw-root",
        type=Path,
        default=Path("data/raw/michelin"),
        help="directory containing immutable Michelin raw snapshots",
    )
    partition.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/partitions"),
        help="publication root (default: data/partitions)",
    )
    partition.add_argument(
        "--compare-root",
        type=Path,
        help="optional read-only partition baseline root",
    )
    partition.add_argument(
        "--validate-only",
        action="store_true",
        help="validate source and outputs in memory without publishing files",
    )
    partition.add_argument(
        "--replace",
        action="store_true",
        help="deliberately replace existing outputs after full validation",
    )

    departments = subparsers.add_parser(
        "departments",
        help="create France departmental and regional application products",
    )
    departments.add_argument("--year", required=True, type=int)
    departments.add_argument(
        "--partition-root",
        type=Path,
        default=Path("data/partitions"),
        help="Stage 1 partition root (default: data/partitions)",
    )
    departments.add_argument(
        "--departments-path",
        type=Path,
        default=Path("data/raw/demographics/departments.csv"),
    )
    departments.add_argument(
        "--statistics-path",
        type=Path,
        default=Path("data/raw/demographics/departmental_stats_2023.csv"),
    )
    departments.add_argument(
        "--geometry-path",
        type=Path,
        default=Path("data/raw/geodata/departments.geojson"),
    )
    departments.add_argument(
        "--region-geometry-path",
        type=Path,
        default=Path("data/raw/geodata/regions.geojson"),
    )
    departments.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/products"),
        help="publication root (default: data/products)",
    )
    departments.add_argument(
        "--validate-only",
        action="store_true",
        help="validate transformations and products without publishing files",
    )
    departments.add_argument(
        "--replace",
        action="store_true",
        help="deliberately replace existing products after full validation",
    )

    monaco = subparsers.add_parser(
        "monaco",
        help="create Monaco restaurant and aggregate application products",
    )
    monaco.add_argument("--year", required=True, type=int)
    monaco.add_argument(
        "--partition-root", type=Path, default=Path("data/partitions")
    )
    monaco.add_argument(
        "--geometry-path",
        type=Path,
        default=Path("data/raw/geodata/monaco.geojson"),
    )
    monaco.add_argument(
        "--output-root", type=Path, default=Path("data/products")
    )
    monaco.add_argument("--validate-only", action="store_true")
    monaco.add_argument("--replace", action="store_true")
    return parser


def _run_partition(args: argparse.Namespace) -> int:
    if args.validate_only and args.replace:
        print("Stage 1 failed: --replace cannot be used with --validate-only", file=sys.stderr)
        return 2
    if args.validate_only and args.compare_root is not None:
        print("Stage 1 failed: --compare-root requires published candidate files", file=sys.stderr)
        return 2

    try:
        if args.validate_only:
            result = validate_stage1(year=args.year, raw_root=args.raw_root)
        else:
            result = run_stage1(
                year=args.year,
                raw_root=args.raw_root,
                output_root=args.output_root,
                replace=args.replace,
            )
    except (
        Stage1PublicationError,
        Stage1ValidationError,
        FileExistsError,
        FileNotFoundError,
    ) as error:
        print(f"Stage 1 failed: {error}", file=sys.stderr)
        return 2

    print(f"Stage 1 validated {result.source_rows} source rows for {result.year}.")
    for country, summary in result.validation.items():
        print(
            f"  {country}: {summary.rows} rows, "
            f"{summary.duplicates} exact duplicates, "
            f"{summary.missing_coordinate_pairs} missing coordinate pairs"
        )
        for warning in summary.warnings:
            print(f"    warning: {warning}")
        if country in result.paths:
            print(f"    wrote: {result.paths[country]}")

    if args.validate_only:
        print("Validation complete; no files were published.")
        return 0

    if args.compare_root is None:
        return 0

    comparisons = compare_partition_roots(
        year=args.year,
        candidate_root=args.output_root,
        baseline_root=args.compare_root,
    )
    matched = True
    for country, comparison in comparisons.items():
        status = "PASS" if comparison.matches else "FAIL"
        print(f"  fidelity {country}: {status} ({comparison.summary})")
        matched = matched and comparison.matches
    return 0 if matched else 1


def _run_departments(args: argparse.Namespace) -> int:
    if args.validate_only and args.replace:
        print("Stage 2 failed: --replace cannot be used with --validate-only", file=sys.stderr)
        return 2

    options = {
        "year": args.year,
        "partition_root": args.partition_root,
        "departments_path": args.departments_path,
        "statistics_path": args.statistics_path,
        "geometry_path": args.geometry_path,
        "region_geometry_path": args.region_geometry_path,
    }
    try:
        if args.validate_only:
            result = validate_stage2(**options)
        else:
            result = run_stage2(
                **options,
                output_root=args.output_root,
                replace=args.replace,
            )
    except (
        Stage2PublicationError,
        Stage2ValidationError,
        FileExistsError,
        FileNotFoundError,
    ) as error:
        print(f"Stage 2 failed: {error}", file=sys.stderr)
        return 2

    print(
        f"Stage 2 validated {result.validation.restaurant_rows} restaurants "
        f"{result.validation.department_rows} departments and "
        f"{result.validation.region_rows} regions for {result.year}."
    )
    if args.validate_only:
        print("Validation complete; no files were published.")
        return 0
    for path in result.paths.values():
        print(f"  wrote: {path}")
    return 0


def _run_monaco(args: argparse.Namespace) -> int:
    if args.validate_only and args.replace:
        print("Monaco Stage 2 failed: --replace cannot be used with --validate-only", file=sys.stderr)
        return 2
    options = {
        "year": args.year,
        "partition_root": args.partition_root,
        "geometry_path": args.geometry_path,
    }
    try:
        if args.validate_only:
            result = validate_monaco_stage2(**options)
        else:
            result = run_monaco_stage2(
                **options, output_root=args.output_root, replace=args.replace
            )
    except (
        Stage2PublicationError,
        Stage2ValidationError,
        FileExistsError,
        FileNotFoundError,
    ) as error:
        print(f"Monaco Stage 2 failed: {error}", file=sys.stderr)
        return 2
    print(
        f"Monaco Stage 2 validated {result.validation.restaurant_rows} restaurants "
        f"and {result.validation.aggregate_rows} aggregate row for {result.year}."
    )
    if args.validate_only:
        print("Validation complete; no files were published.")
    else:
        for path in result.paths.values():
            print(f"  wrote: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "partition":
        return _run_partition(args)
    if args.command == "departments":
        return _run_departments(args)
    if args.command == "monaco":
        return _run_monaco(args)
    raise AssertionError(f"Unhandled command: {args.command}")
