"""Stage 2: France partitions to departmental and regional products."""

from .pipeline import (
    Stage2PublicationError,
    Stage2Result,
    run_stage2,
    validate_stage2,
)
from .validation import Stage2ValidationError

__all__ = [
    "Stage2PublicationError",
    "Stage2Result",
    "Stage2ValidationError",
    "run_stage2",
    "validate_stage2",
]
