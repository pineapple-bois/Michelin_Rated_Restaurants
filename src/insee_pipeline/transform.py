"""Named source loaders and transformations for departmental demographics."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

import geopandas as gpd
import pandas as pd

from .load import load_departmental_zip_data, read_oecd_csv, require_columns, to_numeric
from .validate import Check, InseeValidationError, require_check


METRIC_COLUMNS = (
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


@dataclass(frozen=True)
class SourceFrame:
    frame: pd.DataFrame
    checks: list[Check]
    metadata: dict[str, object]


def department_sort_value(code: object) -> float:
    text = str(code)
    if text == "2A":
        return 20.1
    if text == "2B":
        return 20.2
    return float(int(text))


def sort_by_department_code(frame: pd.DataFrame, column: str = "department_code") -> pd.DataFrame:
    return (
        frame.assign(_department_sort_key=lambda df: df[column].map(department_sort_value))
        .sort_values("_department_sort_key", kind="stable")
        .drop(columns="_department_sort_key")
        .reset_index(drop=True)
    )


def normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", "" if pd.isna(value) else str(value))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold().replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _year_filter(frame: pd.DataFrame, year: int, label: str) -> pd.Series:
    if "TIME_PERIOD" not in frame.columns:
        raise InseeValidationError(f"{label} is missing TIME_PERIOD")
    time_period = pd.to_numeric(frame["TIME_PERIOD"], errors="raise")
    return time_period.eq(year)


def _restrict_metropolitan(frame: pd.DataFrame, department_codes: set[str], *, code_column: str = "GEO") -> pd.DataFrame:
    result = frame.loc[frame[code_column].isin(department_codes)].copy()
    if len(result) != len(department_codes) or result[code_column].nunique() != len(department_codes):
        missing = sorted(department_codes - set(result[code_column]))
        duplicates = sorted(result.loc[result[code_column].duplicated(), code_column].unique())
        raise InseeValidationError(
            f"Expected one row for each metropolitan department; missing={missing}, duplicates={duplicates}"
        )
    renamed = result.rename(columns={code_column: "department_code"})
    sorted_result = sort_by_department_code(renamed)
    if code_column != "department_code":
        sorted_result = sorted_result.rename(columns={"department_code": code_column})
    return sorted_result


def _status_counts(frame: pd.DataFrame, columns: tuple[str, ...]) -> dict[str, dict[str, int]]:
    return {
        column: {str(key): int(value) for key, value in frame[column].value_counts(dropna=False).items()}
        for column in columns
        if column in frame.columns
    }


def load_department_geometry(geometry_path, *, year: int) -> SourceFrame:
    geometry = gpd.read_file(geometry_path)
    require_columns(geometry, ("code", "nom", "geometry"), "department geometry")
    if geometry.crs is None or geometry.crs.to_epsg() != 4326:
        raise InseeValidationError(f"Department geometry must be EPSG:4326, found {geometry.crs}")
    if len(geometry) != 96 or geometry["code"].nunique() != 96:
        raise InseeValidationError("Department geometry must contain 96 unique metropolitan departments")
    if geometry.geometry.isna().any() or geometry.geometry.is_empty.any() or (~geometry.geometry.is_valid).any():
        raise InseeValidationError("Department geometry contains null, empty, or invalid features")
    l93 = geometry.to_crs("EPSG:2154")
    frame = (
        l93[["code", "nom", "geometry"]]
        .assign(area_sq_km=lambda df: df.geometry.area / 1_000_000)
        .drop(columns="geometry")
        .rename(columns={"code": "department_code", "nom": "department_name"})
    )
    frame = sort_by_department_code(frame)
    if frame["area_sq_km"].isna().any() or frame["area_sq_km"].le(0).any():
        raise InseeValidationError("Department area contains null or non-positive values")
    return SourceFrame(
        frame=frame,
        checks=[Check("geometry_metropolitan_coverage", True, {"rows": len(frame), "crs": "EPSG:4326", "area_crs": "EPSG:2154"})],
        metadata={"source": str(geometry_path), "reference_year": year},
    )


def load_wages(path, *, year: int, department_codes: set[str]) -> SourceFrame:
    raw, _metadata, members = load_departmental_zip_data(path)
    require_columns(raw, ("GEO", "GEO_OBJECT", "FREQ", "SEX", "AGE", "DERA_MEASURE", "CONF_STATUS", "TIME_PERIOD", "OBS_VALUE"), "wages")
    filtered = raw.loc[
        raw["GEO_OBJECT"].eq("DEP")
        & raw["FREQ"].eq("A")
        & raw["SEX"].eq("_T")
        & raw["AGE"].eq("_T")
        & raw["DERA_MEASURE"].eq("SALAIRE_NET_EQTP_MENSUEL_MOYENNE")
        & _year_filter(raw, year, "wages")
    ].copy()
    filtered["average_net_monthly_wage_fte_eur"] = to_numeric(filtered["OBS_VALUE"], "wages OBS_VALUE")
    result = _restrict_metropolitan(filtered, department_codes)
    result = result.rename(columns={"GEO": "department_code"})[
        ["department_code", "average_net_monthly_wage_fte_eur", "CONF_STATUS"]
    ]
    return SourceFrame(
        frame=result,
        checks=[Check("wages_complete", True, {"rows": len(result), "statuses": _status_counts(result, ("CONF_STATUS",))})],
        metadata={"dataset_id": "DS_BTS_SAL_EQTP_SEX_AGE", "data_member": members.data_name, "measure": "SALAIRE_NET_EQTP_MENSUEL_MOYENNE"},
    )


def load_filosofi(path, *, year: int, department_codes: set[str]) -> SourceFrame:
    raw, _metadata, members = load_departmental_zip_data(path)
    require_columns(raw, ("FILOSOFI_MEASURE", "GEO", "GEO_OBJECT", "CONF_STATUS", "OBS_STATUS", "TIME_PERIOD", "OBS_VALUE"), "filosofi")
    outputs = []
    checks = []
    for measure, column in (
        ("MED_SL", "median_living_standard_eur"),
        ("PR_MD60", "poverty_rate_percent"),
    ):
        filtered = raw.loc[
            raw["GEO_OBJECT"].eq("DEP")
            & raw["FILOSOFI_MEASURE"].eq(measure)
            & _year_filter(raw, year, f"filosofi {measure}")
        ].copy()
        filtered[column] = to_numeric(filtered["OBS_VALUE"], f"{measure} OBS_VALUE")
        restricted = _restrict_metropolitan(filtered, department_codes)
        outputs.append(restricted.rename(columns={"GEO": "department_code"})[["department_code", column, "CONF_STATUS", "OBS_STATUS"]])
        checks.append(Check(f"filosofi_{measure}_complete", True, {"rows": len(restricted), "statuses": _status_counts(restricted, ("CONF_STATUS", "OBS_STATUS"))}))
    result = outputs[0][["department_code", "median_living_standard_eur"]].merge(
        outputs[1][["department_code", "poverty_rate_percent"]], on="department_code", validate="one_to_one"
    )
    return SourceFrame(
        frame=result,
        checks=checks,
        metadata={"dataset_id": "DS_FILOSOFI_CC", "data_member": members.data_name, "measures": ["MED_SL", "PR_MD60"]},
    )


def load_unemployment(path, *, year: int, department_codes: set[str]) -> SourceFrame:
    raw, _metadata, members = load_departmental_zip_data(path)
    require_columns(raw, ("GEO", "GEO_OBJECT", "EMPSTA_ENQ", "AGE", "PCS", "FREQ", "OBS_STATUS", "TIME_PERIOD", "OBS_VALUE"), "unemployment")
    filtered = raw.loc[
        raw["GEO_OBJECT"].eq("DEP")
        & raw["FREQ"].eq("A")
        & raw["PCS"].eq("_T")
        & raw["AGE"].eq("Y15T64")
        & raw["EMPSTA_ENQ"].isin(["1", "2", "1T2"])
        & _year_filter(raw, year, "unemployment")
    ].copy()
    filtered["OBS_VALUE"] = to_numeric(filtered["OBS_VALUE"], "unemployment OBS_VALUE")
    metropolitan = filtered.loc[filtered["GEO"].isin(department_codes)].copy()
    pivot = metropolitan.pivot(index="GEO", columns="EMPSTA_ENQ", values="OBS_VALUE").reset_index()
    for column in ("1", "2", "1T2"):
        if column not in pivot.columns:
            raise InseeValidationError(f"Unemployment is missing status {column}")
    pivot = _restrict_metropolitan(pivot, department_codes)
    pivot["active_balance"] = pivot["1"] + pivot["2"] - pivot["1T2"]
    max_balance = float(pivot["active_balance"].abs().max())
    if max_balance > 0.01:
        raise InseeValidationError(f"Employment status components do not reconcile: max balance {max_balance}")
    if pivot["1T2"].le(0).any():
        raise InseeValidationError("Active population contains zero or negative values")
    result = pivot.rename(
        columns={
            "GEO": "department_code",
            "1": "employed_population_15_64",
            "2": "unemployed_population_15_64",
            "1T2": "active_population_15_64",
        }
    )
    result["census_unemployment_rate_15_64_percent"] = (
        result["unemployed_population_15_64"] / result["active_population_15_64"] * 100
    )
    result = result[
        [
            "department_code",
            "employed_population_15_64",
            "unemployed_population_15_64",
            "active_population_15_64",
            "census_unemployment_rate_15_64_percent",
        ]
    ]
    return SourceFrame(
        frame=result,
        checks=[Check("unemployment_components_reconcile", True, {"rows": len(result), "max_active_balance": max_balance})],
        metadata={"dataset_id": "DS_RP_EMPLOI_LR_COMP", "data_member": members.data_name, "employment_status_codes": ["1", "2", "1T2"]},
    )


def load_population(path, *, year: int, department_codes: set[str]) -> SourceFrame:
    raw, _metadata, members = load_departmental_zip_data(path)
    require_columns(raw, ("GEO", "GEO_OBJECT", "FREQ", "POPREF_MEASURE", "TIME_PERIOD", "OBS_VALUE"), "population")
    filtered = raw.loc[
        raw["GEO_OBJECT"].eq("DEP")
        & raw["FREQ"].eq("A")
        & raw["POPREF_MEASURE"].isin(["PMUN", "PCAP", "PTOT"])
        & _year_filter(raw, year, "population")
    ].copy()
    filtered["OBS_VALUE"] = to_numeric(filtered["OBS_VALUE"], "population OBS_VALUE")
    metropolitan = filtered.loc[filtered["GEO"].isin(department_codes)].copy()
    pivot = metropolitan.pivot(index="GEO", columns="POPREF_MEASURE", values="OBS_VALUE").reset_index()
    for column in ("PMUN", "PCAP", "PTOT"):
        if column not in pivot.columns:
            raise InseeValidationError(f"Population is missing measure {column}")
    pivot = _restrict_metropolitan(pivot, department_codes)
    pivot["population_balance"] = pivot["PMUN"] + pivot["PCAP"] - pivot["PTOT"]
    max_balance = float(pivot["population_balance"].abs().max())
    if max_balance != 0:
        raise InseeValidationError(f"Population components do not reconcile: max balance {max_balance}")
    result = pivot.rename(
        columns={
            "GEO": "department_code",
            "PMUN": "municipal_population",
            "PCAP": "population_counted_separately",
            "PTOT": "total_population",
        }
    )[
        ["department_code", "municipal_population", "population_counted_separately", "total_population"]
    ]
    return SourceFrame(
        frame=result,
        checks=[Check("population_components_reconcile", True, {"rows": len(result), "max_population_balance": max_balance})],
        metadata={"dataset_id": "DS_POPULATIONS_REFERENCE", "data_member": members.data_name, "measures": ["PMUN", "PCAP", "PTOT"]},
    )


def load_oecd_gdp(path, *, year: int, department_geometry: pd.DataFrame) -> SourceFrame:
    raw = read_oecd_csv(path)
    require_columns(
        raw,
        ("COUNTRY", "TERRITORIAL_LEVEL", "REF_AREA", "Reference area", "TIME_PERIOD", "MEASURE", "ACTIVITY", "PRICES", "UNIT_MEASURE", "UNIT_MULT", "CURRENCY", "OBS_VALUE", "OBS_STATUS", "Observation status"),
        "OECD GDP",
    )
    filtered = raw.loc[
        raw["COUNTRY"].eq("FRA")
        & raw["TERRITORIAL_LEVEL"].eq("TL3")
        & raw["TIME_PERIOD"].astype(str).eq(str(year))
        & raw["MEASURE"].eq("GDP")
        & raw["ACTIVITY"].eq("_T")
        & raw["PRICES"].eq("V")
        & raw["UNIT_MEASURE"].eq("XDC")
        & raw["UNIT_MULT"].eq("6")
        & raw["CURRENCY"].eq("EUR")
    ].copy()
    if filtered.empty:
        raise InseeValidationError(f"No OECD GDP records for {year}")
    duplicate_count = int(filtered.duplicated(subset=["REF_AREA", "TIME_PERIOD"]).sum())
    if duplicate_count:
        raise InseeValidationError(f"OECD GDP has duplicate REF_AREA/TIME_PERIOD rows: {duplicate_count}")
    filtered["gdp_current_prices_million_eur"] = to_numeric(filtered["OBS_VALUE"], "OECD GDP OBS_VALUE")
    filtered["department_name_key"] = filtered["Reference area"].map(normalize_name)
    crosswalk = department_geometry[["department_code", "department_name"]].copy()
    crosswalk["department_name_key"] = crosswalk["department_name"].map(normalize_name)
    matched = filtered.merge(crosswalk, on="department_name_key", how="left", validate="many_to_one")
    unmatched = matched.loc[matched["department_code"].isna(), ["REF_AREA", "Reference area"]].drop_duplicates()
    result = matched.loc[matched["department_code"].notna()].copy()
    if len(result) != 96 or result["department_code"].nunique() != 96:
        raise InseeValidationError(
            "OECD GDP did not map to 96 metropolitan departments; "
            f"matched={len(result)}, unique={result['department_code'].nunique()}, "
            f"unmatched={unmatched.to_dict(orient='records')}"
        )
    output = result.rename(
        columns={
            "REF_AREA": "oecd_tl3_code",
            "Reference area": "oecd_reference_area_name",
            "OBS_STATUS": "gdp_observation_status",
            "Observation status": "gdp_observation_status_label",
        }
    )[
        [
            "department_code",
            "department_name",
            "oecd_tl3_code",
            "oecd_reference_area_name",
            "gdp_current_prices_million_eur",
            "gdp_observation_status",
            "gdp_observation_status_label",
        ]
    ]
    output = sort_by_department_code(output)
    return SourceFrame(
        frame=output,
        checks=[
            Check(
                "oecd_gdp_tl3_crosswalk",
                True,
                {
                    "raw_french_tl3_rows": len(filtered),
                    "matched_metropolitan_rows": len(output),
                    "unmatched_reference_areas": unmatched.to_dict(orient="records"),
                    "status_counts": _status_counts(output, ("gdp_observation_status", "gdp_observation_status_label")),
                },
            )
        ],
        metadata={"dataflow": "OECD.CFE.EDS:DSD_REG_ECO@DF_GDP(2.4)", "dataset_title": "Gross domestic product - Regions"},
    )


def assemble_departmental_table(
    *,
    year: int,
    geometry: pd.DataFrame,
    wages: pd.DataFrame,
    filosofi: pd.DataFrame,
    unemployment: pd.DataFrame,
    population: pd.DataFrame,
    gdp: pd.DataFrame,
) -> pd.DataFrame:
    result = geometry.copy()
    for frame in (wages, filosofi, unemployment, population, gdp.drop(columns=["department_name"])):
        result = result.merge(frame, on="department_code", how="left", validate="one_to_one")
    result["reference_year"] = year
    result["population_density_per_sq_km"] = result["municipal_population"] / result["area_sq_km"]
    ordered = [
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
        "population_counted_separately",
        "total_population",
        "employed_population_15_64",
        "unemployed_population_15_64",
        "active_population_15_64",
        "oecd_tl3_code",
        "gdp_observation_status",
        "gdp_observation_status_label",
    ]
    return sort_by_department_code(result[ordered])
