"""Stage 2 wine AOC regional simplification."""

from .transform import (
    CANONICAL_BUFFER_M,
    CANONICAL_RUN_ID,
    CANONICAL_SIMPLIFY_M,
    CANONICAL_OVERLAP_STRATEGY,
    OUTPUT_COLUMNS,
    STAGE1_COLUMNS,
    classify_residual_overlap,
    SimplificationParameters,
    simplify_region,
    slugify_region,
)
from .batch import run_batch
from .serialization import (
    POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2,
    POST_REPROJECTION_NEGLIGIBLE_ABSOLUTE_M2,
    POST_REPROJECTION_NEGLIGIBLE_RELATIVE,
    POST_REPROJECTION_RELATIVE_TOLERANCE,
    SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2,
    SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE,
    cleanup_final_geometries,
    repair_post_reprojection_geometry,
)
from .diagnostics import DiagnosticResult, run_diagnostics
from .assembly import AssemblyResult, assemble_candidate

__all__ = [
    "CANONICAL_BUFFER_M",
    "CANONICAL_RUN_ID",
    "CANONICAL_SIMPLIFY_M",
    "CANONICAL_OVERLAP_STRATEGY",
    "OUTPUT_COLUMNS",
    "STAGE1_COLUMNS",
    "SimplificationParameters",
    "classify_residual_overlap",
    "simplify_region",
    "slugify_region",
    "run_batch",
    "SERIALIZATION_CLEANUP_ABSOLUTE_TOLERANCE_M2",
    "SERIALIZATION_CLEANUP_RELATIVE_TOLERANCE",
    "POST_REPROJECTION_ABSOLUTE_TOLERANCE_M2",
    "POST_REPROJECTION_RELATIVE_TOLERANCE",
    "POST_REPROJECTION_NEGLIGIBLE_ABSOLUTE_M2",
    "POST_REPROJECTION_NEGLIGIBLE_RELATIVE",
    "cleanup_final_geometries",
    "repair_post_reprojection_geometry",
    "DiagnosticResult",
    "run_diagnostics",
    "AssemblyResult",
    "assemble_candidate",
]
