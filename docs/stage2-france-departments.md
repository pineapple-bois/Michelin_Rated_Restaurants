# Stage 2: France departmental and regional products

This stage replaces the production-relevant departmental work in
`Years/<year>/Notebooks/France/France_Processing.ipynb`. It enriches the annual
France partition and creates the departmental and regional GeoJSON consumed by downstream
application and visualisation work.

Stage 2 currently supports 2025 onward. Restaurant CSV products continue to
preserve the Stage 2 restaurant contract, while departmental and regional
GeoJSON statistics now come from the versioned INSEE/OECD product contract.

## Inputs and outputs

Accepted inputs:

```text
data/partitions/france/france_<year>.csv
data/raw/demographics/departments.csv
data/products/insee/<insee-year>/france_departments_<insee-year>.csv
data/products/insee/<insee-year>/manifest_<insee-year>.json
data/raw/geodata/departments.geojson
data/raw/geodata/regions.geojson
```

Canonical products:

```text
data/products/france/<year>/all_restaurants.csv
data/products/france/<year>/geodata/department_restaurants.geojson
data/products/france/<year>/geodata/region_restaurants.geojson
```

The INSEE product is selected from `data/products/insee/`. By default, Stage 2
inspects immediate child directories whose names are numeric years and selects
the highest numeric year. The highest numeric year must contain both the CSV and
matching manifest; malformed or incomplete highest-year products fail
validation rather than silently falling back to an older year. Non-numeric
directories are ignored.

Use `--insee-year` to request an explicit product year. Explicit years fail
immediately if the corresponding product is missing or invalid. Use
`--insee-product-root` to point at a candidate product root.

## Commands

Build canonical products:

```bash
data_pipeline departments --year 2026
```

Build with an explicit INSEE product year:

```bash
data_pipeline departments \
  --year 2026 \
  --insee-year 2023
```

Build a disposable candidate:

```bash
data_pipeline departments \
  --year 2026 \
  --output-root /tmp/michelin-stage2-2026
```

Validate without publishing, or explicitly rebuild accepted products:

```bash
data_pipeline departments --year 2026 --validate-only
data_pipeline departments --year 2026 --replace
```

Existing targets are never silently replaced.

## Transformation sequence

| Notebook operation | Python replacement |
|---|---|
| Read `france_master_<year>.csv` | Stage 2 reads the Stage 1 contract at `data/partitions/france/france_<year>.csv` in `_load_inputs()`. |
| Rename `city` to `location` | `enrich_restaurants()` constructs the downstream `location` after validated address parsing. |
| Extract two postal-code digits | `_parse_address()` requires one five-digit postal code; `_department_code()` derives the department code. |
| Join department/capital/region | `enrich_restaurants()` performs a validated many-to-one join with `departments.csv`. |
| Rewrite full address | `_parse_address()` separates main address, city, postal code, and France; output location is `city, postal_code`. |
| Strip non-breaking spaces | `_strip_nbsp()` is applied to all Stage 1 partition values before parsing/joining. |
| Repair Corsica after an unmatched `20` join | `_department_code()` maps `200xx`/`201xx` to `2A` and `202xx` to `2B` before the reference join. |
| Create Michelin dummy columns and department totals | `aggregate_departments()` creates category counts, `total_stars`, `starred_restaurants`, and `green_stars`. |
| Select INSEE product | `resolve_insee_product()` selects the latest numeric INSEE product year, unless `--insee-year` is explicit. |
| Validate INSEE product | `load_insee_product()` checks the CSV, manifest, schema, row count, hash, year, unique department codes, and required values. |
| Merge accepted demographics | `aggregate_departments()` performs a one-to-one left join from all accepted departmental INSEE product rows to restaurant counts. |
| Group coordinates by category | `aggregate_departments()` preserves restaurant order and builds the historical `locations` dictionary string. |
| Join `departements.geojson` | `aggregate_departments()` performs a one-to-one geometry/statistics join and validates all features. |
| Aggregate regional products | `aggregate_regions()` groups Michelin categories, sums GDP/population/area, recomputes GDP per capita and density, population-weights departmental poverty, unemployment, wage, and median-living-standard values, groups coordinates, translates legacy region names, and joins `regions.geojson`. |
| Export CSV and GeoJSON | `_write_staged_products()` serializes and reload-validates all three products before `_publish_products()` changes final paths. |

Notebook cells that plot, display, rank, print set differences, or create unused
intermediate frames are intentionally excluded.

## Address and department rules

Every source address must contain exactly one five-digit postal code and must
split into a non-empty main address, city, postal code, and literal `France`.
Ambiguous addresses fail with the original value in the diagnostic.

The legacy 2023-2025 notebooks manually corrected two short Lacave addresses by
row number. The Python compatibility rule identifies the exact year, restaurant
name, and original address instead:

- `Château de la Treyne` — `Lacave, 46200, France`
- `Le Pont de l'Ouysse` — `Lacave, 46200, France`

Both reproduce the historical `address` and `location` values without relying
on unstable dataframe indices. The 2026 source no longer needs this override.

Corsica is explicit:

- `200xx` and `201xx` -> `2A`, Corse-du-Sud, Ajaccio;
- `202xx` -> `2B`, Haute-Corse, Bastia.

The authoritative names/capitals/regions still come from `departments.csv`.
Any other code without exactly one reference match fails; restaurants are never
dropped to make the join succeed.

## Michelin aggregation and coordinates

For 2025 onward, every department has counts for `selected`, `bib_gourmand`,
`1_star`, `2_star`, and `3_star`. `green_stars` counts rows where `greenstar ==
1`. `total_stars` is `1_star + 2*2_star + 3*3_star`, while
`starred_restaurants` counts the number of one-, two-, and three-star rows.

`locations` retains the legacy application representation: a stringified
dictionary with ordered keys `Selected`, `Bib`, `1`, `2`, and `3`; each value is
an ordered list of `(latitude, longitude)` tuples or `None`.

## Output schemas

`all_restaurants.csv` for 2025 onward:

```text
name,address,location,department_num,department,capital,region,price,cuisine,
url,award,stars,greenstar,longitude,latitude
```

`department_restaurants.geojson` properties:

```text
code,department,capital,region,selected,bib_gourmand,1_star,2_star,3_star,
total_stars,starred_restaurants,green_stars,gdp_current_prices_million_eur,
gdp_per_capita_eur,poverty_rate_percent,
census_unemployment_rate_15_64_percent,average_net_monthly_wage_fte_eur,
median_living_standard_eur,municipal_population,
population_density_per_sq_km,area_sq_km,locations
```

`region_restaurants.geojson` has the same aggregate/statistics properties,
without `code`, `department`, or `capital`, and begins with `region`. Regional
GDP, municipal population, and area are sums. GDP per capita and population
density are recomputed from those sums. Poverty, census unemployment, monthly
net full-time-equivalent wage, and median living standard are
population-weighted departmental values; the median field remains named for the
source metric and should not be interpreted as a directly published regional
median.

Metric definitions follow the INSEE/OECD product names:

- `gdp_current_prices_million_eur`: OECD TL3 GDP at current prices in million euros.
- `gdp_per_capita_eur`: GDP per capita derived from GDP and municipal population.
- `poverty_rate_percent`: INSEE Filosofi poverty rate.
- `census_unemployment_rate_15_64_percent`: census-derived unemployment rate for ages 15-64.
- `average_net_monthly_wage_fte_eur`: INSEE net monthly full-time-equivalent wage.
- `median_living_standard_eur`: INSEE Filosofi median living standard.
- `municipal_population`: INSEE municipal population.
- `population_density_per_sq_km`: municipal population divided by geometry-derived area.
- `area_sq_km`: department area derived from geometry in the INSEE product.

Geometry is EPSG:4326. To match the legacy application product, `Brittany`,
`Corsica`, and `Normandy` are translated to `Bretagne`, `Corse`, and
`Normandie` in the GeoJSON only. The restaurant CSV retains the department
reference's legacy region text.

## Validation and publication

Stage 2 fails before publication when:

- an input or required column is missing;
- the selected INSEE product CSV or manifest is missing;
- the highest numeric INSEE product year is malformed or incomplete;
- the INSEE product directory year, manifest year, or CSV `reference_year` disagree;
- the INSEE product manifest schema, row count, or output hash does not match the CSV;
- INSEE product department codes are null, duplicated, incomplete, or not exactly 96 rows;
- department/product/geometry keys are null, duplicated, or different;
- department, capital, or region values disagree across accepted references;
- address parsing is ambiguous or an assignment is unmatched;
- restaurant count/order/schema changes or required fields become null;
- category totals or coordinate groups cannot be represented;
- a department/product/geometry or regional geometry row is lost or duplicated;
- geometry is missing, empty, invalid, or not EPSG:4326.

All three files are written to a private staging tree, reloaded, and compared with
their validated in-memory forms before publication. Individual replacements
are atomic; an exception during the three-file transaction removes new files and
restores previous products from backups. `--replace` is required if any
target already exists.

## Fidelity and intentional differences

Automated integration tests rebuild 2025 and 2026, preserve the restaurant CSV
contract where legacy baselines are available, and assert the departmental and
regional GeoJSON products use the INSEE/OECD metric schema. Departmental and
regional GeoJSON files are no longer byte-identical to the legacy products
because their statistics columns now use the versioned INSEE/OECD product
definitions.

The implementation is intentionally stricter than the notebook:

- address and join diagnostics are fatal rather than printed for inspection;
- Corsica is resolved before joining instead of creating temporary unmatched
  rows;
- the two Lacave corrections use stable record evidence rather than row index;
- all 96 department codes must agree across the department reference, INSEE
  product, and geometry;
- serialized products are reloaded and validated before publication.

The 2023-2024 departmental products are not currently supported. Their legacy
GeoJSON files used earlier demographic values, and the 2024 restaurant data
also contains historical missing coordinates. They require a distinct accepted
reference snapshot/validation contract rather than silently using the current
INSEE product.

## Relationship to other stages

Stage 1 owns Michelin cleaning and country partitioning. Stage 2 consumes its
accepted France output without duplicating those transformations. The
independent `insee_pipeline` owns retrieval, provenance, release-year
selection, and normalization of departmental INSEE/OECD product data. Stage 2
consumes only the validated product CSV and manifest under
`data/products/insee/<year>/`.

## Monaco branch

The Monaco branch replaces the production transformations in
`Years/<year>/Notebooks/France/Monaco_Processing.ipynb`. It is supported for
2025 and 2026, the years with both a reference notebook and historical
application products.

Inputs:

```text
data/partitions/monaco/monaco_<year>.csv
data/raw/geodata/monaco.geojson
```

Canonical outputs:

```text
data/products/france/<year>/monaco_restaurants.csv
data/products/france/<year>/geodata/monaco_restaurants.geojson
```

The products remain below `france/<year>` because that reproduces the existing
application-facing layout; this does not classify Monaco as a French country
partition.

### Commands

```bash
# Canonical build
data_pipeline monaco --year 2026

# Disposable candidate
data_pipeline monaco --year 2026 \
  --output-root /tmp/michelin-stage2-monaco-2026

# No-write validation and deliberate replacement
data_pipeline monaco --year 2026 --validate-only
data_pipeline monaco --year 2026 --replace
```

### Notebook-to-Python mapping

| Notebook operation | Python replacement |
|---|---|
| Drop Stage 1 `city` and `country` | `prepare_monaco_restaurants()` selects only the application contract. |
| Split address/location/postcode | `_parse_monaco_address()` requires a main address, `Monaco`, a `98xxx` postcode, and an accepted historical country suffix. |
| Add administrative fields | `prepare_monaco_restaurants()` assigns arrondissement/department/capital `Monaco`, synthetic code `98`, and region `Provence-Alpes-Côte d'Azur`. |
| Build Michelin indicators and totals | `aggregate_monaco()` uses the shared Stage 2 Michelin category contract and computes total stars and starred restaurants. |
| Add demographic fields | `aggregate_monaco()` adds zero-valued schema placeholders in the France departmental column order. |
| Group coordinates by category | `aggregate_monaco()` creates the historical `Selected`, `Bib`, `1`, `2`, and `3` location dictionary. |
| Attach Monaco geometry and export | `aggregate_monaco()` attaches the single accepted geometry; staged output is reloaded before the two-file transaction publishes. |

Plots, dataframe displays, format-count prints, and other exploratory cells are
not production transformations and are intentionally excluded.

### Contracts and validation

`monaco_restaurants.csv` has the France application restaurant columns plus
`arrondissement`, with the same order as the notebook:

```text
name,address,location,arrondissement,department_num,department,capital,region,
price,cuisine,url,award,greenstar,stars,longitude,latitude
```

The GeoJSON uses the departmental property schema documented above and contains
one feature with `code=98`. Code `98` is a synthetic application compatibility
code, not a French department code. Monaco is associated with
`Provence-Alpes-Côte d'Azur` solely for compatibility with the existing
application. All GDP, population, wage, poverty, unemployment, density, and
area values are `0.0` schema placeholders; they are not Monaco statistics.

Addresses must parse into a non-empty street/address, literal city `Monaco`, a
five-digit `98xxx` postcode, and either the historical `France` suffix (2025)
or `Principality of Monaco` (2026 onward). Malformed input fails instead of
creating notebook-style `None, None` location strings. Rows are never dropped.
Coordinates must be complete and in geographic ranges; fixed administrative
values, output schema, row reconciliation, the single geometry feature, and
EPSG:4326 are validated before publication.

The canonical geometry is `data/raw/geodata/monaco.geojson`. It is byte-identical
to the legacy `ExtraData/Geodata/monaco.geojson` and contains a pre-existing ring
self-intersection also present in both historical products. This migration
preserves that accepted boundary for fidelity and does not silently repair it;
replacing it with valid geometry requires a separately reviewed reference-data
change.

The two outputs are staged, serialized, reloaded, and compared before either
canonical path changes. Existing files require `--replace`; a publication error
rolls back the complete pair. Integration tests demonstrate byte-for-byte
fidelity for both products in 2025 and 2026. No application-product baselines
exist for 2023-2024, so those years remain unsupported rather than being
declared faithful from partitions alone.
