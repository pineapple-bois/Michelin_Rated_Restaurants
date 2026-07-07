"""Validation helpers for the departmental demographic tranche."""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd


class InseeValidationError(ValueError):
    """Raised when a source slice or assembled tranche fails validation."""


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    details: dict[str, object]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def check_unique_department_rows(frame: pd.DataFrame, *, code_column: str = "department_code", expected: int = 96) -> Check:
    rows = len(frame)
    unique = frame[code_column].nunique() if code_column in frame.columns else 0
    nulls = int(frame[code_column].isna().sum()) if code_column in frame.columns else rows
    duplicates = int(frame[code_column].duplicated().sum()) if code_column in frame.columns else rows
    passed = rows == expected and unique == expected and nulls == 0 and duplicates == 0
    return Check(
        "unique_metropolitan_departments",
        passed,
        {"rows": rows, "unique_departments": unique, "null_codes": nulls, "duplicate_codes": duplicates},
    )


def require_check(check: Check) -> Check:
    if not check.passed:
        raise InseeValidationError(f"{check.name} failed: {check.details}")
    return check


def validate_required_values(frame: pd.DataFrame, columns: tuple[str, ...]) -> Check:
    missing = [column for column in columns if column not in frame.columns]
    nulls = {
        column: int(frame[column].isna().sum())
        for column in columns
        if column in frame.columns and frame[column].isna().any()
    }
    passed = not missing and not nulls
    return Check("required_values", passed, {"missing_columns": missing, "nulls": nulls})


def validate_final_table(frame: pd.DataFrame) -> list[Check]:
    required = (
        "department_code",
        "department_name",
        "reference_year",
        "average_net_monthly_wage_fte_eur",
        "median_living_standard_eur",
        "poverty_rate_percent",
        "census_unemployment_rate_15_64_percent",
        "municipal_population",
        "area_sq_km",
        "population_density_per_sq_km",
        "gdp_current_prices_million_eur",
    )
    checks = [
        check_unique_department_rows(frame),
        validate_required_values(frame, required),
    ]
    for check in checks:
        require_check(check)
    return checks
