"""Reference sysprompt templates + per-bot version resolver.

These modules ship REFERENCE TEMPLATES the bot owner copies into
``bots.system_prompt`` when authoring a new bot. The platform never
substitutes a template into the LLM call at runtime: the LLM-bound
``system_prompt`` is always read verbatim from the bot row (CLAUDE.md
"Application MINDSET — Bot owner owns everything"; Quality Gate #10
"Application KHÔNG inject text/template/rule vào answer LLM").

The accompanying ``plan_limits.sysprompt_version`` knob is metadata
only — it records which reference template the owner started from so
admin rollout tooling can audit progress without inspecting the prompt
body. ``resolve_sysprompt_version`` reads that knob with a safe-default
fallback.
"""

from ragbot.orchestration.system_prompts.context_aware_refusal_template import (
    CONTEXT_AWARE_REFUSAL_TEMPLATE,
    resolve_sysprompt_version,
)


__all__ = [
    "CONTEXT_AWARE_REFUSAL_TEMPLATE",
    "resolve_sysprompt_version",
]
