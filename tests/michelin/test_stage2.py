from __future__ import annotations

import hashlib
import json
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
    load_insee_product,
    resolve_insee_product,
    run_stage2,
    validate_stage2,
)
from data_pipeline.stage2.validation import (
    Stage2ValidationError,
    validate_reference_data,
)
from tests.support import REPOSITORY_ROOT


def department_reference() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ("75", "Paris", "Paris", "Île-de-France"),
            ("2A", "Corse-du-Sud", "Ajaccio", "Corsica"),
            ("2B", "Haute-Corse", "Bastia", "Corsica"),
        ],
        columns=["department_num", "department", "capital", "region"],
    )


def insee_product_statistics() -> pd.DataFrame:
    rows = []
    for index, reference in department_reference().iterrows():
        population = 100000 + index
        gdp = 1000.0 + index
        rows.append(
            {
                "department_code": reference["department_num"],
                "department_name": reference["department"],
                "capital": reference["capital"],
                "region": reference["region"],
                "reference_year": 2023,
                "average_net_monthly_wage_fte_eur": 2500.0 + index,
                "median_living_standard_eur": 24000.0 + index,
                "poverty_rate_percent": 10.0 + index,
                "census_unemployment_rate_15_64_percent": 5.0 + index,
                "municipal_population": population,
                "area_sq_km": 1000.0 + index,
                "population_density_per_sq_km": population / (1000.0 + index),
                "gdp_current_prices_million_eur": gdp,
                "gdp_per_capita_eur": gdp * 1_000_000 / population,
            }
        )
    return pd.DataFrame(rows)


def write_insee_product(root: Path, year: int, frame: pd.DataFrame) -> tuple[Path, Path]:
    product_root = root / str(year)
    product_root.mkdir(parents=True, exist_ok=True)
    csv_path = product_root / f"france_departments_{year}.csv"
    manifest_path = product_root / f"manifest_{year}.json"
    frame.to_csv(csv_path, index=False, lineterminator="\n")
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(
            {
                "reference_year": year,
                "rows": len(frame),
                "schema": frame.columns.tolist(),
                "output_hash": digest,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return csv_path, manifest_path


def metropolitan_codes() -> list[str]:
    codes = [f"{number:02d}" for number in range(1, 20)]
    codes.extend(["2A", "2B"])
    codes.extend(f"{number:02d}" for number in range(21, 96))
    return codes


def full_insee_product_statistics(year: int = 2023) -> pd.DataFrame:
    rows = []
    for index, code in enumerate(metropolitan_codes(), start=1):
        population = 100000 + index
        area = 1000.0 + index
        gdp = 1000.0 + index
        rows.append(
            {
                "department_code": code,
                "department_name": f"Dept {code}",
                "capital": f"Capital {code}",
                "region": f"Region {index % 3}",
                "reference_year": year,
                "average_net_monthly_wage_fte_eur": 2500.0 + index,
                "median_living_standard_eur": 24000.0 + index,
                "poverty_rate_percent": 10.0 + index / 100,
                "census_unemployment_rate_15_64_percent": 5.0 + index / 100,
                "municipal_population": population,
                "area_sq_km": area,
                "population_density_per_sq_km": population / area,
                "gdp_current_prices_million_eur": gdp,
                "gdp_per_capita_eur": gdp * 1_000_000 / population,
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
            insee_product_statistics(),
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
            enriched, insee_product_statistics(), region_geometry(), year=2026
        )

        self.assertEqual(product["region"].tolist(), ["Île-de-France", "Corse"])
        corsica = product.set_index("region").loc["Corse"]
        self.assertEqual(corsica["selected"], 1)
        self.assertEqual(corsica["bib_gourmand"], 1)
        self.assertEqual(corsica["municipal_population"], 200003)
        expected_gdp = 1001.0 + 1002.0
        self.assertEqual(corsica["gdp_current_prices_million_eur"], expected_gdp)
        self.assertAlmostEqual(
            corsica["gdp_per_capita_eur"],
            expected_gdp * 1_000_000 / 200003,
        )
        self.assertAlmostEqual(
            corsica["poverty_rate_percent"],
            ((11.0 * 100001) + (12.0 * 100002)) / 200003,
        )
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
                insee_product_statistics(),
                geometry,
            )

    def test_insee_product_latest_year_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame_2023 = full_insee_product_statistics()
            write_insee_product(root, 2023, frame_2023)
            frame_2024 = frame_2023.assign(reference_year=2024)
            write_insee_product(root, 2024, frame_2024)
            selection = resolve_insee_product(product_root=root)
            self.assertEqual(selection.year, 2024)
            loaded = load_insee_product(selection)
            self.assertEqual(set(loaded["reference_year"]), {2024})

    def test_insee_product_explicit_year_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_insee_product(root, 2023, full_insee_product_statistics())
            write_insee_product(root, 2024, full_insee_product_statistics(year=2024))
            selection = resolve_insee_product(product_root=root, insee_year=2023)
            self.assertEqual(selection.year, 2023)

    def test_insee_product_malformed_highest_year_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_insee_product(root, 2023, full_insee_product_statistics())
            (root / "2024").mkdir()
            with self.assertRaisesRegex(FileNotFoundError, "2024 is incomplete"):
                resolve_insee_product(product_root=root)

    def test_insee_product_missing_manifest_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path, manifest_path = write_insee_product(root, 2023, full_insee_product_statistics())
            manifest_path.unlink()
            self.assertTrue(csv_path.is_file())
            with self.assertRaisesRegex(FileNotFoundError, "manifest_2023.json"):
                resolve_insee_product(product_root=root, insee_year=2023)

    def test_insee_product_year_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_insee_product(root, 2023, full_insee_product_statistics(year=2022))
            with self.assertRaisesRegex(Stage2ValidationError, "CSV reference_year"):
                load_insee_product(resolve_insee_product(product_root=root, insee_year=2023))

    def test_insee_product_hash_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path, _manifest_path = write_insee_product(root, 2023, full_insee_product_statistics())
            csv_path.write_text(csv_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(Stage2ValidationError, "hash"):
                load_insee_product(resolve_insee_product(product_root=root, insee_year=2023))

    def test_insee_product_duplicate_department_codes_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame = full_insee_product_statistics()
            frame.loc[1, "department_code"] = frame.loc[0, "department_code"]
            write_insee_product(root, 2023, frame)
            with self.assertRaisesRegex(Stage2ValidationError, "96 unique department_code"):
                load_insee_product(resolve_insee_product(product_root=root, insee_year=2023))

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
