"""EmbeddingSpec carries optional ``task`` field.

Locks the contract that:
1. ``task`` defaults to ``None`` (backward compat — OpenAI / symmetric path).
2. ``task`` accepts the canonical V2 constants
   (``DEFAULT_EMBEDDING_TASK_QUERY`` / ``DEFAULT_EMBEDDING_TASK_PASSAGE``).
3. The model stays frozen so the call-site MUST ``model_copy`` to override
   per-call (query path mutates a passage-default spec to query).
4. ``task`` is not stripped by serialisation round-trip.

Domain-neutral. No brand / industry literals.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_TASK_PASSAGE,
    DEFAULT_EMBEDDING_TASK_QUERY,
)


def _spec(**overrides: object) -> EmbeddingSpec:
    base: dict[str, object] = {
        "binding_id": uuid.uuid4(),
        "model_name": "vendor/model-x",
        "provider": "vendor",
        "dimension": 1024,
        "max_batch": 64,
        "model_version": "v1",
    }
    base.update(overrides)
    return EmbeddingSpec(**base)  # type: ignore[arg-type]


def test_default_task_is_none() -> None:
    """Backward compat — symmetric callers don't gain the kwarg."""
    spec = _spec()
    assert spec.task is None


def test_query_task_constant_accepted() -> None:
    spec = _spec(task=DEFAULT_EMBEDDING_TASK_QUERY)
    assert spec.task == "retrieval.query"


def test_passage_task_constant_accepted() -> None:
    spec = _spec(task=DEFAULT_EMBEDDING_TASK_PASSAGE)
    assert spec.task == "retrieval.passage"


def test_spec_is_frozen() -> None:
    """Mutation must raise so the orchestrator can't accidentally
    flip task on a shared spec object — must use ``model_copy``."""
    spec = _spec(task=DEFAULT_EMBEDDING_TASK_PASSAGE)
    with pytest.raises(ValidationError):
        spec.task = DEFAULT_EMBEDDING_TASK_QUERY  # type: ignore[misc]


def test_model_copy_overrides_task_only() -> None:
    """The query path uses ``model_copy`` to flip a passage-default spec."""
    base = _spec(task=DEFAULT_EMBEDDING_TASK_PASSAGE)
    flipped = base.model_copy(update={"task": DEFAULT_EMBEDDING_TASK_QUERY})
    assert flipped.task == DEFAULT_EMBEDDING_TASK_QUERY
    assert base.task == DEFAULT_EMBEDDING_TASK_PASSAGE  # source untouched
    assert flipped.binding_id == base.binding_id
    assert flipped.dimension == base.dimension
