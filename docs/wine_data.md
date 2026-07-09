# Wine Data Pipeline

## Overview

The wine pipeline builds reproducible French AOC geometry, simplifies it for
delivery, assembles a durable candidate, and promotes that candidate unchanged
into a dated product release.

The lifecycle is:

```text
INAO source
  -> Stage 1 enriched AOC run
  -> Stage 2 regional simplification run
  -> Stage 2 assembled candidate
  -> Stage 3 promoted product
```

The normal CLI flow is:

```bash
python -m wine_pipeline build
python -m wine_pipeline simplify
python -m wine_pipeline assemble-candidate
python -m wine_pipeline publish-product
```

Each downstream command resolves the sole eligible upstream input
automatically. Explicit paths and IDs are needed only for ambiguity, recovery,
experiments, or historical runs. If no eligible input exists the command
explains how to produce or select one; if several exist it lists them and
refuses to choose the newest silently.

Candidate assembly completes Stage 2. `publish-product` is Stage 3. Frontend or
static-asset deployment remains a separate integration concern.

## Stage 1: Build And Enrich

`build` downloads the current INAO AOC parcel source and the UC Davis broad
wine-region polygons:

- INAO dataset page:
  `https://www.data.gouv.fr/datasets/delimitation-parcellaire-des-aoc-viticoles-de-linao`
- configured INAO resource:
  `https://www.data.gouv.fr/api/1/datasets/r/e79a7c68-2fe4-4225-a802-8379a8d6426c`
- UC Davis repository:
  `https://github.com/UCDavisLibrary/wine-ontology/tree/master/examples/france`
- configured UC Davis GeoJSON:
  `https://raw.githubusercontent.com/UCDavisLibrary/wine-ontology/master/examples/france/regions.geojson`

The INAO source and packaged AOC geometry use EPSG:2154. Regional overlay and
area calculations also use EPSG:2154. The UC Davis source is a
non-authoritative, hand-drawn broad regional classification source; its exact
resolved URL and commit SHA are recorded when available.

Every Stage 1 run is isolated beneath:

```text
tmp/wine/<stage-1-run-id>/
├── downloads/
├── extracted/
├── candidates/
│   ├── aoc_packaged.gpkg
│   └── aoc_regions.gpkg
└── run-report.json
```

Both GeoPackages use layer `aocs_france`. The enriched Stage 1 candidate has
this exact schema:

```text
id_app
app
display_name
dt
region
region_method
overlap_ratio
colour
categorie
geometry
```

The INAO package groups parcel rows by `app` and `id_app`. `dt` must contain
exactly one distinct non-null value within each group. Mixed parcel-level
`categorie` values are reported rather than treated as a fatal inconsistency;
the value from the first source row in stable source order is retained as the
representative official value.

Before grouping, Stage 1 defines this dataset semantically as wine-only:
`categorie` must contain the whole word `Vin`, matched case-insensitively.
Rows that do not satisfy that inclusion rule are excluded explicitly. The run
report records the filter expression, excluded row count, and distinct excluded
`app`, `id_app`, and `categorie` records. Packaging fails if filtering leaves no
wine rows, and both the in-memory transform and serialized GeoPackage validation
assert that no non-wine category survives.

### Source data issue: non-wine AOC records

Before grouping, Stage 1 now restricts the dataset to wine records only. 
A row is retained when `categorie` contains the whole word `Vin`, matched case-insensitively.

Rows that do not meet this rule are excluded. The run report records:

- the filter used;
- the number of excluded rows;
- the distinct excluded values of `app`, `id_app`, and `categorie`.

The pipeline fails if no wine rows remain after filtering. Validation also checks that 
no non-wine category is present in either the in-memory result or the written GeoPackage.

Inspection of the 2026-07-08 product found two non-wine appellations:

```text
Taureau de Camargue | Bovin
Béa du Roussillon   | Tubercule
```

The investigation traced both records back through the packaged and enriched candidates to 
the original INAO shapefile. They were therefore present in the source and were not introduced 
by geometry processing, enrichment, simplification, assembly, or publication.

The pipeline had preserved the source correctly, but it had not yet enforced the intended 
wine-only scope of the product. The new Stage 1 filter defines that scope explicitly. 
It does not alter the INAO source; it only controls which source records are included 
in the wine dataset.

Regional assignment uses deterministic majority overlap, followed by reviewed
literal `id_app` overrides and then the reviewed `dt` delegation fallback only
for still-unmatched rows. `region_method` is therefore one of:

```text
spatial_majority
explicit_override
delegation_fallback
```

`overlap_ratio` is retained for spatial-majority assignments and is null for
explicit overrides and delegation fallback. `dt` is preserved as source
delegation metadata; it is not recomputed during enrichment.

Stage 1 provenance records configured and resolved URLs, retrieval headers and
times, hashes, byte sizes, extracted members, source and output schemas, CRS,
bounds, geometry types, repair counts, transformation parameters, reviewed
mappings, validation checks, and output identities. Durable Stage 1 provenance
and validation reports are written beneath `data/wine/provenance/`.

## Stage 2: Regional Simplification

`simplify` consumes one enriched `aoc_regions.gpkg`, discovers non-empty region
names, and processes them in deterministic sorted order. `simplify-region`
uses the same transform for one exact region.

Stage 2 deliberately drops fields that belong only to Stage 1 classification:

```text
id_app
dt
region_method
overlap_ratio
```

Every regional output and assembled candidate has this exact schema and order:

```text
region
app
display_name
colour
categorie
source_area_m2
geometry
```

`source_area_m2` is calculated from each dissolved source geometry in
EPSG:2154. It is captured before morphological closing, simplification,
smallest-wins overlap partitioning, final cleanup, or EPSG:4326 reprojection.
It therefore remains an audit measure of the complete dissolved source
appellation even when the retained exclusive geometry is substantially
smaller.

### Canonical Simplification Policy

The reviewed canonical parameter set is:

```text
run name: close500_simplify150
buffer distance: 500 metres
simplification tolerance: 150 metres
overlap strategy: smallest-wins
working CRS: EPSG:2154
output CRS: EPSG:4326
```

The 500-metre operation is an outward buffer followed by an inward buffer: a
morphological closing that joins small gaps. It is not a permanent 500-metre
expansion.

The 150-metre operation is topology-preserving simplification. It reduces
boundary detail and payload complexity while preserving topology.

`smallest-wins` gives smaller processed appellations priority over larger
overlapping appellations. Priority is sorted exactly by:

1. processed geometry area ascending;
2. appellation name ascending;
3. original row order;
4. stable merge sort.

Accepted output is subsequently sorted deterministically by `region`, `app`,
`display_name`, `colour`, and `categorie`. Parameter overrides are
experimental and are recorded as non-canonical in run metadata.

### Final Component Cleanup

Before serialization, each final geometry is processed in EPSG:2154:

1. repair the geometry;
2. extract Polygon and MultiPolygon components only;
3. identify empty, zero-area, degenerate, invalid, or non-polygonal debris;
4. retain all meaningful valid polygonal geometry;
5. validate the result before reprojection.

A rejected component may be deleted only when the removed area satisfies both
strict limits:

```text
removed area <= 1 square metre
removed area / source_area_m2 <= 1e-9
```

Exceeding either limit fails cleanup. Cleanup also fails if the complete
appellation becomes empty, remains invalid, becomes non-polygonal, or would
lose meaningful geometry. Every cleanup action records component counts,
removed area, relative area, geometry types, validity reason, and action.
Cleanup is never silent.

### Post-Reprojection Topology Repair

Final geometry is reprojected to EPSG:4326 and validated again. Geometry that
becomes or remains invalid receives `make_valid`, followed by polygon-only
extraction and validation.

Area change is measured by projecting the complete pre-repair and post-repair
topological footprints back to EPSG:2154. The comparison does not sum component
areas because overlapping components could be counted more than once.

Post-reprojection repair classification is:

- `none`: no repair was required.
- `negligible`: repair succeeded and absolute area change is at most 10 m²
  **OR** relative area change is at most `1e-8`.
- `review`: repair succeeded and is above both negligible thresholds, while
  not exceeding both fatal thresholds.
- `fatal`: absolute area change is greater than 100 m² **AND** relative area
  change is greater than `1e-6`, or the repaired output is invalid, empty, or
  non-polygonal.

The hard area thresholds use AND semantics: exceeding only one of 100 m² or
`1e-6` is accepted as `review`. Valid, non-empty polygonal output is always
required.

### Residual Overlap Policy

Residual overlap is measured from the final regional geometry without changing
the overlap-removal algorithm. The overlap ratio is:

```text
residual overlap area / union area
```

The numerical tolerance is:

```text
max(1e-6 m², union area * 1e-9)
```

Classification is applied in this order:

- `none`: overlap is less than or equal to the numerical tolerance.
- `fatal`: overlap is greater than 1,000 m² **AND** overlap ratio is greater
  than `1e-5`.
- `negligible`: not fatal, and overlap is at most 100 m² **OR** overlap ratio
  is at most `1e-7`.
- `review`: above the negligible thresholds but not fatal.

Fatal classification uses AND semantics. A region exceeding only one fatal
threshold remains reportable as `review`. `none`, `negligible`, and `review`
allow regional output generation; `fatal` fails the region. Review-level
overlap does not automatically block default solo-maintainer candidate
assembly.

### Fully Covered And Near-Total Reductions

A fully covered appellation has no exclusive geometry left after earlier,
higher-priority appellations claim the same footprint. This can be legitimate
under `smallest-wins`, particularly for coextensive or near-coextensive
appellations. It does not mean the appellation was lost before partitioning.

A near-total reduction retains geometry but loses almost all processed area
during overlap partitioning. It is an inspection warning, not automatically a
geometry failure.

Both conditions are preserved explicitly in regional metrics, batch summaries,
validation evidence, review tables, candidate manifests, and provenance. They
are not silently discarded. The preserved `source_area_m2` and per-app
partition diagnostics retain the source-to-final relationship.

## Batch Artifacts And Review Evidence

An all-region simplification run is written beneath:

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

`run.json` records source identity and hash, Stage 1 run ID, expected region
inventory, parameters, canonical status, dependency versions, package version,
Git state, command, and timestamps. `batch_summary.json` records completion and
failure counts, row totals, fully covered appellations, near-total reductions,
cleanup and repair classifications, and residual overlap. `validation.json`
contains structured batch checks.

`region_review.csv` combines refreshable machine-owned evidence with
human-owned review fields. Automated refresh must preserve:

```text
review_status
reviewer
reviewed_at
geometry_assessment
overlap_assessment
fully_covered_assessment
notes
```

Regional PNGs and other inspection artifacts remain disposable under `tmp/`.
They are not copied wholesale into durable candidate storage.

## Candidate Assembly

`assemble-candidate` validates one complete Stage 2 batch, concatenates its
regional candidates, sorts with a stable merge sort by `region`, `app`,
`display_name`, `colour`, and `categorie`, writes the merged GeoJSON, and
reloads it for validation. It does not repair, simplify, dissolve, remove,
reproject, or otherwise mutate geometry or attributes.

The durable package is:

```text
data/candidates/wine/<candidate-id>/
├── wine_regions.geojson
├── manifest.json
└── provenance.json
```

Assembly requires complete region coverage, consistent Stage 1 source hashes,
consistent simplification parameters and run identity, exact schema,
EPSG:4326, polygon-only valid non-empty geometry, unique output identities,
deterministic ordering, complete regional artifacts, and matching row counts.
Durable review, assembly-summary, validation, and provenance evidence is also
written beneath `data/wine/`.

The default solo-maintainer approval mode is:

```text
automated_validated_batch
```

Blank, `pending`, and `approved` review states may proceed when automated gates
pass. Explicit `rejected` and `rerun_required` states block assembly.
Review-classified repairs, overlap, fully covered appellations, and near-total
reductions remain reportable but do not automatically block the default mode.

Strict review is opt-in:

```bash
python -m wine_pipeline assemble-candidate --require-manual-approval
```

It requires every expected region to be explicitly approved and records:

```text
explicit_manual_approval
```

The selected approval mode is recorded in candidate manifest and provenance.

## Stage 3: Product Promotion

`publish-product` validates one durable candidate against the frontend-facing
contract and publishes it without transformation:

```text
data/products/wine/<YYYY-MM-DD>/
├── wine_regions_aoc_area.geojson
├── manifest.json
├── validation.json
└── provenance.json
```

The product preserves the candidate schema and feature order. Promotion
performs no geometry repair, simplification, dissolve, removal, reprojection,
attribute transformation, or metric recalculation. The candidate GeoJSON is
copied byte-for-byte, and source and product SHA-256 hashes must match.

Stage 3 validates candidate evidence and hash, FeatureCollection structure,
exact ordered schema, required properties, identity strings,
`source_area_m2`, EPSG:4326, polygon-only valid non-empty geometry, duplicate
identities, feature count and order, output hash, and internal release metadata.
It writes product manifest, validation, and provenance files. Existing dated
release folders are protected unless `--overwrite` is supplied; replacement is
transactional.

Repository product creation does not deploy or copy the product into frontend
or static application assets.

## Storage Model

- `tmp/wine/`: disposable downloads, extracted sources, working runs,
  diagnostics, regional candidates, metrics, and visual inspectables.
- `data/candidates/wine/`: durable validated assembled candidates.
- `data/wine/`: durable reports, validation, provenance, and review evidence.
- `data/products/wine/`: dated verified product releases.

Working and regional runs are retained after candidate assembly and product
promotion. Cleanup is an explicit maintainer action, not a pipeline side
effect.

## Recovery And Advanced CLI Usage

The normal workflow requires no path or ID when exactly one eligible upstream
artifact exists. Current recovery and advanced options are:

- `--input PATH`: select a Stage 1 `aoc_regions.gpkg` for `simplify`,
  `simplify-region`, or `diagnose-simplification`.
- `--run-id ID`: name a simplification batch or single-region run.
- `--simplification-run-id ID`: select a batch for candidate assembly.
- `--candidate-id ID`: select a durable candidate for Stage 3.
- `--release-date YYYY-MM-DD`: select the dated product release folder.
- `--resume`: validate and reuse coherent regional artifacts while rebuilding
  incomplete or stale regions.
- `--overwrite`: transactionally replace an existing regional run, batch,
  durable candidate, or dated product release where supported.
- `--keep-failed-temp`: retain a failed single-region transactional directory
  for diagnosis.
- `--require-manual-approval`: require every expected region to be approved
  during assembly.

Experimental `--buffer`, `--simplify`, and `--overlap-strategy` overrides are
available for Stage 2 and are always recorded as non-canonical when they differ
from the reviewed defaults. Stage 3 intentionally has no geometry-processing
parameters.

Use `python -m wine_pipeline <command> --help` for command-specific options.

## Provenance Lineage

The durable lineage is:

```text
configured and resolved INAO/UC Davis sources
  -> enriched Stage 1 run and source hashes
  -> regional simplification run and effective parameters
  -> assembled candidate and approval mode
  -> promoted product with identical candidate/product hash
```

Reports and manifests record source identities, paths and hashes, Stage 1 and
simplification run IDs, effective and canonical parameters, validation state,
row and region inventories, cleanup and review diagnostics, command
invocations, timestamps, package and dependency versions, environment details,
and Git commit/dirty state where available.
