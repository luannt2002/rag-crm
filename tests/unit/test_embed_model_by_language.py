"""F12 — detected document language influences embedding-model selection.

The ingest embedding-model resolver (``DocumentService._embedding_spec``)
must route to a language-appropriate embedding model when the operator
configures ``system_config.embedding_model_by_language`` ({lang: model}).
With no map configured (production default), the resolved spec must be
byte-identical to the pre-F12 single-model behaviour.

Domain-neutral: language codes + model names are arbitrary test data, no
brand / industry literal. Zero-hardcode: the default map comes from
``shared/constants`` (empty), the real map from system_config.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.services.document_service import DocumentService
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_MAX_BATCH,
    DEFAULT_EMBEDDING_MODEL_BY_LANGUAGE,
    DEFAULT_EMBEDDING_TASK_PASSAGE,
)

# Arbitrary, domain-neutral fixtures.
_DEFAULT_MODEL = "vendor/embed-default"
_DEFAULT_DIM = 1024
_DEFAULT_VERSION = "embed-default-v1"
_LANG_DEFAULT = "vi"
_LANG_OTHER = "en"
_OTHER_MODEL = "vendor/embed-other"


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.embedding.model_name = _DEFAULT_MODEL
    s.embedding.dimension = _DEFAULT_DIM
    s.embedding.model_version = _DEFAULT_VERSION
    return s


def _make_session_factory() -> MagicMock:
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()

    @asynccontextmanager
    async def _cm():
        yield mock_session

    return MagicMock(side_effect=lambda: _cm())


def _make_service(
    *,
    config_service: object | None,
    model_resolver: object | None = None,
) -> DocumentService:
    return DocumentService(
        session_factory=_make_session_factory(),
        embedder=MagicMock(),
        settings=_make_settings(),
        config_service=config_service,
        model_resolver=model_resolver,
    )


class _FakeConfig:
    """Minimal stand-in for SystemConfigService — only the keys the resolver
    touches. ``lang_map`` is what ``embedding_model_by_language`` returns.
    """

    def __init__(self, *, lang_map: object) -> None:
        self._lang_map = lang_map
        self.lang_map_reads = 0

    async def get(self, key: str, default=None):  # noqa: ANN001
        if key == "embedding_model":
            return _DEFAULT_MODEL
        if key == "embedding_model_version":
            return _DEFAULT_VERSION
        if key == "embedding_model_by_language":
            self.lang_map_reads += 1
            return self._lang_map if self._lang_map is not None else default
        return default

    async def get_int(self, key: str, default: int = 0) -> int:
        if key == "embedding_dimension":
            return _DEFAULT_DIM
        return default


def _resolver_spec(model_name: str = _DEFAULT_MODEL) -> EmbeddingSpec:
    return EmbeddingSpec(
        binding_id=uuid.uuid4(),
        model_name=model_name,
        provider="vendor",
        dimension=_DEFAULT_DIM,
        max_batch=DEFAULT_EMBEDDING_MAX_BATCH,
        model_version=_DEFAULT_VERSION,
        task=DEFAULT_EMBEDDING_TASK_PASSAGE,
    )


# ── default map is empty (SSoT) ──────────────────────────────────────────

def test_default_map_constant_is_empty() -> None:
    """Shipping default = no routing = byte-identical single-model behaviour."""
    assert DEFAULT_EMBEDDING_MODEL_BY_LANGUAGE == {}


# ── fallback (system_config) path ────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_lang_map_default_model_byte_identical() -> None:
    """No ``embedding_model_by_language`` row → resolved spec unchanged."""
    cfg = _FakeConfig(lang_map=None)  # key absent → resolver gets default {}
    svc = _make_service(config_service=cfg)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        language=_LANG_OTHER,
    )

    assert spec.model_name == _DEFAULT_MODEL
    assert spec.dimension == _DEFAULT_DIM
    assert spec.task == DEFAULT_EMBEDDING_TASK_PASSAGE


@pytest.mark.asyncio
async def test_empty_lang_map_default_model_byte_identical() -> None:
    """Explicit empty map → still no override."""
    cfg = _FakeConfig(lang_map={})
    svc = _make_service(config_service=cfg)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        language=_LANG_OTHER,
    )
    assert spec.model_name == _DEFAULT_MODEL


@pytest.mark.asyncio
async def test_language_none_is_passthrough() -> None:
    """``language=None`` (legacy callers) → map never consulted, no override."""
    cfg = _FakeConfig(lang_map={_LANG_OTHER: _OTHER_MODEL})
    svc = _make_service(config_service=cfg)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        language=None,
    )
    assert spec.model_name == _DEFAULT_MODEL
    assert cfg.lang_map_reads == 0


@pytest.mark.asyncio
async def test_language_not_in_map_keeps_default() -> None:
    """Map exists but has no entry for this language → no override."""
    cfg = _FakeConfig(lang_map={_LANG_OTHER: _OTHER_MODEL})
    svc = _make_service(config_service=cfg)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        language=_LANG_DEFAULT,  # not a key in the map
    )
    assert spec.model_name == _DEFAULT_MODEL


@pytest.mark.asyncio
async def test_configured_language_picks_its_model_fallback_path() -> None:
    """Per-language map entry → model NAME swapped, dims/task preserved."""
    cfg = _FakeConfig(lang_map={_LANG_OTHER: _OTHER_MODEL})
    svc = _make_service(config_service=cfg)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        language=_LANG_OTHER,
    )

    assert spec.model_name == _OTHER_MODEL, "language must pick its mapped model"
    # Vector-space invariants preserved (only the name is routed).
    assert spec.dimension == _DEFAULT_DIM
    assert spec.provider == "litellm"
    assert spec.model_version == _DEFAULT_VERSION
    assert spec.task == DEFAULT_EMBEDDING_TASK_PASSAGE


# ── resolver (per-bot binding) path ──────────────────────────────────────

@pytest.mark.asyncio
async def test_configured_language_picks_its_model_resolver_path() -> None:
    """Per-bot binding resolved, then language routes the model name."""
    resolver = MagicMock()
    resolver.resolve_embedding = AsyncMock(return_value=_resolver_spec())
    cfg = _FakeConfig(lang_map={_LANG_OTHER: _OTHER_MODEL})
    svc = _make_service(config_service=cfg, model_resolver=resolver)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        language=_LANG_OTHER,
    )
    assert spec.model_name == _OTHER_MODEL
    assert spec.dimension == _DEFAULT_DIM
    assert spec.task == DEFAULT_EMBEDDING_TASK_PASSAGE


@pytest.mark.asyncio
async def test_resolver_path_no_map_is_byte_identical() -> None:
    """Per-bot binding + no map → resolver spec returned unchanged."""
    base = _resolver_spec()
    resolver = MagicMock()
    resolver.resolve_embedding = AsyncMock(return_value=base)
    cfg = _FakeConfig(lang_map={})
    svc = _make_service(config_service=cfg, model_resolver=resolver)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        language=_LANG_OTHER,
    )
    assert spec.model_name == base.model_name
    assert spec.dimension == base.dimension


@pytest.mark.asyncio
async def test_mapped_name_equal_current_is_noop() -> None:
    """Map points the language at the SAME model → no spurious copy/swap."""
    cfg = _FakeConfig(lang_map={_LANG_OTHER: _DEFAULT_MODEL})
    svc = _make_service(config_service=cfg)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        language=_LANG_OTHER,
    )
    assert spec.model_name == _DEFAULT_MODEL


@pytest.mark.asyncio
async def test_no_config_service_is_passthrough() -> None:
    """``config_service=None`` → settings fallback, override is a no-op."""
    svc = _make_service(config_service=None)

    spec = await svc._embedding_spec(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
        language=_LANG_OTHER,
    )
    assert spec.model_name == _DEFAULT_MODEL
    assert spec.dimension == _DEFAULT_DIM
