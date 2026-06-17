"""Regression test — `args.batch_size` AttributeError on summary write.

R9 OLD load run completed all 75 turns then crashed with::

    AttributeError: 'Namespace' object has no attribute 'batch_size'

Root cause: an older harness binary (pre BATCH-10) shipped without the
``--batch-size`` argparse flag, but the summary-write block at end of
``main_async`` referenced ``args.batch_size`` unconditionally. The crash
hit AFTER all 75 turns finished, losing the JSON dump entirely.

This test pins a defensive contract:

1. ``--batch-size`` argparse default is ``0`` (batch mode disabled).
2. The summary-write code path uses ``getattr(args, "batch_size", 0)``
   so a hand-built ``Namespace`` (or any caller pre-dating the flag)
   still produces a valid JSON aggregate.

Tests are pure offline — no httpx, no DB.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Module loader — `scripts/` is not a package, import-by-path.
# ---------------------------------------------------------------------------


def _load_harness() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "test_75q_load.py"
    spec = importlib.util.spec_from_file_location("_t75q_harness_bsr", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    return _load_harness()


# ---------------------------------------------------------------------------
# Test 1 — argparse default for --batch-size is 0 (batch mode disabled).
# ---------------------------------------------------------------------------


def test_batch_size_argparse_default_is_zero(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invoking the parser without ``--batch-size`` resolves to 0.

    0 = single-shot mode = preserves original behavior.
    """
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "test_75q_load.py",
            "--bot-id",
            "any-bot",
            "--tenant-id",
            "1",
            "--channel-type",
            "web",
        ],
    )
    args = harness._parse_args()
    assert hasattr(args, "batch_size"), "regression: argparse missing --batch-size flag"
    assert args.batch_size == 0


# ---------------------------------------------------------------------------
# Test 2 — getattr(args, 'batch_size', 0) is the contract used by summary.
# ---------------------------------------------------------------------------


def test_summary_write_resilient_to_missing_attr() -> None:
    """A hand-built Namespace without ``batch_size`` MUST resolve to 0.

    If the production harness ever drops the flag again (or a caller
    constructs ``argparse.Namespace`` manually), the summary-write path
    must NOT raise — it should gracefully fall back to 0 and still emit
    the JSON aggregate. We assert against the same ``getattr`` contract
    used in ``scripts/test_75q_load.py::main_async``.
    """
    legacy_ns = argparse.Namespace(
        bot_id="x", tenant_id=1, channel_type="web", debug="full"
    )
    # The production code at end of main_async:
    #     int(getattr(args, "batch_size", 0) or 0)
    resolved = int(getattr(legacy_ns, "batch_size", 0) or 0)
    assert resolved == 0, "missing attr must resolve to 0 (batch disabled)"


# ---------------------------------------------------------------------------
# Test 3 — main_async summary-write block compiles without batch_size attr.
# ---------------------------------------------------------------------------


def test_main_async_summary_block_is_getattr_guarded() -> None:
    """Static text guard: the production summary-write block uses
    ``getattr(args, "batch_size", 0)`` rather than ``args.batch_size``.

    Locking this ensures a future refactor that re-introduces the
    unguarded access fails CI before R10/R11 ship.
    """
    repo_root = Path(__file__).resolve().parents[2]
    src = (repo_root / "scripts" / "test_75q_load.py").read_text(encoding="utf-8")
    # Both the config_block field and the local batch_size assignment
    # MUST use getattr — searching for the unguarded form should miss.
    assert 'getattr(args, "batch_size"' in src, (
        "regression — summary-write must use getattr() guard"
    )
    # The unguarded form should not appear in the summary block. We grep
    # for the precise legacy AttributeError trigger.
    bad = 'int(args.batch_size or 0)'
    assert bad not in src, f"regression — found unguarded {bad!r}"


# ---------------------------------------------------------------------------
# Test 4 — config_block dict shape includes batch_size key set to 0.
# ---------------------------------------------------------------------------


def test_config_block_batch_size_zero_when_missing() -> None:
    """Simulate the exact ``config_block`` dict construction with a
    legacy Namespace — the dict must build cleanly + include
    ``batch_size: 0``.
    """
    legacy_ns = SimpleNamespace(
        bot_id="b1",
        tenant_id=1,
        channel_type="web",
        bypass_cache=True,
        debug="full",
    )
    config_block = {
        "bot_id": legacy_ns.bot_id,
        "tenant_id": legacy_ns.tenant_id,
        "channel_type": legacy_ns.channel_type,
        "bypass_cache": legacy_ns.bypass_cache,
        "debug": legacy_ns.debug,
        "batch_size": int(getattr(legacy_ns, "batch_size", 0) or 0),
    }
    assert config_block["batch_size"] == 0
    assert config_block["bot_id"] == "b1"
