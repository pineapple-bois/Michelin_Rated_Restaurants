from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from wine_pipeline.aoc_simplification.transform import OUTPUT_COLUMNS
from wine_pipeline.product import PRODUCT_FILENAME, publish_product, resolve_candidate_id
from wine_pipeline.provenance import sha256_file
from wine_pipeline.validation import WinePipelineError


def feature(region: str, app: str, x: float) -> dict[str, object]:
    return {
        "type": "Feature",
        "properties": {
            "region": region,
            "app": app,
            "display_name": app,
            "colour": "#123456",
            "categorie": "AOP",
            "source_area_m2": 1000.0,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[x, 47.0], [x + 0.01, 47.0], [x + 0.01, 47.01], [x, 47.01], [x, 47.0]]],
        },
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def make_candidate(
    root: Path,
    candidate_id: str = "candidate-a",
    *,
    passed: bool = True,
    complete: bool = True,
    features: list[dict[str, object]] | None = None,
) -> Path:
    candidate_dir = root / "candidates" / candidate_id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = candidate_dir / "wine_regions.geojson"
    write_json(
        candidate_path,
        {
            "type": "FeatureCollection",
            "features": features or [feature("Bordeaux", "Alpha", 1.0), feature("Jura", "Beta", 2.0)],
        },
    )
    candidate_hash = sha256_file(candidate_path)
    write_json(
        candidate_dir / "manifest.json",
        {
            "candidate_id": candidate_id,
            "candidate_sha256": candidate_hash,
            "schema": OUTPUT_COLUMNS,
            "validation_status": "passed",
        },
    )
    if complete:
        write_json(
            candidate_dir / "provenance.json",
            {
                "candidate_id": candidate_id,
                "stage1_run_id": "stage1-fixture",
                "stage1_source_path": "tmp/wine/stage1-fixture/candidates/aoc_regions.gpkg",
                "stage1_source_sha256": "a" * 64,
                "simplification_run_id": "close500_simplify150",
                "approval_mode": "automated_validated_batch",
                "canonical_parameter_set": True,
                "effective_simplification_parameters": {
                    "buffer_m": 500.0,
                    "simplify_m": 150.0,
                    "overlap_strategy": "smallest-wins",
                },
            },
        )
    write_json(root / "validation" / f"{candidate_id}.validation.json", {"candidate_id": candidate_id, "passed": passed})
    return candidate_dir


class WineProductTests(unittest.TestCase):
    def publish(self, root: Path, **kwargs):
        return publish_product(
            candidate_id=kwargs.pop("candidate_id", "candidate-a"),
            release_date=kwargs.pop("release_date", "2026-07-08"),
            candidate_root=root / "candidates",
            candidate_validation_root=root / "validation",
            product_root=root / "products",
            project_root=root,
            command=["wine_pipeline", "publish-product"],
            **kwargs,
        )

    def test_sole_candidate_auto_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_candidate(root)
            self.assertEqual(
                resolve_candidate_id(
                    None,
                    project_root=root,
                    candidate_root=root / "candidates",
                    candidate_validation_root=root / "validation",
                ),
                "candidate-a",
            )

    def test_zero_eligible_candidates_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "assemble-candidate.*--candidate-id"):
                resolve_candidate_id(None, project_root=Path(tmp))

    def test_multiple_candidates_require_explicit_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_candidate(root, "alpha")
            make_candidate(root, "beta")
            with self.assertRaisesRegex(WinePipelineError, r"(?s)Multiple validated.*alpha.*beta.*--candidate-id"):
                resolve_candidate_id(
                    None,
                    project_root=root,
                    candidate_root=root / "candidates",
                    candidate_validation_root=root / "validation",
                )
            self.assertEqual(
                resolve_candidate_id(
                    "beta",
                    project_root=root,
                    candidate_root=root / "candidates",
                    candidate_validation_root=root / "validation",
                ),
                "beta",
            )

    def test_incomplete_and_failed_candidates_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_candidate(root, "incomplete", complete=False)
            make_candidate(root, "failed", passed=False)
            make_candidate(root, ".temporary")
            make_candidate(root, "valid")
            self.assertEqual(
                resolve_candidate_id(
                    None,
                    project_root=root,
                    candidate_root=root / "candidates",
                    candidate_validation_root=root / "validation",
                ),
                "valid",
            )

    def test_explicit_failed_candidate_validation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_candidate(root, passed=False)
            with self.assertRaisesRegex(WinePipelineError, "Candidate validation did not pass"):
                self.publish(root)

    def test_default_and_explicit_release_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_candidate(root)
            with mock.patch("wine_pipeline.product.date") as mocked_date:
                mocked_date.today.return_value = date(2030, 4, 5)
                default_result = publish_product(
                    candidate_id="candidate-a",
                    candidate_root=root / "candidates",
                    candidate_validation_root=root / "validation",
                    product_root=root / "products",
                    project_root=root,
                )
            self.assertEqual(default_result.release_date, "2030-04-05")
            second = self.publish(root, release_date="2030-04-06")
            self.assertEqual(second.release_date, "2030-04-06")

    def test_release_collision_refusal_and_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = make_candidate(root)
            first = self.publish(root)
            original_manifest = first.manifest_path.read_bytes()
            with self.assertRaises(FileExistsError):
                self.publish(root)
            payload = read_json(candidate_dir / "wine_regions.geojson")
            payload["features"][0]["properties"]["display_name"] = "Changed"
            write_json(candidate_dir / "wine_regions.geojson", payload)
            manifest = read_json(candidate_dir / "manifest.json")
            manifest["candidate_sha256"] = sha256_file(candidate_dir / "wine_regions.geojson")
            write_json(candidate_dir / "manifest.json", manifest)
            replaced = self.publish(root, overwrite=True)
            self.assertNotEqual(replaced.manifest_path.read_bytes(), original_manifest)
            self.assertEqual(read_json(replaced.product_path)["features"][0]["properties"]["display_name"], "Changed")

    def test_exact_schema_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = make_candidate(root)
            payload = read_json(candidate_dir / "wine_regions.geojson")
            payload["features"][0]["properties"]["extra"] = "not allowed"
            write_json(candidate_dir / "wine_regions.geojson", payload)
            manifest = read_json(candidate_dir / "manifest.json")
            manifest["candidate_sha256"] = sha256_file(candidate_dir / "wine_regions.geojson")
            write_json(candidate_dir / "manifest.json", manifest)
            with self.assertRaisesRegex(WinePipelineError, "schema"):
                self.publish(root)

    def test_invalid_and_empty_geometry_are_rejected(self) -> None:
        bad_geometries = (
            {"type": "Polygon", "coordinates": [[[1, 47], [1.01, 47.01], [1, 47.01], [1.01, 47], [1, 47]]]},
            {"type": "Polygon", "coordinates": []},
        )
        for geometry in bad_geometries:
            with self.subTest(geometry=geometry), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                row = feature("Jura", "Broken", 1.0)
                row["geometry"] = geometry
                make_candidate(root, features=[row])
                with self.assertRaisesRegex(WinePipelineError, "geometry"):
                    self.publish(root)

    def test_candidate_manifest_hash_mismatch_is_rejected_and_temp_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = make_candidate(root)
            manifest = read_json(candidate_dir / "manifest.json")
            manifest["candidate_sha256"] = "0" * 64
            write_json(candidate_dir / "manifest.json", manifest)
            with self.assertRaisesRegex(WinePipelineError, "hash"):
                self.publish(root)
            self.assertFalse((root / "products" / "2026-07-08").exists())
            self.assertEqual(list((root / "products").glob(".*.tmp-*")), [])

    def test_byte_identical_promotion_reload_validation_and_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = make_candidate(root)
            result = self.publish(root)
            self.assertEqual((candidate_dir / "wine_regions.geojson").read_bytes(), result.product_path.read_bytes())
            manifest = read_json(result.manifest_path)
            provenance = read_json(result.provenance_path)
            validation = read_json(result.validation_path)
            self.assertEqual(manifest["product_filename"], PRODUCT_FILENAME)
            self.assertEqual(manifest["candidate_sha256"], manifest["product_sha256"])
            self.assertFalse(manifest["geometry_transformation"])
            self.assertEqual(provenance["lineage"], ["build", "simplify", "assemble-candidate", "publish-product"])
            self.assertEqual(provenance["candidate_assembly_approval_mode"], "automated_validated_batch")
            self.assertTrue(validation["passed"])
            self.assertTrue(any(check["phase"] == "post_write" for check in validation["checks"]))

    def test_failed_overwrite_preserves_previous_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = make_candidate(root)
            first = self.publish(root)
            before = {path.name: path.read_bytes() for path in first.release_dir.iterdir()}
            manifest = read_json(candidate_dir / "manifest.json")
            manifest["candidate_sha256"] = "bad"
            write_json(candidate_dir / "manifest.json", manifest)
            with self.assertRaises(WinePipelineError):
                self.publish(root, overwrite=True)
            after = {path.name: path.read_bytes() for path in first.release_dir.iterdir()}
            self.assertEqual(after, before)

    def test_path_containment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_candidate(root)
            with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                self.publish(root, release_date="../escape")
            with self.assertRaisesRegex(ValueError, "outside"):
                publish_product(
                    candidate_id="../escape",
                    candidate_root=root / "candidates",
                    candidate_validation_root=root / "validation",
                    product_root=root / "products",
                    project_root=root,
                )


if __name__ == "__main__":
    unittest.main()
