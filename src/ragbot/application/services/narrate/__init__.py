"""AdapChunk Layer 7 narrator helpers — rule-based and LLM-backed.

Companion package to :mod:`ragbot.application.services.narrate_service`
(the generic LLM-backed narrator service). Helpers in this package are
either:

* rule-based, deterministic linearisers (zero LLM cost) — e.g.
  :func:`narrate_table` for markdown tables; or
* focused, single-block-type LLM narrators with graceful-degradation
  fallback to the raw input — e.g. :func:`narrate_formula`.

HALLU=0 sacred: every LLM-backed helper falls back to raw input on
provider error rather than fabricating substitute text.
"""
from ragbot.application.services.narrate.formula_narrator import (
    LLMFn,
    narrate_formula,
)
from ragbot.application.services.narrate.table_narrator import narrate_table

__all__ = ["LLMFn", "narrate_formula", "narrate_table"]
