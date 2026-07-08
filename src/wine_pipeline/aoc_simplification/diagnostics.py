"""Lightweight Stage 2 transform and serialization diagnostics."""

from __future__ import annotations

from collections.abc import Callable
import csv
from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
import uuid

import geopandas as gpd

from ..provenance import sha256_file
from .batch import discover_regions
from .runner import (
    _candidate_geometry_diagnostics,
    _validate_candidate_round_trip,
    find_project_root,
    resolve_stage1_input,
    utc_now,
)
from .serialization import (
    POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2,
    POST_REPROJECTION_NEGLIGIBLE_ABSOLUTE_M2,
    POST_REPROJECTION_NEGLIGIBLE_RELATIVE,
    POST_REPROJECTION_RELATIVE_TOLERANCE,
    SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2,
    SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE,
    SerializationCleanupError,
    cleanup_final_geometries,
)
from .transform import SimplificationParameters, select_region, simplify_region, slugify_region


REPORT_COLUMNS = [
    "region",
    "status",
    "failed_app",
    "failure_stage",
    "original_geometry_type",
    "repaired_geometry_type",
    "validity_reason",
    "original_polygon_component_count",
    "retained_component_count",
    "rejected_component_count",
    "rejected_area_m2",
    "rejected_area_fraction_of_source",
    "absolute_tolerance_m2",
    "relative_tolerance",
    "post_reprojection_absolute_tolerance_m2",
    "post_reprojection_relative_tolerance",
    "post_reprojection_negligible_absolute_threshold_m2",
    "post_reprojection_negligible_relative_threshold",
    "whole_appellation_empty",
    "fully_covered_appellations",
    "elapsed_seconds",
    "error",
]


@dataclass(frozen=True)
class DiagnosticResult:
    run_id: str
    run_dir: Path
    json_path: Path
    csv_path: Path
    passed_regions: list[str]
    failed_regions: dict[str, str]

    @property
    def passed(self) -> bool:
        return not self.failed_regions


def _base_row(region: str) -> dict[str, object]:
    return {
        "region": region,
        "status": "passed",
        "failed_app": "",
        "failure_stage": "",
        "original_geometry_type": "",
        "repaired_geometry_type": "",
        "validity_reason": "",
        "original_polygon_component_count": 0,
        "retained_component_count": 0,
        "rejected_component_count": 0,
        "rejected_area_m2": 0.0,
        "rejected_area_fraction_of_source": 0.0,
        "absolute_tolerance_m2": SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2,
        "relative_tolerance": SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE,
        "post_reprojection_absolute_tolerance_m2": POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2,
        "post_reprojection_relative_tolerance": POST_REPROJECTION_RELATIVE_TOLERANCE,
        "post_reprojection_negligible_absolute_threshold_m2": POST_REPROJECTION_NEGLIGIBLE_ABSOLUTE_M2,
        "post_reprojection_negligible_relative_threshold": POST_REPROJECTION_NEGLIGIBLE_RELATIVE,
        "whole_appellation_empty": False,
        "fully_covered_appellations": [],
        "elapsed_seconds": 0.0,
        "error": "",
    }


def _round_trip(frame: gpd.GeoDataFrame, path: Path) -> None:
    frame.to_file(path, driver="GeoJSON", engine="pyogrio", index=False)
    reloaded = gpd.read_file(path, engine="pyogrio")
    _validate_candidate_round_trip(reloaded, context="Diagnostic GeoJSON")


def run_diagnostics(
    *,
    input_path: Path | None = None,
    diagnostic_run_id: str | None = None,
    output_root: Path | None = None,
    region: str | None = None,
    parameters: SimplificationParameters | None = None,
    progress: Callable[[str], None] | None = None,
) -> DiagnosticResult:
    parameters = parameters or SimplificationParameters()
    progress = progress or (lambda message: None)
    started_at = utc_now()
    project_root = find_project_root()
    source_path = resolve_stage1_input(input_path, project_root=project_root)
    source = gpd.read_file(source_path, layer="aocs_france")
    available = discover_regions(source)
    if region is not None:
        if region not in available:
            raise ValueError(f"Unknown region {region!r}; available regions: {', '.join(available)}")
        regions = [region]
    else:
        regions = available

    raw_run_id = diagnostic_run_id or f"{utc_now().replace(':', '').replace('+00:00', 'Z')}_{uuid.uuid4().hex[:8]}"
    run_id = slugify_region(raw_run_id)
    if not run_id:
        raise ValueError("Diagnostic run ID must contain at least one ASCII letter or number.")
    root = (output_root or project_root / "tmp" / "wine" / "simplification" / "diagnostics").resolve()
    run_dir = (root / run_id).resolve()
    if root != run_dir and root not in run_dir.parents:
        raise ValueError(f"Diagnostic run directory resolved outside output root: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)

    rows = []
    for current_region in regions:
        started = perf_counter()
        row = _base_row(current_region)
        stage = "transform"
        round_trip_path = run_dir / f".{slugify_region(current_region)}.roundtrip.geojson"
        progress(f"diagnosing {current_region}")
        try:
            selected = select_region(source, current_region)
            stages = simplify_region(selected, parameters=parameters)
            row["fully_covered_appellations"] = (
                stages.partition_report.fully_covered_app_names if stages.partition_report else []
            )
            stage = "serialization_cleanup"
            candidate, cleanup = cleanup_final_geometries(stages.final)
            row["original_polygon_component_count"] = sum(
                int(item["original_polygon_component_count"]) for item in cleanup
            )
            row["retained_component_count"] = sum(
                int(item["retained_component_count"]) for item in cleanup
            )
            row["rejected_component_count"] = sum(
                int(item["rejected_component_count"]) for item in cleanup
            )
            row["rejected_area_m2"] = sum(float(item["rejected_area_m2"]) for item in cleanup)
            row["rejected_area_fraction_of_source"] = max(
                (float(item["rejected_area_fraction_of_source"]) for item in cleanup),
                default=0.0,
            )
            stage = "geojson_round_trip"
            _round_trip(candidate, round_trip_path)
        except SerializationCleanupError as error:
            row.update(error.diagnostic)
            row["status"] = "failed"
            row["failed_app"] = row.pop("app", "")
            row["failure_stage"] = stage
            row["error"] = str(error)
        except Exception as error:
            row["status"] = "failed"
            row["failure_stage"] = stage
            row["error"] = str(error)
            if stage == "geojson_round_trip" and round_trip_path.is_file():
                try:
                    defects = _candidate_geometry_diagnostics(
                        gpd.read_file(round_trip_path, engine="pyogrio")
                    )
                    if defects:
                        row["failed_app"] = defects[0].get("app", "")
                        row["validity_reason"] = defects[0].get("validity_reason", "")
                except Exception:
                    pass
        finally:
            if round_trip_path.exists():
                round_trip_path.unlink()
            row["elapsed_seconds"] = round(perf_counter() - started, 6)
            rows.append(row)

    rows.sort(key=lambda item: str(item["region"]))
    passed_regions = [str(item["region"]) for item in rows if item["status"] == "passed"]
    failed_regions = {
        str(item["region"]): str(item["error"])
        for item in rows
        if item["status"] == "failed"
    }
    payload = {
        "diagnostic_run_id": run_id,
        "stage1_source_path": str(source_path),
        "stage1_source_sha256": sha256_file(source_path),
        "parameters": parameters.as_dict(),
        "started_at_utc": started_at,
        "completed_at_utc": utc_now(),
        "region_inventory": regions,
        "passed_region_count": len(passed_regions),
        "failed_region_count": len(failed_regions),
        "passed": not failed_regions,
        "regions": rows,
    }
    json_path = run_dir / "report.json"
    csv_path = run_dir / "report.csv"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                **row,
                "fully_covered_appellations": ";".join(row["fully_covered_appellations"]),
            })
    return DiagnosticResult(
        run_id=run_id,
        run_dir=run_dir,
        json_path=json_path,
        csv_path=csv_path,
        passed_regions=passed_regions,
        failed_regions=failed_regions,
    )
