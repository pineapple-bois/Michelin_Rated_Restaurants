# Departmental INSEE/OECD Data Pipeline

## Purpose and Scope

The `insee_pipeline` package builds a year-specific departmental reference-data
candidate from:

- INSEE Melodi departmental statistical sources;
- OECD regional GDP data;
- the repository's accepted metropolitan department geometry.

The implemented pipeline lives under `src/insee_pipeline/` and is independent
from the Michelin `data_pipeline` package. It is a separate reference-data
lifecycle, not a Stage 1/2/3 Michelin transformation.

The current validated candidate is for reference year 2023 and contains 96
metropolitan French departments. The candidate table is intentionally
provenance-rich: it retains source components, OECD mapping fields, and GDP
observation status fields that are useful for validation and reproducibility.
It is not the final Michelin-facing departmental table.

Validation writes durable candidate artifacts under `data/candidates/`; it
does not promote them or reshape them automatically. Runtime source ZIP/CSV
files are disposable cache inputs, not durable repository data. The separate
`product` command writes the narrower INSEE product consumed by the Michelin
Stage 2 France pipeline.

## Running the Pipeline

Normal invocation:

```bash
PYTHONPATH=src .venv/bin/python -m insee_pipeline build --year 2023
```

After a candidate has been validated, build the Michelin-consumable product
without rebuilding the candidate:

```bash
PYTHONPATH=src .venv/bin/python -m insee_pipeline product --year 2023
```

Implemented CLI options:

| Option | Default | Purpose |
|---|---|---|
| `--year YEAR` | required | Requested common reference year. The same year is passed through acquisition, filtering, validation, and output naming for every required source. Metrics must not independently select their newest available year. Sources must provide a valid slice for this exact year. |
| `--raw-root RAW_ROOT` | `tmp/insee_pipeline` | Backward-compatible option name for the disposable source working/cache root. INSEE files are written under `<raw-root>/<year>/insee/`; OECD files under `<raw-root>/<year>/oecd/`. |
| `--candidate-root CANDIDATE_ROOT` | `data/candidates/insee` | Root used for candidate outputs. The build writes to `<candidate-root>/<year>/`. |
| `--geometry-path GEOMETRY_PATH` | `data/raw/geodata/departments.geojson` | Department geometry source used for the 96-department target and area derivation. |
| `--source-cache-root SOURCE_CACHE_ROOT` | unset | Optional local cache consulted before network download. The pipeline looks for files matching the destination filename, such as `DS_FILOSOFI_CC.zip` or `oecd_gdp_regions.csv`, and copies them into the disposable source working path when the working file is absent. |
| `--legacy-statistics-path LEGACY_STATISTICS_PATH` | `data/raw/demographics/departmental_stats_2023.csv` | Optional legacy file used only for the candidate validation report's comparison section. It is not an input to metric derivation. |
| `--replace` | false | Allows existing candidate output files to be replaced. Without this flag, the build refuses to overwrite existing candidate table, crosswalk, manifest, source inventory, or validation report files. |

The `product` command supports:

| Option | Default | Purpose |
|---|---|---|
| `--year YEAR` | required | Requested product year. The product command requires an existing candidate manifest, validation report, and candidate table for this year. |
| `--candidate-root CANDIDATE_ROOT` | `data/candidates/insee` | Root containing validated candidate artifacts. The command reads from `<candidate-root>/<year>/`. |
| `--product-root PRODUCT_ROOT` | `data/products/insee` | Root used for Michelin-consumable product outputs. The command writes to `<product-root>/<year>/`. |
| `--department-lookup-path DEPARTMENT_LOOKUP_PATH` | `data/raw/demographics/departments.csv` | Authoritative department-code lookup used to add `capital` and `region`. |
| `--replace` | false | Allows existing product CSV or product manifest to be replaced. |

Source working files are reused rather than re-downloaded when the expected
cache path already exists, but they are safe to delete and regenerate.
Candidate outputs are protected separately by the `--replace` flag.

## Pipeline Stages

The implemented lifecycle is:

1. Resolve and acquire source artifacts with `sources.acquire_sources()`.
2. Write year-versioned disposable source working files under
   `tmp/insee_pipeline/<year>/insee/` and `tmp/insee_pipeline/<year>/oecd/`
   by default.
3. Load INSEE ZIP data with `load.load_departmental_zip_data()`, retaining only
   departmental observations before source-specific filtering.
4. Apply named source loaders in `transform.py`:
   `load_wages()`, `load_filosofi()`, `load_unemployment()`,
   `load_population()`, `load_department_geometry()`, and `load_oecd_gdp()`.
5. Derive unemployment, population component checks, geometry area, population
   density, and the OECD TL3-to-department mapping.
6. Validate source coverage, reconciliation, uniqueness, required values, and
   output serialization.
7. Write the departmental candidate table, OECD crosswalk, source inventory,
   manifest, and validation report.
8. Optionally transform the validated candidate into a narrower Michelin-facing
   product with `product.build_product()`.

The package uses shared helpers for ZIP inspection, chunked CSV loading,
requested-year filtering, numeric conversion, SHA-256 hashing, and deterministic
CSV/JSON serialization, but keeps statistical source logic visible in named
loader functions.

## Source Provenance

### Source Families

| Source family | Implemented role |
|---|---|
| INSEE Melodi wage data | `DS_BTS_SAL_EQTP_SEX_AGE`; source for `average_net_monthly_wage_fte_eur`. |
| INSEE Filosofi data | `DS_FILOSOFI_CC`; source for `median_living_standard_eur` and `poverty_rate_percent`. |
| INSEE census employment-status data | `DS_RP_EMPLOI_LR_COMP`; source components for `census_unemployment_rate_15_64_percent`. |
| INSEE reference population data | `DS_POPULATIONS_REFERENCE`; source components for `municipal_population`, `population_counted_separately`, and `total_population`. |
| Departmental geometry | `data/raw/geodata/departments.geojson`; source for 96-department geography and geometry-derived `area_sq_km`. |
| OECD TL3 GDP data | `OECD.CFE.EDS:DSD_REG_ECO@DF_GDP(2.4)`; source for `gdp_current_prices_million_eur` and GDP status fields. |

### Implemented Source Filters

Every statistical source is filtered to the requested common `--year`. The
implemented source selectors are:

| Output field(s) | Source | Principal filters and measures |
|---|---|---|
| `average_net_monthly_wage_fte_eur` | INSEE Melodi `DS_BTS_SAL_EQTP_SEX_AGE` | `GEO_OBJECT == "DEP"`, `FREQ == "A"`, `SEX == "_T"`, `AGE == "_T"`, `DERA_MEASURE == "SALAIRE_NET_EQTP_MENSUEL_MOYENNE"`, requested `TIME_PERIOD`. |
| `median_living_standard_eur`, `poverty_rate_percent` | INSEE Melodi `DS_FILOSOFI_CC` | `GEO_OBJECT == "DEP"`, `FILOSOFI_MEASURE in {"MED_SL", "PR_MD60"}`, requested `TIME_PERIOD`. |
| `census_unemployment_rate_15_64_percent` and employment components | INSEE Melodi `DS_RP_EMPLOI_LR_COMP` | `GEO_OBJECT == "DEP"`, `FREQ == "A"`, `PCS == "_T"`, `AGE == "Y15T64"`, `EMPSTA_ENQ in {"1", "2", "1T2"}`, requested `TIME_PERIOD`. |
| `municipal_population`, `population_counted_separately`, `total_population` | INSEE Melodi `DS_POPULATIONS_REFERENCE` | `GEO_OBJECT == "DEP"`, `FREQ == "A"`, `POPREF_MEASURE in {"PMUN", "PCAP", "PTOT"}`, requested `TIME_PERIOD`. |
| `gdp_current_prices_million_eur` and GDP status fields | OECD `OECD.CFE.EDS:DSD_REG_ECO@DF_GDP(2.4)` | `COUNTRY == "FRA"`, `TERRITORIAL_LEVEL == "TL3"`, requested `TIME_PERIOD`, `MEASURE == "GDP"`, `ACTIVITY == "_T"`, `PRICES == "V"`, `UNIT_MEASURE == "XDC"`, `UNIT_MULT == "6"`, `CURRENCY == "EUR"`. |

The authoritative geography set is the 96 metropolitan department codes in
`data/raw/geodata/departments.geojson`. Overseas source observations and
non-regionalised source observations are excluded by design because they do not
belong to this 96-code target.

OECD TL3 records are reconciled to departments through an explicit controlled
crosswalk artifact, `oecd_tl3_crosswalk_<year>.csv`, containing department code,
department name, OECD TL3 code, and OECD reference-area name. Runtime fuzzy
matching is not part of the pipeline contract.

### Source Decisions

Three discovery decisions are now settled in the implemented pipeline:

- INSEE regional accounts were investigated for GDP, but did not provide the
  required 96-department GDP coverage for this contract; OECD TL3 GDP is used
  instead.
- The old local-statistics export is superseded by source-specific INSEE
  Melodi datasets with explicit measure codes, statuses, and a common
  reference year.
- Arrondissement-level INSEE statistics are outside this departmental reference
  pipeline. Stage 3 arrondissement products are built from restaurant data and
  local geography, not arrondissement-level INSEE metrics.

### Source Cache Paths

For 2023, source ZIP/CSV working files are written by default to:

```text
tmp/insee_pipeline/2023/insee/DS_BTS_SAL_EQTP_SEX_AGE.zip
tmp/insee_pipeline/2023/insee/DS_FILOSOFI_CC.zip
tmp/insee_pipeline/2023/insee/DS_RP_EMPLOI_LR_COMP.zip
tmp/insee_pipeline/2023/insee/DS_POPULATIONS_REFERENCE.zip
tmp/insee_pipeline/2023/oecd/oecd_gdp_regions.csv
```

These files are cache/working files. They are ignored by Git through `/tmp/`,
may be reused between builds, and may be deleted at any time. The repository
does not undertake long-term archival of upstream binary artifacts.

The source inventory records provider, source identifier, source URL,
`cache_path`, byte size, SHA-256 hash, observation timestamp, fetch flag, and
retention policy. If a working file is absent, the pipeline first checks
`--source-cache-root`; if no matching cache file exists, it downloads the
source. Any recorded source path is a build-time cache path, not a guaranteed
retained artifact.

Reproducibility is anchored by explicit source identifiers, URLs, filters,
hashes, byte sizes, candidate outputs, validation reports, and pipeline code.
Byte-perfect reconstruction depends on the upstream source bytes remaining
available unless those binaries are archived outside this repository.

## Candidate Artifacts

Validated 2023 candidate artifacts:

| Path | Purpose |
|---|---|
| `data/candidates/insee/2023/france_departments_2023.csv` | Main 96-row departmental candidate table. |
| `data/candidates/insee/2023/oecd_tl3_crosswalk_2023.csv` | Candidate OECD TL3-to-department crosswalk used by the build, with department code/name and OECD TL3 code/name. |
| `data/candidates/insee/2023/source_inventory_2023.json` | Source provenance inventory with cache paths, URLs, hashes, byte sizes, observation timestamps, retention policy, and source families. |
| `data/candidates/insee/2023/manifest_2023.json` | Candidate manifest with reference year, row count, schema, output paths, and legacy comparison summary. |
| `data/candidates/insee/2023/validation_report_2023.json` | Machine-readable validation report with all implemented checks and the legacy comparison block. |

These remain candidate artifacts even after validation passes. Validation does
not promote the files to `data/raw/demographics/`, does not update Stage 2
statistics inputs, and does not alter the Michelin product pipeline.

## Michelin-Consumable Product

The `product` command converts the validated departmental candidate into a
smaller asset intended for later Michelin pipeline consumption. It reads the
existing candidate artifacts under `data/candidates/insee/<year>/`; it does not
acquire sources, rebuild the candidate, or modify candidate reports.

Validated 2023 product artifacts:

| Path | Purpose |
|---|---|
| `data/products/insee/2023/france_departments_2023.csv` | Michelin-consumable 96-row departmental product table. |
| `data/products/insee/2023/manifest_2023.json` | Product manifest with input paths and hashes, schema, dtypes, derivations, excluded candidate fields, row count, and output hash. |

The product joins `data/raw/demographics/departments.csv` by exact department
code to add `capital` and `region`. The join is validated as one-to-one and
fails on duplicate or unmatched department codes. Department codes are strings,
so leading zeros and Corsican codes such as `2A` and `2B` are preserved.

The ordered product schema is:

```python
[
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
```

`gdp_per_capita_eur` is derived as:

```text
gdp_current_prices_million_eur * 1_000_000 / municipal_population
```

The product intentionally excludes the candidate's provenance and
reconciliation fields:

- `population_counted_separately`
- `total_population`
- `employed_population_15_64`
- `unemployed_population_15_64`
- `active_population_15_64`
- `oecd_tl3_code`
- `gdp_observation_status`
- `gdp_observation_status_label`

Those fields remain available in the candidate dataset, source inventory,
manifest, crosswalk, and validation report.

The product stage enforces explicit type classes: department identifiers,
department names, capitals, and regions as strings; `reference_year` and
`municipal_population` as integers; all remaining metric columns as
floating-point numeric values. It requires an existing candidate manifest and a
validation report whose checks all passed, requires exactly 96 departments,
rejects null required values, validates the GDP-per-capita derivation, and
writes deterministic CSV output.

## Canonical Candidate Schema

`france_departments_2023.csv` currently has 19 columns.

| Column | Meaning | Unit | Expected type class | Role | Source or derivation |
|---|---|---|---|---|---|
| `department_code` | Metropolitan department code, including Corsica codes `2A` and `2B`. | none | string | Geometry/provenance field | Repository geometry `code`; defines the 96-code target and joins all source slices. |
| `department_name` | Department name from accepted geometry. | none | string | Geometry/provenance field | Repository geometry `nom`. |
| `reference_year` | Candidate tranche year requested by CLI. | year | integer | Join/provenance field | CLI `--year`. |
| `average_net_monthly_wage_fte_eur` | Average net monthly wage in full-time-equivalent terms. | euros per month | numeric float | Directly sourced primary metric | Direct INSEE observation from `DS_BTS_SAL_EQTP_SEX_AGE`, measure `SALAIRE_NET_EQTP_MENSUEL_MOYENNE`, total sex, total age. |
| `median_living_standard_eur` | Median living standard. | euros | integer-compatible numeric | Directly sourced primary metric | Direct INSEE Filosofi observation `MED_SL`. |
| `poverty_rate_percent` | Poverty rate. | percent | numeric float | Directly sourced primary metric | Direct INSEE Filosofi observation `PR_MD60`. |
| `census_unemployment_rate_15_64_percent` | Census-derived unemployment rate for ages 15-64. | percent | numeric float | Calculated primary metric | Derived from INSEE employment statuses: unemployed active population divided by active population times 100. |
| `municipal_population` | Municipal population used as resident population and density numerator. | people | integer-compatible numeric | Directly sourced primary metric | Direct INSEE reference-population observation `PMUN`. |
| `area_sq_km` | Department area derived from geometry. | square kilometres | numeric float | Geometry-derived primary metric | Geometry reprojected to `EPSG:2154`; `geometry.area / 1_000_000`. |
| `population_density_per_sq_km` | Municipal-population density. | people per square kilometre | numeric float | Calculated primary metric | Derived as `municipal_population / area_sq_km`. |
| `gdp_current_prices_million_eur` | Gross domestic product at current prices. | million euros | numeric float | Directly sourced primary metric | Direct OECD TL3 GDP observation after source filtering and department mapping. |
| `population_counted_separately` | Population counted separately. | people | integer-compatible numeric | Reconciliation/provenance field | Direct INSEE reference-population observation `PCAP`; retained to validate `PMUN + PCAP == PTOT`. |
| `total_population` | Total population. | people | integer-compatible numeric | Reconciliation/provenance field | Direct INSEE reference-population observation `PTOT`; retained to validate `PMUN + PCAP == PTOT`. |
| `employed_population_15_64` | Employed active population age 15-64. | people | numeric float | Reconciliation/provenance field | Direct INSEE census employment-status observation `EMPSTA_ENQ == "1"`. |
| `unemployed_population_15_64` | Unemployed population age 15-64. | people | numeric float | Reconciliation/provenance field | Direct INSEE census employment-status observation `EMPSTA_ENQ == "2"`. |
| `active_population_15_64` | Active population age 15-64. | people | numeric float | Reconciliation/provenance field | Direct INSEE census employment-status observation `EMPSTA_ENQ == "1T2"`. |
| `oecd_tl3_code` | OECD TL3 reference-area code mapped to the department. | none | string | Mapping/provenance field | OECD `REF_AREA`, retained from the GDP mapping. |
| `gdp_observation_status` | OECD GDP observation status code. | none | string | Mapping/provenance field | OECD `OBS_STATUS`; 2023 candidate values are `P`. |
| `gdp_observation_status_label` | OECD GDP observation status label. | none | string | Mapping/provenance field | OECD `Observation status`; 2023 candidate values are `Provisional value`. |

The schema deliberately retains population components, unemployment components,
OECD mapping information, and GDP status fields. Those columns are part of the
candidate's reproducibility and validation evidence, even if a future
Michelin-facing table selects fewer columns.

## Metric Definitions and Safeguards

Implemented metric definitions:

- `average_net_monthly_wage_fte_eur`: INSEE net monthly wage in
  full-time-equivalent terms, total sex and total age.
- `median_living_standard_eur`: INSEE Filosofi median living standard.
- `poverty_rate_percent`: INSEE Filosofi poverty rate.
- `census_unemployment_rate_15_64_percent`: derived census unemployment rate,
  `unemployed_population_15_64 / active_population_15_64 * 100`.
- `municipal_population`: INSEE reference population `PMUN`.
- `area_sq_km`: area derived from department geometry after reprojection to
  `EPSG:2154`.
- `population_density_per_sq_km`: `municipal_population / area_sq_km`.
- `gdp_current_prices_million_eur`: OECD TL3 GDP at current prices in million
  euros.
- `gdp_observation_status` and `gdp_observation_status_label`: OECD status
  fields; all 96 mapped 2023 GDP observations are provisional (`P`,
  `Provisional value`) in the current validation report.

Implemented validation safeguards:

- the department geometry must contain exactly 96 unique metropolitan
  departments in `EPSG:4326`;
- the candidate table must contain exactly 96 unique `department_code` values;
- source loaders filter to the requested `--year`; they do not silently select
  the latest available year;
- required output values for the canonical metric columns must be non-null;
- wage coverage must produce 96 rows; the 2023 report records `CONF_STATUS: F`
  for all 96 rows;
- Filosofi `MED_SL` and `PR_MD60` coverage must each produce 96 rows; the 2023
  report records `CONF_STATUS: F` and `OBS_STATUS: A` for all 96 rows;
- unemployment components must reconcile; the 2023 report records maximum
  active-population balance of approximately `1.00000761449337e-05`;
- population components must reconcile; the 2023 report records maximum
  population balance `0.0`;
- geometry-derived area must be non-null and positive;
- OECD GDP must have no duplicate `REF_AREA`/`TIME_PERIOD` rows for the
  filtered slice and must map to 96 metropolitan departments;
- candidate CSV writes are reloaded and compared with the in-memory dataframe
  with dtype differences ignored, which guards deterministic serialization at
  the table level.

The 2023 OECD validation report records 102 raw French TL3 GDP rows. Ninety-six
map to metropolitan departments. The unmatched reference areas are:
`Martinique`, `La Réunion`, `French Guiana`, `Guadeloupe`, `Mayotte`, and
`France, not regionalised`.

## Relationship to Legacy Departmental Data

The candidate is not schema-compatible or definition-compatible with:

```text
data/raw/demographics/departmental_stats_2023.csv
```

The validation report compares the candidate to that legacy file only as a
diagnostic. It reports 96 candidate rows, 96 legacy rows, and 96 shared
department codes.

Important differences:

- the candidate has 19 provenance-rich columns; the legacy file has 12 columns;
- the legacy file includes `department_num`, `department`, `capital`, and
  `region`, while the candidate currently carries `department_code` and
  geometry-derived `department_name`;
- legacy numeric field names and definitions are not all equivalent to the
  candidate metrics;
- wage units differ: the candidate uses monthly net full-time-equivalent wage,
  not the legacy hourly wage field;
- unemployment definitions differ: the candidate uses a census-derived
  unemployment rate for ages 15-64, not the legacy annual-average unemployment
  field;
- candidate GDP retains OECD observation status fields, including the 2023
  provisional status;
- candidate population and employment component fields are retained for
  reconciliation and reproducibility.

Do not rename the candidate wage or unemployment fields to the legacy names:
the definitions are different.

## Michelin Integration

The product table is the Michelin-consumable INSEE/OECD asset used by Stage 2
France departmental processing. Stage 2 selects the latest numeric product year
under `data/products/insee/` unless an explicit INSEE year is supplied.

Future integration changes should:

- select only the numeric metrics required by the Michelin pipeline;
- rename columns only when the names remain definitionally accurate;
- enforce explicit output dtypes;
- preserve the canonical provenance-rich candidate separately;
- preserve the existing Stage 2 join and publication contract unless a
  separately reviewed schema change updates it.

The current implementation does not replace
`data/raw/demographics/departmental_stats_2023.csv`.

## Data Types

The stored candidate CSV is plain text. When loaded with pandas using
`dtype={"department_code": str}`, the current 2023 candidate reads as:

| Column group | Intended type class | Current pandas read behavior |
|---|---|---|
| Department identifiers and OECD codes | string | `department_code`, `department_name`, `oecd_tl3_code`, `gdp_observation_status`, and `gdp_observation_status_label` read as `object`. |
| Reference year | integer | `reference_year` reads as `int64`. |
| Population counts | integer-compatible numeric | `municipal_population`, `population_counted_separately`, and `total_population` read as `int64`. |
| Employment counts | numeric; integer-compatible conceptually, but source values are fractional census estimates | `employed_population_15_64`, `unemployed_population_15_64`, and `active_population_15_64` read as `float64`. |
| Rates, currency measures, area, and density | numeric | Wage, poverty rate, unemployment rate, area, density, and GDP read as `float64`; `median_living_standard_eur` reads as `int64` for the 2023 candidate. |

Future consumer-facing outputs should define their own explicit dtype contract
instead of relying on pandas inference from CSV.

The product command applies its own dtype contract before writing:

| Product column group | Enforced in-memory type class |
|---|---|
| `department_code`, `department_name`, `capital`, `region` | pandas string |
| `reference_year`, `municipal_population` | `int64` |
| all metric columns, including `gdp_per_capita_eur` | `float64` |

## Testing and Known Limitations

Run the focused INSEE pipeline tests with:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_insee_pipeline -v
```

The new INSEE tests pass. They cover requested-year filtering, fail-closed
unemployment reconciliation, fail-closed population reconciliation, a complete
local-cache candidate build using fixture data without live network calls, and
the candidate-to-product transformation including schema, dtypes, code
preservation, metadata joins, GDP-per-capita derivation, failure conditions,
and deterministic serialization.

Broader repository test-suite note: `PYTHONPATH=src .venv/bin/python -m unittest
discover -s tests -v` may fail when expected legacy `Years/...` baseline files
are absent. That fidelity-baseline availability issue is separate from the
INSEE pipeline tests.

Known limitations:

- 2023 is the first validated tranche.
- No legacy-shaped departmental table has been produced.
- Candidate retention, product publication, and Michelin integration are
  separate concerns.
- Source ZIP/CSV files are disposable cache inputs. Byte-perfect reconstruction
  depends on the upstream sources remaining available unless source binaries
  are archived externally.
- The current repository policy does not include long-term archival, Git LFS,
  or external object storage for upstream source binaries.
- The OECD TL3 crosswalk is currently written as a candidate artifact; it has
  not yet been promoted as an accepted static reference.
