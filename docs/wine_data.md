# Wine Data Pipeline

The wine pipeline produces a durable, validated candidate and promotes it
unchanged through Stage 3 product verification and publication.

## Normal Workflow

```bash
python -m wine_pipeline build
python -m wine_pipeline simplify
python -m wine_pipeline assemble-candidate
python -m wine_pipeline publish-product
```

`build` downloads the INAO parcel source and UC Davis regional source, packages
and enriches the AOC geometry, and writes a unique Stage 1 working run beneath
`tmp/wine/<run-id>/`. Its durable validation and provenance reports are written
beneath `data/wine/provenance/`.

`simplify` resolves the sole Stage 1 candidate, processes every region in
deterministic order, and writes inspectable regional GeoJSON, metrics, plots,
and batch reports beneath `tmp/wine/simplification/<run-id>/`.

`assemble-candidate` resolves the sole validated simplification batch,
validates and concatenates its regional files without mutating geometry, and
writes a durable candidate beneath `data/candidates/wine/<candidate-id>/`.
Candidate assembly completes Stage 2.

`publish-product` resolves the sole validated durable candidate, verifies the
frontend-facing contract, and copies its GeoJSON byte-for-byte into a dated
product release. Stage 3 does not alter geometry, attributes, or feature order.

The lifecycle is:

```text
Stage 1 build
  -> Stage 2 regional simplification
  -> Stage 2 durable candidate assembly
  -> Stage 3 product verification and publication
```

## Storage Model

- `tmp/wine/` contains disposable downloads, working runs, regional candidates,
  metrics, and visual inspectables.
- `data/candidates/wine/` contains durable assembled candidates.
- `data/wine/` contains durable reports, validation, and provenance.
- `data/products/wine/<release-date>/` contains verified, published products.

Candidate assembly does not delete its source simplification run.
The canonical Stage 3 product layout is:

```text
data/products/wine/<YYYY-MM-DD>/
├── wine_regions_aoc_area.geojson
├── manifest.json
├── validation.json
└── provenance.json
```

Repository product publication does not deploy frontend/static application
assets. That remains a separate integration concern.

## Canonical Simplification

The reviewed `close500_simplify150` parameter set uses:

- **Buffer: 500 metres.** This is an outward-then-inward morphological closing
  operation that closes small gaps; it is not a permanent 500-metre expansion.
- **Simplification tolerance: 150 metres.** Topology-preserving simplification
  reduces boundary detail and payload complexity.
- **Overlap strategy: `smallest-wins`.** Smaller processed appellations take
  priority where appellations overlap. This can substantially reduce or fully
  cover larger appellations.

Parameter overrides are experimental. Their effective values and non-canonical
status are recorded in run metadata.

## Automatic Resolution

Wine commands select an upstream input only when it is unambiguous:

- zero matches: fail with the command or override needed to recover;
- one match: select it and print the resolved path or run ID;
- multiple matches: fail and require an explicit path or run ID.

`simplify`, `simplify-region`, and `diagnose-simplification` search
`tmp/wine/<stage-1-run-id>/candidates/aoc_regions.gpkg`. They ignore
simplification, diagnostic, fixture, smoke-test, and hidden temporary
directories.

`assemble-candidate` searches `tmp/wine/simplification/` and considers only
complete batches whose validation report passed. It never silently chooses the
newest of multiple valid runs.

`publish-product` searches `data/candidates/wine/` and considers only complete
candidates whose durable candidate-validation report passed. It also refuses
to choose when multiple eligible candidates exist.

## Product Promotion

The normal command uses the current local date:

```bash
python -m wine_pipeline publish-product
```

Recovery and historical releases can select both inputs explicitly:

```bash
python -m wine_pipeline publish-product \
  --candidate-id <candidate-id> \
  --release-date 2026-07-08
```

Release dates must use `YYYY-MM-DD`. Existing release folders are protected;
`--overwrite` performs a transactional replacement.

The canonical product schema is:

```text
region
app
display_name
colour
categorie
source_area_m2
geometry
```

Stage 3 validates schema and property ordering, product identities, EPSG:4326,
polygonal geometry, validity, emptiness, feature count, feature order, hashes,
and release metadata consistency. The source candidate and destination product
SHA-256 hashes must match.

## Review Policy

Candidate assembly defaults to `automated_validated_batch` approval. Blank,
`pending`, and `approved` regional review states may proceed when all automated
gates pass. Explicit `rejected` and `rerun_required` states always block
assembly.

Use `--require-manual-approval` to require every expected region to be marked
`approved`. The review CSV remains part of durable evidence when supplied.

The pipeline reports rather than silently discards:

- fully covered appellations;
- near-total area reductions;
- residual-overlap classifications;
- serialization component cleanup;
- post-reprojection topology repairs.

## Advanced Usage

- `--input PATH` selects a Stage 1 candidate explicitly for simplification or
  diagnostics.
- `--simplification-run-id ID` selects a simplification batch explicitly for
  assembly.
- `--run-id ID` names a simplification run.
- `--candidate-id ID` selects a durable candidate for publication.
- `--release-date YYYY-MM-DD` selects the dated product folder.
- `--resume` validates and reuses coherent regional artifacts.
- `--overwrite` transactionally replaces an existing run, candidate, or
  product release.
- `--keep-failed-temp` retains a failed single-region transactional directory
  for diagnosis.
- `--require-manual-approval` enables the strict review gate during assembly.
- `--quiet` suppresses command progress messages.

Run `python -m wine_pipeline <command> --help` for command-specific options.

## Current Non-Goals

The current workflow does not publish frontend application assets or delete
working runs after candidate creation.
