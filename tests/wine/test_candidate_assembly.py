from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

import geopandas as gpd
from shapely.geometry import Polygon

from wine_pipeline.aoc_simplification.assembly import assemble_candidate
from wine_pipeline.aoc_simplification.batch import REVIEW_COLUMNS
from wine_pipeline.aoc_simplification.transform import CANONICAL_RUN_ID, OUTPUT_COLUMNS, SimplificationParameters, slugify_region
from wine_pipeline.provenance import sha256_file
from wine_pipeline.validation import WinePipelineError


SOURCE_HASH = "a" * 64


def square(x0: float, y0: float, size: float = 0.01) -> Polygon:
    return Polygon([(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)])


def feature(region: str, app: str, x0: float) -> dict[str, object]:
    return {
        "region": region,
        "app": app,
        "display_name": app,
        "colour": "#123456",
        "categorie": "AOP",
        "source_area_m2": 1000.0,
        "geometry": square(x0, 47.0),
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_review(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_review(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_candidate(path: Path, rows: list[dict[str, object]], crs: str = "EPSG:4326") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(rows, columns=OUTPUT_COLUMNS, geometry="geometry", crs=crs).to_file(path, driver="GeoJSON", index=False)


def make_batch_fixture(
    root: Path,
    *,
    run_id: str = CANONICAL_RUN_ID,
    regions: list[str] | None = None,
    review_status: str = "approved",
) -> Path:
    regions = regions or ["Beta", "Alpha"]
    run_dir = root / "simplification" / run_id
    params = SimplificationParameters().as_dict()
    rows_by_region = {
        "Alpha": [feature("Alpha", "Zulu", 4.0), feature("Alpha", "Alpha", 4.03)],
        "Beta": [feature("Beta", "Beta", 4.1)],
        "Gamma": [feature("Gamma", "Gamma", 4.2)],
    }
    total_rows = 0
    for region in regions:
        region_dir = run_dir / "regions" / slugify_region(region)
        candidate_rows = rows_by_region[region]
        write_candidate(region_dir / "candidate.geojson", candidate_rows)
        for name in ("preview.png", "comparison.png", "overlap_comparison.png"):
            (region_dir / name).write_bytes(name.encode("utf-8"))
        total_rows += len(candidate_rows)
        write_json(
            region_dir / "params.json",
            {
                "region": region,
                "region_slug": slugify_region(region),
                "run_id": run_id,
                "stage1_source_sha256": SOURCE_HASH,
                "effective_parameters": params,
            },
        )
        write_json(
            region_dir / "metrics.json",
            {
                "region": region,
                "region_slug": slugify_region(region),
                "run_id": run_id,
                "parameters": params,
                "overlap": {
                    "residual_overlap": {
                        "residual_overlap_area_m2": 0.0,
                        "union_area_m2": 1000.0,
                        "residual_overlap_ratio": 0.0,
                        "classification": "none",
                        "fatal": False,
                    }
                },
                "partition": {"fully_covered_app_names": []},
                "serialization_cleanup": {"diagnostics": []},
            },
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        run_dir / "run.json",
        {
            "batch_run_id": run_id,
            "stage1_run_id": "stage1-run",
            "stage1_source_path": "tmp/wine/stage1/candidates/aoc_regions.gpkg",
            "stage1_source_sha256": SOURCE_HASH,
            "canonical_parameter_set": True,
            "effective_parameters": params,
            "expected_region_inventory": regions,
            "passed": True,
        },
    )
    write_json(
        run_dir / "batch_summary.json",
        {
            "expected_region_count": len(regions),
            "completed_region_count": len(regions),
            "skipped_region_count": 0,
            "failed_region_count": 0,
            "failed_regions": {},
            "total_final_candidate_rows": total_rows,
            "fully_covered_appellations_by_region": {"Alpha": ["Covered"]},
            "near_total_area_reductions_by_region": {},
            "serialization_cleanup_by_region": {},
            "post_reprojection_review_repairs_by_region": {},
            "residual_overlap_by_region": {"Alpha": {"classification": "review"}},
            "passed": True,
        },
    )
    write_json(
        run_dir / "validation.json",
        {
            "passed": True,
            "checks": [{"name": "fixture", "passed": True}],
        },
    )
    review_rows = []
    for region in regions:
        row = {column: "" for column in REVIEW_COLUMNS}
        row.update(
            {
                "region": region,
                "region_slug": slugify_region(region),
                "status": "completed",
                "source_sha256": SOURCE_HASH,
                "review_status": review_status,
                "reviewer": "Ian",
                "reviewed_at": "2026-07-08T00:00:00+00:00",
                "geometry_assessment": "ok",
                "overlap_assessment": "ok",
                "fully_covered_assessment": "accepted",
                "notes": f"{region} approved",
            }
        )
        review_rows.append(row)
    write_review(run_dir / "region_review.csv", review_rows)
    return run_dir


class WineCandidateAssemblyTests(unittest.TestCase):
    def assemble(self, root: Path, **kwargs):
        return assemble_candidate(
            simplification_run_id=kwargs.pop("run_id", CANONICAL_RUN_ID),
            simplification_root=root / "simplification",
            candidate_root=root / "candidates",
            report_root=root / "reports",
            validation_root=root / "validation",
            provenance_root=root / "provenance",
            candidate_id=kwargs.pop("candidate_id", "candidate"),
            command=["wine_pipeline", "assemble-candidate"],
            **kwargs,
        )

    def test_successful_approved_region_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_batch_fixture(root)
            result = self.assemble(root)
            self.assertTrue(result.passed)
            self.assertTrue(result.candidate_path.is_file())
            merged = gpd.read_file(result.candidate_path, engine="pyogrio")
            self.assertEqual(merged.columns.tolist(), OUTPUT_COLUMNS)
            self.assertEqual(merged.crs.to_epsg(), 4326)
            self.assertEqual(merged[["region", "app"]].astype(str).values.tolist(), [["Alpha", "Alpha"], ["Alpha", "Zulu"], ["Beta", "Beta"]])
            manifest = read_json(result.manifest_path)
            self.assertEqual(manifest["simplification_run_id"], CANONICAL_RUN_ID)
            self.assertEqual(manifest["total_output_rows"], 3)
            self.assertEqual(manifest["approval_mode"], "automated_validated_batch")
            self.assertTrue(manifest["manual_review_file_supplied"])
            self.assertIn("fully_covered_appellations_by_region", manifest)

    def test_refuses_failed_batch_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            write_json(run_dir / "validation.json", {"passed": False, "checks": []})
            with self.assertRaisesRegex(WinePipelineError, "Batch validation"):
                self.assemble(root)

    def test_default_allows_blank_and_pending_review_states(self) -> None:
        for status in ("", "pending"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                make_batch_fixture(root, review_status=status)
                result = self.assemble(root)
                self.assertTrue(result.passed)

    def test_default_allows_missing_review_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            (run_dir / "region_review.csv").unlink()
            result = self.assemble(root)
            manifest = read_json(result.manifest_path)
            self.assertFalse(manifest["manual_review_file_supplied"])
            rows = read_review(result.review_report_path)
            self.assertEqual([row["region"] for row in rows], ["Beta", "Alpha"])
            self.assertTrue(all(row["review_status"] == "" for row in rows))

    def test_blocks_rejected_and_rerun_required_review_states(self) -> None:
        for status in ("rejected", "rerun_required"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                make_batch_fixture(root, review_status=status)
                with self.assertRaisesRegex(WinePipelineError, "rejected or require rerun"):
                    self.assemble(root)

    def test_strict_mode_requires_all_regions_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_batch_fixture(root, review_status="pending")
            with self.assertRaisesRegex(WinePipelineError, "not approved"):
                self.assemble(root, require_manual_approval=True)

    def test_strict_mode_fails_when_review_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            (run_dir / "region_review.csv").unlink()
            with self.assertRaisesRegex(WinePipelineError, "Strict manual approval requires region_review"):
                self.assemble(root, require_manual_approval=True)

    def test_strict_mode_records_manual_approval_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_batch_fixture(root)
            result = self.assemble(root, require_manual_approval=True)
            manifest = read_json(result.manifest_path)
            provenance = read_json(result.provenance_path)
            self.assertEqual(manifest["approval_mode"], "explicit_manual_approval")
            self.assertEqual(provenance["approval_mode"], "explicit_manual_approval")

    def test_blocks_fatal_residual_overlap_and_serialization_classifications(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root, review_status="")
            path = run_dir / "regions" / "alpha" / "metrics.json"
            metrics = read_json(path)
            metrics["overlap"]["residual_overlap"] = {"classification": "fatal", "fatal": True}
            write_json(path, metrics)
            with self.assertRaisesRegex(WinePipelineError, "fatal residual overlap"):
                self.assemble(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root, review_status="")
            path = run_dir / "regions" / "alpha" / "metrics.json"
            metrics = read_json(path)
            metrics["serialization_cleanup"] = {"post_reprojection_review_classification_counts": {"fatal": 1}, "diagnostics": []}
            write_json(path, metrics)
            with self.assertRaisesRegex(WinePipelineError, "fatal serialization"):
                self.assemble(root)

    def test_refuses_missing_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            for child in (run_dir / "regions" / "beta").iterdir():
                child.unlink()
            (run_dir / "regions" / "beta").rmdir()
            with self.assertRaisesRegex(WinePipelineError, "coverage mismatch|incomplete"):
                self.assemble(root)

    def test_refuses_mismatched_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            path = run_dir / "regions" / "alpha" / "params.json"
            payload = read_json(path)
            payload["stage1_source_sha256"] = "bad"
            write_json(path, payload)
            with self.assertRaisesRegex(WinePipelineError, "source hash"):
                self.assemble(root)

    def test_refuses_mismatched_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            path = run_dir / "regions" / "alpha" / "params.json"
            payload = read_json(path)
            payload["effective_parameters"]["buffer_m"] = 999
            write_json(path, payload)
            with self.assertRaisesRegex(WinePipelineError, "parameters"):
                self.assemble(root)

    def test_refuses_wrong_schema_or_crs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            bad = gpd.GeoDataFrame([{"region": "Alpha", "app": "Bad", "geometry": square(4, 47)}], geometry="geometry", crs="EPSG:4326")
            bad.to_file(run_dir / "regions" / "alpha" / "candidate.geojson", driver="GeoJSON", index=False)
            with self.assertRaisesRegex(WinePipelineError, "schema"):
                self.assemble(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            write_candidate(run_dir / "regions" / "alpha" / "candidate.geojson", [feature("Alpha", "Bad", 700000)], crs="EPSG:2154")
            with self.assertRaisesRegex(WinePipelineError, "CRS"):
                self.assemble(root)

    def test_refuses_invalid_or_empty_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            invalid = feature("Alpha", "Bad", 4.0)
            invalid["geometry"] = Polygon([(4, 47), (5, 48), (4, 48), (5, 47), (4, 47)])
            write_candidate(run_dir / "regions" / "alpha" / "candidate.geojson", [invalid])
            with self.assertRaisesRegex(WinePipelineError, "invalid geometry"):
                self.assemble(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            empty = feature("Alpha", "Empty", 4.0)
            empty["geometry"] = None
            write_candidate(run_dir / "regions" / "alpha" / "candidate.geojson", [empty])
            with self.assertRaisesRegex(WinePipelineError, "null geometry"):
                self.assemble(root)

    def test_refuses_duplicate_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            duplicate = [feature("Alpha", "Same", 4.0), feature("Alpha", "Same", 4.02)]
            write_candidate(run_dir / "regions" / "alpha" / "candidate.geojson", duplicate)
            summary = read_json(run_dir / "batch_summary.json")
            summary["total_final_candidate_rows"] = 3
            write_json(run_dir / "batch_summary.json", summary)
            with self.assertRaisesRegex(WinePipelineError, "duplicate"):
                self.assemble(root)

    def test_exact_row_count_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            summary = read_json(run_dir / "batch_summary.json")
            summary["total_final_candidate_rows"] = 999
            write_json(run_dir / "batch_summary.json", summary)
            with self.assertRaisesRegex(WinePipelineError, "row count"):
                self.assemble(root)

    def test_preserves_human_owned_review_fields_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_batch_fixture(root)
            result = self.assemble(root)
            rows = read_review(result.review_report_path)
            self.assertEqual(rows[0]["reviewer"], "Ian")
            self.assertIn("approved", rows[0]["notes"])
            self.assertTrue(result.summary_path.is_file())
            self.assertTrue(result.validation_path.is_file())
            self.assertTrue(result.provenance_path.is_file())

    def test_normal_mode_refuses_existing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_batch_fixture(root)
            self.assemble(root)
            with self.assertRaises(FileExistsError):
                self.assemble(root)

    def test_failed_overwrite_preserves_existing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            result = self.assemble(root)
            before = result.candidate_path.read_bytes()
            write_json(run_dir / "validation.json", {"passed": False, "checks": []})
            with self.assertRaises(WinePipelineError):
                self.assemble(root, overwrite=True)
            self.assertEqual(result.candidate_path.read_bytes(), before)

    def test_successful_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_batch_fixture(root)
            result = self.assemble(root)
            before_hash = sha256_file(result.candidate_path)
            write_candidate(run_dir / "regions" / "beta" / "candidate.geojson", [feature("Beta", "Replacement", 4.1)])
            after = self.assemble(root, overwrite=True)
            self.assertNotEqual(sha256_file(after.candidate_path), before_hash)

    def test_output_path_containment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_batch_fixture(root)
            result = self.assemble(root, candidate_id="../../escape")
            self.assertEqual(result.candidate_dir, (root / "candidates" / "escape").resolve())
            self.assertFalse((root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
