"""Pin tests — 260525 Bug #12 ``_pcfg`` treats None as missing.

Bug #7c (commit a6227b0) populated 78 keys in the pipeline_config dict
with ``raw.get(key, None)`` so the key is present but value is ``None``
when the operator has not set an explicit override in ``system_config``.

Pre-Bug-#12 the helper ``_pcfg(state, key, DEFAULT_*)`` was implemented
as ``dict.get(key, default)`` — ``dict.get`` only returns the default
when the key is *missing*, NOT when its value is ``None``. So callers
who wrote::

    timeout = float(_pcfg(state, "speculative_retrieve_timeout_s",
                          DEFAULT_SPECULATIVE_RETRIEVE_TIMEOUT_S))

received ``float(None)`` and crashed the LangGraph node with
``TypeError: float() argument must be a string or a real number, not 'NoneType'``.

Fix: treat ``None`` as "no operator override" and fall through to the
caller-supplied default. Tests below pin every variant of the contract.
"""

from __future__ import annotations

from ragbot.orchestration.query_graph import _pcfg


def test_pcfg_returns_value_when_set() -> None:
    """Operator has set an explicit override → use it."""
    state = {"pipeline_config": {"some_key": 42}}
    assert _pcfg(state, "some_key", default=99) == 42


def test_pcfg_returns_default_when_key_missing() -> None:
    """Pre-Bug-#7c case — key not in pipeline_config dict at all."""
    state = {"pipeline_config": {"other_key": 1}}
    assert _pcfg(state, "some_key", default=99) == 99


def test_pcfg_returns_default_when_value_is_none() -> None:
    """260525 Bug #12 reproducer — key present but value None.

    Bug #7c populates 78 keys as ``raw.get(key, None)``. A missing
    system_config row means the dict carries the key with value None.
    Without this fix, ``float(_pcfg(...))`` crashes on those keys.
    """
    state = {"pipeline_config": {"some_key": None}}
    assert _pcfg(state, "some_key", default=99) == 99


def test_pcfg_returns_default_when_pipeline_config_missing() -> None:
    """Edge case — state has no pipeline_config at all (e.g. unit-test
    state that bypasses the build step)."""
    state = {}
    assert _pcfg(state, "some_key", default=99) == 99


def test_pcfg_returns_default_when_pipeline_config_none() -> None:
    """Edge case — pipeline_config explicitly set to None."""
    state = {"pipeline_config": None}
    assert _pcfg(state, "some_key", default=99) == 99


def test_pcfg_preserves_zero_value_not_treated_as_none() -> None:
    """Zero is a valid operator value (e.g. disabled timeout, 0% ratio).
    Must NOT collapse to default. None-check must be ``is None``, not
    falsy-check."""
    state = {"pipeline_config": {"some_key": 0}}
    assert _pcfg(state, "some_key", default=99) == 0


def test_pcfg_preserves_false_value_not_treated_as_none() -> None:
    """False is a valid operator value (e.g. feature flag opt-out).
    Must NOT collapse to default."""
    state = {"pipeline_config": {"some_key": False}}
    result = _pcfg(state, "some_key", default=True)
    assert result is False


def test_pcfg_preserves_empty_string_not_treated_as_none() -> None:
    """Empty string is a valid operator value (e.g. clear prefix)."""
    state = {"pipeline_config": {"some_key": ""}}
    assert _pcfg(state, "some_key", default="fallback") == ""


def test_pcfg_preserves_empty_list_not_treated_as_none() -> None:
    """Empty list is a valid operator value (e.g. empty allowlist)."""
    state = {"pipeline_config": {"some_key": []}}
    assert _pcfg(state, "some_key", default=["x"]) == []


def test_pcfg_real_bug12_reproducer_float_speculative_timeout() -> None:
    """The exact crash trace from Bug #12 — speculative_retrieve_timeout_s
    populated as None, caller wraps in float(). Post-fix returns default."""
    state = {"pipeline_config": {"speculative_retrieve_timeout_s": None}}
    timeout_default = 2.0
    timeout = float(_pcfg(state, "speculative_retrieve_timeout_s", timeout_default))
    assert timeout == 2.0
