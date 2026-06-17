"""Fast JSON serialisation helper backed by ``orjson``.

Why this exists
---------------
orjson is 2-5x faster than the stdlib :mod:`json` module on serialise and
1.5-2x faster on deserialise. Per-turn the pipeline serialises audit events,
chunk_metadata snapshots, and Redis Streams payloads dozens of times — the
saving compounds quickly on a 10-20 s turn budget.

Behaviour contract (matches stdlib :mod:`json`)
-----------------------------------------------
- :func:`dumps` always returns ``str`` (orjson natively returns ``bytes``
  — we decode here so callers do not have to special-case).
- :func:`dumps_bytes` exposes the native bytes form for callers that will
  immediately ``.encode()`` again (Redis XADD, file write in binary mode).
- :func:`loads` accepts ``str`` / ``bytes`` / ``bytearray`` — same as stdlib.

Exception compatibility
-----------------------
:class:`orjson.JSONDecodeError` is a **subclass of** :class:`ValueError` AND
:class:`json.JSONDecodeError`, so existing ``except json.JSONDecodeError``
guards keep working. Callers that already ``except (ValueError, TypeError)``
or ``except json.JSONDecodeError`` need no change.

Options
-------
``OPT_NON_STR_KEYS`` is enabled because the pipeline occasionally serialises
dicts whose keys are integers or UUIDs (stdlib silently stringifies them).
"""

from __future__ import annotations

from typing import Any, Callable

import orjson

# Module-level option constant — orjson uses bitwise flags so building this
# per-call would allocate; pin it at import time.
_DUMP_OPTIONS: int = orjson.OPT_NON_STR_KEYS


def dumps(obj: Any, *, default: Callable[[Any], Any] | None = None) -> str:
    """Serialise ``obj`` to a JSON string. Stdlib-compatible return type."""
    return orjson.dumps(obj, default=default, option=_DUMP_OPTIONS).decode()


def dumps_bytes(obj: Any, *, default: Callable[[Any], Any] | None = None) -> bytes:
    """Serialise ``obj`` to raw JSON bytes (skip the decode hop)."""
    return orjson.dumps(obj, default=default, option=_DUMP_OPTIONS)


def loads(s: str | bytes | bytearray) -> Any:
    """Parse a JSON document. Raises ``orjson.JSONDecodeError`` (a subclass of
    both :class:`json.JSONDecodeError` and :class:`ValueError`) on malformed
    input — existing stdlib-style ``except json.JSONDecodeError`` blocks keep
    working unchanged."""
    return orjson.loads(s)
