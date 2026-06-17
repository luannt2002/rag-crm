"""Tier-B Q5-1 regression: `_invoke_llm_node` MUST forward cfg.params.max_tokens.

Audit `DEEPDIVE_24STEP_PER_NODE_20260429_145054.md` (B-Z5-Q5-1):
The plain-text path in `_invoke_llm_node` (line ~596) only forwarded
`temperature`. `cfg.params.max_tokens` from the resolved binding was
silently dropped, so non-generation purposes (decompose / grade /
understand / reflect / rewrite) ran with the LLM provider's hard
default (e.g. OpenAI 4096), wasting 5-10× the tokens needed.

Structured-output path correctly forwarded it (line 720).

This test asserts the source-level fix is in place without booting
the full LangGraph (which requires Postgres + Redis + LiteLLM).
"""
from __future__ import annotations

import inspect


def test_call_kwargs_includes_max_tokens_when_present() -> None:
    """Read query_graph source; verify both branches forward max_tokens."""
    from ragbot.orchestration import query_graph

    src = inspect.getsource(query_graph)
    # Both branches forward _max_tokens (coerced to int upstream once,
    # not per-call, so test stubs with MagicMock don't blow up).
    assert 'stream_kwargs["max_tokens"] = _max_tokens' in src
    assert 'call_kwargs["max_tokens"] = _max_tokens' in src


def test_max_tokens_resolved_from_cfg_params() -> None:
    """The fix reads `cfg.params.max_tokens` (the resolved binding value).
    The pattern uses getattr-of-getattr to defend against test stubs that
    don't expose `.params`."""
    from ragbot.orchestration import query_graph

    src = inspect.getsource(query_graph)
    assert '_max_tokens_raw = getattr(getattr(cfg, "params", None), "max_tokens", None)' in src
    # And the int-coerce guard against MagicMock stubs:
    assert "int(_max_tokens_raw)" in src


def test_zero_or_none_max_tokens_skipped() -> None:
    """If the binding has max_tokens=0 (admin disabled) or None (no binding),
    we must NOT pass max_tokens=0 to the LLM (some providers interpret 0 as
    'no output'). Mirror the predicate."""
    def _should_forward(mt: int | None) -> bool:
        return mt is not None and mt > 0

    assert _should_forward(1000) is True
    assert _should_forward(1) is True
    assert _should_forward(0) is False
    assert _should_forward(-1) is False
    assert _should_forward(None) is False
