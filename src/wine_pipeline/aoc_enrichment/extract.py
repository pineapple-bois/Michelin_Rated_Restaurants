"""Extract the UC Davis regional wine polygon GeoJSON into the run directory."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..config import HTTP_TIMEOUT, UC_DAVIS_BRANCH_URL, UC_DAVIS_COMMITS_API_URL, UC_DAVIS_RAW_URL
from ..provenance import sha256_file


@dataclass(frozen=True)
class UCDavisSource:
    configured_branch_url: str
    configured_raw_url: str
    resolved_commit_sha: str | None
    download_url: str
    final_url: str
    retrieval_time_utc: str
    headers: dict[str, str]
    filename: str
    path: Path
    size_bytes: int
    sha256: str
    authority_warning: str = (
        "Non-authoritative, hand-drawn application-level wine-region boundaries derived "
        "from an ESRI map and used as the best available broad regional classification source."
    )

    def to_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        return payload


def _interesting_headers(headers: requests.structures.CaseInsensitiveDict[str]) -> dict[str, str]:
    wanted = ("ETag", "Last-Modified", "Content-Type")
    return {name: headers[name] for name in wanted if name in headers}


def resolve_uc_davis_commit(*, session: requests.Session | None = None, timeout: tuple[float, float] = HTTP_TIMEOUT) -> str | None:
    client = session or requests.Session()
    try:
        response = client.get(
            UC_DAVIS_COMMITS_API_URL,
            params={"sha": "master", "path": "examples/france/regions.geojson", "per_page": "1"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list) and payload:
            sha = payload[0].get("sha")
            return str(sha) if sha else None
    except requests.RequestException:
        return None
    return None


def _raw_url_for_commit(commit_sha: str | None) -> str:
    if commit_sha:
        return f"https://raw.githubusercontent.com/UCDavisLibrary/wine-ontology/{commit_sha}/examples/france/regions.geojson"
    return UC_DAVIS_RAW_URL


def download_uc_davis_regions(
    run_dir: Path,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = HTTP_TIMEOUT,
) -> UCDavisSource:
    client = session or requests.Session()
    commit_sha = resolve_uc_davis_commit(session=client, timeout=timeout)
    download_url = _raw_url_for_commit(commit_sha)
    destination = run_dir / "downloads" / "uc_davis" / "regions.geojson"
    destination.parent.mkdir(parents=True, exist_ok=True)
    response = client.get(download_url, stream=True, timeout=timeout)
    response.raise_for_status()
    with destination.open("wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                file.write(chunk)
    return UCDavisSource(
        configured_branch_url=UC_DAVIS_BRANCH_URL,
        configured_raw_url=UC_DAVIS_RAW_URL,
        resolved_commit_sha=commit_sha,
        download_url=download_url,
        final_url=response.url,
        retrieval_time_utc=datetime.now(timezone.utc).isoformat(),
        headers=_interesting_headers(response.headers),
        filename=destination.name,
        path=destination,
        size_bytes=destination.stat().st_size,
        sha256=sha256_file(destination),
    )

