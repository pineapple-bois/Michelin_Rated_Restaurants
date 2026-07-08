"""Durable candidate assembly for validated Stage 2 wine simplification batches."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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
from ..provenance import sha256_file, utc_now, write_json
from ..validation import WinePipelineError
from .batch import REVIEW_COLUMNS, _effective_parameters_equal
from .runner import (
    _assert_child_path,
    _expected_artifacts,
    _install_run_directory,
    find_project_root,
    git_state,
)
from .transform import CANONICAL_RUN_ID, OUTPUT_COLUMNS, OUTPUT_IDENTITY_COLUMNS, SimplificationParameters, slugify_region


CANDIDATE_COLUMNS = OUTPUT_COLUMNS
CANDIDATE_IDENTITY_COLUMNS = OUTPUT_IDENTITY_COLUMNS
REVIEW_STATES = {"", "pending", "approved", "rejected", "rerun_required"}
BLOCKING_REVIEW_STATES = {"rejected", "rerun_required"}


@dataclass(frozen=True)
class AssemblyResult:
    candidate_id: str
    candidate_dir: Path
    candidate_path: Path
    manifest_path: Path
    provenance_path: Path
    validation_path: Path
    review_report_path: Path
    summary_path: Path
    rows: int
    passed: bool


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_report_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _dependency_versions() -> dict[str, str]:
    return {
        "geopandas": gpd.__version__,
        "pandas": pd.__version__,
        "pyogrio": pyogrio.__version__,
        "shapely": shapely.__version__,
    }


def _check(checks: list[dict[str, object]], name: str, passed: bool, observed: object, expected: object, message: str = "") -> None:
    checks.append(
        {
            "name": name,
            "passed": bool(passed),
            "observed": observed,
            "expected": expected,
            "message": "" if passed else message,
        }
    )


def _default_candidate_id(simplification_run_id: str) -> str:
    stamp = datetime.fromisoformat(utc_now()).strftime("%Y%m%dt%H%M%Sz").lower()
    return f"{slugify_region(simplification_run_id) or 'simplification'}_{stamp}"


def resolve_simplification_run_id(
    simplification_run_id: str | None,
    *,
    project_root: Path,
    simplification_root: Path | None = None,
) -> str:
    root = (simplification_root or project_root / "tmp" / "wine" / "simplification").resolve()
    if simplification_run_id is not None:
        _resolve_run_dir(project_root, simplification_run_id, root)
        return simplification_run_id

    eligible: list[str] = []
    if root.is_dir():
        for run_dir in sorted(root.iterdir(), key=lambda path: path.name):
            if not run_dir.is_dir() or run_dir.name.startswith(".") or run_dir.name == "diagnostics":
                continue
            required = (
                run_dir / "run.json",
                run_dir / "batch_summary.json",
                run_dir / "validation.json",
                run_dir / "regions",
            )
            if not all(path.is_file() for path in required[:3]) or not required[3].is_dir():
                continue
            try:
                validation = _read_json(run_dir / "validation.json")
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if validation.get("passed") is True:
                eligible.append(run_dir.name)

    if not eligible:
        raise FileNotFoundError(
            "No completed, validated simplification batch was found. Run "
            "'python -m wine_pipeline simplify' or provide --simplification-run-id <run-id>."
        )
    if len(eligible) > 1:
        choices = "\n".join(f"  - {run_id}" for run_id in eligible)
        raise WinePipelineError(
            "Multiple validated simplification batches were found:\n"
            f"{choices}\nProvide --simplification-run-id <run-id> to select one."
        )
    return eligible[0]


def _resolve_run_dir(project_root: Path, simplification_run_id: str, simplification_root: Path | None) -> Path:
    root = (simplification_root or project_root / "tmp" / "wine" / "simplification").resolve()
    run_dir = _assert_child_path(root / simplification_run_id, root, label="Simplification run directory")
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Simplification run directory not found: {run_dir}")
    return run_dir


def _prepare_candidate_dir(candidate_root: Path, candidate_id: str, *, overwrite: bool) -> tuple[Path, Path]:
    candidate_root = candidate_root.resolve()
    safe_id = slugify_region(candidate_id)
    if not safe_id:
        raise ValueError("Candidate ID must contain at least one ASCII letter or number.")
    final_dir = _assert_child_path(candidate_root / safe_id, candidate_root, label="Candidate directory")
    if final_dir.exists() and not overwrite:
        raise FileExistsError(f"Candidate directory already exists: {final_dir}; pass --overwrite to replace it.")
    temp_dir = _assert_child_path(candidate_root / f".{safe_id}.tmp-{uuid.uuid4().hex}", candidate_root, label="Temporary candidate directory")
    temp_dir.mkdir(parents=True, exist_ok=False)
    return final_dir, temp_dir


def _load_review_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def _review_by_region(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {str(row.get("region", "")).strip(): row for row in rows if str(row.get("region", "")).strip()}


def _generated_review_rows(expected_regions: list[str], *, source_sha256: str) -> tuple[list[dict[str, str]], list[str]]:
    rows = []
    for region in expected_regions:
        row = {column: "" for column in REVIEW_COLUMNS}
        row.update(
            {
                "region": region,
                "region_slug": slugify_region(region),
                "status": "completed",
                "source_sha256": source_sha256,
            }
        )
        rows.append(row)
    return rows, list(REVIEW_COLUMNS)


def _review_status(row: dict[str, str] | None) -> str:
    if row is None:
        return ""
    return str(row.get("review_status", "")).strip()


def _validate_candidate_frame(frame: gpd.GeoDataFrame, *, region: str, checks: list[dict[str, object]]) -> None:
    _check(checks, f"{region}: exact_schema", list(frame.columns) == CANDIDATE_COLUMNS, list(frame.columns), CANDIDATE_COLUMNS, "Regional candidate schema mismatch.")
    _check(checks, f"{region}: epsg_4326", frame.crs is not None and frame.crs.to_epsg() == 4326, None if frame.crs is None else frame.crs.to_string(), "EPSG:4326", "Regional candidate CRS mismatch.")
    null_count = int(frame.geometry.isna().sum())
    empty_count = int(frame.geometry.is_empty.sum()) if len(frame) else 0
    invalid_count = int((~frame.geometry.is_valid).sum()) if len(frame) else 0
    types = sorted(set(frame.geom_type.dropna().astype(str)))
    _check(checks, f"{region}: no_null_geometry", null_count == 0, null_count, 0, "Regional candidate contains null geometry.")
    _check(checks, f"{region}: no_empty_geometry", empty_count == 0, empty_count, 0, "Regional candidate contains empty geometry.")
    _check(checks, f"{region}: no_invalid_geometry", invalid_count == 0, invalid_count, 0, "Regional candidate contains invalid geometry.")
    _check(checks, f"{region}: polygon_only", set(types).issubset({"Polygon", "MultiPolygon"}), types, ["Polygon", "MultiPolygon"], "Regional candidate contains non-polygonal geometry.")


def _extract_review_items(summary: dict[str, object]) -> dict[str, object]:
    return {
        "fully_covered_appellations_by_region": summary.get("fully_covered_appellations_by_region", {}),
        "near_total_area_reductions_by_region": summary.get("near_total_area_reductions_by_region", {}),
        "serialization_cleanup_by_region": summary.get("serialization_cleanup_by_region", {}),
        "post_reprojection_review_repairs_by_region": summary.get("post_reprojection_review_repairs_by_region", {}),
        "residual_overlap_by_region": summary.get("residual_overlap_by_region", {}),
    }


def _fatal_residual_overlap_regions(metrics_by_region: dict[str, dict[str, object]], summary: dict[str, object]) -> dict[str, object]:
    fatal: dict[str, object] = {}
    sources: dict[str, object] = {}
    sources.update(summary.get("residual_overlap_by_region") or {})
    for region, metrics in metrics_by_region.items():
        residual = ((metrics.get("overlap") or {}).get("residual_overlap") or {})
        if residual:
            sources[region] = residual
    for region, residual in sources.items():
        if isinstance(residual, dict) and (residual.get("classification") == "fatal" or residual.get("fatal") is True):
            fatal[str(region)] = residual
    return fatal


def _fatal_serialization_regions(metrics_by_region: dict[str, dict[str, object]], summary: dict[str, object]) -> dict[str, object]:
    fatal: dict[str, object] = {}
    summary_sources = {
        **(summary.get("serialization_cleanup_by_region") or {}),
        **(summary.get("post_reprojection_review_repairs_by_region") or {}),
    }
    for region, items in summary_sources.items():
        for item in items if isinstance(items, list) else [items]:
            if isinstance(item, dict) and item.get("post_reprojection_review_classification") == "fatal":
                fatal[str(region)] = item
    for region, metrics in metrics_by_region.items():
        cleanup = metrics.get("serialization_cleanup") or {}
        counts = cleanup.get("post_reprojection_review_classification_counts") or {}
        if int(counts.get("fatal") or 0) > 0:
            fatal[region] = counts
            continue
        for item in cleanup.get("diagnostics") or []:
            if isinstance(item, dict) and item.get("post_reprojection_review_classification") == "fatal":
                fatal[region] = item
                break
    return fatal


def _validate_merged_candidate(frame: gpd.GeoDataFrame, *, expected_rows: int, checks: list[dict[str, object]]) -> None:
    _check(checks, "merged_exact_schema", list(frame.columns) == CANDIDATE_COLUMNS, list(frame.columns), CANDIDATE_COLUMNS, "Merged candidate schema mismatch.")
    _check(checks, "merged_epsg_4326", frame.crs is not None and frame.crs.to_epsg() == 4326, None if frame.crs is None else frame.crs.to_string(), "EPSG:4326", "Merged candidate CRS mismatch.")
    null_count = int(frame.geometry.isna().sum())
    empty_count = int(frame.geometry.is_empty.sum()) if len(frame) else 0
    invalid_count = int((~frame.geometry.is_valid).sum()) if len(frame) else 0
    types = sorted(set(frame.geom_type.dropna().astype(str)))
    duplicate_count = int(frame.duplicated(CANDIDATE_IDENTITY_COLUMNS).sum())
    sorted_frame = frame.sort_values(CANDIDATE_IDENTITY_COLUMNS, kind="mergesort").reset_index(drop=True)
    deterministic = frame[CANDIDATE_IDENTITY_COLUMNS].reset_index(drop=True).equals(sorted_frame[CANDIDATE_IDENTITY_COLUMNS].reset_index(drop=True))
    _check(checks, "merged_no_null_geometry", null_count == 0, null_count, 0, "Merged candidate contains null geometry.")
    _check(checks, "merged_no_empty_geometry", empty_count == 0, empty_count, 0, "Merged candidate contains empty geometry.")
    _check(checks, "merged_no_invalid_geometry", invalid_count == 0, invalid_count, 0, "Merged candidate contains invalid geometry.")
    _check(checks, "merged_polygon_only", set(types).issubset({"Polygon", "MultiPolygon"}), types, ["Polygon", "MultiPolygon"], "Merged candidate contains non-polygonal geometry.")
    _check(checks, "no_duplicate_output_identities", duplicate_count == 0, duplicate_count, 0, "Merged candidate contains duplicate output identities.")
    _check(checks, "deterministic_row_ordering", deterministic, frame[CANDIDATE_IDENTITY_COLUMNS].astype(str).values.tolist(), "sorted by output identity columns", "Merged candidate rows are not deterministically sorted.")
    _check(checks, "row_count_reconciliation", len(frame) == expected_rows, len(frame), expected_rows, "Merged row count does not match regional row total.")


def _raise_if_failed(checks: list[dict[str, object]]) -> None:
    failed = [check for check in checks if not check["passed"]]
    if failed:
        details = "; ".join(f"{item['name']}: {item['message']}" for item in failed[:8])
        raise WinePipelineError(f"Wine candidate assembly validation failed: {details}")


def assemble_candidate(
    *,
    simplification_run_id: str = CANONICAL_RUN_ID,
    simplification_root: Path | None = None,
    candidate_root: Path | None = None,
    report_root: Path | None = None,
    validation_root: Path | None = None,
    provenance_root: Path | None = None,
    candidate_id: str | None = None,
    overwrite: bool = False,
    require_manual_approval: bool = False,
    progress: Callable[[str], None] | None = None,
    command: list[str] | None = None,
) -> AssemblyResult:
    progress = progress or (lambda message: None)
    project_root = find_project_root()
    run_dir = _resolve_run_dir(project_root, simplification_run_id, simplification_root)
    candidate_root = candidate_root or project_root / "data" / "candidates" / "wine"
    report_root = report_root or project_root / "data" / "wine" / "reports"
    validation_root = validation_root or project_root / "data" / "wine" / "validation"
    provenance_root = provenance_root or project_root / "data" / "wine" / "provenance"
    candidate_id = candidate_id or _default_candidate_id(simplification_run_id)
    final_dir, temp_dir = _prepare_candidate_dir(candidate_root, candidate_id, overwrite=overwrite)
    safe_candidate_id = final_dir.name
    started_at = utc_now()
    checks: list[dict[str, object]] = []

    try:
        progress(f"reading simplification batch: {run_dir}")
        run = _read_json(run_dir / "run.json")
        summary = _read_json(run_dir / "batch_summary.json")
        validation = _read_json(run_dir / "validation.json")
        expected_regions = [str(region) for region in run.get("expected_region_inventory") or []]
        review_file_supplied = (run_dir / "region_review.csv").is_file()
        if review_file_supplied:
            review_rows, review_fieldnames = _load_review_rows(run_dir / "region_review.csv")
        else:
            review_rows, review_fieldnames = _generated_review_rows(expected_regions, source_sha256=str(run.get("stage1_source_sha256") or ""))
        review_lookup = _review_by_region(review_rows)
        source_sha256 = str(run.get("stage1_source_sha256") or "")
        if not review_file_supplied:
            review_rows, review_fieldnames = _generated_review_rows(expected_regions, source_sha256=source_sha256)
            review_lookup = _review_by_region(review_rows)
        expected_parameters = SimplificationParameters(
            buffer_m=float((run.get("effective_parameters") or {}).get("buffer_m", -1)),
            simplify_m=float((run.get("effective_parameters") or {}).get("simplify_m", -1)),
            overlap_strategy=str((run.get("effective_parameters") or {}).get("overlap_strategy", "")),
        )

        _check(checks, "batch_validation_passed", bool(validation.get("passed")), validation.get("passed"), True, "Batch validation did not pass.")
        _check(checks, "no_failed_regions", int(summary.get("failed_region_count") or 0) == 0, summary.get("failed_region_count"), 0, "Batch contains failed regions.")
        _check(checks, "complete_expected_region_coverage", bool(expected_regions), expected_regions, "non-empty expected region inventory", "Batch manifest has no expected regions.")
        _check(checks, "summary_region_count", int(summary.get("expected_region_count") or -1) == len(expected_regions), summary.get("expected_region_count"), len(expected_regions), "Batch summary region count does not match manifest.")
        _check(checks, "consistent_simplification_run_id", run.get("batch_run_id") == simplification_run_id, run.get("batch_run_id"), simplification_run_id, "Batch run ID mismatch.")
        missing_reviews = [region for region in expected_regions if region not in review_lookup]
        _check(
            checks,
            "review_file_supplied_for_strict_manual_approval",
            review_file_supplied or not require_manual_approval,
            review_file_supplied,
            True,
            "Strict manual approval requires region_review.csv.",
        )
        _check(
            checks,
            "review_rows_cover_expected_regions_for_strict_manual_approval",
            not require_manual_approval or not missing_reviews,
            missing_reviews,
            [],
            "Strict manual approval requires a review row for every expected region.",
        )
        explicit_blockers = {
            region: _review_status(review_lookup.get(region))
            for region in expected_regions
            if _review_status(review_lookup.get(region)) in BLOCKING_REVIEW_STATES
        }
        _check(checks, "no_explicit_rejected_or_rerun_required_regions", not explicit_blockers, explicit_blockers, {}, "One or more regions are explicitly rejected or require rerun.")
        unapproved = {
            region: _review_status(review_lookup.get(region)) or "blank"
            for region in expected_regions
            if _review_status(review_lookup.get(region)) != "approved"
        }
        _check(checks, "all_regions_approved_when_strict", not require_manual_approval or not unapproved, unapproved, "approved", "One or more regions are not approved.")
        invalid_review_states = sorted(
            {
                str(row.get("review_status", "")).strip()
                for row in review_rows
                if str(row.get("review_status", "")).strip() not in REVIEW_STATES
            }
        )
        _check(checks, "controlled_review_states", not invalid_review_states, invalid_review_states, sorted(REVIEW_STATES), "Review table contains uncontrolled review states.")

        frames: list[gpd.GeoDataFrame] = []
        source_candidates: list[dict[str, object]] = []
        metrics_by_region: dict[str, dict[str, object]] = {}
        total_regional_rows = 0
        region_root = run_dir / "regions"
        actual_region_slugs = sorted(path.name for path in region_root.iterdir() if path.is_dir() and not path.name.startswith(".")) if region_root.is_dir() else []
        expected_slugs = sorted(slugify_region(region) for region in expected_regions)
        _check(checks, "one_regional_directory_per_expected_region", actual_region_slugs == expected_slugs, actual_region_slugs, expected_slugs, "Regional directory coverage mismatch.")

        for region in expected_regions:
            slug = slugify_region(region)
            region_dir = region_root / slug
            missing_artifacts = [path.name for path in _expected_artifacts(region_dir) if not path.is_file()]
            _check(checks, f"{region}: complete_artifact_set", not missing_artifacts, missing_artifacts, [], "Regional artifact set is incomplete.")
            if missing_artifacts:
                continue
            params = _read_json(region_dir / "params.json")
            metrics = _read_json(region_dir / "metrics.json")
            metrics_by_region[region] = metrics
            _check(checks, f"{region}: params_run_id", params.get("run_id") == simplification_run_id, params.get("run_id"), simplification_run_id, "Regional params run ID mismatch.")
            _check(checks, f"{region}: metrics_run_id", metrics.get("run_id") == simplification_run_id, metrics.get("run_id"), simplification_run_id, "Regional metrics run ID mismatch.")
            _check(checks, f"{region}: source_hash", params.get("stage1_source_sha256") == source_sha256, params.get("stage1_source_sha256"), source_sha256, "Regional source hash mismatch.")
            _check(checks, f"{region}: effective_parameters", _effective_parameters_equal(dict(params.get("effective_parameters") or {}), expected_parameters), params.get("effective_parameters"), run.get("effective_parameters"), "Regional parameters mismatch.")
            _check(checks, f"{region}: metrics_parameters", _effective_parameters_equal(dict(metrics.get("parameters") or {}), expected_parameters), metrics.get("parameters"), run.get("effective_parameters"), "Regional metrics parameters mismatch.")
            candidate_path = region_dir / "candidate.geojson"
            frame = gpd.read_file(candidate_path, engine="pyogrio")
            _validate_candidate_frame(frame, region=region, checks=checks)
            if list(frame.columns) != CANDIDATE_COLUMNS:
                continue
            frames.append(frame[CANDIDATE_COLUMNS].copy())
            row_count = len(frame)
            total_regional_rows += row_count
            source_candidates.append(
                {
                    "region": region,
                    "region_slug": slug,
                    "path": str(candidate_path),
                    "sha256": sha256_file(candidate_path),
                    "row_count": row_count,
                    "metrics_path": str(region_dir / "metrics.json"),
                    "params_path": str(region_dir / "params.json"),
                }
            )

        _check(checks, "summary_row_count_reconciliation", int(summary.get("total_final_candidate_rows") or -1) == total_regional_rows, summary.get("total_final_candidate_rows"), total_regional_rows, "Batch summary row count does not match regional candidates.")
        fatal_residual_overlap = _fatal_residual_overlap_regions(metrics_by_region, summary)
        fatal_serialization = _fatal_serialization_regions(metrics_by_region, summary)
        _check(checks, "no_fatal_residual_overlap_classifications", not fatal_residual_overlap, fatal_residual_overlap, {}, "One or more regions have fatal residual overlap.")
        _check(checks, "no_fatal_serialization_or_post_reprojection_classifications", not fatal_serialization, fatal_serialization, {}, "One or more regions have fatal serialization or post-reprojection repair classifications.")
        _raise_if_failed(checks)
        progress("concatenating approved regional candidates")
        merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
        merged = merged[CANDIDATE_COLUMNS].sort_values(CANDIDATE_IDENTITY_COLUMNS, kind="mergesort").reset_index(drop=True)
        _validate_merged_candidate(merged, expected_rows=total_regional_rows, checks=checks)
        _raise_if_failed(checks)

        candidate_path = temp_dir / "wine_regions.geojson"
        progress(f"writing candidate GeoJSON: {candidate_path}")
        merged.to_file(candidate_path, driver="GeoJSON", index=False)
        reloaded = gpd.read_file(candidate_path, engine="pyogrio")
        _validate_merged_candidate(reloaded[CANDIDATE_COLUMNS], expected_rows=total_regional_rows, checks=checks)
        _check(checks, "geojson_round_trip_survives", all(check["passed"] for check in checks), True, True)
        _raise_if_failed(checks)

        completed_at = utc_now()
        review_items = _extract_review_items(summary)
        approval_mode = "explicit_manual_approval" if require_manual_approval else "automated_validated_batch"
        manifest = {
            "candidate_id": safe_candidate_id,
            "simplification_run_id": simplification_run_id,
            "approval_mode": approval_mode,
            "manual_review_file_supplied": review_file_supplied,
            "require_manual_approval": require_manual_approval,
            "stage1_run_id": run.get("stage1_run_id"),
            "stage1_source_path": run.get("stage1_source_path"),
            "stage1_source_sha256": source_sha256,
            "canonical_parameter_set": run.get("canonical_parameter_set"),
            "effective_simplification_parameters": run.get("effective_parameters"),
            "expected_region_inventory": expected_regions,
            "assembled_region_inventory": sorted(merged["region"].dropna().astype(str).unique().tolist()),
            "source_regional_candidates": source_candidates,
            "total_input_rows": total_regional_rows,
            "total_output_rows": len(merged),
            **review_items,
            "git_state": git_state(project_root),
            "package_version": __version__,
            "python_version": sys.version,
            "dependency_versions": _dependency_versions(),
            "command": shlex.join(command or sys.argv),
            "started_at_utc": started_at,
            "completed_at_utc": completed_at,
            "candidate_sha256": sha256_file(candidate_path),
            "candidate_file": "wine_regions.geojson",
        }
        provenance = {
            **manifest,
            "source_simplification_run_dir": str(run_dir),
            "source_reports": {
                "run": str(run_dir / "run.json"),
                "batch_summary": str(run_dir / "batch_summary.json"),
                "validation": str(run_dir / "validation.json"),
            "region_review": str(run_dir / "region_review.csv"),
            },
            "operation": "validate_concat_sort_write_reload_only",
            "geometry_mutation": False,
        }
        validation_report = {
            "candidate_id": safe_candidate_id,
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
        }
        assembly_summary = {
            "candidate_id": safe_candidate_id,
            "simplification_run_id": simplification_run_id,
            "region_count": len(expected_regions),
            "row_count": len(merged),
            "approval_mode": approval_mode,
            "manual_review_file_supplied": review_file_supplied,
            "review_items": review_items,
            "candidate_path": str(final_dir / "wine_regions.geojson"),
            "passed": True,
        }
        write_json(temp_dir / "manifest.json", manifest)
        write_json(temp_dir / "provenance.json", provenance)
        progress(f"installing durable candidate: {final_dir}")
        _install_run_directory(temp_dir, final_dir, overwrite=overwrite)

        review_report_path = report_root / f"{safe_candidate_id}_region_review.csv"
        summary_path = report_root / f"{safe_candidate_id}_assembly_summary.json"
        validation_path = validation_root / f"{safe_candidate_id}.validation.json"
        provenance_path = provenance_root / f"{safe_candidate_id}.provenance.json"
        _write_report_csv(review_report_path, review_rows, review_fieldnames)
        write_json(summary_path, assembly_summary)
        write_json(validation_path, validation_report)
        write_json(provenance_path, provenance)
        return AssemblyResult(
            candidate_id=safe_candidate_id,
            candidate_dir=final_dir,
            candidate_path=final_dir / "wine_regions.geojson",
            manifest_path=final_dir / "manifest.json",
            provenance_path=provenance_path,
            validation_path=validation_path,
            review_report_path=review_report_path,
            summary_path=summary_path,
            rows=len(merged),
            passed=True,
        )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
