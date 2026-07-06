# Stage 3: France arrondissements and Paris

Stage 3 replaces the production work in
`Years/<year>/Notebooks/France/France_Arrondissements.ipynb`. It consumes the
accepted Stage 2 France restaurant CSV and local reference files. The
transformation never accesses the network.

## Inputs and outputs

Inputs:

```text
data/products/france/<year>/all_restaurants.csv
data/raw/demographics/arrondissement_stats_2023.csv
data/raw/demographics/paris_arrondissements.csv
data/raw/demographics/departments.csv
data/raw/geodata/arrondissements-avec-outre-mer.geojson
data/raw/geodata/departments.geojson
data/raw/geodata/paris_arrondissements.geojson
```

`arrondissement_stats_2023.csv` is the accepted legacy snapshot equivalent to
`ExtraData/Demographics/arrondissements_data_2023.csv`. It is parsed with its
third physical row as the header. Reconstructing it from INSEE is outside this
stage.

Canonical outputs:

```text
data/products/france/<year>/all_restaurants(arrondissements).csv
data/products/france/<year>/geodata/arrondissement_restaurants.geojson
data/products/france/<year>/geodata/paris_restaurants.geojson
```

## Commands

```bash
# Create the local Paris naming reference (networked, one-time acquisition)
PYTHONPATH=src python -m data_pipeline acquire-paris-arrondissements

# Deliberately refresh an accepted reference
PYTHONPATH=src python -m data_pipeline acquire-paris-arrondissements --refresh

# Canonical, candidate, and no-write Stage 3 builds
PYTHONPATH=src python -m data_pipeline arrondissements --year 2026
PYTHONPATH=src python -m data_pipeline arrondissements --year 2026 \
  --output-root /tmp/michelin-stage3-2026
PYTHONPATH=src python -m data_pipeline arrondissements --year 2026 --validate-only

# Replace an existing complete product set after validation
PYTHONPATH=src python -m data_pipeline arrondissements --year 2026 --replace
```

The extractor requests
`https://en.wikipedia.org/wiki/Arrondissements_of_Paris` with a user agent and
30-second default timeout. It finds a table by its arrondissement and name
columns, rather than relying on `tables[1]`, and writes only
`arrondissement_number`, `ordinal`, and `name`. Exactly 20 unique rows numbered
1 through 20 are required. Existing accepted bytes are protected unless
`--refresh` is explicit.

## Notebook-to-Python mapping

| Notebook operation | Python replacement |
|---|---|
| Promote row three and remove metadata | `load_arrondissement_demographics()` parses and validates the accepted semicolon file. |
| Remove overseas rows | Demographic and geometry codes beginning `97` are excluded; both mainland sets must contain 320 unique rows. |
| Reconcile definite articles | `reconcile_arrondissement_references()` uses article-free comparison while preserving demographic names. |
| Correct Briey | Geometry `Briey` explicitly reconciles to demographic `Val-de-Briey`. |
| Spatially join restaurants | `assign_restaurants()` uses `within`, detects duplicate matches, and applies a bounded same-department coastal fallback. |
| Replace Paris labels | `enrich_paris_labels()` parses Paris postcodes and maps the local 20-row reference to labels such as `1st (Louvre)`. |
| Aggregate national arrondissements | `build_arrondissement_product()` zero-fills all 320 geometries, joins department metadata and demographics, and groups Michelin counts/coordinates. |
| Build Paris GeoJSON | `build_paris_product()` validates all 20 municipal geometries and produces codes `75001` through `75020`. |
| Export three products | `_write_products()` stages, reloads, and compares all outputs before `run_stage3()` publishes the transaction. |

Plots, rankings, displays, print diagnostics, and unused intermediate frames are
not ported.

## Spatial assignment and validation

Restaurant coordinates must be complete and geographically valid. A normal
`within` join must produce at most one match. Seven accepted 2025/2026 points
lie just offshore their arrondissement polygons. The legacy notebook left them
null. Stage 3 instead chooses the unique nearest polygon in the restaurant's
already validated department, only when it is within 500 metres and no second
candidate is within that threshold:

```text
La Table de L'Oléa -> Arcachon
Le Vivier -> Lorient
La Gaffe -> Calvi
La Pointe du Cap Coz -> Quimper
Mamma - Les Roches Brunes -> Céret
Breizh Café Cancale -> Saint-Malo
Domaine de Rochevilaine -> Vannes
```

Any remaining or ambiguous match fails publication. No restaurant,
demographic row, or geometry is dropped. The pipeline also validates required
columns, unique names/codes, the 320-row mainland contract, department join
cardinality, valid EPSG:4326 geometries, numeric demographics, category-count
reconciliation, complete Paris coverage, deterministic reloads, and all-three
atomic publication.

## Paris rules and the removed positional drop

Paris rows are selected by department code `75`. Locations must end in a Paris
postcode. The final two digits identify municipal arrondissements; `75116` is
explicitly normalized to arrondissement 16 alongside `75016`.

This explains the notebook's unexplained `drop(index=15)`: the intermediate
table had two location rows for arrondissement 16 (`75016` and the special
delivery postcode `75116`). Stage 3 groups by the explicit arrondissement
number and emits one geometry/code row, so no positional deletion is needed.
All 20 labels and all 20 geometries must be present.

## Schemas and aggregation

The enriched CSV preserves the Stage 2 restaurant order and columns, inserting
`arrondissement` after `location`. Paris values use the stable ordinal/name
labels; other values use the national arrondissement name. Region labels use
the same French normalization as Stage 2 geographic products.

The national GeoJSON contains 320 rows and the historical properties:
administrative identity, Michelin category counts, total stars, starred
restaurant count, Green Star count, four accepted demographic fields, and
grouped one/two/three-star coordinates. Arrondissements without restaurants
receive integer zero counts and `None` coordinate groups.

The Paris GeoJSON contains 20 rows, codes `75001` through `75020`, the same
Michelin counts/totals, and grouped coordinates for all Michelin categories.

## Fidelity and intentional differences

For 2025 and 2026, `paris_restaurants.geojson` is byte-identical to the legacy
product. The enriched CSV differs only for the seven coastal assignments that
were null in the notebook. Consequently, the national GeoJSON differs only by
six `selected` increments and one `bib_gourmand` increment in their assigned
arrondissements. These are intentional validation corrections, not
serialization drift.

Other deliberate changes are separation of Wikipedia acquisition from the
offline transformation, table identification by schema, explicit `75116`
normalization instead of `drop(index=15)`, and fail-closed spatial/join checks.
Future reference-data ETL should version and replace the accepted demographic
and geometry snapshots without changing this Stage 3 interface.
