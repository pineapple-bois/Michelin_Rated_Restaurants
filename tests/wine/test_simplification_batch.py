from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import geopandas as gpd
from shapely.geometry import Polygon

from wine_pipeline.aoc_simplification.batch import discover_regions, run_batch
from wine_pipeline.aoc_simplification.runner import run_single_region as real_run_single_region
from wine_pipeline.aoc_simplification.transform import OUTPUT_COLUMNS


def square(x0: float, y0: float, size: float) -> Polygon:
    return Polygon([(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)])


def feature(region: str, app: str, x0: float, *, size: float = 1000, id_app: str | None = None) -> dict[str, object]:
    return {
        "id_app": id_app or f"{region}-{app}",
        "app": app,
        "display_name": app,
        "dt": "Tours",
        "region": region,
        "region_method": "spatial_majority",
        "overlap_ratio": 1.0,
        "colour": "#123456",
        "categorie": "AOP",
        "geometry": square(x0, 6600000, size),
    }


def fixture_frame(*, replacement: bool = False) -> gpd.GeoDataFrame:
    prefix = "New " if replacement else ""
    rows = [
        feature("Alpha", f"{prefix}Alpha Broad", 700000, size=3000, id_app="1"),
        feature("Alpha", f"{prefix}Alpha Cru", 700500, size=800, id_app="2"),
        feature("Beta", f"{prefix}Beta", 710000, size=1500, id_app="3"),
        feature("Gamma", f"{prefix}Gamma", 720000, size=1200, id_app="4"),
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:2154")


def write_fixture(path: Path, frame: gpd.GeoDataFrame | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    (frame if frame is not None else fixture_frame()).to_file(path, layer="aocs_france", driver="GPKG", index=False)
    return path


def fake_write_plots(**kwargs) -> None:
    for key in ("preview_path", "comparison_path", "overlap_comparison_path"):
        Path(kwargs[key]).write_bytes(f"fake {key}".encode("utf-8"))


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def review_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class WineSimplificationBatchTests(unittest.TestCase):
    def test_deterministic_region_discovery_and_ordering(self) -> None:
        frame = fixture_frame()
        self.assertEqual(discover_regions(frame), ["Alpha", "Beta", "Gamma"])

    def test_successful_multi_region_orchestration_manifest_summary_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                result = run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            self.assertTrue(result.passed)
            run_dir = result.run_dir
            for name in ("run.json", "batch_summary.json", "validation.json", "region_review.csv"):
                self.assertTrue((run_dir / name).is_file(), name)
            manifest = read_json(run_dir / "run.json")
            self.assertEqual(manifest["batch_run_id"], "batch")
            self.assertTrue(manifest["canonical_parameter_set"])
            self.assertEqual(manifest["expected_region_inventory"], ["Alpha", "Beta", "Gamma"])
            self.assertIn("dependency_versions", manifest)
            summary = read_json(run_dir / "batch_summary.json")
            self.assertEqual(summary["expected_region_count"], 3)
            self.assertEqual(summary["completed_region_count"], 3)
            self.assertEqual(summary["failed_region_count"], 0)
            validation = read_json(run_dir / "validation.json")
            self.assertTrue(validation["passed"])
            rows = review_rows(run_dir / "region_review.csv")
            self.assertEqual([row["region"] for row in rows], ["Alpha", "Beta", "Gamma"])
            self.assertEqual(rows[0]["review_status"], "")

    def test_one_region_failure_continues_and_returns_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            calls: list[str] = []

            def fake_runner(**kwargs):
                calls.append(kwargs["region"])
                if kwargs["region"] == "Beta":
                    raise RuntimeError("beta exploded")
                return real_run_single_region(**kwargs)

            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots), mock.patch(
                "wine_pipeline.aoc_simplification.batch.run_single_region", side_effect=fake_runner
            ):
                result = run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            self.assertFalse(result.passed)
            self.assertEqual(calls, ["Alpha", "Beta", "Gamma"])
            self.assertEqual(result.failed_regions, {"Beta": "beta exploded"})
            summary = read_json(result.run_dir / "batch_summary.json")
            self.assertEqual(summary["failed_region_count"], 1)

    def test_cli_style_nonzero_when_any_region_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.batch.run_single_region", side_effect=RuntimeError("all fail")):
                result = run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            self.assertFalse(result.passed)
            self.assertEqual(len(result.failed_regions), 3)

    def test_normal_mode_refuses_existing_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            with self.assertRaises(FileExistsError):
                run_batch(input_path=input_path, run_id="batch", output_root=root / "out")

    def test_resume_skips_complete_valid_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            with mock.patch("wine_pipeline.aoc_simplification.batch.run_single_region") as runner:
                result = run_batch(input_path=input_path, run_id="batch", output_root=root / "out", resume=True)
            runner.assert_not_called()
            self.assertEqual(sorted(result.skipped_regions), ["Alpha", "Beta", "Gamma"])

    def test_resume_rebuilds_incomplete_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                first = run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            (first.run_dir / "regions" / "beta" / "preview.png").unlink()
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots), mock.patch(
                "wine_pipeline.aoc_simplification.batch.run_single_region", side_effect=real_run_single_region
            ) as runner:
                result = run_batch(input_path=input_path, run_id="batch", output_root=root / "out", resume=True)
            self.assertTrue(result.passed)
            self.assertEqual([call.kwargs["region"] for call in runner.mock_calls], ["Beta"])

    def test_resume_rebuilds_mismatched_source_hash_and_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                first = run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            alpha_params = first.run_dir / "regions" / "alpha" / "params.json"
            params = read_json(alpha_params)
            params["stage1_source_sha256"] = "bad"
            alpha_params.write_text(json.dumps(params), encoding="utf-8")
            beta_params = first.run_dir / "regions" / "beta" / "params.json"
            params = read_json(beta_params)
            params["effective_parameters"]["buffer_m"] = 999
            beta_params.write_text(json.dumps(params), encoding="utf-8")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots), mock.patch(
                "wine_pipeline.aoc_simplification.batch.run_single_region", side_effect=real_run_single_region
            ) as runner:
                result = run_batch(input_path=input_path, run_id="batch", output_root=root / "out", resume=True)
            self.assertTrue(result.passed)
            self.assertEqual([call.kwargs["region"] for call in runner.mock_calls], ["Alpha", "Beta"])

    def test_transactional_overwrite_preserves_prior_batch_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                first = run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            before = (first.run_dir / "run.json").read_bytes()
            with mock.patch("wine_pipeline.aoc_simplification.batch.run_single_region", side_effect=RuntimeError("overwrite failed")):
                result = run_batch(input_path=input_path, run_id="batch", output_root=root / "out", overwrite=True)
            self.assertFalse(result.passed)
            self.assertEqual((first.run_dir / "run.json").read_bytes(), before)
            self.assertFalse(list((root / "out").glob(".batch.tmp-*")))

    def test_successful_transactional_batch_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                first = run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            old_candidate = (first.run_dir / "regions" / "alpha" / "candidate.geojson").read_bytes()
            write_fixture(input_path, fixture_frame(replacement=True))
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                second = run_batch(input_path=input_path, run_id="batch", output_root=root / "out", overwrite=True)
            self.assertTrue(second.passed)
            self.assertNotEqual((second.run_dir / "regions" / "alpha" / "candidate.geojson").read_bytes(), old_candidate)
            self.assertEqual(second.run_dir, first.run_dir)

    def test_output_root_path_containment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                result = run_batch(input_path=input_path, run_id="../../escape", output_root=root / "out")
            self.assertEqual(result.run_dir, (root / "out" / "escape").resolve())
            self.assertFalse((root / "escape").exists())

    def test_human_owned_review_columns_are_preserved_on_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                first = run_batch(input_path=input_path, run_id="batch", output_root=root / "out")
            review_path = first.run_dir / "region_review.csv"
            rows = review_rows(review_path)
            rows[0]["review_status"] = "approved"
            rows[0]["reviewer"] = "Ian"
            rows[0]["notes"] = "keep"
            with review_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                run_batch(input_path=input_path, run_id="batch", output_root=root / "out", resume=True)
            refreshed = review_rows(review_path)
            alpha = next(row for row in refreshed if row["region"] == rows[0]["region"])
            self.assertEqual(alpha["review_status"], "approved")
            self.assertEqual(alpha["reviewer"], "Ian")
            self.assertEqual(alpha["notes"], "keep")


if __name__ == "__main__":
    unittest.main()
