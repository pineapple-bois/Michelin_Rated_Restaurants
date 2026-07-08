from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import geopandas as gpd
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon

from wine_pipeline.aoc_simplification.runner import _validate_candidate_round_trip, run_single_region
from wine_pipeline.aoc_simplification.serialization import (
    POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2,
    POST_REPROJECTION_NEGLIGIBLE_ABSOLUTE_M2,
    POST_REPROJECTION_NEGLIGIBLE_RELATIVE,
    POST_REPROJECTION_RELATIVE_TOLERANCE,
    SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2,
    SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE,
    SerializationCleanupError,
    cleanup_final_geometries,
    repair_post_reprojection_geometry,
)
from wine_pipeline.aoc_simplification.transform import (
    CANONICAL_BUFFER_M,
    CANONICAL_OVERLAP_STRATEGY,
    CANONICAL_RUN_ID,
    CANONICAL_SIMPLIFY_M,
    OUTPUT_COLUMNS,
    OverlapMetrics,
    SimplificationParameters,
    classify_residual_overlap,
    overlap_tolerance_m2,
    select_region,
    simplify_region,
    validate_stage1_schema,
)
from wine_pipeline.validation import WinePipelineError


def square(x0: float, y0: float, size: float) -> Polygon:
    return Polygon([(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)])


def stage1_rows(rows: list[dict[str, object]], crs: str = "EPSG:2154") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def stage1_feature(
    *,
    id_app: str,
    app: str,
    display_name: str | None = None,
    categorie: str = "Vin tranquille",
    geometry,
    region: str = "Fixture",
    colour: str = "#123456",
) -> dict[str, object]:
    return {
        "id_app": id_app,
        "app": app,
        "display_name": display_name or app,
        "dt": "Tours",
        "region": region,
        "region_method": "spatial_majority",
        "overlap_ratio": 1.0,
        "colour": colour,
        "categorie": categorie,
        "geometry": geometry,
    }


def fixture_stage1() -> gpd.GeoDataFrame:
    return stage1_rows(
        [
            stage1_feature(id_app="1", app="Broad", categorie="AOP", geometry=square(700000, 6600000, 10000)),
            stage1_feature(id_app="2", app="Cru", categorie="AOP", geometry=square(702000, 6602000, 2000)),
        ]
    )


def alternate_fixture_stage1() -> gpd.GeoDataFrame:
    return stage1_rows(
        [
            stage1_feature(
                id_app="10",
                app="Replacement",
                display_name="Replacement",
                categorie="IGP",
                geometry=square(700000, 6600000, 5000),
            )
        ]
    )


def write_stage1_fixture(path: Path, frame: gpd.GeoDataFrame | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    (fixture_stage1() if frame is None else frame).to_file(path, layer="aocs_france", driver="GPKG", index=False)
    return path


def fake_write_plots(**kwargs) -> None:
    for key in ("preview_path", "comparison_path", "overlap_comparison_path"):
        Path(kwargs[key]).write_bytes(f"fake {key}".encode("utf-8"))


def artifact_snapshot(path: Path) -> dict[str, bytes]:
    return {child.name: child.read_bytes() for child in sorted(path.iterdir()) if child.is_file()}


def load_development_simplification():
    path = Path("Development/aoc_simplification/simplification.py")
    spec = importlib.util.spec_from_file_location("development_aoc_simplification", path)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest("Development simplification module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WineSimplificationTests(unittest.TestCase):
    def serialization_frame(self, geometry, *, source_area_m2: float = 1_000_000.0) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            [{
                "region": "Fixture",
                "app": "Cleanup",
                "display_name": "Cleanup",
                "colour": "#123456",
                "categorie": "AOP",
                "source_area_m2": source_area_m2,
                "geometry": geometry,
            }],
            geometry="geometry",
            crs="EPSG:2154",
        )

    def test_serialization_cleanup_removes_degenerate_component_and_keeps_main_polygon(self) -> None:
        geometry = GeometryCollection([
            square(700000, 6600000, 1000),
            LineString([(701500, 6600000), (701500, 6600000)]),
        ])
        cleaned, diagnostics = cleanup_final_geometries(self.serialization_frame(geometry))
        self.assertEqual(len(cleaned), 1)
        self.assertFalse(cleaned.geometry.iloc[0].is_empty)
        self.assertTrue(cleaned.geometry.iloc[0].is_valid)
        self.assertEqual(diagnostics[0]["removed_component_count"], 1)
        self.assertEqual(diagnostics[0]["cleanup_action"], "removed_negligible_invalid_or_degenerate_components")
        self.assertEqual(
            diagnostics[0]["absolute_tolerance_m2"],
            SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2,
        )
        self.assertEqual(
            diagnostics[0]["relative_tolerance"],
            SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE,
        )

    def test_serialization_cleanup_fails_when_entire_geometry_becomes_empty(self) -> None:
        geometry = GeometryCollection([LineString([(0, 0), (1, 1)])])
        with self.assertRaises(SerializationCleanupError) as raised:
            cleanup_final_geometries(self.serialization_frame(geometry))
        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic["region"], "Fixture")
        self.assertEqual(diagnostic["app"], "Cleanup")
        self.assertEqual(diagnostic["original_geometry_type"], "GeometryCollection")
        self.assertEqual(diagnostic["repaired_geometry_type"], "GeometryCollection")
        self.assertEqual(diagnostic["original_polygon_component_count"], 0)
        self.assertEqual(diagnostic["retained_component_count"], 0)
        self.assertEqual(diagnostic["rejected_component_count"], 1)
        self.assertTrue(diagnostic["whole_appellation_empty"])
        self.assertIn("validity_reason", diagnostic)
        self.assertEqual(diagnostic["absolute_tolerance_m2"], 1.0)
        self.assertEqual(diagnostic["relative_tolerance"], 1e-9)

    def test_serialization_cleanup_fails_when_removal_exceeds_tolerance(self) -> None:
        geometry = GeometryCollection([
            square(700000, 6600000, 1000),
            LineString([(701500, 6600000), (701500, 6600000)]),
        ])
        with self.assertRaises(SerializationCleanupError) as raised:
            cleanup_final_geometries(
                self.serialization_frame(geometry),
                absolute_tolerance_m2=-1.0,
            )
        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic["retained_component_count"], 1)
        self.assertEqual(diagnostic["rejected_component_count"], 1)
        self.assertFalse(diagnostic["whole_appellation_empty"])
        self.assertEqual(diagnostic["absolute_tolerance_m2"], -1.0)

    def test_serialization_cleanup_leaves_valid_geometry_unmodified(self) -> None:
        geometry = square(700000, 6600000, 1000)
        cleaned, diagnostics = cleanup_final_geometries(self.serialization_frame(geometry))
        self.assertEqual(diagnostics[0]["cleanup_action"], "unchanged")
        self.assertEqual(diagnostics[0]["removed_component_count"], 0)
        round_tripped = cleaned.to_crs("EPSG:2154").geometry.iloc[0]
        self.assertAlmostEqual(round_tripped.area, geometry.area, places=4)

    def test_serialization_cleanup_survives_geojson_round_trip(self) -> None:
        geometry = GeometryCollection([
            square(700000, 6600000, 1000),
            LineString([(701500, 6600000), (701500, 6600000)]),
        ])
        cleaned, _ = cleanup_final_geometries(self.serialization_frame(geometry))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.geojson"
            cleaned.to_file(path, driver="GeoJSON", engine="pyogrio", index=False)
            reloaded = gpd.read_file(path, engine="pyogrio")
        _validate_candidate_round_trip(reloaded, context="Fixture candidate")
        self.assertTrue(reloaded.geometry.is_valid.all())
        self.assertFalse(reloaded.geometry.is_empty.any())

    def test_post_reprojection_self_intersection_is_repaired_with_negligible_area_change(self) -> None:
        frame = self.serialization_frame(square(700000, 6600000, 1000))
        original_to_crs = gpd.GeoDataFrame.to_crs
        tiny_bowtie = Polygon([
            (4.6000000, 47.9000000),
            (4.6000001, 47.9000001),
            (4.6000000, 47.9000001),
            (4.6000001, 47.9000000),
            (4.6000000, 47.9000000),
        ])

        def invalidating_to_crs(subject, *args, **kwargs):
            result = original_to_crs(subject, *args, **kwargs)
            target = args[0] if args else kwargs.get("crs")
            if str(target) == "EPSG:4326":
                result.at[result.index[0], "geometry"] = tiny_bowtie
            return result

        with mock.patch.object(gpd.GeoDataFrame, "to_crs", autospec=True, side_effect=invalidating_to_crs):
            cleaned, diagnostics = cleanup_final_geometries(
                self.serialization_frame(
                    square(700000, 6600000, 1000),
                    source_area_m2=1_000_000_000.0,
                )
            )
        diagnostic = diagnostics[0]
        self.assertTrue(cleaned.geometry.iloc[0].is_valid)
        self.assertEqual(
            diagnostic["post_reprojection_cleanup_action"],
            "post_reprojection_topology_repair",
        )
        self.assertIn(
            "Self-intersection",
            diagnostic["post_reprojection_validity_reason_before_repair"],
        )
        self.assertLessEqual(
            diagnostic["post_reprojection_absolute_area_change_m2"],
            1.0,
        )
        self.assertLessEqual(
            diagnostic["post_reprojection_relative_area_change"],
            POST_REPROJECTION_RELATIVE_TOLERANCE,
        )

    def test_post_reprojection_2_98_m2_and_5_42e_9_repair_is_accepted(self) -> None:
        bowtie = Polygon([
            (4.600, 47.900),
            (4.601, 47.901),
            (4.600, 47.901),
            (4.601, 47.900),
            (4.600, 47.900),
        ])
        source_area = 2.98 / 5.42e-9
        with mock.patch(
            "wine_pipeline.aoc_simplification.serialization._geometry_area_m2",
            side_effect=[1000.0, 1002.98],
        ):
            repaired, diagnostic = repair_post_reprojection_geometry(
                bowtie,
                region="Fixture",
                app="Accepted",
                source_area_m2=source_area,
            )
        self.assertTrue(repaired.is_valid)
        self.assertAlmostEqual(
            diagnostic["post_reprojection_absolute_area_change_m2"],
            2.98,
        )
        self.assertAlmostEqual(
            diagnostic["post_reprojection_relative_area_change"],
            5.42e-9,
        )
        self.assertEqual(
            diagnostic["post_reprojection_review_classification"],
            "negligible",
        )
        self.assertEqual(
            diagnostic["post_reprojection_absolute_tolerance_m2"],
            POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2,
        )
        self.assertEqual(
            diagnostic["post_reprojection_relative_tolerance"],
            POST_REPROJECTION_RELATIVE_TOLERANCE,
        )
        self.assertEqual(
            diagnostic["post_reprojection_negligible_absolute_threshold_m2"],
            POST_REPROJECTION_NEGLIGIBLE_ABSOLUTE_M2,
        )
        self.assertEqual(
            diagnostic["post_reprojection_negligible_relative_threshold"],
            POST_REPROJECTION_NEGLIGIBLE_RELATIVE,
        )

    def test_post_reprojection_32_m2_repair_is_accepted_for_review(self) -> None:
        bowtie = Polygon([
            (4.600, 47.900),
            (4.601, 47.901),
            (4.600, 47.901),
            (4.601, 47.900),
            (4.600, 47.900),
        ])
        with mock.patch(
            "wine_pipeline.aoc_simplification.serialization._geometry_area_m2",
            side_effect=[1000.0, 1032.0],
        ):
            repaired, diagnostic = repair_post_reprojection_geometry(
                bowtie,
                region="Fixture",
                app="Review",
                source_area_m2=1_000_000_000.0,
            )
        self.assertTrue(repaired.is_valid)
        self.assertEqual(
            diagnostic["post_reprojection_absolute_area_change_m2"],
            32.0,
        )
        self.assertEqual(
            diagnostic["post_reprojection_review_classification"],
            "review",
        )

    def test_post_reprojection_exceeding_only_one_hard_threshold_is_accepted(self) -> None:
        bowtie = Polygon([
            (4.600, 47.900),
            (4.601, 47.901),
            (4.600, 47.901),
            (4.601, 47.900),
            (4.600, 47.900),
        ])
        with mock.patch(
            "wine_pipeline.aoc_simplification.serialization._geometry_area_m2",
            side_effect=[1000.0, 1101.0],
        ):
            repaired, diagnostic = repair_post_reprojection_geometry(
                bowtie,
                region="Fixture",
                app="Absolute Only",
                source_area_m2=1_000_000_000.0,
            )
        self.assertTrue(repaired.is_valid)
        self.assertGreater(
            diagnostic["post_reprojection_absolute_area_change_m2"],
            POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2,
        )
        self.assertLess(
            diagnostic["post_reprojection_relative_area_change"],
            POST_REPROJECTION_RELATIVE_TOLERANCE,
        )
        self.assertEqual(
            diagnostic["post_reprojection_review_classification"],
            "review",
        )

    def test_post_reprojection_repair_exceeding_both_hard_thresholds_fails(self) -> None:
        bowtie = Polygon([
            (4.600, 47.900),
            (4.601, 47.901),
            (4.600, 47.901),
            (4.601, 47.900),
            (4.600, 47.900),
        ])
        with mock.patch(
            "wine_pipeline.aoc_simplification.serialization._geometry_area_m2",
            side_effect=[1000.0, 1101.0],
        ):
            with self.assertRaises(SerializationCleanupError) as raised:
                repair_post_reprojection_geometry(
                    bowtie,
                    region="Fixture",
                    app="Fatal",
                    source_area_m2=50_000_000.0,
                )
        self.assertIn("exceeds serialization cleanup tolerances", str(raised.exception))
        self.assertGreater(
            raised.exception.diagnostic["post_reprojection_absolute_area_change_m2"],
            POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2,
        )
        self.assertGreater(
            raised.exception.diagnostic["post_reprojection_relative_area_change"],
            POST_REPROJECTION_RELATIVE_TOLERANCE,
        )
        self.assertEqual(
            raised.exception.diagnostic["post_reprojection_review_classification"],
            "fatal",
        )

    def test_post_reprojection_non_polygonal_repair_fails(self) -> None:
        bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
        with mock.patch(
            "wine_pipeline.aoc_simplification.serialization.make_valid",
            return_value=LineString([(0, 0), (1, 1)]),
        ):
            with self.assertRaises(SerializationCleanupError) as raised:
                repair_post_reprojection_geometry(
                    bowtie,
                    region="Fixture",
                    app="Non Polygon",
                    source_area_m2=1_000_000.0,
                )
        self.assertIn("non-polygonal", str(raised.exception))
        self.assertEqual(
            raised.exception.diagnostic["post_reprojection_component_count_after_repair"],
            0,
        )
        self.assertEqual(
            raised.exception.diagnostic["post_reprojection_review_classification"],
            "fatal",
        )

    def test_post_reprojection_empty_and_invalid_repairs_are_fatal(self) -> None:
        bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
        replacements = {
            "empty": Polygon(),
            "invalid": bowtie,
        }
        for label, replacement in replacements.items():
            with self.subTest(label=label), mock.patch(
                "wine_pipeline.aoc_simplification.serialization.make_valid",
                return_value=replacement,
            ):
                with self.assertRaises(SerializationCleanupError) as raised:
                    repair_post_reprojection_geometry(
                        bowtie,
                        region="Fixture",
                        app=label,
                        source_area_m2=1_000_000.0,
                    )
                self.assertEqual(
                    raised.exception.diagnostic[
                        "post_reprojection_review_classification"
                    ],
                    "fatal",
                )

    def test_post_reprojection_valid_geometry_is_unchanged(self) -> None:
        geometry = Polygon([
            (4.60, 47.90),
            (4.61, 47.90),
            (4.61, 47.91),
            (4.60, 47.91),
            (4.60, 47.90),
        ])
        repaired, diagnostic = repair_post_reprojection_geometry(
            geometry,
            region="Fixture",
            app="Valid",
            source_area_m2=1_000_000.0,
        )
        self.assertTrue(repaired.equals(geometry))
        self.assertEqual(
            diagnostic["post_reprojection_cleanup_action"],
            "post_reprojection_unchanged",
        )
        self.assertEqual(
            diagnostic["post_reprojection_review_classification"],
            "none",
        )
        self.assertEqual(
            diagnostic["post_reprojection_absolute_area_change_m2"],
            0.0,
        )

    def test_post_reprojection_area_compares_topological_footprints(self) -> None:
        first = Polygon([
            (4.6000, 47.9000),
            (4.6100, 47.9000),
            (4.6100, 47.9100),
            (4.6000, 47.9100),
            (4.6000, 47.9000),
        ])
        second = Polygon([
            (4.6099, 47.9000),
            (4.6200, 47.9000),
            (4.6200, 47.9100),
            (4.6099, 47.9100),
            (4.6099, 47.9000),
        ])
        overlapping = MultiPolygon([first, second])
        self.assertFalse(overlapping.is_valid)
        repaired, diagnostic = repair_post_reprojection_geometry(
            overlapping,
            region="Fixture",
            app="Overlapping Parts",
            source_area_m2=10_000_000.0,
        )
        self.assertTrue(repaired.is_valid)
        self.assertLessEqual(
            diagnostic["post_reprojection_absolute_area_change_m2"],
            1.0,
        )

    def test_stage1_schema_validation_and_region_selection(self) -> None:
        data = fixture_stage1()
        validate_stage1_schema(data)
        selected = select_region(data, "Fixture")
        self.assertEqual(len(selected), 2)
        with self.assertRaisesRegex(ValueError, "missing columns"):
            validate_stage1_schema(data.drop(columns=["display_name"]))
        with self.assertRaisesRegex(ValueError, "Unknown region"):
            select_region(data, "Missing")

    def test_output_schema_preserves_display_and_categorie_and_drops_stage1_only_fields(self) -> None:
        result = simplify_region(fixture_stage1())
        self.assertEqual(result.final.columns.tolist(), OUTPUT_COLUMNS)
        self.assertIn("display_name", result.final.columns)
        self.assertIn("categorie", result.final.columns)
        for column in ("id_app", "dt", "region_method", "overlap_ratio"):
            self.assertNotIn(column, result.final.columns)
        self.assertEqual(set(result.final["display_name"]), {"Broad", "Cru"})
        self.assertEqual(set(result.final["categorie"]), {"AOP"})

    def test_processing_crs_output_crs_and_deterministic_ordering(self) -> None:
        first = simplify_region(fixture_stage1())
        second = simplify_region(fixture_stage1())
        self.assertEqual(first.repaired.crs.to_epsg(), 2154)
        self.assertEqual(first.dissolved.crs.to_epsg(), 2154)
        self.assertEqual(first.final.crs.to_epsg(), 4326)
        self.assertEqual(first.final["app"].tolist(), second.final["app"].tolist())
        self.assertEqual(first.final.columns.tolist(), OUTPUT_COLUMNS)

    def test_canonical_parameter_defaults(self) -> None:
        params = SimplificationParameters()
        self.assertTrue(params.canonical)
        self.assertEqual(CANONICAL_RUN_ID, "close500_simplify150")
        self.assertEqual(params.buffer_m, CANONICAL_BUFFER_M)
        self.assertEqual(params.simplify_m, CANONICAL_SIMPLIFY_M)
        self.assertEqual(params.overlap_strategy, CANONICAL_OVERLAP_STRATEGY)
        experimental = SimplificationParameters(buffer_m=250)
        self.assertFalse(experimental.canonical)
        self.assertIsNone(experimental.as_dict()["canonical_parameter_set_name"])

    def test_topology_preserving_simplification_call(self) -> None:
        original = gpd.GeoSeries.simplify
        with mock.patch.object(gpd.GeoSeries, "simplify", autospec=True, side_effect=original) as patched:
            simplify_region(fixture_stage1())
        self.assertTrue(patched.called)
        self.assertTrue(any(call.kwargs.get("preserve_topology") is True for call in patched.mock_calls))

    def test_smallest_wins_overlap_and_fully_covered_reporting(self) -> None:
        result = simplify_region(fixture_stage1())
        self.assertIsNotNone(result.partition_report)
        report = result.partition_report
        assert report is not None
        self.assertEqual(report.strategy, "smallest-wins")
        self.assertIn("Broad", report.partially_reduced_app_names)
        self.assertEqual(report.fully_covered_app_names, [])

        coextensive = stage1_rows(
            [
                stage1_feature(id_app="1", app="Alpha", geometry=square(700000, 6600000, 1000)),
                stage1_feature(id_app="2", app="Beta", geometry=square(700000, 6600000, 1000)),
            ]
        )
        covered = simplify_region(coextensive)
        assert covered.partition_report is not None
        self.assertEqual(covered.partition_report.fully_covered_app_names, ["Beta"])
        self.assertEqual(covered.partition_report.fully_covered_app_count, 1)

    def test_residual_overlap_inside_numerical_tolerance_is_none(self) -> None:
        metrics = OverlapMetrics(
            summed_app_area_m2=1_000_000.0,
            union_area_m2=1_000_000.0,
            overlap_area_m2=overlap_tolerance_m2(1_000_000.0),
        )
        result = classify_residual_overlap(metrics)
        self.assertEqual(result.classification, "none")
        self.assertFalse(result.fatal)

    def test_loire_scale_residual_overlap_is_negligible(self) -> None:
        metrics = OverlapMetrics(
            summed_app_area_m2=2_687_000_036.02,
            union_area_m2=2_687_000_000.0,
            overlap_area_m2=36.02,
        )
        result = classify_residual_overlap(metrics)
        self.assertEqual(result.classification, "negligible")
        self.assertAlmostEqual(result.residual_overlap_ratio, 1.34052847e-8, places=15)
        self.assertFalse(result.fatal)

    def test_residual_overlap_exceeding_only_one_fatal_threshold_is_review(self) -> None:
        by_area_only = classify_residual_overlap(
            OverlapMetrics(
                summed_app_area_m2=1_000_001_500.0,
                union_area_m2=1_000_000_000.0,
                overlap_area_m2=1_500.0,
            )
        )
        self.assertEqual(by_area_only.classification, "review")
        self.assertFalse(by_area_only.fatal)

        by_ratio_only = classify_residual_overlap(
            OverlapMetrics(
                summed_app_area_m2=5_000_200.0,
                union_area_m2=5_000_000.0,
                overlap_area_m2=200.0,
            )
        )
        self.assertEqual(by_ratio_only.classification, "review")
        self.assertFalse(by_ratio_only.fatal)

    def test_residual_overlap_exceeding_both_fatal_thresholds_is_fatal(self) -> None:
        result = classify_residual_overlap(
            OverlapMetrics(
                summed_app_area_m2=10_002_000.0,
                union_area_m2=10_000_000.0,
                overlap_area_m2=2_000.0,
            )
        )
        self.assertEqual(result.classification, "fatal")
        self.assertTrue(result.fatal)

    def test_residual_overlap_classification_does_not_change_metrics(self) -> None:
        metrics = OverlapMetrics(
            summed_app_area_m2=1_000_036.02,
            union_area_m2=1_000_000.0,
            overlap_area_m2=36.02,
        )
        before = metrics.as_dict()
        result = classify_residual_overlap(metrics)
        self.assertEqual(metrics.as_dict(), before)
        self.assertEqual(result.residual_overlap_area_m2, before["overlap_area_m2"])
        self.assertEqual(result.union_area_m2, before["union_area_m2"])

    def test_source_area_is_dissolved_area_before_later_geometry_changes(self) -> None:
        result = simplify_region(fixture_stage1())
        broad_dissolved = result.dissolved.set_index("app").loc["Broad"]
        broad_final = result.final.to_crs("EPSG:2154").set_index("app").loc["Broad"]
        self.assertEqual(broad_dissolved["source_area_m2"], 100_000_000.0)
        self.assertEqual(broad_final["source_area_m2"], broad_dissolved["source_area_m2"])
        self.assertLess(float(broad_final.geometry.area), broad_final["source_area_m2"])

    def test_invalid_and_empty_geometry_detection(self) -> None:
        invalid = stage1_rows(
            [
                stage1_feature(
                    id_app="1",
                    app="Bowtie",
                    geometry=Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)]),
                )
            ]
        )
        with mock.patch("wine_pipeline.aoc_simplification.transform.repair_geometry", side_effect=lambda geometry: geometry):
            with self.assertRaises(ValueError):
                simplify_region(invalid)

        empty = stage1_rows([stage1_feature(id_app="1", app="Empty", geometry=Polygon())])
        with self.assertRaisesRegex(ValueError, "empty geometry"):
            simplify_region(empty)

    def test_packaged_transform_matches_development_geometry_on_controlled_fixture(self) -> None:
        dev = load_development_simplification()
        source = fixture_stage1()
        ours = simplify_region(source)
        dev_input = source[["region", "app", "colour", "geometry"]].copy()
        theirs = dev.process_region(
            dev_input,
            overlap_strategy="smallest-wins",
            buffer_dist_m=500,
            simplify_m=150,
        )
        ours_area = round(float(ours.final.to_crs("EPSG:2154").geometry.area.sum()), 6)
        theirs_area = round(float(theirs.final.to_crs("EPSG:2154").geometry.area.sum()), 6)
        self.assertEqual(len(ours.final), len(theirs.final))
        self.assertAlmostEqual(ours_area, theirs_area, places=3)

    def test_real_jura_transform_when_stage1_candidate_exists(self) -> None:
        if os.environ.get("WINE_RUN_INTEGRATION_TESTS") != "1":
            self.skipTest("Set WINE_RUN_INTEGRATION_TESTS=1 to process a real Stage 1 regional candidate")
        candidates = sorted(Path("tmp/wine").glob("*/candidates/aoc_regions.gpkg"))
        if not candidates:
            self.skipTest("No Stage 1 aoc_regions.gpkg candidate available")
        data = gpd.read_file(candidates[-1], layer="aocs_france")
        selected = select_region(data, "Jura")
        result = simplify_region(selected)
        self.assertGreater(len(result.final), 0)
        self.assertEqual(result.final.crs.to_epsg(), 4326)
        self.assertEqual(result.final.columns.tolist(), OUTPUT_COLUMNS)
        self.assertTrue(result.final.geometry.is_valid.all())

    def test_single_region_runner_generates_expected_artifact_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "stage1" / "aoc_regions.gpkg"
            write_stage1_fixture(input_path)
            output_root = root / "simplification"
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                result = run_single_region(
                    region="Fixture",
                    input_path=input_path,
                    run_id="test_run",
                    output_root=output_root,
                    overwrite=False,
                )
            expected = [
                result.candidate_path,
                result.metrics_path,
                result.params_path,
                result.preview_path,
                result.comparison_path,
                result.overlap_comparison_path,
            ]
            for path in expected:
                self.assertTrue(path.is_file(), path)
            written = gpd.read_file(result.candidate_path)
            self.assertEqual(written.columns.tolist(), OUTPUT_COLUMNS)
            self.assertEqual(result.run_dir, (output_root / "test_run" / "regions" / "fixture").resolve())
            metrics = json.loads(result.metrics_path.read_text())
            self.assertIn("serialization_cleanup", metrics)
            self.assertEqual(
                len(metrics["serialization_cleanup"]["diagnostics"]),
                len(written),
            )

    def test_failed_overwrite_preserves_previous_completed_artifact_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_stage1_fixture(root / "stage1" / "aoc_regions.gpkg")
            output_root = root / "simplification"
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                first = run_single_region(region="Fixture", input_path=input_path, run_id="stable", output_root=output_root)
            before = artifact_snapshot(first.run_dir)

            def failing_plots(**kwargs):
                fake_write_plots(**kwargs)
                raise RuntimeError("plot failure")

            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=failing_plots):
                with self.assertRaisesRegex(RuntimeError, "plot failure"):
                    run_single_region(
                        region="Fixture",
                        input_path=input_path,
                        run_id="stable",
                        output_root=output_root,
                        overwrite=True,
                    )
            self.assertEqual(artifact_snapshot(first.run_dir), before)
            temp_dirs = list((output_root / "stable" / "regions").glob(".fixture.tmp-*"))
            self.assertEqual(temp_dirs, [])

    def test_failed_run_retains_temp_directory_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_stage1_fixture(root / "stage1" / "aoc_regions.gpkg")
            output_root = root / "simplification"

            def failing_plots(**kwargs):
                fake_write_plots(**kwargs)
                raise RuntimeError("plot failure")

            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=failing_plots):
                with self.assertRaisesRegex(RuntimeError, "plot failure"):
                    run_single_region(region="Fixture", input_path=input_path, run_id="cleaned", output_root=output_root)
            self.assertEqual(list((output_root / "cleaned" / "regions").glob(".fixture.tmp-*")), [])

            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=failing_plots):
                with self.assertRaisesRegex(WinePipelineError, "retained failed temporary directory") as raised:
                    run_single_region(
                        region="Fixture",
                        input_path=input_path,
                        run_id="retained",
                        output_root=output_root,
                        keep_failed_temp=True,
                    )
            retained = list((output_root / "retained" / "regions").glob(".fixture.tmp-*"))
            self.assertEqual(len(retained), 1)
            self.assertIn(str(retained[0]), str(raised.exception))
            self.assertTrue((retained[0] / "candidate.geojson").is_file())
            self.assertTrue((retained[0] / "preview.png").is_file())

    def test_candidate_round_trip_validation_reports_affected_rows(self) -> None:
        frame = gpd.GeoDataFrame(
            [
                {"app": "Broken", "geometry": Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])},
                {"app": "Missing", "geometry": None},
                {"app": "Empty", "geometry": Polygon()},
            ],
            geometry="geometry",
            crs="EPSG:4326",
        )
        with self.assertRaisesRegex(ValueError, "Broken") as raised:
            _validate_candidate_round_trip(frame, context="Candidate GeoJSON")
        message = str(raised.exception)
        self.assertIn("geometry_type='Polygon'", message)
        self.assertIn("geometry_is_null=True", message)
        self.assertIn("geometry_is_empty=True", message)
        self.assertIn("validity_reason=", message)
        self.assertIn("Missing", message)
        self.assertIn("Empty", message)

    def test_successful_overwrite_replaces_complete_artifact_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = write_stage1_fixture(root / "stage1" / "aoc_regions.gpkg")
            output_root = root / "simplification"
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                first = run_single_region(region="Fixture", input_path=input_path, run_id="replace_me", output_root=output_root)
            before = artifact_snapshot(first.run_dir)
            write_stage1_fixture(input_path, alternate_fixture_stage1())
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                second = run_single_region(
                    region="Fixture",
                    input_path=input_path,
                    run_id="replace_me",
                    output_root=output_root,
                    overwrite=True,
                )
            after = artifact_snapshot(second.run_dir)
            self.assertEqual(set(after), {"candidate.geojson", "metrics.json", "params.json", "preview.png", "comparison.png", "overlap_comparison.png"})
            self.assertNotEqual(after["candidate.geojson"], before["candidate.geojson"])
            metrics = json.loads((second.run_dir / "metrics.json").read_text())
            self.assertEqual(metrics["stages"]["final_candidate"]["app_count"], 1)

    def test_run_id_and_region_slug_cannot_escape_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            odd_region = stage1_rows(
                [
                    stage1_feature(
                        id_app="1",
                        app="Odd",
                        region="../Fixture",
                        geometry=square(700000, 6600000, 1000),
                    )
                ]
            )
            input_path = write_stage1_fixture(root / "stage1" / "aoc_regions.gpkg", odd_region)
            output_root = root / "simplification"
            with mock.patch("wine_pipeline.aoc_simplification.runner.write_plots", side_effect=fake_write_plots):
                result = run_single_region(
                    region="../Fixture",
                    input_path=input_path,
                    run_id="../../escape",
                    output_root=output_root,
                    overwrite=False,
                )
            self.assertEqual(result.run_dir, (output_root / "escape" / "regions" / "fixture").resolve())
            self.assertTrue(result.run_dir.is_dir())
            self.assertFalse((root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
