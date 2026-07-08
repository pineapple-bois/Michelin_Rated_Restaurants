# Wine Data Pipeline

The wine pipeline produces a durable, validated candidate in three operational
steps. Product verification and publication are a future Stage 3.

## Normal Workflow

```bash
python -m wine_pipeline build
python -m wine_pipeline simplify
python -m wine_pipeline assemble-candidate
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

The lifecycle is:

```text
Stage 1 build
  -> Stage 2 regional simplification
  -> Stage 2 durable candidate assembly
  -> future Stage 3 product verification and publication
```

## Storage Model

- `tmp/wine/` contains disposable downloads, working runs, regional candidates,
  metrics, and visual inspectables.
- `data/candidates/wine/` contains durable assembled candidates.
- `data/wine/` contains durable reports, validation, and provenance.
- `data/products/wine/` is reserved for verified, published products.

Candidate assembly does not delete its source simplification run.

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
- `--resume` validates and reuses coherent regional artifacts.
- `--overwrite` transactionally replaces an existing run or candidate.
- `--keep-failed-temp` retains a failed single-region transactional directory
  for diagnosis.
- `--require-manual-approval` enables the strict review gate during assembly.

Run `python -m wine_pipeline <command> --help` for command-specific options.

## Current Non-Goals

The current workflow does not publish application assets, write a final product
automatically, implement Stage 3 verification, or delete working runs after
candidate creation.
