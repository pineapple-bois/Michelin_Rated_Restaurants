"""Path helpers for the INSEE/OECD reference-data lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelinePaths:
    year: int
    raw_insee_root: Path
    raw_oecd_root: Path
    candidate_root: Path
    product_root: Path
    geometry_path: Path
    source_cache_root: Path | None = None

    @classmethod
    def create(
        cls,
        *,
        year: int,
        raw_root: Path = Path("tmp/insee_pipeline"),
        candidate_root: Path = Path("data/candidates/insee"),
        product_root: Path = Path("data/products/insee"),
        geometry_path: Path = Path("data/raw/geodata/departments.geojson"),
        source_cache_root: Path | None = None,
    ) -> "PipelinePaths":
        return cls(
            year=year,
            raw_insee_root=raw_root / str(year) / "insee",
            raw_oecd_root=raw_root / str(year) / "oecd",
            candidate_root=candidate_root / str(year),
            product_root=product_root / str(year),
            geometry_path=geometry_path,
            source_cache_root=source_cache_root,
        )

    def insee_zip(self, dataset_id: str) -> Path:
        return self.raw_insee_root / f"{dataset_id}.zip"

    @property
    def oecd_gdp_csv(self) -> Path:
        return self.raw_oecd_root / "oecd_gdp_regions.csv"

    @property
    def departmental_table(self) -> Path:
        return self.candidate_root / f"france_departments_{self.year}.csv"

    @property
    def crosswalk(self) -> Path:
        return self.candidate_root / f"oecd_tl3_crosswalk_{self.year}.csv"

    @property
    def source_inventory(self) -> Path:
        return self.candidate_root / f"source_inventory_{self.year}.json"

    @property
    def manifest(self) -> Path:
        return self.candidate_root / f"manifest_{self.year}.json"

    @property
    def validation_report(self) -> Path:
        return self.candidate_root / f"validation_report_{self.year}.json"

    @property
    def product_table(self) -> Path:
        return self.product_root / f"france_departments_{self.year}.csv"

    @property
    def product_manifest(self) -> Path:
        return self.product_root / f"manifest_{self.year}.json"
