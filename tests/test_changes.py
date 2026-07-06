from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import pandas as pd

from data_pipeline.changes.pipeline import (
    ChangesValidationError,
    run_changes,
    validate_changes,
)


def restaurant(
    name: str, stars: float, *, url: str, address: str = "1 rue Test",
    location: str = "Paris, 75001", greenstar: int | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "name": name, "address": address, "location": location,
        "arrondissement": "1st (Louvre)", "department_num": "75",
        "department": "Paris", "capital": "Paris", "region": "Île-de-France",
        "price": "€€€", "cuisine": "Modern", "url": url,
        "award": "1 Star" if stars == 1 else "2 Stars" if stars == 2 else "Selected Restaurants",
        "stars": stars, "longitude": 2.34, "latitude": 48.86,
    }
    if greenstar is not None:
        row["greenstar"] = greenstar
    return row


def write_products(root: Path, previous: pd.DataFrame, current: pd.DataFrame) -> None:
    directory = root / "france"
    directory.mkdir(parents=True)
    previous.to_csv(directory / "all_restaurants(arrondissements)_23.csv", index=False)
    current.to_csv(directory / "all_restaurants(arrondissements)_24.csv", index=False)


class ChangesTests(unittest.TestCase):
    def test_exact_url_match_can_be_renamed_relocated_and_promoted(self) -> None:
        previous = pd.DataFrame([restaurant("Old Name", 1, url="https://guide.test/a")])
        current = pd.DataFrame([restaurant(
            "New Name", 2, url="https://guide.test/a", address="2 rue Test",
            location="Paris, 75002",
        )])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_products(root, previous, current)
            result = validate_changes(previous_year=2023, current_year=2024, product_root=root)
        row = result.changes.iloc[0]
        self.assertEqual(row["matching_method"], "exact_url")
        self.assertEqual(
            set(row["change_types"].split("|")), {"promoted", "renamed", "relocated"}
        )
        self.assertTrue(row["material"])

    def test_fuzzy_candidate_does_not_consume_addition_or_removal(self) -> None:
        previous = pd.DataFrame([restaurant(
            "Pavyllon", 1, url="https://old.test", address="1 rue A"
        )])
        current = pd.DataFrame([restaurant(
            "Pavyllon Paris", 1, url="https://new.test", address="2 rue B"
        )])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_products(root, previous, current)
            result = validate_changes(previous_year=2023, current_year=2024, product_root=root)
        self.assertEqual(result.validation.matched_rows, 0)
        self.assertEqual(result.validation.new_entries, 1)
        self.assertEqual(result.validation.removed_entries, 1)
        self.assertGreaterEqual(result.validation.fuzzy_candidates, 1)
        self.assertIn("match_candidate", set(result.changes["record_type"]))

    def test_absent_green_star_field_is_unknown_not_zero(self) -> None:
        previous = pd.DataFrame([restaurant("Same", 1, url="https://guide.test/a")])
        current = pd.DataFrame([restaurant(
            "Same", 1, url="https://guide.test/a", greenstar=1
        )])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_products(root, previous, current)
            result = validate_changes(previous_year=2023, current_year=2024, product_root=root)
        self.assertNotIn("green_star_gained", result.changes.iloc[0]["change_types"])

    def test_non_consecutive_years_fail(self) -> None:
        with self.assertRaisesRegex(ChangesValidationError, "consecutive"):
            validate_changes(previous_year=2023, current_year=2025)

    def test_historical_pairs_reconcile_and_publish_deterministically(self) -> None:
        expected = {
            (2023, 2024): (1033, 1017),
            (2024, 2025): (1017, 2985),
            (2025, 2026): (2985, 3055),
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for years, row_counts in expected.items():
                with self.subTest(years=years):
                    first = run_changes(
                        previous_year=years[0], current_year=years[1], output_root=output
                    )
                    self.assertEqual(
                        (first.validation.previous_rows, first.validation.current_rows),
                        row_counts,
                    )
                    before = {name: path.read_bytes() for name, path in first.paths.items()}
                    with self.assertRaises(FileExistsError):
                        run_changes(
                            previous_year=years[0], current_year=years[1], output_root=output
                        )
                    second = run_changes(
                        previous_year=years[0], current_year=years[1],
                        output_root=output, replace=True,
                    )
                    self.assertEqual(
                        before, {name: path.read_bytes() for name, path in second.paths.items()}
                    )


if __name__ == "__main__":
    unittest.main()
