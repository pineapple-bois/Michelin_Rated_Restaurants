"""Extract the INAO AOC parcel archive into the run directory."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

import requests

from ..config import HTTP_TIMEOUT, INAO_DATASET_PAGE_URL, INAO_RESOURCE_URL
from ..provenance import sha256_file
from ..validation import WinePipelineError


REQUIRED_SHAPEFILE_SUFFIXES = {".shp", ".shx", ".dbf", ".prj"}


@dataclass(frozen=True)
class DownloadedSource:
    configured_url: str
    final_url: str
    retrieval_time_utc: str
    headers: dict[str, str]
    filename: str
    path: Path
    size_bytes: int
    sha256: str
    archive_type: str

    def to_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        return payload


@dataclass(frozen=True)
class ExtractedShapefile:
    shapefile_path: Path
    members: list[dict[str, object]]

    def to_json(self) -> dict[str, object]:
        return {"shapefile_path": str(self.shapefile_path), "members": self.members}


def _interesting_headers(headers: requests.structures.CaseInsensitiveDict[str]) -> dict[str, str]:
    wanted = ("ETag", "Last-Modified", "Content-Type")
    return {name: headers[name] for name in wanted if name in headers}


def _filename_from_response(response: requests.Response, fallback: str) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    marker = "filename="
    if marker in disposition:
        filename = disposition.split(marker, 1)[1].strip().strip('"')
        if filename:
            return Path(filename).name
    return Path(response.url).name or fallback


def detect_archive_type(path: Path) -> str:
    if zipfile.is_zipfile(path):
        return "zip"
    if tarfile.is_tarfile(path):
        return "tar"
    raise WinePipelineError(f"Unsupported INAO archive type for {path}")


def stream_download(
    *,
    configured_url: str,
    destination_dir: Path,
    fallback_filename: str,
    timeout: tuple[float, float] = HTTP_TIMEOUT,
    session: requests.Session | None = None,
) -> DownloadedSource:
    destination_dir.mkdir(parents=True, exist_ok=True)
    client = session or requests.Session()
    response = client.get(configured_url, stream=True, timeout=timeout)
    response.raise_for_status()
    filename = _filename_from_response(response, fallback_filename)
    path = destination_dir / filename
    with path.open("wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                file.write(chunk)
    archive_type = detect_archive_type(path)
    return DownloadedSource(
        configured_url=configured_url,
        final_url=response.url,
        retrieval_time_utc=datetime.now(timezone.utc).isoformat(),
        headers=_interesting_headers(response.headers),
        filename=path.name,
        path=path,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        archive_type=archive_type,
    )


def _safe_target(root: Path, member_name: str) -> Path:
    pure = PurePosixPath(member_name)
    if pure.is_absolute() or ".." in pure.parts:
        raise WinePipelineError(f"Archive member would escape extraction root: {member_name}")
    target = (root / Path(*pure.parts)).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise WinePipelineError(f"Archive member would escape extraction root: {member_name}")
    return target


def extract_archive_safely(archive_path: Path, destination_dir: Path) -> list[Path]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    archive_type = detect_archive_type(archive_path)
    if archive_type == "zip":
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                target = _safe_target(destination_dir, info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    output.write(source.read())
                extracted.append(target)
    elif archive_type == "tar":
        with tarfile.open(archive_path) as archive:
            for member in archive.getmembers():
                target = _safe_target(destination_dir, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    continue
                with source, target.open("wb") as output:
                    output.write(source.read())
                extracted.append(target)
    return extracted


def locate_shapefile(extracted_root: Path) -> ExtractedShapefile:
    shp_files = sorted(extracted_root.rglob("*.shp"))
    if len(shp_files) != 1:
        raise WinePipelineError(f"Expected exactly one INAO shapefile dataset, found {len(shp_files)}")
    shp_path = shp_files[0]
    stem = shp_path.with_suffix("")
    related = sorted(shp_path.parent.glob(f"{shp_path.stem}.*"))
    suffixes = {path.suffix.lower() for path in related}
    missing = sorted(REQUIRED_SHAPEFILE_SUFFIXES - suffixes)
    if missing:
        raise WinePipelineError(f"Shapefile dataset is missing required members: {missing}")
    members = [
        {"name": str(path.relative_to(extracted_root)), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in related
        if path.with_suffix("") == stem
    ]
    return ExtractedShapefile(shapefile_path=shp_path, members=members)


def extract_inao_source(run_dir: Path, *, session: requests.Session | None = None) -> tuple[DownloadedSource, ExtractedShapefile]:
    downloaded = stream_download(
        configured_url=INAO_RESOURCE_URL,
        destination_dir=run_dir / "downloads" / "inao",
        fallback_filename="inao_aoc_archive",
        session=session,
    )
    extracted_root = run_dir / "extracted" / "inao"
    extract_archive_safely(downloaded.path, extracted_root)
    shapefile = locate_shapefile(extracted_root)
    return downloaded, shapefile


def source_urls() -> dict[str, str]:
    return {"dataset_page": INAO_DATASET_PAGE_URL, "resource_endpoint": INAO_RESOURCE_URL}

