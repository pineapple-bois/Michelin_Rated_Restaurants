"""Single-region runner for Stage 2 wine AOC simplification."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid

import geopandas as gpd
from shapely.validation import explain_validity

from .. import __version__
from ..provenance import sha256_file
from ..validation import WinePipelineError
from .transform import (
    CANONICAL_RUN_ID,
    OUTPUT_COLUMNS,
    OUTPUT_CRS,
    SimplificationParameters,
    metrics_for_frame,
    select_region,
    simplify_region,
    slugify_region,
)


@dataclass(frozen=True)
class SimplificationRunResult:
    region: str
    run_id: str
    run_dir: Path
    candidate_path: Path
    metrics_path: Path
    params_path: Path
    preview_path: Path
    comparison_path: Path
    overlap_comparison_path: Path
    rows: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_project_root(start: Path | None = None) -> Path:
    start = start or Path.cwd()
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "wine_pipeline").is_dir():
            return candidate
    package_root = Path(__file__).resolve().parents[3]
    if (package_root / "pyproject.toml").is_file():
        return package_root
    raise FileNotFoundError(f"Could not locate repository root from {start}.")


def resolve_stage1_input(path: Path | None, *, project_root: Path) -> Path:
    if path is not None:
        resolved = path if path.is_absolute() else project_root / path
        if not resolved.is_file():
            raise FileNotFoundError(f"Stage 1 input does not exist: {resolved}")
        return resolved
    candidates = sorted((project_root / "tmp" / "wine").glob("*/candidates/aoc_regions.gpkg"))
    if not candidates:
        raise FileNotFoundError("No Stage 1 aoc_regions.gpkg found under tmp/wine/*/candidates/.")
    return candidates[-1]


def infer_stage1_run_id(path: Path, *, project_root: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) >= 4 and parts[0] == "tmp" and parts[1] == "wine" and parts[3] == "candidates":
        return parts[2]
    return None


def _git_text(project_root: Path, args: list[str]) -> str | None:
    result = subprocess.run(["git", *args], cwd=project_root, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def git_state(project_root: Path) -> dict[str, object]:
    status = _git_text(project_root, ["status", "--short"])
    return {
        "commit": _git_text(project_root, ["rev-parse", "HEAD"]),
        "status_short": status or "",
        "dirty": bool(status),
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _assert_child_path(path: Path, root: Path, *, label: str) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError(f"{label} resolved outside the configured output root: {path}")
    return resolved_path


def _expected_artifacts(run_dir: Path) -> list[Path]:
    return [
        run_dir / "candidate.geojson",
        run_dir / "metrics.json",
        run_dir / "params.json",
        run_dir / "preview.png",
        run_dir / "comparison.png",
        run_dir / "overlap_comparison.png",
    ]


def _candidate_geometry_diagnostics(frame: gpd.GeoDataFrame) -> list[dict[str, object]]:
    diagnostics = []
    for index, row in frame.iterrows():
        geometry = row.geometry
        is_null = geometry is None
        is_empty = False if is_null else bool(geometry.is_empty)
        is_valid = False if is_null else bool(geometry.is_valid)
        if not is_null and not is_empty and is_valid:
            continue
        diagnostics.append(
            {
                "row_index": int(index) if isinstance(index, int) else str(index),
                "app": row.get("app"),
                "geometry_type": None if is_null else geometry.geom_type,
                "geometry_is_null": is_null,
                "geometry_is_empty": is_empty,
                "validity_reason": "Null geometry" if is_null else explain_validity(geometry),
            }
        )
    return diagnostics


def _format_candidate_geometry_diagnostics(diagnostics: list[dict[str, object]]) -> str:
    parts = []
    for item in diagnostics:
        parts.append(
            "row {row_index}: app={app!r}, geometry_type={geometry_type!r}, "
            "geometry_is_null={geometry_is_null}, geometry_is_empty={geometry_is_empty}, "
            "validity_reason={validity_reason!r}".format(**item)
        )
    return "; ".join(parts)


def _validate_candidate_round_trip(frame: gpd.GeoDataFrame, *, context: str) -> None:
    diagnostics = _candidate_geometry_diagnostics(frame)
    if diagnostics:
        raise ValueError(f"{context} failed geometry round-trip validation: {_format_candidate_geometry_diagnostics(diagnostics)}")


def _validate_artifact_set(run_dir: Path) -> None:
    missing = [path.name for path in _expected_artifacts(run_dir) if not path.is_file()]
    if missing:
        raise ValueError("Regional run artifact set is incomplete: " + ", ".join(missing))
    candidate = gpd.read_file(run_dir / "candidate.geojson", engine="pyogrio")
    if list(candidate.columns) != OUTPUT_COLUMNS:
        raise ValueError(f"Candidate schema changed after write: {list(candidate.columns)}")
    if candidate.crs is None or candidate.crs.to_epsg() != 4326:
        raise ValueError(f"Candidate CRS changed after write: {candidate.crs}")
    _validate_candidate_round_trip(candidate, context="Candidate GeoJSON")
    for json_name in ("metrics.json", "params.json"):
        json.loads((run_dir / json_name).read_text(encoding="utf-8"))


def _install_run_directory(temp_dir: Path, final_dir: Path, *, overwrite: bool) -> None:
    if final_dir.exists() and not overwrite:
        raise FileExistsError(f"Run directory already exists: {final_dir}; pass --overwrite to replace it.")
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if not final_dir.exists():
        temp_dir.replace(final_dir)
        return

    backup_dir = final_dir.with_name(f".{final_dir.name}.backup-{uuid.uuid4().hex}")
    final_dir.replace(backup_dir)
    try:
        temp_dir.replace(final_dir)
    except Exception:
        if final_dir.exists():
            shutil.rmtree(final_dir)
        backup_dir.replace(final_dir)
        raise
    shutil.rmtree(backup_dir)


def _plot_frame(ax, frame: gpd.GeoDataFrame, *, absent_message: str = "No geometry", fill_color: str | None = None) -> None:
    ax.set_facecolor("white")
    if frame.empty:
        ax.text(0.5, 0.5, absent_message, ha="center", va="center", transform=ax.transAxes)
    else:
        plot_frame = frame.to_crs(OUTPUT_CRS)
        kwargs = {"ax": ax, "edgecolor": "#303030", "linewidth": 0.3}
        if fill_color is not None:
            kwargs["color"] = fill_color
        elif "colour" in plot_frame.columns:
            kwargs["color"] = plot_frame["colour"].fillna("#8a6f96")
        else:
            kwargs["color"] = "#8a6f96"
        plot_frame.plot(**kwargs)
    ax.set_aspect("equal")
    ax.set_axis_off()


def _shared_bounds(frames: list[gpd.GeoDataFrame]) -> tuple[float, float, float, float] | None:
    bounds = []
    for frame in frames:
        if frame.empty:
            continue
        values = frame.to_crs(OUTPUT_CRS).total_bounds
        if all(math.isfinite(float(value)) for value in values):
            bounds.append(values)
    if not bounds:
        return None
    minx = min(float(item[0]) for item in bounds)
    miny = min(float(item[1]) for item in bounds)
    maxx = max(float(item[2]) for item in bounds)
    maxy = max(float(item[3]) for item in bounds)
    padding_x = max((maxx - minx) * 0.05, 0.01)
    padding_y = max((maxy - miny) * 0.05, 0.01)
    return minx - padding_x, miny - padding_y, maxx + padding_x, maxy + padding_y


def write_plots(
    *,
    region: str,
    run_id: str,
    raw: gpd.GeoDataFrame,
    simplified: gpd.GeoDataFrame,
    partitioned: gpd.GeoDataFrame,
    removed_overlap: gpd.GeoDataFrame,
    final: gpd.GeoDataFrame,
    preview_path: Path,
    comparison_path: Path,
    overlap_comparison_path: Path,
) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/wine_matplotlib")
    os.environ.setdefault("XDG_CACHE_HOME", "/tmp/wine_cache")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white", "savefig.dpi": 180})

    figure, axis = plt.subplots(figsize=(14, 9))
    _plot_frame(axis, final)
    axis.set_title(f"{region}: {run_id}")
    figure.tight_layout()
    figure.savefig(preview_path, bbox_inches="tight")
    plt.close(figure)

    figure, axes = plt.subplots(1, 3, figsize=(24, 8))
    panels = ((raw, "Stage 1 regional input"), (simplified, "Simplified before overlap policy"), (final, "Simplified candidate"))
    shared_bounds = _shared_bounds([raw, simplified, final])
    for axis, (frame, title) in zip(axes, panels):
        _plot_frame(axis, frame)
        axis.set_title(title)
        if shared_bounds is not None:
            axis.set_xlim(shared_bounds[0], shared_bounds[2])
            axis.set_ylim(shared_bounds[1], shared_bounds[3])
    figure.suptitle(f"{region}: {run_id}")
    figure.tight_layout()
    figure.savefig(comparison_path, bbox_inches="tight")
    plt.close(figure)

    figure, axes = plt.subplots(1, 3, figsize=(24, 8))
    panels = (
        (simplified, "Simplified before partition", None),
        (partitioned, "Smallest-wins partition", None),
        (removed_overlap, "Overlap removed from broader AOCs", "#b64b4b"),
    )
    shared_bounds = _shared_bounds([simplified, partitioned, removed_overlap])
    for axis, (frame, title, fill) in zip(axes, panels):
        _plot_frame(axis, frame, absent_message="No overlap removed", fill_color=fill)
        axis.set_title(title)
        if shared_bounds is not None:
            axis.set_xlim(shared_bounds[0], shared_bounds[2])
            axis.set_ylim(shared_bounds[1], shared_bounds[3])
    figure.suptitle(f"{region}: {run_id} overlap policy")
    figure.tight_layout()
    figure.savefig(overlap_comparison_path, bbox_inches="tight")
    plt.close(figure)


def run_single_region(
    *,
    region: str,
    input_path: Path | None = None,
    run_id: str = CANONICAL_RUN_ID,
    output_root: Path | None = None,
    parameters: SimplificationParameters | None = None,
    overwrite: bool = False,
    keep_failed_temp: bool = False,
    progress: Callable[[str], None] | None = None,
    command: list[str] | None = None,
) -> SimplificationRunResult:
    progress = progress or (lambda message: None)
    parameters = parameters or SimplificationParameters()
    project_root = find_project_root()
    source_path = resolve_stage1_input(input_path, project_root=project_root)
    stage1_run_id = infer_stage1_run_id(source_path, project_root=project_root)
    region_slug = slugify_region(region)
    if not region_slug:
        raise ValueError("Region name must contain at least one ASCII letter or number.")
    run_id = slugify_region(run_id)
    if not run_id:
        raise ValueError("Run ID must contain at least one ASCII letter or number.")
    output_root = output_root or project_root / "tmp" / "wine" / "simplification"
    output_root = output_root.resolve()
    run_dir = _assert_child_path(output_root / run_id / "regions" / region_slug, output_root, label="Regional run directory")
    if run_dir.exists() and not overwrite:
        raise FileExistsError(f"Run directory already exists: {run_dir}; pass --overwrite to replace it.")
    temp_dir = _assert_child_path(
        run_dir.parent / f".{region_slug}.tmp-{uuid.uuid4().hex}",
        output_root,
        label="Temporary regional run directory",
    )
    temp_dir.mkdir(parents=True, exist_ok=False)

    try:
        progress(f"reading Stage 1 input: {source_path}")
        source = gpd.read_file(source_path, layer="aocs_france")
        selected = select_region(source, region)
        progress(f"selected {len(selected)} Stage 1 rows for {region}")
        stages = simplify_region(selected, parameters=parameters)
        if stages.partition_report and stages.partition_report.fully_covered_app_names:
            progress("fully covered appellations: " + ", ".join(stages.partition_report.fully_covered_app_names))

        candidate_path = temp_dir / "candidate.geojson"
        metrics_path = temp_dir / "metrics.json"
        params_path = temp_dir / "params.json"
        preview_path = temp_dir / "preview.png"
        comparison_path = temp_dir / "comparison.png"
        overlap_comparison_path = temp_dir / "overlap_comparison.png"

        progress(f"writing regional candidate: {candidate_path}")
        stages.final[OUTPUT_COLUMNS].to_file(candidate_path, driver="GeoJSON", engine="pyogrio", index=False)
        reloaded = gpd.read_file(candidate_path, engine="pyogrio")
        if list(reloaded.columns) != OUTPUT_COLUMNS:
            raise ValueError(f"Candidate schema changed after write: {list(reloaded.columns)}")
        if reloaded.crs is None or reloaded.crs.to_epsg() != 4326:
            raise ValueError(f"Candidate CRS changed after write: {reloaded.crs}")
        _validate_candidate_round_trip(reloaded, context="Candidate GeoJSON")

        progress("writing visual inspection outputs")
        write_plots(
            region=region,
            run_id=run_id,
            raw=stages.raw,
            simplified=stages.simplified,
            partitioned=stages.partitioned,
            removed_overlap=stages.removed_overlap,
            final=stages.final,
            preview_path=preview_path,
            comparison_path=comparison_path,
            overlap_comparison_path=overlap_comparison_path,
        )

        partition = stages.partition_report.as_dict() if stages.partition_report else None
        final_metrics = metrics_for_frame(stages.final)
        metrics = {
            "region": region,
            "region_slug": region_slug,
            "run_id": run_id,
            "canonical_parameter_set": parameters.canonical,
            "parameters": parameters.as_dict(),
            "stages": {
                "stage1_regional_input": metrics_for_frame(stages.raw),
                "repaired_source_geometry": metrics_for_frame(stages.repaired),
                "dissolved_by_identity": metrics_for_frame(stages.dissolved),
                "morphologically_closed": metrics_for_frame(stages.closed),
                "simplified_pre_partition": metrics_for_frame(stages.simplified),
                "partitioned": metrics_for_frame(stages.partitioned),
                "removed_overlap": metrics_for_frame(stages.removed_overlap),
                "final_candidate": final_metrics,
            },
            "overlap": {
                "before_partition": stages.overlap_before.as_dict(),
                "after_partition": stages.overlap_after.as_dict(),
                "overlap_tolerance_m2": stages.overlap_tolerance_m2,
                "residual_overlap_within_tolerance": stages.overlap_after.overlap_area_m2 <= stages.overlap_tolerance_m2,
            },
            "partition": partition,
            "fully_covered_app_warnings": stages.partition_report.fully_covered_app_names if stages.partition_report else [],
            "candidate_file_size_mb": round(candidate_path.stat().st_size / (1024 * 1024), 6),
            "final_validation": {
                "schema": list(reloaded.columns),
                "crs": reloaded.crs.to_string(),
                "row_count": len(reloaded),
                "invalid_geometry_count": int((~reloaded.geometry.is_valid).sum()),
                "empty_geometry_count": int(reloaded.geometry.is_empty.sum() + reloaded.geometry.isna().sum()),
                "passed": True,
            },
        }
        params = {
            "region": region,
            "region_slug": region_slug,
            "run_id": run_id,
            "stage1_run_id": stage1_run_id,
            "stage1_source_path": str(source_path),
            "stage1_source_sha256": sha256_file(source_path),
            "output_crs": OUTPUT_CRS,
            "package_version": __version__,
            "git_state": git_state(project_root),
            "generated_at_utc": utc_now(),
            "effective_parameters": parameters.as_dict(),
            "command": shlex.join(command or sys.argv),
        }
        _write_json(metrics_path, metrics)
        _write_json(params_path, params)
        _validate_artifact_set(temp_dir)
        _install_run_directory(temp_dir, run_dir, overwrite=overwrite)
    except Exception as error:
        if temp_dir.exists():
            if keep_failed_temp:
                message = f"retained failed temporary regional run directory: {temp_dir}"
                progress(message)
                raise WinePipelineError(f"{error}; retained failed temporary directory: {temp_dir}") from error
            shutil.rmtree(temp_dir)
        raise
    candidate_path = run_dir / "candidate.geojson"
    metrics_path = run_dir / "metrics.json"
    params_path = run_dir / "params.json"
    preview_path = run_dir / "preview.png"
    comparison_path = run_dir / "comparison.png"
    overlap_comparison_path = run_dir / "overlap_comparison.png"
    reloaded = gpd.read_file(candidate_path, engine="pyogrio")
    progress(f"completed regional simplification: {run_dir}")
    return SimplificationRunResult(
        region=region,
        run_id=run_id,
        run_dir=run_dir,
        candidate_path=candidate_path,
        metrics_path=metrics_path,
        params_path=params_path,
        preview_path=preview_path,
        comparison_path=comparison_path,
        overlap_comparison_path=overlap_comparison_path,
        rows=len(reloaded),
    )
