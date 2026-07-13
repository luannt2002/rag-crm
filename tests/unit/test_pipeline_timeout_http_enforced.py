"""B6: the synchronous test_chat HTTP path must enforce ``pipeline_timeout_s``.

Flow-5 audit (2026-07-13): the async worker wraps ``graph.ainvoke`` in
``asyncio.wait_for(timeout=pipeline_timeout_s)``, but the sync test_chat HTTP
handler awaited it bare — a hung upstream held the request slot until an
external (gateway/client) cut. This makes the harness enforce the SAME
server-side wall-clock budget a production consumer gets on the worker path.
"""
from __future__ import annotations

import inspect

from ragbot.interfaces.http.routes.test_chat import chat_routes


def _src() -> str:
    return inspect.getsource(chat_routes)


def test_sync_ainvoke_wrapped_in_wait_for() -> None:
    src = _src()
    assert "asyncio.wait_for(" in src, "sync pipeline invoke must be timeout-bounded"
    assert "pipeline_timeout_s" in src, "timeout must be config-driven per bot"
    # the value is resolved from pipeline_config (not a bare literal)
    assert 'pipeline_config.get("pipeline_timeout_s")' in src


def test_zero_disables_the_timeout() -> None:
    """0 = disabled (operator escape) so the wrapper is never a forced kill."""
    src = _src()
    assert "_pipeline_timeout_s > 0" in src


def test_timeout_maps_to_retryable_503_not_500() -> None:
    """A pipeline timeout is transient infra → 503 (retryable), not a 500."""
    src = _src()
    assert "except asyncio.TimeoutError:" in src
    # handled in the same 503 envelope as LLMError (_svc_unavailable)
    idx = src.find("except asyncio.TimeoutError:")
    after = src[idx : idx + 800]
    assert "_svc_unavailable = True" in after
    assert "test_chat_pipeline_timeout" in after
