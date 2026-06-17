"""Output guardrail must skip leak detection when answer is the OOS refusal.

The bot's per-tenant ``oos_answer_template`` shares vocabulary with its
``system_prompt`` (owner phrasing). Without skipping, shingle hashes from the
refusal text collide with sysprompt hashes and the answer is mislabelled as
``system_leak`` — the false-positive surfaced as 25/100 turns ``blocked`` in
the Wave 2 100Q load test (2026-05-08).
"""

from __future__ import annotations

import hashlib

import pytest

from ragbot.infrastructure.guardrails.local_guardrail import (
    GuardrailBlocked,
    LocalGuardrail,
    OutputGuardrail,
)
from ragbot.shared.constants import DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE


def _hash_shingles(text: str, size: int = DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE) -> list[str]:
    words = text.split()
    if len(words) < size:
        return [hashlib.sha256(text.encode("utf-8")).hexdigest()]
    return [
        hashlib.sha256(" ".join(words[i : i + size]).encode("utf-8")).hexdigest()
        for i in range(len(words) - size + 1)
    ]


# Long enough to satisfy the 24-word shingle requirement; built from typical
# refusal vocabulary that overlaps a per-bot system_prompt.
_OOS_TEMPLATE = (
    "Xin lỗi, tôi không thể giúp câu hỏi này vì nằm ngoài tài liệu hiện có. "
    "Vui lòng liên hệ hotline để được hỗ trợ thêm thông tin chính xác và đầy đủ "
    "bởi nhân viên tư vấn của chúng tôi."
)

# Sysprompt that re-uses many of the OOS-template words — this is the
# real-world Wave 2 collision pattern.
_SYS_PROMPT = (
    "Bạn là chuyên gia tư vấn. Khi không thể giúp, hãy lịch sự xin lỗi và đề "
    "nghị khách vui lòng liên hệ hotline để được hỗ trợ thêm thông tin chính "
    "xác và đầy đủ bởi nhân viên tư vấn của chúng tôi. Câu hỏi nằm ngoài tài "
    "liệu hiện có thì từ chối."
)


def test_exact_oos_template_skips_leak_detection() -> None:
    """Bot answer = exact OOS template → no system_leak hit."""
    sys_hash = _hash_shingles(_SYS_PROMPT)
    # Without OOS skip the assert would trip — verify the collision is real.
    collide = OutputGuardrail.system_prompt_leak(_OOS_TEMPLATE, sys_hash)
    assert collide is not None and collide.rule_id == "system_leak", (
        "test fixture must produce a real shingle collision; if not the "
        "OOS-skip path is being tested against a no-op input"
    )

    hit = OutputGuardrail.system_prompt_leak(
        _OOS_TEMPLATE, sys_hash, oos_template=_OOS_TEMPLATE
    )
    assert hit is None


def test_oos_template_with_trailing_whitespace_skips() -> None:
    """Trailing whitespace/punct difference still skips (≥0.90 Jaccard)."""
    sys_hash = _hash_shingles(_SYS_PROMPT)
    answer = _OOS_TEMPLATE + "   \n"
    hit = OutputGuardrail.system_prompt_leak(
        answer, sys_hash, oos_template=_OOS_TEMPLATE
    )
    assert hit is None


def test_real_sysprompt_phrase_still_detected() -> None:
    """Genuine sysprompt leakage MUST still be detected (no false negative)."""
    sys_hash = _hash_shingles(_SYS_PROMPT)
    # The bot regurgitates a long verbatim sysprompt phrase. This shares
    # essentially zero word-overlap with the OOS template's distinctive tokens
    # ("hotline", "ngoài"), so the OOS-skip must NOT swallow it.
    leaked_answer = (
        "Bạn là chuyên gia tư vấn. Khi không thể giúp, hãy lịch sự xin lỗi và "
        "đề nghị khách vui lòng liên hệ hotline để được hỗ trợ thêm thông tin "
        "chính xác và đầy đủ bởi nhân viên tư vấn của chúng tôi."
    )
    hit = OutputGuardrail.system_prompt_leak(
        leaked_answer, sys_hash, oos_template="Xin lỗi, không có thông tin."
    )
    assert hit is not None
    assert hit.rule_id == "system_leak"
    assert hit.severity == "block"


def test_empty_answer_returns_none() -> None:
    """Empty answer is a no-op (existing behaviour preserved)."""
    sys_hash = _hash_shingles(_SYS_PROMPT)
    assert (
        OutputGuardrail.system_prompt_leak(
            "", sys_hash, oos_template=_OOS_TEMPLATE
        )
        is None
    )


def test_empty_oos_template_falls_back_to_normal_check() -> None:
    """Bot with empty oos_answer_template (allowed per Application MINDSET)
    must still run the normal leak comparison — no silent skip.
    """
    sys_hash = _hash_shingles(_SYS_PROMPT)
    leaked_answer = (
        "Bạn là chuyên gia tư vấn. Khi không thể giúp, hãy lịch sự xin lỗi và "
        "đề nghị khách vui lòng liên hệ hotline để được hỗ trợ thêm thông tin "
        "chính xác và đầy đủ bởi nhân viên tư vấn của chúng tôi."
    )
    hit = OutputGuardrail.system_prompt_leak(
        leaked_answer, sys_hash, oos_template=""
    )
    assert hit is not None
    assert hit.rule_id == "system_leak"


@pytest.mark.asyncio
async def test_check_output_does_not_block_oos_refusal() -> None:
    """End-to-end: check_output must not raise GuardrailBlocked when the
    answer is the bot's OOS template, even with colliding sys_prompt_hash.
    """
    guard = LocalGuardrail(guardrail_repository=None)
    sys_hash = _hash_shingles(_SYS_PROMPT)
    hits = await guard.check_output(
        _OOS_TEMPLATE,
        system_prompt_hash=sys_hash,
        tenant_id=None,
        message_id=42,
        request_id=None,
        oos_template=_OOS_TEMPLATE,
    )
    assert all(h.rule_id != "system_leak" for h in hits)


@pytest.mark.asyncio
async def test_check_output_still_blocks_real_leak() -> None:
    """End-to-end regression guard: a true leak still raises GuardrailBlocked
    even when oos_template is set.
    """
    guard = LocalGuardrail(guardrail_repository=None)
    sys_hash = _hash_shingles(_SYS_PROMPT)
    leaked_answer = (
        "Bạn là chuyên gia tư vấn. Khi không thể giúp, hãy lịch sự xin lỗi và "
        "đề nghị khách vui lòng liên hệ hotline để được hỗ trợ thêm thông tin "
        "chính xác và đầy đủ bởi nhân viên tư vấn của chúng tôi."
    )
    with pytest.raises(GuardrailBlocked):
        await guard.check_output(
            leaked_answer,
            system_prompt_hash=sys_hash,
            tenant_id=None,
            message_id=43,
            request_id=None,
            oos_template="Xin lỗi, không có thông tin.",
        )
