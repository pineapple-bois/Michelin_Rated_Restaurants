from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import geopandas as gpd
from shapely.geometry import Polygon

from wine_pipeline.aoc_simplification.diagnostics import run_diagnostics
from wine_pipeline.aoc_simplification.serialization import (
    SerializationCleanupError,
    cleanup_final_geometries as real_cleanup,
)


def square(x0: float, size: float = 1000) -> Polygon:
    return Polygon([(x0, 6600000), (x0 + size, 6600000), (x0 + size, 6600000 + size), (x0, 6600000 + size)])


def fixture_frame() -> gpd.GeoDataFrame:
    rows = []
    for index, region in enumerate(("Alpha", "Beta", "Gamma"), start=1):
        rows.append({
            "id_app": str(index),
            "app": f"{region} AOC",
            "display_name": f"{region} AOC",
            "dt": "Tours",
            "region": region,
            "region_method": "spatial_majority",
            "overlap_ratio": 1.0,
            "colour": "#123456",
            "categorie": "AOP",
            "geometry": square(700000 + index * 5000),
        })
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:2154")


def write_fixture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fixture_frame().to_file(path, layer="aocs_france", driver="GPKG", index=False)
    return path


class WineSimplificationDiagnosticTests(unittest.TestCase):
    def test_fixture_diagnostic_pass_is_sorted_and_writes_only_compact_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            result = run_diagnostics(
                input_path=source,
                diagnostic_run_id="fixture",
                output_root=root / "diagnostics",
            )
            self.assertTrue(result.passed)
            payload = json.loads(result.json_path.read_text())
            self.assertEqual(payload["region_inventory"], ["Alpha", "Beta", "Gamma"])
            self.assertEqual([row["region"] for row in payload["regions"]], ["Alpha", "Beta", "Gamma"])
            self.assertEqual({path.name for path in result.run_dir.iterdir()}, {"report.json", "report.csv"})
            with result.csv_path.open(newline="", encoding="utf-8") as handle:
                self.assertEqual([row["region"] for row in csv.DictReader(handle)], ["Alpha", "Beta", "Gamma"])

    def test_cleanup_failure_is_structured_and_later_regions_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            calls = []

            def cleanup(frame, **kwargs):
                region = str(frame.iloc[0]["region"])
                calls.append(region)
                if region == "Beta":
                    raise SerializationCleanupError("fixture defect", {
                        "region": "Beta",
                        "app": "Beta AOC",
                        "original_geometry_type": "MultiPolygon",
                        "repaired_geometry_type": "GeometryCollection",
                        "validity_reason": "Self-intersection[1 1]",
                        "original_polygon_component_count": 2,
                        "retained_component_count": 1,
                        "rejected_component_count": 1,
                        "rejected_area_m2": 2.0,
                        "rejected_area_fraction_of_source": 2e-6,
                        "absolute_tolerance_m2": 1.0,
                        "relative_tolerance": 1e-9,
                        "whole_appellation_empty": False,
                    })
                return real_cleanup(frame, **kwargs)

            with mock.patch(
                "wine_pipeline.aoc_simplification.diagnostics.cleanup_final_geometries",
                side_effect=cleanup,
            ):
                result = run_diagnostics(
                    input_path=source,
                    diagnostic_run_id="defects",
                    output_root=root / "diagnostics",
                )
            self.assertEqual(calls, ["Alpha", "Beta", "Gamma"])
            self.assertFalse(result.passed)
            payload = json.loads(result.json_path.read_text())
            beta = next(row for row in payload["regions"] if row["region"] == "Beta")
            self.assertEqual(beta["failed_app"], "Beta AOC")
            self.assertEqual(beta["failure_stage"], "serialization_cleanup")
            self.assertEqual(beta["rejected_component_count"], 1)
            self.assertEqual(beta["rejected_area_m2"], 2.0)
            self.assertEqual(beta["validity_reason"], "Self-intersection[1 1]")

    def test_round_trip_failure_is_reported_without_candidate_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_fixture(root / "stage1" / "aoc_regions.gpkg")
            with mock.patch(
                "wine_pipeline.aoc_simplification.diagnostics._round_trip",
                side_effect=ValueError("fixture serialization defect"),
            ):
                result = run_diagnostics(
                    input_path=source,
                    diagnostic_run_id="roundtrip",
                    output_root=root / "diagnostics",
                    region="Beta",
                )
            self.assertFalse(result.passed)
            payload = json.loads(result.json_path.read_text())
            self.assertEqual(payload["regions"][0]["failure_stage"], "geojson_round_trip")
            self.assertEqual({path.name for path in result.run_dir.iterdir()}, {"report.json", "report.csv"})


if __name__ == "__main__":
    unittest.main()
