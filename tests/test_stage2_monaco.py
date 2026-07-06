from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from data_pipeline.stage2.fidelity import compare_department_geojson, compare_restaurant_csv
from data_pipeline.stage2.monaco import (
    aggregate_monaco,
    prepare_monaco_restaurants,
    run_monaco_stage2,
    validate_monaco_stage2,
)
from data_pipeline.stage2.pipeline import Stage2PublicationError
from data_pipeline.stage2.validation import Stage2ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def partition() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "name": "Example", "address": "Hotel, 1 avenue Test, Monaco, 98000, Principality of Monaco",
            "city": "Monaco", "country": "Principality of Monaco", "price": "€€€",
            "cuisine": "Modern", "url": "https://example.test", "award": "1 Star",
            "greenstar": 1, "stars": 1.0, "longitude": 7.42, "latitude": 43.74,
        },
        {
            "name": "Selected", "address": "2 avenue Test, Monaco, 98000, France",
            "city": "Monaco", "country": "France", "price": "€€", "cuisine": "French",
            "url": None, "award": "Selected Restaurants", "greenstar": 0, "stars": 0.25,
            "longitude": 7.43, "latitude": 43.75,
        },
    ])


def geometry() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"name": ["Monaco"], "geometry": [box(7.4, 43.7, 7.5, 43.8)]}, crs="EPSG:4326")


class MonacoStage2Tests(unittest.TestCase):
    def test_prepares_addresses_and_fixed_application_fields(self) -> None:
        result = prepare_monaco_restaurants(partition(), year=2026)
        self.assertEqual(result.loc[0, "address"], "Hotel, 1 avenue Test")
        self.assertEqual(result["location"].tolist(), ["Monaco, 98000", "Monaco, 98000"])
        self.assertTrue(result["department_num"].eq("98").all())
        self.assertTrue(result["region"].eq("Provence-Alpes-Côte d'Azur").all())

    def test_malformed_address_fails(self) -> None:
        source = partition()
        source.loc[0, "address"] = "Monaco"
        with self.assertRaisesRegex(Stage2ValidationError, "cannot be split"):
            prepare_monaco_restaurants(source, year=2026)

    def test_unexpected_award_value_fails_aggregation(self) -> None:
        restaurants = prepare_monaco_restaurants(partition(), year=2026)
        restaurants.loc[0, "stars"] = 4.0
        with self.assertRaisesRegex(Stage2ValidationError, "unexpected stars"):
            aggregate_monaco(restaurants, geometry(), year=2026)

    def test_aggregate_aligns_with_department_schema(self) -> None:
        restaurants = prepare_monaco_restaurants(partition(), year=2026)
        product = aggregate_monaco(restaurants, geometry(), year=2026)
        self.assertEqual(len(product), 1)
        self.assertEqual(product.loc[0, "code"], "98")
        self.assertEqual(product.loc[0, "selected"], 1)
        self.assertEqual(product.loc[0, "1_star"], 1)
        self.assertEqual(product.loc[0, "total_stars"], 1)
        self.assertEqual(product.loc[0, "green_stars"], 1)
        self.assertTrue(all(product.loc[0, column] == 0.0 for column in (
            "GDP_millions(€)", "municipal_population", "area(sq_km)"
        )))

    def test_validate_only_does_not_publish(self) -> None:
        result = validate_monaco_stage2(year=2026)
        self.assertEqual(result.paths, {})
        self.assertEqual(result.validation.restaurant_rows, 17)
        self.assertEqual(result.validation.aggregate_rows, 1)

    def test_publication_failure_rolls_back_both_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "products"
            from data_pipeline.stage2 import monaco
            real_replace = monaco.os.replace
            calls = 0

            def fail_second(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated failure")
                real_replace(source, destination)

            with patch("data_pipeline.stage2.monaco.os.replace", side_effect=fail_second):
                with self.assertRaisesRegex(Stage2PublicationError, "rolled back"):
                    run_monaco_stage2(year=2026, output_root=output)
            root = output / "france" / "2026"
            self.assertFalse((root / "monaco_restaurants.csv").exists())
            self.assertFalse((root / "geodata" / "monaco_restaurants.geojson").exists())

    def test_2025_and_2026_match_legacy_products(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for year in (2025, 2026):
                with self.subTest(year=year):
                    result = run_monaco_stage2(year=year, output_root=output)
                    baseline = REPOSITORY_ROOT / "Years" / str(year) / "data" / "France"
                    csv = compare_restaurant_csv(
                        result.paths["restaurants"], baseline / "monaco_restaurants.csv"
                    )
                    geo = compare_department_geojson(
                        result.paths["aggregate"], baseline / "geodata" / "monaco_restaurants.geojson"
                    )
                    self.assertEqual(csv.summary, "byte-identical")
                    self.assertEqual(geo.summary, "byte-identical")

    def test_replacement_is_deterministic_and_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            first = run_monaco_stage2(year=2026, output_root=output)
            before = {name: path.read_bytes() for name, path in first.paths.items()}
            with self.assertRaises(FileExistsError):
                run_monaco_stage2(year=2026, output_root=output)
            replaced = run_monaco_stage2(year=2026, output_root=output, replace=True)
            self.assertEqual(before, {name: path.read_bytes() for name, path in replaced.paths.items()})


if __name__ == "__main__":
    unittest.main()
