"""Validate owner-submitted ``bots.action_config`` (memory-flow Phase G).

The bot owner declares, per action, the slot fields the bot should remember
(name / phone / address / ...). This validator is the single gate the admin
API runs BEFORE persisting, so a malformed or oversized schema can never reach
the slot extractor. Domain-neutral: field keys/labels are owner-defined; this
code only enforces SHAPE + bounds, never specific business names.

Shape (new owner format):
    {
      "enabled": true,
      "slots_schema": {
        "<action>": {
          "fields": [
            {"key": "...", "label": "...", "desc": "...",
             "type": "text|number|date|time|phone", "required": true}
          ]
        }
      }
    }
"""

from __future__ import annotations

import re
from typing import Any

from ragbot.shared.constants import DEFAULT_MAX_ACTION_SLOTS

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_ALLOWED_TYPES = frozenset({"text", "number", "date", "time", "phone", "email"})


class ActionConfigValidationError(ValueError):
    """Raised when an owner-submitted action_config is invalid."""


def validate_action_config(
    action_config: Any, *, max_fields: int = DEFAULT_MAX_ACTION_SLOTS,
) -> dict[str, Any]:
    """Validate + normalize an owner-submitted action_config.

    @return: normalized config (every field carries key/label/desc/type/required).
    @raises ActionConfigValidationError: on any shape / bound violation.
    """
    if not isinstance(action_config, dict):
        raise ActionConfigValidationError("action_config must be an object")

    enabled = bool(action_config.get("enabled", False))
    raw_schema = action_config.get("slots_schema") or {}
    if not isinstance(raw_schema, dict):
        raise ActionConfigValidationError("slots_schema must be an object")

    norm_schema: dict[str, Any] = {}
    for action, sub in raw_schema.items():
        if not isinstance(action, str) or not action.strip():
            raise ActionConfigValidationError("action name must be a non-empty string")
        if not isinstance(sub, dict):
            raise ActionConfigValidationError(f"action {action!r} must be an object")
        fields = sub.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ActionConfigValidationError(
                f"action {action!r} must declare a non-empty 'fields' list",
            )
        if len(fields) > max_fields:
            raise ActionConfigValidationError(
                f"action {action!r} has {len(fields)} fields — max {max_fields}",
            )
        seen: set[str] = set()
        norm_fields: list[dict[str, Any]] = []
        for f in fields:
            if not isinstance(f, dict):
                raise ActionConfigValidationError("each field must be an object")
            key = str(f.get("key", "")).strip()
            if not _KEY_RE.match(key):
                raise ActionConfigValidationError(
                    f"invalid field key {key!r} — must match {_KEY_RE.pattern}",
                )
            if key in seen:
                raise ActionConfigValidationError(f"duplicate field key {key!r}")
            seen.add(key)
            ftype = str(f.get("type", "text")).strip().lower() or "text"
            if ftype not in _ALLOWED_TYPES:
                raise ActionConfigValidationError(
                    f"field {key!r} type {ftype!r} not in {sorted(_ALLOWED_TYPES)}",
                )
            norm_fields.append({
                "key": key,
                "label": str(f.get("label") or key).strip(),
                "desc": str(f.get("desc") or f.get("description") or "").strip(),
                "type": ftype,
                "required": bool(f.get("required", False)),
            })
        norm_schema[action] = {"fields": norm_fields}

    return {"enabled": enabled, "slots_schema": norm_schema}


__all__ = ["validate_action_config", "ActionConfigValidationError"]
