# Wine AOC Pipeline Roadmap

## Delivery Status

- **Stage 1 build: complete.** Source extraction, AOC packaging, regional
  enrichment, and durable provenance/validation reporting are operational.
- **Stage 2 simplification: complete.** Single-region diagnostics and
  transactional all-region batch generation use the reviewed canonical
  geometry workflow.
- **Stage 2 candidate assembly: complete.** Validated batches can be assembled
  transactionally into durable candidates without geometry mutation.
- **CLI ergonomics and operational documentation: complete.** Normal commands
  resolve a sole valid upstream run automatically; ambiguity requires an
  explicit path or run ID. See `docs/wine_data.md`.
- **Stage 3 verification and product publication: complete.** Durable
  candidates are validated and promoted unchanged into dated folders beneath
  `data/products/wine/`.
- **Frontend/static-asset deployment: separate integration concern.** Product
  releases are not copied into application assets by this pipeline.

This document maps the current wine AOC geospatial workflow to the repository's
new `wine_pipeline` package and the tracked simplification work under
`Development/aoc_simplification/`.

It is based on the current code and notes and records the implemented product
publication boundary. Frontend deployment remains outside this pipeline.

## Current Boundary

The pipeline has three explicit stages. Stage 1 and regional Stage 2 work can
be long-running; Stage 2 assembly and Stage 3 publication apply separate
validation gates.

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

### Stage 2: simplification runners

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

Current single-region package command:

```bash
wine_pipeline simplify-region \
  --region "Jura"
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

Current all-region batch command:

```bash
wine_pipeline simplify
```

The batch command discovers non-empty region names from the Stage 1 candidate,
sorts them deterministically, and calls the same packaged Python
simplification runner used by `simplify-region`. It uses one consistent
parameter object across all regions.

Batch outputs are tmp-only inspection artifacts:

```text
tmp/wine/simplification/<run-id>/
├── run.json
├── batch_summary.json
├── validation.json
├── region_review.csv
└── regions/
    └── <region-slug>/
        ├── candidate.geojson
        ├── metrics.json
        ├── params.json
        ├── preview.png
        ├── comparison.png
        └── overlap_comparison.png
```

`run.json` records the Stage 1 source path and hash, effective parameters,
canonical status, dependency versions, package version, Git state, timestamps,
and command invocation. `batch_summary.json` records completed, skipped, and
failed regions, row totals, fully covered appellations, near-total reductions,
and whether the batch passed. `validation.json` checks region coverage,
artifact completeness, schema, CRS, geometry validity, source-hash consistency,
parameter consistency, and residual-overlap status.

`region_review.csv` is the current review handoff table. Machine-owned columns
may be refreshed by later tooling:

```text
region
region_slug
status
input_row_count
final_row_count
source_area_m2
final_area_m2
final_to_source_area_ratio
fully_covered_app_count
fully_covered_apps
near_total_reduction_count
invalid_geometry_count
empty_geometry_count
candidate_file_size_mb
parameter_set
source_sha256
error
```

Human-owned columns must not be overwritten by automated refreshes:

```text
review_status
reviewer
reviewed_at
geometry_assessment
overlap_assessment
fully_covered_assessment
notes
```

Normal batch mode refuses an existing run directory. `--resume` verifies
existing regional artifacts and skips only complete, coherent regions;
incomplete, stale, or mismatched regions are regenerated transactionally.
`--overwrite` replaces the complete batch run transactionally and leaves the
previous completed batch untouched if replacement fails. `--resume` and
`--overwrite` are mutually exclusive.

Durable candidate assembly is implemented as the completion gate for Stage 2.
Product verification and byte-preserving publication are implemented as
Stage 3.

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

Final serialization cleanup is deliberately narrower than the geometry
transform. Immediately before GeoJSON output, final geometry is repaired and
polygonal components are retained. Invalid, empty, zero-area, degenerate, or
non-polygonal debris may be removed only when the removed area is no more than
both 1 square metre and `1e-9` of `source_area_m2`. Empty rows or larger
removals fail. Per-app diagnostics are written to regional `metrics.json` and
surfaced in the batch summary and review table.

Invalid geometry exposed only after EPSG:4326 reprojection receives a separate
post-reprojection topology repair. Pre-repair and post-repair geometry areas
are measured in EPSG:2154. Repairs are `negligible` within either 10 square
metres or `1e-8`, and become review items above that level. They are `fatal`
only when both 100 square metres and `1e-6` of `source_area_m2` are exceeded,
or when output is invalid, empty, or non-polygonal. Component removal keeps the
separate, stricter 1-square-metre and `1e-9` tolerances. Regional metrics, batch
summaries, and the review table expose the classification.

The packaged lightweight diagnostic pass is:

```bash
wine_pipeline diagnose-simplification
```

It discovers regions deterministically, runs the existing transform and final
serialization checks, continues after failures, and creates no plots or normal
regional artifact directories. Compact `report.json` and `report.csv` outputs
are retained beneath
`tmp/wine/simplification/diagnostics/<diagnostic-run-id>/`. An optional exact
`--region` filter supports focused reproduction. This is diagnostic reporting
only; it does not approve, assemble, promote, or publish candidates.

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

1. Run one region with `wine_pipeline simplify-region`, an all-region tmp batch
   with `wine_pipeline simplify`, or in the development area with
   `run_experiment.py`.
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
   residual_overlap.classification
   invalid_geometry_count
   empty_geometry_count
   ```

4. Diagnose invalid candidates without mutating them:

   ```bash
   .venv/bin/python Development/aoc_simplification/diagnose_invalid_candidates.py \
     --region "Bourgogne" \
     --run-id close500_simplify150
   ```

5. Historical development merge command, retained only as context:

   ```bash
   .venv/bin/python Development/aoc_simplification/merge_candidates.py \
     --run-id close500_simplify150 \
     --output Development/aoc_simplification/datasets/aoc_regions_close500_simplify150.geojson
   ```

The development merge utility established the packaged candidate assembly
contract: validate schema, CRS, polygonal geometry, geometry validity,
emptiness, duplicates, region coverage, and numeric `source_area_m2`; then
concatenate regional candidates without repair, buffering, simplification,
clipping, pruning, dissolving, or other geometry changes.

## Durable Candidate Assembly

After the Stage 2 batch passes automated validation, the packaged assembly
command promotes a durable candidate:

```bash
wine_pipeline assemble-candidate
```

The default assembly policy is designed for solo maintenance. It allows blank
or `pending` review statuses when automated gates pass, and it blocks only
explicit `rejected` or `rerun_required` states. It also blocks failed batches,
missing expected regions, fatal residual-overlap classifications, and fatal
serialization or post-reprojection repair classifications. If
`region_review.csv` is absent, the durable copied review report is generated
from machine batch evidence and records that no manual review file was supplied.

Strict manual approval remains available:

```bash
wine_pipeline assemble-candidate \
  --require-manual-approval
```

Strict mode requires every expected region to have
`review_status=approved`. Candidate manifest and provenance record either
`automated_validated_batch` or `explicit_manual_approval`.

Assembly consumes:

```text
tmp/wine/simplification/<run-id>/
├── run.json
├── batch_summary.json
├── validation.json
├── region_review.csv
└── regions/*/
    ├── candidate.geojson
    ├── metrics.json
    └── params.json
```

It requires batch validation to have passed, no failed regions, complete
expected-region coverage, consistent Stage 1 source hash, consistent canonical
parameters, complete regional artifact sets, exact regional schema, EPSG:4326,
and valid non-empty polygonal geometry.

The durable candidate package is written beneath:

```text
data/candidates/wine/<candidate-id>/
```

Candidate contents:

```text
data/candidates/wine/<candidate-id>/
├── wine_regions.geojson
├── manifest.json
└── provenance.json
```

Durable evidence is copied to:

```text
data/wine/reports/<candidate-id>_region_review.csv
data/wine/reports/<candidate-id>_assembly_summary.json
data/wine/validation/<candidate-id>.validation.json
data/wine/provenance/<candidate-id>.provenance.json
```

Machine-owned review-table columns may be refreshed before assembly, but the
human-owned columns must remain intact:

```text
review_status
reviewer
reviewed_at
geometry_assessment
overlap_assessment
fully_covered_assessment
notes
```

The tmp regional outputs remain disposable after a durable candidate is
assembled. The candidate layer is durable evidence for the next gate, but it is
still not the application product.

## Stage 3 Product Publication

Publication is an explicit Stage 3 command after candidate assembly, not a side
effect of simplification:

```bash
wine_pipeline publish-product
```

The command verifies the candidate and copies its GeoJSON byte-for-byte into:

```text
data/products/wine/<release-date>/
├── wine_regions_aoc_area.geojson
├── manifest.json
├── validation.json
└── provenance.json
```

The canonical filename is `wine_regions_aoc_area.geojson`. Stage 3 does not
repair, simplify, dissolve, remove, recalculate, or reorder candidate features.
The candidate and product hashes must match.

The existing development notes mention old app comparison geometry at:

```text
assets/data/wine_regions_cleaned.geojson
```

That path is a comparison baseline in the current development scripts, not an
automatic write target for the repository pipeline.

Publication verification checks:

- exact product schema expected by the app;
- EPSG:4326 coordinate convention;
- valid, non-empty polygonal geometry;
- no duplicate product features;
- byte-identical promotion and preserved feature ordering;
- provenance link back to the Stage 1 enriched candidate and Stage 2 regional
  run metrics.

Frontend/static-asset deployment remains separate and is not performed by
`publish-product`.

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
