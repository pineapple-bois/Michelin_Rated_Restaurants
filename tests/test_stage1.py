from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from data_pipeline.stage1.pipeline import (
    Stage1PublicationError,
    clean_snapshot,
    prepare_partitions,
    run_stage1,
    validate_stage1,
)
from data_pipeline.stage1.schema import spec_for_year
from data_pipeline.stage1.validation import Stage1ValidationError


def modern_raw() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Name": "France One", "Address": "1 rue A", "Location": "Paris, France",
                "Price": "€€", "Cuisine": "Modern", "WebsiteUrl": "https://fr.example",
                "Award": "1 Star", "GreenStar": 0, "Longitude": 2.35, "Latitude": 48.86,
            },
            {
                "Name": "Monaco One", "Address": "1 avenue B",
                "Location": "Monaco, Principality of Monaco", "Price": "€€€",
                "Cuisine": "Modern", "WebsiteUrl": None, "Award": "Selected Restaurants",
                "GreenStar": 0, "Longitude": 7.42, "Latitude": 43.74,
            },
            {
                "Name": "UK One", "Address": "1 High Street", "Location": "London, United Kingdom",
                "Price": "££", "Cuisine": "British", "WebsiteUrl": "https://uk.example",
                "Award": "Bib Gourmand", "GreenStar": 1, "Longitude": -0.12, "Latitude": 51.50,
            },
            {
                "Name": "US One", "Address": "1 Ocean Drive", "Location": "Miami, FL, USA",
                "Price": "$$$$", "Cuisine": "Seafood", "WebsiteUrl": "https://us.example",
                "Award": "2 Stars", "GreenStar": 0, "Longitude": -80.19, "Latitude": 25.76,
            },
            {
                "Name": "Singapore One", "Address": "1 Bay", "Location": "Singapore",
                "Price": "$$$", "Cuisine": "Asian", "WebsiteUrl": "https://sg.example",
                "Award": "3 Stars", "GreenStar": 0, "Longitude": 103.82, "Latitude": 1.35,
            },
        ]
    )


class Stage1UnitTests(unittest.TestCase):
    def test_modern_partitioning_and_location_parsing(self) -> None:
        partitions, validation = prepare_partitions(modern_raw(), year=2026)

        self.assertEqual(list(partitions), ["france", "monaco", "uk"])
        self.assertEqual(partitions["france"]["name"].tolist(), ["France One"])
        self.assertEqual(partitions["monaco"]["country"].tolist(), ["Principality of Monaco"])
        self.assertEqual(partitions["uk"]["stars"].tolist(), [0.5])
        self.assertEqual(validation["france"].rows, 1)

        cleaned = clean_snapshot(modern_raw(), spec_for_year(2026))
        self.assertEqual(cleaned.loc[3, "city"], "Miami")
        self.assertEqual(cleaned.loc[3, "country"], "USA")
        self.assertEqual(cleaned.loc[4, "country"], "Singapore")

    def test_missing_required_field_fails(self) -> None:
        raw = modern_raw().drop(columns="Award")
        with self.assertRaisesRegex(Stage1ValidationError, "missing required fields"):
            prepare_partitions(raw, year=2026)

    def test_unparsed_location_fails(self) -> None:
        raw = modern_raw()
        raw.loc[0, "Location"] = "Unmapped place"
        with self.assertRaisesRegex(Stage1ValidationError, "Location parsing failed"):
            prepare_partitions(raw, year=2026)

    def test_unexpected_award_fails(self) -> None:
        raw = modern_raw()
        raw.loc[0, "Award"] = "Five Hats"
        with self.assertRaisesRegex(Stage1ValidationError, "Unexpected award"):
            prepare_partitions(raw, year=2026)

    def test_invalid_coordinate_fails(self) -> None:
        raw = modern_raw()
        raw.loc[0, "Longitude"] = 200.0
        with self.assertRaisesRegex(Stage1ValidationError, "out-of-range"):
            prepare_partitions(raw, year=2026)

    def test_partial_coordinate_pair_fails(self) -> None:
        raw = modern_raw()
        raw.loc[0, "Longitude"] = None
        with self.assertRaisesRegex(Stage1ValidationError, "partial coordinate pairs"):
            prepare_partitions(raw, year=2026)

    def test_exact_duplicate_partition_row_fails(self) -> None:
        raw = pd.concat([modern_raw(), modern_raw().iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(Stage1ValidationError, "exact duplicate rows"):
            prepare_partitions(raw, year=2026)

    def test_modern_monaco_misclassified_as_france_fails(self) -> None:
        raw = modern_raw()
        raw.loc[1, "Location"] = "Monaco, France"
        with self.assertRaisesRegex(Stage1ValidationError, "classified as France"):
            prepare_partitions(raw, year=2026)

    def test_write_is_deterministic_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root = root / "raw"
            raw_root.mkdir()
            modern_raw().to_csv(raw_root / "michelin_data_2026.csv", index=False)

            first = run_stage1(year=2026, raw_root=raw_root, output_root=root / "first")
            second = run_stage1(year=2026, raw_root=raw_root, output_root=root / "second")
            for country in first.paths:
                self.assertEqual(
                    first.paths[country].read_bytes(),
                    second.paths[country].read_bytes(),
                )

            with self.assertRaises(FileExistsError):
                run_stage1(year=2026, raw_root=raw_root, output_root=root / "first")

            before_replace = {
                country: path.read_bytes()
                for country, path in first.paths.items()
            }
            replaced = run_stage1(
                year=2026,
                raw_root=raw_root,
                output_root=root / "first",
                replace=True,
            )
            self.assertEqual(
                before_replace,
                {country: path.read_bytes() for country, path in replaced.paths.items()},
            )

    def test_validate_only_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root = root / "raw"
            raw_root.mkdir()
            modern_raw().to_csv(raw_root / "michelin_data_2026.csv", index=False)

            result = validate_stage1(year=2026, raw_root=raw_root)

            self.assertEqual(result.paths, {})
            self.assertFalse((root / "partitions").exists())
            self.assertEqual(
                {country: summary.rows for country, summary in result.validation.items()},
                {"france": 1, "monaco": 1, "uk": 1},
            )

    def test_failed_validation_cannot_replace_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root = root / "raw"
            raw_root.mkdir()
            raw_path = raw_root / "michelin_data_2026.csv"
            modern_raw().to_csv(raw_path, index=False)
            original = run_stage1(
                year=2026,
                raw_root=raw_root,
                output_root=root / "partitions",
            )
            original_bytes = {
                country: path.read_bytes()
                for country, path in original.paths.items()
            }

            invalid = modern_raw()
            invalid.loc[0, "Award"] = "Five Hats"
            invalid.to_csv(raw_path, index=False)
            with self.assertRaises(Stage1ValidationError):
                run_stage1(
                    year=2026,
                    raw_root=raw_root,
                    output_root=root / "partitions",
                    replace=True,
                )

            self.assertEqual(
                original_bytes,
                {country: path.read_bytes() for country, path in original.paths.items()},
            )

    def test_publication_failure_rolls_back_all_country_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root = root / "raw"
            raw_root.mkdir()
            modern_raw().to_csv(raw_root / "michelin_data_2026.csv", index=False)
            output_root = root / "partitions"

            from data_pipeline.stage1 import pipeline

            real_replace = pipeline.os.replace
            call_count = 0

            def fail_on_second_publish(source: Path, destination: Path) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("simulated publication failure")
                real_replace(source, destination)

            with patch(
                "data_pipeline.stage1.pipeline.os.replace",
                side_effect=fail_on_second_publish,
            ):
                with self.assertRaisesRegex(Stage1PublicationError, "rolled back"):
                    run_stage1(
                        year=2026,
                        raw_root=raw_root,
                        output_root=output_root,
                    )

            expected = (
                output_root / "france" / "france_2026.csv",
                output_root / "monaco" / "monaco_2026.csv",
                output_root / "uk" / "uk_2026.csv",
            )
            self.assertFalse(any(path.exists() for path in expected))

    def test_failed_replacement_restores_complete_previous_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root = root / "raw"
            raw_root.mkdir()
            modern_raw().to_csv(raw_root / "michelin_data_2026.csv", index=False)
            output_root = root / "partitions"
            original = run_stage1(
                year=2026,
                raw_root=raw_root,
                output_root=output_root,
            )
            original_bytes = {
                country: path.read_bytes()
                for country, path in original.paths.items()
            }

            from data_pipeline.stage1 import pipeline

            real_replace = pipeline.os.replace
            call_count = 0

            def fail_on_second_publish(source: Path, destination: Path) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("simulated replacement failure")
                real_replace(source, destination)

            with patch(
                "data_pipeline.stage1.pipeline.os.replace",
                side_effect=fail_on_second_publish,
            ):
                with self.assertRaisesRegex(Stage1PublicationError, "rolled back"):
                    run_stage1(
                        year=2026,
                        raw_root=raw_root,
                        output_root=output_root,
                        replace=True,
                    )

            self.assertEqual(
                original_bytes,
                {country: path.read_bytes() for country, path in original.paths.items()},
            )

    def test_2022_price_schema_is_explicit(self) -> None:
        spec = spec_for_year(2022)
        self.assertIn("MaxPrice", spec.source_columns)
        self.assertEqual(spec.rename_columns["maxprice"], "price")


if __name__ == "__main__":
    unittest.main()
