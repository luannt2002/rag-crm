"""Pure-function SHA256 hash chain helper for ``audit_log`` tamper detection.

Each ``audit_log`` row carries a ``row_hash`` derived from the previous
row's hash concatenated with the row's own critical fields. Any UPDATE
or DELETE that bypasses the DB ``audit_log_immutable`` trigger leaves a
broken chain — the verifier scans rows ordered by ``(created_at, id)``
and reports mismatches.

The hash function is bit-stable with the SQL backfill emitted by
alembic ``20260516_010g_audit_log_tamper_chain.py``:

  sha256(prev_hash || US || record_tenant_id || US || workspace_id ||
         US || actor_user_id || US || action || US || resource_type ||
         US || resource_id || US || before_json || US || after_json ||
         US || reason || US || trace_id || US || created_at_iso)

  - ``US`` = ASCII Unit Separator (0x1F).
  - JSON fields are serialised with ``json.dumps(..., sort_keys=True,
    separators=(",", ":"), ensure_ascii=False)`` — matches Postgres
    ``::text`` cast on ``jsonb`` (alphabetical keys, no whitespace).
  - NULLs render as the empty string (matches SQL ``COALESCE(...,'')``).
  - ``created_at`` is serialised using Postgres' ISO-with-microseconds
    text format, i.e. ``YYYY-MM-DD HH:MM:SS.ffffff[+ZZ:ZZ]``.

This module performs no I/O; it is safe to call from any context.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Final
from uuid import UUID

# US (Unit Separator) — ambiguity-free delimiter, same as the SQL side.
_FIELD_SEP: Final[bytes] = b"\x1f"

# Canonical field order. KEEP IN SYNC with the alembic ``_hash_expr``
# and the SQL backfill in ``20260516_010g_audit_log_tamper_chain.py``.
_FIELD_ORDER: Final[tuple[str, ...]] = (
    "record_tenant_id",
    "workspace_id",
    "actor_user_id",
    "action",
    "resource_type",
    "resource_id",
    "before_json",
    "after_json",
    "reason",
    "trace_id",
    "created_at",
)


def _encode_field(name: str, value: Any) -> bytes:
    """Render one field to canonical bytes (matches Postgres ``COALESCE``)."""
    if name in ("before_json", "after_json"):
        # JSONB columns: SQLAlchemy maps Python ``None`` to a JSONB ``null``
        # literal (NOT SQL NULL), and Postgres ``::text`` then returns the
        # 4-char string ``"null"``. The verifier reads ``::text`` straight
        # from the DB, so we must hash the writer-side ``None`` the same way.
        if value is None:
            return b"null"
        # Match Postgres ``jsonb::text`` canonical form:
        #   - alphabetical key order
        #   - separator ``", "`` between items, ``": "`` between key/value
        #   - non-ASCII preserved (``ensure_ascii=False``)
        if isinstance(value, str):
            # Caller passed pre-serialised text (e.g. the verifier feeding
            # ``::text`` from Postgres straight through); use verbatim.
            return value.encode("utf-8")
        return json.dumps(
            value, sort_keys=True, separators=(", ", ": "), ensure_ascii=False,
        ).encode("utf-8")
    if value is None:
        # Non-JSON column → SQL NULL → ``COALESCE(...,'')`` returns ''.
        return b""
    if isinstance(value, datetime):
        # Canonical form = UTC-normalised ``YYYY-MM-DD HH:MM:SS.ffffff``
        # (6-digit microseconds, no tz suffix). Matches the SQL backfill
        # ``to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')``
        # in alembic 010g so DB backfill and Python writer produce
        # bit-identical bytes regardless of session timezone.
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.strftime("%Y-%m-%d %H:%M:%S.%f").encode("utf-8")
    if isinstance(value, UUID):
        return str(value).encode("utf-8")
    return str(value).encode("utf-8")


def compute_audit_row_hash(
    *,
    prev_hash: str,
    record_tenant_id: UUID | str | None,
    workspace_id: str | None,
    actor_user_id: str | None,
    action: str | None,
    resource_type: str | None,
    resource_id: str | None,
    before_json: dict[str, Any] | str | None,
    after_json: dict[str, Any] | str | None,
    reason: str | None,
    trace_id: str | None,
    created_at: datetime | str | None,
) -> str:
    """Compute the SHA256 hex digest for one ``audit_log`` row.

    ``prev_hash`` is the previous row's ``row_hash`` (or ``""`` for the
    very first row in the chain). All other args mirror the column
    names on ``audit_log``.

    Returns: lowercase hex string of length ``DEFAULT_CONTENT_HASH_HEX_LEN``
    (= 64).
    """
    fields: dict[str, Any] = {
        "record_tenant_id": record_tenant_id,
        "workspace_id": workspace_id,
        "actor_user_id": actor_user_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "before_json": before_json,
        "after_json": after_json,
        "reason": reason,
        "trace_id": trace_id,
        "created_at": created_at,
    }
    h = hashlib.sha256()
    h.update((prev_hash or "").encode("utf-8"))
    for name in _FIELD_ORDER:
        h.update(_FIELD_SEP)
        h.update(_encode_field(name, fields[name]))
    return h.hexdigest()


__all__ = ["compute_audit_row_hash"]
