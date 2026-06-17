"""bind_request_context — UUID-string tenant_id vs external INT tenant_id_int."""
from __future__ import annotations

import contextvars

import pytest

from ragbot.config.logging import (
    bind_request_context,
    clear_request_context,
    get_tenant_id,
    get_tenant_id_int,
    tenant_id_ctx,
    tenant_id_int_ctx,
)


@pytest.fixture(autouse=True)
def _isolate_contextvars():
    """Run each test inside a fresh copy of the current context."""
    ctx = contextvars.copy_context()
    yield ctx
    # Reset both vars to their module defaults so a leak from one test
    # cannot poison the next.
    tenant_id_ctx.set("UNSET")
    tenant_id_int_ctx.set(None)


def _run(callable_):
    ctx = contextvars.copy_context()
    return ctx.run(callable_)


def test_both_vars_set_independently():
    def _inner():
        bind_request_context(
            tenant_id="00000000-0000-0000-0000-000000000001",
            tenant_id_int=32,
        )
        return get_tenant_id(), get_tenant_id_int()

    uuid_val, int_val = _run(_inner)
    assert uuid_val == "00000000-0000-0000-0000-000000000001"
    assert int_val == 32


def test_only_uuid_leaves_int_unset():
    def _inner():
        bind_request_context(tenant_id="00000000-0000-0000-0000-000000000002")
        return get_tenant_id(), get_tenant_id_int()

    uuid_val, int_val = _run(_inner)
    assert uuid_val == "00000000-0000-0000-0000-000000000002"
    assert int_val is None


def test_only_int_leaves_uuid_unset():
    def _inner():
        bind_request_context(tenant_id_int=99)
        return get_tenant_id(), get_tenant_id_int()

    uuid_val, int_val = _run(_inner)
    assert uuid_val is None
    assert int_val == 99


def test_clear_resets_int_var():
    def _inner():
        bind_request_context(
            tenant_id="00000000-0000-0000-0000-000000000003",
            tenant_id_int=7,
        )
        clear_request_context()
        return get_tenant_id_int()

    assert _run(_inner) is None


def test_int_zero_is_bound_not_skipped():
    def _inner():
        bind_request_context(tenant_id_int=0)
        return tenant_id_int_ctx.get()

    # Zero is a valid INT id (some upstream test fixtures use it). The
    # ``is not None`` guard inside bind_request_context must not treat 0
    # as missing.
    assert _run(_inner) == 0


def test_get_tenant_id_returns_none_for_sentinel():
    def _inner():
        # Default sentinel — never bound.
        return get_tenant_id()

    assert _run(_inner) is None
