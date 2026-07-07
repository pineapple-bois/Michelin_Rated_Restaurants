from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import geopandas as gpd

from data_pipeline.stage2.fidelity import (
    compare_restaurant_csv,
)
from data_pipeline.stage2.pipeline import run_stage2
from data_pipeline.stage2.schema import (
    france_departmental_property_columns,
    france_regional_property_columns,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class Stage2FidelityTests(unittest.TestCase):
    def test_2025_and_2026_preserve_restaurant_outputs_and_use_insee_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            for year in (2026, 2025):
                with self.subTest(year=year):
                    result = run_stage2(year=year, output_root=output_root)
                    baseline = REPOSITORY_ROOT / "Years" / str(year) / "data" / "France"
                    restaurant_baseline = baseline / "all_restaurants.csv"
                    if restaurant_baseline.is_file():
                        restaurant_comparison = compare_restaurant_csv(
                            result.paths["restaurants"],
                            restaurant_baseline,
                        )
                        self.assertEqual(restaurant_comparison.summary, "byte-identical")
                    departments = gpd.read_file(result.paths["departments"])
                    regions = gpd.read_file(result.paths["regions"])
                    self.assertEqual(
                        tuple(departments.columns),
                        (*france_departmental_property_columns(year), "geometry"),
                    )
                    self.assertEqual(
                        tuple(regions.columns),
                        (*france_regional_property_columns(year), "geometry"),
                    )
                    self.assertEqual(len(departments), 96)
                    self.assertEqual(len(regions), 13)
                    self.assertIn("average_net_monthly_wage_fte_eur", departments.columns)
                    self.assertIn("census_unemployment_rate_15_64_percent", regions.columns)

    def test_replacement_is_deterministic_and_requires_explicit_option(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            first = run_stage2(year=2026, output_root=output_root)
            before = {name: path.read_bytes() for name, path in first.paths.items()}

            with self.assertRaises(FileExistsError):
                run_stage2(year=2026, output_root=output_root)

            replaced = run_stage2(year=2026, output_root=output_root, replace=True)
            self.assertEqual(
                before,
                {name: path.read_bytes() for name, path in replaced.paths.items()},
            )


if __name__ == "__main__":
    unittest.main()
