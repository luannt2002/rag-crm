"""Pin tests — /api/ragbot/test/chat must honour caller's ``connect_id``.

Prior to the 2026-05-26 fix, ``test_chat.py`` hard-coded the route
``connect_id = DEFAULT_CONNECT_ID`` (ignoring whatever the caller put
in the request body). Effect: every harness room shared the same
``chat_histories`` row stream → cross-test history pollution → load
tests for *different* questions silently reused each other's prior
turns and produced false HALLU-looking artefacts.

The route now ships ``connect_id: str | None`` in
``TestChatRequest`` and resolves it via
``req.connect_id or DEFAULT_CONNECT_ID``, so the demo UI (which
omits the field) keeps working unchanged while harness callers can
pass a unique slug per test room.

This module pins the contract at three levels:

1. The Pydantic model carries the optional field.
2. The route source uses ``req.connect_id or DEFAULT_CONNECT_ID``
   rather than the previous hard-coded constant assignment.
3. The constant ``DEFAULT_CONNECT_ID`` still exists for the demo UI
   fallback and is imported by the route module.
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_ROUTE_PATH: Path = (
    _REPO_ROOT
    / "src"
    / "ragbot"
    / "interfaces"
    / "http"
    / "routes"
    / "test_chat"
    / "chat_routes.py"
)


def _route_src() -> str:
    return _ROUTE_PATH.read_text(encoding="utf-8")


def test_test_chat_request_declares_optional_connect_id() -> None:
    """Pydantic body model must expose ``connect_id: str | None``."""
    from ragbot.interfaces.http.routes.test_chat import TestChatRequest

    fields = TestChatRequest.model_fields
    assert "connect_id" in fields, (
        "TestChatRequest is missing the optional connect_id field — "
        "harness rooms cannot isolate without it."
    )
    annotation = fields["connect_id"].annotation
    # Accept either ``str | None`` or ``Optional[str]`` forms.
    assert "str" in repr(annotation) and "None" in repr(annotation), (
        f"connect_id must be ``str | None``; got {annotation!r}"
    )


def test_route_no_longer_hardcodes_connect_id_assignment() -> None:
    """The route source must not contain the old hard-coded assignment
    ``connect_id = DEFAULT_CONNECT_ID`` (the pre-fix pattern). The
    correct pattern is ``req.connect_id or DEFAULT_CONNECT_ID``.
    """
    src = _route_src()
    # Look for the exact bug pattern as a stand-alone line (not inside
    # a longer expression like ``req.connect_id or DEFAULT_CONNECT_ID``).
    bad_lines = [
        line
        for line in src.splitlines()
        if line.strip() == "connect_id = DEFAULT_CONNECT_ID"
    ]
    assert not bad_lines, (
        "Found hard-coded ``connect_id = DEFAULT_CONNECT_ID`` — must use "
        "``req.connect_id or DEFAULT_CONNECT_ID`` so harness rooms can "
        "supply their own slug."
    )


def test_route_uses_caller_supplied_connect_id_with_fallback() -> None:
    """The route must explicitly fall back to ``DEFAULT_CONNECT_ID`` so
    the demo UI (which omits the field) keeps working unchanged.
    """
    src = _route_src()
    assert "req.connect_id or DEFAULT_CONNECT_ID" in src, (
        "Route must resolve connect_id via the standard "
        "``req.connect_id or DEFAULT_CONNECT_ID`` fallback pattern."
    )


def test_default_connect_id_constant_still_imported() -> None:
    """The DEFAULT_CONNECT_ID constant must remain importable for the
    fallback path (zero-hardcode rule: constants live in
    ``shared/constants.py``).
    """
    from ragbot.shared.constants import DEFAULT_CONNECT_ID

    assert isinstance(DEFAULT_CONNECT_ID, str)
    assert DEFAULT_CONNECT_ID  # non-empty
