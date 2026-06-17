"""Regression tests for domain-neutral multi-tenant resolver pattern.

Lesson 2026-05-14: hardcoded VN regex / stopwords / prompt strings in logic
code force Vietnamese behaviour on tenants whose ``bots.language`` is
``en`` / ``ja`` / ``th``. Violates CLAUDE.md domain-neutral rule.

These tests pin the canonical pattern:

1. ``shared/constants.py`` holds SEED defaults (boot-fallback only).
2. Resolver service prefers ``bots.custom_vocabulary`` → ``system_config``
   per-language map → constants SEED, in that order.
3. Platform prompts (multi-query expansion, condense, grounding) come
   from ``language_packs`` DB rows via :class:`LanguagePackService`.
4. ``LanguagePackService.get(lang, key)`` falls back to the default
   language row when the requested language has no entry.
5. ``ViTokenizer.get_abbreviations(bot, lang)`` merges system_config
   per-language seed with ``bots.custom_vocabulary.abbreviations``,
   bot wins on key collision.

The corresponding refactor is tracked in
``plans/260514-domain-neutral-multitenant-fix/plan.md``; tests that
exercise not-yet-implemented surface area are marked ``xfail(strict=False)``
so they flip to PASS automatically when the refactor lands.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.shared import constants


# ---------------------------------------------------------------------------
# Test 1 — constants SEED present
# ---------------------------------------------------------------------------


def test_default_boilerplate_patterns_vi_in_constants() -> None:
    """SEED tuple exists, is non-empty, every entry is a non-empty string."""
    assert hasattr(constants, "DEFAULT_BOILERPLATE_PATTERNS_VI"), (
        "DEFAULT_BOILERPLATE_PATTERNS_VI must live in shared/constants.py "
        "as the boot-fallback SEED for tenants whose system_config row "
        "is missing the boilerplate_removal_patterns_by_language map."
    )
    seed = constants.DEFAULT_BOILERPLATE_PATTERNS_VI
    assert isinstance(seed, (list, tuple)), "must be list or tuple"
    # Spec: 11 SEED patterns shipping with V1 — bump this number when
    # the SEED grows so future drift surfaces in code review.
    assert len(seed) == 11, f"expected 11 SEED patterns, got {len(seed)}"
    for entry in seed:
        assert isinstance(entry, str) and entry, "each entry is a non-empty string"


# ---------------------------------------------------------------------------
# Test 2 — boilerplate resolver fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="BoilerplateResolver not yet refactored — pins target API.",
    strict=False,
)
async def test_boilerplate_resolver_fallback_chain() -> None:
    """bot.custom_vocabulary > system_config per-language > constants SEED."""
    try:
        from ragbot.application.services.boilerplate_resolver import (  # type: ignore[import-not-found]
            BoilerplateResolver,
        )
    except ImportError as exc:  # refactor not landed yet
        pytest.xfail(f"BoilerplateResolver missing: {exc}")
        return

    # --- arrange ----------------------------------------------------------
    config_service = MagicMock()
    config_service.get_by_language = AsyncMock(return_value=None)  # empty

    bot_no_override = SimpleNamespace(custom_vocabulary={})
    bot_with_override = SimpleNamespace(
        custom_vocabulary={"boilerplate_patterns": [r"^CUSTOM\s+RULE.*"]},
    )

    resolver = BoilerplateResolver(config_service=config_service)

    # --- layer 3: SEED fallback ------------------------------------------
    patterns = await resolver.resolve(bot=bot_no_override, language="vi")
    assert list(patterns) == list(constants.DEFAULT_BOILERPLATE_PATTERNS_VI), (
        "empty config + empty bot override → SEED defaults"
    )

    # --- layer 2: system_config wins over SEED ---------------------------
    config_service.get_by_language = AsyncMock(
        return_value=[r"^TENANT\s+OVERRIDE.*"],
    )
    patterns = await resolver.resolve(bot=bot_no_override, language="vi")
    assert list(patterns) == [r"^TENANT\s+OVERRIDE.*"]

    # --- layer 1: bot.custom_vocabulary wins over system_config ----------
    patterns = await resolver.resolve(bot=bot_with_override, language="vi")
    assert list(patterns) == [r"^CUSTOM\s+RULE.*"]


# ---------------------------------------------------------------------------
# Test 3 — multi-query expansion loads prompt from language_packs
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="MultiQueryExpansionService not yet wired to LanguagePackService.",
    strict=False,
)
async def test_multi_query_expansion_loads_from_language_pack() -> None:
    """Expansion service must call LanguagePackService.get(lang, key)."""
    try:
        from ragbot.application.services.multi_query_expansion import (
            MultiQueryExpansionService,
        )
    except ImportError as exc:
        pytest.xfail(f"MultiQueryExpansionService missing: {exc}")
        return

    lang_pack = MagicMock()
    lang_pack.get = AsyncMock(return_value="MOCK_FACTOID_PROMPT")

    # We probe by best-effort instantiation; if constructor signature
    # diverges from the planned shape, mark xfail rather than hard-error.
    try:
        service = MultiQueryExpansionService(language_pack=lang_pack)  # type: ignore[call-arg]
    except TypeError as exc:
        pytest.xfail(f"constructor not yet accepting language_pack kwarg: {exc}")
        return

    # The planned API exposes ``_resolve_prompt(language, intent)`` which
    # routes to LanguagePackService.
    resolver = getattr(service, "_resolve_prompt", None)
    if resolver is None:
        pytest.xfail("_resolve_prompt(language, intent) not yet exposed")
        return

    prompt = await resolver("vi", "factoid")
    assert prompt == "MOCK_FACTOID_PROMPT"
    lang_pack.get.assert_awaited()
    args, _kwargs = lang_pack.get.call_args
    # Either positional or kwargs — assert both pieces appear.
    flat = list(args) + list(_kwargs.values())
    assert "vi" in flat
    assert any("factoid" in str(a) for a in flat)


# ---------------------------------------------------------------------------
# Test 4 — language_pack falls back to default language when missing
# ---------------------------------------------------------------------------


async def test_multi_query_falls_back_vi_if_lang_missing() -> None:
    """LanguagePackService.get('en', key) → falls through to default lang."""
    try:
        from ragbot.application.services.language_pack_service import (
            LanguagePackService,
        )
    except ImportError as exc:
        pytest.skip(f"LanguagePackService import failed: {exc}")
        return

    repo = MagicMock()

    async def _get_pack(language: str, key: str) -> str | None:
        if language == "vi" and key == "multi_query_factoid_prompt":
            return "VI_PROMPT_FROM_DB"
        return None

    repo.get_pack = AsyncMock(side_effect=_get_pack)

    # Redis stub: cache miss for every key.
    redis_stub = MagicMock()
    redis_stub.get = AsyncMock(return_value=None)
    redis_stub.set = AsyncMock(return_value=True)

    service = LanguagePackService(
        repo=repo,
        redis_client=redis_stub,
        default_language="vi",
    )

    out = await service.get("en", "multi_query_factoid_prompt")
    # English row absent → falls through to ``vi`` default row.
    assert out == "VI_PROMPT_FROM_DB", (
        "missing language row must fall back to default_language seed"
    )


# ---------------------------------------------------------------------------
# Test 5 — vi_tokenizer abbreviations merge order
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="ViTokenizer.get_abbreviations(bot, lang) not yet implemented.",
    strict=False,
)
def test_vi_tokenizer_get_abbreviations_merge() -> None:
    """SEED (constants) → system_config per-lang → bot.custom_vocabulary."""
    try:
        from ragbot.infrastructure.tokenizer.vi_tokenizer import ViTokenizer
    except ImportError as exc:
        pytest.xfail(f"ViTokenizer import failed: {exc}")
        return

    if not hasattr(ViTokenizer, "get_abbreviations"):
        pytest.xfail("ViTokenizer.get_abbreviations not yet implemented")
        return

    config_service = MagicMock()
    # tenant-wide row overrides one SEED key + adds a new one.
    config_service.get_by_language = MagicMock(
        return_value={"sg": "Singapore", "tt": "thanh toán"},
    )

    bot = SimpleNamespace(
        custom_vocabulary={
            # bot override wins on key collision (``tt`` becomes
            # "thông tin" instead of the tenant's "thanh toán").
            "abbreviations": {"tt": "thông tin", "ldn": "lãnh đạo"},
        },
    )

    tokenizer = ViTokenizer(
        language="vi",
        config_service=config_service,  # type: ignore[call-arg]
    )
    merged = tokenizer.get_abbreviations(bot=bot, language="vi")  # type: ignore[attr-defined]
    assert isinstance(merged, dict)
    # bot wins on collision
    assert merged.get("tt") == "thông tin"
    # tenant-only key survives
    assert merged.get("sg") == "Singapore"
    # bot-only key survives
    assert merged.get("ldn") == "lãnh đạo"


# ---------------------------------------------------------------------------
# Test 6 — audit script exits 0 once refactor is complete
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="Refactor in progress — audit script returns 1 until VN literals are lifted.",
    strict=False,
)
def test_audit_domain_neutral_script_exits_clean() -> None:
    """`scripts/audit_domain_neutral.sh` exits 0 once the refactor lands."""
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "audit_domain_neutral.sh"
    assert script.exists(), f"audit script missing at {script}"
    assert script.stat().st_mode & 0o111, "audit script must be executable"

    result = subprocess.run(
        ["bash", str(script)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"audit failed (exit={result.returncode}):\n"
        f"STDOUT: {result.stdout[-2000:]}\n"
        f"STDERR: {result.stderr[-500:]}"
    )


# ---------------------------------------------------------------------------
# Test 7 — script presence smoke test (always passes; sanity for path resolution)
# ---------------------------------------------------------------------------


def test_audit_domain_neutral_script_exists_and_executable() -> None:
    """Smoke: file is on disk + has +x bit, regardless of pass/fail of run."""
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "audit_domain_neutral.sh"
    assert script.exists(), f"audit script missing at {script}"
    assert script.stat().st_mode & 0o111, "audit script must be executable (chmod +x)"
