# Wine AOC Pipeline Roadmap

This document maps the current wine AOC geospatial workflow to the repository's
new `wine_pipeline` package and the tracked simplification work under
`Development/aoc_simplification/`.

It is based on the current code and notes. It is not a record of completed
product publication.

## Current Boundary

The pipeline should remain a two-stage process because both stages are long
running and produce inspection artifacts that need different review gates.

### Stage 1: source extraction and enrichment

Implemented package:

```text
src/wine_pipeline/
```

Current command:

```bash
wine_pipeline build
```

Current scope:

```text
INAO source download
  -> packaged AOC GeoPackage
  -> UC Davis broad-region enrichment
  -> enriched AOC-region candidate GeoPackage
  -> durable provenance and validation reports
```

Current candidate outputs:

```text
tmp/wine/<run-id>/candidates/aoc_packaged.gpkg
tmp/wine/<run-id>/candidates/aoc_regions.gpkg
```

Current durable report outputs:

```text
data/wine/provenance/wine_pipeline_<source-date>_<hash>.provenance.json
data/wine/provenance/wine_pipeline_<source-date>_<hash>.validation.json
```

Stage 1 does not simplify final app geometry and does not write to
`data/products/`.

### Stage 2: single-region simplification

Current development home:

```text
Development/aoc_simplification/
```

Current simplification source expected by the development scripts:

```text
Development/WineData/aoc_regions.gpkg
```

The package runner now reads the Stage 1 enriched candidate:

```text
tmp/wine/<run-id>/candidates/aoc_regions.gpkg
```

Current package command:

```bash
wine_pipeline simplify-region \
  --region "Jura" \
  --input tmp/wine/<run-id>/candidates/aoc_regions.gpkg
```

The runner processes one exact region only. It writes regional candidates and
inspection artifacts under:

```text
tmp/wine/simplification/<run-id>/regions/<region-slug>/
```

The regional artifact set is:

```text
candidate.geojson
metrics.json
params.json
preview.png
comparison.png
overlap_comparison.png
```

The simplified candidate schema is:

```text
region
app
display_name
colour
categorie
source_area_m2
geometry
```

The Stage 1-only fields are dropped:

```text
id_app
dt
region_method
overlap_ratio
```

All-region orchestration and merged candidate promotion are still future
stages.

## Existing Simplification Logic

The active strategy, now ported into
`src/wine_pipeline/aoc_simplification/transform.py`, is:

1. repair AOC geometry;
2. dissolve by `region`, `app`, `display_name`, `colour`, and `categorie`;
3. apply morphological closing with an outward and inward metric buffer;
4. simplify with topology preservation;
5. repair again;
6. optionally partition overlaps, with smaller complete appellations taking
   priority;
7. perform final polygon-only repair and validation;
8. reproject to EPSG:4326;
9. compare the result with the old app geometry.

Current constants and schemas:

```text
Working CRS: EPSG:2154
Output CRS: EPSG:4326
Stage 1 source columns: id_app, app, display_name, dt, region, region_method, overlap_ratio, colour, categorie, geometry
Output columns: region, app, display_name, colour, categorie, source_area_m2, geometry
Residual overlap tolerance: max(1e-6 m2, union_area_m2 * 1e-9)
```

The canonical package defaults are:

```text
RUN_ID = close500_simplify150
buffer = 500
simplify = 150
overlap_strategy = smallest-wins
```

Parameter meanings:

- `buffer = 500` is an outward-then-inward morphological closing operation in
  metres. It is not a permanent 500-metre expansion.
- `simplify = 150` is a topology-preserving simplification tolerance in
  metres. It reduces boundary detail and payload complexity.
- `overlap_strategy = smallest-wins` gives processed smaller appellations
  priority over larger overlapping appellations. It may substantially reduce
  or fully remove covered appellations.
- `close500_simplify150` is the canonical reviewed parameter set. CLI
  overrides are experimental and are recorded as non-canonical in `params.json`
  and `metrics.json`.

## Existing Review And Merge Workflow

The current development process is:

1. Run one region with `wine_pipeline simplify-region`, or in the development
   area with `run_experiment.py`.
2. Refresh the policy table from saved metrics:

   ```bash
   .venv/bin/python Development/aoc_simplification/update_region_policy_metrics.py \
     --run-id close500_simplify150
   ```

3. Review each region's plots and `metrics.json`, especially:

   ```text
   partition.fully_covered_app_names
   partition.per_app
   removed_overlap_percent
   residual_overlap_within_tolerance
   invalid_geometry_count
   empty_geometry_count
   ```

4. Diagnose invalid candidates without mutating them:

   ```bash
   .venv/bin/python Development/aoc_simplification/diagnose_invalid_candidates.py \
     --region "Bourgogne" \
     --run-id close500_simplify150
   ```

5. In a later tranche, merge only a complete, valid run:

   ```bash
   .venv/bin/python Development/aoc_simplification/merge_candidates.py \
     --run-id close500_simplify150 \
     --output Development/aoc_simplification/datasets/aoc_regions_close500_simplify150.geojson
   ```

The merge utility validates schema, CRS, polygonal geometry, geometry validity,
emptiness, duplicates, region coverage, and numeric `source_area_m2`. It
concatenates regional candidates without repair, buffering, simplification,
clipping, pruning, dissolving, or other geometry changes.

That merge behavior remains the future package-level candidate assembly
contract.

## Future Durable Candidate Layer

After all regional runs pass review, Stage 2 should promote a merged
simplification candidate to:

```text
data/candidates/wine/<run-id>/
```

Suggested candidate contents:

```text
data/candidates/wine/<run-id>/
├── wine_regions.geojson
├── wine_regions.metrics.json
├── wine_regions.validation.json
├── wine_regions.provenance.json
├── region_policy.csv
└── regions/
    └── <region-slug>/
        ├── metrics.json
        ├── params.json
        └── candidate.geojson
```

The candidate layer should be durable enough to review and reproduce the
decision, but it should still not be treated as the application product.

## Future Product Publication Gate

Publication should be an explicit third command or subcommand after candidate
verification, not a side effect of simplification.

Possible command:

```bash
wine_pipeline product --candidate-run-id <run-id>
```

Expected final output location:

```text
data/products/wine/
```

The exact product filename should be decided when the application contract is
wired. The existing development notes mention old app comparison geometry at:

```text
assets/data/wine_regions_cleaned.geojson
```

That path is a comparison baseline in the current development scripts, not an
automatic write target for the repository pipeline.

Before publication, verification should check:

- exact product schema expected by the app;
- CRS and coordinate order;
- valid, non-empty polygonal geometry;
- no duplicate product features;
- expected region coverage;
- expected app coverage, including any intentionally fully covered or
  coextensive appellations;
- residual overlap policy;
- file size and coordinate-count envelope;
- deterministic serialization;
- provenance link back to the Stage 1 enriched candidate and Stage 2 regional
  run metrics.

## Known Geometry Decision Point

The `close500_simplify150` investigation found that all source appellations
survive repair, dissolve, closing, and simplification. Loss happens during
smallest-wins partitioning.

Current saved-batch evidence records:

```text
354 source appellations
347 retained after partitioning
7 fully covered and omitted from candidate geometry
148 partially reduced
21 retained with at least 99% of processed area removed
```

The fully covered cases are coextensive or near-coextensive with earlier
priority rows. Area plus alphabetical tie-breaking therefore becomes an
ownership rule for those footprints.

Before publishing a product, the pipeline needs an explicit policy for these
cases. Options documented by the development notes include:

- represent coextensive appellations as aliases or multi-label footprints;
- use a reviewed appellation-priority table instead of area plus alphabetical
  tie-breaking;
- preserve fully covered appellations in metadata even when they have no unique
  polygon;
- allow selected coextensive appellations to overlap;
- keep a report-only review gate for fully covered and near-total-removal
  cases.

This is a product decision, not a geometry-cleaning bug.

## Package Mapping

Current development script responsibilities map naturally to future package
modules:

| Current file | Future package responsibility |
| --- | --- |
| `simplification.py` | `src/wine_pipeline/aoc_simplification/transform.py` |
| `run_experiment.py` | regional simplification runner under `pipeline.py` or `aoc_simplification/run.py` |
| `batch_processing.py` | batch orchestration over all regions |
| `update_region_policy_metrics.py` | candidate metrics and policy-table materialization |
| `diagnose_invalid_candidates.py` | optional diagnostics command for failed candidates |
| `merge_candidates.py` | candidate assembly and validation |
| `region_policy.csv` | reviewed candidate evidence copied into `data/candidates/wine/<run-id>/` |

The first package version should keep the logic specific to this wine-data
pipeline. It should not introduce a generic ETL framework.

## Non-Goals For The Next Wiring Step

- Do not write directly to `data/products/` from the simplification command.
- Do not delete `tmp/wine/simplification/<run-id>/` after a run.
- Do not hide regional failures by dropping rows.
- Do not treat a successful batch as product acceptance.
- Do not mutate notebook files as part of the package wiring.
- Do not copy the old app comparison baseline into the candidate unless the
  candidate report explicitly needs a small, documented comparison extract.
