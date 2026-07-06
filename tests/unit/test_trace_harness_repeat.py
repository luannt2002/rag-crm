"""Unit tests for the repeated-run trace harness verdict classifier
(specs/001-rag-truth-audit/contracts/harness-cli.md §3-4, research.md D1/D2).

Pure-function tests — no server, no DB: classify_numbers(answer, chunks, stats).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "rag_trace_capture",
    Path(__file__).resolve().parents[2] / "scripts" / "rag_trace_capture.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
classify_numbers = _mod.classify_numbers


def _classes(verdicts: list[dict]) -> dict[str, str]:
    return {v["token"]: v["class"] for v in verdicts}


def test_grounded_literal_substring() -> None:
    v = classify_numbers("Giá 1.242.000đ/lốp ạ.", ["... LPD: 1.242.000 | quantity: 507"], set())
    assert _classes(v)["1.242.000"] == "grounded"


def test_grounded_via_stats_value_despite_format_drift() -> None:
    # Chunk holds the bare int, answer holds dotted format — stats-value equality saves it.
    v = classify_numbers("Giá 1.242.000đ.", ["no numbers here"], {1242000})
    assert _classes(v)["1.242.000"] == "grounded"


def test_unsupported_fabricated_price_the_q20_case() -> None:
    # Verbatim A-q20 fabrication: 26.000.000 absent from chunks AND stats.
    v = classify_numbers(
        "Lốp Neoterra 195/65R16 giá 26.000.000đ, còn 26 lốp.",
        ["| 195/65R16, 195 65 16 ... | 2-R16 195/65 NEO | ... | | | 26 | |"],
        {810000, 1242000},
    )
    assert _classes(v)["26.000.000"] == "unsupported"


def test_derived_valid_difference() -> None:
    # "cao hơn 432.000đ" = 1.602.000 − 1.170.000 (both grounded) → derived_valid.
    v = classify_numbers(
        "235/40ZR18 giá 1.602.000đ, 205/65R16 giá 1.170.000đ — chênh 432.000đ.",
        ["... 1.602.000 ... 1.170.000 ..."], set(),
    )
    c = _classes(v)
    assert c["1.602.000"] == "grounded" and c["1.170.000"] == "grounded"
    assert c["432.000"] == "derived_valid"


def test_small_tokens_ignored_min_digits() -> None:
    # Sizes/ordinals (<5 digits per token) never classified — no noise verdicts.
    v = classify_numbers("Lốp 205/55R16 còn 9 lốp.", ["205/55R16 ... quantity: 9"], set())
    assert v == []


def test_repeat_flag_and_exit_codes_exist() -> None:
    # Contract surface pinned: flags + exit codes are part of the CLI contract.
    assert _mod.EXIT_CACHE_CONTAMINATED == 2
    assert _mod.EXIT_CORPUS_DRIFT == 3


def test_rate_limit_retry_redrives_until_success() -> None:
    """P-09 lesson: harness must re-drive RATE_LIMITED responses, not record them."""
    import asyncio

    calls = {"n": 0}

    async def _once():
        calls["n"] += 1
        if calls["n"] < 3:
            return {"error": {"code": "RATE_LIMITED", "message": "x", "retry_after_s": 0}}
        return {"answer": "ok", "error": None}

    async def _no_sleep(_s):
        return None

    resp = asyncio.run(_mod._with_rate_limit_retry(_once, sleep=_no_sleep))
    assert resp["answer"] == "ok" and calls["n"] == 3


def test_rate_limit_retry_gives_up_after_max() -> None:
    import asyncio

    async def _always_limited():
        return {"error": {"code": "RATE_LIMITED", "retry_after_s": 0}}

    async def _no_sleep(_s):
        return None

    resp = asyncio.run(_mod._with_rate_limit_retry(_always_limited, max_retries=2, sleep=_no_sleep))
    assert _mod._rate_limited(resp) is not None  # still limited, surfaced honestly


def test_capture_cap_is_constant_with_truncated_flag() -> None:
    """002-B: the 500-char hardcap blinded graders into 4 wrongful verdicts —
    cap now lives in shared constants and every cut chunk is flagged."""
    import inspect

    from ragbot.shared.constants import TRACE_CHUNK_CAPTURE_MAX_CHARS

    src = inspect.getsource(_mod._record)
    assert "TRACE_CHUNK_CAPTURE_MAX_CHARS" in src
    assert '"truncated"' in src
    assert "[:500]" not in src
    assert TRACE_CHUNK_CAPTURE_MAX_CHARS >= 1500
