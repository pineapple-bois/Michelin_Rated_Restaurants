# Repository guidance for coding agents

## Purpose and direction

This repository is the historical data-ingestion and transformation pipeline behind the Michelin-rated restaurant analysis and its downstream application data. It combines annual Michelin Guide extracts with INSEE/demographic tables and French geographic boundaries, then produces enriched CSV and GeoJSON datasets for analysis, visualisation, and the separate Dash application linked from `README.md`.

The current workflow is legacy and notebook-led. Preserve it while moving reusable work incrementally toward a reproducible Python **Extract -> Transform** process. Do not describe a migration as complete until scripts/modules, tests, validation reports, and documented commands actually exist.

## Current repository map

- `Years/<year>/data/Michelin/michelin_data_<year>.csv` is the annual source extract. Treat source extracts as immutable raw data: never clean, normalize, or overwrite them in place.
- `Years/<year>/Notebooks/Data-Preparation.ipynb` currently selects columns, normalizes names and locations, converts awards to `stars`, partitions countries, and writes French, UK, and Monaco outputs.
- The French normalized/master output is `france_master.csv` in 2023 and `france_master_<year>.csv` from 2024 onward. Historic master files are records of prior guide years; do not rewrite them to make a new year easier to process.
- `France_Processing.ipynb` derives department codes from postal addresses, joins `ExtraData/Demographics/departments.csv`, enriches restaurant rows, and writes `all_restaurants.csv` plus department/region GeoJSON. `france_complete_2024.csv` is an additional 2024 intermediate, not a repository-wide convention.
- `France_Arrondissements.ipynb` adds arrondissement assignments and writes `all_restaurants(arrondissements).csv` plus arrondissement and Paris GeoJSON. These enriched CSV/GeoJSON files are the closest existing equivalents to application-ready outputs.
- `France_Changes.ipynb` compares adjacent years. Existing matching uses combinations of `url`, `name`, `location`, address/postcode, and fuzzy name matching; it is useful precedent, but not yet a stable identity system.
- `ExtraData/Demographics/` and `ExtraData/Geodata/` contain shared reference inputs. `ExtraData/France_Departments_Demographics.ipynb` produced shared demographic tables. `ExtraData/Wine/` and `Functions/functions_wine.py` support a separate geospatial enrichment thread.
- `Functions/` currently contains shared analysis, visualisation, Plotly, and wine helpers. Core annual cleaning and joining logic remains duplicated in notebooks.
- `requirements.txt` is the only checked-in dependency configuration. The README documents `python3 -m venv env`, `source env/bin/activate`, and `pip install -r requirements.txt`. There is currently no checked-in test directory, pytest dependency/configuration, package metadata, Makefile, or CI workflow.

Years 2023-2025 are the tracked annual sequence; a 2022 import and the 2026 workflow/data may also be present as uncommitted user work. The next annual target is **2027**. Build 2027 support beside historic years; do not mutate an older year into the new baseline or blindly copy stale notebook outputs.

## Data contracts and fidelity

Keep these logical stages distinct even where the legacy tree currently co-locates them:

1. **Raw input:** the untouched annual Michelin extract and authoritative shared source files.
2. **Normalized annual data:** standardized fields and country partitions, including the annual French master.
3. **Enriched/intermediate data:** postal, department, region, demographic, and spatial joins such as `all_restaurants.csv`.
4. **Validation reports:** proposed machine-readable summaries of checks, match decisions, exceptions, and row-count reconciliation. No dedicated layer exists yet; add one only as an explicit, reviewable migration step.
5. **Application-ready data:** the final arrondissement-enriched CSV and geographic outputs consumed by visualisation/application workflows.

Preserve source spelling, accents, URLs, coordinates, awards, Green Star values, and provenance unless a documented normalization rule requires a derived value. Do not silently discard rows or coerce unknown categories. Record manual overrides and explain why they are needed. Use explicit schemas and deterministic ordering/serialization where practical so rerunning the same inputs yields equivalent outputs. Transformations must be idempotent and must not depend on notebook execution history.

Before publishing application-ready artifacts or replacing any master data, validate at least:

- required columns, column order where it is a consumer contract, and expected dtypes;
- nulls in required identifiers, awards, locations, postal/department fields, and geometries;
- exact duplicates and duplicate candidate identities;
- join cardinality and unmatched rows before and after every demographic or geospatial join;
- valid longitude/latitude ranges, CRS, non-empty/valid geometries, and plausible containment in France/Monaco as applicable;
- allowed country, award/star, Green Star, region, department, and other categorical values;
- row counts reconciled from raw input through country partitioning and every later stage, with explained additions/exclusions.

A critical schema, identity, duplicate, join-cardinality, geographic, or row-reconciliation failure blocks master-data replacement and application-ready publication. Produce diagnostics and stop; do not “fix” the output by dropping problematic records.

## Restaurant identity across years

Do not treat restaurant name alone as an identity. URLs can change, names can change with a chef, and addresses/location strings can be reformatted. For the 2027 pipeline, preserve source fields and develop a durable identity/match table incrementally using normalized URL, address/postcode, coordinates, name, and prior-year evidence. Classify matches as exact, rule-based, fuzzy candidate, or manually confirmed; retain scores/reasons and both source records. Require review for ambiguous one-to-many or many-to-one matches. Fuzzy matching may propose candidates but must not silently update history or determine star changes by itself.

## Implementation and tests

Move reusable normalization, partitioning, identity matching, validation, and export logic out of notebooks into small tested Python modules. Keep notebooks as orchestration, exploration, and visual verification clients of that logic. Prefer pure functions with explicit inputs/outputs over cells that mutate global state. Make changes small enough to review against one stage or contract at a time.

Use small synthetic fixtures to test accents and apostrophes, Corsican/alphanumeric department codes, Paris arrondissements, Monaco, missing URLs, renamed restaurants, duplicate candidates, changed coordinates, unmatched joins, and join explosions. Add regression tests for any confirmed production edge case without copying sensitive or unnecessarily large source files into fixtures. Test both successful output and fail-closed validation behavior.

Do not invent project commands. Read `README.md`, the relevant yearly README/notebooks, `requirements.txt`, and any test or dependency configuration that exists at task time. Use only commands supported there or by newly added tooling in the same reviewed change; if no test runner exists, say so and run focused, read-only checks appropriate to the files changed. Do not execute notebooks merely to inspect them, because execution can rewrite outputs and data artifacts.

## Git and change safety

Assume the working tree may already be dirty. At the start and end of work, inspect `git status --short`, `git diff --stat`, and targeted diffs. Every pre-existing tracked modification and untracked file is user-owned. Do not stash, reset, restore, checkout over, clean, delete, reformat, regenerate, or otherwise alter unrelated work. Never use destructive Git commands such as `git reset --hard` or `git clean`. Do not rewrite notebook metadata or outputs incidentally.

Touch only files required by the request. Before editing a dirty file, inspect its diff and preserve the user's changes. Prefer narrow patches; do not combine pipeline migration, historical-data rewrites, dependency cleanup, and output regeneration in one change.

## Handoff expectations

Report:

- every file changed and which logical data stage it affects;
- tests and validation commands run, with pass/fail results;
- row counts, unmatched records, duplicate findings, join cardinalities, and other relevant validation outcomes;
- assumptions, manual mappings, deferred issues, and whether any output is proposed rather than published;
- all pre-existing dirty files observed, clearly separated from files changed by the task.

If validation was not run, tooling was absent, or a source/provenance question remains unresolved, state that explicitly. Never imply that application-ready or master data is safe to publish without evidence from the relevant validation layer.
