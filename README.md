# Michelin Rated Restaurants Data Pipeline

This repository maintains the local data pipeline behind the Michelin-rated
restaurant analysis and downstream application datasets. It accepts annual
Michelin restaurant snapshots, combines the French partition with departmental
INSEE/OECD reference data and local French geographic boundaries, and publishes
validated CSV, GeoJSON, and guide-change report assets.

The project began as an analysis of Michelin restaurants and French
socioeconomic geography. The data produced serves a live dash application; 

- [Michelin Guide to France](https://restaurant-guide-france.net) 
- [Github Repo](https://github.com/pineapple-bois/Michelin_App_Development)

## What This Repository Produces

The maintained pipeline produces:

- country partitions for each accepted Michelin snapshot;
- France restaurant products enriched with department and region fields;
- departmental, regional, arrondissement, Paris, and Monaco GeoJSON products;
- a versioned INSEE/OECD departmental product consumed by Stage 2;
- reproducible French wine AOC geometry products, generated on demand from
  current source data;
- annual France guide-change reports in CSV, JSON, and Markdown.

The wine pipeline can produce a new dated product at any time. In practice,
wine appellation geography and classifications are comparatively stable, so a
new release is normally warranted by a material upstream change rather than an
annual schedule.

Canonical outputs are stored under `data/partitions/`, `data/products/`, and
`data/reports/`. The implementation is in `src/data_pipeline/` and
`src/insee_pipeline/`, with wine processing in `src/wine_pipeline/`.

## Source Data And Provenance

| Source family            | Source                                                                                                                     | Repository role                                                                |
|--------------------------|----------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| Michelin restaurant data | [ngshiheng/michelin-my-maps](https://github.com/ngshiheng/michelin-my-maps)                                                | Upstream Michelin restaurant dataset used to create accepted annual snapshots. |
| INSEE data               | [INSEE Melodi API catalog](https://portail-api.insee.fr/catalog/api/a890b735-159c-4c91-90b7-35159c7c9126?aq=ALL)           | Machine-readable departmental demographic and socioeconomic source products.   |
| INSEE exploration        | [Statistiques locales](https://statistiques-locales.insee.fr/#c=home)                                                      | Graphical exploration site, not the machine-readable pipeline source.          |
| OECD data                | [OECD Data Explorer](https://data-explorer.oecd.org/)                                                                      | Regional/TL3 GDP source used by the INSEE pipeline.                            |
| French geographic data   | [gregoiredavid/france-geojson](https://github.com/gregoiredavid/france-geojson/tree/master)                                | Boundary GeoJSON files retained locally so normal transformations run offline. |
| French wine AOC data     | [datagouv: AOC Viticoles de l'INAO](https://www.data.gouv.fr/datasets/delimitation-parcellaire-des-aoc-viticoles-de-linao) | Raw shape files representing French wine appellations.                         |

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

### Local Annual Orchestration

The local first-iteration annual orchestrator is:

```bash
scripts/run_annual_pipeline.sh
```

It is a thin shell wrapper around the existing Python CLI commands. It does not
move transformation logic into shell, send email, read `.env` files, create
commits, push branches, run GitHub Actions, or include the wine pipeline.

The script runs this stage order:

1. determine the latest accepted France partition year from
   `data/partitions/france/france_<year>.csv`;
2. run `data_pipeline partition --acquire-next`;
3. determine the latest accepted France partition year again;
4. stop successfully if Stage 1 did not publish a new France partition;
5. continue only when Stage 1 published exactly the next Michelin year;
6. attempt the next INSEE year with `insee_pipeline build --year <year>` and
   `insee_pipeline product --year <year>`;
7. fall back explicitly to the latest already accepted INSEE product if that
   next-year INSEE attempt fails;
8. run Stage 2 France with
   `data_pipeline departments --year <guide-year> --insee-year <insee-year>`;
9. run Stage 2 Monaco with `data_pipeline monaco --year <guide-year>`;
10. run Stage 3 with `data_pipeline arrondissements --year <guide-year>`;
11. generate guide changes with consecutive previous/current guide years.

The INSEE product year is derived from accepted files under
`data/products/insee/<year>/`. The next numeric year is always attempted first.
Only that INSEE attempt is non-fatal; once an INSEE product year has been
selected, failures in Stage 2 France, Stage 2 Monaco, Stage 3, or guide changes
stop the script.

Run locally from the repository root:

```bash
scripts/run_annual_pipeline.sh
```

To use another Python executable:

```bash
PYTHON=python scripts/run_annual_pipeline.sh
```

Logs are written to `tmp/logs/annual_pipeline_YYYYMMDD_HHMMSS.log` while also
remaining visible in the console.

### GitHub Actions Annual Run

The first GitHub Actions integration for the annual orchestrator is:

```text
.github/workflows/annual-pipeline.yml
```

It can be started manually from the GitHub Actions tab with
`workflow_dispatch`. It also runs at approximately 08:17 UTC every Thursday
during April, May, and June:

```text
17 8 * 4-6 4
```

The schedule is only an opportunity to run the local orchestrator. Stage 1
still decides whether acquisition is allowed and whether a new France guide has
actually been accepted. If no new guide is published, downstream stages are
skipped and the workflow succeeds. After a successful annual run, the workflow
can be disabled manually; if left enabled, later runs are safe because Stage 1
derives the next candidate year and blocks acquisition until the following
1 April.

The workflow installs the project with:

```bash
python -m pip install -e .
```

Then it runs:

```bash
PYTHON=python scripts/run_annual_pipeline.sh
```

If the script leaves no repository changes, the workflow stops after uploading
logs and does not create a branch, commit, or pull request. If accepted annual
data was generated, the workflow validates that every changed path is under the
maintained generated-data roots:

```text
data/raw/michelin/
data/partitions/
data/candidates/insee/
data/products/insee/
data/products/france/
data/reports/
```

Unexpected paths fail the workflow before any commit or pull request is
created. `tmp/logs/` is never staged.

For a valid generated-data run, the workflow commits the allowlisted outputs to
a deterministic branch named:

```text
automation/annual-pipeline-<year>
```

and opens or updates one pull request against the default branch. It does not
push directly to the default branch, auto-approve, or auto-merge. Manual review
and merge are required. If the automation branch already exists, the workflow
updates it only after checking the existing branch for bot-authored,
allowlisted changes; unexpected divergence fails for manual intervention.

Logs from `tmp/logs/` are uploaded as a 30-day artifact even if the job fails.
When the run creates or updates annual data under the maintained data roots,
those generated outputs are uploaded as a separate 30-day artifact.
The workflow only creates pull requests; it does not approve them. 

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
data/                   raw inputs, candidates, partitions, products, reports, and retained reference data
docs/                   durable stage and reference-data documentation
tests/                  domain-grouped coverage for implemented pipeline behavior
  automation/           annual shell-script and GitHub Actions workflow tests
  michelin/             Stage 1, Stage 2, Stage 3, Monaco, and guide-change tests
  insee/                INSEE/OECD candidate and product tests
  wine/                 Wine AOC pipeline tests
  fixtures/             shared regression fixtures
src/data_pipeline/      Michelin Stage 1, Stage 2, Stage 3, Monaco, and guide-change code
src/insee_pipeline/     INSEE/OECD source acquisition, candidate build, and product build
tmp/                    disposable local build/download workspace
```

The maintained Python packages use the `src/` layout and are packaged through
`pyproject.toml`.

## Wine AOC Pipeline

The wine pipeline builds an enriched AOC source package, simplifies regional
geometry, and assembles a durable candidate without publishing application
assets or final products. Normal operation is:

```bash
python -m wine_pipeline build
python -m wine_pipeline simplify
python -m wine_pipeline assemble-candidate
python -m wine_pipeline publish-product
```

See [`docs/wine_data.md`](docs/wine_data.md) for stage contracts, automatic
input resolution, storage lifecycle, review policy, and advanced recovery
options.

## Documentation Index

- [`docs/wine_data.md`](docs/wine_data.md) - Wine build,
  simplification, durable candidate assembly, and product publication.
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

Install the maintained pipeline:

```bash
python -m pip install -e .
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

Pytest is also supported as an optional runner for the same tests:

```bash
python -m pip install -e ".[test]"
python -m pytest
```

Pipeline commands validate schemas, row reconciliation, duplicate and join
cardinality checks, required values, geographic ranges/geometries, deterministic
serialization, and protected publication. Validation failures block publication;
records are not dropped to make outputs pass.
