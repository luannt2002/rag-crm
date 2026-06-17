"""P26 L3 — query vs ingest embedding model consistency detector.

``_check_embed_model_consistency`` compares ``spec.model_name`` resolved
at query time (``model_resolver`` per-bot binding) against the ingest
default recorded in ``pipeline_config["embedding_model"]`` (populated from
``system_config``). Any divergence means the retrieval vector and the
indexed vectors live in different spaces — a silent quality regression.

The helper is detection-only: it bumps a Prometheus counter + emits a
warning log, then returns. Retrieval continues with the resolved spec —
MUST NOT raise or mutate state.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ragbot.infrastructure.observability.metrics import (
    embedding_model_mismatch_total,
)
from ragbot.orchestration.query_graph import _check_embed_model_consistency


def _make_state(*, ingest_model: str, bot_id: str = "bot-xyz") -> dict:
    """GraphState-shaped dict with pipeline_config.embedding_model set."""
    return {
        "pipeline_config": {"embedding_model": ingest_model},
        "record_bot_id": bot_id,
    }


def _make_spec(model_name: str) -> SimpleNamespace:
    """Minimal EmbeddingSpec-shaped stub — only model_name is read."""
    return SimpleNamespace(model_name=model_name)


def test_mismatch_logs_warning() -> None:
    """Ingest=small, query=large → warning fired with expected event key."""
    state = _make_state(ingest_model="text-embedding-3-small")
    spec = _make_spec("text-embedding-3-large")
    log = MagicMock()

    mismatch = _check_embed_model_consistency(state, spec, log)

    assert mismatch is True
    assert log.warning.called, "expected logger.warning call on mismatch"
    call_args = log.warning.call_args
    assert call_args.args[0] == "embedding_model_mismatch_query_vs_ingest"
    kwargs = call_args.kwargs
    assert kwargs["resolved_at_query"] == "text-embedding-3-large"
    assert kwargs["system_config_ingest_default"] == "text-embedding-3-small"
    assert kwargs["record_bot_id"] == "bot-xyz"
    assert kwargs["action"] == "using_resolved_model"


def test_match_no_warning() -> None:
    """Ingest model == resolved model → helper returns False, no warning."""
    state = _make_state(ingest_model="text-embedding-3-small")
    spec = _make_spec("text-embedding-3-small")
    log = MagicMock()

    mismatch = _check_embed_model_consistency(state, spec, log)

    assert mismatch is False
    log.warning.assert_not_called()


def test_missing_pcfg_skips_check() -> None:
    """Empty pipeline_config.embedding_model → bootstrap / unknown ingest
    default. Helper must return False and emit nothing (no false positives).
    """
    state = _make_state(ingest_model="")
    spec = _make_spec("text-embedding-3-large")
    log = MagicMock()

    before = embedding_model_mismatch_total.labels(
        expected="",
        resolved="text-embedding-3-large",
    )._value.get()

    mismatch = _check_embed_model_consistency(state, spec, log)

    after = embedding_model_mismatch_total.labels(
        expected="",
        resolved="text-embedding-3-large",
    )._value.get()

    assert mismatch is False
    log.warning.assert_not_called()
    assert after == before, "counter must not increment when ingest default is empty"


def test_counter_increments_on_mismatch() -> None:
    """Prometheus counter for (expected, resolved) pair grows by exactly 1."""
    expected = "text-embedding-3-small"
    resolved = "bge-m3"
    state = _make_state(ingest_model=expected)
    spec = _make_spec(resolved)
    log = MagicMock()

    before = embedding_model_mismatch_total.labels(
        expected=expected,
        resolved=resolved,
    )._value.get()

    _check_embed_model_consistency(state, spec, log)

    after = embedding_model_mismatch_total.labels(
        expected=expected,
        resolved=resolved,
    )._value.get()

    assert after == before + 1
