"""Wave-2 Cluster C4 — feedback router wire smoke tests.

The handoff (CODER_HANDOFF_PROMPT.md §❹ C4) requires Wire Option A:
``/feedback`` (legacy, in chat.router) AND ``/feedback/thumbs`` (new
analytics endpoint, in feedback.router) coexist on the same prefix.
This module pins the router state so a future cleanup that removes one
of the two paths fails loudly.
"""
from __future__ import annotations

import inspect


def test_feedback_module_imported_in_router() -> None:
    """The composite router must import the feedback module, not just
    the legacy chat router (which carries /feedback)."""
    from ragbot.interfaces.http import router as router_module

    src = inspect.getsource(router_module)
    assert "feedback," in src or "feedback\n" in src, (
        "router.py must import the feedback module so its router is "
        "available to include_router(...)"
    )
    assert "include_router(feedback.router" in src, (
        "router.py must call include_router(feedback.router, ...) so "
        "POST /feedback/thumbs is reachable"
    )


def test_thumbs_path_registered() -> None:
    """Composite router must expose POST /api/ragbot/feedback/thumbs."""
    from ragbot.interfaces.http.router import router

    from tests.unit._helpers_routes import leaf_method_paths

    paths = leaf_method_paths(router.routes)
    assert (("POST",), "/api/ragbot/feedback/thumbs") in paths, (
        f"POST /api/ragbot/feedback/thumbs not registered. "
        f"Registered paths sample: {sorted(paths)[:8]}"
    )


def test_baseline_feedback_path_still_registered() -> None:
    """Wire Option A: the baseline /feedback path must remain so the
    existing UI rating button keeps working alongside the new analytics
    endpoint."""
    from ragbot.interfaces.http.router import router

    from tests.unit._helpers_routes import leaf_method_paths

    paths = leaf_method_paths(router.routes)
    assert (("POST",), "/api/ragbot/feedback") in paths, (
        "POST /api/ragbot/feedback removed — Wire Option A requires both "
        "endpoints to coexist (legacy rating + new thumbs analytics)"
    )


def test_container_exposes_message_feedback_repo() -> None:
    """The C4 wire is incomplete without the DI provider — the route
    pulls ``container.message_feedback_repo()`` so the provider must be
    declared on the Container class."""
    from ragbot.bootstrap import Container

    container = Container()
    provider = getattr(container, "message_feedback_repo", None)
    assert provider is not None, (
        "Container.message_feedback_repo provider missing — feedback.router "
        "would NameError at request time"
    )
    # Provider type is a Factory (per-request instance).
    assert "Factory" in type(provider).__name__


def test_container_exposes_bot_registry_service() -> None:
    """Same contract as the chat route — feedback.thumbs needs a 4-key
    bot lookup before writing to message_feedback."""
    from ragbot.bootstrap import Container

    container = Container()
    assert getattr(container, "bot_registry_service", None) is not None
