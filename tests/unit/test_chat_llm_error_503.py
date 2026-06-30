"""B4 — provider exhaustion (innocom 5xx after retries → LLMError) is a
TRANSIENT infra failure, not a pipeline bug. It must map to a retryable 503
(like ExternalServiceError), not a 500. Pin via source so the dedicated
handler branch isn't lost.
"""
from __future__ import annotations

import inspect

from ragbot.interfaces.http.routes.test_chat import chat_routes


def test_llm_error_maps_to_retryable_503() -> None:
    src = inspect.getsource(chat_routes)
    assert "except LLMError" in src, (
        "no dedicated LLMError branch — innocom 5xx exhaustion becomes a 500"
    )
    idx = src.find("except LLMError")
    block = src[idx : idx + 500]
    assert "_svc_unavailable = True" in block, (
        "LLMError branch must set _svc_unavailable so it maps to 503 (retryable)"
    )
    # It IS an LLM failure → the LLM circuit must still count it.
    assert "record_failure" in block, (
        "LLMError branch must record the LLM circuit failure"
    )
