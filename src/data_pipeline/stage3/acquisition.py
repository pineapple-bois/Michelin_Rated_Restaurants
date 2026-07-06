"""Acquisition of the small Paris arrondissement naming reference."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
import os
import tempfile
from urllib.request import Request, urlopen

import pandas as pd


SOURCE_URL = "https://en.wikipedia.org/wiki/Arrondissements_of_Paris"
USER_AGENT = "Michelin-Rated-Restaurants data pipeline/1.0"


class ParisReferenceError(ValueError):
    """Raised when the upstream Paris table no longer satisfies its contract."""


def _flatten_column(column: object) -> str:
    if isinstance(column, tuple):
        return " ".join(str(value) for value in column if str(value) != "nan").strip()
    return str(column).strip()


def normalize_paris_table(table: pd.DataFrame) -> pd.DataFrame:
    frame = table.copy()
    frame.columns = [_flatten_column(column) for column in frame.columns]
    arrondissement_column = next(
        (column for column in frame.columns if "Arrondissement" in column), None
    )
    name_column = next((column for column in frame.columns if column == "Name"), None)
    if arrondissement_column is None or name_column is None:
        raise ParisReferenceError("Table lacks expected arrondissement and name columns")
    numbers = frame[arrondissement_column].astype(str).str.extract(r"(\d{1,2})", expand=False)
    ordinals = frame[arrondissement_column].astype(str).str.extract(
        r"(\d{1,2}(?:st|nd|rd|th))", expand=False
    )
    result = pd.DataFrame({
        "arrondissement_number": pd.to_numeric(numbers, errors="coerce"),
        "ordinal": ordinals,
        "name": frame[name_column].astype(str).str.strip(),
    }).dropna()
    result["arrondissement_number"] = result["arrondissement_number"].astype(int)
    result = result.sort_values("arrondissement_number").reset_index(drop=True)
    expected = set(range(1, 21))
    if (
        len(result) != 20
        or set(result["arrondissement_number"]) != expected
        or result["arrondissement_number"].duplicated().any()
        or result[["ordinal", "name"]].eq("").any().any()
    ):
        raise ParisReferenceError(
            "Paris reference must contain exactly 20 unique arrondissements numbered 1 through 20"
        )
    return result


def extract_paris_reference(
    *,
    output_path: Path = Path("data/raw/demographics/paris_arrondissements.csv"),
    refresh: bool = False,
    timeout: float = 30.0,
) -> Path:
    if output_path.exists() and not refresh:
        raise FileExistsError(
            f"Refusing to overwrite accepted Paris reference without --refresh: {output_path}"
        )
    request = Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        html = response.read().decode(response.headers.get_content_charset() or "utf-8")
    candidates: list[pd.DataFrame] = []
    for table in pd.read_html(StringIO(html)):
        try:
            candidates.append(normalize_paris_table(table))
        except ParisReferenceError:
            continue
    if len(candidates) != 1:
        raise ParisReferenceError(
            f"Expected exactly one matching Paris arrondissement table, found {len(candidates)}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    os.close(handle)
    temporary_path = Path(temporary_name)
    try:
        candidates[0].to_csv(temporary_path, index=False, lineterminator="\n")
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_path
