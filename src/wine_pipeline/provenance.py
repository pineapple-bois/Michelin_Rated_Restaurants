"""Durable provenance and validation report writers for the wine pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path

from .config import DURABLE_REPORT_ROOT
from .validation import Check


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def source_date_from_headers(headers: dict[str, str], fallback_iso: str) -> str:
    last_modified = headers.get("Last-Modified") or headers.get("last-modified")
    if last_modified:
        try:
            parsed = parsedate_to_datetime(last_modified)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            pass
    return fallback_iso[:10]


@dataclass
class ReportCollector:
    run_id: str
    started_at_utc: str = field(default_factory=utc_now)
    completed_at_utc: str | None = None
    provenance: dict[str, object] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)

    def add_check(self, check: Check) -> Check:
        self.checks.append(check)
        return check

    def extend_checks(self, checks: list[Check]) -> None:
        self.checks.extend(checks)

    def write_durable_reports(self, *, source_date: str, hash_prefix: str, report_root: Path = DURABLE_REPORT_ROOT) -> dict[str, Path]:
        self.completed_at_utc = utc_now()
        stem = f"wine_pipeline_{source_date}_{hash_prefix}"
        provenance_path = report_root / f"{stem}.provenance.json"
        validation_path = report_root / f"{stem}.validation.json"
        provenance_payload = {
            "run_id": self.run_id,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            **self.provenance,
        }
        validation_payload = {
            "run_id": self.run_id,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "checks": [check.to_json() for check in self.checks],
            "passed": all(check.passed for check in self.checks),
        }
        write_json(provenance_path, provenance_payload)
        write_json(validation_path, validation_payload)
        return {"provenance": provenance_path, "validation": validation_path}
