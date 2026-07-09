"""Command-line interface for local Michelin transformations."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys

from .stage1.fidelity import compare_partition_roots
from .stage1.acquisition import run_stage1_acquisition
from .stage1.pipeline import Stage1PublicationError, run_stage1, validate_stage1
from .stage1.validation import Stage1ValidationError
from .stage2.pipeline import Stage2PublicationError, run_stage2, validate_stage2
from .stage2.monaco import run_monaco_stage2, validate_monaco_stage2
from .stage2.validation import Stage2ValidationError
from .stage3.acquisition import ParisReferenceError, extract_paris_reference
from .stage3.pipeline import Stage3PublicationError, run_stage3, validate_stage3
from .changes.pipeline import (
    ChangesPublicationError,
    ChangesValidationError,
    run_changes,
    validate_changes,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m data_pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    partition = subparsers.add_parser(
        "partition",
        help="create validated annual country-partition candidates",
    )
    partition.add_argument("--year", type=int)
    partition.add_argument(
        "--acquire-next",
        action="store_true",
        help="download, evaluate, and publish the next annual Stage 1 snapshot when accepted",
    )
    partition.add_argument(
        "--today",
        type=date.fromisoformat,
        help="override the local calendar date for acquisition gate testing",
    )
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
        "--insee-product-root",
        type=Path,
        default=Path("data/products/insee"),
        help="root containing versioned INSEE departmental products",
    )
    departments.add_argument(
        "--insee-year",
        type=int,
        help="explicit INSEE product year; defaults to the latest numeric year",
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

    arrondissements = subparsers.add_parser(
        "arrondissements",
        help="create national arrondissement and Paris municipal-arrondissement products",
    )
    arrondissements.add_argument("--year", required=True, type=int)
    arrondissements.add_argument("--stage2-root", type=Path, default=Path("data/products"))
    arrondissements.add_argument("--output-root", type=Path, default=Path("data/products"))
    arrondissements.add_argument(
        "--paris-reference-path", type=Path,
        default=Path("data/raw/demographics/paris_arrondissements.csv"),
    )
    arrondissements.add_argument(
        "--arrondissement-geometry-path", type=Path,
        default=Path("data/raw/geodata/arrondissements-avec-outre-mer.geojson"),
    )
    arrondissements.add_argument(
        "--department-reference-path", type=Path,
        default=Path("data/raw/demographics/departments.csv"),
    )
    arrondissements.add_argument(
        "--department-geometry-path", type=Path,
        default=Path("data/raw/geodata/departments.geojson"),
    )
    arrondissements.add_argument(
        "--paris-geometry-path", type=Path,
        default=Path("data/raw/geodata/paris_arrondissements.geojson"),
    )
    arrondissements.add_argument("--validate-only", action="store_true")
    arrondissements.add_argument("--replace", action="store_true")

    paris_reference = subparsers.add_parser(
        "acquire-paris-arrondissements",
        help="retrieve and validate the Wikipedia Paris arrondissement naming table",
    )
    paris_reference.add_argument(
        "--output-path", type=Path,
        default=Path("data/raw/demographics/paris_arrondissements.csv"),
    )
    paris_reference.add_argument("--refresh", action="store_true")
    paris_reference.add_argument("--timeout", type=float, default=30.0)

    changes = subparsers.add_parser(
        "changes", help="compare consecutive annual France Michelin Guide products"
    )
    changes.add_argument("--previous-year", required=True, type=int)
    changes.add_argument("--current-year", required=True, type=int)
    changes.add_argument("--product-root", type=Path, default=Path("data/products"))
    changes.add_argument("--output-root", type=Path, default=Path("data/reports"))
    changes.add_argument(
        "--overrides-path", type=Path,
        default=Path("data/overrides/france_change_matches.csv"),
    )
    changes.add_argument("--validate-only", action="store_true")
    changes.add_argument("--replace", action="store_true")
    return parser


def _run_partition(args: argparse.Namespace) -> int:
    if args.acquire_next:
        if args.year is not None:
            print("Stage 1 failed: --acquire-next derives the year; omit --year", file=sys.stderr)
            return 2
        if args.validate_only or args.replace or args.compare_root is not None:
            print(
                "Stage 1 failed: --acquire-next cannot be combined with "
                "--validate-only, --replace, or --compare-root",
                file=sys.stderr,
            )
            return 2
        try:
            result = run_stage1_acquisition(
                raw_root=args.raw_root,
                output_root=args.output_root,
                today=args.today,
            )
        except (
            Stage1PublicationError,
            Stage1ValidationError,
            FileExistsError,
            FileNotFoundError,
        ) as error:
            print(f"Stage 1 acquisition failed: {error}", file=sys.stderr)
            return 2

        print(result.message)
        print(f"Candidate year: {result.year}")
        print(f"Latest accepted France partition: {result.previous_year}")
        if result.source is not None:
            print(f"Source URL: {result.source.url}")
            if result.source.revision is not None:
                print(f"Source revision: {result.source.revision}")
        if result.comparison is not None:
            comparison = result.comparison
            print(f"Compared with accepted {result.previous_year} partition:")
            print(f"  Previous rows: {comparison.previous_rows}")
            print(f"  Candidate rows: {comparison.candidate_rows}")
            print("Schema:")
            print(f"  Added columns: {', '.join(comparison.added_columns) or 'none'}")
            print(f"  Removed columns: {', '.join(comparison.removed_columns) or 'none'}")
            print(f"  Shared columns: {len(comparison.shared_columns)}")
            print("Matching:")
            print(f"  Matched: {comparison.matched_restaurants}")
            print(f"  Match rate: {comparison.match_rate:.1%}")
            print(f"  Ambiguous: {comparison.ambiguous_matches}")
            print(f"  Duplicate-key conflicts: {comparison.duplicate_conflicts}")
            print(f"  Previous unmatched: {comparison.removed_restaurants}")
            print(f"  Candidate unmatched: {comparison.added_restaurants}")
            print("Guide changes:")
            print(f"  Added restaurants: {comparison.added_restaurants}")
            print(f"  Removed restaurants: {comparison.removed_restaurants}")
            print(f"  Award-label changes: {comparison.award_label_changes}")
            for transition, count in comparison.award_label_transition_counts.items():
                print(f"    {transition}: {count}")
            print(f"  Non-award changes: {comparison.other_changed_rows}")
            print(f"  Unchanged: {comparison.unchanged_restaurants}")
            if not comparison.comparison_reliable:
                print("Comparison quality:")
                print(f"  {comparison.reliability_reason}")
        if result.acceptance is not None:
            print("Reason:")
            print(f"  {result.acceptance.reason}")
        if result.status == "accepted":
            if result.raw_path is not None:
                print(f"  wrote: {result.raw_path}")
            for path in result.paths.values():
                print(f"  wrote: {path}")
        elif result.status in {"rejected", "comparison-unreliable"}:
            print("No files were published.")
            print("Temporary files were removed.")
        return 0

    if args.year is None:
        print("Stage 1 failed: --year is required unless --acquire-next is used", file=sys.stderr)
        return 2
    if args.today is not None:
        print("Stage 1 failed: --today can only be used with --acquire-next", file=sys.stderr)
        return 2
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
        "insee_product_root": args.insee_product_root,
        "insee_year": args.insee_year,
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


def _run_arrondissements(args: argparse.Namespace) -> int:
    if args.validate_only and args.replace:
        print("Stage 3 failed: --replace cannot be used with --validate-only", file=sys.stderr)
        return 2
    try:
        inputs = {
            "year": args.year,
            "stage2_root": args.stage2_root,
            "paris_reference_path": args.paris_reference_path,
            "arrondissement_geometry_path": args.arrondissement_geometry_path,
            "department_reference_path": args.department_reference_path,
            "department_geometry_path": args.department_geometry_path,
            "paris_geometry_path": args.paris_geometry_path,
        }
        if args.validate_only:
            result = validate_stage3(**inputs)
        else:
            result = run_stage3(
                **inputs, output_root=args.output_root, replace=args.replace,
            )
    except (Stage3PublicationError, Stage2ValidationError, FileExistsError, FileNotFoundError) as error:
        print(f"Stage 3 failed: {error}", file=sys.stderr)
        return 2
    print(
        f"Stage 3 validated {result.validation.restaurant_rows} restaurants, "
        f"{result.validation.arrondissement_rows} national arrondissements, and "
        f"{result.validation.paris_rows} Paris arrondissements for {result.year}."
    )
    print(f"  coastal fallbacks: {len(result.validation.coastal_fallbacks)}")
    if args.validate_only:
        print("Validation complete; no files were published.")
    else:
        for path in result.paths.values():
            print(f"  wrote: {path}")
    return 0


def _run_paris_reference(args: argparse.Namespace) -> int:
    try:
        path = extract_paris_reference(
            output_path=args.output_path, refresh=args.refresh, timeout=args.timeout
        )
    except (ParisReferenceError, FileExistsError, OSError, ValueError) as error:
        print(f"Paris reference acquisition failed: {error}", file=sys.stderr)
        return 2
    print(f"Wrote validated Paris arrondissement reference: {path}")
    return 0


def _run_changes(args: argparse.Namespace) -> int:
    if args.validate_only and args.replace:
        print("Changes report failed: --replace cannot be used with --validate-only", file=sys.stderr)
        return 2
    options = {
        "previous_year": args.previous_year,
        "current_year": args.current_year,
        "product_root": args.product_root,
        "overrides_path": args.overrides_path,
    }
    try:
        if args.validate_only:
            result = validate_changes(**options)
        else:
            result = run_changes(
                **options, output_root=args.output_root, replace=args.replace
            )
    except (
        ChangesPublicationError,
        ChangesValidationError,
        FileExistsError,
        FileNotFoundError,
    ) as error:
        print(f"Changes report failed: {error}", file=sys.stderr)
        return 2
    validation = result.validation
    print(
        f"Changes {result.previous_year}->{result.current_year}: "
        f"{validation.matched_rows} matched, {validation.new_entries} new, "
        f"{validation.removed_entries} removed, "
        f"{validation.fuzzy_candidates} fuzzy review candidates."
    )
    if args.validate_only:
        print("Validation complete; no reports were published.")
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
    if args.command == "arrondissements":
        return _run_arrondissements(args)
    if args.command == "acquire-paris-arrondissements":
        return _run_paris_reference(args)
    if args.command == "changes":
        return _run_changes(args)
    raise AssertionError(f"Unhandled command: {args.command}")
