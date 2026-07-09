"""build_verdict_meta — deterministic guard self-verdict for metadata_json.

Pins the observe-only contract: the summary reflects guard flags + numeric
fidelity, NEVER touches correctness grading, and the grounding classifier
catches ALL three rule_id shapes (incl. the ``llm_`` prefixed judge variant
that ``.startswith`` would miss).
"""

from ragbot.shared.verdict_meta import build_verdict_meta


def test_none_state_is_all_clear() -> None:
    v = build_verdict_meta(None)
    assert v["grounding_flagged"] is False
    assert v["grounding_rule_id"] is None
    assert v["grounding_blocked"] is False
    assert v["numeric_flagged"] is False
    assert v["embed_degraded"] is False
    assert v["flag_count"] == 0
    # MUST NOT invent a correctness grade.
    assert "is_correct" not in v


def test_embed_degraded_surfaced() -> None:
    """The HALLU-safety embed-degraded flag (was a dead-write) is now read
    and persisted, so a degraded turn is DB-queryable."""
    assert build_verdict_meta({"embed_degraded": True})["embed_degraded"] is True
    assert build_verdict_meta({"embed_degraded": False})["embed_degraded"] is False
    assert build_verdict_meta({})["embed_degraded"] is False


def test_empty_state_is_all_clear() -> None:
    v = build_verdict_meta({})
    assert v["grounding_flagged"] is False
    assert v["numeric_unsupported"] == 0
    assert v["numeric_misattributed"] == 0


def test_llm_grounding_fail_is_caught_substring_not_startswith() -> None:
    """``llm_grounding_fail`` is prefixed ``llm_`` — a ``.startswith('grounding')``
    check would MISS it. The substring classifier must catch it."""
    state = {
        "guardrail_flags": [
            {"stage": "output", "rule_id": "llm_grounding_fail",
             "severity": "high", "action": "flag"},
        ],
    }
    v = build_verdict_meta(state)
    assert v["grounding_flagged"] is True
    assert v["grounding_rule_id"] == "llm_grounding_fail"
    assert v["grounding_blocked"] is False
    assert v["flag_count"] == 1


def test_grounding_fail_closed_blocked() -> None:
    state = {
        "guardrail_flags": [
            {"rule_id": "grounding_fail_closed", "blocked": True},
        ],
        "answer_type": "blocked",
    }
    v = build_verdict_meta(state)
    assert v["grounding_flagged"] is True
    assert v["grounding_blocked"] is True
    assert v["answer_type"] == "blocked"


def test_numeric_unsupported_flags() -> None:
    """The S-005 fabricated-phone class: numeric fidelity found an unsupported
    number → numeric_flagged True, counts surfaced."""
    state = {
        "numeric_fidelity": {
            "n_numbers": 3, "n_grounded": 2, "n_unsupported": 1,
            "n_misattributed": 0,
        },
    }
    v = build_verdict_meta(state)
    assert v["numeric_unsupported"] == 1
    assert v["numeric_flagged"] is True


def test_numeric_misattributed_flags() -> None:
    state = {"numeric_fidelity": {"n_unsupported": 0, "n_misattributed": 2}}
    v = build_verdict_meta(state)
    assert v["numeric_misattributed"] == 2
    assert v["numeric_flagged"] is True


def test_malformed_flag_does_not_raise() -> None:
    """A non-dict flag entry must not crash the forensic summary."""
    state = {"guardrail_flags": ["oops", None, {"rule_id": "grounding_fail"}]}
    v = build_verdict_meta(state)
    assert v["grounding_flagged"] is True
    assert v["flag_count"] == 3
