"""Pin test — shared code paths must not hardcode a tenant/industry service.

CLAUDE.md sacred (Domain-neutral rule): the platform serves many tenants
across many industries, so shared ``src/ragbot`` code MUST NOT illustrate
itself with a specific service name (a hair-washing / skincare / hair-removal
salon term). Such literals leaked into three shared modules as few-shot
examples and docstrings:

- ``shared/vi_tokenizer.py``  — diacritic-removal docstring example.
- ``shared/i18n.py``          — ``prompt_rewriter`` few-shot example
  (VI + EN), which is sent verbatim into the rewriter LLM call.
- ``interfaces/http/routes/test_chat/bot_insights_routes.py`` — the
  golden-set generation ``system_prompt`` typo example.

The fix replaces each domain-specific example with a domain-neutral one
that demonstrates the same mechanism (filler removal / diacritic stripping /
typo handling) without naming any industry. The language-pack mechanism and
the tokenizer must keep working for bots that DO configure such vocabulary
through ``custom_vocabulary`` / ``system_config`` — those layers are
unaffected by this test.
"""

from __future__ import annotations

from pathlib import Path

from ragbot.shared.i18n import get_pack
from ragbot.shared.vi_tokenizer import remove_diacritics

_SRC_ROOT: Path = Path(__file__).resolve().parents[2] / "src" / "ragbot"

# Industry/service literals that must not appear in shared source. These are
# concrete salon-service names (hair washing / skincare / hair removal) — a
# tenant-specific vocabulary that belongs in per-bot ``custom_vocabulary`` or
# ``system_config``, never inlined as a platform example.
_FORBIDDEN_SERVICE_LITERALS: tuple[str, ...] = (
    "gội đầu",
    "goi dau",
    "triệt lông",
    "triet long",
    "shampoo",
)

# The three shared modules that carried the violation (relative to _SRC_ROOT).
_GUARDED_FILES: tuple[str, ...] = (
    "shared/vi_tokenizer.py",
    "shared/i18n.py",
    "interfaces/http/routes/test_chat/bot_insights_routes.py",
)


def test_no_service_literal_in_guarded_shared_files() -> None:
    """No salon-service literal may appear in the three guarded shared files."""
    leaks: list[tuple[str, str]] = []
    for rel in _GUARDED_FILES:
        content = (_SRC_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for literal in _FORBIDDEN_SERVICE_LITERALS:
            if literal in content:
                leaks.append((rel, literal))
    assert not leaks, (
        "Tenant/industry service literal leaked into shared code. Move such "
        "vocabulary to per-bot custom_vocabulary / system_config and use a "
        f"domain-neutral example instead. Found: {leaks}"
    )


def test_rewriter_prompt_still_demonstrates_filler_removal() -> None:
    """The rewriter few-shot must keep a concrete filler-removal example.

    Removing the domain term must not gut the prompt: both packs must still
    show at least one ``User: ... → Output: ...`` example so the LLM keeps
    its filler-stripping behaviour (the mechanism the example teaches).
    """
    for code in ("vi", "en"):
        prompt = get_pack(code).prompt_rewriter
        assert "User:" in prompt and "Output:" in prompt, (
            f"{code} prompt_rewriter lost its few-shot example after the "
            f"domain-neutral rewrite. Got: {prompt[:200]}"
        )
        for literal in _FORBIDDEN_SERVICE_LITERALS:
            assert literal not in prompt, (
                f"{code} prompt_rewriter still contains service literal "
                f"{literal!r}."
            )


def test_remove_diacritics_works_on_generic_vocab() -> None:
    """Diacritic stripping is a generic mechanism — verify on neutral words.

    The tokenizer must strip Vietnamese diacritics for ANY vocabulary, not a
    hardcoded service term. Assert the mechanism on domain-neutral input.
    """
    assert remove_diacritics("tài liệu") == "tai lieu"
    assert remove_diacritics("dịch vụ") == "dich vu"
    assert remove_diacritics("chính sách") == "chinh sach"
