"""Input-guardrail node — runs input guardrails + pre-loads the language pack.

Extracted from ``query_graph.build_graph``. di_kwargs (``guardrail``,
``language_pack_service``) and the builder helper ``_resolved_oos_template`` are
threaded in as kwargs, bound via ``functools.partial`` in the graph builder.
"""
from __future__ import annotations

from typing import Any

from ragbot.application.ports.guardrail_port import GuardrailBlocked
from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import DEFAULT_LANGUAGE


async def guard_input(
    state: GraphState,
    *,
    guardrail: Any,
    language_pack_service: Any,
    _resolved_oos_template: Any,
) -> dict:
    async with state["step_tracker"].step("guard_input"):
        # Pre-load DB-driven language pack rows so downstream nodes read from the same source.
        lpack_rows: dict[str, str] | None = None
        if language_pack_service is not None:
            try:
                lpack_rows = await language_pack_service.get_pack(
                    state.get("language", DEFAULT_LANGUAGE),
                )
            except (OSError, RuntimeError, AttributeError,
                    KeyError, ValueError):
                # Defensive: language-pack lookup failure must never
                # block the input guard pipeline.
                lpack_rows = None
        flags = list(state.get("guardrail_flags", []))
        try:
            hits = await guardrail.check_input(
                state["query"],
                tenant_id=state.get("record_tenant_id"),
                message_id=state["message_id"],
                request_id=state.get("request_id"),
            )
            for h in hits:
                flags.append(
                    {
                        "stage": "input",
                        "rule_id": h.rule_id,
                        "severity": h.severity,
                        "action": h.action,
                    }
                )
            out: dict[str, Any] = {"guardrail_flags": flags}
            if lpack_rows is not None:
                out["_language_pack_rows"] = lpack_rows
            return out
        except GuardrailBlocked as exc:
            # Per-rule response_message overrides bot-level oos_answer_template.
            blocked_answer = _resolved_oos_template(state)
            for h in exc.hits:
                flags.append(
                    {
                        "stage": "input",
                        "rule_id": h.rule_id,
                        "severity": h.severity,
                        "action": h.action,
                        "blocked": True,
                    }
                )
                if h.severity == "block" and h.details.get("response_message"):
                    blocked_answer = h.details["response_message"]
            out_blocked: dict[str, Any] = {
                "guardrail_flags": flags,
                "answer": blocked_answer,
                "answer_type": "blocked",
                "answer_reason": "Input guardrail blocked",
            }
            if lpack_rows is not None:
                out_blocked["_language_pack_rows"] = lpack_rows
            return out_blocked
