"""SysPromptAssembler — append platform-default rules to bot.system_prompt.

Implements the J1 multi-tenant scaling pattern documented in
``plans/260529-MASTER-CONSOLIDATED-FIX-ALL/PLAN.md`` (post-Tier-1 J1).

Problem solved
~~~~~~~~~~~~~~
Rules 15-19 (SYNTHESIS_COMPLETE / COMPARISON_VERDICT / ANTI_CSV_ROW_CONFLATE
/ INLINE_SLOT_CAPTURE / STRICT_PROMO_BINDING) are DOMAIN-NEUTRAL and benefit
EVERY bot, not just spa. Per-bot alembic ship (alembic 0142-0145) is an
anti-pattern for tenant scaling — each new tenant onboarding N bots forces
N alembic patches.

Resolution chain
~~~~~~~~~~~~~~~~
::

    Final system_prompt seen by LLM =
        bot.system_prompt                                  (Tier 1: owner content)
      + language_packs[bot.language].sysprompt_default_rules (Tier 6: platform default)
      − rules listed in bot.plan_limits["sysprompt_rules_disabled"]  (per-bot opt-out)

Owner self-service
~~~~~~~~~~~~~~~~~~
- Add a rule platform-wide → alembic UPDATE ``language_packs[code].sysprompt_default_rules``
  → all bots with that locale auto-inherit. No per-bot ship.
- Opt-out per-bot → admin UI edits ``bots.plan_limits``::

    "sysprompt_rules_disabled": ["rule_17", "rule_19"]

  → assembler strips matching rule blocks BEFORE appending.

Sacred-rule alignment
~~~~~~~~~~~~~~~~~~~~~
- Domain-neutral: rule text generic (locale-aware seed in language_packs).
- Multi-tenant: tier 6 resolves per ``bot.language`` automatically; no
  tenant-specific code branch.
- Per-bot override: ``plan_limits`` JSONB list — owner self-service.
- Strategy + DI: constructor takes ``language_pack_service`` port via DI;
  tests inject ``AsyncMock``.
- Graceful degrade: any port failure → return bot.system_prompt unchanged
  (no exception bubbles to caller).
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# DB prompt_key under ``language_packs`` storing platform-default rules.
_RULES_PROMPT_KEY = "sysprompt_default_rules"

# JSONB field under ``bots.plan_limits`` listing rule IDs to strip.
_DISABLED_KEY = "sysprompt_rules_disabled"

# Pattern matching one rule block through the end of the block (next rule
# header OR end of text). Used to strip disabled rules. Two header shapes are
# recognised because the seeded default rules use the markdown form:
#   * markdown  — ``# ANTI-FABRICATE`` / ``# CHỐNG BỊA DỮ LIỆU`` (the LIVE seed
#     format, alembic 20260627/20260701) → captured as ``hname``.
#   * legacy    — ``15. ⭐ SYNTHESIS_COMPLETE`` (numbered) → ``num`` + ``name``.
# Matching ONLY the legacy shape was the GEN-F6 bug: real ``# HEADER`` rules
# never matched, so ``sysprompt_rules_disabled`` was a silent no-op (breaking the
# sacred-exception per-bot opt-out condition). DOTALL so the body captures
# multiline text; non-greedy + a look-ahead on EITHER header shape (or end of
# text) as the boundary so a block never consumes into the next rule.
_RULE_BLOCK_RE = re.compile(
    r"\n\n(?:(?P<num>\d+)\.\s*⭐\s*(?P<name>[A-Z_]+)|#\s*(?P<hname>[^\n]+))"
    r".*?(?=\n\n(?:\d+\.\s*⭐|#\s)|\Z)",
    re.DOTALL,
)


def _norm_rule_name(text: str) -> str:
    """Fold a rule name/header to a comparison key: lower-case, drop spaces /
    hyphens / underscores. So ``"ANTI-FABRICATE"``, ``"anti fabricate"`` and
    ``"anti_fabricate"`` all match the ``# ANTI-FABRICATE`` block header the
    owner sees in ``GET /admin/bots/{id}/effective-prompt``."""
    return re.sub(r"[\s_\-]+", "", text.strip().lower())


class SysPromptAssembler:
    """Assemble final system_prompt from owner column + platform-default tier.

    Constructor injection of ``language_pack_service`` keeps the service
    transport-agnostic. Production wiring (in :mod:`ragbot.bootstrap`)
    passes the real ``LanguagePackService``; unit tests pass ``AsyncMock``.
    """

    def __init__(self, *, language_pack_service: Any) -> None:
        self._language_pack_service = language_pack_service

    async def assemble(
        self,
        *,
        bot: Any,
        language: str | None = None,
    ) -> str:
        """Return ``bot.system_prompt + platform-default rules`` with opt-outs applied.

        @param bot: bot row / config object. Read via getattr so SQLAlchemy
            mapped object, ``BotConfig`` DTO, and ``SimpleNamespace`` all work.
        @param language: locale code (``"vi"``, ``"en"``, ...); falls back to
            ``getattr(bot, "language", None)`` then constants
            ``DEFAULT_LANGUAGE``.

        @return: assembled system_prompt. Owner content always comes first
            (authoritative); platform-default rules append after. Empty
            platform default → returns bot.system_prompt unchanged. Any
            port error → graceful degrade to bot.system_prompt.
        """
        base = self._extract_bot_prompt(bot)
        locale = self._resolve_locale(bot, language)

        try:
            platform_rules = await self._fetch_platform_rules(locale)
        except Exception as exc:  # noqa: BLE001 — graceful degrade
            logger.debug(
                "sysprompt_assembler_platform_fetch_failed",
                locale=locale,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return base

        if not platform_rules:
            return base

        # Per-bot opt-out
        disabled = self._extract_disabled_rules(bot)
        if disabled:
            platform_rules = self._strip_rules(platform_rules, disabled)
            if not platform_rules.strip():
                return base

        return base + platform_rules

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #
    async def _fetch_platform_rules(self, locale: str) -> str:
        """Best-effort read of ``language_packs[code][sysprompt_default_rules]``."""
        get_fn = getattr(self._language_pack_service, "get", None)
        if get_fn is None:
            return ""
        raw = await get_fn(locale, _RULES_PROMPT_KEY)
        return str(raw) if raw else ""

    @staticmethod
    def _resolve_locale(bot: Any, explicit_language: str | None) -> str:
        """Pick locale: explicit > bot.language > constants DEFAULT_LANGUAGE."""
        from ragbot.shared.constants import DEFAULT_LANGUAGE  # noqa: PLC0415 — avoid module-load cycle
        if explicit_language and explicit_language.strip():
            return explicit_language.strip()
        bot_lang = getattr(bot, "language", None)
        if bot_lang and str(bot_lang).strip():
            return str(bot_lang).strip()
        return DEFAULT_LANGUAGE

    @staticmethod
    def _extract_bot_prompt(bot: Any) -> str:
        """Pull ``system_prompt`` off the bot row tolerantly."""
        if bot is None:
            return ""
        value = getattr(bot, "system_prompt", None)
        if value is None and isinstance(bot, dict):
            value = bot.get("system_prompt")
        return str(value) if value else ""

    @staticmethod
    def _extract_disabled_rules(bot: Any) -> list[str]:
        """Pull ``plan_limits[sysprompt_rules_disabled]`` list of rule IDs.

        Accepts BOTH addressing schemes (the seeded default rules are named,
        legacy rules are numbered):
        - numbered — ``["rule_17", "rule_19"]``, ``[17, 19]``, ``["17"]``
        - named    — ``["ANTI-FABRICATE", "anti_pad_list"]`` (matches the
          ``# HEADER`` the owner sees in the effective prompt)
        Returns the raw non-empty string tokens; ``_strip_rules`` classifies
        each into a numeric-match or name-match key.
        """
        if bot is None:
            return []
        plan_limits = getattr(bot, "plan_limits", None)
        if plan_limits is None and isinstance(bot, dict):
            plan_limits = bot.get("plan_limits")
        if not isinstance(plan_limits, dict):
            return []
        raw = plan_limits.get(_DISABLED_KEY, [])
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                out.append(s)
        return out

    @staticmethod
    def _strip_rules(rules_text: str, disabled_ids: list[str]) -> str:
        """Strip rule blocks whose number OR name matches a disabled entry.

        Each block starts with either ``"NN. ⭐ RULE_NAME"`` (legacy numbered)
        or ``"# RULE_NAME"`` (the seeded markdown form) — both captured by
        ``_RULE_BLOCK_RE``. A disabled entry that carries digits and no other
        letters (``"17"``, ``"rule_17"``) matches by the block's ``num`` group;
        any other entry matches by folded NAME against the block's ``name`` /
        ``hname`` header (so a ``# HEADER`` default rule is actually strippable —
        the GEN-F6 fix).
        """
        disabled_nums: set[str] = set()
        disabled_names: set[str] = set()
        for rid in disabled_ids:
            digits = "".join(c for c in rid if c.isdigit())
            # "rule_17"/"17" → pure numeric addressing; anything with its own
            # alphabetic identity (beyond a "rule" prefix) → name addressing.
            residual_letters = re.sub(r"[^a-z]", "", rid.lower()).replace("rule", "")
            if digits and not residual_letters:
                disabled_nums.add(digits)
            else:
                disabled_names.add(_norm_rule_name(rid))
        if not disabled_nums and not disabled_names:
            return rules_text

        def _maybe_strip(m: re.Match) -> str:
            gd = m.groupdict()
            num = gd.get("num")
            if num and num in disabled_nums:
                return ""
            name = gd.get("name") or gd.get("hname")
            if name and disabled_names and _norm_rule_name(name) in disabled_names:
                return ""
            return m.group(0)

        return _RULE_BLOCK_RE.sub(_maybe_strip, rules_text)


__all__ = [
    "SysPromptAssembler",
]
