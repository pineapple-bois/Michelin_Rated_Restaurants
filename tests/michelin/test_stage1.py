from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from data_pipeline.stage1 import acquisition
from data_pipeline.stage1.acquisition import (
    compare_france_partitions,
    evaluate_france_acceptance,
    latest_accepted_france_year,
    run_stage1_acquisition,
)
from data_pipeline.changes import pipeline as changes_pipeline
from data_pipeline.stage1.pipeline import (
    Stage1PublicationError,
    clean_snapshot,
    partition_paths,
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


def raw_restaurant(
    name: str,
    *,
    address: str | None = None,
    city: str = "Paris",
    country: str = "France",
    award: str = "1 Star",
    greenstar: int = 0,
    url: str | None = None,
    longitude: float = 2.35,
    latitude: float = 48.86,
) -> dict:
    return {
        "Name": name,
        "Address": address or f"{name} address 75001",
        "Location": f"{city}, {country}",
        "Price": "€€",
        "Cuisine": "Modern",
        "WebsiteUrl": url if url is not None else f"https://{name.lower().replace(' ', '-')}.example",
        "Award": award,
        "GreenStar": greenstar,
        "Longitude": longitude,
        "Latitude": latitude,
    }


def raw_with_france(names: list[str], *, awards: dict[str, str] | None = None) -> pd.DataFrame:
    awards = awards or {}
    rows = [
        raw_restaurant(name, award=awards.get(name, "1 Star"))
        for name in names
    ]
    rows.append(
        raw_restaurant(
            "Monaco Stable",
            city="Monaco",
            country="Principality of Monaco",
            award="Selected Restaurants",
        )
    )
    rows.append(
        raw_restaurant(
            "UK Stable",
            city="London",
            country="United Kingdom",
            award="Bib Gourmand",
        )
    )
    return pd.DataFrame(rows)


def write_accepted_stage1(root: Path, *, year: int, raw: pd.DataFrame) -> None:
    raw_root = root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    raw.to_csv(raw_root / f"michelin_data_{year}.csv", index=False)
    run_stage1(year=year, raw_root=raw_root, output_root=root / "partitions")


def make_downloader(raw: pd.DataFrame, seen_paths: list[Path] | None = None):
    def downloader(destination: Path):
        if seen_paths is not None:
            seen_paths.append(destination)
        raw.to_csv(destination, index=False)
        return acquisition.SourceInfo("https://example.test/michelin.csv", "abc123")

    return downloader


def broad_accepted_transition() -> tuple[pd.DataFrame, pd.DataFrame]:
    previous_names = [f"Old {index}" for index in range(300)]
    candidate_names = previous_names[:260] + [f"New {index}" for index in range(30)]
    awards = {name: "2 Stars" for name in previous_names[:10]}
    return raw_with_france(previous_names), raw_with_france(candidate_names, awards=awards)


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

    def test_latest_accepted_france_year_is_derived_from_partitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "partitions" / "france"
            root.mkdir(parents=True)
            (root / "france_2024.csv").write_text("x\n", encoding="utf-8")
            (root / "france_2026.csv").write_text("x\n", encoding="utf-8")
            (root / "notes.csv").write_text("x\n", encoding="utf-8")

            self.assertEqual(latest_accepted_france_year(root.parent), 2026)

    def test_before_april_no_download_or_workspace_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_accepted_stage1(root, year=2026, raw=raw_with_france(["Stable"]))

            with patch("data_pipeline.stage1.acquisition.tempfile.TemporaryDirectory") as tempdir:
                result = run_stage1_acquisition(
                    raw_root=root / "raw",
                    output_root=root / "partitions",
                    today=date(2027, 3, 31),
                    downloader=make_downloader(raw_with_france(["Should Not Download"])),
                )

            self.assertEqual(result.status, "not-yet")
            tempdir.assert_not_called()
            self.assertEqual(
                sorted(path.name for path in (root / "raw").iterdir()),
                ["michelin_data_2026.csv"],
            )

    def test_existing_candidate_assets_block_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_accepted_stage1(root, year=2026, raw=raw_with_france(["Stable"]))
            (root / "raw" / "michelin_data_2027.csv").write_text("existing\n", encoding="utf-8")
            downloader = unittest.mock.Mock()

            result = run_stage1_acquisition(
                raw_root=root / "raw",
                output_root=root / "partitions",
                today=date(2027, 4, 1),
                downloader=downloader,
            )

            self.assertEqual(result.status, "already-exists")
            downloader.assert_not_called()

    def test_acquisition_uses_temporary_workspace(self) -> None:
        previous_raw, candidate_raw = broad_accepted_transition()
        seen_paths: list[Path] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_accepted_stage1(root, year=2026, raw=previous_raw)

            result = run_stage1_acquisition(
                raw_root=root / "raw",
                output_root=root / "partitions",
                today=date(2027, 4, 1),
                downloader=make_downloader(candidate_raw, seen_paths),
            )

            self.assertEqual(result.status, "accepted")
            self.assertEqual(len(seen_paths), 1)
            self.assertNotEqual(seen_paths[0].parent, root / "raw")
            self.assertFalse(seen_paths[0].exists())

    def test_acquisition_reuses_existing_partition_logic(self) -> None:
        previous_raw, candidate_raw = broad_accepted_transition()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_accepted_stage1(root, year=2026, raw=previous_raw)

            with patch(
                "data_pipeline.stage1.acquisition.prepare_partitions",
                wraps=acquisition.prepare_partitions,
            ) as wrapped:
                run_stage1_acquisition(
                    raw_root=root / "raw",
                    output_root=root / "partitions",
                    today=date(2027, 4, 1),
                    downloader=make_downloader(candidate_raw),
                )

            wrapped.assert_called_once()

    def test_france_comparison_identifies_additions(self) -> None:
        previous = prepare_partitions(raw_with_france(["A", "B"]), year=2026)[0]["france"]
        candidate = prepare_partitions(raw_with_france(["A", "B", "C"]), year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.added_restaurants, 1)
        self.assertEqual(comparison.removed_restaurants, 0)
        self.assertEqual(comparison.award_label_changes, 0)

    def test_france_comparison_identifies_removals(self) -> None:
        previous = prepare_partitions(raw_with_france(["A", "B"]), year=2026)[0]["france"]
        candidate = prepare_partitions(raw_with_france(["A"]), year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.added_restaurants, 0)
        self.assertEqual(comparison.removed_restaurants, 1)

    def test_france_comparison_identifies_award_label_changes(self) -> None:
        previous = prepare_partitions(raw_with_france(["A"]), year=2026)[0]["france"]
        candidate = prepare_partitions(
            raw_with_france(["A"], awards={"A": "2 Stars"}),
            year=2026,
        )[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.award_label_changes, 1)
        self.assertEqual(comparison.award_categories_changed, ("1 Star", "2 Stars"))

    def test_punctuation_only_address_changes_do_not_create_add_remove(self) -> None:
        previous_raw = pd.DataFrame([
            raw_restaurant(
                "Stable",
                address="12, rue de Test - 75001",
                url="https://guide.test/stable",
            )
        ])
        candidate_raw = pd.DataFrame([
            raw_restaurant(
                "Stable",
                address="12 rue de Test 75001",
                url="https://guide.test/stable",
            )
        ])
        previous = prepare_partitions(previous_raw, year=2026)[0]["france"]
        candidate = prepare_partitions(candidate_raw, year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.matched_restaurants, 1)
        self.assertEqual(comparison.added_restaurants, 0)
        self.assertEqual(comparison.removed_restaurants, 0)
        self.assertEqual(comparison.other_changed_rows, 0)

    def test_case_whitespace_and_accent_normalization_are_consistent(self) -> None:
        previous_raw = pd.DataFrame([
            raw_restaurant(
                "Café Été",
                address="1 rue Accent 75001",
                url="https://old.test/cafe",
            )
        ])
        candidate_raw = pd.DataFrame([
            raw_restaurant(
                "  cafe   ete  ",
                address="1 rue Accent 75001",
                url="https://new.test/cafe",
            )
        ])
        previous = prepare_partitions(previous_raw, year=2026)[0]["france"]
        candidate = prepare_partitions(candidate_raw, year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.matched_restaurants, 1)
        self.assertEqual(comparison.unchanged_restaurants, 1)
        self.assertEqual(comparison.other_changed_rows, 0)

    def test_url_changes_do_not_create_new_restaurant_with_stronger_identity(self) -> None:
        previous_raw = pd.DataFrame([
            raw_restaurant("URL Stable", url="https://old.test/restaurant")
        ])
        candidate_raw = pd.DataFrame([
            raw_restaurant("URL Stable", url="https://new.test/restaurant")
        ])
        previous = prepare_partitions(previous_raw, year=2026)[0]["france"]
        candidate = prepare_partitions(candidate_raw, year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.matched_restaurants, 1)
        self.assertEqual(comparison.added_restaurants, 0)
        self.assertEqual(comparison.removed_restaurants, 0)

    def test_coordinate_changes_do_not_count_as_award_label_changes(self) -> None:
        previous_raw = pd.DataFrame([
            raw_restaurant("Coordinate Stable", longitude=2.35, latitude=48.86)
        ])
        candidate_raw = pd.DataFrame([
            raw_restaurant("Coordinate Stable", longitude=2.36, latitude=48.87)
        ])
        previous = prepare_partitions(previous_raw, year=2026)[0]["france"]
        candidate = prepare_partitions(candidate_raw, year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.matched_restaurants, 1)
        self.assertEqual(comparison.award_label_changes, 0)
        self.assertEqual(comparison.other_changed_rows, 0)

    def test_award_change_is_found_after_reconciliation(self) -> None:
        previous_raw = pd.DataFrame([
            raw_restaurant("Award Stable", award="1 Star", url="https://old.test/a")
        ])
        candidate_raw = pd.DataFrame([
            raw_restaurant("Award Stable", award="2 Stars", url="https://new.test/a")
        ])
        previous = prepare_partitions(previous_raw, year=2026)[0]["france"]
        candidate = prepare_partitions(candidate_raw, year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.matched_restaurants, 1)
        self.assertEqual(comparison.award_label_changes, 1)
        self.assertEqual(comparison.award_label_transition_counts, {"1 Star -> 2 Stars": 1})

    def test_duplicate_names_are_not_forced_into_incorrect_matches(self) -> None:
        previous_raw = pd.DataFrame([
            raw_restaurant("Duplicate", address="1 rue Old 75001", url=None),
            raw_restaurant("Duplicate", address="2 rue Old 75002", url=None),
        ])
        candidate_raw = pd.DataFrame([
            raw_restaurant("Duplicate", address="3 rue New 75003", url=None),
            raw_restaurant("Duplicate", address="4 rue New 75004", url=None),
        ])
        previous = prepare_partitions(previous_raw, year=2026)[0]["france"]
        candidate = prepare_partitions(candidate_raw, year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.matched_restaurants, 0)
        self.assertEqual(comparison.added_restaurants, 2)
        self.assertEqual(comparison.removed_restaurants, 2)
        self.assertGreater(comparison.duplicate_conflicts, 0)

    def test_ambiguous_matches_remain_visible(self) -> None:
        previous_raw = pd.DataFrame([
            raw_restaurant("Pavyllon", address="1 rue A 75001", url="https://old.test")
        ])
        candidate_raw = pd.DataFrame([
            raw_restaurant(
                "Pavyllon Paris",
                address="2 rue B 75002",
                url="https://new.test",
            )
        ])
        previous = prepare_partitions(previous_raw, year=2026)[0]["france"]
        candidate = prepare_partitions(candidate_raw, year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.matched_restaurants, 0)
        self.assertEqual(comparison.ambiguous_matches, 1)

    def test_schema_only_changes_are_reported_separately(self) -> None:
        previous = prepare_partitions(raw_with_france(["A"]), year=2026)[0]["france"]
        candidate = previous.copy()
        candidate["scraper_batch"] = "new"

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.added_columns, ("scraper_batch",))
        self.assertEqual(comparison.award_label_changes, 0)
        self.assertEqual(comparison.other_changed_rows, 0)

    def test_global_non_material_field_change_does_not_mark_every_restaurant_changed(self) -> None:
        previous = prepare_partitions(raw_with_france(["A", "B", "C"]), year=2026)[0]["france"]
        candidate = previous.copy()
        candidate["url"] = ["https://new.test/a", "https://new.test/b", "https://new.test/c"]
        candidate["longitude"] = [2.41, 2.42, 2.43]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.matched_restaurants, 3)
        self.assertEqual(comparison.award_label_changes, 0)
        self.assertEqual(comparison.other_changed_rows, 0)
        self.assertEqual(comparison.unchanged_restaurants, 3)

    def test_poor_match_quality_prevents_publication_through_acceptance_policy(self) -> None:
        previous_raw = raw_with_france([f"Old {index}" for index in range(100)])
        candidate_raw = raw_with_france([f"New {index}" for index in range(100)])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_accepted_stage1(root, year=2026, raw=previous_raw)

            with patch(
                "data_pipeline.stage1.acquisition.evaluate_france_acceptance",
                wraps=evaluate_france_acceptance,
            ) as policy:
                result = run_stage1_acquisition(
                    raw_root=root / "raw",
                    output_root=root / "partitions",
                    today=date(2027, 4, 1),
                    downloader=make_downloader(candidate_raw),
                )

            self.assertEqual(result.status, "comparison-unreliable")
            self.assertFalse(result.comparison.comparison_reliable)
            policy.assert_called_once()
            self.assertFalse(result.acceptance.accepted)
            self.assertEqual(result.acceptance.reason, result.comparison.reliability_reason)

    def test_unreliable_comparison_cannot_be_accepted_by_policy(self) -> None:
        previous = prepare_partitions(
            raw_with_france([f"Old {index}" for index in range(100)]),
            year=2026,
        )[0]["france"]
        candidate = prepare_partitions(
            raw_with_france(
                [f"New {index}" for index in range(100)],
                awards={f"New {index}": "2 Stars" for index in range(20)},
            ),
            year=2026,
        )[0]["france"]
        comparison = compare_france_partitions(previous, candidate)

        acceptance = evaluate_france_acceptance(comparison)

        self.assertFalse(comparison.comparison_reliable)
        self.assertFalse(acceptance.accepted)
        self.assertEqual(acceptance.reason, comparison.reliability_reason)

    def test_stage1_and_guide_changes_use_the_same_reconciliation_helper(self) -> None:
        self.assertIs(acquisition.reconcile_restaurants, changes_pipeline.reconcile_restaurants)

    def test_non_material_row_changes_are_not_award_label_changes(self) -> None:
        previous = prepare_partitions(raw_with_france(["A"]), year=2026)[0]["france"]
        candidate_raw = raw_with_france(["A"])
        candidate_raw.loc[0, "WebsiteUrl"] = "https://changed.example"
        candidate = prepare_partitions(candidate_raw, year=2026)[0]["france"]

        comparison = compare_france_partitions(previous, candidate)

        self.assertEqual(comparison.award_label_changes, 0)
        self.assertEqual(comparison.other_changed_rows, 0)

    def test_minor_selected_restaurant_churn_is_rejected(self) -> None:
        stable = [f"Stable {index}" for index in range(100)]
        selected = [f"Selected {index}" for index in range(25)]
        previous = prepare_partitions(raw_with_france(stable), year=2026)[0]["france"]
        candidate = prepare_partitions(
            raw_with_france(
                [*stable, *selected],
                awards={name: "Selected Restaurants" for name in selected},
            ),
            year=2026,
        )[0]["france"]

        acceptance = evaluate_france_acceptance(compare_france_partitions(previous, candidate))

        self.assertFalse(acceptance.accepted)
        self.assertIn("Selected Restaurants", acceptance.reason)

    def test_representative_broad_annual_transition_is_accepted(self) -> None:
        previous_raw, candidate_raw = broad_accepted_transition()
        previous_france = prepare_partitions(previous_raw, year=2026)[0]["france"]
        candidate_france = prepare_partitions(
            candidate_raw,
            year=2026,
        )[0]["france"]

        acceptance = evaluate_france_acceptance(
            compare_france_partitions(previous_france, candidate_france)
        )

        self.assertTrue(acceptance.accepted)

    def test_france_rejection_prevents_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stable = [f"Stable {index}" for index in range(100)]
            selected = [f"Selected {index}" for index in range(25)]
            write_accepted_stage1(root, year=2026, raw=raw_with_france(stable))
            result = run_stage1_acquisition(
                raw_root=root / "raw",
                output_root=root / "partitions",
                today=date(2027, 4, 1),
                downloader=make_downloader(
                    raw_with_france(
                        [*stable, *selected],
                        awards={name: "Selected Restaurants" for name in selected},
                    )
                ),
            )

            self.assertEqual(result.status, "rejected")
            self.assertFalse((root / "raw" / "michelin_data_2027.csv").exists())
            self.assertFalse(any(path.exists() for path in partition_paths(2027, root / "partitions").values()))

    def test_france_acceptance_publishes_raw_and_every_partition(self) -> None:
        previous_raw, candidate_raw = broad_accepted_transition()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_accepted_stage1(root, year=2026, raw=previous_raw)
            result = run_stage1_acquisition(
                raw_root=root / "raw",
                output_root=root / "partitions",
                today=date(2027, 4, 1),
                downloader=make_downloader(candidate_raw),
            )

            self.assertEqual(result.status, "accepted")
            self.assertTrue((root / "raw" / "michelin_data_2027.csv").is_file())
            self.assertEqual(set(result.paths), {"france", "monaco", "uk"})
            self.assertTrue(all(path.is_file() for path in result.paths.values()))

    def test_rejected_temporary_files_are_removed(self) -> None:
        seen_paths: list[Path] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_accepted_stage1(root, year=2026, raw=raw_with_france(["A", "B"]))
            result = run_stage1_acquisition(
                raw_root=root / "raw",
                output_root=root / "partitions",
                today=date(2027, 4, 1),
                downloader=make_downloader(raw_with_france(["A", "B"]), seen_paths),
            )

            self.assertEqual(result.status, "rejected")
            self.assertEqual(len(seen_paths), 1)
            self.assertFalse(seen_paths[0].exists())

    def test_publication_failure_leaves_canonical_data_unchanged(self) -> None:
        previous_raw, candidate_raw = broad_accepted_transition()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_accepted_stage1(root, year=2026, raw=previous_raw)
            existing_bytes = {
                path: path.read_bytes()
                for path in [
                    root / "raw" / "michelin_data_2026.csv",
                    *partition_paths(2026, root / "partitions").values(),
                ]
            }
            from data_pipeline.stage1 import acquisition as acquisition_module

            real_replace = acquisition_module.os.replace
            calls = 0

            def fail_on_partition_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if destination.name == "france_2027.csv":
                    raise OSError("simulated accepted-run failure")
                real_replace(source, destination)

            with patch(
                "data_pipeline.stage1.acquisition.os.replace",
                side_effect=fail_on_partition_publish,
            ):
                with self.assertRaises(OSError):
                    run_stage1_acquisition(
                        raw_root=root / "raw",
                        output_root=root / "partitions",
                        today=date(2027, 4, 1),
                        downloader=make_downloader(candidate_raw),
                    )

            self.assertFalse((root / "raw" / "michelin_data_2027.csv").exists())
            self.assertFalse(any(path.exists() for path in partition_paths(2027, root / "partitions").values()))
            self.assertEqual(existing_bytes, {path: path.read_bytes() for path in existing_bytes})


if __name__ == "__main__":
    unittest.main()
