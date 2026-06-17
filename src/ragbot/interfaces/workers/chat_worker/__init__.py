"""Chat worker package — consume `chat.received.v1` and run the RAG pipeline.

Split from the former single-file ``chat_worker.py`` (god-file decompose).
The importable name ``chat_worker`` is preserved so existing call sites such
as ``from ragbot.interfaces.workers.chat_worker import handle_chat_received,
main, _CHAT_CONFIG_KEYS, _maybe_redact_chat_query, _parse_intent_list`` keep
working unchanged.
"""

from __future__ import annotations

from .config import *  # noqa: F401,F403
from .payload import *  # noqa: F401,F403
from .callbacks import *  # noqa: F401,F403
from .pipeline import *  # noqa: F401,F403

from .config import (
    _CHAT_CONFIG_KEYS,
    _cfg_bool,
    _cfg_float,
    _cfg_get,
    _cfg_int,
    _parse_intent_list,
)
from .payload import _maybe_redact_chat_query, _resolve_record_tenant_id
from .pipeline import handle_chat_received, main

__all__ = [
    "handle_chat_received",
    "main",
    "_CHAT_CONFIG_KEYS",
    "_parse_intent_list",
    "_maybe_redact_chat_query",
    "_resolve_record_tenant_id",
    "_cfg_int",
    "_cfg_float",
    "_cfg_bool",
    "_cfg_get",
]
