"""Command-line orchestration for the reproducible wine AOC pipeline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import uuid
from collections.abc import Callable

import geopandas as gpd
import requests

from . import __version__
from .aoc_enrichment.extract import download_uc_davis_regions
from .aoc_enrichment.mappings import FALLBACK_REGIONS_BY_DT, REGION_OVERRIDE_METADATA, REGION_OVERRIDES_BY_ID, WINE_REGION_COLORS
from .aoc_enrichment.transform import write_enriched_candidate
from .aoc_package.extract import extract_inao_source, source_urls
from .aoc_package.transform import write_packaged_candidate
from .aoc_simplification.assembly import assemble_candidate, resolve_simplification_run_id
from .aoc_simplification.batch import run_batch
from .aoc_simplification.diagnostics import run_diagnostics
from .aoc_simplification.runner import find_project_root, resolve_stage1_input, run_single_region
from .aoc_simplification.transform import CANONICAL_RUN_ID, SimplificationParameters
from .config import DURABLE_REPORT_ROOT, OUTPUT_LAYER, RUN_ROOT
from .provenance import ReportCollector, sha256_file, source_date_from_headers, utc_now, write_json
from .product import publish_product, resolve_candidate_id
from .validation import WinePipelineError


@dataclass(frozen=True)
class WineBuildResult:
    run_id: str
    run_dir: Path
    packaged_candidate: Path
    enriched_candidate: Path
    provenance_report: Path
    validation_report: Path
    checks: int


def _run_id() -> str:
    return f"{utc_now().replace(':', '').replace('+00:00', 'Z')}_{uuid.uuid4().hex[:8]}"


def _console_progress(enabled: bool) -> Callable[[str], None]:
    def progress(message: str) -> None:
        if enabled:
            print(f"[wine_pipeline {utc_now()}] {message}", flush=True)

    return progress


def build(
    *,
    run_root: Path = RUN_ROOT,
    report_root: Path = DURABLE_REPORT_ROOT,
    progress: Callable[[str], None] | None = None,
) -> WineBuildResult:
    run_id = _run_id()
    run_dir = run_root / run_id
    candidates_dir = run_dir / "candidates"
    report = ReportCollector(run_id=run_id)
    durable_paths: dict[str, Path] = {}
    progress = progress or (lambda message: None)
    try:
        progress(f"creating run directory: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=False)
        progress("downloading INAO AOC parcel archive")
        inao_download, shapefile = extract_inao_source(run_dir)
        progress(f"INAO archive downloaded and extracted: {shapefile.shapefile_path}")
        progress("downloading UC Davis regional GeoJSON")
        uc_davis_source = download_uc_davis_regions(run_dir)
        progress(f"UC Davis regions downloaded: {uc_davis_source.path}")

        progress("reading INAO shapefile")
        raw_aoc = gpd.read_file(shapefile.shapefile_path)
        packaged_path = candidates_dir / "aoc_packaged.gpkg"
        progress("building packaged AOC GeoPackage")
        packaged, package_checks, package_metadata = write_packaged_candidate(raw_aoc, packaged_path, progress=progress)
        report.extend_checks(package_checks)
        progress(f"packaged candidate written: {packaged_path} ({len(packaged)} rows)")

        progress("reading UC Davis regional polygons")
        region_data = gpd.read_file(uc_davis_source.path)
        enriched_path = candidates_dir / "aoc_regions.gpkg"
        progress("building regional enrichment candidate; spatial overlay may take a while")
        enriched, enrichment_checks, enrichment_metadata = write_enriched_candidate(packaged, region_data, enriched_path, progress=progress)
        report.extend_checks(enrichment_checks)
        progress(f"enriched candidate written: {enriched_path} ({len(enriched)} rows)")

        output_paths = {
            "aoc_packaged": packaged_path,
            "aoc_regions": enriched_path,
        }
        report.provenance = {
            "pipeline_version": __version__,
            "configured_source_urls": {
                "inao": source_urls(),
                "uc_davis": {
                    "branch_url": uc_davis_source.configured_branch_url,
                    "raw_url": uc_davis_source.configured_raw_url,
                },
            },
            "final_resolved_source_urls": {
                "inao": inao_download.final_url,
                "uc_davis": uc_davis_source.final_url,
            },
            "retrieval_headers": {
                "inao": inao_download.headers,
                "uc_davis": uc_davis_source.headers,
            },
            "source_files": {
                "inao_archive": inao_download.to_json(),
                "uc_davis_regions": uc_davis_source.to_json(),
            },
            "uc_davis_commit_sha": uc_davis_source.resolved_commit_sha,
            "extracted_shapefile": shapefile.to_json(),
            "source_row_counts": {
                "inao_parcels": len(raw_aoc),
                "uc_davis_regions": len(region_data),
            },
            "source_schemas": {
                "inao": raw_aoc.columns.tolist(),
                "uc_davis": region_data.columns.tolist(),
            },
            "source_crs": {
                "inao": raw_aoc.crs.to_string() if raw_aoc.crs else None,
                "uc_davis": region_data.crs.to_string() if region_data.crs else None,
            },
            "source_geometry_types": {
                "inao": raw_aoc.geometry.geom_type.value_counts(dropna=False).to_dict(),
                "uc_davis": region_data.geometry.geom_type.value_counts(dropna=False).to_dict(),
            },
            "source_bounds": {
                "inao": raw_aoc.total_bounds.tolist() if len(raw_aoc) else None,
                "uc_davis": region_data.total_bounds.tolist() if len(region_data) else None,
            },
            "packaging": package_metadata,
            "regional_source_authority_warning": uc_davis_source.authority_warning,
            "regional_enrichment": enrichment_metadata,
            "reviewed_mappings": {
                "explicit_override_mappings": REGION_OVERRIDES_BY_ID,
                "explicit_override_metadata": REGION_OVERRIDE_METADATA,
                "fallback_mappings": FALLBACK_REGIONS_BY_DT,
                "colour_mapping": WINE_REGION_COLORS,
            },
            "output_paths": {name: str(path) for name, path in output_paths.items()},
            "output_layer_names": {name: OUTPUT_LAYER for name in output_paths},
            "output_schemas": {
                "aoc_packaged": packaged.columns.tolist(),
                "aoc_regions": enriched.columns.tolist(),
            },
            "output_byte_sizes": {name: path.stat().st_size for name, path in output_paths.items()},
            "output_hashes": {name: sha256_file(path) for name, path in output_paths.items()},
            "output_crs": {
                "aoc_packaged": packaged.crs.to_string() if packaged.crs else None,
                "aoc_regions": enriched.crs.to_string() if enriched.crs else None,
            },
            "output_bounds": {
                "aoc_packaged": packaged.total_bounds.tolist() if len(packaged) else None,
                "aoc_regions": enriched.total_bounds.tolist() if len(enriched) else None,
            },
            "output_geometry_validity_status": {
                "aoc_packaged": bool(packaged.geometry.is_valid.all()),
                "aoc_regions": bool(enriched.geometry.is_valid.all()),
            },
        }
        source_date = source_date_from_headers(inao_download.headers, inao_download.retrieval_time_utc)
        progress("writing durable provenance and validation reports")
        durable_paths = report.write_durable_reports(
            source_date=source_date,
            hash_prefix=inao_download.sha256[:12],
            report_root=report_root,
        )
        run_payload = {
            "run_id": run_id,
            "status": "success",
            "run_dir": str(run_dir),
            "candidates": {name: str(path) for name, path in output_paths.items()},
            "durable_reports": {name: str(path) for name, path in durable_paths.items()},
            "checks": len(report.checks),
        }
        write_json(run_dir / "run-report.json", run_payload)
        progress(f"run report written: {run_dir / 'run-report.json'}")
        return WineBuildResult(
            run_id=run_id,
            run_dir=run_dir,
            packaged_candidate=packaged_path,
            enriched_candidate=enriched_path,
            provenance_report=durable_paths["provenance"],
            validation_report=durable_paths["validation"],
            checks=len(report.checks),
        )
    except Exception as error:
        write_json(
            run_dir / "run-report.json",
            {
                "run_id": run_id,
                "status": "failed",
                "run_dir": str(run_dir),
                "error_type": type(error).__name__,
                "error": str(error),
                "durable_reports": {name: str(path) for name, path in durable_paths.items()},
            },
        )
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m wine_pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="build the AOC package and regional candidate GeoPackages")
    build_parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    build_parser.add_argument("--report-root", type=Path, default=DURABLE_REPORT_ROOT)
    build_parser.add_argument("--quiet", action="store_true", help="suppress stage progress messages")
    simplify_parser = subparsers.add_parser("simplify-region", help="run Stage 2 simplification for one exact region")
    simplify_parser.add_argument("--region", required=True, help="exact region display name to simplify")
    simplify_parser.add_argument("--input", type=Path, help="Stage 1 aoc_regions.gpkg; defaults to the sole available Stage 1 candidate")
    simplify_parser.add_argument("--run-id", default=CANONICAL_RUN_ID, help="simplification run id")
    simplify_parser.add_argument("--output-root", type=Path, help="default: tmp/wine/simplification")
    simplify_parser.add_argument("--buffer", type=float, default=500.0, help="morphological closing distance in metres")
    simplify_parser.add_argument("--simplify", type=float, default=150.0, help="topology-preserving simplification tolerance in metres")
    simplify_parser.add_argument("--overlap-strategy", choices=("none", "smallest-wins"), default="smallest-wins")
    simplify_parser.add_argument("--overwrite", action="store_true", help="replace an existing regional run directory")
    simplify_parser.add_argument("--keep-failed-temp", action="store_true", help="retain the temporary regional run directory when processing fails")
    simplify_parser.add_argument("--quiet", action="store_true", help="suppress stage progress messages")
    batch_parser = subparsers.add_parser("simplify", help="run Stage 2 simplification for every region in one Stage 1 candidate")
    batch_parser.add_argument("--input", type=Path, help="Stage 1 aoc_regions.gpkg; defaults to the sole available Stage 1 candidate")
    batch_parser.add_argument("--run-id", default=CANONICAL_RUN_ID, help="batch simplification run id")
    batch_parser.add_argument("--output-root", type=Path, help="default: tmp/wine/simplification")
    batch_parser.add_argument("--buffer", type=float, default=500.0, help="morphological closing distance in metres")
    batch_parser.add_argument("--simplify", type=float, default=150.0, help="topology-preserving simplification tolerance in metres")
    batch_parser.add_argument("--overlap-strategy", choices=("none", "smallest-wins"), default="smallest-wins")
    mode = batch_parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", help="reuse complete matching regional artifacts and rebuild stale regions")
    mode.add_argument("--overwrite", action="store_true", help="replace the complete batch run transactionally")
    batch_parser.add_argument("--quiet", action="store_true", help="suppress stage progress messages")
    diagnostic_parser = subparsers.add_parser(
        "diagnose-simplification",
        help="run transform and serialization diagnostics without regional artifacts",
    )
    diagnostic_parser.add_argument("--input", type=Path, help="Stage 1 aoc_regions.gpkg; defaults to the sole available Stage 1 candidate")
    diagnostic_parser.add_argument("--diagnostic-run-id", help="diagnostic report directory name")
    diagnostic_parser.add_argument("--output-root", type=Path, help="default: tmp/wine/simplification/diagnostics")
    diagnostic_parser.add_argument("--region", help="optional exact region; defaults to every discovered region")
    diagnostic_parser.add_argument("--buffer", type=float, default=500.0, help="morphological closing distance in metres")
    diagnostic_parser.add_argument("--simplify", type=float, default=150.0, help="topology-preserving simplification tolerance in metres")
    diagnostic_parser.add_argument("--overlap-strategy", choices=("none", "smallest-wins"), default="smallest-wins")
    diagnostic_parser.add_argument("--quiet", action="store_true", help="suppress region progress messages")
    assemble_parser = subparsers.add_parser("assemble-candidate", help="assemble a validated Stage 2 simplification batch into a durable wine candidate")
    assemble_parser.add_argument("--simplification-run-id", help="simplification batch run id; defaults to the sole validated batch")
    assemble_parser.add_argument("--simplification-root", type=Path, help="default: tmp/wine/simplification")
    assemble_parser.add_argument("--candidate-id", help="candidate directory id; defaults to run-id plus UTC timestamp")
    assemble_parser.add_argument("--candidate-root", type=Path, help="default: data/candidates/wine")
    assemble_parser.add_argument("--report-root", type=Path, help="default: data/wine/reports")
    assemble_parser.add_argument("--validation-root", type=Path, help="default: data/wine/validation")
    assemble_parser.add_argument("--provenance-root", type=Path, help="default: data/wine/provenance")
    assemble_parser.add_argument("--overwrite", action="store_true", help="replace an existing durable candidate transactionally")
    assemble_parser.add_argument("--require-manual-approval", action="store_true", help="require every expected region to have review_status=approved")
    assemble_parser.add_argument("--quiet", action="store_true", help="suppress assembly progress messages")
    publish_parser = subparsers.add_parser("publish-product", help="verify and publish a durable wine candidate unchanged")
    publish_parser.add_argument("--candidate-id", help="candidate id; defaults to the sole validated durable candidate")
    publish_parser.add_argument("--release-date", help="product release date in YYYY-MM-DD; defaults to the current local date")
    publish_parser.add_argument("--overwrite", action="store_true", help="replace an existing dated product release transactionally")
    publish_parser.add_argument("--quiet", action="store_true", help="suppress publication progress messages")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            result = build(run_root=args.run_root, report_root=args.report_root, progress=_console_progress(not args.quiet))
            print(f"Built wine AOC candidates for run {result.run_id}")
            print(f"  run dir: {result.run_dir}")
            print(f"  packaged candidate: {result.packaged_candidate}")
            print(f"  enriched candidate: {result.enriched_candidate}")
            print(f"  provenance report: {result.provenance_report}")
            print(f"  validation report: {result.validation_report}")
            print(f"  validation checks: {result.checks}")
            return 0
        if args.command == "simplify-region":
            input_path = resolve_stage1_input(args.input, project_root=find_project_root())
            if args.input is None:
                print(f"Resolved sole Stage 1 wine candidate:\n{input_path}")
            parameters = SimplificationParameters(
                buffer_m=float(args.buffer),
                simplify_m=float(args.simplify),
                overlap_strategy=args.overlap_strategy,
            )
            result = run_single_region(
                region=args.region,
                input_path=input_path,
                run_id=args.run_id,
                output_root=args.output_root,
                parameters=parameters,
                overwrite=args.overwrite,
                keep_failed_temp=args.keep_failed_temp,
                progress=_console_progress(not args.quiet),
                command=["wine_pipeline", *sys.argv[1:]],
            )
            print(f"Built wine simplification candidate for {result.region}")
            print(f"  run dir: {result.run_dir}")
            print(f"  candidate: {result.candidate_path}")
            print(f"  metrics: {result.metrics_path}")
            print(f"  params: {result.params_path}")
            print(f"  preview: {result.preview_path}")
            print(f"  comparison: {result.comparison_path}")
            print(f"  overlap comparison: {result.overlap_comparison_path}")
            print(f"  rows: {result.rows}")
            return 0
        if args.command == "simplify":
            input_path = resolve_stage1_input(args.input, project_root=find_project_root())
            if args.input is None:
                print(f"Resolved sole Stage 1 wine candidate:\n{input_path}")
            parameters = SimplificationParameters(
                buffer_m=float(args.buffer),
                simplify_m=float(args.simplify),
                overlap_strategy=args.overlap_strategy,
            )
            result = run_batch(
                input_path=input_path,
                run_id=args.run_id,
                output_root=args.output_root,
                parameters=parameters,
                resume=args.resume,
                overwrite=args.overwrite,
                progress=_console_progress(not args.quiet),
                command=["wine_pipeline", *sys.argv[1:]],
            )
            print(f"Built wine simplification batch {result.run_id}")
            print(f"  run dir: {result.run_dir}")
            print(f"  completed regions: {len(result.completed_regions)}")
            print(f"  skipped regions: {len(result.skipped_regions)}")
            print(f"  failed regions: {len(result.failed_regions)}")
            print(f"  validation passed: {result.passed}")
            return 0 if result.passed else 2
        if args.command == "diagnose-simplification":
            input_path = resolve_stage1_input(args.input, project_root=find_project_root())
            if args.input is None:
                print(f"Resolved sole Stage 1 wine candidate:\n{input_path}")
            parameters = SimplificationParameters(
                buffer_m=float(args.buffer),
                simplify_m=float(args.simplify),
                overlap_strategy=args.overlap_strategy,
            )
            result = run_diagnostics(
                input_path=input_path,
                diagnostic_run_id=args.diagnostic_run_id,
                output_root=args.output_root,
                region=args.region,
                parameters=parameters,
                progress=_console_progress(not args.quiet),
            )
            print(f"Completed wine simplification diagnostics {result.run_id}")
            print(f"  run dir: {result.run_dir}")
            print(f"  JSON report: {result.json_path}")
            print(f"  CSV report: {result.csv_path}")
            print(f"  passed regions: {len(result.passed_regions)}")
            print(f"  failed regions: {len(result.failed_regions)}")
            return 0 if result.passed else 2
        if args.command == "assemble-candidate":
            simplification_run_id = resolve_simplification_run_id(
                args.simplification_run_id,
                project_root=find_project_root(),
                simplification_root=args.simplification_root,
            )
            if args.simplification_run_id is None:
                print(f"Resolved sole validated simplification run:\n{simplification_run_id}")
            result = assemble_candidate(
                simplification_run_id=simplification_run_id,
                simplification_root=args.simplification_root,
                candidate_id=args.candidate_id,
                candidate_root=args.candidate_root,
                report_root=args.report_root,
                validation_root=args.validation_root,
                provenance_root=args.provenance_root,
                overwrite=args.overwrite,
                require_manual_approval=args.require_manual_approval,
                progress=_console_progress(not args.quiet),
                command=["wine_pipeline", *sys.argv[1:]],
            )
            print(f"Assembled validated wine candidate {result.candidate_id}")
            print(f"  candidate dir: {result.candidate_dir}")
            print(f"  candidate: {result.candidate_path}")
            print(f"  manifest: {result.manifest_path}")
            print(f"  provenance report: {result.provenance_path}")
            print(f"  validation report: {result.validation_path}")
            print(f"  review report: {result.review_report_path}")
            print(f"  assembly summary: {result.summary_path}")
            print(f"  rows: {result.rows}")
            return 0
        if args.command == "publish-product":
            candidate_id = resolve_candidate_id(args.candidate_id, project_root=find_project_root())
            if args.candidate_id is None:
                print(f"Resolved sole validated wine candidate:\n{candidate_id}")
            result = publish_product(
                candidate_id=candidate_id,
                release_date=args.release_date,
                overwrite=args.overwrite,
                progress=_console_progress(not args.quiet),
                command=["wine_pipeline", *sys.argv[1:]],
            )
            print(f"Published wine product release {result.release_date}")
            print(f"  candidate: {result.candidate_id}")
            print(f"  release dir: {result.release_dir}")
            print(f"  product: {result.product_path}")
            print(f"  manifest: {result.manifest_path}")
            print(f"  validation: {result.validation_path}")
            print(f"  provenance: {result.provenance_path}")
            print(f"  features: {result.feature_count}")
            return 0
        raise AssertionError(args.command)
    except (WinePipelineError, FileExistsError, FileNotFoundError, requests.RequestException, ValueError) as error:
        print(f"Wine pipeline failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
