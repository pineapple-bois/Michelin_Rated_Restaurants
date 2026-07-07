from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from data_pipeline.stage3.acquisition import ParisReferenceError, normalize_paris_table
from data_pipeline.stage3.pipeline import (
    Stage3PublicationError,
    load_paris_reference,
    run_stage3,
    validate_stage3,
)


ROOT = Path(__file__).resolve().parents[1]
ARRONDISSEMENT_INSEE_FIELDS = {
    "municipal_population",
    "population_density(inhabitants/sq_km)",
    "poverty_rate(%)",
    "average_net_hourly_wage(€)",
}


def expected_arrondissement_columns() -> list[str]:
    return [
        "code", "arrondissement", "department_num", "department", "capital", "region",
        "selected", "bib_gourmand", "1_star", "2_star", "3_star",
        "total_stars", "starred_restaurants", "green_stars", "locations", "geometry",
    ]


class Stage3Tests(unittest.TestCase):
    def test_paris_table_is_identified_and_normalized_by_columns(self) -> None:
        table = pd.DataFrame({
            "Arrondissement (R for Right Bank, L for Left Bank)": [
                f"{number}{'st' if number == 1 else 'nd' if number == 2 else 'rd' if number == 3 else 'th'}"
                for number in range(1, 21)
            ],
            "Name": [f"Name {number}" for number in range(1, 21)],
            "Unused": range(20),
        })
        result = normalize_paris_table(table)
        self.assertEqual(result["arrondissement_number"].tolist(), list(range(1, 21)))
        self.assertEqual(result.columns.tolist(), ["arrondissement_number", "ordinal", "name"])

    def test_incomplete_paris_table_fails(self) -> None:
        with self.assertRaises(ParisReferenceError):
            normalize_paris_table(pd.DataFrame({"Arrondissement": ["1st"], "Name": ["Louvre"]}))

    def test_local_paris_reference_has_complete_coverage(self) -> None:
        reference = load_paris_reference(
            ROOT / "data/raw/demographics/paris_arrondissements.csv"
        )
        self.assertEqual(len(reference), 20)
        self.assertEqual(set(reference["arrondissement_number"]), set(range(1, 21)))

    def test_validate_only_reports_complete_outputs_and_coastal_fallbacks(self) -> None:
        result = validate_stage3(year=2026)
        self.assertEqual(result.paths, {})
        self.assertEqual(result.validation.restaurant_rows, 3055)
        self.assertEqual(result.validation.arrondissement_rows, 320)
        self.assertEqual(result.validation.paris_rows, 20)
        self.assertEqual(len(result.validation.coastal_fallbacks), 7)
        self.assertFalse(result.restaurants["arrondissement"].isna().any())
        self.assertEqual(result.arrondissements.columns.tolist(), expected_arrondissement_columns())
        self.assertFalse(ARRONDISSEMENT_INSEE_FIELDS & set(result.arrondissements.columns))
        self.assertEqual(len(result.arrondissements["code"]), 320)
        self.assertTrue(result.arrondissements["code"].is_unique)
        self.assertIn("01001", set(result.arrondissements["code"]))
        self.assertIn("95003", set(result.arrondissements["code"]))
        self.assertFalse(result.arrondissements.geometry.isna().any())
        self.assertFalse(result.arrondissements.geometry.is_empty.any())
        self.assertEqual(int(result.arrondissements["selected"].sum()), int(result.restaurants["stars"].eq(0.25).sum()))
        self.assertEqual(int(result.arrondissements["bib_gourmand"].sum()), int(result.restaurants["stars"].eq(0.5).sum()))
        self.assertEqual(int(result.arrondissements["1_star"].sum()), int(result.restaurants["stars"].eq(1).sum()))
        self.assertEqual(int(result.arrondissements["2_star"].sum()), int(result.restaurants["stars"].eq(2).sum()))
        self.assertEqual(int(result.arrondissements["3_star"].sum()), int(result.restaurants["stars"].eq(3).sum()))
        self.assertEqual(int(result.arrondissements["green_stars"].sum()), int(result.restaurants["greenstar"].eq(1).sum()))
        self.assertTrue(result.arrondissements["locations"].astype(str).str.startswith("{").all())

    def test_stage3_does_not_require_arrondissement_demographics_input(self) -> None:
        result = validate_stage3(year=2026)
        self.assertEqual(result.validation.arrondissement_rows, 320)
        self.assertFalse(ARRONDISSEMENT_INSEE_FIELDS & set(result.arrondissements.columns))

    def test_publication_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "products"
            from data_pipeline.stage3 import pipeline
            actual_replace = pipeline.os.replace
            calls = 0

            def fail_second(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated publication failure")
                actual_replace(source, destination)

            with patch("data_pipeline.stage3.pipeline.os.replace", side_effect=fail_second):
                with self.assertRaisesRegex(Stage3PublicationError, "rolled back"):
                    run_stage3(year=2026, output_root=output)
            root = output / "france" / "2026"
            self.assertFalse((root / "all_restaurants(arrondissements).csv").exists())
            self.assertFalse((root / "geodata/arrondissement_restaurants.geojson").exists())
            self.assertFalse((root / "geodata/paris_restaurants.geojson").exists())

    def test_repeated_builds_are_deterministic_and_protected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            first = run_stage3(year=2026, output_root=output)
            before = {name: path.read_bytes() for name, path in first.paths.items()}
            with self.assertRaises(FileExistsError):
                run_stage3(year=2026, output_root=output)
            second = run_stage3(year=2026, output_root=output, replace=True)
            self.assertEqual(before, {name: path.read_bytes() for name, path in second.paths.items()})

    def test_outputs_have_expected_schema_and_optional_paris_fidelity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for year in (2025, 2026):
                with self.subTest(year=year):
                    result = run_stage3(year=year, output_root=output)
                    baseline = ROOT / "Years" / str(year) / "data/France"
                    paris_baseline = baseline / "geodata/paris_restaurants.geojson"
                    if paris_baseline.is_file():
                        self.assertEqual(
                            result.paths["paris"].read_bytes(),
                            paris_baseline.read_bytes(),
                        )
                    self.assertEqual(result.arrondissements.columns.tolist(), expected_arrondissement_columns())
                    self.assertFalse(ARRONDISSEMENT_INSEE_FIELDS & set(result.arrondissements.columns))
                    self.assertEqual(len(result.arrondissements), 320)
                    self.assertEqual(set(result.arrondissements["code"].str.len()), {5})
                    self.assertFalse(result.arrondissements.geometry.isna().any())
                    self.assertFalse(result.arrondissements.geometry.is_empty.any())
                    self.assertEqual(
                        int(result.arrondissements[["selected", "bib_gourmand", "1_star", "2_star", "3_star"]].to_numpy().sum()),
                        len(result.restaurants),
                    )
                    self.assertEqual(
                        int(result.arrondissements["green_stars"].sum()),
                        int(result.restaurants["greenstar"].eq(1).sum()),
                    )
                    self.assertTrue(result.arrondissements["locations"].astype(str).str.startswith("{").all())


if __name__ == "__main__":
    unittest.main()
