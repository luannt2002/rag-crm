"""Regression: /admin/models query uses canonical column names.

Pre-fix incident (2026-04-29):
The endpoint queried legacy columns that no longer exist post migration
0034 rename:
  - `m.display_name` → renamed to `m.name`
  - `m.purpose`      → renamed to `m.kind`
  - `m.is_default`   → column dropped
  - `m.is_active`    → renamed to `m.enabled`
  - `m.provider_id`  → renamed to `m.record_provider_id`

Effect: every call to GET /admin/models returned 500
`UndefinedColumnError: column m.provider_id does not exist`. Endpoint
was advertised in the admin UI but broken since the rename — silent
operator pain.

This test asserts the source SQL uses the canonical columns. We don't
boot Postgres because the columns are reflected in the SQL string.
"""
from __future__ import annotations

import inspect
import re

from ragbot.interfaces.http.routes import test_chat


def _admin_list_models_sql() -> str:
    src = inspect.getsource(test_chat.admin_list_models)
    # Strip docstring (the docstring intentionally describes both the bug
    # and the fix, so it mentions the legacy names).
    func_body = re.sub(r'""".*?"""', "", src, count=1, flags=re.DOTALL)
    return func_body


def test_uses_record_provider_id_not_backcompat_provider_id() -> None:
    body = _admin_list_models_sql()
    assert "record_provider_id" in body
    # Bare `m.provider_id` (without `record_` prefix) must be gone.
    assert not re.search(r"m\.provider_id\b", body)


def test_uses_enabled_not_is_active() -> None:
    body = _admin_list_models_sql()
    assert "m.enabled = true" in body
    assert "m.is_active" not in body


def test_uses_name_aliased_as_display_name() -> None:
    """`display_name` is the API contract field; underlying column is `m.name`."""
    body = _admin_list_models_sql()
    assert "m.name AS display_name" in body
    # Bare `m.display_name` (column reference) must be gone.
    assert not re.search(r"m\.display_name\b", body)


def test_uses_kind_aliased_as_purpose() -> None:
    body = _admin_list_models_sql()
    assert "m.kind AS purpose" in body
    assert not re.search(r"m\.purpose\b", body)


def test_filters_soft_deleted() -> None:
    """Deleted models must be excluded from the active list."""
    body = _admin_list_models_sql()
    assert "deleted_at IS NULL" in body


def test_is_default_returned_as_none() -> None:
    """Schema dropped the column, but the API contract still ships
    `is_default` for client compat — we return None explicitly."""
    body = _admin_list_models_sql()
    assert '"is_default": None' in body


def test_orders_by_kind_and_name_post_rename() -> None:
    body = _admin_list_models_sql()
    assert "ORDER BY m.kind, m.name" in body
