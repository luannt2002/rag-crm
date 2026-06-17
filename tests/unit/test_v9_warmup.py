"""Startup warmup runner tests.

Verifies the warmup runner is fail-soft, never raises, and goes through the
DI container so embedder/LLM provider swaps are zero-code-change. The
"ping" probe text + max_tokens budget are documented constants — never
prompt content.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ragbot.infrastructure.observability import warmup as warmup_mod
from ragbot.shared.constants import (
    DEFAULT_WARMUP_LLM_MAX_TOKENS,
    DEFAULT_WARMUP_LLM_PROBE_TEXT,
)


class _OkEmbedder:
    """Stub embedder whose ``health_check`` returns True after a fast call."""

    def __init__(self) -> None:
        self.calls = 0

    async def health_check(self) -> bool:
        self.calls += 1
        return True


class _RaisingEmbedder:
    """Embedder that raises a network-class error from ``health_check``."""

    async def health_check(self) -> bool:
        raise OSError("dns fail")


class _SlowEmbedder:
    """Embedder whose ``health_check`` exceeds the warmup timeout."""

    async def health_check(self) -> bool:
        await asyncio.sleep(5.0)
        return True


class _OkLLM:
    async def health_check(self) -> bool:
        return True


class _RaisingLLM:
    async def health_check(self) -> bool:
        raise ConnectionError("upstream down")


class _StubContainer:
    def __init__(self, *, embedder: Any, llm: Any) -> None:
        self._embedder = embedder
        self._llm = llm

    def embedder(self) -> Any:
        return self._embedder

    def llm(self) -> Any:
        return self._llm


@pytest.mark.asyncio
async def test_warmup_runs_both_probes_when_healthy() -> None:
    embedder = _OkEmbedder()
    container = _StubContainer(embedder=embedder, llm=_OkLLM())
    summary = await warmup_mod.run_warmup(container, timeout_s=2.0)
    assert summary["embed_ok"] is True
    assert summary["llm_ok"] is True
    # Probe metadata surfaced for log forensics — these are documented
    # constants, NEVER concatenated into a real prompt.
    assert summary["probe_text"] == DEFAULT_WARMUP_LLM_PROBE_TEXT
    assert summary["max_tokens"] == DEFAULT_WARMUP_LLM_MAX_TOKENS
    assert embedder.calls == 1


@pytest.mark.asyncio
async def test_warmup_disabled_via_env_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAGBOT_WARMUP_ENABLED", "false")
    embedder = _OkEmbedder()
    container = _StubContainer(embedder=embedder, llm=_OkLLM())
    summary = await warmup_mod.run_warmup(container, timeout_s=2.0)
    assert summary == {"skipped": True}
    assert embedder.calls == 0


@pytest.mark.asyncio
async def test_warmup_swallows_embed_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force toggle on so the env-default path is exercised
    monkeypatch.delenv("RAGBOT_WARMUP_ENABLED", raising=False)
    container = _StubContainer(embedder=_RaisingEmbedder(), llm=_OkLLM())
    summary = await warmup_mod.run_warmup(container, timeout_s=2.0)
    assert summary["embed_ok"] is False
    # LLM should still run independently
    assert summary["llm_ok"] is True


@pytest.mark.asyncio
async def test_warmup_swallows_llm_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAGBOT_WARMUP_ENABLED", raising=False)
    container = _StubContainer(embedder=_OkEmbedder(), llm=_RaisingLLM())
    summary = await warmup_mod.run_warmup(container, timeout_s=2.0)
    assert summary["embed_ok"] is True
    assert summary["llm_ok"] is False


@pytest.mark.asyncio
async def test_warmup_enforces_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAGBOT_WARMUP_ENABLED", raising=False)
    container = _StubContainer(embedder=_SlowEmbedder(), llm=_OkLLM())
    summary = await warmup_mod.run_warmup(container, timeout_s=0.1)
    # Timeout is treated as a soft failure — not a crash.
    assert summary["embed_ok"] is False


@pytest.mark.asyncio
async def test_warmup_skipped_when_container_lacks_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAGBOT_WARMUP_ENABLED", raising=False)

    class _BrokenContainer:
        def embedder(self) -> Any:
            raise RuntimeError("DI not wired")

        def llm(self) -> Any:
            raise RuntimeError("DI not wired")

    summary = await warmup_mod.run_warmup(_BrokenContainer(), timeout_s=1.0)
    assert summary["embed_ok"] is False
    assert summary["llm_ok"] is False


def test_warmup_enabled_helper_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAGBOT_WARMUP_ENABLED", raising=False)
    assert warmup_mod._warmup_enabled() is True


@pytest.mark.parametrize("flag", ["0", "false", "FALSE", "no", "off"])
def test_warmup_enabled_helper_off_values(
    flag: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAGBOT_WARMUP_ENABLED", flag)
    assert warmup_mod._warmup_enabled() is False


@pytest.mark.parametrize("flag", ["1", "true", "yes", "on"])
def test_warmup_enabled_helper_on_values(
    flag: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAGBOT_WARMUP_ENABLED", flag)
    assert warmup_mod._warmup_enabled() is True
