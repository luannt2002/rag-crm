"""Pin test — callback constants exist in ``shared/constants.py`` and
have sensible values (zero-hardcode guard).

Ensures ``DEFAULT_CALLBACK_TIMEOUT_S``, ``DEFAULT_CALLBACK_MAX_RETRIES``
and ``DEFAULT_CALLBACK_BACKOFF_BASE_S`` are importable and not magic
inline literals. If a future edit removes them from constants.py this
test fails loudly before the CI run.
"""
from __future__ import annotations

import pytest

from ragbot.shared.constants import (
    DEFAULT_CALLBACK_BACKOFF_BASE_S,
    DEFAULT_CALLBACK_MAX_RETRIES,
    DEFAULT_CALLBACK_TIMEOUT_S,
)


def test_callback_timeout_is_positive_int():
    """``DEFAULT_CALLBACK_TIMEOUT_S`` must be a positive integer."""
    assert isinstance(DEFAULT_CALLBACK_TIMEOUT_S, int)
    assert DEFAULT_CALLBACK_TIMEOUT_S > 0


def test_callback_max_retries_is_positive_int():
    """``DEFAULT_CALLBACK_MAX_RETRIES`` must be a positive integer."""
    assert isinstance(DEFAULT_CALLBACK_MAX_RETRIES, int)
    assert DEFAULT_CALLBACK_MAX_RETRIES > 0


def test_callback_backoff_base_is_positive_float():
    """``DEFAULT_CALLBACK_BACKOFF_BASE_S`` must be a positive float."""
    assert isinstance(DEFAULT_CALLBACK_BACKOFF_BASE_S, float)
    assert DEFAULT_CALLBACK_BACKOFF_BASE_S > 0.0


def test_callback_timeout_exported_in_all():
    """All three constants are listed in ``constants.__all__``."""
    import ragbot.shared.constants as _mod

    all_names = set(getattr(_mod, "__all__", []))
    for name in (
        "DEFAULT_CALLBACK_TIMEOUT_S",
        "DEFAULT_CALLBACK_MAX_RETRIES",
        "DEFAULT_CALLBACK_BACKOFF_BASE_S",
    ):
        assert name in all_names, (
            f"{name} missing from constants.__all__ — "
            "add it so callers can ``from ragbot.shared.constants import *``"
        )
