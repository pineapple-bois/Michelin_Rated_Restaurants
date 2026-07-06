# Stage 1: Michelin country partitions

Stage 1 converts one accepted local Michelin snapshot into annual France,
Monaco, and United Kingdom partitions. It does not acquire data or perform
reference-data enrichment.

## Contract

Input:

```text
data/raw/michelin/michelin_data_<year>.csv
```

Canonical outputs:

```text
data/partitions/france/france_<year>.csv
data/partitions/monaco/monaco_<year>.csv
data/partitions/uk/uk_<year>.csv
```

An input file is an accepted raw snapshot: its bytes and filename are fixed
before Stage 1 begins. `validate_stage1()` in
`src/data_pipeline/stage1/pipeline.py` is the boundary a future Extract stage
will call after it has retrieved a candidate, verified provenance and French
freshness, and accepted the snapshot into `data/raw/michelin/`. Stage 1 never
downloads or edits raw data.

## Operational commands

Run commands from the repository root with the project environment active.

Build a year into the canonical partition tree:

```bash
PYTHONPATH=src python -m data_pipeline partition --year 2026
```

Build a review candidate elsewhere:

```bash
PYTHONPATH=src python -m data_pipeline partition \
  --year 2026 \
  --output-root /tmp/michelin-stage1-2026
```

Validate source, transformations, and all three in-memory outputs without
publishing:

```bash
PYTHONPATH=src python -m data_pipeline partition --year 2026 --validate-only
```

Existing targets are protected. Rebuild them only with an explicit option:

```bash
PYTHONPATH=src python -m data_pipeline partition --year 2026 --replace
```

`--compare-root <path>` may be added to a published candidate build when that
root contains the same `france/`, `monaco/`, and `uk/` filename layout.

## Notebook-to-Python transformation map

The evidence implementations are the annual
`Years/<year>/Notebooks/Data-Preparation.ipynb` notebooks (with the older 2022
notebook at `Years/2022/Data-Preparation-2022.ipynb`). Stage 1 extracts only
their material partition-building behavior.

| Transformation | Python owner | Behavior |
|---|---|---|
| Select source schema | `schema.spec_for_year()` and `validation.validate_source_columns()` | Require the known columns for the requested year before transforming. |
| Normalize names | `pipeline.clean_snapshot()` | Lowercase selected column names; rename `WebsiteUrl` to `url`; map 2022 `MaxPrice` to `price`. |
| Parse `Location` | `pipeline.parse_locations()` | Normalize known single-place cases, split from the right into city/optional state/country, trim whitespace, then fail if city or country is unresolved. |
| Derive stars | `schema.py` award maps and `pipeline.clean_snapshot()` | Preserve `award` verbatim and add its historical numeric analysis value as `stars`. |
| Partition countries | `pipeline.prepare_partitions()` | Select France, Monaco, and United Kingdom without sorting or dropping rows. |
| Validate outputs | `validation.validate_cleaned()` and `validation.validate_partitions()` | Enforce schemas, membership, row preservation, awards, duplicates, coordinates, and partition disjointness. |
| Serialize and publish | `pipeline._write_staged_partitions()` and `pipeline.publish_partitions()` | Stage and reload all files before replacing any final path; protect existing data and roll back a failed publication. |
| Historical comparison | `fidelity.compare_partition_files()` | Compare reloaded dataframe values/dtypes/order and exact CSV bytes. |

### Location parsing

The legacy notebooks normalize these location values before splitting:

| Source value | Parsed location |
|---|---|
| `Hong Kong` | `Hong Kong, Hong Kong SAR China` |
| `Macau` | `Macau, Macau SAR China` |
| `Singapore` | `Singapore, Singapore` |
| `Dubai` | `Dubai, United Arab Emirates` |
| `Luxembourg` | `Luxembourg, Luxembourg` |
| `Abu Dhabi` | `Abu Dhabi, United Arab Emirates` |

The 2023-2025 sources use city/country values. The 2026 source also contains
US-style `city, state, country` values. `parse_locations()` supports both by
splitting from the right into at most three components. `state` is parsed so
country selection is correct but is intentionally omitted from the historical
partition schema, matching the 2026 notebook output.

Any non-special location without enough information to produce both city and
country is a validation failure. Stage 1 does not infer countries from names,
addresses, or coordinates.

### Award values

Award text remains unchanged. `stars` reproduces the notebook's analysis
values:

| Source years | Award | `stars` |
|---|---|---:|
| 2022-2023 | `3 MICHELIN Stars` / `2 MICHELIN Stars` / `1 MICHELIN Star` | 3 / 2 / 1 |
| 2024 onward | `3 Stars` / `2 Stars` / `1 Star` | 3 / 2 / 1 |
| All supported schemas | `Bib Gourmand` | 0.5 |
| 2025 onward | `Selected Restaurants` | 0.25 |

Unknown or null awards fail validation rather than receiving a guessed value.

### Country selection

- UK is `country == "United Kingdom"`.
- France is `country == "France"`, excluding `city == "Monaco"`.
- In 2023-2025, Monaco is represented by `country == "France"` and
  `city == "Monaco"`, matching those historical sources.
- From 2026, Monaco is `country == "Principality of Monaco"`. A modern source
  that still classifies Monaco as France fails validation.

The three selected index sets must be disjoint. France is explicitly checked
for Monaco rows.

### Output schemas

For 2023-2024:

```text
name,address,city,country,price,cuisine,url,award,stars,longitude,latitude
```

For 2025 onward:

```text
name,address,city,country,price,cuisine,url,award,greenstar,stars,longitude,latitude
```

Input row order is retained. Pandas CSV serialization uses UTF-8, no index, and
LF line endings.

## Validation and publication

Before publication Stage 1 checks:

- required source columns and complete city/country parsing;
- preservation of every source row through cleaning;
- expected output columns and ordering;
- known non-null awards and numeric star derivation;
- exact country membership and Monaco exclusion from France;
- no exact duplicate partition rows or overlap between partitions;
- numeric, paired, in-range coordinates;
- the exact historical 2024 exception of 52 paired missing coordinates in
  France and 12 in the UK (reported as warnings).

All three frames are validated in memory, written under a private staging
directory inside the output root, reloaded, and compared with the in-memory
frames. Only then are the final files atomically replaced one at a time. If a
publication operation raises an exception, new files are removed and prior
files are restored from private backups. The command returns non-zero on
validation, collision, comparison, or publication failure.

Without `--replace`, the presence of any target for the requested year blocks
publication. With `--replace`, validation and staging still complete before
any existing file changes.

## Historical differences and fidelity

- 2023 uses the older `MICHELIN Star(s)` labels.
- 2024 uses shortened award labels and contains the declared missing-coordinate
  exception above.
- 2025 adds `GreenStar` and `Selected Restaurants`.
- 2026 adds three-part US locations and changes Monaco's source country value.

The integration test generates 2023-2026 from `data/raw/michelin/` and compares
all 12 outputs with the corresponding legacy CSVs under `Years/`. They are
currently byte-identical. This is the evidence for exact historical fidelity;
notebook display output is not part of that claim.

## Intentionally excluded notebook work

The Python Stage 1 omits notebook display calls, dataframe summaries, unique
value printing, pivot tables, rankings, and other exploratory cells. It also
excludes INSEE/demographic enrichment, administrative/geographic joins,
GeoJSON generation, application fields, cross-year identity matching, change
classification, and source acquisition. Those belong to later pipeline stages.
