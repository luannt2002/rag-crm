"""W6-D12 — behavioural tests for the feedback-loop read handlers.

Drive the two wired handlers directly with a stubbed DI container:

* :func:`admin_analytics.analytics_feedback_aggregate` must read
  ``MessageFeedbackRepository.aggregate_per_bot`` and surface the
  thumbs_up / thumbs_down counts (the previously-orphaned read path).
* :func:`admin_refuse_suggestions.get_faq_candidates` must build a
  :class:`FAQCandidateService` from the container + per-bot embedding
  spec, call ``find_candidates``, and serialise the clusters.

No live DB — repo / service are stubbed at the container seam. Tenant
scope (``record_tenant_id`` from JWT) + RBAC level-60 gate are pinned.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from ragbot.interfaces.http.routes import admin_analytics, admin_refuse_suggestions
from ragbot.shared.constants import (
    FEEDBACK_VERDICT_THUMBS_DOWN,
    FEEDBACK_VERDICT_THUMBS_UP,
)
from ragbot.shared.errors import ForbiddenError


def _make_request(*, container: Any, role: str = "admin", tenant: Any = "set") -> Any:
    """Minimal Request stub exposing app.state.container + state.role/tenant."""
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    record_tenant_id = uuid4() if tenant == "set" else None
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(role=role, record_tenant_id=record_tenant_id),
    )


# ---------------------------------------------------------------------------
# Fix 2 — thumbs aggregate read
# ---------------------------------------------------------------------------
class _FakeFeedbackRepo:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.captured: dict[str, Any] = {}

    async def aggregate_per_bot(self, **kwargs: Any) -> dict[str, int]:
        self.captured = dict(kwargs)
        return self._counts


@pytest.mark.asyncio
async def test_feedback_aggregate_surfaces_counts() -> None:
    counts = {FEEDBACK_VERDICT_THUMBS_UP: 7, FEEDBACK_VERDICT_THUMBS_DOWN: 3}
    repo = _FakeFeedbackRepo(counts)
    container = MagicMock()
    container.message_feedback_repo = MagicMock(return_value=repo)
    request = _make_request(container=container)
    bot_id = uuid4()

    resp = await admin_analytics.analytics_feedback_aggregate(
        request, record_bot_id=bot_id, since_days=14,
    )

    assert resp["ok"] is True
    assert resp["data"] == counts
    assert resp["record_bot_id"] == str(bot_id)
    assert resp["since_days"] == 14
    # RLS contract: tenant from JWT + bot id forwarded to the repo.
    assert repo.captured["record_tenant_id"] == request.state.record_tenant_id
    assert repo.captured["record_bot_id"] == bot_id
    assert repo.captured["since_days"] == 14


@pytest.mark.asyncio
async def test_feedback_aggregate_rejects_low_role() -> None:
    container = MagicMock()
    request = _make_request(container=container, role="guest")
    with pytest.raises(ForbiddenError):
        await admin_analytics.analytics_feedback_aggregate(
            request, record_bot_id=uuid4(),
        )
    # Repo never touched when RBAC rejects.
    container.message_feedback_repo.assert_not_called()


# ---------------------------------------------------------------------------
# Fix 3 — FAQ candidates from refuse
# ---------------------------------------------------------------------------
class _FakeCandidate:
    def __init__(self, cid: str, count: int) -> None:
        self.cluster_id = cid
        self.representative_question = f"q-{cid}"
        self.sample_questions = [f"q-{cid}", f"q2-{cid}"]
        self.occurrence_count = count
        self.avg_top_score = 0.42


def _faq_container(captured: dict[str, Any]) -> Any:
    """Container whose model_resolver stub records the resolve call.

    The route-local FAQCandidateService symbol is patched via monkeypatch
    in the test; here we only assemble the container seams the handler
    reads (session_factory, embedder, model_resolver).
    """
    container = MagicMock()
    container.session_factory = MagicMock(return_value=MagicMock())
    container.embedder = MagicMock(return_value=MagicMock())

    spec = object()

    class _Resolver:
        async def resolve_embedding(self, bot_id: Any, *, record_tenant_id: Any) -> Any:
            captured["resolve_bot_id"] = bot_id
            captured["resolve_tenant"] = record_tenant_id
            return spec

    container.model_resolver = MagicMock(return_value=_Resolver())
    return container


@pytest.mark.asyncio
async def test_faq_candidates_from_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    candidates = [_FakeCandidate("0001", 5), _FakeCandidate("0002", 3)]
    container = _faq_container(captured)

    class _FakeService:
        def __init__(self, **kwargs: Any) -> None:
            captured["service_kwargs"] = kwargs

        async def find_candidates(self, **kwargs: Any) -> list[Any]:
            captured["find_kwargs"] = kwargs
            return candidates

    monkeypatch.setattr(
        admin_refuse_suggestions, "FAQCandidateService", _FakeService,
    )
    request = _make_request(container=container)
    bot_id = uuid4()

    out = await admin_refuse_suggestions.get_faq_candidates(
        request, bot_id=bot_id, since_days=7, min_occurrences=3,
    )

    assert [c.cluster_id for c in out] == ["0001", "0002"]
    assert out[0].occurrence_count == 5
    assert out[0].representative_question == "q-0001"
    # Per-bot embedding spec resolved + forwarded.
    assert captured["resolve_bot_id"] == bot_id
    assert captured["resolve_tenant"] == request.state.record_tenant_id
    assert captured["service_kwargs"]["embedding_spec"] is not None
    # Tenant + bot scope forwarded to the service.
    assert captured["find_kwargs"]["record_bot_id"] == bot_id
    assert captured["find_kwargs"]["record_tenant_id"] == request.state.record_tenant_id
    assert captured["find_kwargs"]["min_occurrences"] == 3


@pytest.mark.asyncio
async def test_faq_candidates_missing_tenant_rejected() -> None:
    container = MagicMock()
    request = _make_request(container=container, role="admin", tenant="none")
    with pytest.raises(HTTPException) as exc:
        await admin_refuse_suggestions.get_faq_candidates(
            request, bot_id=uuid4(),
        )
    assert exc.value.status_code == 400
