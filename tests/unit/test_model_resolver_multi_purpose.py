"""Unit tests — :meth:`ModelResolverService.resolve_multi_purpose`.

ROI rationale (T2 case-study, 2026-05-18): live diagnostic showed
chat-worker boot fires 3 sequential resolver calls (llm + embedding +
rerank), each issuing one ``list_bindings`` SELECT. Collapsing into a
single ``purpose IN (...)`` query removes 2 of 3 sequential DB round-
trips on the cold-cache path.

The test surface verifies:

- ONE invocation of ``list_bindings_multi_purpose`` (not N).
- Spec types are purpose-correct (LLM / Embedding / Reranker).
- Per-purpose L1 cache is warmed so a follow-up single-purpose resolve
  skips the DB entirely.
- Missing purposes are omitted from the result (NOT KeyError).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
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


class _StubClock:
    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t


class _StubCache:
    """No-op CachePort (in-process L1 inside resolver still exercised)."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, *, ttl_s: int) -> None:  # noqa: ARG002
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@dataclass
class _StubRepo:
    """Records every call so the test can assert SINGLE multi-purpose
    SQL trip vs N sequential trips.
    """

    bindings_by_purpose: dict[str, list[BindingRow]]
    models_by_id: dict[str, ModelRow]
    providers_by_id: dict[str, ProviderRow]
    multi_call_count: int = 0
    single_call_count: int = 0
    models_call_count: int = 0
    providers_call_count: int = 0
    last_multi_purposes: list[str] = field(default_factory=list)

    async def list_bindings_multi_purpose(
        self,
        *,
        record_tenant_id: UUID,  # noqa: ARG002
        record_bot_id: UUID,  # noqa: ARG002
        purposes: list[str],
        active_only: bool = True,  # noqa: ARG002
    ) -> dict[str, list[BindingRow]]:
        self.multi_call_count += 1
        self.last_multi_purposes = list(purposes)
        return {p: list(self.bindings_by_purpose.get(p, [])) for p in purposes}

    async def list_bindings(
        self,
        *,
        record_tenant_id: UUID,  # noqa: ARG002
        record_bot_id: UUID,  # noqa: ARG002
        purpose: str | None = None,
        active_only: bool = True,  # noqa: ARG002
    ) -> list[BindingRow]:
        self.single_call_count += 1
        if purpose is None:
            out: list[BindingRow] = []
            for binds in self.bindings_by_purpose.values():
                out.extend(binds)
            return out
        return list(self.bindings_by_purpose.get(purpose, []))

    async def get_models_by_ids(
        self, model_ids: list[UUID],
    ) -> dict[str, ModelRow]:
        self.models_call_count += 1
        return {
            str(mid): self.models_by_id[str(mid)]
            for mid in model_ids
            if str(mid) in self.models_by_id
        }

    async def get_providers_by_ids(
        self, provider_ids: list[UUID],
    ) -> dict[str, ProviderRow]:
        self.providers_call_count += 1
        return {
            str(pid): self.providers_by_id[str(pid)]
            for pid in provider_ids
            if str(pid) in self.providers_by_id
        }


# ---------------------------------------------------------------------
# Fixtures (build a 3-purpose bot: llm_primary + embedding + rerank).
# ---------------------------------------------------------------------


def _provider(code: str) -> ProviderRow:
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
        requires_prefix=True,
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


def _binding(
    tenant_id: UUID, bot_id: UUID, purpose: str, model: ModelRow, *, rank: int = 0,
) -> BindingRow:
    return BindingRow(
        id=uuid4(),
        record_tenant_id=tenant_id,
        record_bot_id=bot_id,
        purpose=purpose,
        model_id=model.id,
        rank=rank,
        variant=None,
        weight=100,
        temperature=0.0,
        max_tokens=512,
        top_p=1.0,
        extra_params={},
        active=True,
        version=1,
    )


def _build_repo() -> tuple[_StubRepo, UUID, UUID]:
    tenant_id = uuid4()
    bot_id = uuid4()
    p_llm = _provider("openai")
    p_emb = _provider("zeroentropy")
    p_rrk = _provider("zeroentropy")
    m_llm = _model(p_llm, "gpt-4.1-mini", "chat")
    m_emb = _model(p_emb, "zembed-1", "embedding")
    m_rrk = _model(p_rrk, "zerank-2", "reranker")
    bindings = {
        BindingPurpose.LLM_PRIMARY.value: [
            _binding(tenant_id, bot_id, BindingPurpose.LLM_PRIMARY.value, m_llm),
        ],
        BindingPurpose.EMBEDDING.value: [
            _binding(tenant_id, bot_id, BindingPurpose.EMBEDDING.value, m_emb),
        ],
        BindingPurpose.RERANK.value: [
            _binding(tenant_id, bot_id, BindingPurpose.RERANK.value, m_rrk),
        ],
    }
    models = {str(m.id): m for m in (m_llm, m_emb, m_rrk)}
    providers = {str(p.id): p for p in (p_llm, p_emb, p_rrk)}
    return (
        _StubRepo(
            bindings_by_purpose=bindings,
            models_by_id=models,
            providers_by_id=providers,
        ),
        tenant_id,
        bot_id,
    )


def _new_service(repo: _StubRepo) -> ModelResolverService:
    return ModelResolverService(repo=repo, cache=_StubCache(), clock=_StubClock())  # type: ignore[arg-type]


# ---------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------


def test_resolve_multi_purpose_one_db_trip_for_three_purposes() -> None:
    """3 purposes → 1 list_bindings_multi_purpose + 1 models batch +
    1 providers batch (NOT 3× each).
    """
    repo, tenant_id, bot_id = _build_repo()
    svc = _new_service(repo)
    result = asyncio.run(
        svc.resolve_multi_purpose(
            bot_id,
            record_tenant_id=tenant_id,
            purposes=[
                BindingPurpose.LLM_PRIMARY.value,
                BindingPurpose.EMBEDDING.value,
                BindingPurpose.RERANK.value,
            ],
        ),
    )
    assert repo.multi_call_count == 1, "must collapse to one purpose IN query"
    assert repo.single_call_count == 0, "must NOT call single-purpose list_bindings"
    # Models + providers each batched once across all purposes (one IN
    # clause), not N × purposes.
    assert repo.models_call_count == 1
    assert repo.providers_call_count == 1
    assert set(result.keys()) == {
        BindingPurpose.LLM_PRIMARY.value,
        BindingPurpose.EMBEDDING.value,
        BindingPurpose.RERANK.value,
    }


def test_resolve_multi_purpose_returns_correct_spec_types() -> None:
    """Each purpose returns the correctly typed spec."""
    repo, tenant_id, bot_id = _build_repo()
    svc = _new_service(repo)
    result = asyncio.run(
        svc.resolve_multi_purpose(
            bot_id,
            record_tenant_id=tenant_id,
            purposes=[
                BindingPurpose.LLM_PRIMARY.value,
                BindingPurpose.EMBEDDING.value,
                BindingPurpose.RERANK.value,
            ],
        ),
    )
    assert isinstance(result[BindingPurpose.LLM_PRIMARY.value], LLMSpec)
    assert isinstance(result[BindingPurpose.EMBEDDING.value], EmbeddingSpec)
    assert isinstance(result[BindingPurpose.RERANK.value], RerankerSpec)


def test_resolve_multi_purpose_missing_purpose_omitted_not_raised() -> None:
    """Purpose with no binding is OMITTED from result (caller decides
    whether to treat as error)."""
    repo, tenant_id, bot_id = _build_repo()
    svc = _new_service(repo)
    result = asyncio.run(
        svc.resolve_multi_purpose(
            bot_id,
            record_tenant_id=tenant_id,
            purposes=[
                BindingPurpose.LLM_PRIMARY.value,
                "nonexistent_purpose",
            ],
        ),
    )
    assert BindingPurpose.LLM_PRIMARY.value in result
    assert "nonexistent_purpose" not in result


def test_resolve_multi_purpose_warms_single_purpose_cache() -> None:
    """After the multi-purpose call, a follow-up single-purpose
    ``resolve_reranker`` MUST hit the in-process L1 cache (zero new
    DB calls).
    """
    repo, tenant_id, bot_id = _build_repo()
    svc = _new_service(repo)
    asyncio.run(
        svc.resolve_multi_purpose(
            bot_id,
            record_tenant_id=tenant_id,
            purposes=[
                BindingPurpose.LLM_PRIMARY.value,
                BindingPurpose.EMBEDDING.value,
                BindingPurpose.RERANK.value,
            ],
        ),
    )
    baseline_multi = repo.multi_call_count
    baseline_single = repo.single_call_count
    baseline_models = repo.models_call_count

    # Follow-up single-purpose call should be a cache hit.
    spec = asyncio.run(
        svc.resolve_reranker(bot_id, record_tenant_id=tenant_id),
    )
    assert isinstance(spec, RerankerSpec)
    assert repo.multi_call_count == baseline_multi
    assert repo.single_call_count == baseline_single
    assert repo.models_call_count == baseline_models


def test_resolve_multi_purpose_empty_list_returns_empty_dict() -> None:
    """Empty purposes list short-circuits with NO DB call."""
    repo, tenant_id, bot_id = _build_repo()
    svc = _new_service(repo)
    result = asyncio.run(
        svc.resolve_multi_purpose(
            bot_id,
            record_tenant_id=tenant_id,
            purposes=[],
        ),
    )
    assert result == {}
    assert repo.multi_call_count == 0


def test_resolve_multi_purpose_partial_l1_hit_only_cold_purposes_queried() -> None:
    """When L1 has 1 purpose warm + 2 cold, only the cold 2 hit DB."""
    repo, tenant_id, bot_id = _build_repo()
    svc = _new_service(repo)
    # Warm just the LLM purpose via single-purpose call.
    asyncio.run(svc.resolve_llm(
        bot_id,
        record_tenant_id=tenant_id,
        intent="llm_primary",  # type: ignore[arg-type]
    ))
    baseline_single = repo.single_call_count
    assert baseline_single == 1

    # Multi-purpose request — LLM should be cache hit, the other two
    # cold → one multi-purpose query for purposes=[embedding, rerank].
    result = asyncio.run(
        svc.resolve_multi_purpose(
            bot_id,
            record_tenant_id=tenant_id,
            purposes=[
                BindingPurpose.LLM_PRIMARY.value,
                BindingPurpose.EMBEDDING.value,
                BindingPurpose.RERANK.value,
            ],
        ),
    )
    assert repo.multi_call_count == 1
    assert set(repo.last_multi_purposes) == {
        BindingPurpose.EMBEDDING.value,
        BindingPurpose.RERANK.value,
    }
    # All three purposes ended up in result.
    assert len(result) == 3
