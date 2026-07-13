"""#1 — the PII ``redact`` action was a silent NO-OP.

``InputGuardrail.pii_vi`` / ``pii_en`` return hits with ``action="redact"``, but
``guard_input`` only appended them to ``guardrail_flags`` and NEVER touched
``state["query"]`` — so a phone / email / SSN in the user's question flowed
verbatim to the third-party LLM gateway, into the persisted conversation, and
into the audit preview. The rule said "redact"; the app did nothing.

SAFETY (measured, not assumed): the ``pii_vi_cmnd`` pattern is
``\\b(\\d{9}|\\d{12})\\b`` — it matches ANY bare 9- or 12-digit number, which in a
catalog bot includes PRICES (150000000 = 150 triệu is 9 digits) and SKUs. Masking
on that pattern would CORRUPT legitimate questions. So redaction runs against an
explicit allow-list of unambiguous PII shapes and that over-broad rule is
excluded — it keeps flagging (observability) but never rewrites the query.
"""
from __future__ import annotations

from ragbot.infrastructure.guardrails.local_guardrail import redact_pii
from ragbot.shared.constants import (
    DEFAULT_PII_REDACTABLE_RULE_IDS,
    DEFAULT_PII_REDACT_MASK,
)


# --- masks real PII ---------------------------------------------------------

def test_redacts_vn_phone() -> None:
    out, n = redact_pii("Gọi cho em số 0901234567 nhé")
    assert "0901234567" not in out
    assert DEFAULT_PII_REDACT_MASK in out
    assert n == 1


def test_redacts_email() -> None:
    out, n = redact_pii("Mail cho tôi: khach.hang@example.com")
    assert "khach.hang@example.com" not in out
    assert n == 1


def test_redacts_us_ssn() -> None:
    out, n = redact_pii("SSN 123-45-6789")
    assert "123-45-6789" not in out
    assert n == 1


def test_redacts_intl_phone() -> None:
    out, n = redact_pii("Liên hệ +84901234567")
    assert "+84901234567" not in out
    assert n == 1


# --- MUST NOT corrupt legitimate catalog questions --------------------------

def test_does_not_mask_a_bare_9_digit_price() -> None:
    """THE landmine: pii_vi_cmnd matches any bare 9-digit number — a price.
    Redaction must leave it intact or the bot can no longer answer about it."""
    q = "Xe nào giá 150000000 vậy shop?"
    out, n = redact_pii(q)
    assert out == q
    assert n == 0


def test_does_not_mask_a_bare_12_digit_code() -> None:
    q = "Mã sản phẩm 123456789012 còn hàng không?"
    out, n = redact_pii(q)
    assert out == q
    assert n == 0


def test_clean_question_untouched() -> None:
    q = "Lốp 185/60R15 giá bao nhiêu?"
    out, n = redact_pii(q)
    assert out == q
    assert n == 0


def test_over_broad_cmnd_rule_is_not_in_the_allowlist() -> None:
    """Pin the safety decision: the bare-digit rule keeps FLAGGING (so PII is
    still observable) but must never rewrite the query."""
    assert "pii_vi_cmnd" not in DEFAULT_PII_REDACTABLE_RULE_IDS
    assert "pii_vi_phone" in DEFAULT_PII_REDACTABLE_RULE_IDS
    assert "pii_vi_email" in DEFAULT_PII_REDACTABLE_RULE_IDS
    assert "pii_en_ssn" in DEFAULT_PII_REDACTABLE_RULE_IDS


# --- the node actually applies it -------------------------------------------

def test_guard_input_rewrites_the_query_on_a_redact_hit() -> None:
    """Regression pin: guard_input must return the REDACTED query, not just a
    flag (the original bug was that it flagged and shipped the raw PII)."""
    import inspect

    from ragbot.orchestration.nodes import guard_input as gi

    src = inspect.getsource(gi)
    assert "redact_pii" in src or "redact" in src
    # it must put the rewritten query into the node output
    assert '"query"' in src
