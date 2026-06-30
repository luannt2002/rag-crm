"""Unit tests — ModelResolverService falls back to system_config SSoT.

P2 fix: a bot WITHOUT a per-bot ``bot_model_bindings`` row for a purpose
must transparently follow the realtime platform default in
``system_config`` (Redis-cached, ~5-min TTL) instead of raising
``InvariantViolation``. Updating ``system_config.<key>`` then swaps the
model for every such bot with NO app restart.

Mirrors the established ``per-bot binding → system_config + ai_models →
NullObject`` fallback chain (CLAUDE.md
feedback_resolver_must_fallback_system_config) already implemented for
the INGEST embedding path (``document_service._embedding_spec``) and the
reranker (``reranker_resolver._lookup_platform_default``).

Invariants asserted (real, not ``assert True``):

(a) resolve_llm, NO binding  → LLMSpec for system_config.llm_default_model
(b) resolve_llm, WITH binding → binding wins (no regression, no sysconfig read)
(c) resolve_reranker, NO binding → RerankerSpec for system_config.reranker_model
(d) embedding query path (resolve_runtime purpose='embedding'), NO binding →
    a kind='embedding' ModelRuntimeConfig — NEVER an LLM (cross-kind bug).
(e) realtime: changing the stub system_config value swaps the resolved model
    on the next call (fallback spec not over-cached past the sysconfig TTL).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ragbot.application.dto.ai_specs import (
    BindingPurpose,
    EmbeddingSpec,
    LLMSpec,
    RerankerSpec,
)
from ragbot.application.ports.ai_config_port import (
    BindingRow,
    ModelRow,
    ProviderRow,
)
from ragbot.application.services.model_resolver import ModelResolverService
from ragbot.shared.errors import InvariantViolation


# ── Stubs ────────────────────────────────────────────────────────────


class _StubClock:
    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


class _StubCache:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, *, ttl_s: int) -> None:  # noqa: ARG002
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class _StubSystemConfig:
    """Mimics SystemConfigService.get — the Redis-cached SSoT reader."""

    def __init__(self, values: dict[str, object]) -> None:
        self.values = dict(values)
        self.get_calls: list[str] = []

    async def get(self, key: str, default=None):  # noqa: ANN001, ANN201
        self.get_calls.append(key)
        return self.values.get(key, default)


@dataclass
class _StubRepo:
    """Repo with the methods the resolver needs for the fallback path."""

    bindings_by_purpose: dict[str, list[BindingRow]]
    models: list[ModelRow]
    providers_by_id: dict[str, ProviderRow]
    list_models_calls: list[str | None] = field(default_factory=list)

    async def list_bindings(
        self,
        *,
        record_tenant_id: UUID,  # noqa: ARG002
        record_bot_id: UUID,  # noqa: ARG002
        purpose: str | None = None,
        active_only: bool = True,  # noqa: ARG002
    ) -> list[BindingRow]:
        if purpose is None:
            out: list[BindingRow] = []
            for binds in self.bindings_by_purpose.values():
                out.extend(binds)
            return out
        return list(self.bindings_by_purpose.get(purpose, []))

    async def get_models_by_ids(
        self, model_ids: list[UUID],
    ) -> dict[str, ModelRow]:
        by_id = {str(m.id): m for m in self.models}
        return {str(mid): by_id[str(mid)] for mid in model_ids if str(mid) in by_id}

    async def get_providers_by_ids(
        self, provider_ids: list[UUID],
    ) -> dict[str, ProviderRow]:
        return {
            str(pid): self.providers_by_id[str(pid)]
            for pid in provider_ids
            if str(pid) in self.providers_by_id
        }

    async def list_models(
        self,
        *,
        provider_id: UUID | None = None,  # noqa: ARG002
        kind: str | None = None,
        enabled_only: bool = True,  # noqa: ARG002
    ) -> list[ModelRow]:
        self.list_models_calls.append(kind)
        return [m for m in self.models if kind is None or m.kind == kind]

    async def get_model(self, model_id: UUID) -> ModelRow | None:
        for m in self.models:
            if m.id == model_id:
                return m
        return None

    async def get_provider(self, provider_id: UUID) -> ProviderRow | None:
        return self.providers_by_id.get(str(provider_id))


# ── Fixtures ─────────────────────────────────────────────────────────


def _provider(code: str, *, requires_prefix: bool = True) -> ProviderRow:
    return ProviderRow(
        id=uuid4(),
        name=code.title(),
        code=code,
        type="llm",
        base_url="https://example.test",
        auth_type="bearer",
        credentials_vault_path=None,
        enabled=True,
        metadata={},
        requires_prefix=requires_prefix,
    )


def _model(provider: ProviderRow, name: str, kind: str) -> ModelRow:
    return ModelRow(
        id=uuid4(),
        provider_id=provider.id,
        name=name,
        kind=kind,
        context_window=128_000,
        max_output_tokens=4096,
        input_price_per_1k_usd=Decimal("0.001"),
        output_price_per_1k_usd=Decimal("0.002"),
        supports_streaming=True,
        supports_tools=False,
        supports_vision=False,
        supports_json_mode=False,
        languages=("vi", "en"),
        enabled=True,
        metadata={},
        embedding_dimension=1280,
    )


def _binding(tenant: UUID, bot: UUID, purpose: str, model: ModelRow) -> BindingRow:
    return BindingRow(
        id=uuid4(),
        record_tenant_id=tenant,
        record_bot_id=bot,
        purpose=purpose,
        model_id=model.id,
        rank=0,
        variant=None,
        weight=100,
        temperature=0.0,
        max_tokens=512,
        top_p=1.0,
        extra_params={},
        active=True,
        version=1,
    )


def _world():
    """Build a model catalog mirroring the live SSoT names + a stub repo."""
    p_llm = _provider("openai", requires_prefix=False)
    p_ze = _provider("zeroentropy", requires_prefix=True)
    m_llm_default = _model(p_llm, "openai/claude", "llm")
    m_llm_other = _model(p_llm, "gpt-4.1-mini", "llm")
    m_emb = _model(p_ze, "zembed-1", "embedding")
    m_rrk = _model(p_ze, "zerank-2", "reranker")
    repo = _StubRepo(
        bindings_by_purpose={},
        models=[m_llm_default, m_llm_other, m_emb, m_rrk],
        providers_by_id={str(p_llm.id): p_llm, str(p_ze.id): p_ze},
    )
    return repo, m_llm_default, m_llm_other, m_emb, m_rrk


def _svc(repo, sysconfig) -> ModelResolverService:
    return ModelResolverService(
        repo=repo,  # type: ignore[arg-type]
        cache=_StubCache(),
        clock=_StubClock(),
        system_config=sysconfig,
    )


def _sysconfig() -> _StubSystemConfig:
    return _StubSystemConfig(
        {
            "llm_default_model": "openai/claude",
            "reranker_model": "zerank-2",
            "embedding_model": "zembed-1",
            "embedding_dimension": 1280,
        }
    )


# ── (a) resolve_llm NO binding → system_config default ───────────────


def test_resolve_llm_no_binding_falls_back_to_system_config_default() -> None:
    repo, m_llm_default, *_ = _world()
    sc = _sysconfig()
    svc = _svc(repo, sc)

    spec = asyncio.run(
        svc.resolve_llm(uuid4(), record_tenant_id=uuid4(), intent="llm_primary"),  # type: ignore[arg-type]
    )

    assert isinstance(spec, LLMSpec)
    # openai/claude already has a "/" → format_litellm_model passthrough.
    assert spec.model_name == "openai/claude"
    assert spec.provider == "openai"
    assert "llm_default_model" in sc.get_calls


def test_resolve_llm_no_binding_no_system_config_value_raises() -> None:
    """If system_config has no llm_default_model the resolver still raises
    (NullObject contract — no silent broken spec)."""
    repo, *_ = _world()
    sc = _StubSystemConfig({})  # empty SSoT
    svc = _svc(repo, sc)

    with pytest.raises(InvariantViolation):
        asyncio.run(
            svc.resolve_llm(uuid4(), record_tenant_id=uuid4(), intent="llm_primary"),  # type: ignore[arg-type]
        )


# ── (b) resolve_llm WITH binding → binding wins (no regression) ──────


def test_resolve_llm_with_binding_uses_binding_not_system_config() -> None:
    repo, _m_default, m_other, *_ = _world()
    tenant, bot = uuid4(), uuid4()
    repo.bindings_by_purpose[BindingPurpose.LLM_PRIMARY.value] = [
        _binding(tenant, bot, BindingPurpose.LLM_PRIMARY.value, m_other),
    ]
    sc = _sysconfig()
    svc = _svc(repo, sc)

    spec = asyncio.run(
        svc.resolve_llm(bot, record_tenant_id=tenant, intent="llm_primary"),  # type: ignore[arg-type]
    )

    assert isinstance(spec, LLMSpec)
    # Binding model (gpt-4.1-mini) wins over system_config (openai/claude).
    assert spec.model_name == "gpt-4.1-mini"
    # System_config must NOT be consulted when a binding exists.
    assert sc.get_calls == []


# ── (c) resolve_reranker NO binding → system_config default ──────────


def test_resolve_reranker_no_binding_falls_back_to_system_config() -> None:
    repo, *_rest, m_rrk = _world()
    sc = _sysconfig()
    svc = _svc(repo, sc)

    spec = asyncio.run(svc.resolve_reranker(uuid4(), record_tenant_id=uuid4()))

    assert isinstance(spec, RerankerSpec)
    assert spec.model_name == "zerank-2"
    assert spec.provider == "zeroentropy"
    assert "reranker_model" in sc.get_calls


# ── (d) embedding query path NEVER returns a non-embedding model ─────


def test_resolve_runtime_embedding_no_binding_never_cross_kind() -> None:
    """The cross-kind bug: resolve_runtime(purpose='embedding') with NO
    embedding binding previously fell back to llm_primary → handed an LLM
    to the embedder. It MUST resolve to a kind='embedding' model from
    system_config.embedding_model instead.
    """
    repo, m_llm_default, *_ = _world()
    tenant, bot = uuid4(), uuid4()
    # Bot HAS an llm_primary binding but NO embedding binding — the exact
    # shape that triggered the ZeroEntropy 404.
    repo.bindings_by_purpose[BindingPurpose.LLM_PRIMARY.value] = [
        _binding(tenant, bot, BindingPurpose.LLM_PRIMARY.value, m_llm_default),
    ]
    sc = _sysconfig()
    svc = _svc(repo, sc)

    cfg = asyncio.run(
        svc.resolve_runtime(tenant, bot, "embedding"),
    )

    assert cfg.kind == "embedding", "embedder must NEVER receive a non-embedding kind"
    assert cfg.wire_model_id == "zembed-1"
    assert "embedding_model" in sc.get_calls


def test_resolve_runtime_embedding_no_binding_no_sysconfig_raises() -> None:
    repo, *_ = _world()
    sc = _StubSystemConfig({})  # no embedding_model
    svc = _svc(repo, sc)

    with pytest.raises(InvariantViolation):
        asyncio.run(svc.resolve_runtime(uuid4(), uuid4(), "embedding"))


# ── (e) realtime: sysconfig change swaps model on next call ─────────


def test_resolve_llm_fallback_is_realtime_not_over_cached() -> None:
    """Flipping system_config.llm_default_model swaps the resolved model on
    the very next resolve_llm call — the fallback spec is NOT pinned in the
    resolver's own (longer-TTL) cache past the system_config read.
    """
    repo, *_ = _world()
    sc = _sysconfig()
    svc = _svc(repo, sc)
    tenant, bot = uuid4(), uuid4()

    spec1 = asyncio.run(
        svc.resolve_llm(bot, record_tenant_id=tenant, intent="llm_primary"),  # type: ignore[arg-type]
    )
    assert spec1.model_name == "openai/claude"

    # Operator flips the SSoT (UPDATE system_config) — Redis TTL elapsed.
    sc.values["llm_default_model"] = "gpt-4.1-mini"

    spec2 = asyncio.run(
        svc.resolve_llm(bot, record_tenant_id=tenant, intent="llm_primary"),  # type: ignore[arg-type]
    )
    assert spec2.model_name == "gpt-4.1-mini", "fallback must reflect realtime SSoT"
