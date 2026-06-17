"""LSP compliance tests — RerankerPort signature must match all implementations.

Phase 2 Y1 infra audit 2026-04-29: P0-BUG-2 fix verification.

Ensures that every concrete reranker class:
1. Has a `rerank` method with parameters that are a superset of the Port contract.
2. Exposes a `mode` property (observability identifier).
3. Has `health_check` and `close` async methods.
4. Is structurally compatible with `RerankerPort` via runtime_checkable.
"""

from __future__ import annotations

import inspect

import pytest

from ragbot.application.ports.reranker_port import RerankerPort
from ragbot.infrastructure.reranker.jina_reranker import JinaReranker
from ragbot.infrastructure.reranker.litellm_reranker import LiteLLMReranker
from ragbot.infrastructure.reranker.null_reranker import NullReranker

# We test the 3 instantiable impls only; ViRankerLocalReranker raises on init
_IMPL_CLASSES = [NullReranker, LiteLLMReranker, JinaReranker]

# Minimum parameter set declared by the Port (excluding 'self')
_PORT_RERANK_PARAMS = set(
    inspect.signature(RerankerPort.rerank).parameters.keys()
) - {"self"}


@pytest.mark.parametrize("impl_cls", _IMPL_CLASSES)
def test_rerank_signature_superset_of_port(impl_cls: type) -> None:
    """impl.rerank must accept ALL params that RerankerPort.rerank declares."""
    impl_params = set(inspect.signature(impl_cls.rerank).parameters.keys()) - {"self"}
    missing = _PORT_RERANK_PARAMS - impl_params
    assert not missing, (
        f"{impl_cls.__name__}.rerank is missing Port params: {missing}. "
        "Port contract requires: query, chunks, top_n, model."
    )


@pytest.mark.parametrize("impl_cls", _IMPL_CLASSES)
def test_rerank_return_annotation_is_list(impl_cls: type) -> None:
    """impl.rerank return annotation must be list (of dicts), not list[float]."""
    sig = inspect.signature(impl_cls.rerank)
    ret = sig.return_annotation
    # Accept list[dict[...]] or the generic list alias; reject float-list old sig.
    assert ret is not inspect.Parameter.empty, f"{impl_cls.__name__}.rerank lacks return annotation"
    ret_str = str(ret)
    assert "float" not in ret_str, (
        f"{impl_cls.__name__}.rerank return annotation contains 'float': {ret_str!r}. "
        "Port contract expects list[dict[str, Any]] not list[float]."
    )


@pytest.mark.parametrize("impl_cls", _IMPL_CLASSES)
def test_mode_property_exists(impl_cls: type) -> None:
    """impl must expose a `mode` property for observability."""
    assert hasattr(impl_cls, "mode"), f"{impl_cls.__name__} is missing `mode` property"


def test_null_reranker_mode_value() -> None:
    """NullReranker.mode must return 'null'."""
    r = NullReranker()
    assert r.mode == "null"


def test_litellm_reranker_mode_value() -> None:
    """LiteLLMReranker.mode must encode provider and model."""
    from ragbot.shared.constants import DEFAULT_RERANK_MODEL
    r = LiteLLMReranker()
    assert r.mode.startswith("litellm:")
    assert DEFAULT_RERANK_MODEL in r.mode


def test_jina_reranker_mode_value() -> None:
    """JinaReranker.mode must encode 'jina:' prefix."""
    r = JinaReranker(api_key="test-key-placeholder")
    assert r.mode.startswith("jina:")


@pytest.mark.parametrize("impl_cls", _IMPL_CLASSES)
def test_has_health_check_and_close(impl_cls: type) -> None:
    """impl must have async health_check and close methods."""
    assert hasattr(impl_cls, "health_check"), f"{impl_cls.__name__} missing health_check"
    assert hasattr(impl_cls, "close"), f"{impl_cls.__name__} missing close"
    assert inspect.iscoroutinefunction(impl_cls.health_check)
    assert inspect.iscoroutinefunction(impl_cls.close)
