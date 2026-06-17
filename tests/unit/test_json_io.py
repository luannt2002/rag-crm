"""Behavioural tests for :mod:`ragbot.shared.json_io` — orjson hot-path wrapper.

The wrapper exists to keep the stdlib :mod:`json` contract (``dumps`` returns
``str``, ``loads`` raises ``json.JSONDecodeError`` on malformed input) while
swapping the underlying engine for the faster orjson.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import orjson
import pytest

from ragbot.shared.json_io import dumps, dumps_bytes, loads


def test_dumps_returns_str_not_bytes() -> None:
    out = dumps({"a": 1})
    assert isinstance(out, str)
    assert out == '{"a":1}'


def test_dumps_bytes_returns_bytes() -> None:
    out = dumps_bytes({"a": 1})
    assert isinstance(out, bytes)
    assert out == b'{"a":1}'


def test_roundtrip_basic() -> None:
    payload = {"k": "v", "n": 42, "arr": [1, 2, 3], "nested": {"x": True}}
    assert loads(dumps(payload)) == payload


def test_roundtrip_unicode_no_escape() -> None:
    # orjson always emits UTF-8 (equivalent of stdlib ensure_ascii=False).
    text = "Tiếng Việt có dấu — không escape"
    payload = {"msg": text}
    encoded = dumps(payload)
    # Real character preserved, not the \uXXXX escape form.
    assert text in encoded
    assert loads(encoded) == payload


def test_loads_accepts_bytes_and_str() -> None:
    blob = b'{"x":1}'
    assert loads(blob) == {"x": 1}
    assert loads(blob.decode()) == {"x": 1}


def test_non_str_keys_supported() -> None:
    # Stdlib silently stringifies int keys; orjson refuses unless OPT_NON_STR_KEYS
    # is set — which our wrapper enables.
    payload = {1: "one", 2: "two"}
    encoded = dumps(payload)
    decoded = loads(encoded)
    # JSON has no integer keys, both engines emit strings on the wire.
    assert decoded == {"1": "one", "2": "two"}


def test_uuid_key_supported() -> None:
    u = UUID("12345678-1234-5678-1234-567812345678")
    payload = {u: "value"}
    encoded = dumps(payload)
    decoded = loads(encoded)
    assert decoded == {str(u): "value"}


def test_malformed_raises_json_decode_error() -> None:
    # The whole point of the wrapper: existing stdlib-style guards keep working.
    with pytest.raises(json.JSONDecodeError):
        loads("{not json")


def test_malformed_also_raises_value_error() -> None:
    # JSONDecodeError is a ValueError subclass — guard alternatives keep working.
    with pytest.raises(ValueError):
        loads("{not json")


class _Unserialisable:
    """Custom type orjson does not natively support — used to exercise the
    ``default=`` fall-back hook that callers like the audit logger pass in."""

    def __init__(self, label: str) -> None:
        self.label = label

    def __str__(self) -> str:
        return f"<Unserialisable:{self.label}>"


def test_default_callable_invoked_on_unknown_type() -> None:
    # Mirrors the ``default=str`` pattern used by audit logger and event bus.
    obj = _Unserialisable("audit-payload")
    encoded = dumps({"v": obj}, default=str)
    decoded = loads(encoded)
    assert decoded == {"v": "<Unserialisable:audit-payload>"}


def test_default_raises_type_error_without_callable() -> None:
    # When the caller does NOT pass ``default=...``, an unsupported type must
    # surface as TypeError (same behaviour as stdlib).
    with pytest.raises(TypeError):
        dumps({"v": _Unserialisable("x")})


def test_native_datetime_serialised_to_iso() -> None:
    # orjson serialises datetime natively (faster than stdlib + default=str).
    # Verify the wrapper preserves that capability.
    dt = datetime(2026, 5, 11, 12, 0, 0)
    encoded = dumps({"when": dt})
    decoded = loads(encoded)
    assert decoded["when"].startswith("2026-05-11")


def test_decode_error_is_orjson_subclass() -> None:
    # Documents the contract used in the helper docstring: orjson's exception
    # is a SUBCLASS of stdlib's, so call sites unchanged.
    try:
        loads("garbage")
    except json.JSONDecodeError as exc:
        assert isinstance(exc, orjson.JSONDecodeError)
    else:
        pytest.fail("loads did not raise on malformed input")


def test_dumps_bytes_unicode_no_escape() -> None:
    text = "Tiếng Việt"
    out = dumps_bytes({"msg": text})
    assert isinstance(out, bytes)
    assert text.encode("utf-8") in out
