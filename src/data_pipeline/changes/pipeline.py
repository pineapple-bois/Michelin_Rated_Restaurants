"""Deterministic France Michelin Guide changes reports."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
from pandas.testing import assert_frame_equal


REQUIRED_COLUMNS = (
    "name", "address", "location", "department", "region", "award", "stars",
    "longitude", "latitude",
)
OUTPUT_COLUMNS = (
    "record_type", "previous_year", "current_year", "previous_row", "current_row",
    "previous_name", "current_name", "previous_address", "current_address",
    "previous_location", "current_location", "department", "region",
    "previous_award", "current_award", "previous_stars", "current_stars",
    "previous_greenstar", "current_greenstar", "change_types", "material",
    "matching_method", "matching_confidence", "review_status", "match_evidence",
)


class ChangesValidationError(ValueError):
    """Raised when input, identity, or reconciliation contracts fail."""


class ChangesPublicationError(RuntimeError):
    """Raised when a validated report set cannot be published atomically."""


@dataclass(frozen=True)
class ChangesValidation:
    previous_rows: int
    current_rows: int
    matched_rows: int
    new_entries: int
    removed_entries: int
    exact_matches: int
    overridden_matches: int
    fuzzy_candidates: int
    unresolved_starred: int


@dataclass(frozen=True)
class ChangesResult:
    previous_year: int
    current_year: int
    changes: pd.DataFrame
    report: str
    summary: dict[str, object]
    validation: ChangesValidation
    paths: dict[str, Path]


def annual_product_path(year: int, product_root: Path) -> Path:
    historical = product_root / "france" / f"all_restaurants(arrondissements)_{year % 100:02d}.csv"
    current = product_root / "france" / str(year) / "all_restaurants(arrondissements).csv"
    if historical.is_file():
        return historical
    return current


def report_paths(previous_year: int, current_year: int, output_root: Path) -> dict[str, Path]:
    stem = f"changes_{previous_year}_{current_year}"
    root = output_root / "france"
    return {kind: root / f"{stem}.{kind}" for kind in ("csv", "json", "md")}


def _normalized_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value).casefold())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _normalized_url(value: object) -> str:
    if pd.isna(value) or not str(value).strip():
        return ""
    parsed = urlsplit(str(value).strip())
    host = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.casefold(), host, path, "", ""))


def _postal_code(value: object) -> str:
    match = re.search(r"\b\d{5}\b", "" if pd.isna(value) else str(value))
    return match.group(0) if match else ""


def _distance_km(previous: pd.Series, current: pd.Series) -> float:
    lat1, lon1 = math.radians(previous["latitude"]), math.radians(previous["longitude"])
    lat2, lon2 = math.radians(current["latitude"]), math.radians(current["longitude"])
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _load_product(path: Path, year: int) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Annual France product does not exist: {path}")
    frame = pd.read_csv(path)
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ChangesValidationError(f"{year} product is missing required columns: {missing}")
    if frame[list(REQUIRED_COLUMNS)].isna().any().any():
        nulls = frame[list(REQUIRED_COLUMNS)].isna().sum()
        # URL is deliberately not required; every listed identity/classification field is.
        raise ChangesValidationError(f"{year} product contains required nulls: {nulls[nulls > 0].to_dict()}")
    if frame.duplicated().any():
        raise ChangesValidationError(f"{year} product contains exact duplicate rows")
    frame = frame.copy()
    frame["source_row"] = range(len(frame))
    frame["normalized_name"] = frame["name"].map(_normalized_text)
    frame["normalized_address"] = frame["address"].map(_normalized_text)
    frame["normalized_url"] = frame.get("url", pd.Series("", index=frame.index)).map(_normalized_url)
    frame["postal_code"] = frame["location"].map(_postal_code)
    return frame


def _unique_matches(
    previous: pd.DataFrame,
    current: pd.DataFrame,
    previous_available: set[int],
    current_available: set[int],
    columns: tuple[str, ...],
) -> list[tuple[int, int]]:
    if not previous_available or not current_available:
        return []
    old_values = previous.loc[sorted(previous_available), list(columns)].fillna("").astype(str)
    new_values = current.loc[sorted(current_available), list(columns)].fillna("").astype(str)
    old = old_values[old_values.ne("").all(axis=1)].apply(
        lambda row: "|".join(row.tolist()), axis=1
    )
    new = new_values[new_values.ne("").all(axis=1)].apply(
        lambda row: "|".join(row.tolist()), axis=1
    )
    old_counts, new_counts = old.value_counts(), new.value_counts()
    old_map = {value: index for index, value in old.items() if old_counts[value] == 1}
    new_map = {value: index for index, value in new.items() if new_counts[value] == 1}
    return [(old_map[value], new_map[value]) for value in sorted(set(old_map) & set(new_map))]


def _read_overrides(
    path: Path | None, previous_year: int, current_year: int,
    previous: pd.DataFrame, current: pd.DataFrame,
) -> list[tuple[int, int, str]]:
    if path is None or not path.exists():
        return []
    overrides = pd.read_csv(path)
    required = {"previous_year", "current_year", "previous_name", "current_name", "reason"}
    if not required.issubset(overrides.columns):
        raise ChangesValidationError(f"Override file is missing columns: {sorted(required - set(overrides.columns))}")
    overrides = overrides[
        overrides["previous_year"].eq(previous_year) & overrides["current_year"].eq(current_year)
    ]
    result: list[tuple[int, int, str]] = []
    for row in overrides.itertuples(index=False):
        old = previous.index[previous["name"].eq(row.previous_name)].tolist()
        new = current.index[current["name"].eq(row.current_name)].tolist()
        if len(old) != 1 or len(new) != 1:
            raise ChangesValidationError(
                f"Override names must identify one row: {row.previous_name!r} -> {row.current_name!r}"
            )
        result.append((old[0], new[0], str(row.reason)))
    return result


def _classification(stars: float) -> str:
    if stars >= 1:
        return "starred"
    if stars == 0.5:
        return "bib_gourmand"
    if stars == 0.25:
        return "selected"
    return "other"


def _optional_green(row: pd.Series) -> int | None:
    if "greenstar" not in row.index or pd.isna(row["greenstar"]):
        return None
    return int(row["greenstar"])


def _change_types(previous: pd.Series, current: pd.Series) -> list[str]:
    changes: list[str] = []
    old_stars, new_stars = float(previous["stars"]), float(current["stars"])
    if old_stars < 1 <= new_stars:
        changes.extend(("newly_starred", "promoted"))
    elif old_stars >= 1 and new_stars >= 1 and new_stars > old_stars:
        changes.append("promoted")
    elif old_stars >= 1 and new_stars < old_stars:
        changes.append("demoted")
    if _classification(old_stars) != _classification(new_stars):
        changes.append("classification_changed")
    if previous["normalized_name"] != current["normalized_name"]:
        changes.append("renamed")
    if (
        previous["normalized_address"] != current["normalized_address"]
        or previous["postal_code"] != current["postal_code"]
    ):
        changes.append("relocated")
    old_green, new_green = _optional_green(previous), _optional_green(current)
    if old_green is not None and new_green is not None:
        if old_green == 0 and new_green == 1:
            changes.append("green_star_gained")
        elif old_green == 1 and new_green == 0:
            changes.append("green_star_lost")
    return changes or ["unchanged"]


def _material(change_types: list[str], previous_stars: float | None, current_stars: float | None) -> bool:
    material_types = {
        "promoted", "demoted", "newly_starred", "green_star_gained", "green_star_lost",
    }
    if material_types.intersection(change_types):
        return True
    if "new_entry" in change_types and (current_stars or 0) >= 1:
        return True
    if "removed_from_guide" in change_types and (previous_stars or 0) >= 1:
        return True
    if "needs_review" in change_types and max(previous_stars or 0, current_stars or 0) >= 1:
        return True
    return False


def _record(
    *, previous_year: int, current_year: int,
    previous: pd.Series | None, current: pd.Series | None,
    record_type: str, changes: list[str], method: str,
    confidence: float, review_status: str, evidence: str,
) -> dict[str, object]:
    def value(row: pd.Series | None, column: str) -> object:
        return None if row is None or column not in row.index or pd.isna(row[column]) else row[column]
    previous_stars = value(previous, "stars")
    current_stars = value(current, "stars")
    return {
        "record_type": record_type,
        "previous_year": previous_year,
        "current_year": current_year,
        "previous_row": value(previous, "source_row"),
        "current_row": value(current, "source_row"),
        "previous_name": value(previous, "name"),
        "current_name": value(current, "name"),
        "previous_address": value(previous, "address"),
        "current_address": value(current, "address"),
        "previous_location": value(previous, "location"),
        "current_location": value(current, "location"),
        "department": value(current, "department") or value(previous, "department"),
        "region": value(current, "region") or value(previous, "region"),
        "previous_award": value(previous, "award"),
        "current_award": value(current, "award"),
        "previous_stars": previous_stars,
        "current_stars": current_stars,
        "previous_greenstar": value(previous, "greenstar"),
        "current_greenstar": value(current, "greenstar"),
        "change_types": "|".join(changes),
        "material": _material(changes, previous_stars, current_stars),
        "matching_method": method,
        "matching_confidence": round(confidence, 4),
        "review_status": review_status,
        "match_evidence": evidence,
    }


def _fuzzy_candidates(
    previous: pd.DataFrame, current: pd.DataFrame,
    previous_available: set[int], current_available: set[int],
    previous_year: int, current_year: int,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for current_index in sorted(current_available):
        new = current.loc[current_index]
        scored: list[tuple[float, float, int, str]] = []
        for previous_index in sorted(previous_available):
            old = previous.loc[previous_index]
            distance = _distance_km(old, new)
            same_postal = bool(old["postal_code"] and old["postal_code"] == new["postal_code"])
            same_address = bool(old["normalized_address"] and old["normalized_address"] == new["normalized_address"])
            if not (same_postal or same_address or distance <= 2.0):
                continue
            score = SequenceMatcher(None, old["normalized_name"], new["normalized_name"]).ratio()
            if score < 0.72:
                continue
            evidence = f"name_similarity={score:.3f};distance_km={distance:.3f};same_postal={same_postal};same_address={same_address}"
            scored.append((score, -distance, previous_index, evidence))
        for score, _negative_distance, previous_index, evidence in sorted(scored, reverse=True)[:3]:
            candidates.append(_record(
                previous_year=previous_year, current_year=current_year,
                previous=previous.loc[previous_index], current=new,
                record_type="match_candidate", changes=["needs_review"],
                method="fuzzy_candidate", confidence=score,
                review_status="needs_review", evidence=evidence,
            ))
    return candidates


def compare_products(
    previous: pd.DataFrame, current: pd.DataFrame,
    *, previous_year: int, current_year: int, overrides_path: Path | None,
) -> tuple[pd.DataFrame, ChangesValidation]:
    previous_available, current_available = set(previous.index), set(current.index)
    matches: list[tuple[int, int, str, float, str]] = []

    for old, new, reason in _read_overrides(
        overrides_path, previous_year, current_year, previous, current
    ):
        if old not in previous_available or new not in current_available:
            raise ChangesValidationError("Overrides assign a restaurant more than once")
        matches.append((old, new, "override", 1.0, reason))
        previous_available.remove(old)
        current_available.remove(new)

    exact_methods = (
        (("normalized_url", "postal_code"), "exact_url_postal"),
        (("normalized_url",), "exact_url"),
        (("normalized_name", "postal_code"), "exact_name_postal"),
        (("normalized_address", "postal_code"), "exact_address_postal"),
        (("normalized_address",), "exact_address"),
        (("normalized_name", "latitude", "longitude"), "exact_name_coordinates"),
    )
    for columns, method in exact_methods:
        for old, new in _unique_matches(previous, current, previous_available, current_available, columns):
            matches.append((old, new, method, 1.0, "+".join(columns)))
            previous_available.remove(old)
            current_available.remove(new)

    for old, new in _unique_matches(
        previous, current, previous_available, current_available, ("normalized_name",)
    ):
        distance = _distance_km(previous.loc[old], current.loc[new])
        if distance <= 5.0:
            matches.append((old, new, "exact_name_nearby", 1.0, f"unique normalized name;distance_km={distance:.3f}"))
            previous_available.remove(old)
            current_available.remove(new)

    records: list[dict[str, object]] = []
    for old, new, method, confidence, evidence in sorted(matches):
        changes = _change_types(previous.loc[old], current.loc[new])
        records.append(_record(
            previous_year=previous_year, current_year=current_year,
            previous=previous.loc[old], current=current.loc[new], record_type="comparison",
            changes=changes, method=method, confidence=confidence,
            review_status="reviewed" if method == "override" else "not_required", evidence=evidence,
        ))
    for index in sorted(previous_available):
        records.append(_record(
            previous_year=previous_year, current_year=current_year,
            previous=previous.loc[index], current=None, record_type="comparison",
            changes=["removed_from_guide"], method="unmatched", confidence=0.0,
            review_status="unmatched", evidence="no deterministic match",
        ))
    for index in sorted(current_available):
        records.append(_record(
            previous_year=previous_year, current_year=current_year,
            previous=None, current=current.loc[index], record_type="comparison",
            changes=["new_entry"], method="unmatched", confidence=0.0,
            review_status="unmatched", evidence="no deterministic match",
        ))
    fuzzy = _fuzzy_candidates(
        previous, current, previous_available, current_available, previous_year, current_year
    )
    records.extend(fuzzy)
    changes = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    changes.sort_values(
        ["record_type", "material", "change_types", "current_name", "previous_name"],
        ascending=[True, False, True, True, True], na_position="last", inplace=True,
    )
    changes.reset_index(drop=True, inplace=True)

    primary = changes[changes["record_type"].eq("comparison")]
    matched = primary[primary["previous_row"].notna() & primary["current_row"].notna()]
    new = primary[primary["previous_row"].isna()]
    removed = primary[primary["current_row"].isna()]
    if len(matched) + len(removed) != len(previous) or len(matched) + len(new) != len(current):
        raise ChangesValidationError("Comparison accounting does not reconcile to annual inputs")
    if matched["previous_row"].duplicated().any() or matched["current_row"].duplicated().any():
        raise ChangesValidationError("A deterministic match assigned an annual row more than once")
    unresolved_starred = int(changes[
        changes["change_types"].str.contains("needs_review") & changes["material"]
    ].shape[0])
    validation = ChangesValidation(
        previous_rows=len(previous), current_rows=len(current), matched_rows=len(matched),
        new_entries=len(new), removed_entries=len(removed),
        exact_matches=int(matched["matching_method"].ne("override").sum()),
        overridden_matches=int(matched["matching_method"].eq("override").sum()),
        fuzzy_candidates=len(fuzzy), unresolved_starred=unresolved_starred,
    )
    return changes, validation


def _classification_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {
        "selected": int(frame["stars"].eq(0.25).sum()),
        "bib_gourmand": int(frame["stars"].eq(0.5).sum()),
        "one_star": int(frame["stars"].eq(1).sum()),
        "two_star": int(frame["stars"].eq(2).sum()),
        "three_star": int(frame["stars"].eq(3).sum()),
    }


def _summary(
    previous: pd.DataFrame, current: pd.DataFrame, changes: pd.DataFrame,
    validation: ChangesValidation,
) -> dict[str, object]:
    primary = changes[changes["record_type"].eq("comparison")]
    count = lambda label: int(primary["change_types"].str.split("|").map(lambda values: label in values).sum())
    return {
        "previous_entries": len(previous), "current_entries": len(current),
        "previous_classifications": _classification_counts(previous),
        "current_classifications": _classification_counts(current),
        "new_entries": validation.new_entries, "removed_from_guide": validation.removed_entries,
        "promotions": count("promoted"), "demotions": count("demoted"),
        "newly_starred": count("newly_starred"),
        "green_star_gained": count("green_star_gained"),
        "green_star_lost": count("green_star_lost"),
        "fuzzy_candidates": validation.fuzzy_candidates,
        "unresolved_starred_candidates": validation.unresolved_starred,
    }


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_None._\n"
    columns = ["previous_name", "current_name", "previous_location", "current_location", "previous_stars", "current_stars", "matching_method"]
    display = frame.loc[:, columns].fillna("—")
    header = "| " + " | ".join(columns) + " |"
    separator = "|" + "|".join("---" for _ in columns) + "|"
    rows = ["| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |" for row in display.itertuples(index=False, name=None)]
    return "\n".join([header, separator, *rows]) + "\n"


def render_report(previous_year: int, current_year: int, changes: pd.DataFrame, summary: dict[str, object]) -> str:
    primary = changes[changes["record_type"].eq("comparison")]
    lines = [
        f"# France Michelin Guide changes: {previous_year} to {current_year}", "",
        "Disappearance from this dataset is reported as `removed_from_guide`; it does not establish closure.", "",
        "## Summary", "",
        f"- Guide entries: {summary['previous_entries']} -> {summary['current_entries']}",
        f"- New entries: {summary['new_entries']}",
        f"- Removed from guide: {summary['removed_from_guide']}",
        f"- Promotions: {summary['promotions']}; demotions: {summary['demotions']}; newly starred: {summary['newly_starred']}",
        f"- Green Star gains: {summary['green_star_gained']}; losses: {summary['green_star_lost']}",
        f"- Fuzzy review candidates: {summary['fuzzy_candidates']} ({summary['unresolved_starred_candidates']} involving starred records)", "",
        "## Classification totals", "",
        "| Classification | Previous | Current |", "|---|---:|---:|",
    ]
    previous_counts = summary["previous_classifications"]
    current_counts = summary["current_classifications"]
    for classification in ("selected", "bib_gourmand", "one_star", "two_star", "three_star"):
        lines.append(f"| {classification} | {previous_counts[classification]} | {current_counts[classification]} |")
    groups = (
        ("Promotions", "promoted"), ("Demotions", "demoted"),
        ("Newly starred", "newly_starred"), ("Starred new entries", "new_entry"),
        ("Starred removals", "removed_from_guide"),
        ("Green Star gains", "green_star_gained"), ("Green Star losses", "green_star_lost"),
    )
    for title, label in groups:
        subset = primary[primary["change_types"].str.split("|").map(lambda values: label in values)]
        if label == "new_entry":
            subset = subset[subset["current_stars"].fillna(0).ge(1)]
        if label == "removed_from_guide":
            subset = subset[subset["previous_stars"].fillna(0).ge(1)]
        lines.extend(("", f"## {title}", "", _markdown_table(subset).rstrip()))
    review = changes[changes["record_type"].eq("match_candidate") & changes["material"]]
    lines.extend(("", "## Unresolved starred identity candidates", "", _markdown_table(review).rstrip(), ""))
    return "\n".join(lines)


def validate_changes(
    *, previous_year: int, current_year: int,
    product_root: Path = Path("data/products"),
    overrides_path: Path | None = Path("data/overrides/france_change_matches.csv"),
) -> ChangesResult:
    if current_year != previous_year + 1:
        raise ChangesValidationError("Guide change comparisons must use consecutive years")
    previous = _load_product(annual_product_path(previous_year, product_root), previous_year)
    current = _load_product(annual_product_path(current_year, product_root), current_year)
    changes, validation = compare_products(
        previous, current, previous_year=previous_year, current_year=current_year,
        overrides_path=overrides_path,
    )
    summary = _summary(previous, current, changes, validation)
    report = render_report(previous_year, current_year, changes, summary)
    return ChangesResult(previous_year, current_year, changes, report, summary, validation, {})


def _write_reports(result: ChangesResult, root: Path) -> dict[str, Path]:
    paths = report_paths(result.previous_year, result.current_year, root)
    paths["csv"].parent.mkdir(parents=True, exist_ok=True)
    result.changes.to_csv(paths["csv"], index=False, lineterminator="\n")
    payload = {
        "summary": result.summary,
        "changes": json.loads(result.changes.to_json(orient="records")),
    }
    paths["json"].write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    paths["md"].write_text(result.report, encoding="utf-8")
    reloaded = pd.read_csv(paths["csv"])
    try:
        assert_frame_equal(
            result.changes.fillna(""), reloaded.fillna(""), check_dtype=False
        )
    except AssertionError as error:
        raise ChangesPublicationError("Serialized changes table failed reload validation") from error
    return paths


def run_changes(
    *, previous_year: int, current_year: int,
    product_root: Path = Path("data/products"), output_root: Path = Path("data/reports"),
    overrides_path: Path | None = Path("data/overrides/france_change_matches.csv"),
    replace: bool = False,
) -> ChangesResult:
    prepared = validate_changes(
        previous_year=previous_year, current_year=current_year,
        product_root=product_root, overrides_path=overrides_path,
    )
    final = report_paths(previous_year, current_year, output_root)
    existing = {name: path for name, path in final.items() if path.exists()}
    if existing and not replace:
        raise FileExistsError("Refusing to replace existing reports without --replace: " + ", ".join(map(str, existing.values())))
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".changes-{previous_year}-{current_year}-", dir=output_root))
    backups, published = staging / "backups", []
    try:
        staged = _write_reports(prepared, staging / "candidate")
        for name, path in existing.items():
            backup = backups / name / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        for name in ("csv", "json", "md"):
            final[name].parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged[name], final[name])
            published.append(name)
    except Exception as error:
        for name in reversed(published):
            backup = backups / name / final[name].name
            if backup.exists():
                os.replace(backup, final[name])
            else:
                final[name].unlink(missing_ok=True)
        raise ChangesPublicationError(f"Changes publication failed and was rolled back: {error}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return ChangesResult(previous_year, current_year, prepared.changes, prepared.report, prepared.summary, prepared.validation, final)
