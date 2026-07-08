"""Configuration constants for the wine AOC geospatial pipeline."""

from __future__ import annotations

from pathlib import Path


INAO_DATASET_PAGE_URL = "https://www.data.gouv.fr/datasets/delimitation-parcellaire-des-aoc-viticoles-de-linao"
INAO_RESOURCE_URL = "https://www.data.gouv.fr/api/1/datasets/r/e79a7c68-2fe4-4225-a802-8379a8d6426c"
UC_DAVIS_BRANCH_URL = "https://github.com/UCDavisLibrary/wine-ontology/tree/master/examples/france"
UC_DAVIS_RAW_URL = "https://raw.githubusercontent.com/UCDavisLibrary/wine-ontology/master/examples/france/regions.geojson"
UC_DAVIS_COMMITS_API_URL = "https://api.github.com/repos/UCDavisLibrary/wine-ontology/commits"

TARGET_CRS = "EPSG:2154"
OUTPUT_LAYER = "aocs_france"
AOC_SIMPLIFICATION_TOLERANCE = 0.1
HTTP_TIMEOUT = (15.0, 120.0)

RUN_ROOT = Path("tmp/wine")
DURABLE_REPORT_ROOT = Path("data/wine/provenance")

