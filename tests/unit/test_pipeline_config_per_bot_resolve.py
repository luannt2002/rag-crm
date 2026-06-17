"""Per-bot tuning of pipeline_config knobs that gate retrieval / rerank /
grounding must reach the runtime, not get silently dropped at the
chat_worker / REST entry-point.

Several keys live in ``PLAN_LIMIT_SCHEMA`` (so ``bots.threshold_overrides``
or ``bots.plan_limits`` can carry per-bot tunings) but the existing
build path calls ``cfg_svc.get_*()`` directly — bypassing
``resolve_bot_limit`` entirely. The bot owner's override is therefore
discarded and the system-wide default wins.

These tests pin the wiring on both entry-points (``chat_worker`` for
production traffic, ``test_chat`` REST route for QA) so a regression
bringing back the bypass pattern fails CI.
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace

import pytest

from ragbot.shared import bot_limits


# Keys that must travel through resolve_bot_limit so per-bot overrides win.
# Restricted to the keys actually present in PLAN_LIMIT_SCHEMA AND read by
# the pipeline_config builders. (rerank_top_n / max_history / etc are
# already wired and excluded here to keep the audit focused.)
_PER_BOT_REQUIRED_KEYS: tuple[str, ...] = (
    "reranker_min_score_active",
    "rerank_filter_strategy",
    "rerank_cliff_gap_ratio",
    "rerank_cliff_absolute_floor",
    "rerank_cliff_min_keep",
    "grounding_check_threshold",
)


def _entry_source(module_path: str, attr: str | None = None) -> str:
    import importlib
    from pathlib import Path

    mod = importlib.import_module(module_path)
    if attr is None:
        # ``chat_worker`` was split from a single module into a package; when
        # the import resolves to a package, concatenate every sub-module so
        # the static-text guards see the pipeline_config builder wherever it
        # landed.
        mod_file = getattr(mod, "__file__", None)
        if mod_file and Path(mod_file).name == "__init__.py":
            pkg_dir = Path(mod_file).parent
            return "\n".join(
                p.read_text(encoding="utf-8")
                for p in sorted(pkg_dir.glob("*.py"))
            )
        return inspect.getsource(mod)
    return inspect.getsource(getattr(mod, attr))


@pytest.mark.parametrize("key", _PER_BOT_REQUIRED_KEYS)
def test_chat_worker_routes_key_through_resolve_bot_limit(key: str) -> None:
    src = _entry_source("ragbot.interfaces.workers.chat_worker")
    pattern = re.compile(
        rf'"{re.escape(key)}":\s*resolve_bot_limit\(',
        re.MULTILINE,
    )
    assert pattern.search(src), (
        f"chat_worker.py builds pipeline_config[{key!r}] without "
        f"resolve_bot_limit — per-bot override via "
        f"bots.threshold_overrides / bots.plan_limits is silently dropped. "
        f"Wrap the assignment as: "
        f'"{key}": resolve_bot_limit(bot_cfg, "{key}", system_default=await _cfg_svc.get_float(...))'
    )


@pytest.mark.parametrize("key", _PER_BOT_REQUIRED_KEYS)
def test_test_chat_route_routes_key_through_resolve_bot_limit(key: str) -> None:
    src = _entry_source("ragbot.interfaces.http.routes.test_chat._pipeline_config")
    pattern = re.compile(
        rf'"{re.escape(key)}":\s*resolve_bot_limit\(',
        re.MULTILINE,
    )
    assert pattern.search(src), (
        f"test_chat REST route builds pipeline_config[{key!r}] without "
        f"resolve_bot_limit — QA endpoint can't exercise per-bot tuning, "
        f"so a fix verified via /test-chat would silently miss the override "
        f"path used in production."
    )


def test_resolve_bot_limit_honours_threshold_override_for_reranker_min_score() -> None:
    """End-to-end behavioural sanity: bot threshold override is honoured.

    260525 Bug #6 fix — pre-fix this resolver applied ``max(bot, system)``
    which silently elevated the bot value to the system floor. Post-fix
    the bot value WINS outright and can override DOWNWARD.

    ``reranker_min_score_active`` has no schema range entry so no range
    guard fires; bot 0.10 wins over system 0.40.
    """
    bot_cfg = SimpleNamespace(
        plan_limits=None,
        threshold_overrides={"reranker_min_score_active": 0.10},
    )
    resolved = bot_limits.resolve_bot_limit(
        bot_cfg, "reranker_min_score_active", system_default=0.40,
    )
    assert resolved == 0.10, (
        "Bug #6 regression — resolver should return the bot threshold "
        "override outright; max() heuristic was removed."
    )


def test_resolve_bot_limit_lifts_above_system_floor_when_bot_sets_higher() -> None:
    bot_cfg = SimpleNamespace(
        plan_limits=None,
        threshold_overrides={"reranker_min_score_active": 0.50},
    )
    resolved = bot_limits.resolve_bot_limit(
        bot_cfg, "reranker_min_score_active", system_default=0.15,
    )
    assert resolved == 0.50


def test_resolve_bot_limit_falls_back_when_bot_has_no_override() -> None:
    bot_cfg = SimpleNamespace(plan_limits=None, threshold_overrides=None)
    resolved = bot_limits.resolve_bot_limit(
        bot_cfg, "reranker_min_score_active", system_default=0.25,
    )
    assert resolved == 0.25
