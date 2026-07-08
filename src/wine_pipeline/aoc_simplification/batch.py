"""All-region Stage 2 simplification orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import csv
import json
from pathlib import Path
import shlex
import shutil
import sys
import uuid

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely

from .. import __version__
from ..provenance import sha256_file
from .runner import (
    _assert_child_path,
    _expected_artifacts,
    _install_run_directory,
    find_project_root,
    git_state,
    infer_stage1_run_id,
    resolve_stage1_input,
    run_single_region,
    utc_now,
)
from .transform import (
    CANONICAL_RUN_ID,
    OUTPUT_COLUMNS,
    SimplificationParameters,
    validate_stage1_schema,
    slugify_region,
)


MACHINE_REVIEW_COLUMNS = [
    "region",
    "region_slug",
    "status",
    "input_row_count",
    "final_row_count",
    "source_area_m2",
    "final_area_m2",
    "final_to_source_area_ratio",
    "fully_covered_app_count",
    "fully_covered_apps",
    "near_total_reduction_count",
    "invalid_geometry_count",
    "empty_geometry_count",
    "candidate_file_size_mb",
    "serialization_cleanup_count",
    "serialization_cleanup_apps",
    "serialization_cleanup_removed_area_m2",
    "post_reprojection_review_count",
    "post_reprojection_review_apps",
    "residual_overlap_area_m2",
    "residual_overlap_ratio",
    "residual_overlap_classification",
    "parameter_set",
    "source_sha256",
    "error",
]

HUMAN_REVIEW_COLUMNS = [
    "review_status",
    "reviewer",
    "reviewed_at",
    "geometry_assessment",
    "overlap_assessment",
    "fully_covered_assessment",
    "notes",
]

REVIEW_COLUMNS = [*MACHINE_REVIEW_COLUMNS, *HUMAN_REVIEW_COLUMNS]
NEAR_TOTAL_REDUCTION_PERCENT = 99.0


@dataclass(frozen=True)
class RegionOutcome:
    region: str
    region_slug: str
    status: str
    error: str = ""


@dataclass(frozen=True)
class BatchResult:
    run_id: str
    run_dir: Path
    completed_regions: list[str]
    skipped_regions: list[str]
    failed_regions: dict[str, str]
    passed: bool


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def discover_regions(source: gpd.GeoDataFrame) -> list[str]:
    validate_stage1_schema(source)
    return sorted({str(value).strip() for value in source["region"].dropna() if str(value).strip()})


def _dependency_versions() -> dict[str, str]:
    return {
        "geopandas": gpd.__version__,
        "pandas": pd.__version__,
        "pyogrio": pyogrio.__version__,
        "shapely": shapely.__version__,
    }


def _effective_parameters_equal(observed: dict[str, object], expected: SimplificationParameters) -> bool:
    return (
        float(observed.get("buffer_m", -1)) == expected.buffer_m
        and float(observed.get("simplify_m", -1)) == expected.simplify_m
        and observed.get("overlap_strategy") == expected.overlap_strategy
        and bool(observed.get("canonical_parameter_set")) == expected.canonical
    )


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_candidate(path: Path) -> tuple[int, int, int]:
    frame = gpd.read_file(path, engine="pyogrio")
    if list(frame.columns) != OUTPUT_COLUMNS:
        raise ValueError(f"candidate schema mismatch: {list(frame.columns)}")
    if frame.crs is None or frame.crs.to_epsg() != 4326:
        raise ValueError(f"candidate CRS mismatch: {frame.crs}")
    invalid = int((~frame.geometry.is_valid).sum())
    empty = int(frame.geometry.is_empty.sum() + frame.geometry.isna().sum())
    if invalid or empty:
        raise ValueError(f"candidate contains {invalid} invalid and {empty} empty geometries")
    return len(frame), invalid, empty


def _residual_overlap_payload(metrics: dict[str, object] | None) -> dict[str, object]:
    overlap = (metrics or {}).get("overlap") or {}
    residual = overlap.get("residual_overlap") or {}
    if isinstance(residual, dict) and residual:
        return residual
    after = overlap.get("after_partition") or {}
    return {
        "residual_overlap_area_m2": after.get("overlap_area_m2", ""),
        "union_area_m2": after.get("union_area_m2", ""),
        "residual_overlap_ratio": "",
        "classification": "none" if overlap.get("residual_overlap_within_tolerance") else "fatal",
        "fatal": not bool(overlap.get("residual_overlap_within_tolerance", False)),
    }


def validate_region_artifacts(
    region_dir: Path,
    *,
    run_id: str,
    region: str,
    source_sha256: str,
    parameters: SimplificationParameters,
) -> tuple[bool, str]:
    try:
        missing = [path.name for path in _expected_artifacts(region_dir) if not path.is_file()]
        if missing:
            return False, "missing artifacts: " + ", ".join(missing)
        params = _read_json(region_dir / "params.json")
        metrics = _read_json(region_dir / "metrics.json")
        if params.get("run_id") != run_id:
            return False, f"params run_id mismatch: {params.get('run_id')!r}"
        if params.get("region") != region:
            return False, f"params region mismatch: {params.get('region')!r}"
        if params.get("stage1_source_sha256") != source_sha256:
            return False, "stage1 source hash mismatch"
        if not _effective_parameters_equal(dict(params.get("effective_parameters") or {}), parameters):
            return False, "effective parameters mismatch"
        if not _effective_parameters_equal(dict(metrics.get("parameters") or {}), parameters):
            return False, "metrics parameters mismatch"
        _validate_candidate(region_dir / "candidate.geojson")
        residual = _residual_overlap_payload(metrics)
        if residual.get("classification") == "fatal" or residual.get("fatal") is True:
            return False, "residual overlap is fatal"
    except Exception as error:
        return False, str(error)
    return True, ""


def _region_metrics(region_dir: Path) -> dict[str, object] | None:
    path = region_dir / "metrics.json"
    if not path.is_file():
        return None
    return _read_json(path)


def _near_total_reductions(metrics: dict[str, object] | None) -> list[str]:
    partition = (metrics or {}).get("partition") or {}
    per_app = partition.get("per_app") or []
    names = []
    for item in per_app:
        if not isinstance(item, dict) or item.get("became_empty"):
            continue
        try:
            removed = float(item.get("removed_overlap_percent", 0))
        except (TypeError, ValueError):
            continue
        if removed >= NEAR_TOTAL_REDUCTION_PERCENT:
            names.append(str(item.get("app", "")))
    return sorted(name for name in names if name)


def _review_row(
    *,
    region: str,
    status: str,
    source_sha256: str,
    error: str = "",
    region_dir: Path | None = None,
    input_rows: int = 0,
) -> dict[str, str]:
    metrics = _region_metrics(region_dir) if region_dir else None
    stages = (metrics or {}).get("stages") or {}
    final = stages.get("final_candidate") or {}
    source = stages.get("dissolved_by_identity") or stages.get("stage1_regional_input") or {}
    partition = (metrics or {}).get("partition") or {}
    final_area = final.get("area_m2_epsg_2154", "")
    source_area = source.get("area_m2_epsg_2154", "")
    try:
        ratio = "" if float(source_area) == 0 else f"{float(final_area) / float(source_area):.8f}"
    except (TypeError, ValueError):
        ratio = ""
    fully_covered = partition.get("fully_covered_app_names") or []
    near_total = _near_total_reductions(metrics)
    cleanup = (metrics or {}).get("serialization_cleanup") or {}
    cleanup_diagnostics = cleanup.get("diagnostics") or []
    cleanup_apps = sorted(
        str(item.get("app"))
        for item in cleanup_diagnostics
        if isinstance(item, dict)
        and (
            item.get("cleanup_action") != "unchanged"
            or item.get("post_reprojection_cleanup_action")
            != "post_reprojection_unchanged"
        )
    )
    review_repairs = [
        item
        for item in cleanup_diagnostics
        if isinstance(item, dict)
        and item.get("post_reprojection_review_classification") == "review"
    ]
    residual_overlap = _residual_overlap_payload(metrics)
    return {
        "region": region,
        "region_slug": slugify_region(region),
        "status": status,
        "input_row_count": str(input_rows or ((stages.get("stage1_regional_input") or {}).get("feature_count") or "")),
        "final_row_count": str(final.get("feature_count", "")),
        "source_area_m2": str(source_area),
        "final_area_m2": str(final_area),
        "final_to_source_area_ratio": ratio,
        "fully_covered_app_count": str(partition.get("fully_covered_app_count", "")),
        "fully_covered_apps": ";".join(map(str, fully_covered)),
        "near_total_reduction_count": str(len(near_total)),
        "invalid_geometry_count": str(final.get("invalid_geometry_count", "")),
        "empty_geometry_count": str(final.get("empty_geometry_count", "")),
        "candidate_file_size_mb": str((metrics or {}).get("candidate_file_size_mb", "")),
        "serialization_cleanup_count": str(cleanup.get("removed_component_count", "")),
        "serialization_cleanup_apps": ";".join(cleanup_apps),
        "serialization_cleanup_removed_area_m2": str(cleanup.get("removed_area_m2", "")),
        "post_reprojection_review_count": str(len(review_repairs)),
        "post_reprojection_review_apps": ";".join(
            sorted(str(item.get("app")) for item in review_repairs)
        ),
        "residual_overlap_area_m2": str(residual_overlap.get("residual_overlap_area_m2", "")),
        "residual_overlap_ratio": str(residual_overlap.get("residual_overlap_ratio", "")),
        "residual_overlap_classification": str(residual_overlap.get("classification", "")),
        "parameter_set": str((metrics or {}).get("parameters", {}).get("canonical_parameter_set_name") or "experimental"),
        "source_sha256": source_sha256,
        "error": error,
    }


def _load_existing_human_review(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row.get("region_slug", ""): {column: row.get(column, "") for column in HUMAN_REVIEW_COLUMNS}
            for row in csv.DictReader(handle)
        }


def write_review_table(path: Path, rows: list[dict[str, str]]) -> None:
    existing_human = _load_existing_human_review(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item["region"]):
            human = existing_human.get(row["region_slug"], {})
            writer.writerow({**row, **{column: human.get(column, "") for column in HUMAN_REVIEW_COLUMNS}})


def _summarise(
    *,
    expected_regions: list[str],
    outcomes: list[RegionOutcome],
    run_dir: Path,
) -> dict[str, object]:
    completed = [item.region for item in outcomes if item.status == "completed"]
    skipped = [item.region for item in outcomes if item.status == "skipped"]
    failed = {item.region: item.error for item in outcomes if item.status == "failed"}
    fully_covered: dict[str, list[str]] = {}
    near_total: dict[str, list[str]] = {}
    serialization_cleanup: dict[str, list[dict[str, object]]] = {}
    post_reprojection_review: dict[str, list[dict[str, object]]] = {}
    residual_overlap_by_region: dict[str, dict[str, object]] = {}
    total_final_rows = 0
    for region in expected_regions:
        metrics = _region_metrics(run_dir / "regions" / slugify_region(region))
        if not metrics:
            continue
        final = ((metrics.get("stages") or {}).get("final_candidate") or {})
        total_final_rows += int(final.get("feature_count") or 0)
        partition = metrics.get("partition") or {}
        covered = [str(name) for name in partition.get("fully_covered_app_names") or []]
        if covered:
            fully_covered[region] = covered
        reduced = _near_total_reductions(metrics)
        if reduced:
            near_total[region] = reduced
        cleanup_diagnostics = (metrics.get("serialization_cleanup") or {}).get("diagnostics") or []
        modified = [
            item for item in cleanup_diagnostics
            if isinstance(item, dict)
            and (
                item.get("cleanup_action") != "unchanged"
                or item.get("post_reprojection_cleanup_action")
                != "post_reprojection_unchanged"
            )
        ]
        if modified:
            serialization_cleanup[region] = modified
        review_repairs = [
            item
            for item in cleanup_diagnostics
            if isinstance(item, dict)
            and item.get("post_reprojection_review_classification") == "review"
        ]
        if review_repairs:
            post_reprojection_review[region] = review_repairs
        residual_overlap = _residual_overlap_payload(metrics)
        if residual_overlap:
            residual_overlap_by_region[region] = residual_overlap
    return {
        "expected_region_count": len(expected_regions),
        "completed_region_count": len(completed),
        "skipped_region_count": len(skipped),
        "failed_region_count": len(failed),
        "completed_regions": sorted(completed),
        "skipped_regions": sorted(skipped),
        "failed_regions": dict(sorted(failed.items())),
        "total_input_rows": None,
        "total_final_candidate_rows": total_final_rows,
        "fully_covered_appellations_by_region": dict(sorted(fully_covered.items())),
        "near_total_area_reductions_by_region": dict(sorted(near_total.items())),
        "serialization_cleanup_by_region": dict(sorted(serialization_cleanup.items())),
        "post_reprojection_review_repairs_by_region": dict(
            sorted(post_reprojection_review.items())
        ),
        "residual_overlap_by_region": dict(sorted(residual_overlap_by_region.items())),
        "passed": not failed and len(completed) + len(skipped) == len(expected_regions),
    }


def validate_batch(
    *,
    run_dir: Path,
    expected_regions: list[str],
    source_sha256: str,
    parameters: SimplificationParameters,
) -> dict[str, object]:
    checks = []
    region_root = run_dir / "regions"
    actual_slugs = sorted(path.name for path in region_root.iterdir() if path.is_dir() and not path.name.startswith("."))
    expected_slugs = sorted(slugify_region(region) for region in expected_regions)
    checks.append({"name": "exact_expected_region_coverage", "passed": actual_slugs == expected_slugs, "observed": actual_slugs, "expected": expected_slugs})
    checks.append({"name": "one_output_directory_per_region", "passed": len(actual_slugs) == len(set(actual_slugs)) == len(expected_slugs), "observed": len(actual_slugs), "expected": len(expected_slugs)})
    source_hashes = []
    parameter_matches = []
    schema_matches = []
    crs_matches = []
    invalid_counts = []
    empty_counts = []
    complete_artifacts = []
    residual_matches = []
    residual_classifications = []
    residual_overlap_by_region = {}
    review_regions = []
    for region in expected_regions:
        region_dir = region_root / slugify_region(region)
        try:
            params = _read_json(region_dir / "params.json")
            metrics = _read_json(region_dir / "metrics.json")
            source_hashes.append(params.get("stage1_source_sha256"))
            parameter_matches.append(_effective_parameters_equal(dict(params.get("effective_parameters") or {}), parameters))
            complete_artifacts.append(all(path.is_file() for path in _expected_artifacts(region_dir)))
            candidate = gpd.read_file(region_dir / "candidate.geojson", engine="pyogrio")
            schema_matches.append(list(candidate.columns) == OUTPUT_COLUMNS)
            crs_matches.append(candidate.crs is not None and candidate.crs.to_epsg() == 4326)
            invalid_counts.append(int((~candidate.geometry.is_valid).sum()))
            empty_counts.append(int(candidate.geometry.is_empty.sum() + candidate.geometry.isna().sum()))
            residual = _residual_overlap_payload(metrics)
            residual_classifications.append(residual.get("classification"))
            residual_matches.append(residual.get("classification") != "fatal" and residual.get("fatal") is not True)
            residual_overlap_by_region[region] = residual
            review_regions.append(region)
        except Exception:
            source_hashes.append(None)
            parameter_matches.append(False)
            complete_artifacts.append(False)
            schema_matches.append(False)
            crs_matches.append(False)
            invalid_counts.append(1)
            empty_counts.append(1)
            residual_matches.append(False)
    checks.extend(
        [
            {"name": "consistent_source_hash", "passed": all(value == source_sha256 for value in source_hashes), "observed": sorted(set(map(str, source_hashes))), "expected": source_sha256},
            {"name": "consistent_parameters", "passed": all(parameter_matches), "observed": parameter_matches, "expected": True},
            {"name": "exact_candidate_schema", "passed": all(schema_matches), "observed": schema_matches, "expected": OUTPUT_COLUMNS},
            {"name": "epsg_4326_outputs", "passed": all(crs_matches), "observed": crs_matches, "expected": "EPSG:4326"},
            {"name": "no_invalid_geometry", "passed": sum(invalid_counts) == 0, "observed": sum(invalid_counts), "expected": 0},
            {"name": "no_empty_geometry", "passed": sum(empty_counts) == 0, "observed": sum(empty_counts), "expected": 0},
            {"name": "complete_artifact_sets", "passed": all(complete_artifacts), "observed": complete_artifacts, "expected": True},
            {
                "name": "residual_overlap_not_fatal",
                "passed": all(residual_matches),
                "observed": residual_classifications,
                "expected": ["none", "negligible", "review"],
            },
            {"name": "deterministic_region_ordering", "passed": expected_regions == sorted(expected_regions), "observed": expected_regions, "expected": sorted(expected_regions)},
        ]
    )
    unexpected_candidate_files = [
        str(path.relative_to(run_dir))
        for path in region_root.glob("**/candidate.geojson")
        if path.parent.name not in expected_slugs
    ]
    checks.append({"name": "no_unexpected_candidate_files", "passed": not unexpected_candidate_files, "observed": unexpected_candidate_files, "expected": []})
    fully_covered = {}
    near_total = {}
    for region in expected_regions:
        metrics = _region_metrics(region_root / slugify_region(region))
        if not metrics:
            continue
        partition = metrics.get("partition") or {}
        if partition.get("fully_covered_app_names"):
            fully_covered[region] = partition.get("fully_covered_app_names")
        reduced = _near_total_reductions(metrics)
        if reduced:
            near_total[region] = reduced
    return {
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "fully_covered_appellations_by_region": dict(sorted(fully_covered.items())),
        "near_total_area_reductions_by_region": dict(sorted(near_total.items())),
        "residual_overlap_by_region": dict(sorted(residual_overlap_by_region.items())),
    }


def _write_batch_reports(
    *,
    run_dir: Path,
    run_payload: dict[str, object],
    summary: dict[str, object],
    validation: dict[str, object],
    review_rows: list[dict[str, str]],
) -> None:
    _write_json(run_dir / "run.json", run_payload)
    _write_json(run_dir / "batch_summary.json", summary)
    _write_json(run_dir / "validation.json", validation)
    write_review_table(run_dir / "region_review.csv", review_rows)


def _prepare_run_dir(output_root: Path, run_id: str, *, resume: bool, overwrite: bool) -> tuple[Path, Path | None]:
    output_root = output_root.resolve()
    final_dir = _assert_child_path(output_root / run_id, output_root, label="Batch run directory")
    if resume:
        final_dir.mkdir(parents=True, exist_ok=True)
        return final_dir, None
    if overwrite:
        temp_root = _assert_child_path(output_root / f".{run_id}.tmp-{uuid.uuid4().hex}", output_root, label="Temporary batch root")
        temp_root.mkdir(parents=True, exist_ok=False)
        return temp_root / run_id, temp_root
    if final_dir.exists():
        raise FileExistsError(f"Batch run directory already exists: {final_dir}; use --resume or --overwrite.")
    final_dir.mkdir(parents=True, exist_ok=False)
    return final_dir, None


def run_batch(
    *,
    input_path: Path | None = None,
    run_id: str = CANONICAL_RUN_ID,
    output_root: Path | None = None,
    parameters: SimplificationParameters | None = None,
    resume: bool = False,
    overwrite: bool = False,
    progress: Callable[[str], None] | None = None,
    command: list[str] | None = None,
) -> BatchResult:
    if resume and overwrite:
        raise ValueError("--resume and --overwrite are mutually exclusive.")
    progress = progress or (lambda message: None)
    parameters = parameters or SimplificationParameters()
    project_root = find_project_root()
    source_path = resolve_stage1_input(input_path, project_root=project_root)
    source_hash = sha256_file(source_path)
    stage1_run_id = infer_stage1_run_id(source_path, project_root=project_root)
    source = gpd.read_file(source_path, layer="aocs_france")
    regions = discover_regions(source)
    input_counts = source.groupby("region").size().to_dict()
    total_input_rows = len(source)
    run_id = slugify_region(run_id)
    if not run_id:
        raise ValueError("Run ID must contain at least one ASCII letter or number.")
    output_root = output_root or project_root / "tmp" / "wine" / "simplification"
    run_dir, temp_root = _prepare_run_dir(output_root, run_id, resume=resume, overwrite=overwrite)
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    outcomes: list[RegionOutcome] = []
    review_rows: list[dict[str, str]] = []
    try:
        for region in regions:
            slug = slugify_region(region)
            region_dir = run_dir / "regions" / slug
            if resume and region_dir.exists():
                valid, reason = validate_region_artifacts(
                    region_dir,
                    run_id=run_id,
                    region=region,
                    source_sha256=source_hash,
                    parameters=parameters,
                )
                if valid:
                    progress(f"skipping complete region: {region}")
                    outcomes.append(RegionOutcome(region, slug, "skipped"))
                    review_rows.append(
                        _review_row(region=region, status="skipped", source_sha256=source_hash, region_dir=region_dir, input_rows=int(input_counts.get(region, 0)))
                    )
                    continue
                progress(f"regenerating stale region {region}: {reason}")
            try:
                run_single_region(
                    region=region,
                    input_path=source_path,
                    run_id=run_id,
                    output_root=run_dir.parent,
                    parameters=parameters,
                    overwrite=resume or overwrite,
                    progress=progress,
                    command=command,
                )
                outcomes.append(RegionOutcome(region, slug, "completed"))
                review_rows.append(
                    _review_row(region=region, status="completed", source_sha256=source_hash, region_dir=region_dir, input_rows=int(input_counts.get(region, 0)))
                )
            except Exception as error:
                outcomes.append(RegionOutcome(region, slug, "failed", str(error)))
                review_rows.append(
                    _review_row(region=region, status="failed", source_sha256=source_hash, error=str(error), region_dir=region_dir, input_rows=int(input_counts.get(region, 0)))
                )
                progress(f"failed region {region}: {error}")
        summary = _summarise(expected_regions=regions, outcomes=outcomes, run_dir=run_dir)
        summary["total_input_rows"] = total_input_rows
        validation = validate_batch(run_dir=run_dir, expected_regions=regions, source_sha256=source_hash, parameters=parameters) if summary["failed_region_count"] == 0 else {
            "checks": [],
            "passed": False,
            "fully_covered_appellations_by_region": {},
            "near_total_area_reductions_by_region": {},
            "skipped_due_to_region_failures": True,
        }
        run_payload = {
            "batch_run_id": run_id,
            "stage1_run_id": stage1_run_id,
            "stage1_source_path": str(source_path),
            "stage1_source_sha256": source_hash,
            "canonical_parameter_set": parameters.canonical,
            "effective_parameters": parameters.as_dict(),
            "expected_region_inventory": regions,
            "package_version": __version__,
            "git_state": git_state(project_root),
            "python_version": sys.version,
            "dependency_versions": _dependency_versions(),
            "started_at_utc": started_at,
            "completed_at_utc": utc_now(),
            "command": shlex.join(command or sys.argv),
            "passed": bool(summary["passed"] and validation["passed"]),
        }
        _write_batch_reports(run_dir=run_dir, run_payload=run_payload, summary=summary, validation=validation, review_rows=review_rows)
        if overwrite and temp_root is not None and run_payload["passed"]:
            final_dir = (output_root or project_root / "tmp" / "wine" / "simplification").resolve() / run_id
            _install_run_directory(run_dir, final_dir, overwrite=True)
            shutil.rmtree(temp_root, ignore_errors=True)
            run_dir = final_dir
        elif overwrite and temp_root is not None and not run_payload["passed"]:
            shutil.rmtree(temp_root, ignore_errors=True)
        completed = [item.region for item in outcomes if item.status == "completed"]
        skipped = [item.region for item in outcomes if item.status == "skipped"]
        failed = {item.region: item.error for item in outcomes if item.status == "failed"}
        return BatchResult(run_id=run_id, run_dir=run_dir, completed_regions=completed, skipped_regions=skipped, failed_regions=failed, passed=bool(run_payload["passed"]))
    except Exception:
        if overwrite and temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
        raise
