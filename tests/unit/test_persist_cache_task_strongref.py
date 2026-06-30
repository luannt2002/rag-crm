"""PERSIST-CACHE pin — the fire-and-forget cache-write task must be strong-
referenced so the GC cannot collect it mid-write (silent hit-rate leak).
asyncio holds only a WEAK ref to a bare create_task result.
"""
from __future__ import annotations

import inspect

import ragbot.orchestration.nodes.persist as persist


def test_cache_write_task_is_strong_referenced() -> None:
    src = inspect.getsource(persist)
    assert "_BG_CACHE_TASKS" in src, "module-level strong-ref set missing"
    # The cache-write create_task result must be added to the set + self-discard.
    assert "_BG_CACHE_TASKS.add(" in src, "cache-write task not added to strong-ref set"
    assert "_BG_CACHE_TASKS.discard" in src, "task does not self-remove on done"


def test_bg_cache_tasks_is_a_set() -> None:
    assert isinstance(persist._BG_CACHE_TASKS, set)
