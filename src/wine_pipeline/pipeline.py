"""Command-line orchestration for the reproducible wine AOC pipeline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import uuid

import geopandas as gpd
import requests

from . import __version__
from .aoc_enrichment.extract import download_uc_davis_regions
from .aoc_enrichment.mappings import FALLBACK_REGIONS_BY_DT, REGION_OVERRIDE_METADATA, REGION_OVERRIDES_BY_ID, WINE_REGION_COLORS
from .aoc_enrichment.transform import write_enriched_candidate
from .aoc_package.extract import extract_inao_source, source_urls
from .aoc_package.transform import write_packaged_candidate
from .config import DURABLE_REPORT_ROOT, OUTPUT_LAYER, RUN_ROOT
from .provenance import ReportCollector, sha256_file, source_date_from_headers, utc_now, write_json
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


def build(*, run_root: Path = RUN_ROOT, report_root: Path = DURABLE_REPORT_ROOT) -> WineBuildResult:
    run_id = _run_id()
    run_dir = run_root / run_id
    candidates_dir = run_dir / "candidates"
    report = ReportCollector(run_id=run_id)
    durable_paths: dict[str, Path] = {}
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        inao_download, shapefile = extract_inao_source(run_dir)
        uc_davis_source = download_uc_davis_regions(run_dir)

        raw_aoc = gpd.read_file(shapefile.shapefile_path)
        packaged_path = candidates_dir / "aoc_packaged.gpkg"
        packaged, package_checks, package_metadata = write_packaged_candidate(raw_aoc, packaged_path)
        report.extend_checks(package_checks)

        region_data = gpd.read_file(uc_davis_source.path)
        enriched_path = candidates_dir / "aoc_regions.gpkg"
        enriched, enrichment_checks, enrichment_metadata = write_enriched_candidate(packaged, region_data, enriched_path)
        report.extend_checks(enrichment_checks)

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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            result = build(run_root=args.run_root, report_root=args.report_root)
            print(f"Built wine AOC candidates for run {result.run_id}")
            print(f"  run dir: {result.run_dir}")
            print(f"  packaged candidate: {result.packaged_candidate}")
            print(f"  enriched candidate: {result.enriched_candidate}")
            print(f"  provenance report: {result.provenance_report}")
            print(f"  validation report: {result.validation_report}")
            print(f"  validation checks: {result.checks}")
            return 0
        raise AssertionError(args.command)
    except (WinePipelineError, FileExistsError, FileNotFoundError, requests.RequestException, ValueError) as error:
        print(f"Wine pipeline failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

