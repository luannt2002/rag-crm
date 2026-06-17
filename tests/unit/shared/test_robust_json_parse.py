"""Unit tests for ``ragbot.shared.json_parse`` — 4-strategy robust parser.

Coverage matrix (per-strategy + cross-cutting):

| ID | Input shape                                  | Strategy expected | Outcome |
|----|----------------------------------------------|-------------------|---------|
| 1  | Strict object ``{"k":"v"}``                  | 1                 | ok      |
| 2  | Strict array ``[1,2]``                       | 1                 | ok      |
| 3  | Fenced ``\\`\\`\\`json\\n{...}\\n\\`\\`\\``` | 2                 | ok      |
| 4  | Fenced ``\\`\\`\\`\\n[...]\\n\\`\\`\\```     | 2                 | ok      |
| 5  | Single-quoted ``{'k':'v'}``                  | 3                 | ok      |
| 6  | Trailing comma ``{"k":"v",}``                | 3                 | ok      |
| 7  | Prose prefix ``"Here: {"k":"v"}"``           | 4                 | ok      |
| 8  | Mixed prose + fences + trailing comma        | 4                 | ok      |
| 9  | Empty string                                 | n/a               | raises  |
| 10 | Pure garbage ``"hello world"``               | n/a               | raises  |
| 11 | Truncated ``{"k":"v"``                       | n/a               | raises  |
| 12 | Nested object with prose                     | 4                 | ok      |
| 13 | String value containing ``{``                | 4                 | ok      |
| 14 | Flag OFF + malformed → raises immediately    | 1 only            | raises  |
| 15 | Flag OFF + strict input → still ok           | 1                 | ok      |

These are REAL behavioural assertions per CLAUDE.md test rule (no
``assert True``). Each assertion targets a value, a strategy id, or an
exception type — never just object identity.
"""

from __future__ import annotations

from typing import Any

import pytest

from ragbot.shared.constants import (
    DEFAULT_ROBUST_JSON_PARSER_ENABLED,
    ROBUST_JSON_PARSER_FLAG_KEY,
)
from ragbot.shared.json_parse import (
    JSONParseError,
    _extract_outermost_json_span,
    _strip_code_fences,
    _strip_trailing_commas,
    _swap_single_to_double_quotes,
    robust_json_parse,
)


# ----------------------------------------------------------------------
# Strategy 1 — direct parse (fast path).
# ----------------------------------------------------------------------

def test_strategy_1_strict_object_returns_dict() -> None:
    result = robust_json_parse('{"k": "v"}', parser_name="t1")
    assert result == {"k": "v"}


def test_strategy_1_strict_array_returns_list() -> None:
    result = robust_json_parse("[1, 2, 3]", parser_name="t1")
    assert result == [1, 2, 3]


def test_strategy_1_unicode_passthrough() -> None:
    # Vietnamese diacritics are common in production LLM output.
    result = robust_json_parse('{"city": "Hà Nội"}', parser_name="t1")
    assert result == {"city": "Hà Nội"}


# ----------------------------------------------------------------------
# Strategy 2 — code fence strip.
# ----------------------------------------------------------------------

def test_strategy_2_strips_json_fence() -> None:
    raw = '```json\n{"k": "v"}\n```'
    result = robust_json_parse(raw, parser_name="t2")
    assert result == {"k": "v"}


def test_strategy_2_strips_bare_fence() -> None:
    raw = '```\n[1, 2, 3]\n```'
    result = robust_json_parse(raw, parser_name="t2")
    assert result == [1, 2, 3]


def test_strategy_2_strips_uppercase_language_tag() -> None:
    # ``` JSON ``` uppercase — some LLM completions emit this.
    raw = '```JSON\n{"k": 1}\n```'
    result = robust_json_parse(raw, parser_name="t2")
    assert result == {"k": 1}


# ----------------------------------------------------------------------
# Strategy 3 — quote + trailing comma repair.
# ----------------------------------------------------------------------

def test_strategy_3_swaps_single_to_double_quotes() -> None:
    raw = "{'k': 'v'}"
    result = robust_json_parse(raw, parser_name="t3")
    assert result == {"k": "v"}


def test_strategy_3_strips_trailing_comma_object() -> None:
    raw = '{"k": "v",}'
    result = robust_json_parse(raw, parser_name="t3")
    assert result == {"k": "v"}


def test_strategy_3_strips_trailing_comma_array() -> None:
    raw = "[1, 2, 3,]"
    result = robust_json_parse(raw, parser_name="t3")
    assert result == [1, 2, 3]


def test_strategy_3_repairs_combined_quote_and_comma() -> None:
    raw = "{'k': 'v', 'arr': [1, 2,],}"
    result = robust_json_parse(raw, parser_name="t3")
    assert result == {"k": "v", "arr": [1, 2]}


# ----------------------------------------------------------------------
# Strategy 4 — substring extract from prose.
# ----------------------------------------------------------------------

def test_strategy_4_extracts_object_from_prose() -> None:
    raw = 'Here is the JSON: {"k": "v"} — that is the answer.'
    result = robust_json_parse(raw, parser_name="t4")
    assert result == {"k": "v"}


def test_strategy_4_extracts_array_from_prose() -> None:
    raw = "I think the list is: [1, 2, 3] (three items)."
    result = robust_json_parse(raw, parser_name="t4")
    assert result == [1, 2, 3]


def test_strategy_4_handles_nested_braces() -> None:
    raw = 'Output: {"outer": {"inner": [1, 2]}, "n": 3}'
    result = robust_json_parse(raw, parser_name="t4")
    assert result == {"outer": {"inner": [1, 2]}, "n": 3}


def test_strategy_4_quote_aware_against_brace_in_string() -> None:
    # String value contains a stray ``{`` — must not trip bracket counter.
    raw = 'Result: {"template": "Hello {name}!", "count": 1}'
    result = robust_json_parse(raw, parser_name="t4")
    assert result == {"template": "Hello {name}!", "count": 1}


def test_strategy_4_extracts_after_fence_stripping() -> None:
    # Fence + prose inside fence + trailing junk: strategy 4 must succeed.
    raw = "```json\nNote: {'k': 'v',}\n```"
    result = robust_json_parse(raw, parser_name="t4")
    assert result == {"k": "v"}


# ----------------------------------------------------------------------
# Failure paths — HALLU=0 invariant (parser MUST raise, never fabricate).
# ----------------------------------------------------------------------

def test_empty_string_raises() -> None:
    with pytest.raises(JSONParseError) as exc:
        robust_json_parse("", parser_name="t_empty")
    assert exc.value.parser_name == "t_empty"
    assert exc.value.input_preview == ""


def test_whitespace_only_raises() -> None:
    with pytest.raises(JSONParseError):
        robust_json_parse("   \n\t  ", parser_name="t_ws")


def test_pure_garbage_raises() -> None:
    with pytest.raises(JSONParseError) as exc:
        robust_json_parse("hello world", parser_name="t_garbage")
    assert "all 4 strategies failed" in str(exc.value)
    # Preview must surface the offending text for debugging.
    assert exc.value.input_preview == "hello world"


def test_truncated_json_raises() -> None:
    # No closing brace — substring extract will not find a balanced span.
    with pytest.raises(JSONParseError):
        robust_json_parse('{"k": "v"', parser_name="t_trunc")


def test_non_string_input_raises() -> None:
    # Defensive: bytes / int / None must not silently parse.
    with pytest.raises(JSONParseError):
        robust_json_parse(None, parser_name="t_none")  # type: ignore[arg-type]


def test_jsonparseerror_is_valueerror_subclass() -> None:
    # Existing callers ``except (ValueError, json.JSONDecodeError)`` must
    # keep catching this without modification.
    assert issubclass(JSONParseError, ValueError)


# ----------------------------------------------------------------------
# Feature-flag behaviour.
# ----------------------------------------------------------------------

def test_flag_off_strict_input_still_parses() -> None:
    # Strategy 1 is always attempted regardless of flag.
    result = robust_json_parse('{"k": 1}', parser_name="t_off", flag_value=False)
    assert result == {"k": 1}


def test_flag_off_malformed_raises_immediately() -> None:
    # Fence-wrapped is malformed for strategy 1. With flag OFF, strategies
    # 2-4 are skipped and we get JSONParseError right away — no silent
    # success that would let bad data into the pipeline.
    raw = '```json\n{"k": 1}\n```'
    with pytest.raises(JSONParseError):
        robust_json_parse(raw, parser_name="t_off", flag_value=False)


def test_default_flag_value_is_true() -> None:
    assert DEFAULT_ROBUST_JSON_PARSER_ENABLED is True


def test_flag_key_constant_matches_observability_matrix() -> None:
    # Contract: flag key MUST match the OBSERVABILITY-MATRIX.md entry so
    # the master ablation harness can read the same key.
    assert ROBUST_JSON_PARSER_FLAG_KEY == "robust_json_parser_enabled"


# ----------------------------------------------------------------------
# Telemetry — structlog event ``robust_json_parse``.
#
# structlog is configured at process level so caplog (stdlib logging
# bridge) does not always intercept events when structlog renders
# straight to stdout. We swap in an in-memory ``capture_logs`` context
# instead — that surface is contract-stable across structlog versions.
# ----------------------------------------------------------------------

def test_telemetry_records_strategy_used_on_success() -> None:
    from structlog.testing import capture_logs

    with capture_logs() as cap:
        robust_json_parse('{"k": 1}', parser_name="dec")
    events = [e for e in cap if e.get("event") == "robust_json_parse"]
    assert events, "telemetry event not emitted"
    payload = events[-1]
    assert payload["strategy_used"] == 1
    assert payload["parser_name"] == "dec"
    assert payload["outcome"] == "ok"
    assert payload["flag_value"] is True
    assert payload["input_length"] == 8
    assert isinstance(payload["duration_us"], int)


def test_telemetry_records_failure_outcome() -> None:
    from structlog.testing import capture_logs

    with capture_logs() as cap:
        with pytest.raises(JSONParseError):
            robust_json_parse("hello world", parser_name="failtag")
    events = [e for e in cap if e.get("event") == "robust_json_parse"]
    assert events, "telemetry event not emitted on failure"
    payload = events[-1]
    assert payload["strategy_used"] is None
    assert payload["parser_name"] == "failtag"
    assert payload["outcome"] == "failed_all_strategies"


def test_telemetry_records_flag_off_outcome() -> None:
    """Flag OFF path emits a dedicated outcome tag so dashboards can
    separate ``flag-off rejections`` from ``all-strategies-failed``."""
    from structlog.testing import capture_logs

    with capture_logs() as cap:
        with pytest.raises(JSONParseError):
            robust_json_parse(
                '```json\n{"k": 1}\n```',
                parser_name="flagged",
                flag_value=False,
            )
    events = [e for e in cap if e.get("event") == "robust_json_parse"]
    assert events, "telemetry event not emitted on flag-off failure"
    payload = events[-1]
    assert payload["outcome"] == "failed_flag_off"
    assert payload["flag_value"] is False
    assert payload["strategy_used"] is None


# ----------------------------------------------------------------------
# Helper coverage — internal primitives.
# ----------------------------------------------------------------------

def test_strip_code_fences_idempotent_when_no_fence() -> None:
    assert _strip_code_fences("plain text") == "plain text"


def test_strip_code_fences_handles_language_tag() -> None:
    assert _strip_code_fences("```python\nx=1\n```") == "x=1"


def test_strip_trailing_commas_object() -> None:
    assert _strip_trailing_commas('{"k": "v",}') == '{"k": "v"}'


def test_strip_trailing_commas_array() -> None:
    assert _strip_trailing_commas("[1, 2,]") == "[1, 2]"


def test_strip_trailing_commas_keeps_legitimate_comma() -> None:
    # ``,`` inside the array between items must survive.
    assert _strip_trailing_commas("[1, 2, 3]") == "[1, 2, 3]"


def test_swap_single_to_double_preserves_inner_double_quoted_text() -> None:
    # Apostrophes inside a double-quoted string survive untouched.
    src = '{"msg": "it\'s ok", "flag": true}'
    out = _swap_single_to_double_quotes(src)
    assert out == src  # no change: all single quotes were inside a "..." span.


def test_swap_single_to_double_handles_basic_python_dict_literal() -> None:
    assert _swap_single_to_double_quotes("{'k': 'v'}") == '{"k": "v"}'


def test_extract_outermost_span_object() -> None:
    assert _extract_outermost_json_span('prefix {"a":1} suffix') == '{"a":1}'


def test_extract_outermost_span_array() -> None:
    assert _extract_outermost_json_span("note: [1, 2, 3] end") == "[1, 2, 3]"


def test_extract_outermost_span_nested() -> None:
    src = 'x {"a": {"b": [1, 2]}, "c": 3} y'
    assert _extract_outermost_json_span(src) == '{"a": {"b": [1, 2]}, "c": 3}'


def test_extract_outermost_span_none_when_no_bracket() -> None:
    assert _extract_outermost_json_span("just prose, no braces") is None


def test_extract_outermost_span_quote_aware() -> None:
    # The ``}`` inside the string value must NOT close the span early.
    src = 'lead {"tpl": "close } here", "n": 1} trail'
    extracted = _extract_outermost_json_span(src)
    assert extracted == '{"tpl": "close } here", "n": 1}'


# ----------------------------------------------------------------------
# Domain-neutral guard — module must not embed tenant identifiers.
# ----------------------------------------------------------------------

def test_module_source_has_no_tenant_literal() -> None:
    """Smoke check: the helper source code carries zero hardcoded tenant /
    brand identifiers. ``robust_json_parse`` is shared infrastructure and
    MUST stay domain-neutral per CLAUDE.md tenant-identifier rule.
    """
    import inspect

    from ragbot.shared import json_parse

    src = inspect.getsource(json_parse)
    forbidden = (".vn", "innocom", "ragbot.vn", "10.0.1", "backendsg")
    for token in forbidden:
        assert token not in src.lower(), f"forbidden literal {token!r} leaked into json_parse.py"


# ----------------------------------------------------------------------
# Parametric malformed-JSON catalogue — ensures EACH variant succeeds.
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 10+ malformed variants the parser must handle.
        ('{"k":"v"}', {"k": "v"}),
        ('```json\n{"k":"v"}\n```', {"k": "v"}),
        ('```\n{"k":"v"}\n```', {"k": "v"}),
        ("{'k':'v'}", {"k": "v"}),
        ('{"k":"v",}', {"k": "v"}),
        ("[1, 2, 3,]", [1, 2, 3]),
        ('Here: {"k":"v"} end.', {"k": "v"}),
        ("Output:\n```json\n[1,2,3]\n```\nDone.", [1, 2, 3]),
        ("Result is [1, 2, 3] — three items.", [1, 2, 3]),
        ('{"outer":{"inner":[1,2]}}', {"outer": {"inner": [1, 2]}}),
        ('{"tpl":"Hello {name}!"}', {"tpl": "Hello {name}!"}),
        ("```\n{'k': 'v',}\n```", {"k": "v"}),
    ],
)
def test_malformed_variants_round_trip(raw: str, expected: Any) -> None:
    assert robust_json_parse(raw, parser_name="catalog") == expected
