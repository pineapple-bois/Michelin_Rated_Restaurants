# Michelin data pipeline roadmap

## Purpose and status language

This roadmap converts the repository from a copied, year-specific notebook workflow into a reproducible pure-Python data pipeline and historical data repository. It preserves the notebooks and their outputs as evidence while establishing explicit contracts, validation gates, and provenance for future years.

The labels below are deliberate:

- **Current fact** — observed in the repository.
- **Direction** — user-approved target or boundary.
- **Proposed** — an implementation shape to evaluate, not present tooling.
- **Open decision** — must be resolved with evidence before becoming a contract.

## Definitions

- **Raw Michelin snapshot:** immutable bytes obtained for one guide year, stored as `data/raw/michelin/michelin_data_<year>.csv`, with no cleaning or in-place correction.
- **Country partition:** a cleaned, schema-normalized annual subset for France, Monaco, or the UK. It is neither raw data, a cross-year canonical master, nor app-ready data.
- **Reference data:** independently versioned INSEE, demographic, administrative, and geographic source data used to enrich country partitions.
- **App-ready or published data:** validated annual CSV/GeoJSON products whose concrete file and schema contracts are confirmed against the application.
- **Fidelity baseline:** a trusted historical output used to test whether a replacement transformation reproduces legacy behavior. A baseline is read-only during comparison and is not automatically authoritative merely because it exists.

## Current repository facts

### Historical data

The tracked raw archive contains immutable Michelin snapshots for 2022–2026:

```text
data/raw/michelin/
  michelin_data_2022.csv
  michelin_data_2023.csv
  michelin_data_2024.csv
  michelin_data_2025.csv
  michelin_data_2026.csv
```

The canonical partition tree contains generated outputs for the supported historical years:

```text
data/partitions/france/france_{2023,2024,2025,2026}.csv
data/partitions/monaco/monaco_{2023,2024,2025,2026}.csv
data/partitions/uk/uk_{2023,2024,2025,2026}.csv
```

Each partition is byte-for-byte identical to its corresponding legacy output under `Years/<year>/data/`. The legacy files remain the read-only fidelity evidence. No 2022 country-partition baseline is present.

The sources evolved across years:

- 2022 has `MinPrice`, `MaxPrice`, and `Currency`; the legacy notebook maps `MaxPrice` to `price`.
- 2023 uses `Price` and award labels such as `3 MICHELIN Stars`.
- 2024 uses shortened award labels such as `3 Stars`.
- 2025 introduces `GreenStar` and `Selected Restaurants`.
- 2026 introduces three-part `Location` parsing (`city`, optional `state`, `country`) for US-style locations, but the final legacy partition drops `state`.
- The copied Monaco partitions report `country=France` in 2023–2025 and `country=Principality of Monaco` in 2026.

### Legacy transformation and outputs

`Years/2026/Notebooks/Data-Preparation.ipynb` is the latest Stage 1 reference. It selects source columns, lowercases names, maps `WebsiteUrl` to `url`, handles known location special cases, parses two- and three-part locations, derives numeric `stars`, partitions France/Monaco/UK, and writes CSVs. Earlier annual notebooks contain variations of the same logic. They are historical evidence and must not be edited in place.

Later France and Monaco notebooks currently produce files including:

```text
all_restaurants.csv
all_restaurants(arrondissements).csv
monaco_restaurants.csv
geodata/arrondissement_restaurants.geojson
geodata/department_restaurants.geojson
geodata/monaco_restaurants.geojson
geodata/paris_restaurants.geojson
geodata/region_restaurants.geojson
```

`ExtraData/Demographics/`, `ExtraData/Geodata/`, and `ExtraData/France_Departments_Demographics.ipynb` hold legacy reference sources and processing. `Functions/` contains analysis, visualisation, Plotly, and wine helpers; it is not yet a reusable Stage 1 package.

### Tooling

`requirements.txt` remains the only dependency configuration. Stage 1 now has a `src/data_pipeline/` package, standard-library `unittest` coverage, and a `python -m data_pipeline partition` CLI documented in the README and `docs/stage1.md`. There is still no package metadata or CI workflow; commands currently use `PYTHONPATH=src`.

The README and parts of `AGENTS.md` still describe the legacy `Years/<year>/data/Michelin/` locations and pre-partition naming. The tracked `data/raw/michelin/` archive and the approved `data/partitions/` terminology are the forward-looking contracts for this roadmap.

## Separation of concerns

Keep four lifecycles independent:

1. **Michelin acquisition:** retrieve, verify, and accept an immutable annual raw snapshot.
2. **Stage 1 partitioning:** transform one accepted raw snapshot into annual France, Monaco, and UK partitions.
3. **Reference-data ETL:** version and normalize INSEE/demographic/geographic sources independently of Michelin guide years.
4. **Stage 2 publication:** combine accepted partitions and compatible reference data into validated application products.

Acceptance or failure in one lifecycle must not silently mutate artifacts in another.

## Stage 1 contract

### Scope

```text
data/raw/michelin/michelin_data_<year>.csv
  -> required-column selection and schema normalization
  -> Location parsing
  -> award-value derivation
  -> France, Monaco, and UK partitioning
  -> validation
  -> data/partitions/<country>/<country>_<year>.csv
```

Stage 1 includes only:

- selecting required source columns and detecting missing/unexpected schema;
- normalizing output column names and mapping `WebsiteUrl` to `url`;
- parsing Michelin's `Location` into geographic components;
- explicit rules for known city-state/principality values and two-part versus US-style three-part locations;
- preserving source award labels while deriving the existing numeric `stars` analysis value;
- selecting France, Monaco, and United Kingdom records, with an explicit assertion that Monaco is absent from France;
- deterministic, idempotent annual CSV serialization.

Differences from a baseline must be reported, never silently normalized away.

### Fidelity milestone

The first implementation milestone is complete for 2023-2026: the pure-Python process reproduces all legacy country partitions byte-for-byte, supports disposable candidates and canonical publication, and protects existing outputs. The unresolved 2022 source remains fail-closed.

For each country/year, compare:

- expected path and filename;
- columns and column order;
- row count and row ordering;
- cell values and null counts;
- preserved award labels and derived `stars` mappings;
- country membership, including Monaco exclusion from France;
- longitude and latitude values;
- exact and candidate duplicate behavior;
- inferred dtypes after writing and reloading CSV;
- byte/content stability across deterministic reruns.

Write candidate outputs to a separate temporary or explicitly named comparison location. Validation must refuse to overwrite the baseline. Classify every earlier-year difference as one of:

1. source-schema evolution requiring a documented compatibility rule;
2. an intentional, reviewed departure from legacy behavior; or
3. a fidelity failure to fix before acceptance.

The missing 2022 baseline prevents the same direct three-country comparison currently available for 2023–2026. Resolve its provenance and expected outputs before declaring 2022 fidelity complete.

### Implemented Python shape

Stage 1 currently uses:

```text
src/data_pipeline/
  __init__.py
  __main__.py
  cli.py
  stage1/
    pipeline.py
    schema.py
    validation.py
    fidelity.py
tests/test_stage1.py
tests/test_stage1_fidelity.py
```

The implementation keeps transformation, schema compatibility, validation,
publication, and fidelity comparison separate without introducing a packaging
framework beyond the repository's current needs.

The operational interface is:

```text
PYTHONPATH=src python -m data_pipeline partition --year 2026
```

This interface is implemented. With no output override it publishes to `data/partitions/`; `--output-root`, `--validate-only`, and `--replace` provide candidate, no-write, and deliberate rebuild workflows.

## Non-goals for the first implementation

Stage 1 must not expand to include:

- INSEE, demographic, department, arrondissement, region, or other reference enrichment;
- spatial joins or generated GeoJSON;
- application-specific fields or Stage 2 publication;
- cross-year restaurant identity or canonical master data;
- guide-year change classification;
- network acquisition for 2027;
- rewriting, deleting, or parameterizing legacy notebooks;
- silently correcting historical baselines to fit a preferred modern schema.

## Phased plan

### Phase 0 — Repository and data inventory

- Classify tracked and untracked files as raw, partition, reference, intermediate, app-facing, notebook evidence, or analysis-only.
- Record raw and partition schemas, row counts, hashes, provenance, and producing notebook cells for every available year.
- Confirm the 12 uncommitted 2023–2026 partition baselines remain exact legacy copies.
- Determine whether a trustworthy 2022 France/Monaco/UK baseline can be recovered or must be reconstructed under a separately reviewed rule.
- Record current dependency/tooling gaps without changing the data.

**Exit:** a reviewed inventory identifies every Stage 1 input/baseline and its provenance.

### Phase 1 — Stage 1 contract

- Define accepted raw schemas and required-column variants by year.
- Define the stable partition schema and allowed year-specific schema differences.
- Document location parsing, city-state/principality handling, US state handling, award labels, and numeric `stars` rules.
- Define candidate-output paths, validation reports, overwrite protection, and fail-closed behavior.
- Decide whether `state` and `greenstar` belong in one stable schema or require compatibility/version rules.

**Exit:** fixtures and expected validation outcomes can be written without consulting notebook execution state.

### Phase 2 — Stage 1 Python implementation

**Status:** complete for the current Stage 1 contract.

- Add the smallest reusable Python modules needed for the agreed contract.
- Add a year-parameterized proposed CLI only after its interface is documented.
- Write candidates separately from baselines and accepted outputs.
- Add focused unit tests for schema variants, special locations, Monaco exclusion, awards, nulls, duplicates, and deterministic serialization.

**Exit:** Stage 1 can produce and validate candidates without executing or modifying notebooks.

### Phase 3 — Historical fidelity

**Status:** complete for 2023-2026; 2022 remains unresolved because its source lacks country information for most rows and has no trusted partition baseline.

- Reproduce all three 2026 partitions and issue field-level comparison reports.
- Extend the same code path to 2023–2025 baselines, then resolve 2022.
- Document compatibility rules for source schema, awards, `GreenStar`, location structure, and Monaco semantics.
- Repeat runs to prove determinism and idempotence.
- Promote generated partitions to authoritative status only through an explicit review/acceptance step; never overwrite evidence during validation.

**Exit:** every accepted historical year passes its declared contract, or has a reviewed and documented exception.

### Phase 4 — 2027 acquisition

The expected upstream candidate is `https://github.com/ngshiheng/michelin-my-maps/blob/main/data/michelin_my_maps.csv`. Do not implement retrieval until this phase.

- Retrieve the candidate CSV without writing over an accepted snapshot.
- Preserve downloaded bytes and record URL, retrieval timestamp, upstream revision/commit when available, checksum, schema, and row count.
- Validate source integrity before considering `michelin_data_2027.csv` accepted.
- Isolate a normalized French candidate subset and compare it with accepted 2026 French source data.
- Evaluate row count, available identity fields, award distribution, additions/removals, award changes, address/coordinate changes, and normalized record fingerprints.
- Fail closed and report evidence when French content appears materially unchanged; a successful HTTP response or whole-file hash change is insufficient because other countries may update first.
- On rejection, preserve diagnostics but do not replace/publish an accepted raw snapshot or run publication.
- Design any explicit reviewed override later; it must never be implicit.
- After acceptance, archive the immutable 2027 snapshot and run Stage 1.

**Open:** define quantitative and qualitative freshness thresholds robust to legitimate years with few French changes and source formatting churn.

### Phase 5 — Reference-data ETL

- Inventory every INSEE/demographic/geographic source, its provenance, current consumers, schema, code system, CRS, reference year/release, and refresh behavior.
- Establish immutable source snapshots and separate normalized reference outputs.
- Version even infrequently changing base geography; do not assume it is timeless.
- Validate schema, geographic codes, CRS/geometries, uniqueness, join cardinality, and compatibility with the guide/publication year.

**Exit:** Stage 2 can request explicit compatible reference versions rather than reading ambiguous shared files.

### Phase 6 — Stage 2 France application contract

- Inventory the notebooks that produce each current France/Monaco CSV and GeoJSON.
- Inspect the separate application's actual paths, schemas, null/category expectations, CRS, and geographic granularity.
- Classify each product as intermediate, app-facing, or analysis-only.
- Define historical storage, schema/version rules, validation gates, and publication/rollback behavior.
- Reproduce at least one trusted historical year from accepted partitions plus normalized reference data.

**Exit:** application compatibility is evidenced by a concrete consumer contract, not inferred from filenames.

### Phase 7 — Replace yearly notebook copying

- Parameterize accepted transformations by guide year and explicit reference versions.
- Preserve legacy notebooks unchanged as evidence until replacement outputs are validated.
- Generate annual CSV/GeoJSON products deterministically with validation reports.
- Add regression and integration tests from raw snapshot through published candidates.
- Retire notebook copying only after historical and application fidelity are demonstrated.

## Validation and publication gates

Each stage should emit a machine-readable report plus a concise human summary. Critical schema, null, duplicate, membership, join-cardinality, coordinate/geometry, row-reconciliation, provenance, freshness, or fidelity failures must stop acceptance/publication. A report may explain an exception, but code must not convert an unexplained discrepancy into success by dropping rows, changing order, coercing categories, or overwriting the expected output.

Tests should use small synthetic fixtures for parsing and failure behavior; full historical snapshots/baselines should be read-only integration inputs. Add the test tool and its documented command only in the implementation change that introduces it—do not invent repository commands in advance.

## Open decisions

1. What is the trusted source and expected schema for missing 2022 France, Monaco, and UK baselines?
2. Should accepted partitions use one stable superset schema across all years, or preserve documented year-specific columns such as `greenstar`?
3. Should parsed `state` be retained in the partition contract, despite the 2026 notebook dropping it?
4. Should historical Monaco country values reproduce legacy `France` exactly first, and if/when should canonical `Principality of Monaco` semantics be introduced as a versioned departure?
5. Which serialization details define fidelity: semantic dataframe equality, exact CSV bytes, or both at different acceptance levels?
6. Where should candidate outputs, validation reports, and eventually accepted generated partitions live, and which belong in Git?
7. Which packaging/build and test configuration best fits the existing lightweight `requirements.txt` setup?
8. What upstream revision identifier is reliably available during acquisition, and how are candidate snapshots retained after rejection?
9. What French-subset freshness algorithm and thresholds distinguish stale upstream data from a legitimately low-change guide year?
10. What exact files and schemas does the separate Dash application currently consume, and how should published product versions be selected?
11. How should guide years select compatible INSEE releases, geographic-code vintages, and boundary versions?

## Decision log

- Use `data/partitions/`, not `master/`, for annual country data.
- Name partitions `france_<year>.csv`, `monaco_<year>.csv`, and `uk_<year>.csv` under country directories.
- Preserve raw yearly Michelin files as immutable snapshots.
- Implement replacement transformations as reusable pure Python, not copied annual notebooks.
- Do not edit legacy notebooks in place; retain them as historical evidence until replacements are validated.
- Use the legacy yearly CSV outputs under `Years/` as read-only historical reproduction baselines; generated canonical partitions must match them for 2023-2026.
- Keep Michelin acquisition, Stage 1 partitioning, reference-data ETL, and Stage 2 publication as separate concerns.
- Start fidelity implementation with 2026, then apply the same pipeline to 2022–2025 with explicit compatibility classification.
- Fail closed on critical validation and on apparently stale 2027 French source content; never publish or replace accepted data implicitly.
