# France Michelin Guide changes reporting

This downstream report compares two accepted consecutive annual France
restaurant products. It does not alter, correct, or establish the provenance of
either input. `France_Changes.ipynb` is historical evidence for the reporting
intent; the Python process is the operational implementation.

## Inputs and commands

Historical inputs use the paths already established in the repository:

```text
data/products/france/all_restaurants(arrondissements)_23.csv
data/products/france/all_restaurants(arrondissements)_24.csv
data/products/france/2025/all_restaurants(arrondissements).csv
data/products/france/2026/all_restaurants(arrondissements).csv
```

Canonical report:

```bash
PYTHONPATH=src python -m data_pipeline changes \
  --previous-year 2023 --current-year 2024
```

Candidate, validation-only, and deliberate replacement:

```bash
PYTHONPATH=src python -m data_pipeline changes \
  --previous-year 2025 --current-year 2026 \
  --output-root /tmp/michelin-changes

PYTHONPATH=src python -m data_pipeline changes \
  --previous-year 2025 --current-year 2026 --validate-only

PYTHONPATH=src python -m data_pipeline changes \
  --previous-year 2025 --current-year 2026 --replace
```

Years must be consecutive. Reports are written atomically to:

```text
data/reports/france/changes_<previous_year>_<current_year>.csv
data/reports/france/changes_<previous_year>_<current_year>.json
data/reports/france/changes_<previous_year>_<current_year>.md
```

The CSV is the analysis table, JSON packages the same records with a summary,
and Markdown is the readable material-change report.

## Matching hierarchy

The matcher never includes `stars` or `award` in identity keys. Reviewed
overrides are applied first, followed by deterministic one-to-one evidence:

1. normalized Michelin URL plus postcode;
2. unique normalized Michelin URL;
3. normalized name plus postcode;
4. normalized address plus postcode;
5. unique normalized address;
6. normalized name plus exact coordinates;
7. unique normalized name within five kilometres.

URLs discard query/fragment data and normalize host/trailing slash. Names and
addresses are case-folded, accent-folded, punctuation-normalized strings.
Duplicate keys are not forced into matches.

Remaining records stay explicitly classified as `new_entry` and
`removed_from_guide`. A controlled fuzzy pass compares normalized names only
when postcode/address agrees or coordinates are within two kilometres. Up to
three candidates scoring at least 0.72 are emitted as separate
`match_candidate`/`needs_review` records. They do not consume either identity,
do not suppress additions/removals, and are never silently accepted.

Optional reviewed matches can be stored separately at
`data/overrides/france_change_matches.csv` with:

```text
previous_year,current_year,previous_name,current_name,reason
```

Names must uniquely identify both annual rows. No overrides are currently
required or shipped; unresolved candidates remain visible in reports.

## Change classification and materiality

A matched identity may carry multiple pipe-separated change types:

```text
unchanged, promoted, demoted, newly_starred, renamed, relocated,
classification_changed, green_star_gained, green_star_lost
```

Unmatched annual rows use `new_entry` or `removed_from_guide`; fuzzy suggestions
use `needs_review`. A disappearance is never described as closure.

`classification_changed` covers movement among Selected, Bib Gourmand, and
starred classes. `newly_starred` is a matched record moving from below one star
to at least one star. A new entry already carrying stars remains a starred
`new_entry`, because the prior guide does not prove when it first received the
award.

Material records are:

- every star-count promotion or demotion and every newly starred match;
- every Green Star gain/loss when both years expose the field;
- starred additions and removals;
- unresolved fuzzy candidates involving a starred record.

All broader Selected/Bib and identity changes remain in CSV/JSON even when not
listed in the material Markdown tables.

## Optional fields and output audit data

The 2023–2024 products have no `greenstar` column and contain Bib/starred
restaurants only. The 2025–2026 schema adds Selected restaurants and Green
Stars. Missing Green Star history is represented as null/unknown; it is never
fabricated as zero, so 2024→2025 does not claim Green Star gains or losses.

Each structured record includes years, source row numbers, names, addresses,
locations, department/region, awards, numeric stars, optional Green Stars,
multi-valued change types, material status, matching method/confidence,
review status, and match evidence. Primary comparison rows reconcile exactly
to each annual input; candidate rows are visibly separate.

## Validation and limitations

The pipeline validates input existence/schema/nulls/duplicates, consecutive
years, one-to-one deterministic assignment, full row accounting, optional-field
handling, deterministic reruns, reloadable CSV, and three-file rollback.
Restaurant identity remains a pairwise reporting mechanism, not a global master
identity system. Fuzzy candidates require human review. No external closure,
press-release, or Michelin-news assertions are made.

The implementation intentionally excludes notebook dataframe displays,
visualisations, hard-coded row selections, one-off mutations/corrections,
inconsistent year-specific variables, press-announcement assertions, and the
destructive mutation of a prior-year dataframe.
