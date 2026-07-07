# INSEE Departmental Demographics ETL

## 1. Purpose

This document records the completed demographic-source discovery work in
`demographics_data.ipynb` and defines the plan for converting that work into a
formal, year-versioned departmental ETL tranche.

The current repository still uses `data/raw/demographics/departmental_stats_2023.csv`
as a legacy accepted snapshot for Stage 2 France products. That file is useful
as a migration input, but it does not carry enough source provenance, source
measure codes, release status, or year-selection evidence to serve as the
future demographic contract.

The new ETL should produce a coherent departmental tranche for one reference
year. It should distinguish:

- observations published directly by INSEE or OECD;
- values derived from published observations;
- values derived from geometry;
- static controlled crosswalks used to join external geography codes.

This document is a design and source contract. It does not implement the ETL.

## 2. Geographic Scope

The ETL is departmental only.

The target geography is the 96 metropolitan French departments represented by
`data/raw/geodata/departments.geojson`. That geometry file has 96 rows, columns
`code`, `nom`, and `geometry`, and CRS `EPSG:4326`.

Overseas departments are intentionally excluded. Source datasets may expose 97
or 100 departmental rows before filtering; the ETL must keep only the 96
metropolitan department codes that reconcile with the accepted geometry.

Arrondissement-level INSEE statistics are out of scope. They are not used by
the downstream Stage 2 departmental products and should not appear in the
intended demographic ETL schema.

## 3. Reference-Year Policy

The output represents a coherent demographic tranche for `<year>`. The ETL
must select the latest fully validated common year across all required sources,
not the newest year independently available for each metric.

A candidate year is valid only when:

- every required metric is available for that year;
- all 96 metropolitan departments are represented;
- there is one observation per department per metric after filtering;
- required values are non-null;
- source units and measure codes match this contract;
- reconciliation checks pass;
- OECD GDP has a corresponding complete French TL3 slice;
- every required OECD TL3 record maps through the controlled crosswalk.

For the currently validated discovery tranche, the common year is 2023. A
newer tranche should not be emitted until all required sources are complete and
validated for the same year.

## 4. Source Inventory

### Source-to-output specification from `demographics_data.ipynb`

| Output metric | Provider | Dataset or dataflow | Source URL/API | Source codes and filters | Available years observed | Selected year | Coverage shown in discovery | Transformation and checks |
|---|---|---|---|---|---|---:|---|---|
| `average_net_monthly_wage_fte_eur` | INSEE Melodi | `DS_BTS_SAL_EQTP_SEX_AGE` | `https://api.insee.fr/melodi/file/DS_BTS_SAL_EQTP_SEX_AGE/DS_BTS_SAL_EQTP_SEX_AGE_2023_CSV_FR` | `GEO_OBJECT == "DEP"`, `FREQ == "A"`, `SEX == "_T"`, `AGE == "_T"`, `DERA_MEASURE == "SALAIRE_NET_EQTP_MENSUEL_MOYENNE"` | 2022-2023 observed in the ZIP | 2023 | 100 DEP rows before metropolitan filtering; 96 intersect the geometry codes; no missing values; `CONF_STATUS == "F"` in discovery output | Cast `OBS_VALUE` to numeric and rename. This is net monthly full-time-equivalent wage, not hourly wage. |
| `median_living_standard_eur` | INSEE Melodi | `DS_FILOSOFI_CC` | `https://api.insee.fr/melodi/file/DS_FILOSOFI_CC/DS_FILOSOFI_CC_2023_CSV_FR` | `GEO_OBJECT == "DEP"`, `FILOSOFI_MEASURE == "MED_SL"` | 2023 observed in the ZIP | 2023 | 97 DEP rows before metropolitan filtering; 96 intersect the geometry codes; no missing values; `CONF_STATUS == "F"`, `OBS_STATUS == "A"` in discovery output | Cast `OBS_VALUE` to numeric and rename. |
| `poverty_rate_percent` | INSEE Melodi | `DS_FILOSOFI_CC` | `https://api.insee.fr/melodi/file/DS_FILOSOFI_CC/DS_FILOSOFI_CC_2023_CSV_FR` | `GEO_OBJECT == "DEP"`, `FILOSOFI_MEASURE == "PR_MD60"` | 2023 observed in the ZIP | 2023 | 97 DEP rows before metropolitan filtering; 96 intersect the geometry codes; no missing values; `CONF_STATUS == "F"`, `OBS_STATUS == "A"` in discovery output | Cast `OBS_VALUE` to numeric and rename. |
| `census_unemployment_rate_15_64_percent` | INSEE Melodi | `DS_RP_EMPLOI_LR_COMP` | `https://api.insee.fr/melodi/file/DS_RP_EMPLOI_LR_COMP/DS_RP_EMPLOI_LR_COMP_2023_CSV_FR` | `GEO_OBJECT == "DEP"`, `FREQ == "A"`, `PCS == "_T"`, `AGE == "Y15T64"`, `EMPSTA_ENQ in {"1", "2", "1T2"}` | 2012-2023 according to catalogue output | 2023 | 300 rows for three employment-status codes across 100 DEP geographies before metropolitan filtering; 96 intersect the geometry codes; no missing values; `OBS_STATUS == "A"` in discovery output | Pivot statuses, derive `unemployed_population_15_64 / active_population_15_64 * 100`, and validate `employed + unemployed ~= active`. |
| `municipal_population`, optional `population_counted_separately`, optional `total_population` | INSEE Melodi | `DS_POPULATIONS_REFERENCE` | `https://api.insee.fr/melodi/file/DS_POPULATIONS_REFERENCE/DS_POPULATIONS_REFERENCE_2023_CSV_FR` | `GEO_OBJECT == "DEP"`, `FREQ == "A"`, `POPREF_MEASURE in {"PMUN", "PCAP", "PTOT"}` | 2023 observed in the ZIP | 2023 | 300 rows for three population measures across 100 DEP geographies before metropolitan filtering; 96 intersect the geometry codes; no missing values | Pivot measures and validate `PMUN + PCAP == PTOT`. Use `PMUN` as resident population and density numerator. |
| `area_sq_km` | Repository geometry | `data/raw/geodata/departments.geojson` | local file | 96 accepted metropolitan department geometries, source CRS `EPSG:4326` | Geometry has no statistical reference year in the discovery notebook | 2023 tranche companion | 96 rows, 96 unique department codes | Reproject to `EPSG:2154`; derive `geometry.area / 1_000_000`; validate positive non-null area. |
| `population_density_per_sq_km` | Derived from INSEE population plus repository geometry | `DS_POPULATIONS_REFERENCE` + `departments.geojson` | local and Melodi file APIs | `PMUN` joined one-to-one to department area | 2023 | 2023 | 96 metropolitan departments after inner join; notebook raises if count is not 96, codes are duplicated, values are missing, or area is non-positive | Derive `municipal_population / area_sq_km`. |
| `gdp_current_prices_million_eur` | OECD Data Explorer / OECD SDMX API | `OECD.CFE.EDS:DSD_REG_ECO@DF_GDP(2.4)`; title `Gross domestic product - Regions` | `https://sdmx.oecd.org/public/rest/data/OECD.CFE.EDS,DSD_REG_ECO@DF_GDP,2.4/all` with `dimensionAtObservation=AllDimensions`, `format=csvfilewithlabels` | `COUNTRY == "FRA"`, `TERRITORIAL_LEVEL == "TL3"`, `MEASURE == "GDP"`, `ACTIVITY == "_T"`, `PRICES == "V"`, `UNIT_MEASURE == "XDC"`, `UNIT_MULT == "6"`, `CURRENCY == "EUR"`, annual frequency | 2020-2024 complete slices observed in discovery output | 2023 | 102 French TL3 records in 2023 before metropolitan filtering; 96 name-matched to metropolitan department geometry; no duplicate territory-year rows; all 2023 records have `OBS_STATUS == "P"` / `Provisional value` | Cast `OBS_VALUE` to numeric, rename, retain provisional status, and join through a controlled TL3 crosswalk. |

The discovery notebook uses `https://api.insee.fr/melodi/catalog/all?lang=fr`
and per-dataset `https://api.insee.fr/melodi/catalog/<dataset_id>?lang=fr`
metadata calls to find and inspect INSEE products before downloading ZIP files.
The formal ETL should persist the downloaded raw archives and the relevant
metadata payloads.

## 5. Metric Definitions

### Salary

Canonical field: `average_net_monthly_wage_fte_eur`.

Source: INSEE `DS_BTS_SAL_EQTP_SEX_AGE`, measure
`SALAIRE_NET_EQTP_MENSUEL_MOYENNE`, total age, total sex, annual departmental
observations.

This is net monthly full-time-equivalent salary. It supersedes the older
legacy field `average_net_hourly_wage(€)`.

### Living standards and poverty

Source: INSEE `DS_FILOSOFI_CC`.

- `FILOSOFI_MEASURE == "MED_SL"` becomes `median_living_standard_eur`.
- `FILOSOFI_MEASURE == "PR_MD60"` becomes `poverty_rate_percent`.

Both fields are direct INSEE observations after source filtering and numeric
conversion.

### Unemployment

Canonical field: `census_unemployment_rate_15_64_percent`.

Source: INSEE `DS_RP_EMPLOI_LR_COMP`, with `GEO_OBJECT == "DEP"`, annual
frequency, `PCS == "_T"`, and `AGE == "Y15T64"`.

Employment-status codes:

- `1`: employed active population;
- `2`: unemployed population;
- `1T2`: active population.

The metric is derived as:

```text
unemployed_population_15_64 / active_population_15_64 * 100
```

The ETL must validate:

```text
employed_population_15_64 + unemployed_population_15_64 ~= active_population_15_64
```

This is a census-derived unemployment rate. It must not be described as the
localised annual-average unemployment rate.

### Population

Source: INSEE `DS_POPULATIONS_REFERENCE`.

Reference-population measures:

- `PMUN`: municipal population;
- `PCAP`: population counted separately;
- `PTOT`: total population.

The ETL must validate:

```text
PMUN + PCAP == PTOT
```

Use `municipal_population` as the resident-population measure and as the
population-density numerator. `population_counted_separately` and
`total_population` are useful audit fields and may live in the main output or a
companion metadata table.

## 6. Departmental Area and Density Derivation

Area is geometry-derived, not an INSEE-published statistical field.

Input: `data/raw/geodata/departments.geojson`.

Process:

1. Read the accepted 96-feature metropolitan department geometry in `EPSG:4326`.
2. Keep `code`, `nom`, and `geometry`.
3. Reproject to Lambert-93, `EPSG:2154`.
4. Derive:

```text
area_sq_km = geometry.area / 1_000_000
```

5. Join `municipal_population` one-to-one by department code.
6. Derive:

```text
population_density_per_sq_km = municipal_population / area_sq_km
```

The discovery notebook validates exactly 96 rows after the population-area
join, 96 unique department codes, non-null population and area, and positive
area.

## 7. OECD GDP and TL3 Crosswalk

GDP source:

- Provider: OECD Data Explorer / OECD SDMX API.
- Dataflow: `OECD.CFE.EDS:DSD_REG_ECO@DF_GDP(2.4)`.
- Dataset title: `Gross domestic product - Regions`.
- Metric: `gdp_current_prices_million_eur`.
- Unit contract: current prices, national currency, million euros
  (`PRICES == "V"`, `UNIT_MEASURE == "XDC"`, `UNIT_MULT == "6"`,
  `CURRENCY == "EUR"`).

The 2023 observations are provisional in the discovery output
(`OBS_STATUS == "P"`, `Observation status == "Provisional value"`). That
status must be retained in the output metadata or a companion status table.

OECD TL3 geography is not the same identifier system as INSEE department
codes. The discovery notebook initially normalises OECD `Reference area` names
and GeoJSON `nom` values, then performs a name-based join. This is acceptable
for discovery, but it is not acceptable as the recurring production join.

The formal ETL must create and persist a validated static crosswalk with at
least:

```text
GEO
department_name
oecd_tl3_code
oecd_reference_area_name
```

Future GDP joins must use the persisted OECD TL3 code. Name normalisation may
be used only to construct or validate that crosswalk. The pipeline must fail on
unmatched or duplicate metropolitan departments.

Discovery results to preserve:

- 102 French TL3 GDP records exist for 2023 before filtering.
- Six do not match the metropolitan department geometry by name:
  `France, not regionalised`, `French Guiana`, `Guadeloupe`, `La Réunion`,
  `Martinique`, and `Mayotte`.
- 96 records match the metropolitan department geometry.
- Duplicate territory-year rows for the 2023 GDP slice are zero.

## 8. Rejected or Superseded Sources

### Rejected GDP source: INSEE `DS_COMPTES_REGIONAUX`

`DS_COMPTES_REGIONAUX` was investigated because catalogue metadata advertises
departmental availability. The downloaded data does not provide the required 96
departmental GDP observations. Its observed `GEO_OBJECT` values are `FRANCE`,
`REG`, and `OTHER`, with regional and aggregate geography codes rather than
the 96 metropolitan departments.

It is therefore rejected for departmental GDP.

### Superseded legacy INSEE local-statistics export

The legacy notebook used `Demographics/INSEE/stats_locales_2023.csv`, a local
export from INSEE Statistiques locales. It retained mixed-reference-year
fields:

- poverty rate from 2021;
- annual-average unemployment rate from 2023;
- net hourly wage from 2022;
- municipal population from 2022;
- historical population density from 2021;
- non-schooled persons age 15+ from 2021, later dropped.

That file is superseded by the source-specific Melodi datasets and the
common-reference-year policy.

### Removed arrondissement statistics

`data/raw/demographics/arrondissement_stats_2023.csv`,
`data/raw/demographics/paris_arrondissements.csv`, and the legacy
arrondissement demographic idea are not part of the new ETL. The intended
schema is departmental only.

## 9. Legacy Notebook Comparison

The comparison here is material, not a line-by-line diff.

| Area | Legacy `France_Departments_Demographics.ipynb` | New departmental ETL direction |
|---|---|---|
| Geography | Drops the last five rows of local exports as overseas departments; assumes row order. | Uses the accepted 96-code metropolitan geometry as the explicit geography contract. |
| Salary | `average_net_hourly_wage(€)` from Statistiques locales, labelled 2022. | `average_net_monthly_wage_fte_eur` from `DS_BTS_SAL_EQTP_SEX_AGE`, 2023, total age and sex. Definition and unit change. |
| Poverty | `poverty_rate(%)`, source year 2021. | `poverty_rate_percent` from `DS_FILOSOFI_CC` / `PR_MD60`, selected common year 2023. |
| Living standards | Not retained as a canonical legacy output field. | Adds `median_living_standard_eur` from `DS_FILOSOFI_CC` / `MED_SL`. |
| Unemployment | `average_annual_unemployment_rate(%)`, source year 2023, from local export. | `census_unemployment_rate_15_64_percent`, derived from census active-population components in `DS_RP_EMPLOI_LR_COMP`. Definition changes and should be named accordingly. |
| Population | `municipal_population`, source year 2022. | `municipal_population` from `DS_POPULATIONS_REFERENCE` / `PMUN`, common year 2023; optional `PCAP` and `PTOT` available for reconciliation. |
| Density | `population_density(inhabitants/sq_km)` from local export, source year 2021. | `population_density_per_sq_km` derived from 2023 municipal population and geometry-derived area. |
| Area | Back-calculated from population and legacy density. | Derived from department geometry reprojected to `EPSG:2154`. |
| GDP | `GDP_millions(€)` from `Demographics/GDP_departmental.csv`, most recent selected year 2020; source/path provenance is limited. | `gdp_current_prices_million_eur` from OECD TL3 GDP, current prices, million euros, 2023 provisional, joined through a controlled TL3 crosswalk. |
| GDP per capita | Derived and retained as `GDP_per_capita(€)`. | Not required in the minimum canonical schema. It may be derived later if a consumer contract requires it, but GDP and population provenance should remain separate first. |
| Dropped education metric | Drops `non_schooled_persons_15_and_over` as weak. | Not included. |
| Provenance/status | Mostly implicit in filenames and notebook text. | Source identifiers, measure codes, selected year, units, statuses, and crosswalk version must be explicit. |

Missing or unexplained legacy provenance remains for `GDP_departmental.csv`:
its OECD extraction path, price basis, unit contract, and geography mapping are
not sufficiently documented in the legacy notebook.

## 10. Proposed Canonical Schema

Minimum departmental output columns:

```text
department_code
department_name
reference_year
average_net_monthly_wage_fte_eur
median_living_standard_eur
poverty_rate_percent
census_unemployment_rate_15_64_percent
municipal_population
area_sq_km
population_density_per_sq_km
gdp_current_prices_million_eur
```

Useful optional audit columns in the main table:

```text
population_counted_separately
total_population
employed_population_15_64
unemployed_population_15_64
active_population_15_64
oecd_tl3_code
```

Useful provenance/status fields, either in a companion metadata table or a
manifest:

```text
source_provider
dataset_id_or_dataflow
source_url
source_archive_path
source_measure_code
source_unit_code
source_unit_label
source_frequency
source_confidentiality_status
source_observation_status
source_observation_status_label
downloaded_at
source_modified_at
raw_archive_checksum
crosswalk_version
etl_version
```

For OECD GDP, `gdp_observation_status` and
`gdp_observation_status_label` should be retained because the 2023 values are
provisional.

## 11. Required Schema Migration

The existing Stage 2 statistics columns are legacy-compatible:

```text
GDP_millions(€)
GDP_per_capita(€)
poverty_rate(%)
average_annual_unemployment_rate(%)
average_net_hourly_wage(€)
municipal_population
population_density(inhabitants/sq_km)
area(sq_km)
```

The new ETL should migrate these toward explicit source and unit names:

| Legacy or implied field | New field or decision |
|---|---|
| `GDP_millions(€)` | `gdp_current_prices_million_eur`; retain OECD status/provisional metadata. |
| `GDP_per_capita(€)` | Not minimum canonical output; derive later only with an explicit consumer contract. |
| `poverty_rate(%)` | `poverty_rate_percent`. |
| `average_annual_unemployment_rate(%)` | Replace with `census_unemployment_rate_15_64_percent`; do not imply localised annual-average unemployment. |
| `average_net_hourly_wage(€)` | Replace with `average_net_monthly_wage_fte_eur`. |
| `population_density(inhabitants/sq_km)` | Replace with `population_density_per_sq_km`, derived from municipal population and geometry-derived area. |
| `area(sq_km)` | Replace with `area_sq_km`, derived from `EPSG:2154` geometry. |
| arrondissement-level demographic fields | Remove from intended ETL schema. |
| GDP columns lacking source, price basis, unit, geography mapping, or year | Treat as incompatible with the new contract. |

The migration should be staged because current Stage 2 products are already
byte-verified against legacy output schemas. A future Stage 2 schema change
should be explicit and separately validated against downstream consumers.

## 12. Validation Contract

The ETL should fail closed before producing a tranche when any of the following
checks fail:

- downloaded archive is missing, malformed, or lacks exactly one expected data
  file and metadata file for a source;
- required source columns are missing;
- source filters do not produce observations for the candidate year;
- post-filter metropolitan coverage is not exactly 96 departments;
- department codes are duplicated or unmatched against the accepted geometry;
- there is not exactly one observation per department per required metric;
- required values are null or non-numeric after conversion;
- source measure, unit, frequency, price basis, currency, or multiplier codes
  differ from this contract;
- salary, Filosofi, population, and unemployment statuses are not recorded;
- `employed + unemployed ~= active` fails for unemployment;
- `PMUN + PCAP == PTOT` fails for population;
- geometry CRS is missing or not transformable to `EPSG:2154`;
- geometry-derived areas are null, non-positive, or duplicate by department;
- OECD GDP has missing values, duplicate territory-year rows, or incomplete
  French TL3 coverage for the selected year;
- the OECD TL3 crosswalk has unmatched, duplicate, or non-metropolitan records
  in the required 96-department slice;
- the final assembled table is not deterministic when regenerated from the same
  inputs.

The tranche manifest should record row counts, selected year, source archive
paths or checksums, status counts, reconciliation maxima, unmatched records,
and any accepted exclusions such as overseas departments.

## 13. Proposed ETL Stages

Proposed script/module structure:

```text
src/data_pipeline/reference_data/
  insee/
    catalog.py              # source metadata discovery
    download.py             # source download and raw archive persistence
    extract.py              # ZIP/data/metadata extraction
    salary.py               # DS_BTS_SAL_EQTP_SEX_AGE transform
    filosofi.py             # DS_FILOSOFI_CC transform
    unemployment.py         # DS_RP_EMPLOI_LR_COMP transform
    population.py           # DS_POPULATIONS_REFERENCE transform
    validation.py           # source and tranche validation
  geography/
    departments.py          # accepted department geometry and area derivation
    oecd_tl3_crosswalk.py   # controlled static crosswalk validation
  oecd/
    gdp.py                  # OECD SDMX extraction and transform
  demographics/
    common_year.py          # common-year selection
    assemble.py             # final departmental table
    manifest.py             # metadata manifest
```

Pipeline stages:

1. Download source metadata and archives without overwriting accepted raw
   archives.
2. Persist raw archives and metadata with checksums.
3. Extract source data and source codebooks.
4. Apply source-specific filters and transformations.
5. Load and validate department geometry; derive `area_sq_km`.
6. Load and validate the OECD TL3 crosswalk.
7. Select the latest common year satisfying all required metrics.
8. Validate each transformed source slice.
9. Assemble the final 96-row departmental table.
10. Write the tranche output and metadata manifest atomically.
11. Reload written files and compare with the in-memory validated outputs.

No stage should silently drop rows to make validation pass. Overseas exclusions
must be explicit and reconciled against the 96-code metropolitan target.

## 14. Open Decisions

- Exact output location for accepted demographic tranches: for example
  `data/reference/demographics/france_departments_<year>.csv` versus a
  `data/raw/demographics/` replacement path.
- Whether `population_counted_separately`, `total_population`, and
  employment-status component counts belong in the main table or only in an
  audit table.
- Whether GDP per capita remains a derived Stage 2/application field or becomes
  part of the demographic tranche.
- How to version the OECD TL3 crosswalk and who reviews changes to it.
- Whether the ETL should preserve full source codebook rows in a manifest or
  only selected status/unit/measure metadata.
- How to transition existing Stage 2 products from legacy statistics column
  names without breaking the separate application.
- What acceptance process promotes a discovered 2023 tranche from provisional
  discovery output to an accepted repository artifact.

## 15. Relationship to `ROADMAP.md`

`ROADMAP.md` already identifies reference-data ETL as a future lifecycle, but
it does not yet include this completed demographic-source discovery or the
departmental-only schema direction.

Recommended concise future roadmap entry:

```markdown
### Departmental demographic reference ETL

Use `INSEE.md` as the source and schema contract for the France departmental
demographic tranche. The ETL is departmental only, targets the 96 metropolitan
departments, selects the latest fully validated common year across required
INSEE and OECD sources, derives area/density from accepted department geometry,
and replaces the legacy mixed-year `departmental_stats_2023.csv` snapshot only
after source archives, validation reports, a controlled OECD TL3 crosswalk, and
consumer-facing schema migration are reviewed.
```

The roadmap should link to this document rather than duplicating all source
codes, filters, and schema details.
