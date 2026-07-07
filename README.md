# Michelin Rated Restaurants Data Pipeline

This repository maintains the local data pipeline behind the Michelin-rated
restaurant analysis and downstream application datasets. It accepts annual
Michelin restaurant snapshots, combines the French partition with departmental
INSEE/OECD reference data and local French geographic boundaries, and publishes
validated CSV, GeoJSON, and guide-change report assets.

The project began as an analysis of Michelin restaurants and French
socioeconomic geography. The maintained focus is now the reproducible annual
data pipeline; historical notebooks and helper code are retained separately as
legacy material.

## What This Repository Produces

The maintained pipeline produces:

- country partitions for each accepted Michelin snapshot;
- France restaurant products enriched with department and region fields;
- departmental, regional, arrondissement, Paris, and Monaco GeoJSON products;
- a versioned INSEE/OECD departmental product consumed by Stage 2;
- annual France guide-change reports in CSV, JSON, and Markdown.

Canonical outputs are stored under `data/partitions/`, `data/products/`, and
`data/reports/`. The implementation is in `src/data_pipeline/` and
`src/insee_pipeline/`.

## Source Data And Provenance

| Source family | Source | Repository role |
|---|---|---|
| Michelin restaurant data | [ngshiheng/michelin-my-maps](https://github.com/ngshiheng/michelin-my-maps) | Upstream Michelin restaurant dataset used to create accepted annual snapshots. |
| INSEE data | [INSEE Melodi API catalog](https://portail-api.insee.fr/catalog/api/a890b735-159c-4c91-90b7-35159c7c9126?aq=ALL) | Machine-readable departmental demographic and socioeconomic source products. |
| INSEE exploration | [Statistiques locales](https://statistiques-locales.insee.fr/#c=home) | Graphical exploration site, not the machine-readable pipeline source. |
| OECD data | [OECD Data Explorer](https://data-explorer.oecd.org/) | Regional/TL3 GDP source used by the INSEE pipeline. |
| French geographic data | [gregoiredavid/france-geojson](https://github.com/gregoiredavid/france-geojson/tree/master) | Boundary GeoJSON files retained locally so normal transformations run offline. |

### Michelin Annual Snapshots

Stage 1 reads accepted local snapshots named:

```text
data/raw/michelin/michelin_data_<year>.csv
```

The current pipeline does not automate annual Michelin acquisition. The
intended upcoming maintenance workflow is:

1. Check the upstream Michelin repository when the France guide is normally
   updated, approximately April or May.
2. Compare the new upstream France data with the previously accepted year.
3. Review whether the schema or interpretation has fundamentally changed.
4. If compatible, store the accepted annual snapshot using the established
   filename convention, for example `michelin_data_<year>.csv`.
5. After the 2026 snapshot, the next expected annual snapshot would be 2027.

The upstream release timing is not guaranteed by this repository.

### INSEE And OECD Reference Data

`src/insee_pipeline/` builds departmental reference data from INSEE Melodi
products and OECD regional GDP. The candidate layer is deliberately
provenance-rich:

```text
data/candidates/insee/<year>/
```

For 2023 it contains `france_departments_2023.csv`,
`oecd_tl3_crosswalk_2023.csv`, `source_inventory_2023.json`,
`validation_report_2023.json`, and `manifest_2023.json`. These files retain
source identifiers, URLs, hashes, byte sizes, validation checks, and the
controlled TL3-to-department GDP crosswalk.

The consumer-facing product layer is narrower:

```text
data/products/insee/<year>/
```

For 2023 it contains `france_departments_2023.csv` and `manifest_2023.json`.
Stage 2 consumes this product layer, not the candidate table directly.

Downloaded INSEE ZIP files and OECD CSV files are disposable build/cache files
under `tmp/insee_pipeline/<year>/`. They are not the durable provenance layer.

### Local Geographic Inputs

The normal pipeline uses local GeoJSON files:

```text
data/raw/geodata/departments.geojson
data/raw/geodata/regions.geojson
data/raw/geodata/arrondissements-avec-outre-mer.geojson
data/raw/geodata/paris_arrondissements.geojson
data/raw/geodata/monaco.geojson
```

These are treated as retained source/reference inputs for the transformation
pipeline.

## Operating Sequence

The maintained flow is:

```text
External source data
  -> accepted annual Michelin snapshot
  -> Stage 1 country partitions
  -> INSEE/OECD candidate and departmental product
  -> Stage 2 France department and region enrichment
  -> Stage 2 Monaco products
  -> Stage 3 arrondissement and Paris products
  -> annual France guide-change reports
```

The INSEE product must exist before Stage 2 France departmental products are
built. By default, Stage 2 selects the latest numeric year under
`data/products/insee/<year>/`; pass `--insee-year` to require a specific INSEE
product year.

| Stage | Purpose | Main input | Main output | Normal command | Details |
|---|---|---|---|---|---|
| Stage 1 | Build France, Monaco, and UK partitions from one accepted Michelin snapshot. | `data/raw/michelin/michelin_data_<year>.csv` | `data/partitions/{france,monaco,uk}/..._<year>.csv` | `data_pipeline partition --year 2026` | [`docs/stage1.md`](docs/stage1.md) |
| INSEE candidate | Build validated, provenance-rich departmental statistics. | INSEE Melodi, OECD GDP, local department geometry | `data/candidates/insee/<year>/` | `insee_pipeline build --year 2023` | [`docs/insee_data.md`](docs/insee_data.md) |
| INSEE product | Convert a valid candidate into the Michelin-facing departmental product. | `data/candidates/insee/<year>/` | `data/products/insee/<year>/` | `insee_pipeline product --year 2023` | [`docs/insee_data.md`](docs/insee_data.md) |
| Stage 2 France | Enrich France restaurants and build department/region GeoJSON. | `data/partitions/france/france_<year>.csv`, INSEE product, local geography | `data/products/france/<year>/all_restaurants.csv` and geodata | `data_pipeline departments --year 2026` | [`docs/stage2-france-departments.md`](docs/stage2-france-departments.md) |
| Stage 2 Monaco | Build Monaco restaurant and aggregate products. | `data/partitions/monaco/monaco_<year>.csv`, local Monaco geometry | `data/products/france/<year>/monaco_restaurants.csv` and geodata | `data_pipeline monaco --year 2026` | [`docs/stage2-france-departments.md`](docs/stage2-france-departments.md) |
| Stage 3 | Add arrondissements and publish national/Paris arrondissement GeoJSON. | `data/products/france/<year>/all_restaurants.csv`, local references/geography | `all_restaurants(arrondissements).csv`, arrondissement and Paris GeoJSON | `data_pipeline arrondissements --year 2026` | [`docs/stage3-arrondissements.md`](docs/stage3-arrondissements.md) |
| Guide changes | Compare consecutive accepted France products. | arrondissement-enriched annual France products | `data/reports/france/changes_<previous>_<current>.*` | `data_pipeline changes --previous-year 2025 --current-year 2026` | [`docs/guide-changes.md`](docs/guide-changes.md) |

## Implemented CLI Commands

The implemented Michelin command group is:

```bash
data_pipeline partition --year 2026
data_pipeline departments --year 2026
data_pipeline monaco --year 2026
data_pipeline arrondissements --year 2026
data_pipeline acquire-paris-arrondissements
data_pipeline changes --previous-year 2025 --current-year 2026
```

The implemented INSEE command group is:

```bash
insee_pipeline build --year 2023
insee_pipeline product --year 2023
```

Common publication commands protect existing outputs. Where implemented,
`--validate-only` checks transformations without writing final files,
`--replace` deliberately replaces existing accepted outputs after validation,
and `--output-root` writes a candidate tree outside the canonical location.
Stage 2 France also supports `--insee-year` and `--insee-product-root`.

Supported year behavior is stage-specific:

- Stage 1 supports the known raw snapshot schemas from 2022 onward; tracked
  canonical partitions currently cover 2023-2026.
- Stage 2 France, Stage 2 Monaco, and Stage 3 are supported from 2025 under the
  current application-product contracts.
- The current validated INSEE candidate and product are for 2023.
- Guide-change reports require consecutive years and can read legacy 2023/2024
  arrondissement CSV paths as well as current `data/products/france/<year>/`
  products.

## Data Directory Lifecycle

```text
data/
  raw/                  accepted or locally retained source/reference inputs
    michelin/           accepted annual Michelin snapshots
    demographics/       department and Paris reference tables
    geodata/            retained French and Monaco GeoJSON boundaries
  candidates/           validated, provenance-rich intermediate outputs
    insee/<year>/       INSEE/OECD candidate, crosswalk, inventory, reports
  products/             derived assets for later pipeline or app consumption
    insee/<year>/       normalized departmental product for Stage 2
    france/<year>/      France and Monaco CSV/GeoJSON products
  partitions/           Stage 1 country partitions
  reports/              annual guide-change outputs
  wine/                 separate retained wine geospatial inputs
```

`tmp/` is used for disposable downloads, caches, and build working files. It is
not a provenance layer.

Important representative product paths include:

```text
data/products/france/<year>/all_restaurants.csv
data/products/france/<year>/all_restaurants(arrondissements).csv
data/products/france/<year>/monaco_restaurants.csv
data/products/france/<year>/geodata/department_restaurants.geojson
data/products/france/<year>/geodata/region_restaurants.geojson
data/products/france/<year>/geodata/arrondissement_restaurants.geojson
data/products/france/<year>/geodata/paris_restaurants.geojson
data/products/france/<year>/geodata/monaco_restaurants.geojson
data/products/insee/<year>/france_departments_<year>.csv
```

## Repository Map

```text
src/data_pipeline/      Michelin Stage 1, Stage 2, Stage 3, Monaco, and guide-change code
src/insee_pipeline/     INSEE/OECD source acquisition, candidate build, and product build
docs/                   durable stage and reference-data documentation
tests/                  unittest coverage for implemented pipeline behavior
data/                   raw inputs, candidates, partitions, products, reports, and retained reference data
notebooks/              parameterized posterity copies of legacy notebook workflows
legacy/                 archived notebook-era material, not maintained pipeline code
tmp/                    disposable local build/download workspace
```

The maintained Python packages use the `src/` layout and are packaged through
`pyproject.toml`.

## Documentation Index

- [`docs/stage1.md`](docs/stage1.md) - Stage 1 country partitions.
- [`docs/stage2-france-departments.md`](docs/stage2-france-departments.md) -
  France department/region products and Monaco products.
- [`docs/stage3-arrondissements.md`](docs/stage3-arrondissements.md) - Stage 3
  arrondissements and Paris products.
- [`docs/guide-changes.md`](docs/guide-changes.md) - annual France guide-change
  reports.
- [`docs/insee_data.md`](docs/insee_data.md) - INSEE/OECD candidate and product
  lifecycle.

## Prerequisites And Validation

Install the maintained pipeline and development/test extras:

```bash
python -m pip install -e ".[dev]"
```

The existing module invocation style remains supported:

```bash
python -m data_pipeline --help
python -m insee_pipeline --help
```

Editable installation also provides console scripts:

```bash
data_pipeline --help
insee_pipeline --help
```

The principal test command is:

```bash
python -m unittest discover -s tests -v
```

Pipeline commands validate schemas, row reconciliation, duplicate and join
cardinality checks, required values, geographic ranges/geometries, deterministic
serialization, and protected publication. Validation failures block publication;
records are not dropped to make outputs pass.
