"""W6-D12 — feedback-loop read-path wire smoke tests.

The P2-I gap report flagged three "built-but-not-wired" holes in the
D12 feedback loop:

* :mod:`admin_refuse_suggestions` route exists but is NOT included in
  the composed router → ``GET /admin/bots/{id}/refuse_suggestions`` 404.
* ``MessageFeedbackRepository.aggregate_per_bot`` (thumbs read) has zero
  callers → thumbs verdicts INSERT and then die unread.
* ``FAQCandidateService`` (refuse → cluster → FAQ-candidate) has zero
  call sites → the closing-the-loop refuse→FAQ flow never runs.

This module pins the wiring contract: route registration paths +
endpoint handlers reading the orphaned repo / service. It does NOT
exercise the DB / auth path (those have integration coverage); it
guards the URL + handler surface so a future cleanup that orphans one
of the three again fails loudly.
"""

from __future__ import annotations

import inspect

from ragbot.interfaces.http.router import router as composed_router
from ragbot.interfaces.http.routes import (
    admin_analytics,
    admin_refuse_suggestions,
)
from tests.unit._helpers_routes import leaf_paths


def _paths() -> set[str]:
    # FastAPI composes include_router(...) results as lazy _IncludedRouter
    # wrappers; the real leaf paths live one level down. Flatten the tree so
    # the assertion checks the SAME fully-prefixed paths the live app serves.
    return leaf_paths(composed_router.routes)


# ---------------------------------------------------------------------------
# Fix 1 — admin_refuse_suggestions route registered (was 404)
# ---------------------------------------------------------------------------
def test_refuse_suggestions_module_imported_in_router() -> None:
    """Composed router module must import + include the refuse module."""
    from ragbot.interfaces.http import router as router_module

    src = inspect.getsource(router_module)
    assert "admin_refuse_suggestions" in src, (
        "router.py must import admin_refuse_suggestions so its router is "
        "available to include_router(...)"
    )
    assert "include_router(admin_refuse_suggestions.router" in src


def test_refuse_suggestions_path_registered() -> None:
    """Composed app router must expose the refuse-suggestions path."""
    assert (
        "/api/ragbot/admin/bots/{bot_id}/refuse_suggestions" in _paths()
    )


# ---------------------------------------------------------------------------
# Fix 2 — aggregate_per_bot reader wired into analytics route
# ---------------------------------------------------------------------------
def test_feedback_aggregate_path_registered() -> None:
    """A read endpoint must surface the per-bot thumbs aggregate."""
    assert (
        "/api/ragbot/admin/analytics/bots/{record_bot_id}/feedback"
        in _paths()
    )


def test_feedback_aggregate_handler_calls_repo() -> None:
    """The handler must read MessageFeedbackRepository.aggregate_per_bot.

    Guards against re-orphaning the repo method — the whole point of the
    wire is that thumbs counts become reachable from HTTP.
    """
    src = inspect.getsource(admin_analytics.analytics_feedback_aggregate)
    assert "aggregate_per_bot" in src
    assert "message_feedback_repo" in src


# ---------------------------------------------------------------------------
# Fix 3 — FAQ-candidate generation wired into refuse route
# ---------------------------------------------------------------------------
def test_faq_candidates_path_registered() -> None:
    """The FAQ-candidate read endpoint must be reachable."""
    assert (
        "/api/ragbot/admin/bots/{bot_id}/faq_candidates" in _paths()
    )


def test_faq_candidates_handler_calls_service() -> None:
    """The handler must drive FAQCandidateService.find_candidates."""
    src = inspect.getsource(
        admin_refuse_suggestions.get_faq_candidates,
    )
    assert "FAQCandidateService" in src
    assert "find_candidates" in src
