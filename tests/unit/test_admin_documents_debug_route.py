"""Smoke tests for the renamed admin debug-view route.

Verifies:

1. Module import (the rename from ``admin_documents_md`` →
   ``admin_documents_debug`` did not orphan an import).
2. Route is registered at the generic ``/debug-view`` path (the old
   ``/markdown`` suffix violated the no-version-ref rule by encoding a
   format in the URL).
3. ``format`` query param defaults to ``DEFAULT_DEBUG_VIEW_FORMAT`` and
   only accepts values in ``DEBUG_VIEW_FORMATS_ALLOWED``.

No DB / auth path is exercised here — those have their own integration
tests; this module guards the URL contract surface.
"""

from __future__ import annotations

import inspect

from ragbot.interfaces.http.router import router as composed_router
from ragbot.interfaces.http.routes import admin_documents_debug
from ragbot.shared.constants import (
    DEBUG_VIEW_FORMATS_ALLOWED,
    DEFAULT_DEBUG_VIEW_FORMAT,
)
from tests.unit._helpers_routes import leaf_paths


def test_renamed_module_is_importable() -> None:
    """The renamed module must expose a FastAPI ``router`` symbol."""
    assert hasattr(admin_documents_debug, "router")
    assert hasattr(admin_documents_debug, "get_document_debug_view")


def test_debug_view_route_registered_on_app_router() -> None:
    """Composed app router must include the new generic path."""
    # FastAPI lazy-composes include_router(...) as _IncludedRouter wrappers;
    # flatten to the real leaf paths the live app serves.
    paths = leaf_paths(composed_router.routes)
    assert "/api/ragbot/admin/documents/{document_id}/debug-view" in paths


def test_old_markdown_path_is_gone() -> None:
    """Removing the format suffix is mandatory — the old URL must not exist."""
    paths = leaf_paths(composed_router.routes)
    assert "/api/ragbot/admin/documents/{document_id}/markdown" not in paths


def test_format_default_matches_constant() -> None:
    """The endpoint signature must default ``format`` to the SSoT constant."""
    sig = inspect.signature(admin_documents_debug.get_document_debug_view)
    fmt_param = sig.parameters["format"]
    # FastAPI Query(default=...) keeps the underlying str default reachable
    # via ``.default.default``; older versions store it directly.
    raw_default = fmt_param.default
    actual = getattr(raw_default, "default", raw_default)
    assert actual == DEFAULT_DEBUG_VIEW_FORMAT


def test_allowed_formats_is_non_empty_frozenset() -> None:
    """Guard against accidental empty-set regression that would 422 everything."""
    assert isinstance(DEBUG_VIEW_FORMATS_ALLOWED, frozenset)
    assert DEFAULT_DEBUG_VIEW_FORMAT in DEBUG_VIEW_FORMATS_ALLOWED
    assert len(DEBUG_VIEW_FORMATS_ALLOWED) >= 1
