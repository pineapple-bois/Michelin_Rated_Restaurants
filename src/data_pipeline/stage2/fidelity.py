"""Fidelity comparisons for France departmental application products."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from geopandas.testing import assert_geodataframe_equal
import pandas as pd
from pandas.testing import assert_frame_equal


@dataclass(frozen=True)
class ProductComparison:
    matches: bool
    byte_identical: bool
    summary: str


def compare_restaurant_csv(candidate: Path, baseline: Path) -> ProductComparison:
    candidate_frame = pd.read_csv(candidate, dtype={"department_num": "string"})
    baseline_frame = pd.read_csv(baseline, dtype={"department_num": "string"})
    byte_identical = candidate.read_bytes() == baseline.read_bytes()
    try:
        assert_frame_equal(candidate_frame, baseline_frame, check_dtype=True, check_like=False)
    except AssertionError as error:
        return ProductComparison(False, byte_identical, str(error).splitlines()[0])
    return ProductComparison(
        True,
        byte_identical,
        "byte-identical" if byte_identical else "dataframe-identical only",
    )


def compare_department_geojson(candidate: Path, baseline: Path) -> ProductComparison:
    candidate_frame = gpd.read_file(candidate)
    baseline_frame = gpd.read_file(baseline)
    byte_identical = candidate.read_bytes() == baseline.read_bytes()
    try:
        assert_geodataframe_equal(
            candidate_frame,
            baseline_frame,
            check_dtype=False,
            check_like=False,
        )
    except AssertionError as error:
        return ProductComparison(False, byte_identical, str(error).splitlines()[0])
    return ProductComparison(
        True,
        byte_identical,
        "byte-identical" if byte_identical else "geodataframe-identical only",
    )


def compare_region_geojson(candidate: Path, baseline: Path) -> ProductComparison:
    return compare_department_geojson(candidate, baseline)
