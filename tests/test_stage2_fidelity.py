from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from data_pipeline.stage2.fidelity import (
    compare_department_geojson,
    compare_region_geojson,
    compare_restaurant_csv,
)
from data_pipeline.stage2.pipeline import run_stage2


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class Stage2FidelityTests(unittest.TestCase):
    def test_2025_and_2026_are_byte_identical_to_legacy_products(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            for year in (2026, 2025):
                with self.subTest(year=year):
                    result = run_stage2(year=year, output_root=output_root)
                    baseline = REPOSITORY_ROOT / "Years" / str(year) / "data" / "France"
                    restaurant_comparison = compare_restaurant_csv(
                        result.paths["restaurants"],
                        baseline / "all_restaurants.csv",
                    )
                    department_comparison = compare_department_geojson(
                        result.paths["departments"],
                        baseline / "geodata" / "department_restaurants.geojson",
                    )
                    region_comparison = compare_region_geojson(
                        result.paths["regions"],
                        baseline / "geodata" / "region_restaurants.geojson",
                    )
                    self.assertEqual(restaurant_comparison.summary, "byte-identical")
                    self.assertEqual(department_comparison.summary, "byte-identical")
                    self.assertEqual(region_comparison.summary, "byte-identical")

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
