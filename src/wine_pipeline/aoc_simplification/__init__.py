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
]
