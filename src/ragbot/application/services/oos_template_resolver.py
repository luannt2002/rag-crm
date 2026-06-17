"""OosTemplateResolver — per-bot OOS / refuse text resolution chain.

Resolves the refusal text emitted when the pipeline short-circuits with no
context (zero graded chunks, OOS intent, output guardrail block, etc.).
Mirrors the master 7-tier resolver pattern documented in
``plans/260529-MASTER-CONSOLIDATED-FIX-ALL/PLAN.md``::

    Tier 1: bots.oos_answer_template                    (owner column)
    Tier 2: bots.plan_limits["oos_answer_template"]     (owner JSONB override)
    Tier 3: workspace_config["oos_answer_template"]     (workspace tier, Phase 4)
    Tier 4: tenants.<column>                            (tenant tier, partial)
    Tier 5: system_config["oos_answer_template"]        (platform default)
    Tier 6: language_packs[code]["refuse_message"]      (per-locale fallback)
    Tier 7: shared/constants.DEFAULT_OOS_ANSWER_TEMPLATE (em-of-line, empty)

Tiers 3+4 are no-ops in this revision — Phase 4 will introduce
``workspace_config`` and a tenant-level prompt override. The resolver is
already wired for them so the diff stays surgical.

Why a dedicated service (vs. inline ``_pcfg``):

- ``_pcfg`` only reads ``state["pipeline_config"]`` (the flattened dict
  built upstream); it CANNOT reach ``system_config`` or
  ``language_packs`` lazily, which is exactly the gap that left 10/13
  bots with empty refuse text (verified DB scan 2026-05-29).
- Moving the chain into a service makes the resolution testable in
  isolation (inject ``MagicMock`` for each port), keeps orchestration
  domain-neutral, and lets bot owners self-serve via DB without code
  changes (CLAUDE.md sacred rule: zero-hardcode + multi-tenant).

Sacred-rule compliance:

- Domain-neutral: no brand / industry text inline; locale-specific seeds
  live in ``language_packs`` and ``shared/i18n.py`` fallback pack.
- Zero-hardcode: tier 7 returns ``DEFAULT_OOS_ANSWER_TEMPLATE`` which is
  ``""`` (neutral) — non-empty defaults must come from DB.
- Strategy + DI: constructor takes ports; orchestration imports the
  service via :mod:`bootstrap`.
- HALLU=0: returning ``""`` is acceptable (caller decides — better than
  fabricating a refusal).
"""

from __future__ import annotations

from typing import Any

import structlog

from ragbot.shared.constants import DEFAULT_LANGUAGE, DEFAULT_OOS_ANSWER_TEMPLATE

logger = structlog.get_logger(__name__)


# Per-locale refuse text lives under this prompt_key in ``language_packs``.
# Seeded by alembic 0136 for ``vi`` and ``en``; additional locales are a
# pure DB seed away (no code change).
_REFUSE_MESSAGE_KEY = "refuse_message"

# Platform-default lives under this key in ``system_config``. Operators set
# it via admin UI / alembic; absence falls through to language pack tier.
_SYSTEM_CONFIG_KEY = "oos_answer_template"

# Per-bot override path inside ``bots.plan_limits`` JSONB. Owners can ship
# tenant-specific phrasing without touching ``bots.oos_answer_template``
# (handy when the column is reserved for a tenant-portal default while a
# specific channel/bot needs its own variant).
_PLAN_LIMITS_KEY = "oos_answer_template"


class OosTemplateResolver:
    """Resolve the OOS / refuse template for a given bot + locale.

    Constructor injection of the two ports keeps the resolver transport-
    agnostic. Production wiring (in :mod:`ragbot.bootstrap`) passes the
    real :class:`SystemConfigService` and :class:`LanguagePackService`;
    unit tests pass ``MagicMock`` / ``AsyncMock`` instances.
    """

    def __init__(
        self,
        *,
        config_service: Any,
        language_pack_service: Any,
    ) -> None:
        self._config_service = config_service
        self._language_pack_service = language_pack_service

    async def resolve(
        self,
        *,
        bot: Any,
        language: str | None = None,
        bot_name_substitution: str | None = None,
    ) -> str:
        """Walk the 7-tier chain; return first non-empty hit or ``""``.

        @param bot: bot row / config object. Read via getattr so SQLAlchemy
            model, ``BotConfig`` DTO, and ``SimpleNamespace`` all work.
        @param language: locale code (``"vi"``, ``"en"``, ...); ``None``
            falls back to :data:`DEFAULT_LANGUAGE`.
        @param bot_name_substitution: optional value to substitute into the
            ``{bot_name}`` placeholder when present in the resolved
            template. Caller passes ``bot.bot_name`` in production; tests
            can override.

        @return: resolved refuse template after ``{bot_name}`` substitution,
            or ``""`` if every tier is empty. Caller decides whether to
            emit empty answer or short-circuit elsewhere.
        """
        locale = (language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE

        # Tier 1 — bot.oos_answer_template column (owner self-service).
        tier1 = _coerce_str(getattr(bot, "oos_answer_template", None))
        if tier1:
            return _substitute(tier1, bot_name_substitution)

        # Tier 2 — bots.plan_limits["oos_answer_template"] JSONB override.
        tier2 = _extract_plan_limits_value(bot)
        if tier2:
            return _substitute(tier2, bot_name_substitution)

        # Tier 3 — workspace_config (Phase 4 placeholder; no-op today).
        # Intentional pass-through. Wiring lives in Phase 4 patch.

        # Tier 4 — tenants.<column> (Phase 4 placeholder; no-op today).
        # The ``tenants`` table currently exposes rate / quota columns
        # only; the OOS template column will be added in Phase 4 when
        # business introduces the tenant_admin role.

        # Tier 5 — system_config["oos_answer_template"] platform default.
        tier5 = await self._fetch_system_config()
        if tier5:
            return _substitute(tier5, bot_name_substitution)

        # Tier 6 — language_packs[code]["refuse_message"] locale fallback.
        tier6 = await self._fetch_language_pack(locale)
        if tier6:
            return _substitute(tier6, bot_name_substitution)

        # Tier 7 — neutral em-of-line safety net. ``""`` by sacred rule.
        return DEFAULT_OOS_ANSWER_TEMPLATE

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #
    async def _fetch_system_config(self) -> str:
        """Best-effort read of the platform-default template."""
        get_fn = getattr(self._config_service, "get", None)
        if get_fn is None:
            return ""
        try:
            raw = await get_fn(_SYSTEM_CONFIG_KEY, None)
        except Exception as exc:  # noqa: BLE001 — graceful degrade to next tier
            logger.debug(
                "oos_resolver_system_config_fetch_failed",
                key=_SYSTEM_CONFIG_KEY,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ""
        return _coerce_str(raw)

    async def _fetch_language_pack(self, locale: str) -> str:
        """Best-effort read of the per-locale refuse message."""
        get_fn = getattr(self._language_pack_service, "get", None)
        if get_fn is None:
            return ""
        try:
            raw = await get_fn(locale, _REFUSE_MESSAGE_KEY)
        except Exception as exc:  # noqa: BLE001 — graceful degrade to neutral default
            logger.debug(
                "oos_resolver_language_pack_fetch_failed",
                locale=locale,
                prompt_key=_REFUSE_MESSAGE_KEY,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ""
        return _coerce_str(raw)


# ---------------------------------------------------------------------- #
# Helpers                                                                 #
# ---------------------------------------------------------------------- #
def _coerce_str(value: Any) -> str:
    """Treat ``None`` / non-string / empty as missing tier."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    # Tolerate JSONB stringification: a dict / list reaching here is a
    # config drift — log once at debug and treat as empty.
    logger.debug(
        "oos_resolver_unexpected_type",
        value_type=type(value).__name__,
    )
    return ""


def _extract_plan_limits_value(bot: Any) -> str:
    """Pull ``plan_limits["oos_answer_template"]`` off the bot row.

    Tolerates SQLAlchemy mapped object (attribute access), BotConfig DTO
    (attribute access), raw dict, and ``SimpleNamespace`` used in tests.
    """
    if bot is None:
        return ""
    plan_limits = getattr(bot, "plan_limits", None)
    if plan_limits is None and isinstance(bot, dict):
        plan_limits = bot.get("plan_limits")
    if not isinstance(plan_limits, dict):
        return ""
    return _coerce_str(plan_limits.get(_PLAN_LIMITS_KEY))


def _substitute(template: str, bot_name: str | None) -> str:
    """Replace ``{bot_name}`` placeholder if present and value provided.

    Backward-compatible with the legacy ``_oos_text`` behaviour. When
    caller does not pass a substitution value, the placeholder remains as
    literal text — owners using the placeholder MUST configure bot_name
    upstream or set an explicit template without the placeholder.
    """
    if "{bot_name}" not in template:
        return template
    return template.replace("{bot_name}", bot_name or "")


__all__ = [
    "OosTemplateResolver",
]
