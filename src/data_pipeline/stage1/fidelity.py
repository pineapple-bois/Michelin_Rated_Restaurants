"""Read-only comparisons between generated candidates and fidelity baselines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from .pipeline import partition_paths


@dataclass(frozen=True)
class FidelityComparison:
    matches: bool
    byte_identical: bool
    summary: str


def compare_partition_files(candidate: Path, baseline: Path) -> FidelityComparison:
    if not baseline.is_file():
        return FidelityComparison(False, False, f"baseline missing: {baseline}")
    if not candidate.is_file():
        return FidelityComparison(False, False, f"candidate missing: {candidate}")

    byte_identical = candidate.read_bytes() == baseline.read_bytes()
    candidate_frame = pd.read_csv(candidate)
    baseline_frame = pd.read_csv(baseline)
    try:
        assert_frame_equal(
            candidate_frame,
            baseline_frame,
            check_dtype=True,
            check_like=False,
        )
    except AssertionError as error:
        first_line = str(error).splitlines()[0] or "dataframe mismatch"
        return FidelityComparison(False, byte_identical, first_line)

    summary = "byte-identical" if byte_identical else "dataframe-identical only"
    return FidelityComparison(True, byte_identical, summary)


def compare_partition_roots(
    *,
    year: int,
    candidate_root: Path,
    baseline_root: Path,
) -> dict[str, FidelityComparison]:
    candidate_paths = partition_paths(year, candidate_root)
    baseline_paths = partition_paths(year, baseline_root)
    return {
        country: compare_partition_files(candidate_paths[country], baseline_paths[country])
        for country in candidate_paths
    }
