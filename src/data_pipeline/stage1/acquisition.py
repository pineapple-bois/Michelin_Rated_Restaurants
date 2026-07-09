"""Stage 1 acquisition and annual France acceptance gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import re
import shutil
import tempfile

import pandas as pd
import requests

from data_pipeline.changes.matching import (
    normalized_text,
    prepare_matching_frame,
    reconcile_restaurants,
)

from .pipeline import (
    _write_staged_partitions,
    partition_paths,
    prepare_partitions,
)


UPSTREAM_CSV_URL = (
    "https://raw.githubusercontent.com/ngshiheng/michelin-my-maps/"
    "main/data/michelin_my_maps.csv"
)
UPSTREAM_COMMITS_URL = (
    "https://api.github.com/repos/ngshiheng/michelin-my-maps/commits"
)
UPSTREAM_PATH = "data/michelin_my_maps.csv"


@dataclass(frozen=True)
class SourceInfo:
    url: str
    revision: str | None = None


@dataclass(frozen=True)
class FranceComparison:
    previous_rows: int
    candidate_rows: int
    added_columns: tuple[str, ...]
    removed_columns: tuple[str, ...]
    shared_columns: tuple[str, ...]
    missing_expected_columns: tuple[str, ...]
    matched_restaurants: int
    match_rate: float
    ambiguous_matches: int
    duplicate_conflicts: int
    comparison_reliable: bool
    reliability_reason: str
    unchanged_restaurants: int
    added_restaurants: int
    removed_restaurants: int
    award_label_changes: int
    other_changed_rows: int
    award_categories_changed: tuple[str, ...]
    award_label_transition_counts: dict[str, int]
    added_selected_restaurants: int


@dataclass(frozen=True)
class FranceAcceptance:
    accepted: bool
    reason: str


@dataclass(frozen=True)
class Stage1AcquisitionResult:
    year: int
    previous_year: int
    source_rows: int
    source: SourceInfo | None
    comparison: FranceComparison | None
    acceptance: FranceAcceptance | None
    validation: dict
    paths: dict[str, Path]
    raw_path: Path | None
    status: str
    message: str


EXPECTED_COMPARISON_COLUMNS = (
    "name", "address", "city", "country", "price", "cuisine", "url",
    "award", "stars", "longitude", "latitude",
)
AWARD_COMPARISON_COLUMNS = ("award", "stars")
NON_AWARD_COMPARISON_COLUMNS = ("name", "address", "city", "price", "cuisine")
MINIMUM_MATCH_RATE = 0.80
MAXIMUM_AMBIGUOUS_MATCH_RATE = 0.05
MAXIMUM_DUPLICATE_CONFLICT_RATE = 0.02
MAXIMUM_NON_AWARD_CHANGE_RATE = 0.50


def latest_accepted_france_year(partition_root: Path) -> int:
    france_root = partition_root / "france"
    years: list[int] = []
    if france_root.is_dir():
        for path in france_root.glob("france_*.csv"):
            match = re.fullmatch(r"france_(\d{4})\.csv", path.name)
            if match:
                years.append(int(match.group(1)))
    if not years:
        raise FileNotFoundError(f"No accepted France partitions found in {france_root}")
    return max(years)


def acquisition_eligible_on(candidate_year: int, today: date) -> bool:
    return today >= date(candidate_year, 4, 1)


def candidate_paths_exist(
    *,
    year: int,
    raw_root: Path,
    output_root: Path,
) -> dict[str, Path]:
    paths = {"raw": raw_root / f"michelin_data_{year}.csv"}
    paths.update(partition_paths(year, output_root))
    return {name: path for name, path in paths.items() if path.exists()}


def fetch_upstream_revision(
    *,
    session=requests,
    commits_url: str = UPSTREAM_COMMITS_URL,
    timeout: float = 30.0,
) -> str | None:
    response = session.get(
        commits_url,
        params={"path": UPSTREAM_PATH, "sha": "main", "per_page": 1},
        timeout=timeout,
    )
    response.raise_for_status()
    commits = response.json()
    if not commits:
        return None
    sha = commits[0].get("sha")
    return str(sha) if sha else None


def download_upstream_snapshot(
    destination: Path,
    *,
    session=requests,
    source_url: str = UPSTREAM_CSV_URL,
    timeout: float = 60.0,
) -> SourceInfo:
    revision = fetch_upstream_revision(session=session, timeout=timeout)
    response = session.get(source_url, timeout=timeout)
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as output:
        output.write(response.content)
    return SourceInfo(url=source_url, revision=revision)


def compare_france_partitions(
    previous: pd.DataFrame,
    candidate: pd.DataFrame,
) -> FranceComparison:
    previous_columns = set(previous.columns)
    candidate_columns = set(candidate.columns)
    added_columns = tuple(sorted(candidate_columns - previous_columns))
    removed_columns = tuple(sorted(previous_columns - candidate_columns))
    shared_columns = tuple(sorted(previous_columns & candidate_columns))
    missing_expected = tuple(
        column for column in EXPECTED_COMPARISON_COLUMNS
        if column not in previous_columns or column not in candidate_columns
    )

    previous_matched = prepare_matching_frame(previous)
    candidate_matched = prepare_matching_frame(candidate)
    reconciliation = reconcile_restaurants(previous_matched, candidate_matched)

    award_change_count = 0
    other_change_count = 0
    unchanged_count = 0
    categories: set[str] = set()
    transition_counts: dict[str, int] = {}

    for match in reconciliation.matches:
        previous_row = previous_matched.loc[match.previous_index]
        candidate_row = candidate_matched.loc[match.current_index]
        award_changed = any(
            previous_row[column] != candidate_row[column]
            for column in AWARD_COMPARISON_COLUMNS
            if column in previous_row.index and column in candidate_row.index
        )
        if award_changed:
            award_change_count += 1
            previous_award = str(previous_row["award"])
            candidate_award = str(candidate_row["award"])
            categories.update((previous_award, candidate_award))
            transition = f"{previous_award} -> {candidate_award}"
            transition_counts[transition] = transition_counts.get(transition, 0) + 1
            continue

        non_award_changed = any(
            normalized_text(previous_row[column]) != normalized_text(candidate_row[column])
            for column in NON_AWARD_COMPARISON_COLUMNS
            if column in previous_row.index and column in candidate_row.index
        )
        if non_award_changed:
            other_change_count += 1
        else:
            unchanged_count += 1

    matched_count = len(reconciliation.matches)
    match_denominator = max(len(previous), len(candidate), 1)
    match_rate = matched_count / match_denominator
    ambiguous_count = len(reconciliation.fuzzy_candidates)
    ambiguous_rate = ambiguous_count / match_denominator
    duplicate_rate = reconciliation.duplicate_conflicts / match_denominator
    non_award_change_rate = other_change_count / max(matched_count, 1)
    reliable = True
    reliability_reason = "Comparison quality checks passed."
    if missing_expected:
        reliable = False
        reliability_reason = (
            "Expected comparison columns are missing: "
            + ", ".join(missing_expected)
        )
    elif match_rate < MINIMUM_MATCH_RATE:
        reliable = False
        reliability_reason = (
            f"Restaurant match rate {match_rate:.1%} is below "
            f"the {MINIMUM_MATCH_RATE:.0%} reliability floor."
        )
    elif ambiguous_rate > MAXIMUM_AMBIGUOUS_MATCH_RATE:
        reliable = False
        reliability_reason = (
            f"Ambiguous candidate rate {ambiguous_rate:.1%} is above "
            f"the {MAXIMUM_AMBIGUOUS_MATCH_RATE:.0%} reliability ceiling."
        )
    elif duplicate_rate > MAXIMUM_DUPLICATE_CONFLICT_RATE:
        reliable = False
        reliability_reason = (
            f"Unresolved duplicate-key conflict rate {duplicate_rate:.1%} "
            f"is above the {MAXIMUM_DUPLICATE_CONFLICT_RATE:.0%} reliability ceiling."
        )
    elif non_award_change_rate > MAXIMUM_NON_AWARD_CHANGE_RATE:
        reliable = False
        reliability_reason = (
            f"Non-award descriptive change rate {non_award_change_rate:.1%} "
            f"is above the {MAXIMUM_NON_AWARD_CHANGE_RATE:.0%} reliability ceiling."
        )

    candidate_unmatched = candidate_matched.loc[list(reconciliation.current_unmatched)]
    added_selected = int(
        candidate_unmatched["award"].eq("Selected Restaurants").sum()
        if not candidate_unmatched.empty
        else 0
    )
    return FranceComparison(
        previous_rows=len(previous),
        candidate_rows=len(candidate),
        added_columns=added_columns,
        removed_columns=removed_columns,
        shared_columns=shared_columns,
        missing_expected_columns=missing_expected,
        matched_restaurants=matched_count,
        match_rate=match_rate,
        ambiguous_matches=ambiguous_count,
        duplicate_conflicts=reconciliation.duplicate_conflicts,
        comparison_reliable=reliable,
        reliability_reason=reliability_reason,
        unchanged_restaurants=unchanged_count,
        added_restaurants=len(reconciliation.current_unmatched),
        removed_restaurants=len(reconciliation.previous_unmatched),
        award_label_changes=award_change_count,
        other_changed_rows=other_change_count,
        award_categories_changed=tuple(sorted(categories)),
        award_label_transition_counts=dict(sorted(transition_counts.items())),
        added_selected_restaurants=added_selected,
    )


def evaluate_france_acceptance(comparison: FranceComparison) -> FranceAcceptance:
    if not comparison.comparison_reliable:
        return FranceAcceptance(False, comparison.reliability_reason)

    material_changes = (
        comparison.added_restaurants
        + comparison.removed_restaurants
        + comparison.award_label_changes
    )
    selected_only_additions = (
        comparison.added_restaurants > 0
        and comparison.added_selected_restaurants == comparison.added_restaurants
        and comparison.removed_restaurants == 0
        and comparison.award_label_changes == 0
    )
    if selected_only_additions:
        return FranceAcceptance(
            False,
            "The candidate only adds Selected Restaurants and does not show broader award-label movement.",
        )
    if material_changes < 50:
        return FranceAcceptance(
            False,
            "The candidate has fewer than 50 material France restaurant changes.",
        )
    if comparison.award_label_changes < 10:
        return FranceAcceptance(
            False,
            "The candidate has fewer than 10 Michelin award-label changes.",
        )
    if len(comparison.award_categories_changed) < 2:
        return FranceAcceptance(
            False,
            "The candidate award-label movement affects fewer than two Michelin award categories.",
        )
    return FranceAcceptance(
        True,
        "The candidate shows broad France restaurant and Michelin award-label movement.",
    )


def publish_raw_snapshot(
    source_path: Path,
    *,
    year: int,
    raw_root: Path,
) -> Path:
    final = raw_root / f"michelin_data_{year}.csv"
    if final.exists():
        raise FileExistsError(f"Refusing to replace existing raw snapshot: {final}")
    final.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".stage1-raw-{year}-", dir=raw_root) as temporary:
        staged = Path(temporary) / final.name
        shutil.copy2(source_path, staged)
        pd.read_csv(staged)
        os.replace(staged, final)
    return final


def publish_raw_and_partitions(
    source_path: Path,
    partitions: dict[str, pd.DataFrame],
    *,
    year: int,
    raw_root: Path,
    output_root: Path,
) -> tuple[Path, dict[str, Path]]:
    raw_final = raw_root / f"michelin_data_{year}.csv"
    partition_final = partition_paths(year, output_root)
    existing = [raw_final, *partition_final.values()]
    present = [path for path in existing if path.exists()]
    if present:
        raise FileExistsError(
            "Refusing to replace existing Stage 1 accepted assets: "
            + ", ".join(str(path) for path in present)
        )

    output_root.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".stage1-accept-{year}-", dir=output_root))
    raw_published = False
    partition_paths_written: dict[str, Path] = {}
    try:
        staged_partitions = _write_staged_partitions(
            partitions,
            year=year,
            staging_root=staging_root / "candidate_partitions",
        )
        raw_path = publish_raw_snapshot(source_path, year=year, raw_root=raw_root)
        raw_published = True
        for country in ("france", "monaco", "uk"):
            final = partition_final[country]
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_partitions[country], final)
            partition_paths_written[country] = final
    except Exception:
        for path in partition_paths_written.values():
            path.unlink(missing_ok=True)
        if raw_published:
            raw_final.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    return raw_final, partition_final


def run_stage1_acquisition(
    *,
    raw_root: Path = Path("data/raw/michelin"),
    output_root: Path = Path("data/partitions"),
    today: date | None = None,
    downloader=download_upstream_snapshot,
) -> Stage1AcquisitionResult:
    today = today or date.today()
    previous_year = latest_accepted_france_year(output_root)
    candidate_year = previous_year + 1

    if not acquisition_eligible_on(candidate_year, today):
        return Stage1AcquisitionResult(
            year=candidate_year,
            previous_year=previous_year,
            source_rows=0,
            source=None,
            comparison=None,
            acceptance=None,
            validation={},
            paths={},
            raw_path=None,
            status="not-yet",
            message=(
                f"Nothing to do yet: candidate {candidate_year} acquisition "
                f"opens on {candidate_year}-04-01."
            ),
        )

    existing = candidate_paths_exist(
        year=candidate_year,
        raw_root=raw_root,
        output_root=output_root,
    )
    if existing:
        return Stage1AcquisitionResult(
            year=candidate_year,
            previous_year=previous_year,
            source_rows=0,
            source=None,
            comparison=None,
            acceptance=None,
            validation={},
            paths={},
            raw_path=None,
            status="already-exists",
            message=(
                "Candidate-year Stage 1 assets already exist; normal acquisition "
                "will not replace accepted annual data: "
                + ", ".join(str(path) for path in existing.values())
            ),
        )

    with tempfile.TemporaryDirectory(prefix=f"stage1-acquire-{candidate_year}-") as temporary:
        workspace = Path(temporary)
        candidate_source = workspace / f"michelin_data_{candidate_year}.csv"
        source = downloader(candidate_source)
        raw = pd.read_csv(candidate_source)
        partitions, validation = prepare_partitions(raw, year=candidate_year)
        previous_france = pd.read_csv(output_root / "france" / f"france_{previous_year}.csv")
        comparison = compare_france_partitions(previous_france, partitions["france"])
        acceptance = evaluate_france_acceptance(comparison)
        if not acceptance.accepted:
            return Stage1AcquisitionResult(
                year=candidate_year,
                previous_year=previous_year,
                source_rows=len(raw),
                source=source,
                comparison=comparison,
                acceptance=acceptance,
                validation=validation,
                paths={},
                raw_path=None,
                status="rejected" if comparison.comparison_reliable else "comparison-unreliable",
                message=(
                    "Candidate France snapshot rejected."
                    if comparison.comparison_reliable
                    else "The candidate could not be compared reliably with the accepted France partition."
                ),
            )

        raw_path, paths = publish_raw_and_partitions(
            candidate_source,
            partitions,
            year=candidate_year,
            raw_root=raw_root,
            output_root=output_root,
        )
        return Stage1AcquisitionResult(
            year=candidate_year,
            previous_year=previous_year,
            source_rows=len(raw),
            source=source,
            comparison=comparison,
            acceptance=acceptance,
            validation=validation,
            paths=paths,
            raw_path=raw_path,
            status="accepted",
            message="Candidate France snapshot accepted.",
        )
