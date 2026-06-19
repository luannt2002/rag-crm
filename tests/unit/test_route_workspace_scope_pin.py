"""Architectural fitness function — pins the 4-key workspace contract on routes.

WHY: when the per-bot ``workspace_id`` feature landed (alembic 0213, demo bots
moved spa/xe/legal), the MAIN paths (chat, add_document, upload) were made
workspace-aware but 8 secondary endpoints were forgotten — they resolve a bot by
``(bot_id, channel_type)`` WITHOUT ``workspace_id``, so they 404 for any bot
living in a non-default workspace. That is "feature drift": per-route enforcement
of a cross-cutting contract always drifts.

This is the DETECTIVE guard (best-practice 2026: architectural fitness function /
"locked-by-test", same discipline as ``test_sysprompt_assembler_pin.py``). It
walks ``app.routes`` and asserts: every route whose path carries BOTH ``{bot_id}``
and ``{channel_type}`` (the 2-key external resolve that, post-0213, REQUIRES the
workspace to disambiguate) MUST accept ``workspace_id`` — either as a direct
handler param OR as a field on a Pydantic body model. A new endpoint that forgets
it fails CI here, so the contract cannot silently drift again.

Resolving the contract correctly required two non-obvious things the naive
``inspect.signature`` check gets wrong (verified 2026-06-13):
  * ``from __future__ import annotations`` stringifies annotations → must
    ``typing.get_type_hints`` to see the real type.
  * ``workspace_id`` usually lives as a FIELD inside a Pydantic body model
    (e.g. ``AddDocumentRequest``), not as a bare param → must inspect
    ``model_fields``.

Structural fix (router-level ``Depends(resolve_bot_4key)`` + RLS GUC) is the
long-term ideal; this guard is the cheap, immediate backstop that makes the
drift impossible to merge.
"""
from __future__ import annotations

import inspect
import typing

import pytest

from ragbot.interfaces.http.app import create_app

from tests.unit._helpers_routes import iter_leaf_routes

# Routes intentionally exempt from the workspace contract (must be justified).
# A route is exempt ONLY when it does NOT resolve a bot from the DB by
# (bot_id, channel_type) — i.e. nothing that could match the wrong workspace.
_EXEMPT: dict[str, str] = {
    "bot_detail_page": "serves a static HTML template (read_text); bot data is "
                       "loaded client-side via the API, no server DB bot lookup",
    "quality_dashboard": "reads golden_set result FILES by bot_id slug, not a DB "
                         "bot row — no (bot_id, channel) DB resolution",
    "get_callback_format": "returns a static callback-format spec; no bot DB lookup",
}


def _carries_workspace(endpoint) -> bool:
    """True if the handler accepts workspace_id (direct param or body-model field)."""
    sig = inspect.signature(endpoint)
    if "workspace_id" in sig.parameters:
        return True
    try:
        hints = typing.get_type_hints(endpoint)
    except Exception:  # noqa: BLE001 — annotation resolution is best-effort
        hints = {}
    for name, param in sig.parameters.items():
        ann = hints.get(name, param.annotation)
        model_fields = getattr(ann, "model_fields", None)
        if model_fields and "workspace_id" in model_fields:
            return True
    return False


def _bot_channel_routes():
    app = create_app()
    out = []
    # FastAPI lazy-composes include_router(...) as _IncludedRouter wrappers;
    # flatten to the real leaf routes (path + resolved endpoint) the live app
    # serves so the fitness function inspects every bot-channel handler.
    for lr in iter_leaf_routes(app.routes):
        path = lr.path
        if "{bot_id}" in path and "{channel_type}" in path:
            ep = lr.endpoint
            if ep is not None:
                out.append((ep.__name__, ep, path))
    return out


def test_every_bot_channel_route_is_workspace_aware():
    """Every /{bot_id}/{channel_type} route must accept workspace_id (4-key contract)."""
    drift = [
        (name, path)
        for name, ep, path in _bot_channel_routes()
        if name not in _EXEMPT and not _carries_workspace(ep)
    ]
    assert not drift, (
        "Workspace-drift: these /{bot_id}/{channel_type} routes resolve a bot "
        "without workspace_id and will 404 for workspace-scoped bots. Add a "
        "workspace_id param (or a body model carrying it) and pass it to "
        "_find_bot_uuid(...), or justify an entry in _EXEMPT:\n"
        + "\n".join(f"  - {n}  ({p})" for n, p in sorted(drift))
    )


def test_guard_actually_inspects_routes():
    """Sanity: the guard found bot-channel routes to inspect (not a no-op)."""
    assert _bot_channel_routes(), "no /{bot_id}/{channel_type} routes found — guard is inert"
