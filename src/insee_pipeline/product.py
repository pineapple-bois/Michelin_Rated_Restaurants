"""Build Michelin-consumable departmental products from validated candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from .paths import PipelinePaths
from .sources import sha256_file
from .validate import InseeValidationError, check_unique_department_rows, require_check, validate_required_values


PRODUCT_COLUMNS = [
    "department_code",
    "department_name",
    "capital",
    "region",
    "reference_year",
    "average_net_monthly_wage_fte_eur",
    "median_living_standard_eur",
    "poverty_rate_percent",
    "census_unemployment_rate_15_64_percent",
    "municipal_population",
    "area_sq_km",
    "population_density_per_sq_km",
    "gdp_current_prices_million_eur",
    "gdp_per_capita_eur",
]

EXCLUDED_CANDIDATE_FIELDS = [
    "population_counted_separately",
    "total_population",
    "employed_population_15_64",
    "unemployed_population_15_64",
    "active_population_15_64",
    "oecd_tl3_code",
    "gdp_observation_status",
    "gdp_observation_status_label",
]

STRING_COLUMNS = ["department_code", "department_name", "capital", "region"]
INTEGER_COLUMNS = ["reference_year", "municipal_population"]
FLOAT_COLUMNS = [
    "average_net_monthly_wage_fte_eur",
    "median_living_standard_eur",
    "poverty_rate_percent",
    "census_unemployment_rate_15_64_percent",
    "area_sq_km",
    "population_density_per_sq_km",
    "gdp_current_prices_million_eur",
    "gdp_per_capita_eur",
]

PRODUCT_DTYPES = {
    **{column: "string" for column in STRING_COLUMNS},
    **{column: "int64" for column in INTEGER_COLUMNS},
    **{column: "float64" for column in FLOAT_COLUMNS},
}


@dataclass(frozen=True)
class ProductResult:
    year: int
    rows: int
    paths: dict[str, Path]
    output_hash: str


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _write_product_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n", float_format="%.17f")
    reloaded = pd.read_csv(path, dtype={"department_code": str})
    reloaded = _enforce_product_types(reloaded)
    assert_frame_equal(frame, reloaded, check_dtype=True, rtol=1e-12, atol=1e-12)


def _require_candidate_inputs(paths: PipelinePaths, year: int) -> tuple[dict[str, object], dict[str, object]]:
    manifest = _read_json(paths.manifest)
    validation = _read_json(paths.validation_report)
    if int(manifest.get("reference_year", -1)) != year:
        raise InseeValidationError(f"Candidate manifest year does not match requested year {year}")
    if int(validation.get("reference_year", -1)) != year:
        raise InseeValidationError(f"Candidate validation report year does not match requested year {year}")
    if int(manifest.get("rows", -1)) != 96:
        raise InseeValidationError(f"Candidate manifest must record 96 rows: {manifest.get('rows')}")
    checks = validation.get("checks", [])
    if not checks:
        raise InseeValidationError("Candidate validation report contains no checks")
    failed = [check for check in checks if not check.get("passed")]
    if failed:
        names = [check.get("name") for check in failed]
        raise InseeValidationError(f"Candidate validation report contains failed checks: {names}")
    if not paths.departmental_table.is_file():
        raise FileNotFoundError(paths.departmental_table)
    return manifest, validation


def _load_candidate(path: Path, year: int) -> pd.DataFrame:
    candidate = pd.read_csv(path, dtype={"department_code": str})
    require_check(check_unique_department_rows(candidate))
    year_values = set(candidate["reference_year"].dropna().astype(int))
    if year_values != {year}:
        raise InseeValidationError(f"Candidate reference_year values do not match {year}: {sorted(year_values)}")
    required = tuple(column for column in PRODUCT_COLUMNS if column not in {"capital", "region", "gdp_per_capita_eur"})
    require_check(validate_required_values(candidate, required))
    return candidate


def _load_department_lookup(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    lookup = pd.read_csv(path, dtype={"department_num": str})
    required = ("department_num", "capital", "region")
    missing = [column for column in required if column not in lookup.columns]
    if missing:
        raise InseeValidationError(f"Department lookup is missing columns: {missing}")
    lookup = lookup.rename(columns={"department_num": "department_code"})
    lookup = lookup[["department_code", "capital", "region"]].copy()
    if lookup["department_code"].duplicated().any():
        duplicates = lookup.loc[lookup["department_code"].duplicated(), "department_code"].tolist()
        raise InseeValidationError(f"Department lookup has duplicate department codes: {duplicates}")
    require_check(validate_required_values(lookup, ("department_code", "capital", "region")))
    return lookup


def _enforce_product_types(frame: pd.DataFrame) -> pd.DataFrame:
    typed = frame.copy()
    for column in STRING_COLUMNS:
        typed[column] = typed[column].astype("string")
    for column in INTEGER_COLUMNS:
        typed[column] = pd.to_numeric(typed[column], errors="raise").astype("int64")
    for column in FLOAT_COLUMNS:
        typed[column] = pd.to_numeric(typed[column], errors="raise").astype("float64")
    return typed[PRODUCT_COLUMNS]


def build_product_frame(candidate: pd.DataFrame, lookup: pd.DataFrame, *, year: int) -> pd.DataFrame:
    merged = candidate.merge(lookup, on="department_code", how="left", validate="one_to_one")
    unmatched = merged.loc[merged["capital"].isna() | merged["region"].isna(), "department_code"].tolist()
    if unmatched:
        raise InseeValidationError(f"Department lookup is missing candidate department codes: {unmatched}")
    merged["gdp_per_capita_eur"] = (
        pd.to_numeric(merged["gdp_current_prices_million_eur"], errors="raise")
        * 1_000_000
        / pd.to_numeric(merged["municipal_population"], errors="raise")
    )
    product = _enforce_product_types(merged[PRODUCT_COLUMNS])
    require_check(check_unique_department_rows(product))
    require_check(validate_required_values(product, tuple(PRODUCT_COLUMNS)))
    if set(product["reference_year"].astype(int)) != {year}:
        raise InseeValidationError(f"Product reference_year values do not match {year}")
    expected_gdp_per_capita = (
        product["gdp_current_prices_million_eur"] * 1_000_000 / product["municipal_population"]
    )
    if not np.allclose(product["gdp_per_capita_eur"], expected_gdp_per_capita, rtol=1e-12, atol=1e-9):
        raise InseeValidationError("GDP-per-capita derivation failed validation")
    return product


def build_product(
    *,
    year: int,
    candidate_root: Path = Path("data/candidates/insee"),
    product_root: Path = Path("data/products/insee"),
    department_lookup_path: Path = Path("data/raw/demographics/departments.csv"),
    replace: bool = False,
) -> ProductResult:
    paths = PipelinePaths.create(
        year=year,
        candidate_root=candidate_root,
        product_root=product_root,
    )
    outputs = {
        "departmental_product": paths.product_table,
        "manifest": paths.product_manifest,
    }
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not replace:
        raise FileExistsError("Refusing to replace existing product outputs without --replace: " + ", ".join(map(str, existing)))

    candidate_manifest, validation_report = _require_candidate_inputs(paths, year)
    candidate = _load_candidate(paths.departmental_table, year)
    lookup = _load_department_lookup(department_lookup_path)
    product = build_product_frame(candidate, lookup, year=year)

    _write_product_csv(paths.product_table, product)
    output_hash = sha256_file(paths.product_table)
    manifest_payload = {
        "reference_year": year,
        "rows": len(product),
        "schema": PRODUCT_COLUMNS,
        "dtypes": PRODUCT_DTYPES,
        "inputs": {
            "candidate_table": {"path": str(paths.departmental_table), "sha256": sha256_file(paths.departmental_table)},
            "candidate_manifest": {"path": str(paths.manifest), "sha256": sha256_file(paths.manifest)},
            "validation_report": {"path": str(paths.validation_report), "sha256": sha256_file(paths.validation_report)},
            "department_lookup": {"path": str(department_lookup_path), "sha256": sha256_file(department_lookup_path)},
        },
        "candidate_manifest": {
            "reference_year": candidate_manifest.get("reference_year"),
            "rows": candidate_manifest.get("rows"),
        },
        "validation_report": {
            "reference_year": validation_report.get("reference_year"),
            "checks": len(validation_report.get("checks", [])),
        },
        "derivations": {
            "gdp_per_capita_eur": "gdp_current_prices_million_eur * 1_000_000 / municipal_population",
        },
        "excluded_candidate_fields": EXCLUDED_CANDIDATE_FIELDS,
        "outputs": {
            "departmental_product": str(paths.product_table),
            "manifest": str(paths.product_manifest),
        },
        "output_hash": output_hash,
    }
    _write_json(paths.product_manifest, manifest_payload)
    return ProductResult(year=year, rows=len(product), paths=outputs, output_hash=output_hash)
