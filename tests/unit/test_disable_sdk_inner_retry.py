"""B7 #1: disable the provider-SDK's OWN inner retry so ONLY the app's
``retry_with_backoff`` retries.

Verified mechanism (litellm 1.83.0 / openai 2.32.0): when no ``max_retries`` is
passed, litellm builds ``AsyncOpenAI(max_retries=2)`` (openai/llms/openai.py:682
``pop("max_retries", 2)``), whose retry loop stacks under the app's
``retry_with_backoff(max_attempts=3)`` — the load-test's 244 uncoordinated
"Retrying request" lines. Passing ``max_retries=0`` (+ ``num_retries=0``) at
every ``acompletion`` call collapses the amplification to one controlled layer.
"""
from __future__ import annotations

import asyncio
import inspect

from ragbot.infrastructure.llm.dynamic_litellm_router import _disable_sdk_inner_retry


def test_helper_sets_both_to_zero() -> None:
    k: dict = {"model": "m", "messages": []}
    _disable_sdk_inner_retry(k)
    assert k["num_retries"] == 0
    assert k["max_retries"] == 0


def test_helper_does_not_override_explicit_value() -> None:
    """setdefault semantics — an explicit caller value survives."""
    k: dict = {"num_retries": 5, "max_retries": 3}
    _disable_sdk_inner_retry(k)
    assert k["num_retries"] == 5
    assert k["max_retries"] == 3


def test_every_router_acompletion_is_guarded() -> None:
    """Every ``litellm.acompletion(`` CALL site in the router must be preceded
    by ``_disable_sdk_inner_retry(kwargs)`` — a new call site added without it
    silently re-introduces the nested-retry amplification."""
    from ragbot.infrastructure.llm import dynamic_litellm_router as r

    src = inspect.getsource(r)
    # 4 real call sites (:701 non-stream, :947 stream, :1150 spec, :1282 spec-stream)
    assert src.count("_disable_sdk_inner_retry(kwargs)") == 4


def test_structured_output_helper_disables_inner_retry() -> None:
    """Behavioural: the shared ``_safe_acompletion`` (used by every structured
    call) passes num_retries=0 + max_retries=0 to the injected litellm module."""
    from ragbot.application.services.structured_output_helper import _safe_acompletion

    class _MockLitellm:
        def __init__(self) -> None:
            self.captured: dict | None = None

        async def acompletion(self, **kwargs):  # noqa: ANN003
            self.captured = kwargs
            return "resp"

    mock = _MockLitellm()

    async def _run():
        return await _safe_acompletion(
            litellm_module=mock,
            schema_name="UnderstandOutput",
            provider_code="p",
            litellm_name="model",
            model="model",
            messages=[{"role": "user", "content": "hi"}],
        )

    resp = asyncio.run(_run())
    assert resp == "resp"
    assert mock.captured is not None
    assert mock.captured["num_retries"] == 0
    assert mock.captured["max_retries"] == 0


def test_structured_helper_respects_explicit_retries() -> None:
    from ragbot.application.services.structured_output_helper import _safe_acompletion

    class _MockLitellm:
        def __init__(self) -> None:
            self.captured: dict | None = None

        async def acompletion(self, **kwargs):  # noqa: ANN003
            self.captured = kwargs
            return "r"

    mock = _MockLitellm()

    async def _run():
        return await _safe_acompletion(
            litellm_module=mock,
            schema_name="S",
            provider_code="p",
            litellm_name="m",
            model="m",
            messages=[],
            max_retries=7,
        )

    asyncio.run(_run())
    assert mock.captured is not None
    assert mock.captured["max_retries"] == 7  # explicit not clobbered
