"""CRAG Grader strategies — Port + Strategy + Registry + Null Object.

See :mod:`ragbot.application.ports.crag_grader_port` for the contract.

Public entry-point :func:`build_crag_grader` returns the strategy matching
``system_config.crag_grader_provider``; default ``"per_chunk"`` preserves
the legacy N-call behaviour so this module is **opt-in** until an
operator flips the system_config row.
"""

from __future__ import annotations

from ragbot.application.services.crag_grader.batch_grader import (
    BatchCragGrader,
)
from ragbot.application.services.crag_grader.null_grader import (
    NullCragGrader,
)
from ragbot.application.services.crag_grader.per_chunk_grader import (
    PerChunkCragGrader,
)
from ragbot.application.services.crag_grader.registry import (
    build_crag_grader,
    list_providers,
)

__all__ = [
    "BatchCragGrader",
    "NullCragGrader",
    "PerChunkCragGrader",
    "build_crag_grader",
    "list_providers",
]
