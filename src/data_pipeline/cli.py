"""Command-line interface for local Michelin transformations."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .stage1.fidelity import compare_partition_roots
from .stage1.pipeline import Stage1PublicationError, run_stage1, validate_stage1
from .stage1.validation import Stage1ValidationError


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

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
