"""Context-aware refusal sysprompt — reference template + version resolver.

The baseline refusal rule reads:

    "When no context chunks are present, refuse: <oos template>."

That rule refuses too aggressively when retrieval surfaces weak chunks
that still share topical keywords with the question — the LLM ends up
firing the blanket refusal even though the bot owner would prefer a
hedged answer with an explicit uncertainty caveat.

The context-aware refusal template (this module) splits the single
EMPTY_CONTEXT rule into four behaviours keyed off the number of chunks
fed to the LLM and the top reranker score reported in
``state["top_score"]``:

* EMPTY    — chunks_used == 0 → refuse (unchanged behaviour).
* PARTIAL  — chunks_used >= 1 AND top_score >= partial_threshold →
             answer with a grounding qualifier (e.g. "Theo thông tin
             có sẵn, ...").
* WEAK     — chunks_used >= 1 AND top_score <  partial_threshold →
             answer with an explicit uncertainty caveat.
* HALLU_TRAP — intent == "hallu_trap" → ALWAYS refuse (sacred).

The platform DOES NOT inject this text into the LLM prompt. The bot
owner pastes the template into ``bots.system_prompt`` (typically
followed by their own persona / brand copy) and toggles
``plan_limits.sysprompt_version`` to record which reference template
their row is based on. ``resolve_sysprompt_version`` returns that
metadata label with a safe-default fallback so admin tooling can audit
rollout state cheaply.

CLAUDE.md sacred rules honoured:

* Domain-neutral — no brand, industry, or tenant-specific literal.
* App KHÔNG inject text vào LLM prompt — this constant ships as a
  reference template the owner copies; the runtime path never reads
  it into a ``role=system`` message (Quality Gate #10).
* HALLU sacred — Rule 7 hallu_trap refusal cannot be opted out of.
* Zero-hardcode — partial-ground threshold + version labels resolve
  from ``shared/constants.py`` SSoT.
"""

from __future__ import annotations

from typing import Any

from ragbot.shared.constants import (
    ALLOWED_SYSPROMPT_VERSIONS,
    DEFAULT_PARTIAL_GROUND_THRESHOLD,
    DEFAULT_SYSPROMPT_VERSION,
    SYSPROMPT_VERSION_BASELINE,
    SYSPROMPT_VERSION_CONTEXT_AWARE,
)


# Stable section anchors so unit tests can pin the rule structure without
# coupling to free-text wording (which the bot owner is encouraged to
# adapt to their persona). The anchors must appear verbatim in the
# template body.
RULE_ANCHOR_EMPTY: str = "[RULE 4 EMPTY]"
RULE_ANCHOR_PARTIAL: str = "[RULE 5 PARTIAL]"
RULE_ANCHOR_WEAK: str = "[RULE 6 WEAK]"
RULE_ANCHOR_HALLU_TRAP: str = "[RULE 7 HALLU_TRAP]"

# Sacred refusal directive for Rule 7. The bot owner may rewrite the
# surrounding rules to match their voice, but Rule 7 must keep this
# directive verbatim so the HALLU=0 invariant survives prompt edits.
HALLU_TRAP_SACRED_DIRECTIVE: str = (
    "When the upstream intent classifier flags the request as "
    "``hallu_trap`` the assistant MUST refuse using the bot's "
    "configured refusal template, even when retrieved chunks appear "
    "to support an answer. This rule has no opt-out and overrides "
    "every other rule in this prompt."
)


CONTEXT_AWARE_REFUSAL_TEMPLATE: str = f"""\
You are an assistant that answers strictly from the documents wrapped in
<documents>...</documents>. Use only that material; never fabricate facts.

REFUSAL RULES (context-aware) — apply in order:

{RULE_ANCHOR_EMPTY}
When <documents> contains no chunks (chunks_used == 0), reply with the
bot's configured refusal template verbatim. Do not improvise from prior
knowledge.

{RULE_ANCHOR_PARTIAL}
When <documents> contains one or more chunks AND the top retrieved
chunk's reranker score is at-or-above {DEFAULT_PARTIAL_GROUND_THRESHOLD:.2f},
answer the question using ONLY the supplied chunks. Open the answer with
a brief grounding qualifier — for example "Based on the available
documents, ..." — and cite the source chunk inline.

{RULE_ANCHOR_WEAK}
When <documents> contains one or more chunks AND the top retrieved
chunk's reranker score is below {DEFAULT_PARTIAL_GROUND_THRESHOLD:.2f},
answer cautiously: stick strictly to what the chunks actually state,
prefix the answer with an explicit uncertainty caveat — for example
"I found some related material but cannot fully verify it; please
double-check." — and offer the bot's refusal template if the user wants
a definitive answer.

{RULE_ANCHOR_HALLU_TRAP}
{HALLU_TRAP_SACRED_DIRECTIVE}

CITATION: when answering under [RULE 5 PARTIAL] or [RULE 6 WEAK], cite
the supporting chunk inline using the placeholder marker the retrieval
pipeline emits (the platform rewrites the marker into a user-friendly
source label). Do not cite for refusals.
"""


def resolve_sysprompt_version(bot_cfg: Any) -> str:
    """Resolve the per-bot sysprompt version metadata label.

    Reads ``plan_limits.sysprompt_version`` from the bot config with the
    domain-default as a fallback. An unknown value (e.g. a stale label
    from a downgraded deployment) falls back to the baseline so the
    audit signal stays safe-by-default and the caller never sees a
    label it cannot interpret.

    @param bot_cfg: ``BotConfig`` DTO or any object exposing
        ``plan_limits`` as a dict-compatible attribute.
    @return: one of ``ALLOWED_SYSPROMPT_VERSIONS`` — metadata only; the
        caller MUST NOT use the value to substitute prompt text.
    """
    plan_limits = getattr(bot_cfg, "plan_limits", None) or {}
    raw = plan_limits.get("sysprompt_version")
    if isinstance(raw, str) and raw in ALLOWED_SYSPROMPT_VERSIONS:
        return raw
    return DEFAULT_SYSPROMPT_VERSION


__all__ = [
    "CONTEXT_AWARE_REFUSAL_TEMPLATE",
    "HALLU_TRAP_SACRED_DIRECTIVE",
    "RULE_ANCHOR_EMPTY",
    "RULE_ANCHOR_HALLU_TRAP",
    "RULE_ANCHOR_PARTIAL",
    "RULE_ANCHOR_WEAK",
    "SYSPROMPT_VERSION_BASELINE",
    "SYSPROMPT_VERSION_CONTEXT_AWARE",
    "resolve_sysprompt_version",
]
