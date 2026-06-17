"""``ModelResolverService._get_cached`` resolves model + provider rows
per-binding via sequential ``await``. With N bindings the wall-clock
cost stacks linearly on DB round-trip latency. Gathering the lookups
removes the per-binding sequential dependency without changing the
Repo Protocol.
"""

from __future__ import annotations

import inspect
import re

from ragbot.application.services.model_resolver import ModelResolverService


def test_get_cached_uses_asyncio_gather_for_per_binding_lookups() -> None:
    src = inspect.getsource(ModelResolverService._get_cached)
    # The legacy code shape was a sequential ``for b in bindings:`` loop
    # with ``m = await self._repo.get_model(...)`` inside it. Either
    # asyncio.gather or asyncio.TaskGroup must replace that pattern so
    # the per-binding awaits fan out concurrently.
    has_gather = "asyncio.gather" in src or "TaskGroup" in src
    assert has_gather, (
        "ModelResolverService._get_cached must fan out per-binding "
        "model + provider lookups concurrently (asyncio.gather or "
        "asyncio.TaskGroup); sequential await per binding stacks N "
        "round-trips of DB latency on every cache miss"
    )


def test_get_cached_no_longer_iterates_get_model_in_for_loop() -> None:
    src = inspect.getsource(ModelResolverService._get_cached)
    # Disallow the legacy pattern `for b in bindings:\n ... await self._repo.get_model(...)`.
    pat = re.compile(
        r"for\s+\w+\s+in\s+bindings\s*:\s*\n[^\n]*\n[^\n]*await\s+self\._repo\.get_model",
        re.MULTILINE,
    )
    assert not pat.search(src), (
        "Legacy sequential per-binding await detected — replace with "
        "asyncio.gather or batch repo call"
    )
