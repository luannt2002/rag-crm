"""Regression test for mega-sprint-G20 — chat_worker history limit is config-driven.

Bug: ``chat_worker._dispatch_chat`` loaded conversation history with a
hardcoded ``limit=6`` (and ``messages[-6:]`` fallback). This violates
the zero-hardcode rule (CLAUDE.md) — the operator could never tune
multi-turn depth without a code change. The ``chat_max_history`` knob
already existed in ``_CHAT_CONFIG_KEYS`` but was unused on the
history-load path.

Fix: drive the limit from the batched ``_cfg`` snapshot via
``_cfg_int(_cfg, "chat_max_history", DEFAULT_MAX_HISTORY)``. No new
Redis round-trip — the key is already in the batch. Falls back to
``DEFAULT_MAX_HISTORY`` (declared in ``shared/constants.py``) when the
key is absent or null.

Pre-fix: literal ``limit=6`` and ``messages[-6:]`` present.
Post-fix: literals gone; ``chat_max_history`` is the configured knob.
"""
from __future__ import annotations

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


def test_default_max_history_constant_exists_and_is_int() -> None:
    """Hard guard: ``DEFAULT_MAX_HISTORY`` must exist in shared/constants.py
    so the chat-worker import never breaks at startup.
    """
    from ragbot.shared.constants import DEFAULT_MAX_HISTORY

    assert isinstance(DEFAULT_MAX_HISTORY, int)
    assert DEFAULT_MAX_HISTORY > 0, (
        "DEFAULT_MAX_HISTORY must be a positive integer — "
        "0 would disable multi-turn entirely."
    )


def test_chat_worker_imports_default_max_history() -> None:
    """chat_worker.py must import the constant so the limit is grounded
    in the SSoT (``shared/constants.py``), not redefined locally.
    """
    src = _CW_PATH.read_text(encoding="utf-8")
    assert "DEFAULT_MAX_HISTORY" in src, (
        "chat_worker.py must import DEFAULT_MAX_HISTORY from "
        "shared/constants.py to drive the chat_max_history knob default."
    )


def test_chat_worker_no_hardcoded_history_limit_six() -> None:
    """The literal ``limit=6`` and ``messages[-6:]`` slice must be gone
    — they were the original zero-hardcode violations.
    """
    src = _CW_PATH.read_text(encoding="utf-8")
    assert "limit=6" not in src, (
        "chat_worker.py must not hardcode limit=6 — drive history depth "
        "from `_cfg_int(_cfg, 'chat_max_history', DEFAULT_MAX_HISTORY)`."
    )
    assert "messages[-6:]" not in src, (
        "chat_worker.py must not hardcode messages[-6:] slice — drive "
        "from the same configured limit as history_for_llm()."
    )


def test_chat_worker_drives_history_limit_from_cfg_snapshot() -> None:
    """Behavioural anchor: the configured key + default constant appear
    together on the history-load path so the limit is plumbed through
    the batched config snapshot (no extra Redis round-trip).
    """
    src = _CW_PATH.read_text(encoding="utf-8")
    assert 'chat_max_history' in src, (
        "chat_worker.py must reference the chat_max_history config key."
    )
    # The known config key + constant must appear in the same expression
    # so we are sure the SSoT default flows through the lookup.
    assert '_cfg_int(_cfg, "chat_max_history", DEFAULT_MAX_HISTORY)' in src, (
        "chat_worker.py must call _cfg_int(_cfg, 'chat_max_history', "
        "DEFAULT_MAX_HISTORY) on the history-load path."
    )


def test_chat_max_history_in_batched_keys() -> None:
    """``chat_max_history`` must remain in ``_CHAT_CONFIG_KEYS`` so the
    history-load lookup hits the batched snapshot, not a separate Redis
    round-trip (defends G21's perf invariant too).
    """
    from ragbot.interfaces.workers.chat_worker import _CHAT_CONFIG_KEYS

    assert "chat_max_history" in _CHAT_CONFIG_KEYS, (
        "chat_max_history must be in _CHAT_CONFIG_KEYS for the batched "
        "get_many round-trip — otherwise history-load triggers a "
        "second uncached Redis call."
    )
