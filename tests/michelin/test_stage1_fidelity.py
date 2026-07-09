from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from data_pipeline.stage1.fidelity import compare_partition_files
from data_pipeline.stage1.pipeline import run_stage1
from tests.support import REPOSITORY_ROOT


LEGACY_ROOT = REPOSITORY_ROOT / "legacy" / "Years"

# Optional migration-confidence checks: these compare regenerated outputs with
# local historical artifacts. They are useful when the uncommitted legacy
# fixtures are present, but they are not the validation strategy for future
# annual tranches.


def legacy_baselines(year: int) -> dict[str, Path]:
    year_root = LEGACY_ROOT / str(year) / "data"
    suffix = "" if year == 2023 else f"_{year}"
    return {
        "france": year_root / "France" / f"france_master{suffix}.csv",
        "monaco": year_root / "France" / f"monaco{suffix}.csv",
        "uk": year_root / "UK" / f"uk_data{suffix}.csv",
    }


class HistoricalFidelityTests(unittest.TestCase):
    def test_2023_to_2026_are_byte_identical_to_baselines(self) -> None:
        raw_root = REPOSITORY_ROOT / "data" / "raw" / "michelin"
        with tempfile.TemporaryDirectory() as temporary:
            candidate_root = Path(temporary)
            for year in (2026, 2023, 2024, 2025):
                with self.subTest(year=year):
                    baselines = legacy_baselines(year)
                    missing = [path for path in baselines.values() if not path.is_file()]
                    if missing:
                        raise unittest.SkipTest(
                            "optional Stage 1 legacy regression fixtures absent: "
                            + ", ".join(str(path) for path in missing)
                        )
                    result = run_stage1(
                        year=year,
                        raw_root=raw_root,
                        output_root=candidate_root / str(year),
                    )
                    comparisons = {
                        country: compare_partition_files(
                            result.paths[country], baseline,
                        )
                        for country, baseline in baselines.items()
                    }
                    self.assertEqual(
                        {country: report.summary for country, report in comparisons.items()},
                        {country: "byte-identical" for country in ("france", "monaco", "uk")},
                    )
                    self.assertEqual(
                        sum(summary.rows for summary in result.validation.values()),
                        sum(len(frame) for frame in result.partitions.values()),
                    )
                    if year == 2024:
                        self.assertEqual(
                            {
                                country: summary.missing_coordinate_pairs
                                for country, summary in result.validation.items()
                            },
                            {"france": 52, "monaco": 0, "uk": 12},
                        )


if __name__ == "__main__":
    unittest.main()
