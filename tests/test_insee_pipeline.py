from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from insee_pipeline.pipeline import build
from insee_pipeline.product import PRODUCT_COLUMNS, build_product
from insee_pipeline.transform import (
    load_filosofi,
    load_population,
    load_unemployment,
    load_wages,
)
from insee_pipeline.validate import InseeValidationError


def write_zip(path: Path, data: pd.DataFrame, metadata: pd.DataFrame | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = metadata if metadata is not None else pd.DataFrame({"COD_VAR": ["GEO"]})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(path.stem + "_data.csv", data.to_csv(sep=";", index=False))
        archive.writestr(path.stem + "_metadata.csv", metadata.to_csv(sep=";", index=False))


def fixture_geometry(path: Path, codes: list[str]) -> None:
    rows = []
    for index, code in enumerate(codes):
        x = float(index)
        rows.append(
            {
                "code": code,
                "nom": f"Dept {code}",
                "geometry": Polygon([(x, 46.0), (x + 0.1, 46.0), (x + 0.1, 46.1), (x, 46.1)]),
            }
        )
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GeoJSON")


def metropolitan_codes() -> list[str]:
    codes = [f"{number:02d}" for number in range(1, 20)]
    codes.extend(["2A", "2B"])
    codes.extend(f"{number:02d}" for number in range(21, 96))
    return codes


def write_candidate_fixture(root: Path, *, year: int = 2023, failed_validation: bool = False) -> Path:
    candidate_root = root / "candidates" / str(year)
    candidate_root.mkdir(parents=True, exist_ok=True)
    codes = metropolitan_codes()
    rows = []
    for index, code in enumerate(codes, start=1):
        rows.append(
            {
                "department_code": code,
                "department_name": f"Dept {code}",
                "reference_year": year,
                "average_net_monthly_wage_fte_eur": 2200.0 + index,
                "median_living_standard_eur": 24000.0 + index,
                "poverty_rate_percent": 10.0 + index / 100,
                "census_unemployment_rate_15_64_percent": 8.0 + index / 100,
                "municipal_population": 1000 + index,
                "area_sq_km": 50.0 + index,
                "population_density_per_sq_km": (1000 + index) / (50.0 + index),
                "gdp_current_prices_million_eur": 100.0 + index,
                "population_counted_separately": 10,
                "total_population": 1010 + index,
                "employed_population_15_64": 80.0,
                "unemployed_population_15_64": 20.0,
                "active_population_15_64": 100.0,
                "oecd_tl3_code": f"FR{index:03d}",
                "gdp_observation_status": "P",
                "gdp_observation_status_label": "Provisional value",
            }
        )
    candidate = pd.DataFrame(rows)
    candidate_path = candidate_root / f"france_departments_{year}.csv"
    candidate.to_csv(candidate_path, index=False, lineterminator="\n")
    (candidate_root / f"manifest_{year}.json").write_text(
        json.dumps({"reference_year": year, "rows": len(candidate), "schema": candidate.columns.tolist()}) + "\n",
        encoding="utf-8",
    )
    (candidate_root / f"validation_report_{year}.json").write_text(
        json.dumps(
            {
                "reference_year": year,
                "checks": [
                    {"name": "fixture_validation", "passed": not failed_validation, "details": {}},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return root / "candidates"


def write_lookup_fixture(path: Path, *, duplicate: bool = False, missing_code: str | None = None) -> None:
    rows = []
    for code in metropolitan_codes():
        if code == missing_code:
            continue
        rows.append(
            {
                "department_num": code,
                "department": f"Dept {code}",
                "capital": f"Capital {code}",
                "region": f"Region {code}",
            }
        )
    if duplicate:
        rows.append(rows[0].copy())
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, lineterminator="\n")


class InseePipelineTests(unittest.TestCase):
    def test_wage_loader_requires_requested_year_and_metropolitan_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wages.zip"
            data = pd.DataFrame(
                [
                    {
                        "GEO": "01", "GEO_OBJECT": "DEP", "FREQ": "A", "SEX": "_T",
                        "AGE": "_T", "DERA_MEASURE": "SALAIRE_NET_EQTP_MENSUEL_MOYENNE",
                        "CONF_STATUS": "F", "TIME_PERIOD": "2023", "OBS_VALUE": "2500",
                    }
                ]
            )
            write_zip(path, data)
            loaded = load_wages(path, year=2023, department_codes={"01"})
            self.assertEqual(loaded.frame.iloc[0]["average_net_monthly_wage_fte_eur"], 2500)
            with self.assertRaises(InseeValidationError):
                load_wages(path, year=2024, department_codes={"01"})

    def test_unemployment_reconciliation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unemployment.zip"
            rows = []
            for status, value in [("1", "70"), ("2", "20"), ("1T2", "100")]:
                rows.append(
                    {
                        "GEO": "01", "GEO_OBJECT": "DEP", "EMPSTA_ENQ": status,
                        "AGE": "Y15T64", "PCS": "_T", "FREQ": "A",
                        "OBS_STATUS": "A", "TIME_PERIOD": "2023", "OBS_VALUE": value,
                    }
                )
            write_zip(path, pd.DataFrame(rows))
            with self.assertRaisesRegex(InseeValidationError, "do not reconcile"):
                load_unemployment(path, year=2023, department_codes={"01"})

    def test_population_reconciliation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "population.zip"
            rows = []
            for measure, value in [("PMUN", "100"), ("PCAP", "4"), ("PTOT", "105")]:
                rows.append(
                    {
                        "GEO": "01", "GEO_OBJECT": "DEP", "FREQ": "A",
                        "POPREF_MEASURE": measure, "TIME_PERIOD": "2023", "OBS_VALUE": value,
                    }
                )
            write_zip(path, pd.DataFrame(rows))
            with self.assertRaisesRegex(InseeValidationError, "Population components"):
                load_population(path, year=2023, department_codes={"01"})

    def test_complete_build_from_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            raw = root / "raw"
            candidate = root / "candidates"
            geometry = root / "geodata" / "departments.geojson"
            codes = metropolitan_codes()
            self.assertEqual(len(codes), 96)
            fixture_geometry(geometry, codes)

            wage_rows = []
            for index, code in enumerate(codes):
                wage_rows.append({
                    "GEO": code, "GEO_OBJECT": "DEP", "FREQ": "A", "SEX": "_T", "AGE": "_T",
                    "DERA_MEASURE": "SALAIRE_NET_EQTP_MENSUEL_MOYENNE",
                    "CONF_STATUS": "F", "TIME_PERIOD": "2023", "OBS_VALUE": str(2200 + index),
                })
            write_zip(cache / "DS_BTS_SAL_EQTP_SEX_AGE.zip", pd.DataFrame(wage_rows))

            filosofi_rows = []
            for code in codes:
                for measure, value in [("MED_SL", "24000"), ("PR_MD60", "10.5")]:
                    filosofi_rows.append({
                        "FILOSOFI_MEASURE": measure, "GEO": code, "GEO_OBJECT": "DEP",
                        "UNIT_MEASURE": "EUR", "CONF_STATUS": "F", "OBS_STATUS": "A",
                        "UNIT_MULT": "0", "TIME_PERIOD": "2023", "OBS_VALUE": value,
                    })
            write_zip(cache / "DS_FILOSOFI_CC.zip", pd.DataFrame(filosofi_rows))

            unemployment_rows = []
            for code in codes:
                for status, value in [("1", "80"), ("2", "20"), ("1T2", "100")]:
                    unemployment_rows.append({
                        "GEO": code, "GEO_OBJECT": "DEP", "EMPSTA_ENQ": status,
                        "AGE": "Y15T64", "PCS": "_T", "RP_MEASURE": "POP",
                        "FREQ": "A", "OBS_STATUS": "A", "TIME_PERIOD": "2023", "OBS_VALUE": value,
                    })
            write_zip(cache / "DS_RP_EMPLOI_LR_COMP.zip", pd.DataFrame(unemployment_rows))

            population_rows = []
            for code in codes:
                for measure, value in [("PMUN", "1000"), ("PCAP", "10"), ("PTOT", "1010")]:
                    population_rows.append({
                        "GEO": code, "GEO_OBJECT": "DEP", "FREQ": "A",
                        "POPREF_MEASURE": measure, "TIME_PERIOD": "2023", "OBS_VALUE": value,
                    })
            write_zip(cache / "DS_POPULATIONS_REFERENCE.zip", pd.DataFrame(population_rows))

            oecd = pd.DataFrame([
                {
                    "COUNTRY": "FRA", "TERRITORIAL_LEVEL": "TL3", "REF_AREA": f"FR{index:03d}",
                    "Reference area": f"Dept {code}", "TIME_PERIOD": "2023", "MEASURE": "GDP",
                    "ACTIVITY": "_T", "PRICES": "V", "UNIT_MEASURE": "XDC",
                    "UNIT_MULT": "6", "CURRENCY": "EUR", "OBS_VALUE": str(1000 + index),
                    "OBS_STATUS": "P", "Observation status": "Provisional value",
                }
                for index, code in enumerate(codes, start=1)
            ])
            oecd.to_csv(cache / "oecd_gdp_regions.csv", index=False)

            result = build(
                year=2023,
                raw_root=raw,
                candidate_root=candidate,
                geometry_path=geometry,
                source_cache_root=cache,
            )
            self.assertEqual(result.rows, 96)
            output = pd.read_csv(result.paths["departmental_table"], dtype={"department_code": str})
            self.assertEqual(output["department_code"].tolist(), codes)
            self.assertIn("gdp_current_prices_million_eur", output.columns)
            inventory = json.loads(result.paths["source_inventory"].read_text())
            self.assertIn("source_artifact_retention", inventory)
            self.assertEqual(
                inventory["source_artifact_retention"]["policy"],
                "Source ZIP/CSV files are disposable build-cache inputs, not durable repository data.",
            )
            for artifact in inventory["artifacts"]:
                self.assertIn("cache_path", artifact)
                self.assertIn("sha256", artifact)
                self.assertGreater(artifact["bytes"], 0)
                self.assertTrue(Path(artifact["cache_path"]).is_relative_to(raw))
            manifest = json.loads(result.paths["manifest"].read_text())
            self.assertIn("source_artifact_retention_policy", manifest)

            shutil.rmtree(raw)
            self.assertFalse(raw.exists())
            self.assertTrue(result.paths["departmental_table"].is_file())
            preserved = pd.read_csv(result.paths["departmental_table"], dtype={"department_code": str})
            self.assertEqual(len(preserved), 96)

    def test_product_builds_schema_types_metadata_and_gdp_per_capita(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_root = write_candidate_fixture(root)
            lookup = root / "departments.csv"
            product_root = root / "products"
            write_lookup_fixture(lookup)

            result = build_product(
                year=2023,
                candidate_root=candidate_root,
                product_root=product_root,
                department_lookup_path=lookup,
            )

            product_path = result.paths["departmental_product"]
            output = pd.read_csv(product_path, dtype={"department_code": str})
            self.assertEqual(output.columns.tolist(), PRODUCT_COLUMNS)
            self.assertEqual(len(output), 96)
            self.assertEqual(output.loc[0, "department_code"], "01")
            self.assertIn("2A", output["department_code"].tolist())
            self.assertEqual(output.loc[0, "capital"], "Capital 01")
            self.assertEqual(output.loc[0, "region"], "Region 01")
            self.assertEqual(str(output["reference_year"].dtype), "int64")
            self.assertEqual(str(output["municipal_population"].dtype), "int64")
            self.assertEqual(str(output["gdp_per_capita_eur"].dtype), "float64")
            expected = output["gdp_current_prices_million_eur"] * 1_000_000 / output["municipal_population"]
            self.assertTrue((abs(output["gdp_per_capita_eur"] - expected) < 1e-7).all())
            for excluded in [
                "population_counted_separately",
                "total_population",
                "employed_population_15_64",
                "unemployed_population_15_64",
                "active_population_15_64",
                "oecd_tl3_code",
                "gdp_observation_status",
                "gdp_observation_status_label",
            ]:
                self.assertNotIn(excluded, output.columns)
            manifest = json.loads(result.paths["manifest"].read_text())
            self.assertEqual(manifest["rows"], 96)
            self.assertEqual(manifest["schema"], PRODUCT_COLUMNS)
            self.assertEqual(manifest["output_hash"], result.output_hash)
            self.assertIn("candidate_table", manifest["inputs"])
            self.assertIn("sha256", manifest["inputs"]["candidate_table"])
            self.assertEqual(
                manifest["derivations"]["gdp_per_capita_eur"],
                "gdp_current_prices_million_eur * 1_000_000 / municipal_population",
            )

    def test_product_serialization_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_root = write_candidate_fixture(root)
            lookup = root / "departments.csv"
            product_root = root / "products"
            write_lookup_fixture(lookup)

            first = build_product(
                year=2023,
                candidate_root=candidate_root,
                product_root=product_root,
                department_lookup_path=lookup,
            )
            second = build_product(
                year=2023,
                candidate_root=candidate_root,
                product_root=product_root,
                department_lookup_path=lookup,
                replace=True,
            )
            self.assertEqual(first.output_hash, second.output_hash)

    def test_product_fails_on_unsuccessful_candidate_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_root = write_candidate_fixture(root, failed_validation=True)
            lookup = root / "departments.csv"
            write_lookup_fixture(lookup)

            with self.assertRaisesRegex(InseeValidationError, "failed checks"):
                build_product(
                    year=2023,
                    candidate_root=candidate_root,
                    product_root=root / "products",
                    department_lookup_path=lookup,
                )

    def test_product_fails_on_duplicate_department_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_root = write_candidate_fixture(root)
            lookup = root / "departments.csv"
            write_lookup_fixture(lookup, duplicate=True)

            with self.assertRaisesRegex(InseeValidationError, "duplicate department codes"):
                build_product(
                    year=2023,
                    candidate_root=candidate_root,
                    product_root=root / "products",
                    department_lookup_path=lookup,
                )

    def test_product_fails_on_unmatched_department_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_root = write_candidate_fixture(root)
            lookup = root / "departments.csv"
            write_lookup_fixture(lookup, missing_code="2A")

            with self.assertRaisesRegex(InseeValidationError, "missing candidate department codes"):
                build_product(
                    year=2023,
                    candidate_root=candidate_root,
                    product_root=root / "products",
                    department_lookup_path=lookup,
                )


if __name__ == "__main__":
    unittest.main()
