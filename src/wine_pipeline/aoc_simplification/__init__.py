"""Stage 2 wine AOC regional simplification."""

from .transform import (
    CANONICAL_BUFFER_M,
    CANONICAL_RUN_ID,
    CANONICAL_SIMPLIFY_M,
    CANONICAL_OVERLAP_STRATEGY,
    OUTPUT_COLUMNS,
    STAGE1_COLUMNS,
    SimplificationParameters,
    simplify_region,
    slugify_region,
)
from .batch import run_batch
from .serialization import (
    SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2,
    SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE,
    cleanup_final_geometries,
)

__all__ = [
    "CANONICAL_BUFFER_M",
    "CANONICAL_RUN_ID",
    "CANONICAL_SIMPLIFY_M",
    "CANONICAL_OVERLAP_STRATEGY",
    "OUTPUT_COLUMNS",
    "STAGE1_COLUMNS",
    "SimplificationParameters",
    "simplify_region",
    "slugify_region",
    "run_batch",
    "SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2",
    "SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE",
    "cleanup_final_geometries",
]
