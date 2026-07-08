"""Stage 3 validation and byte-preserving wine product promotion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
import json
from numbers import Real
from pathlib import Path
import platform
import shlex
import shutil
import sys
import uuid

import geopandas as gpd
from shapely.geometry import shape

from . import __version__
from .aoc_simplification.runner import _assert_child_path, _install_run_directory, find_project_root, git_state
from .aoc_simplification.transform import OUTPUT_COLUMNS, OUTPUT_IDENTITY_COLUMNS
from .provenance import sha256_file, utc_now, write_json
from .validation import WinePipelineError


PRODUCT_FILENAME = "wine_regions_aoc_area.geojson"
PRODUCT_TYPE = "wine_regions_aoc_area"


@dataclass(frozen=True)
class ProductResult:
    candidate_id: str
    release_date: str
    release_dir: Path
    product_path: Path
    manifest_path: Path
    validation_path: Path
    provenance_path: Path
    feature_count: int
    passed: bool


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_validation_path(project_root: Path, candidate_id: str, validation_root: Path | None) -> Path:
    root = validation_root or project_root / "data" / "wine" / "validation"
    return root / f"{candidate_id}.validation.json"


def resolve_candidate_id(
    candidate_id: str | None,
    *,
    project_root: Path,
    candidate_root: Path | None = None,
    candidate_validation_root: Path | None = None,
) -> str:
    root = (candidate_root or project_root / "data" / "candidates" / "wine").resolve()
    if candidate_id is not None:
        candidate_dir = _assert_child_path(root / candidate_id, root, label="Candidate directory")
        if not candidate_dir.is_dir():
            raise FileNotFoundError(f"Wine candidate directory not found: {candidate_dir}")
        return candidate_id

    eligible: list[str] = []
    if root.is_dir():
        for candidate_dir in sorted(root.iterdir(), key=lambda path: path.name):
            if not candidate_dir.is_dir() or candidate_dir.name.startswith("."):
                continue
            required = (
                candidate_dir / "wine_regions.geojson",
                candidate_dir / "manifest.json",
                candidate_dir / "provenance.json",
            )
            if not all(path.is_file() for path in required):
                continue
            validation_path = _candidate_validation_path(
                project_root,
                candidate_dir.name,
                candidate_validation_root,
            )
            try:
                validation = _read_json(validation_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if validation.get("passed") is True:
                eligible.append(candidate_dir.name)

    if not eligible:
        raise FileNotFoundError(
            "No complete, validated wine candidate was found. Run "
            "'python -m wine_pipeline assemble-candidate' or provide --candidate-id <candidate-id>."
        )
    if len(eligible) > 1:
        choices = "\n".join(f"  - {item}" for item in eligible)
        raise WinePipelineError(
            "Multiple validated wine candidates were found:\n"
            f"{choices}\nProvide --candidate-id <candidate-id> to select one."
        )
    return eligible[0]


def _release_date(value: str | None) -> str:
    if value is None:
        return date.today().isoformat()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("--release-date must use YYYY-MM-DD.") from error
    if parsed.isoformat() != value:
        raise ValueError("--release-date must use YYYY-MM-DD.")
    return value


def _check(
    checks: list[dict[str, object]],
    *,
    phase: str,
    name: str,
    passed: bool,
    observed: object,
    expected: object,
    message: str,
) -> None:
    checks.append(
        {
            "phase": phase,
            "name": name,
            "passed": bool(passed),
            "observed": observed,
            "expected": expected,
            "message": "" if passed else message,
        }
    )


def _raise_if_failed(checks: list[dict[str, object]]) -> None:
    failed = [check for check in checks if not check["passed"]]
    if failed:
        details = "; ".join(f"{check['phase']}.{check['name']}: {check['message']}" for check in failed[:8])
        raise WinePipelineError(f"Wine product validation failed: {details}")


def _schema_from_geojson(payload: dict[str, object]) -> list[str]:
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        return []
    first = features[0]
    if not isinstance(first, dict) or not isinstance(first.get("properties"), dict):
        return []
    return [*first["properties"].keys(), "geometry"]


def _feature_order(payload: dict[str, object]) -> list[list[object]]:
    order: list[list[object]] = []
    for feature in payload.get("features") or []:
        properties = feature.get("properties") or {}
        order.append([properties.get(column) for column in OUTPUT_IDENTITY_COLUMNS])
    return order


def _validate_geojson(
    payload: dict[str, object],
    *,
    path: Path,
    phase: str,
    checks: list[dict[str, object]],
) -> dict[str, object]:
    is_collection = payload.get("type") == "FeatureCollection" and isinstance(payload.get("features"), list)
    _check(
        checks,
        phase=phase,
        name="feature_collection",
        passed=is_collection,
        observed=payload.get("type"),
        expected="FeatureCollection",
        message="GeoJSON root must be a FeatureCollection.",
    )
    features = payload.get("features") if is_collection else []
    schema = _schema_from_geojson(payload)
    _check(
        checks,
        phase=phase,
        name="exact_ordered_schema",
        passed=schema == OUTPUT_COLUMNS,
        observed=schema,
        expected=OUTPUT_COLUMNS,
        message="GeoJSON schema or property ordering does not match the product contract.",
    )

    missing_properties: list[dict[str, object]] = []
    schema_mismatches: list[dict[str, object]] = []
    blank_identity: list[dict[str, object]] = []
    invalid_areas: list[dict[str, object]] = []
    null_or_empty: list[dict[str, object]] = []
    invalid_types: list[dict[str, object]] = []
    invalid_geometry: list[dict[str, object]] = []
    identities: list[tuple[object, ...]] = []
    geometry_types: set[str] = set()
    regions: set[str] = set()
    required_properties = OUTPUT_COLUMNS[:-1]

    for index, feature in enumerate(features):
        properties = feature.get("properties") if isinstance(feature, dict) else None
        properties = properties if isinstance(properties, dict) else {}
        missing = [column for column in required_properties if column not in properties]
        if missing:
            missing_properties.append({"feature": index, "missing": missing})
        if list(properties) != required_properties:
            schema_mismatches.append({"feature": index, "properties": list(properties)})
        for column in ("region", "app", "display_name"):
            value = properties.get(column)
            if not isinstance(value, str) or not value.strip():
                blank_identity.append({"feature": index, "column": column, "value": value})
        area = properties.get("source_area_m2")
        if isinstance(area, bool) or not isinstance(area, Real) or area < 0:
            invalid_areas.append({"feature": index, "value": area})
        geometry_payload = feature.get("geometry") if isinstance(feature, dict) else None
        if geometry_payload is None:
            null_or_empty.append({"feature": index, "reason": "null"})
        else:
            try:
                geometry = shape(geometry_payload)
                if geometry.is_empty:
                    null_or_empty.append({"feature": index, "reason": "empty"})
                geometry_types.add(geometry.geom_type)
                if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                    invalid_types.append({"feature": index, "type": geometry.geom_type})
                if not geometry.is_valid:
                    invalid_geometry.append({"feature": index, "type": geometry.geom_type})
            except (TypeError, ValueError) as error:
                invalid_geometry.append({"feature": index, "error": str(error)})
        identities.append(tuple(properties.get(column) for column in OUTPUT_IDENTITY_COLUMNS))
        region = properties.get("region")
        if isinstance(region, str) and region.strip():
            regions.add(region)

    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1}, key=str)
    validations = (
        ("all_required_properties", not missing_properties, missing_properties, [], "Features are missing required properties."),
        ("all_feature_schemas_exact", not schema_mismatches, schema_mismatches, [], "Every feature must use the exact ordered property schema."),
        ("non_empty_identity_strings", not blank_identity, blank_identity, [], "region, app, and display_name must be non-empty strings."),
        ("valid_source_area_m2", not invalid_areas, invalid_areas, [], "source_area_m2 must be numeric and non-negative."),
        ("no_null_or_empty_geometry", not null_or_empty, null_or_empty, [], "Geometry must be non-null and non-empty."),
        ("polygon_only", not invalid_types, invalid_types, [], "Geometry must be Polygon or MultiPolygon."),
        ("valid_geometry", not invalid_geometry, invalid_geometry, [], "All geometry must be valid."),
        ("no_duplicate_product_identities", not duplicates, duplicates, [], "Duplicate product identities were found."),
    )
    for name, passed, observed, expected, message in validations:
        _check(checks, phase=phase, name=name, passed=passed, observed=observed, expected=expected, message=message)

    crs = None
    try:
        frame = gpd.read_file(path, engine="pyogrio")
        crs = frame.crs.to_epsg() if frame.crs is not None else None
    except Exception as error:
        crs = f"unreadable: {error}"
    _check(
        checks,
        phase=phase,
        name="epsg_4326",
        passed=crs == 4326,
        observed=crs,
        expected=4326,
        message="GeoJSON must use the EPSG:4326 coordinate reference convention.",
    )
    return {
        "feature_count": len(features),
        "schema": schema,
        "feature_order": _feature_order(payload),
        "geometry_types": sorted(geometry_types),
        "regions": sorted(regions),
        "crs": "EPSG:4326" if crs == 4326 else crs,
    }


def _relative_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path.resolve())


def publish_product(
    *,
    candidate_id: str | None = None,
    release_date: str | None = None,
    overwrite: bool = False,
    candidate_root: Path | None = None,
    candidate_validation_root: Path | None = None,
    product_root: Path | None = None,
    project_root: Path | None = None,
    progress: Callable[[str], None] | None = None,
    command: list[str] | None = None,
) -> ProductResult:
    project_root = project_root or find_project_root()
    progress = progress or (lambda message: None)
    candidate_root = (candidate_root or project_root / "data" / "candidates" / "wine").resolve()
    product_root = (product_root or project_root / "data" / "products" / "wine").resolve()
    candidate_id = resolve_candidate_id(
        candidate_id,
        project_root=project_root,
        candidate_root=candidate_root,
        candidate_validation_root=candidate_validation_root,
    )
    candidate_dir = _assert_child_path(candidate_root / candidate_id, candidate_root, label="Candidate directory")
    release_date = _release_date(release_date)
    release_dir = _assert_child_path(product_root / release_date, product_root, label="Product release directory")
    if release_dir.exists() and not overwrite:
        raise FileExistsError(f"Wine product release already exists: {release_dir}; pass --overwrite to replace it.")
    temp_dir = _assert_child_path(
        product_root / f".{release_date}.tmp-{uuid.uuid4().hex}",
        product_root,
        label="Temporary product release directory",
    )
    temp_dir.mkdir(parents=True, exist_ok=False)

    checks: list[dict[str, object]] = []
    started_at = utc_now()
    try:
        candidate_path = candidate_dir / "wine_regions.geojson"
        candidate_manifest_path = candidate_dir / "manifest.json"
        candidate_provenance_path = candidate_dir / "provenance.json"
        validation_path = _candidate_validation_path(project_root, candidate_id, candidate_validation_root)
        required_files = (candidate_path, candidate_manifest_path, candidate_provenance_path, validation_path)
        for path, name in zip(required_files, ("candidate_geojson", "candidate_manifest", "candidate_provenance", "candidate_validation")):
            _check(
                checks,
                phase="pre_write",
                name=f"{name}_exists",
                passed=path.is_file(),
                observed=str(path),
                expected="existing file",
                message=f"Required {name.replace('_', ' ')} is missing.",
            )
        _raise_if_failed(checks)

        candidate_manifest = _read_json(candidate_manifest_path)
        candidate_provenance = _read_json(candidate_provenance_path)
        candidate_validation = _read_json(validation_path)
        _check(
            checks,
            phase="pre_write",
            name="candidate_validation_passed",
            passed=candidate_validation.get("passed") is True,
            observed=candidate_validation.get("passed"),
            expected=True,
            message="Candidate validation did not pass.",
        )
        for name, observed in (
            ("manifest_candidate_id", candidate_manifest.get("candidate_id")),
            ("provenance_candidate_id", candidate_provenance.get("candidate_id")),
            ("validation_candidate_id", candidate_validation.get("candidate_id")),
        ):
            _check(
                checks,
                phase="pre_write",
                name=name,
                passed=observed == candidate_id,
                observed=observed,
                expected=candidate_id,
                message="Candidate evidence does not identify the selected candidate.",
            )
        candidate_hash = sha256_file(candidate_path)
        recorded_hash = candidate_manifest.get("candidate_sha256")
        _check(
            checks,
            phase="pre_write",
            name="candidate_manifest_hash",
            passed=recorded_hash == candidate_hash,
            observed=recorded_hash,
            expected=candidate_hash,
            message="Candidate GeoJSON hash does not agree with its manifest.",
        )
        payload = _read_json(candidate_path)
        source_info = _validate_geojson(payload, path=candidate_path, phase="pre_write", checks=checks)
        _raise_if_failed(checks)

        product_path = temp_dir / PRODUCT_FILENAME
        progress(f"copying validated candidate unchanged: {product_path}")
        shutil.copyfile(candidate_path, product_path)
        product_hash = sha256_file(product_path)
        _check(
            checks,
            phase="post_write",
            name="byte_identical_hash",
            passed=product_hash == candidate_hash,
            observed=product_hash,
            expected=candidate_hash,
            message="Published GeoJSON is not byte-identical to the candidate.",
        )
        product_payload = _read_json(product_path)
        product_info = _validate_geojson(product_payload, path=product_path, phase="post_write", checks=checks)
        comparisons = (
            ("feature_count_matches", product_info["feature_count"], source_info["feature_count"]),
            ("schema_matches", product_info["schema"], source_info["schema"]),
            ("feature_order_matches", product_info["feature_order"], source_info["feature_order"]),
        )
        for name, observed, expected in comparisons:
            _check(
                checks,
                phase="post_write",
                name=name,
                passed=observed == expected,
                observed=observed,
                expected=expected,
                message=f"Published product {name.replace('_', ' ')} failed.",
            )
        _raise_if_failed(checks)

        created_at = utc_now()
        candidate_relative_path = _relative_path(candidate_dir, project_root)
        product_relative_path = _relative_path(release_dir / PRODUCT_FILENAME, project_root)
        manifest = {
            "product_type": PRODUCT_TYPE,
            "release_date": release_date,
            "product_filename": PRODUCT_FILENAME,
            "product_relative_path": product_relative_path,
            "candidate_id": candidate_id,
            "candidate_relative_path": candidate_relative_path,
            "candidate_sha256": candidate_hash,
            "product_sha256": product_hash,
            "feature_count": product_info["feature_count"],
            "schema": product_info["schema"],
            "crs": product_info["crs"],
            "geometry_types": product_info["geometry_types"],
            "regions_represented": product_info["regions"],
            "created_at_utc": created_at,
            "pipeline_version": __version__,
            "git_commit": git_state(project_root).get("commit"),
            "validation_status": "passed",
            "geometry_transformation": False,
            "operation": "validation_and_byte_preserving_promotion",
        }
        provenance = {
            "product_type": PRODUCT_TYPE,
            "release_date": release_date,
            "stage1_run_id": candidate_provenance.get("stage1_run_id"),
            "stage1_source_path": candidate_provenance.get("stage1_source_path"),
            "stage1_source_sha256": candidate_provenance.get("stage1_source_sha256"),
            "simplification_run_id": candidate_provenance.get("simplification_run_id"),
            "candidate_id": candidate_id,
            "candidate_assembly_approval_mode": candidate_provenance.get("approval_mode"),
            "canonical_parameter_set": candidate_provenance.get("canonical_parameter_set"),
            "effective_simplification_parameters": candidate_provenance.get("effective_simplification_parameters"),
            "source_candidate_path": candidate_relative_path,
            "destination_product_path": product_relative_path,
            "candidate_sha256": candidate_hash,
            "product_sha256": product_hash,
            "lineage": ["build", "simplify", "assemble-candidate", "publish-product"],
            "geometry_transformation": False,
            "command": shlex.join(command or sys.argv),
            "started_at_utc": started_at,
            "completed_at_utc": created_at,
            "pipeline_version": __version__,
            "python_version": sys.version,
            "platform": platform.platform(),
            "git_state": git_state(project_root),
        }
        validation_report = {
            "product_type": PRODUCT_TYPE,
            "release_date": release_date,
            "candidate_id": candidate_id,
            "candidate_sha256": candidate_hash,
            "product_sha256": product_hash,
            "checks": checks,
            "passed": all(check["passed"] for check in checks),
        }
        write_json(temp_dir / "manifest.json", manifest)
        write_json(temp_dir / "provenance.json", provenance)
        write_json(temp_dir / "validation.json", validation_report)

        reloaded_manifest = _read_json(temp_dir / "manifest.json")
        reloaded_provenance = _read_json(temp_dir / "provenance.json")
        reloaded_validation = _read_json(temp_dir / "validation.json")
        metadata_consistent = (
            reloaded_manifest.get("candidate_id") == candidate_id
            and reloaded_manifest.get("product_sha256") == product_hash
            and reloaded_provenance.get("candidate_id") == candidate_id
            and reloaded_provenance.get("product_sha256") == product_hash
            and reloaded_validation.get("passed") is True
        )
        _check(
            checks,
            phase="post_write",
            name="release_metadata_consistent",
            passed=metadata_consistent,
            observed=metadata_consistent,
            expected=True,
            message="Release manifest, provenance, and validation metadata are inconsistent.",
        )
        _raise_if_failed(checks)
        validation_report["checks"] = checks
        validation_report["passed"] = True
        write_json(temp_dir / "validation.json", validation_report)

        progress(f"installing wine product release: {release_dir}")
        _install_run_directory(temp_dir, release_dir, overwrite=overwrite)
        return ProductResult(
            candidate_id=candidate_id,
            release_date=release_date,
            release_dir=release_dir,
            product_path=release_dir / PRODUCT_FILENAME,
            manifest_path=release_dir / "manifest.json",
            validation_path=release_dir / "validation.json",
            provenance_path=release_dir / "provenance.json",
            feature_count=int(product_info["feature_count"]),
            passed=True,
        )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
