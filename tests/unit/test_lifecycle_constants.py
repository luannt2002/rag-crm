"""Constants contract — bot lifecycle purge (ADR-W1-D4 step 2).

``SUBJECT_BOT_PURGED`` is the outbox subject emitted atomically with the
hard ``DELETE FROM bots`` inside :class:`BotLifecycleService.purge_bot`
so peer replicas can bust their caches. ``CACHE_KEY_UQ_PREFIX`` pins the
understand-query cache key prefix shared between the writer
(``UnderstandQueryCache``) and the purge SCAN pattern — drift between
the two means the purge silently stops matching live keys.
"""

from __future__ import annotations

from ragbot.shared import constants
from ragbot.shared.constants import (
    CACHE_KEY_UQ_PREFIX,
    DEFAULT_PURGE_UQ_SCAN_COUNT,
    SUBJECT_BOT_PURGED,
)


def test_subject_bot_purged_value() -> None:
    assert SUBJECT_BOT_PURGED == "bot.purged.v1"


def test_subject_bot_purged_exported() -> None:
    assert "SUBJECT_BOT_PURGED" in constants.__all__


def test_uq_prefix_matches_understand_query_cache_key_shape() -> None:
    """The SCAN pattern in purge S5 must keep matching the keys the
    understand-query cache actually writes. We pin via the real key
    builder so a cache-side key change turns this red instead of
    silently orphaning Redis keys."""
    from ragbot.infrastructure.cache.understand_query_cache import (  # noqa: PLC0415 — deferred so a cache-module break doesn't kill constants tests
        UnderstandQueryCache,
    )

    cache = UnderstandQueryCache(None, prompt_version=1)
    key = cache._key("bot-uuid", "any query")
    assert key.startswith(CACHE_KEY_UQ_PREFIX)
    # Full shape: ragbot:uq:v{pv}:{record_bot_id}:{hash}
    assert key.startswith(f"{CACHE_KEY_UQ_PREFIX}1:bot-uuid:")


def test_purge_uq_scan_count_positive_int() -> None:
    assert isinstance(DEFAULT_PURGE_UQ_SCAN_COUNT, int)
    assert DEFAULT_PURGE_UQ_SCAN_COUNT > 0
    assert "DEFAULT_PURGE_UQ_SCAN_COUNT" in constants.__all__
