"""Unit tests for ``audit_log_hasher.compute_audit_row_hash``.

Covers: determinism, chain dependence, field sensitivity, type
normalisation (UUID / datetime / dict / None), and digest format.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from ragbot.application.services.audit_log_hasher import compute_audit_row_hash


_FIXED_TS = datetime(2026, 5, 16, 11, 22, 33, 123456, tzinfo=timezone.utc)
_TENANT = UUID("11111111-1111-1111-1111-111111111111")


def _base_row(**overrides: object) -> dict[str, object]:
    """Build a baseline row payload; tests override one field at a time."""
    base: dict[str, object] = {
        "prev_hash": "",
        "record_tenant_id": _TENANT,
        "workspace_id": "system",
        "actor_user_id": "user-1",
        "action": "create",
        "resource_type": "bot",
        "resource_id": "bot-1",
        "before_json": None,
        "after_json": {"name": "alpha"},
        "reason": None,
        "trace_id": "trace-1",
        "created_at": _FIXED_TS,
    }
    base.update(overrides)
    return base


def test_hash_chain_deterministic() -> None:
    """Same inputs → same hex digest, repeatedly."""
    row = _base_row()
    h1 = compute_audit_row_hash(**row)  # type: ignore[arg-type]
    h2 = compute_audit_row_hash(**row)  # type: ignore[arg-type]
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_hash_chain_changes_on_action_change() -> None:
    """Different ``action`` → different digest (field sensitivity)."""
    h_create = compute_audit_row_hash(**_base_row(action="create"))  # type: ignore[arg-type]
    h_update = compute_audit_row_hash(**_base_row(action="update"))  # type: ignore[arg-type]
    assert h_create != h_update


def test_hash_chain_changes_on_resource_id() -> None:
    """Different ``resource_id`` → different digest."""
    h_a = compute_audit_row_hash(**_base_row(resource_id="bot-1"))  # type: ignore[arg-type]
    h_b = compute_audit_row_hash(**_base_row(resource_id="bot-2"))  # type: ignore[arg-type]
    assert h_a != h_b


def test_hash_chain_changes_on_after_json_value() -> None:
    """Mutating a nested JSON value flips the digest."""
    h_a = compute_audit_row_hash(
        **_base_row(after_json={"name": "alpha"}),  # type: ignore[arg-type]
    )
    h_b = compute_audit_row_hash(
        **_base_row(after_json={"name": "BETA"}),  # type: ignore[arg-type]
    )
    assert h_a != h_b


def test_hash_chain_stable_on_json_key_reorder() -> None:
    """``after_json`` key order MUST NOT affect digest (canonical sort)."""
    h_a = compute_audit_row_hash(
        **_base_row(after_json={"a": 1, "b": 2}),  # type: ignore[arg-type]
    )
    h_b = compute_audit_row_hash(
        **_base_row(after_json={"b": 2, "a": 1}),  # type: ignore[arg-type]
    )
    assert h_a == h_b


def test_hash_chain_includes_prev_hash() -> None:
    """Different ``prev_hash`` → different digest (chain dependence)."""
    h_seed = compute_audit_row_hash(**_base_row(prev_hash=""))  # type: ignore[arg-type]
    h_next = compute_audit_row_hash(**_base_row(prev_hash=h_seed))  # type: ignore[arg-type]
    assert h_seed != h_next


def test_hash_chain_null_fields_render_empty() -> None:
    """``None`` renders identical to explicit empty string in JSON-safe slots."""
    # before_json None vs before_json={} differ because {} serialises to "{}"
    # while None serialises to "".
    h_none = compute_audit_row_hash(**_base_row(before_json=None))  # type: ignore[arg-type]
    h_empty_dict = compute_audit_row_hash(**_base_row(before_json={}))  # type: ignore[arg-type]
    assert h_none != h_empty_dict


def test_hash_chain_uuid_string_vs_uuid_object_equivalent() -> None:
    """Passing UUID as object or as canonical str yields the same digest."""
    h_obj = compute_audit_row_hash(**_base_row(record_tenant_id=_TENANT))  # type: ignore[arg-type]
    h_str = compute_audit_row_hash(
        **_base_row(record_tenant_id=str(_TENANT)),  # type: ignore[arg-type]
    )
    assert h_obj == h_str


def test_hash_chain_changes_on_created_at() -> None:
    """Two otherwise-identical rows at different timestamps hash differently."""
    other_ts = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
    h_a = compute_audit_row_hash(**_base_row(created_at=_FIXED_TS))  # type: ignore[arg-type]
    h_b = compute_audit_row_hash(**_base_row(created_at=other_ts))  # type: ignore[arg-type]
    assert h_a != h_b


def test_hash_chain_pre_serialised_json_string_accepted() -> None:
    """Pre-serialised canonical JSON str produces same digest as dict input.

    Canonical form mirrors Postgres ``jsonb::text``: alphabetical keys,
    ``", "`` between items, ``": "`` between key/value.
    """
    payload = {"name": "alpha"}
    h_dict = compute_audit_row_hash(**_base_row(after_json=payload))  # type: ignore[arg-type]
    h_str = compute_audit_row_hash(
        **_base_row(after_json='{"name": "alpha"}'),  # type: ignore[arg-type]
    )
    assert h_dict == h_str


def test_hash_chain_two_step_chain_propagation() -> None:
    """Chain row3 depends on row2, which depends on row1 — modifying row1
    breaks row2's expected hash AND row3's expected hash."""
    row1_a = compute_audit_row_hash(**_base_row(action="create"))  # type: ignore[arg-type]
    row2_a = compute_audit_row_hash(
        **_base_row(prev_hash=row1_a, action="update"),  # type: ignore[arg-type]
    )
    row3_a = compute_audit_row_hash(
        **_base_row(prev_hash=row2_a, action="delete"),  # type: ignore[arg-type]
    )

    # Tamper row1 → action changes; row2 and row3 chains recompute differently.
    row1_b = compute_audit_row_hash(**_base_row(action="rotate_key"))  # type: ignore[arg-type]
    row2_b = compute_audit_row_hash(
        **_base_row(prev_hash=row1_b, action="update"),  # type: ignore[arg-type]
    )
    row3_b = compute_audit_row_hash(
        **_base_row(prev_hash=row2_b, action="delete"),  # type: ignore[arg-type]
    )

    assert row1_a != row1_b
    assert row2_a != row2_b
    assert row3_a != row3_b


def test_hash_chain_naive_datetime_treated_as_utc() -> None:
    """Naive datetime is normalised to UTC, not raised on."""
    naive = datetime(2026, 5, 16, 11, 22, 33, 123456)
    aware = naive.replace(tzinfo=timezone.utc)
    h_naive = compute_audit_row_hash(**_base_row(created_at=naive))  # type: ignore[arg-type]
    h_aware = compute_audit_row_hash(**_base_row(created_at=aware))  # type: ignore[arg-type]
    assert h_naive == h_aware
