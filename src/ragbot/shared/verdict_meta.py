"""Deterministic guard self-verdict summary for ``request_logs.metadata_json``.

OBSERVE-ONLY forensics — never a grade. The pipeline cannot self-assess answer
*correctness*: that is the graded/feedback column ``is_correct`` (owner- or
judge-marked; a grounding BLOCK is a HALLU that was PREVENTED from reaching the
user, so it must never be counted as ``is_correct = False``). What the pipeline
CAN record is its own deterministic guard verdict — did the grounding judge
flag the answer, did the numeric-fidelity gate find unsupported/misattributed
numbers, what was the answer type. Persisting this compact summary under
``metadata_json.guard_verdict`` lets load-test + analytics read grounding-fail /
numeric-flag rates straight from the DB without re-running the pipeline. It
touches ``metadata_json`` only (sacred #10 safe — no answer change, no grade).
"""

from typing import Any

# ``guardrail_flags`` rule_ids for the grounding judge come in three shapes:
# ``grounding_fail`` / ``grounding_fail_closed`` (regex/fail-closed path) AND
# ``llm_grounding_fail`` (LLM-judge path, prefixed ``llm_``). A substring match
# is REQUIRED — ``rule_id.startswith("grounding")`` silently misses the
# ``llm_grounding_fail`` variant, under-counting the very judge signal we want.
_GROUNDING_RULE_MARKER = "grounding"


def build_verdict_meta(final_state: dict[str, Any] | None) -> dict[str, Any]:
    """Build the compact guard self-verdict from a finished pipeline state.

    Pure function: reads ``guardrail_flags`` + ``numeric_fidelity`` +
    ``answer_type`` off *final_state* and returns a flat, JSON-safe dict. Never
    raises on a missing/None state or malformed flag — defaults to the
    all-clear verdict so a forensic write can never break request finalize.
    """
    state = final_state or {}
    flags = state.get("guardrail_flags") or []

    grounding_flag = next(
        (
            f
            for f in flags
            if isinstance(f, dict)
            and _GROUNDING_RULE_MARKER in str(f.get("rule_id", ""))
        ),
        None,
    )

    nf = state.get("numeric_fidelity") or {}
    n_unsupported = int(nf.get("n_unsupported", 0) or 0)
    n_misattributed = int(nf.get("n_misattributed", 0) or 0)

    return {
        "grounding_flagged": grounding_flag is not None,
        "grounding_rule_id": (
            grounding_flag.get("rule_id") if grounding_flag else None
        ),
        "grounding_blocked": (
            bool(grounding_flag.get("blocked")) if grounding_flag else False
        ),
        "numeric_unsupported": n_unsupported,
        "numeric_misattributed": n_misattributed,
        "numeric_flagged": n_unsupported > 0 or n_misattributed > 0,
        "answer_type": state.get("answer_type"),
        "flag_count": len(flags),
    }
