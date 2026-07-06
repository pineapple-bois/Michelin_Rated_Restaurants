from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from data_pipeline.stage2.pipeline import (
    Stage2PublicationError,
    aggregate_departments,
    aggregate_regions,
    enrich_restaurants,
    run_stage2,
    validate_stage2,
)
from data_pipeline.stage2.validation import (
    Stage2ValidationError,
    validate_reference_data,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def department_reference() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ("75", "Paris", "Paris", "Île-de-France"),
            ("2A", "Corse-du-Sud", "Ajaccio", "Corsica"),
            ("2B", "Haute-Corse", "Bastia", "Corsica"),
        ],
        columns=["department_num", "department", "capital", "region"],
    )


def departmental_statistics() -> pd.DataFrame:
    rows = []
    for index, reference in department_reference().iterrows():
        rows.append(
            {
                **reference.to_dict(),
                "GDP_millions(€)": 1000.0 + index,
                "GDP_per_capita(€)": 30000.0,
                "poverty_rate(%)": 10.0,
                "average_annual_unemployment_rate(%)": 5.0,
                "average_net_hourly_wage(€)": 15.0,
                "municipal_population": 100000.0,
                "population_density(inhabitants/sq_km)": 100.0,
                "area(sq_km)": 1000.0,
            }
        )
    return pd.DataFrame(rows)


def department_geometry() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "code": ["2A", "2B", "75"],
            "nom": ["Corse-du-Sud", "Haute-Corse", "Paris"],
            "geometry": [box(8, 41, 9, 42), box(9, 42, 10, 43), box(2, 48, 3, 49)],
        },
        crs="EPSG:4326",
    )


def region_geometry() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "code": ["11", "94"],
            "nom": ["Île-de-France", "Corse"],
            "geometry": [box(1, 47, 4, 50), box(8, 41, 10, 43)],
        },
        crs="EPSG:4326",
    )


def france_partition() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "name": "Paris One", "address": "1 rue\xa0A, Paris, 75001, France",
                "city": "Paris", "country": "France", "price": "€€",
                "cuisine": "Modern", "url": "https://paris.example", "award": "1 Star",
                "greenstar": 0, "stars": 1.0, "longitude": 2.35, "latitude": 48.86,
            },
            {
                "name": "South Corsica", "address": "1 rue B, Ajaccio, 20100, France",
                "city": "Ajaccio", "country": "France", "price": "€€",
                "cuisine": "Corsican", "url": None, "award": "Selected Restaurants",
                "greenstar": 1, "stars": 0.25, "longitude": 8.74, "latitude": 41.92,
            },
            {
                "name": "North Corsica", "address": "1 rue C, Bastia, 20200, France",
                "city": "Bastia", "country": "France", "price": "€€€",
                "cuisine": "Corsican", "url": "https://bastia.example", "award": "Bib Gourmand",
                "greenstar": 0, "stars": 0.5, "longitude": 9.45, "latitude": 42.70,
            },
        ]
    )


class Stage2UnitTests(unittest.TestCase):
    def test_address_normalization_and_corsica_assignment(self) -> None:
        enriched = enrich_restaurants(
            france_partition(),
            department_reference(),
            year=2026,
        )

        self.assertEqual(enriched["department_num"].tolist(), ["75", "2A", "2B"])
        self.assertEqual(enriched["location"].tolist(), ["Paris, 75001", "Ajaccio, 20100", "Bastia, 20200"])
        self.assertEqual(enriched.loc[0, "address"], "1 rueA")
        self.assertNotIn("country", enriched.columns)

    def test_department_counts_coordinates_and_geometry(self) -> None:
        enriched = enrich_restaurants(france_partition(), department_reference(), year=2026)
        product = aggregate_departments(
            enriched,
            departmental_statistics(),
            department_geometry(),
            year=2026,
        )

        self.assertEqual(product["code"].tolist(), ["2A", "2B", "75"])
        self.assertEqual(product["region"].tolist(), ["Corse", "Corse", "Île-de-France"])
        paris = product.set_index("code").loc["75"]
        self.assertEqual(paris["1_star"], 1)
        self.assertEqual(paris["total_stars"], 1)
        self.assertIn("48.86", paris["locations"])
        south = product.set_index("code").loc["2A"]
        self.assertEqual(south["selected"], 1)
        self.assertEqual(south["green_stars"], 1)

    def test_region_counts_weighted_statistics_and_geometry(self) -> None:
        enriched = enrich_restaurants(france_partition(), department_reference(), year=2026)
        product = aggregate_regions(
            enriched, departmental_statistics(), region_geometry(), year=2026
        )

        self.assertEqual(product["region"].tolist(), ["Île-de-France", "Corse"])
        corsica = product.set_index("region").loc["Corse"]
        self.assertEqual(corsica["selected"], 1)
        self.assertEqual(corsica["bib_gourmand"], 1)
        self.assertEqual(corsica["municipal_population"], 200000.0)
        self.assertIn("41.92", corsica["locations"])

    def test_ambiguous_address_fails(self) -> None:
        partition = france_partition()
        partition.loc[0, "address"] = "Paris, France"
        with self.assertRaisesRegex(Stage2ValidationError, "exactly one postal code"):
            enrich_restaurants(partition, department_reference(), year=2026)

    def test_unmatched_department_fails(self) -> None:
        partition = france_partition()
        partition.loc[0, "address"] = "1 rue A, Paris, 99000, France"
        with self.assertRaisesRegex(Stage2ValidationError, "Unmatched restaurant"):
            enrich_restaurants(partition, department_reference(), year=2026)

    def test_reference_code_mismatch_fails(self) -> None:
        geometry = department_geometry().iloc[:2].copy()
        with self.assertRaisesRegex(Stage2ValidationError, "code sets differ"):
            validate_reference_data(
                department_reference(),
                departmental_statistics(),
                geometry,
            )

    def test_validate_only_does_not_publish(self) -> None:
        result = validate_stage2(year=2026)
        self.assertEqual(result.paths, {})
        self.assertEqual(result.validation.restaurant_rows, 3055)
        self.assertEqual(result.validation.department_rows, 96)
        self.assertEqual(result.validation.region_rows, 13)

    def test_publication_failure_leaves_no_partial_product_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "products"
            from data_pipeline.stage2 import pipeline

            real_replace = pipeline.os.replace
            call_count = 0

            def fail_on_second_publish(source: Path, destination: Path) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("simulated publication failure")
                real_replace(source, destination)

            with patch(
                "data_pipeline.stage2.pipeline.os.replace",
                side_effect=fail_on_second_publish,
            ):
                with self.assertRaisesRegex(Stage2PublicationError, "rolled back"):
                    run_stage2(year=2026, output_root=output_root)

            expected_root = output_root / "france" / "2026"
            self.assertFalse((expected_root / "all_restaurants.csv").exists())
            self.assertFalse(
                (expected_root / "geodata" / "department_restaurants.geojson").exists()
            )
            self.assertFalse(
                (expected_root / "geodata" / "region_restaurants.geojson").exists()
            )


if __name__ == "__main__":
    unittest.main()
