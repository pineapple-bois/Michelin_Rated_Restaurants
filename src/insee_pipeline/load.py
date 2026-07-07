"""Reusable loading helpers for INSEE ZIPs and OECD CSV files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zipfile

import pandas as pd


class SourceLoadError(ValueError):
    """Raised when a raw source cannot be loaded under the ETL contract."""


@dataclass(frozen=True)
class ZipMembers:
    data_name: str
    metadata_name: str


def inspect_zip(path: Path) -> ZipMembers:
    if not path.is_file():
        raise FileNotFoundError(path)
    if not zipfile.is_zipfile(path):
        raise SourceLoadError(f"Not a valid ZIP archive: {path}")
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
        if corrupt is not None:
            raise SourceLoadError(f"Corrupt ZIP member in {path}: {corrupt}")
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    data_names = [name for name in csv_names if name.lower().endswith("_data.csv")]
    metadata_names = [name for name in csv_names if name.lower().endswith("_metadata.csv")]
    if len(data_names) != 1:
        raise SourceLoadError(f"Expected one data CSV in {path}, found {data_names}")
    if len(metadata_names) != 1:
        raise SourceLoadError(f"Expected one metadata CSV in {path}, found {metadata_names}")
    return ZipMembers(data_names[0], metadata_names[0])


def load_departmental_zip_data(
    path: Path,
    *,
    chunksize: int = 50_000,
    department_only: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, ZipMembers]:
    members = inspect_zip(path)
    chunks: list[pd.DataFrame] = []
    with zipfile.ZipFile(path) as archive:
        with archive.open(members.data_name) as file:
            for chunk in pd.read_csv(file, sep=";", dtype=str, chunksize=chunksize, low_memory=False):
                if department_only:
                    if "GEO_OBJECT" not in chunk.columns:
                        raise SourceLoadError(f"{members.data_name} has no GEO_OBJECT column")
                    chunk = chunk.loc[chunk["GEO_OBJECT"].eq("DEP")].copy()
                chunks.append(chunk)
        with archive.open(members.metadata_name) as file:
            metadata = pd.read_csv(file, sep=";", dtype=str)
    if not chunks:
        raise SourceLoadError(f"No chunks loaded from {path}")
    data = pd.concat(chunks, ignore_index=True)
    if department_only and data.empty:
        raise SourceLoadError(f"No departmental rows found in {path}")
    return data, metadata, members


def require_columns(frame: pd.DataFrame, columns: tuple[str, ...], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise SourceLoadError(f"{label} is missing required columns: {missing}")


def to_numeric(series: pd.Series, label: str) -> pd.Series:
    try:
        return pd.to_numeric(series, errors="raise")
    except Exception as error:
        raise SourceLoadError(f"{label} contains non-numeric values") from error


def read_oecd_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_csv(path, dtype=str, low_memory=False)
