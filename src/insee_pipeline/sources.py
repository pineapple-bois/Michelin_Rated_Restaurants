"""Source acquisition and immutable raw-artifact helpers."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import shutil

import requests


MELODI_BASE_URL = "https://api.insee.fr/melodi"

INSEE_DATASETS = {
    "wages": "DS_BTS_SAL_EQTP_SEX_AGE",
    "filosofi": "DS_FILOSOFI_CC",
    "unemployment": "DS_RP_EMPLOI_LR_COMP",
    "population": "DS_POPULATIONS_REFERENCE",
}

OECD_GDP_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.CFE.EDS,DSD_REG_ECO@DF_GDP,2.4/all"
)


@dataclass(frozen=True)
class SourceArtifact:
    provider: str
    source_id: str
    url: str
    cache_path: Path
    sha256: str
    bytes: int
    fetched: bool
    observed_at: str
    retention_policy: str = "disposable build cache; safe to delete and regenerate"

    def to_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["cache_path"] = str(self.cache_path)
        return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def melodi_csv_url(dataset_id: str, year: int) -> str:
    return f"{MELODI_BASE_URL}/file/{dataset_id}/{dataset_id}_{year}_CSV_FR"


def _cache_candidates(cache_root: Path | None, destination: Path) -> list[Path]:
    if cache_root is None:
        return []
    return [
        cache_root / destination.name,
        cache_root / destination.stem / destination.name,
    ]


def ensure_artifact(
    *,
    provider: str,
    source_id: str,
    url: str,
    destination: Path,
    cache_root: Path | None = None,
    timeout: tuple[float, float] = (30.0, 300.0),
    params: dict[str, str] | None = None,
) -> SourceArtifact:
    """Ensure one disposable source artifact, using a local cache before network."""

    fetched = False
    if destination.exists():
        pass
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        for candidate in _cache_candidates(cache_root, destination):
            if candidate.is_file():
                shutil.copy2(candidate, destination)
                break
        else:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            destination.write_bytes(response.content)
            fetched = True

    return SourceArtifact(
        provider=provider,
        source_id=source_id,
        url=_url_with_params(url, params),
        cache_path=destination,
        sha256=sha256_file(destination),
        bytes=destination.stat().st_size,
        fetched=fetched,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


def _url_with_params(url: str, params: dict[str, str] | None) -> str:
    if not params:
        return url
    prepared = requests.Request("GET", url, params=params).prepare()
    return prepared.url or url


def acquire_sources(paths) -> list[SourceArtifact]:
    artifacts: list[SourceArtifact] = []
    for source_id in INSEE_DATASETS.values():
        artifacts.append(
            ensure_artifact(
                provider="INSEE Melodi",
                source_id=source_id,
                url=melodi_csv_url(source_id, paths.year),
                destination=paths.insee_zip(source_id),
                cache_root=paths.source_cache_root,
            )
        )
    artifacts.append(
        ensure_artifact(
            provider="OECD SDMX",
            source_id="OECD.CFE.EDS:DSD_REG_ECO@DF_GDP(2.4)",
            url=OECD_GDP_URL,
            destination=paths.oecd_gdp_csv,
            cache_root=paths.source_cache_root,
            params={
                "startPeriod": str(paths.year),
                "endPeriod": str(paths.year),
                "dimensionAtObservation": "AllDimensions",
                "format": "csvfilewithlabels",
            },
        )
    )
    return artifacts
