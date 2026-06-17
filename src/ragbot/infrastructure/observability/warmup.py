"""Startup warmup runner.

Best-effort probe of every CB-protected provider at app boot so the
*first* real chat request does not pay a cold connect / DNS / model-load
tax. Designed to be:

* **Non-blocking**: spawned via ``asyncio.create_task`` from the FastAPI
  lifespan so a slow probe does not delay readiness.
* **Fail-soft**: never raises. Failures emit ``warmup_failed`` warnings.
* **Provider-neutral**: reaches into the DI container for the configured
  ``EmbeddingPort`` / ``LLMPort`` / ``RerankerPort`` /
  ``TokenizerPort`` — no provider class or model name hardcoded here.
  Ops swap providers via ``system_config`` / ``ai_models`` with zero
  code change.
* **Toggleable**: gated by ``DEFAULT_WARMUP_ENABLED`` (constant) +
  optional ``RAGBOT_WARMUP_ENABLED`` env override so a sidecar deploy can
  skip the probe (cold-start canary, integration test, etc.).

The runner probes embedder, LLM, reranker and tokenizer providers — all
sit behind a ``CircuitBreaker`` and benefit from cold-start warm. Each
probe is independent (any one failing does NOT short-circuit the others)
and the per-probe duration is recorded into the
``ragbot_warmup_provider_duration_ms{provider, ok}`` histogram so
operators can correlate cold-start vs. p99 outliers per provider.

The "ping" probe text used by the LLM call is a documented protocol probe,
not user-facing prompt content; it is **never** injected into a real chat
prompt or system_prompt. See ``DEFAULT_WARMUP_LLM_PROBE_TEXT`` constant.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog

from ragbot.shared.constants import (
    DEFAULT_WARMUP_ENABLED,
    DEFAULT_WARMUP_LLM_MAX_TOKENS,
    DEFAULT_WARMUP_LLM_PROBE_TEXT,
    DEFAULT_WARMUP_TIMEOUT_S,
)

logger = structlog.get_logger(__name__)

_ENV_WARMUP_ENABLED = "RAGBOT_WARMUP_ENABLED"


def _safe_log(level: str, event: str, **fields: Any) -> None:
    """Best-effort structured log.

    structlog can raise ``ValueError: I/O operation on closed file`` when a
    captured stream (pytest capsys teardown, container shutdown, broken
    pipe) is torn down after the cached handle was bound. Warmup output is
    informational — never let log-sink failures bubble out of a fail-soft
    runner.
    """
    try:
        getattr(logger, level)(event, **fields)
    except (ValueError, OSError, AttributeError):
        pass


def _warmup_enabled() -> bool:
    """Resolve the warmup toggle.

    Env var ``RAGBOT_WARMUP_ENABLED`` (``"0"``/``"false"``/``"no"``) wins
    over the constant default so an operator can flip warmup off without
    redeploy.
    """
    raw = os.environ.get(_ENV_WARMUP_ENABLED)
    if raw is None:
        return DEFAULT_WARMUP_ENABLED
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


async def _warmup_embedding(container: Any, *, timeout_s: float) -> tuple[bool, float]:
    """Probe the configured embedder via ``health_check()``.

    ``LiteLLMEmbedder.health_check`` already executes a real ``aembedding``
    call against the bound model, so this is a true network warm — no
    extra spec / tenant context required.
    """
    t0 = time.perf_counter()
    try:
        embedder = container.embedder()
    except (AttributeError, RuntimeError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="embed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False, 0.0
    try:
        ok = await asyncio.wait_for(embedder.health_check(), timeout=timeout_s)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="embed",
            error_type=type(exc).__name__,
            error=str(exc),
            timeout_s=timeout_s,
        )
        return False, round((time.perf_counter() - t0) * 1000.0, 1)
    except (OSError, ConnectionError, ValueError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="embed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False, round((time.perf_counter() - t0) * 1000.0, 1)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return bool(ok), elapsed_ms


async def _warmup_llm(container: Any, *, timeout_s: float) -> tuple[bool, float]:
    """Probe the configured LLM router.

    ``DynamicLiteLLMRouter.health_check`` exercises ``refresh_routing``
    (DB read + Redis cache write). A deeper completion probe would need a
    bot-bound ``LLMSpec`` which warmup must not synthesise — application
    layer never invents bindings (Application MINDSET). This is enough to
    surface a misconfigured ``ai_models`` table at boot instead of on the
    first user request.
    """
    t0 = time.perf_counter()
    try:
        llm = container.llm()
    except (AttributeError, RuntimeError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="llm",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False, 0.0
    try:
        ok = await asyncio.wait_for(llm.health_check(), timeout=timeout_s)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="llm",
            error_type=type(exc).__name__,
            error=str(exc),
            timeout_s=timeout_s,
        )
        return False, round((time.perf_counter() - t0) * 1000.0, 1)
    except (OSError, ConnectionError, ValueError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="llm",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False, round((time.perf_counter() - t0) * 1000.0, 1)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return bool(ok), elapsed_ms


async def _warmup_reranker(container: Any, *, timeout_s: float) -> tuple[bool, float]:
    """Probe the configured reranker via ``health_check()``.

    The reranker sits behind a ``CircuitBreaker`` (Jina HTTP API) and pays
    a DNS + TLS handshake on the first real call; warming it here moves
    that cost from the user's first turn to the boot lifecycle.

    The container provider may be absent (legacy deploy without a
    reranker registry binding) or return a ``NullReranker`` (default
    OFF) — both cases are silent successes (``ok=True``, ``ms=0``) so a
    Grafana panel does not flag a happy default-OFF deploy as degraded.
    """
    t0 = time.perf_counter()
    try:
        reranker = container.reranker()
    except (AttributeError, RuntimeError) as exc:
        _safe_log(
            "info",
            "warmup_provider_missing",
            stage="reranker",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return True, 0.0
    if reranker is None:
        return True, 0.0
    health_check = getattr(reranker, "health_check", None)
    if health_check is None:
        # Strategy implementation does not expose a probe (legacy adapter).
        # Treat as silent success — caller's CB will flap on first real
        # call but warmup must not fabricate a probe API.
        return True, 0.0
    try:
        ok = await asyncio.wait_for(health_check(), timeout=timeout_s)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="reranker",
            error_type=type(exc).__name__,
            error=str(exc),
            timeout_s=timeout_s,
        )
        return False, round((time.perf_counter() - t0) * 1000.0, 1)
    except (OSError, ConnectionError, ValueError, KeyError, AttributeError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="reranker",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False, round((time.perf_counter() - t0) * 1000.0, 1)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return bool(ok), elapsed_ms


async def _warmup_tokenizer(container: Any, *, timeout_s: float) -> tuple[bool, float]:
    """Warm the configured tokenizer via a no-op tokenisation.

    The Vietnamese ``underthesea`` tokenizer lazy-loads a multi-MB model
    on first use. A 1-token probe forces that load at boot. The
    ``TokenizerPort`` contract is sync (no ``await``); we run the call
    in the default executor to honour the ``timeout_s`` budget without
    blocking the event loop on a slow first-load.

    The container provider may be absent — treated as silent success.
    """
    t0 = time.perf_counter()
    if not hasattr(container, "tokenizer"):
        # Tokenizer not wired in this container — silent skip. Used to log
        # an INFO warmup_provider_missing at every restart; the absence is
        # by design (tokenizer is opt-in per deployment).
        return True, 0.0
    try:
        tokenizer = container.tokenizer()
    except (AttributeError, RuntimeError) as exc:
        _safe_log(
            "info",
            "warmup_provider_missing",
            stage="tokenizer",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return True, 0.0
    if tokenizer is None:
        return True, 0.0
    tokenize = getattr(tokenizer, "tokenize", None)
    if tokenize is None:
        return True, 0.0
    loop = asyncio.get_event_loop()
    try:
        tokens = await asyncio.wait_for(
            loop.run_in_executor(None, tokenize, DEFAULT_WARMUP_LLM_PROBE_TEXT),
            timeout=timeout_s,
        )
    except (TimeoutError, asyncio.TimeoutError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="tokenizer",
            error_type=type(exc).__name__,
            error=str(exc),
            timeout_s=timeout_s,
        )
        return False, round((time.perf_counter() - t0) * 1000.0, 1)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        _safe_log(
            "warning",
            "warmup_failed",
            stage="tokenizer",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False, round((time.perf_counter() - t0) * 1000.0, 1)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    return bool(tokens is not None), elapsed_ms


def _emit_provider_metric(provider: str, ok: bool, elapsed_ms: float) -> None:
    """Record per-provider warmup outcome on the histogram. Optional —
    the metrics module may be unavailable in unit-test imports, so any
    failure to observe is silently swallowed (matches ``_safe_log``)."""
    try:
        from ragbot.infrastructure.observability.metrics import (
            warmup_provider_duration_ms,
        )
    except (ImportError, AttributeError):
        return
    try:
        warmup_provider_duration_ms.labels(
            provider=provider,
            ok="true" if ok else "false",
        ).observe(float(elapsed_ms))
    except (ValueError, KeyError, AttributeError):
        return


async def run_warmup(
    container: Any,
    *,
    timeout_s: float = DEFAULT_WARMUP_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute the embed + LLM + reranker + tokenizer warmup probes.

    Returns a summary dict with one ``{provider}_ok`` / ``{provider}_ms``
    pair per provider plus the probe-text + max-tokens metadata. NEVER
    raises. Caller is the FastAPI lifespan in
    ``ragbot.interfaces.http.app.lifespan``.

    Every probe is independent: a slow / down reranker MUST NOT starve
    the tokenizer probe, and vice versa. Probes run sequentially so
    cumulative wall-time is bounded by ``4 * timeout_s``; a parallel
    fan-out would shave latency at the cost of stampeding a single
    upstream during a degraded scenario, which is the wrong trade-off
    for a fail-soft probe.
    """
    if not _warmup_enabled():
        _safe_log("info", "warmup_skipped", reason="disabled")
        return {"skipped": True}
    embed_ok, embed_ms = await _warmup_embedding(container, timeout_s=timeout_s)
    _safe_log(
        "info",
        "warmup_provider_complete",
        provider="embed",
        ok=embed_ok,
        ms=embed_ms,
    )
    _emit_provider_metric("embed", embed_ok, embed_ms)
    llm_ok, llm_ms = await _warmup_llm(container, timeout_s=timeout_s)
    _safe_log(
        "info",
        "warmup_provider_complete",
        provider="llm",
        ok=llm_ok,
        ms=llm_ms,
    )
    _emit_provider_metric("llm", llm_ok, llm_ms)
    reranker_ok, reranker_ms = await _warmup_reranker(container, timeout_s=timeout_s)
    _safe_log(
        "info",
        "warmup_provider_complete",
        provider="reranker",
        ok=reranker_ok,
        ms=reranker_ms,
    )
    _emit_provider_metric("reranker", reranker_ok, reranker_ms)
    tokenizer_ok, tokenizer_ms = await _warmup_tokenizer(container, timeout_s=timeout_s)
    _safe_log(
        "info",
        "warmup_provider_complete",
        provider="tokenizer",
        ok=tokenizer_ok,
        ms=tokenizer_ms,
    )
    _emit_provider_metric("tokenizer", tokenizer_ok, tokenizer_ms)
    summary: dict[str, Any] = {
        "embed_ok": embed_ok,
        "embed_ms": embed_ms,
        "llm_ok": llm_ok,
        "llm_ms": llm_ms,
        "reranker_ok": reranker_ok,
        "reranker_ms": reranker_ms,
        "tokenizer_ok": tokenizer_ok,
        "tokenizer_ms": tokenizer_ms,
        "probe_text": DEFAULT_WARMUP_LLM_PROBE_TEXT,
        "max_tokens": DEFAULT_WARMUP_LLM_MAX_TOKENS,
    }
    if embed_ok and llm_ok and reranker_ok and tokenizer_ok:
        _safe_log("info", "warmup_complete", **summary)
    else:
        _safe_log("warning", "warmup_partial", **summary)
    return summary


__all__ = ["run_warmup"]
