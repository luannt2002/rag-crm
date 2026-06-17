"""Regression test for mega-sprint-G21 — no redundant Redis round-trips
in chat_worker after the batched ``get_many`` snapshot.

Bug: 3 ``await _cfg_svc.get_int / get`` calls in
``chat_worker._dispatch_chat`` immediately after the batched
``get_many`` were issuing 3 extra Redis round-trips per chat turn for
keys (``rag_rerank_top_n``, ``grounding_check_enabled``,
``graph_rag_default_mode``) that were already in the batched snapshot.
Two of the three (``rerank_top_n``, ``grounding``) were even
overwriting the exact values just assigned via the snapshot helpers —
pure waste.

Fix: source all three values from the ``_cfg`` snapshot via the
existing ``_cfg_int / _cfg_get`` helpers. Net: -3 Redis hits per turn,
-3 await-suspend points on the request hot path.

Pre-fix: 3 ``await _cfg_svc.get*("rag_rerank_top_n" | "grounding_check_enabled" |
"graph_rag_default_mode", ...)`` calls in chat_worker.
Post-fix: zero such calls — values lifted from the ``_cfg`` snapshot.
"""
from __future__ import annotations

import re
from pathlib import Path

_CW_DIR = (
    Path(__file__).resolve().parents[2]
    / "src" / "ragbot" / "interfaces" / "workers" / "chat_worker"
)


class _CW_PATH:
    """Reads + concatenates every module in the ``chat_worker`` package.

    The worker was split from a single ``chat_worker.py`` into a package;
    these static-text guards now scan the whole package so a grepped
    pattern is found regardless of which sub-module it landed in.
    """

    @staticmethod
    def read_text(encoding: str = "utf-8") -> str:
        return "\n".join(
            p.read_text(encoding=encoding) for p in sorted(_CW_DIR.glob("*.py"))
        )


def test_no_post_batch_cfg_svc_get_for_batched_keys() -> None:
    """The 3 wasted ``await _cfg_svc.get*("<batched-key>", ...)`` calls
    must be gone — they double-fetched values already in ``_cfg``.
    """
    src = _CW_PATH.read_text(encoding="utf-8")
    redundant_keys = (
        "rag_rerank_top_n",
        "grounding_check_enabled",
        "graph_rag_default_mode",
    )
    for key in redundant_keys:
        # Match patterns like ``await _cfg_svc.get_int("rag_rerank_top_n", ...)``
        # or ``await _cfg_svc.get("grounding_check_enabled", ...)``
        pattern = re.compile(
            r"await\s+_cfg_svc\.get(?:_int|_bool|_float)?\(\s*[\"']"
            + re.escape(key) + r"[\"']",
        )
        match = pattern.search(src)
        assert match is None, (
            f"chat_worker.py still issues an extra Redis round-trip for "
            f"key {key!r} via _cfg_svc.get* — value is already in the "
            f"batched _cfg snapshot. Remove the redundant await call."
        )


def test_batched_keys_still_pulled_via_cfg_helpers() -> None:
    """Defence: the values must still be sourced from the snapshot
    (no silent removal that would default everything downstream).
    """
    src = _CW_PATH.read_text(encoding="utf-8")
    assert '_cfg_int(_cfg, "rag_rerank_top_n"' in src, (
        "rag_rerank_top_n must be lifted from the _cfg snapshot via _cfg_int."
    )
    assert '_cfg_get(\n            _cfg, "grounding_check_enabled"' in src \
        or '_cfg_get(\n        _cfg, "grounding_check_enabled"' in src \
        or '_cfg_get(_cfg, "grounding_check_enabled"' in src, (
        "grounding_check_enabled must be lifted from the _cfg snapshot via _cfg_get."
    )
    assert '_cfg_get(_cfg, "graph_rag_default_mode"' in src, (
        "graph_rag_default_mode must be lifted from the _cfg snapshot via _cfg_get."
    )


def test_get_many_batched_call_present() -> None:
    """The single ``get_many`` round-trip must still anchor the snapshot;
    if it is removed the assertions above become meaningless.
    """
    src = _CW_PATH.read_text(encoding="utf-8")
    assert "await _cfg_svc.get_many(list(_CHAT_CONFIG_KEYS))" in src, (
        "Batched _cfg_svc.get_many(list(_CHAT_CONFIG_KEYS)) must remain "
        "the single source for the chat-worker config snapshot."
    )


def test_get_many_called_exactly_once_in_dispatch() -> None:
    """``get_many(list(_CHAT_CONFIG_KEYS))`` must appear exactly once in
    the worker source — guards against accidental re-fetch in the same
    request scope (would defeat the perf invariant).
    """
    src = _CW_PATH.read_text(encoding="utf-8")
    occurrences = src.count("get_many(list(_CHAT_CONFIG_KEYS))")
    assert occurrences == 1, (
        f"get_many(list(_CHAT_CONFIG_KEYS)) must be called exactly once "
        f"in chat_worker.py to preserve the 1-round-trip invariant; "
        f"found {occurrences}."
    )
