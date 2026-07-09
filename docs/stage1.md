# Stage 1: Michelin source acquisition and country partitions

Stage 1 acquires the upstream Michelin restaurant source and converts one
accepted annual snapshot into France, Monaco, and United Kingdom partitions.
This replaces the former manual download and acceptance step. It still does
not perform reference-data enrichment; INSEE/demographic joins, GeoJSON
generation, and application products remain later-stage responsibilities.

## Contract

Upstream source:

```text
https://github.com/ngshiheng/michelin-my-maps
https://raw.githubusercontent.com/ngshiheng/michelin-my-maps/main/data/michelin_my_maps.csv
```

Accepted raw snapshot:

```text
data/raw/michelin/michelin_data_<year>.csv
```

Canonical outputs:

```text
data/partitions/france/france_<year>.csv
data/partitions/monaco/monaco_<year>.csv
data/partitions/uk/uk_<year>.csv
```

The current annual acquisition path derives the latest accepted France year
from `data/partitions/france/france_<year>.csv`. If the latest accepted France
partition is year `Y`, Stage 1 evaluates candidate year `Y + 1`.

Normal acquisition is blocked until 1 April of the candidate year. Before that
date it makes no network request, creates no temporary workspace, and writes no
files. The date is only an eligibility gate: reaching 1 April does not prove
that Michelin has published a new French guide or that the upstream repository
has updated.

Before downloading, Stage 1 also checks that no candidate-year accepted assets
already exist. This includes the raw snapshot and all expected country
partitions. Normal acquisition never overwrites accepted annual data.

## Operational commands

Run commands from the repository root with the project environment active.

Normal annual acquisition:

```bash
data_pipeline partition --acquire-next
```

The command:

1. derives the candidate year from the latest accepted France partition;
2. applies the 1 April date gate;
3. refuses to continue if any candidate-year raw or partition output exists;
4. creates a temporary run workspace;
5. downloads the upstream CSV into that workspace;
6. records useful source information, including the URL and upstream commit
   when GitHub reports one;
7. validates the upstream schema;
8. applies the same Stage 1 cleaning and partition logic used by local builds;
9. validates France, Monaco, and UK candidate partitions;
10. compares the candidate France partition with the latest accepted France
    partition;
11. accepts or rejects the candidate through the central France policy;
12. publishes the raw snapshot and all three partitions only if France passes.

For deterministic tests or dry calendar checks, override the local date:

```bash
data_pipeline partition --acquire-next --today 2027-03-31
```

Build an already accepted local raw snapshot into the canonical partition tree:

```bash
data_pipeline partition --year 2026
```

Build a review candidate elsewhere from an existing local raw snapshot:

```bash
data_pipeline partition \
  --year 2026 \
  --output-root /tmp/michelin-stage1-2026
```

Validate source, transformations, and all three in-memory outputs without
publishing:

```bash
data_pipeline partition --year 2026 --validate-only
```

Existing partition targets are protected. Rebuild them only with an explicit
manual replacement option:

```bash
data_pipeline partition --year 2026 --replace
```

`--replace` is intentionally not part of `--acquire-next`; accepted annual
snapshots are not silently replaced by normal acquisition.

`--compare-root <path>` may be added to a published local candidate build when
that root contains the same `france/`, `monaco/`, and `uk/` filename layout.

## Acquisition acceptance

All downloaded candidate work happens in a temporary workspace until the France
acceptance gate passes. Candidate partitions are built before comparison, and
the acceptance unit is the cleaned France partition, not the global raw CSV and
not any Stage 2 enriched product.

France is the sole publication driver:

- if the candidate France partition is accepted, Stage 1 publishes the raw
  snapshot and all France, Monaco, and UK partitions from the same downloaded
  source;
- if the candidate France partition is rejected, Stage 1 publishes nothing,
  deletes candidate assets, reports the France comparison, and leaves
  canonical data unchanged;
- Monaco and UK changes do not cause publication when France fails the gate.

The deterministic France comparison reports:

- schema fields added to or removed from the candidate;
- shared comparison columns and missing expected fields;
- matched restaurants and match rate;
- unmatched accepted and candidate restaurants;
- ambiguous match candidates and duplicate-key conflicts;
- previous France row count;
- candidate France row count;
- unchanged restaurants;
- added restaurants;
- removed restaurants;
- Michelin award-label changes;
- other material row changes.

Restaurant identity uses the same shared deterministic reconciliation helper as
`data_pipeline changes`, not a separate Stage 1 key. The hierarchy never
includes `stars` or `award` in identity evidence and uses normalized URL,
postcode, name, address, exact coordinates, and controlled nearby-name matching.
Exact full-row equality is not used as identity. URL and coordinate-only
changes are treated as non-material metadata changes and do not count as
Michelin award-label movement.

Stage 1 classifies matched rows with explicit comparison fields only:

- award fields: `award`, `stars`;
- non-award descriptive fields: `name`, `address`, `city`, `price`, `cuisine`.

`Award-label changes` means matched restaurants whose source Michelin `award`
or derived `stars` fields differ between the accepted and candidate partitions.
It is an automated Stage 1 acceptance signal, not the authoritative annual
guide-change classification. The downstream `data_pipeline changes` report
remains responsible for curated concepts such as promotions, demotions, newly
starred restaurants, and Green Star gains or losses.

Schema drift, URL churn, coordinate movement, and bulk non-comparison-field
changes are reported separately from Michelin guide movement.

Before the acceptance policy runs, a comparison-quality guard checks that the
reconciliation is credible. It blocks publication when expected comparison
fields are missing, the match rate is too low, ambiguous candidates are too
common, or non-award descriptive churn is unusually high. In that case Stage 1
prints that the candidate could not be compared reliably and publishes nothing;
this is distinct from a normal business rejection by the France acceptance
policy.

The first acceptance policy is deliberately conservative and is centralized in
`evaluate_france_acceptance()` in
`src/data_pipeline/stage1/acquisition.py`. A candidate is accepted only when it
shows broad evidence of a new French guide:

- at least 50 material France changes across additions, removals, and award
  label changes;
- at least 10 Michelin award-label changes;
- award-label movement across at least two Michelin award categories;
- not a batch consisting only of new Selected Restaurants.

These thresholds are intentionally easy to inspect and adjust. They are not a
confidence score, and they should be recalibrated when more annual transitions
are reviewed. A batch consisting mainly of roughly 20-30 newly added Selected
Restaurants without broader award-label movement is rejected.

## Notebook-to-Python transformation map

The evidence implementations are the annual
`Years/<year>/Notebooks/Data-Preparation.ipynb` notebooks (with the older 2022
notebook at `Years/2022/Data-Preparation-2022.ipynb`). Stage 1 extracts only
their material partition-building behavior.

| Transformation | Python owner | Behavior |
|---|---|---|
| Acquire source | `acquisition.download_upstream_snapshot()` | Download the maintained upstream CSV after the annual date and candidate-asset gates pass. |
| Select source schema | `schema.spec_for_year()` and `validation.validate_source_columns()` | Require the known columns for the requested year before transforming. |
| Normalize names | `pipeline.clean_snapshot()` | Lowercase selected column names; rename `WebsiteUrl` to `url`; map 2022 `MaxPrice` to `price`. |
| Parse `Location` | `pipeline.parse_locations()` | Normalize known single-place cases, split from the right into city/optional state/country, trim whitespace, then fail if city or country is unresolved. |
| Derive stars | `schema.py` award maps and `pipeline.clean_snapshot()` | Preserve `award` verbatim and add its historical numeric analysis value as `stars`. |
| Partition countries | `pipeline.prepare_partitions()` | Select France, Monaco, and United Kingdom without sorting or dropping rows. |
| Validate outputs | `validation.validate_cleaned()` and `validation.validate_partitions()` | Enforce schemas, membership, row preservation, awards, duplicates, coordinates, and partition disjointness. |
| Reconcile restaurants | `changes.matching.reconcile_restaurants()` | Match accepted and candidate restaurants using the same deterministic hierarchy as the guide-change report. |
| Compare France | `acquisition.compare_france_partitions()` | Report schema drift, reconciliation quality, and explicit guide-change metrics for the cleaned France partitions. |
| Accept France | `acquisition.evaluate_france_acceptance()` | Apply the conservative substantial-change policy that decides whether publication is allowed. |
| Serialize and publish | `pipeline._write_staged_partitions()` and `acquisition.publish_raw_and_partitions()` | Stage and reload all files before publishing; protect existing data and roll back accepted-run failures. |
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

For local `--year` builds, all three frames are validated in memory, written
under a private staging directory inside the output root, reloaded, and
compared with the in-memory frames. Only then are the final partition files
atomically replaced one at a time. If a publication operation raises an
exception, new files are removed and prior files are restored from private
backups.

For `--acquire-next`, the downloaded source and candidate partitions remain in
the temporary workspace until France acceptance passes. Accepted publication
stages and reloads every partition before publishing the raw snapshot and all
partition files as one coherent Stage 1 run. Existing accepted files are never
overwritten silently. If accepted-run publication fails, newly written
candidate-year files are removed and existing canonical data is left unchanged.

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
GeoJSON generation, application fields, cross-year identity matching beyond
the narrow France acquisition gate, and change-report publication. Those
belong to later pipeline stages or the dedicated changes command.
