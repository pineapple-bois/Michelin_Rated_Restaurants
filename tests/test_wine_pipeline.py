from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Polygon

from wine_pipeline.aoc_enrichment.transform import enrich_aoc_regions, write_enriched_candidate
from wine_pipeline.aoc_package.extract import extract_archive_safely, locate_shapefile, stream_download
from wine_pipeline.aoc_package.transform import package_aoc_geometries, write_packaged_candidate
from wine_pipeline.aoc_package.validate import validate_packaged_artifact
from wine_pipeline.provenance import sha256_file
from wine_pipeline.validation import WinePipelineError, validate_and_repair_geometry


class FakeResponse:
    def __init__(self, content: bytes = b"", *, status_error: Exception | None = None, url: str = "https://example.test/file.zip"):
        self.content = content
        self.status_error = status_error
        self.url = url
        self.headers = {
            "Content-Type": "application/zip",
            "ETag": "abc",
            "Last-Modified": "Mon, 22 Jun 2026 12:00:00 GMT",
        }

    def raise_for_status(self) -> None:
        if self.status_error:
            raise self.status_error

    def iter_content(self, chunk_size: int):
        midpoint = max(1, len(self.content) // 2)
        yield self.content[:midpoint]
        yield self.content[midpoint:]


class FakeSession:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def aoc_frame(rows: list[dict[str, object]], crs: str = "EPSG:2154") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def one_aoc(id_app: str = "1", *, x0: float = 0.0, dt: str = "Tours", app: str = "AOC One") -> dict[str, object]:
    return {
        "app": app,
        "id_app": id_app,
        "dt": dt,
        "categorie": "Vin tranquille",
        "geometry": Polygon([(x0, 0), (x0 + 10, 0), (x0 + 10, 10), (x0, 10)]),
    }


def region_frame(rows: list[tuple[str, Polygon]], crs: str = "EPSG:2154") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [{"region": region, "geometry": geometry} for region, geometry in rows],
        geometry="geometry",
        crs=crs,
    )


class WinePipelineTests(unittest.TestCase):
    def test_successful_streamed_download_records_hash_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = zip_bytes({"dataset.shp": b"", "dataset.shx": b"", "dataset.dbf": b"", "dataset.prj": b""})
            session = FakeSession(FakeResponse(payload, url="https://example.test/archive.zip"))
            result = stream_download(
                configured_url="https://configured.test/archive",
                destination_dir=Path(tmp),
                fallback_filename="archive.zip",
                timeout=(1.0, 2.0),
                session=session,
            )
            self.assertEqual(result.size_bytes, len(payload))
            self.assertEqual(result.sha256, sha256_file(result.path))
            self.assertEqual(result.archive_type, "zip")
            self.assertEqual(session.calls[0][1]["timeout"], (1.0, 2.0))

    def test_failed_http_response_and_timeout_propagate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(requests.HTTPError):
                stream_download(
                    configured_url="https://configured.test/archive",
                    destination_dir=Path(tmp),
                    fallback_filename="archive.zip",
                    session=FakeSession(FakeResponse(zip_bytes({"a": b""}), status_error=requests.HTTPError("500"))),
                )
            with self.assertRaises(requests.Timeout):
                stream_download(
                    configured_url="https://configured.test/archive",
                    destination_dir=Path(tmp),
                    fallback_filename="archive.zip",
                    session=FakeSession(error=requests.Timeout("slow")),
                )

    def test_safe_archive_extraction_and_traversal_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "good.zip"
            archive.write_bytes(zip_bytes({"a/dataset.shp": b"shp"}))
            extracted = extract_archive_safely(archive, root / "out")
            self.assertEqual(extracted[0].read_bytes(), b"shp")

            bad = root / "bad.zip"
            bad.write_bytes(zip_bytes({"../evil.shp": b"no"}))
            with self.assertRaisesRegex(WinePipelineError, "escape"):
                extract_archive_safely(bad, root / "bad-out")

    def test_shapefile_member_and_ambiguity_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            complete = root / "complete"
            complete.mkdir()
            for suffix in [".shp", ".shx", ".dbf", ".prj"]:
                (complete / f"dataset{suffix}").write_bytes(b"x")
            found = locate_shapefile(complete)
            self.assertEqual(found.shapefile_path.name, "dataset.shp")

            missing = root / "missing"
            missing.mkdir()
            (missing / "dataset.shp").write_bytes(b"x")
            with self.assertRaisesRegex(WinePipelineError, "missing required"):
                locate_shapefile(missing)

            ambiguous = root / "ambiguous"
            ambiguous.mkdir()
            for stem in ["a", "b"]:
                for suffix in [".shp", ".shx", ".dbf", ".prj"]:
                    (ambiguous / f"{stem}{suffix}").write_bytes(b"x")
            with self.assertRaisesRegex(WinePipelineError, "exactly one"):
                locate_shapefile(ambiguous)

    def test_aoc_packaging_rejects_missing_columns_crs_and_inconsistent_dt(self) -> None:
        good = aoc_frame([one_aoc("1"), one_aoc("1", x0=20)])
        with self.assertRaises(WinePipelineError):
            package_aoc_geometries(good.drop(columns=["dt"]))
        with self.assertRaises(WinePipelineError):
            package_aoc_geometries(aoc_frame([one_aoc("1")], crs="EPSG:4326"))

        inconsistent_dt = aoc_frame([one_aoc("1", dt="Tours"), one_aoc("1", x0=20, dt="Dijon")])
        with self.assertRaisesRegex(WinePipelineError, "Inconsistent dt"):
            package_aoc_geometries(inconsistent_dt)

    def test_aoc_packaging_reports_mixed_categorie_and_keeps_first_source_value(self) -> None:
        mixed_category = aoc_frame(
            [one_aoc("1"), {**one_aoc("1", x0=20), "categorie": "Vin mousseux"}]
        )
        packaged, checks, metadata = package_aoc_geometries(mixed_category)
        self.assertEqual(packaged.loc[0, "categorie"], "Vin tranquille")
        mixed_check = [check for check in checks if check.name == "aoc_package_group_categorie_mixed_values_reported"][0]
        self.assertIn("AOC One|1", mixed_check.observed)
        self.assertIn("AOC One|1", metadata["transformation_parameters"]["mixed_categorie_groups"])

    def test_aoc_packaging_excludes_every_non_wine_category(self) -> None:
        raw = aoc_frame(
            [
                one_aoc("1", app="Wine AOC"),
                {**one_aoc("456", x0=20, app="Taureau de Camargue"), "categorie": "Bovin"},
                {**one_aoc("593", x0=40, app="Béa du Roussillon"), "categorie": "Tubercule"},
                {**one_aoc("999", x0=60, app="Vinaigre Test"), "categorie": "Vinaigre"},
                {**one_aoc("1000", x0=80, app="Missing Category"), "categorie": None},
            ]
        )

        packaged, checks, metadata = package_aoc_geometries(raw)

        self.assertEqual(packaged["app"].tolist(), ["Wine AOC"])
        self.assertTrue(
            packaged["categorie"].str.contains(r"\bVin\b", case=False, na=False).all()
        )
        retained_check = next(
            check for check in checks if check.name == "aoc_package_retained_categories_are_wine"
        )
        self.assertTrue(retained_check.passed)
        self.assertEqual(retained_check.observed, 0)
        category_filter = metadata["transformation_parameters"]["wine_category_filter"]
        self.assertEqual(category_filter["pattern"], r"\bVin\b")
        self.assertEqual(category_filter["excluded_rows"], 4)
        self.assertEqual(
            {record["categorie"] for record in category_filter["excluded_records"]},
            {"Bovin", "Tubercule", "Vinaigre", None},
        )

    def test_aoc_packaging_fails_when_source_has_no_wine_categories(self) -> None:
        non_wine = aoc_frame(
            [
                {**one_aoc("456", app="Taureau de Camargue"), "categorie": "Bovin"},
                {**one_aoc("593", x0=20, app="Béa du Roussillon"), "categorie": "Tubercule"},
            ]
        )

        with self.assertRaisesRegex(WinePipelineError, "no wine categories remain"):
            package_aoc_geometries(non_wine)

    def test_packaged_artifact_validation_rejects_non_wine_category(self) -> None:
        corrupted = aoc_frame(
            [{**one_aoc("456", app="Taureau de Camargue"), "categorie": "Bovin"}]
        )

        with mock.patch(
            "wine_pipeline.aoc_package.validate.gpd.read_file",
            return_value=corrupted,
        ):
            with self.assertRaisesRegex(WinePipelineError, "aoc_package_categories_are_wine"):
                validate_packaged_artifact(Path("corrupted.gpkg"), corrupted)

    def test_invalid_geometry_repair_and_remaining_invalid_failure(self) -> None:
        bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
        frame = gpd.GeoDataFrame([{"geometry": bowtie}], geometry="geometry", crs="EPSG:2154")
        repaired, counts = validate_and_repair_geometry(frame, "fixture")
        self.assertTrue(repaired.geometry.is_valid.all())
        self.assertEqual(counts["invalid_before_repair"], 1)

        with mock.patch("wine_pipeline.validation.make_valid", side_effect=lambda geometry: geometry):
            with self.assertRaisesRegex(WinePipelineError, "still contains"):
                validate_and_repair_geometry(frame, "fixture")

    def test_majority_overlap_tie_breaking_fallback_override_and_colour_failures(self) -> None:
        aocs = aoc_frame([one_aoc("1", dt="Unknown"), one_aoc("2", x0=20, dt="Tours"), one_aoc("3", x0=40, dt="Unknown")])
        regions = region_frame(
            [
                ("Beta", Polygon([(0, 0), (5, 0), (5, 10), (0, 10)])),
                ("Alpha", Polygon([(5, 0), (10, 0), (10, 10), (5, 10)])),
            ]
        )
        final, checks, metadata = enrich_aoc_regions(
            aocs.iloc[:2].copy(),
            regions,
            overrides_by_id={"1": "Jura"},
            fallbacks_by_dt={"Tours": "Loire"},
            colors={"Jura": "#88A378", "Loire": "#556B2F"},
        )
        row1 = final.set_index("id_app").loc["1"]
        row2 = final.set_index("id_app").loc["2"]
        self.assertEqual(row1["region"], "Jura")
        self.assertEqual(row1["region_method"], "explicit_override")
        self.assertEqual(row2["region"], "Loire")
        self.assertEqual(row2["region_method"], "delegation_fallback")
        self.assertEqual(metadata["regional_assignment"]["fallback_assignment_count"], 1)
        self.assertTrue(checks)

        majority, _, _ = enrich_aoc_regions(
            aocs.iloc[:1].copy(),
            regions,
            overrides_by_id={},
            fallbacks_by_dt={},
            colors={"Alpha": "#111111", "Beta": "#222222"},
        )
        self.assertEqual(majority.iloc[0]["region"], "Alpha")

        with self.assertRaisesRegex(WinePipelineError, "absent"):
            enrich_aoc_regions(aocs.iloc[:1].copy(), regions, overrides_by_id={"999": "Jura"}, fallbacks_by_dt={}, colors={"Alpha": "#111111"})

        with self.assertRaisesRegex(WinePipelineError, "remain unmatched"):
            enrich_aoc_regions(aocs.iloc[[2]].copy(), regions, overrides_by_id={}, fallbacks_by_dt={}, colors={})

        with self.assertRaisesRegex(WinePipelineError, "missing colours"):
            enrich_aoc_regions(aocs.iloc[:1].copy(), regions, overrides_by_id={}, fallbacks_by_dt={}, colors={})

    def test_enriched_schema_round_trip_and_deterministic_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            aocs = aoc_frame([one_aoc("2", x0=20, app="Loire Two"), one_aoc("1", app="Alsace grand cru First ou Alias")])
            regions = region_frame(
                [
                    ("region Loire", Polygon([(20, 0), (30, 0), (30, 10), (20, 10)])),
                    ("Alsace", Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])),
                ]
            )
            output = Path(tmp) / "aoc_regions.gpkg"
            first, _, _ = write_enriched_candidate(aocs, regions, output, overrides_by_id={}, fallbacks_by_dt={})
            second, _, _ = write_enriched_candidate(aocs, regions, output, overrides_by_id={}, fallbacks_by_dt={})
            self.assertEqual(first.columns.tolist(), ["id_app", "app", "display_name", "dt", "region", "region_method", "overlap_ratio", "colour", "categorie", "geometry"])
            self.assertEqual(first["id_app"].tolist(), second["id_app"].tolist())
            self.assertEqual(first.loc[first["id_app"] == "1", "display_name"].iloc[0], "First")
            self.assertTrue(output.is_file())

    def test_packaged_geopackage_round_trip_and_deterministic_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = aoc_frame([one_aoc("2", x0=20), one_aoc("1")])
            output = Path(tmp) / "aoc_packaged.gpkg"
            first, _, _ = write_packaged_candidate(raw, output)
            second, _, _ = write_packaged_candidate(raw, output)
            self.assertEqual(first.columns.tolist(), ["app", "id_app", "dt", "categorie", "geometry"])
            self.assertEqual(first["id_app"].tolist(), ["1", "2"])
            self.assertEqual(first["id_app"].tolist(), second["id_app"].tolist())
            self.assertEqual(sha256_file(output), sha256_file(output))


if __name__ == "__main__":
    unittest.main()
