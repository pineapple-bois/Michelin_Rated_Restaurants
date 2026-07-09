"""Shared deterministic restaurant reconciliation for Michelin guide data."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import math
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import pandas as pd


EXACT_MATCH_METHODS = (
    (("normalized_url", "postal_code"), "exact_url_postal"),
    (("normalized_url",), "exact_url"),
    (("normalized_name", "postal_code"), "exact_name_postal"),
    (("normalized_address", "postal_code"), "exact_address_postal"),
    (("normalized_address",), "exact_address"),
    (("normalized_name", "latitude", "longitude"), "exact_name_coordinates"),
)


@dataclass(frozen=True)
class RestaurantMatch:
    previous_index: int
    current_index: int
    method: str
    confidence: float
    evidence: str


@dataclass(frozen=True)
class FuzzyMatchCandidate:
    previous_index: int
    current_index: int
    confidence: float
    evidence: str


@dataclass(frozen=True)
class RestaurantReconciliation:
    matches: tuple[RestaurantMatch, ...]
    previous_unmatched: tuple[int, ...]
    current_unmatched: tuple[int, ...]
    fuzzy_candidates: tuple[FuzzyMatchCandidate, ...]
    duplicate_conflicts: int


def normalized_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value).casefold())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalized_url(value: object) -> str:
    if pd.isna(value) or not str(value).strip():
        return ""
    parsed = urlsplit(str(value).strip())
    host = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.casefold(), host, path, "", ""))


def postal_code(value: object) -> str:
    match = re.search(r"\b\d{5}\b", "" if pd.isna(value) else str(value))
    return match.group(0) if match else ""


def distance_km(previous: pd.Series, current: pd.Series) -> float:
    lat1, lon1 = math.radians(previous["latitude"]), math.radians(previous["longitude"])
    lat2, lon2 = math.radians(current["latitude"]), math.radians(current["longitude"])
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def prepare_matching_frame(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    if "source_row" not in prepared.columns:
        prepared["source_row"] = range(len(prepared))
    prepared["normalized_name"] = prepared["name"].map(normalized_text)
    prepared["normalized_address"] = prepared["address"].map(normalized_text)
    prepared["normalized_url"] = prepared.get("url", pd.Series("", index=prepared.index)).map(normalized_url)
    postal_source = (
        prepared["location"]
        if "location" in prepared.columns
        else prepared["address"]
    )
    prepared["postal_code"] = postal_source.map(postal_code)
    return prepared


def duplicate_conflict_count(
    previous: pd.DataFrame,
    current: pd.DataFrame,
    previous_available: set[int],
    current_available: set[int],
    columns: tuple[str, ...],
) -> int:
    if not previous_available or not current_available:
        return 0
    old_values = previous.loc[sorted(previous_available), list(columns)].fillna("").astype(str)
    new_values = current.loc[sorted(current_available), list(columns)].fillna("").astype(str)
    old = old_values[old_values.ne("").all(axis=1)].apply(
        lambda row: "|".join(row.tolist()), axis=1
    )
    new = new_values[new_values.ne("").all(axis=1)].apply(
        lambda row: "|".join(row.tolist()), axis=1
    )
    old_counts, new_counts = old.value_counts(), new.value_counts()
    conflicts = 0
    for value in sorted(set(old_counts.index) & set(new_counts.index)):
        if old_counts[value] != 1 or new_counts[value] != 1:
            conflicts += int(old_counts[value] + new_counts[value])
    return conflicts


def unique_matches(
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


def fuzzy_candidates(
    previous: pd.DataFrame,
    current: pd.DataFrame,
    previous_available: set[int],
    current_available: set[int],
) -> tuple[FuzzyMatchCandidate, ...]:
    candidates: list[FuzzyMatchCandidate] = []
    for current_index in sorted(current_available):
        new = current.loc[current_index]
        scored: list[tuple[float, float, int, str]] = []
        for previous_index in sorted(previous_available):
            old = previous.loc[previous_index]
            distance = distance_km(old, new)
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
            candidates.append(FuzzyMatchCandidate(previous_index, current_index, score, evidence))
    return tuple(candidates)


def reconcile_restaurants(
    previous: pd.DataFrame,
    current: pd.DataFrame,
    *,
    precomputed_matches: tuple[RestaurantMatch, ...] = (),
) -> RestaurantReconciliation:
    previous_available = set(previous.index)
    current_available = set(current.index)
    matches: list[RestaurantMatch] = []

    for match in precomputed_matches:
        if match.previous_index not in previous_available or match.current_index not in current_available:
            raise ValueError("Precomputed matches assign a restaurant more than once")
        matches.append(match)
        previous_available.remove(match.previous_index)
        current_available.remove(match.current_index)

    for columns, method in EXACT_MATCH_METHODS:
        for old, new in unique_matches(previous, current, previous_available, current_available, columns):
            matches.append(RestaurantMatch(old, new, method, 1.0, "+".join(columns)))
            previous_available.remove(old)
            current_available.remove(new)

    for old, new in unique_matches(
        previous, current, previous_available, current_available, ("normalized_name",)
    ):
        distance = distance_km(previous.loc[old], current.loc[new])
        if distance <= 5.0:
            matches.append(RestaurantMatch(
                old, new, "exact_name_nearby", 1.0,
                f"unique normalized name;distance_km={distance:.3f}",
            ))
            previous_available.remove(old)
            current_available.remove(new)

    duplicate_conflicts = 0
    for columns, _method in (*EXACT_MATCH_METHODS, (("normalized_name",), "exact_name_nearby")):
        duplicate_conflicts += duplicate_conflict_count(
            previous, current, previous_available, current_available, columns
        )

    return RestaurantReconciliation(
        matches=tuple(matches),
        previous_unmatched=tuple(sorted(previous_available)),
        current_unmatched=tuple(sorted(current_available)),
        fuzzy_candidates=fuzzy_candidates(previous, current, previous_available, current_available),
        duplicate_conflicts=duplicate_conflicts,
    )
