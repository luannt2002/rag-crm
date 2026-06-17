"""Unit tests for CAG Mode (Cache-Augmented Generation).

Mock-only — no live LLM calls, no DB. Verifies:

- NullCAGService.should_engage → False (default OFF baseline) and
  build_corpus_payload → None (the Null adapter performs zero I/O).
- AnthropicCAGService.should_engage gates by:
    * feature flag (False when ``enabled=False`` — no corpus_loader call).
    * empty corpus (loader returns "" → False).
    * over-ceiling corpus (loader.tokens > max → False, RAG fallback).
    * loader exception (RepositoryError / RetrievalError / OSError /
      ValueError) → False (degrade silent, HALLU=0 sacred).
    * happy path (corpus fits + flag ON) → True.
- AnthropicCAGService.build_corpus_payload returns ``CAGPayload`` with
  ``cache_breakpoint=True`` on the happy path and ``None`` on every
  failure path.
- CAGService.decide:
    * False strategy gate → CAGDecision(engaged=False, payload=None) and
      payload loader is NEVER called (no extra I/O when gate refuses).
    * True gate + valid payload → CAGDecision(engaged=True, payload=<obj>).
    * True gate but payload load returns None → CAGDecision(engaged=False)
      so the orchestrator falls back to RAG (HALLU=0 safe even on the
      gate-accepted-but-load-failed race).
- Registry: build_cag("null") / ("anthropic") returns matching strategy;
  unknown provider raises ValueError; case + whitespace tolerated.
- Constants: defaults match the spec (flag OFF, provider "null",
  ceiling = 80000 tokens) — so a Sonnet refactor cannot silently lower
  the cross-over point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from ragbot.application.ports.cag_port import CAGPayload, CAGServicePort
try:
    from ragbot.application.services.cag_service import CAGDecision, CAGService
    from ragbot.infrastructure.cag.anthropic_cag import (
        AnthropicCAGService,
        CorpusSnapshot,
    )
    from ragbot.infrastructure.cag.null_cag import NullCAGService
    from ragbot.infrastructure.cag.registry import build_cag, list_providers
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "cag subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.constants import (
    DEFAULT_CAG_MAX_CORPUS_TOKENS,
    DEFAULT_CAG_MODE_ENABLED,
    DEFAULT_CAG_PROVIDER,
)
from ragbot.shared.errors import RepositoryError, RetrievalError
from ragbot.shared.types import TenantId


# ---------- shared fixtures --------------------------------------------------


@dataclass
class FakeCorpusLoader:
    """Records every ``__call__`` and returns a scripted ``CorpusSnapshot``.

    If ``raise_exc`` is set, ``__call__`` raises it instead of returning
    a snapshot — used to exercise the degrade-silent path.
    """

    text: str = "Article 1. Backups run nightly. Article 2. Retention 30 days."
    tokens: int = 12
    raise_exc: Exception | None = None
    calls: list[tuple[TenantId, str]] = field(default_factory=list)

    async def __call__(
        self,
        record_tenant_id: TenantId,
        record_bot_id: str,
    ) -> CorpusSnapshot:
        self.calls.append((record_tenant_id, record_bot_id))
        if self.raise_exc is not None:
            raise self.raise_exc
        return CorpusSnapshot(text=self.text, tokens=self.tokens)


def _tenant() -> TenantId:
    return TenantId(uuid4())


# ---------- NullCAGService ---------------------------------------------------


@pytest.mark.asyncio
async def test_null_cag_should_engage_always_false() -> None:
    """Default OFF Null Object: no engagement, no LLM call, no DB load."""
    null: CAGServicePort = NullCAGService()

    engaged = await null.should_engage(
        record_tenant_id=_tenant(),
        record_bot_id="bot-x",
    )

    assert engaged is False
    assert NullCAGService.get_provider_name() == "null"


@pytest.mark.asyncio
async def test_null_cag_build_payload_returns_none() -> None:
    """Null adapter never loads corpus — payload is always None."""
    null = NullCAGService()

    payload = await null.build_corpus_payload(
        record_tenant_id=_tenant(),
        record_bot_id="bot-x",
    )

    assert payload is None


# ---------- AnthropicCAGService — gating logic -------------------------------


@pytest.mark.asyncio
async def test_anthropic_cag_flag_off_short_circuits_without_loader_call() -> None:
    """Feature flag OFF: corpus loader MUST NOT be called (cost guard)."""
    loader = FakeCorpusLoader()
    cag = AnthropicCAGService(
        corpus_loader=loader,
        enabled=False,
        max_corpus_tokens=DEFAULT_CAG_MAX_CORPUS_TOKENS,
    )

    engaged = await cag.should_engage(
        record_tenant_id=_tenant(),
        record_bot_id="bot-flag-off",
    )

    assert engaged is False
    assert loader.calls == []


@pytest.mark.asyncio
async def test_anthropic_cag_happy_path_engages_and_returns_cached_payload() -> None:
    """Flag ON + corpus fits → engage, payload carries cache breakpoint."""
    loader = FakeCorpusLoader(text="The full corpus text here.", tokens=8)
    cag = AnthropicCAGService(
        corpus_loader=loader,
        enabled=True,
        max_corpus_tokens=DEFAULT_CAG_MAX_CORPUS_TOKENS,
    )
    tenant = _tenant()

    engaged = await cag.should_engage(
        record_tenant_id=tenant,
        record_bot_id="bot-happy",
    )
    payload = await cag.build_corpus_payload(
        record_tenant_id=tenant,
        record_bot_id="bot-happy",
    )

    assert engaged is True
    assert payload is not None
    assert payload.corpus_text == "The full corpus text here."
    assert payload.corpus_tokens == 8
    # Anthropic prompt-cache breakpoint MUST be set so the LLM adapter
    # wraps the corpus block in cache_control: ephemeral.
    assert payload.cache_breakpoint is True
    # Loader called exactly once per public method (should_engage + payload).
    assert len(loader.calls) == 2
    assert loader.calls[0] == (tenant, "bot-happy")


@pytest.mark.asyncio
async def test_anthropic_cag_empty_corpus_declines() -> None:
    """Empty corpus → False (RAG fallback handles the refuse path)."""
    loader = FakeCorpusLoader(text="", tokens=0)
    cag = AnthropicCAGService(
        corpus_loader=loader,
        enabled=True,
        max_corpus_tokens=DEFAULT_CAG_MAX_CORPUS_TOKENS,
    )

    engaged = await cag.should_engage(
        record_tenant_id=_tenant(),
        record_bot_id="bot-empty",
    )
    payload = await cag.build_corpus_payload(
        record_tenant_id=_tenant(),
        record_bot_id="bot-empty",
    )

    assert engaged is False
    assert payload is None


@pytest.mark.asyncio
async def test_anthropic_cag_over_ceiling_declines_so_rag_takes_over() -> None:
    """Corpus larger than ceiling → False — paper says RAG cheaper here."""
    loader = FakeCorpusLoader(text="x" * 100, tokens=200000)
    cag = AnthropicCAGService(
        corpus_loader=loader,
        enabled=True,
        max_corpus_tokens=80000,  # explicit per-test ceiling
    )

    engaged = await cag.should_engage(
        record_tenant_id=_tenant(),
        record_bot_id="bot-huge",
    )
    payload = await cag.build_corpus_payload(
        record_tenant_id=_tenant(),
        record_bot_id="bot-huge",
    )

    assert engaged is False
    assert payload is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        RepositoryError("db down"),
        RetrievalError("upstream search timeout"),
        OSError("socket closed"),
        ValueError("malformed corpus"),
    ],
)
async def test_anthropic_cag_loader_error_degrades_silent_to_rag(
    exc: Exception,
) -> None:
    """Loader exception → False — never let the LLM answer from memory."""
    loader = FakeCorpusLoader(raise_exc=exc)
    cag = AnthropicCAGService(
        corpus_loader=loader,
        enabled=True,
        max_corpus_tokens=DEFAULT_CAG_MAX_CORPUS_TOKENS,
    )

    engaged = await cag.should_engage(
        record_tenant_id=_tenant(),
        record_bot_id="bot-loader-err",
    )
    payload = await cag.build_corpus_payload(
        record_tenant_id=_tenant(),
        record_bot_id="bot-loader-err",
    )

    assert engaged is False
    assert payload is None
    # Both methods attempted the loader exactly once each — no retry.
    assert len(loader.calls) == 2


# ---------- CAGService orchestration ----------------------------------------


class _StubStrategy:
    """Configurable stub implementing the CAGServicePort surface."""

    def __init__(
        self,
        *,
        engage: bool,
        payload: CAGPayload | None,
    ) -> None:
        self._engage = engage
        self._payload = payload
        self.engage_calls: list[tuple[Any, ...]] = []
        self.payload_calls: list[tuple[Any, ...]] = []

    async def should_engage(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: str,
    ) -> bool:
        self.engage_calls.append((record_tenant_id, record_bot_id))
        return self._engage

    async def build_corpus_payload(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: str,
    ) -> CAGPayload | None:
        self.payload_calls.append((record_tenant_id, record_bot_id))
        return self._payload


@pytest.mark.asyncio
async def test_cag_service_gate_false_skips_payload_load() -> None:
    """When the gate declines, payload loader MUST NOT be invoked."""
    stub = _StubStrategy(engage=False, payload=None)
    service = CAGService(strategy=stub)

    decision = await service.decide(
        record_tenant_id=_tenant(),
        record_bot_id="bot-gate-off",
    )

    assert decision == CAGDecision(engaged=False, payload=None)
    assert len(stub.engage_calls) == 1
    assert stub.payload_calls == []  # no extra I/O on the OFF path


@pytest.mark.asyncio
async def test_cag_service_happy_path_returns_engaged_decision_with_payload() -> None:
    """Gate True + payload → engaged decision exposes corpus to orchestrator."""
    payload = CAGPayload(
        corpus_text="full corpus body",
        corpus_tokens=4,
        cache_breakpoint=True,
    )
    stub = _StubStrategy(engage=True, payload=payload)
    service = CAGService(strategy=stub)

    decision = await service.decide(
        record_tenant_id=_tenant(),
        record_bot_id="bot-happy",
    )

    assert decision.engaged is True
    assert decision.payload is payload
    assert decision.payload is not None
    assert decision.payload.cache_breakpoint is True


@pytest.mark.asyncio
async def test_cag_service_gate_true_but_payload_none_falls_back_to_rag() -> None:
    """Defensive race: gate accepted then payload load returned None →
    decision MUST be engaged=False so orchestrator runs RAG. HALLU=0."""
    stub = _StubStrategy(engage=True, payload=None)
    service = CAGService(strategy=stub)

    decision = await service.decide(
        record_tenant_id=_tenant(),
        record_bot_id="bot-race",
    )

    assert decision == CAGDecision(engaged=False, payload=None)
    assert len(stub.engage_calls) == 1
    assert len(stub.payload_calls) == 1


# ---------- Registry --------------------------------------------------------


def test_build_cag_null_returns_null_strategy() -> None:
    strategy = build_cag("null")

    assert isinstance(strategy, NullCAGService)
    assert "null" in list_providers()


def test_build_cag_anthropic_returns_anthropic_strategy() -> None:
    loader = FakeCorpusLoader()
    strategy = build_cag(
        "anthropic",
        corpus_loader=loader,
        enabled=True,
        max_corpus_tokens=DEFAULT_CAG_MAX_CORPUS_TOKENS,
    )

    assert isinstance(strategy, AnthropicCAGService)
    assert "anthropic" in list_providers()


def test_build_cag_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown cag provider"):
        build_cag("does-not-exist")


def test_build_cag_provider_key_case_insensitive_and_whitespace_tolerant() -> None:
    """Operator might type ``Null`` or ``  anthropic  `` in admin UI."""
    assert isinstance(build_cag("  Null  "), NullCAGService)
    assert isinstance(build_cag("NULL"), NullCAGService)
    loader = FakeCorpusLoader()
    assert isinstance(
        build_cag(
            "  Anthropic  ",
            corpus_loader=loader,
            enabled=False,
            max_corpus_tokens=DEFAULT_CAG_MAX_CORPUS_TOKENS,
        ),
        AnthropicCAGService,
    )


def test_build_cag_null_ignores_extra_kwargs() -> None:
    """Registry call site stays uniform — Null silently drops adapter-specific
    kwargs so callers can pass the full kwargs bag without branching."""
    strategy = build_cag(
        "null",
        corpus_loader=FakeCorpusLoader(),
        enabled=True,
        max_corpus_tokens=DEFAULT_CAG_MAX_CORPUS_TOKENS,
    )

    assert isinstance(strategy, NullCAGService)


# ---------- Constants (pin against silent refactor drift) -------------------


def test_cag_constants_match_spec() -> None:
    """Pin the constant values that the rest of the platform relies on.

    Citation: Chan et al. 2024 — "Don't Do RAG" (arXiv:2412.15605).
    Paper Figure 5 cross-over: RAG cheaper than CAG above ~80K tokens
    on Llama 3.1 8B / 70B. 80000 is therefore the conservative ceiling;
    raising it without re-measuring would regress cost without a recall
    benefit. Pin it here so a Sonnet refactor cannot silently lower
    (or raise) the cross-over point.
    """
    assert DEFAULT_CAG_MODE_ENABLED is False
    assert DEFAULT_CAG_PROVIDER == "null"
    assert DEFAULT_CAG_MAX_CORPUS_TOKENS == 80000


# ---------- HALLU=0 contract: CAG never overrides the LLM answer ------------


@pytest.mark.asyncio
async def test_cag_service_never_returns_text_outside_corpus() -> None:
    """App-mindset: CAG payload text MUST equal the corpus loader's text.

    No template injection, no prefix/suffix, no 'You are a helpful
    assistant' prepend — the bot owner's system_prompt is the single
    source of truth and CAG just exposes the corpus to it.
    """
    corpus_text = (
        "Article 1. Backups run nightly at 02:00 UTC.\n"
        "Article 2. Retention period is 30 days for tier A and 90 days "
        "for tier B documents."
    )
    loader = FakeCorpusLoader(text=corpus_text, tokens=24)
    strategy = AnthropicCAGService(
        corpus_loader=loader,
        enabled=True,
        max_corpus_tokens=DEFAULT_CAG_MAX_CORPUS_TOKENS,
    )
    service = CAGService(strategy=strategy)

    decision = await service.decide(
        record_tenant_id=_tenant(),
        record_bot_id="bot-faithful",
    )

    assert decision.engaged is True
    assert decision.payload is not None
    # The payload text is EXACTLY the corpus — no platform-injected
    # instructions, no industry literals, no LLM rewriting.
    assert decision.payload.corpus_text == corpus_text
