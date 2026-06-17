"""Informativeness gate for scripts/test_75q_load.py::classify().

Locks the 5 cases from reports/MEGA_REFUSE_WITH_DOCS_DEEPDIVE_20260501.md
Mode D so the gate cannot regress silently.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
from scripts.test_75q_load import _has_factual_claim, classify  # noqa: E402


@pytest.mark.parametrize(
    "answer, chunks, expected",
    [
        # 1. Price + hedge → PASS (was REFUSE_WITH_DOCS)
        (
            "Theo bảng giá, gói 10 buổi giá 60.000 đồng ạ. "
            "Tuy nhiên, em chưa có thông tin về chia kỳ thanh toán.",
            3,
            "PASS",
        ),
        # 2. Hedge only, no fact → REFUSE_WITH_DOCS (kept)
        ("Em chưa có thông tin về vấn đề này ạ.", 2, "REFUSE_WITH_DOCS"),
        # 3. Chunk marker present → PASS even with hedge
        (
            "Chi tiết tại [chunk:abc-123]. Tuy nhiên chưa có thông tin về phần khác.",
            1,
            "PASS",
        ),
        # 4. Long-form (>350 chars) answer with hedge → PASS via length heuristic
        (
            "Dạ " + ("nội dung chi tiết bla bla " * 30) + " tuy nhiên em chưa có thông tin.",
            4,
            "PASS",
        ),
        # 5. Polite idiom — must NOT trip refuse (drop "không biết")
        (
            "Dạ chị muốn tư vấn dịch vụ nào ạ? Không biết chị cần loại nào để em hỗ trợ ạ?",
            0,
            "PASS",
        ),
    ],
)
def test_classify_factual_gate(answer: str, chunks: int, expected: str) -> None:
    assert classify(answer, chunks_used=chunks, error=None) == expected


def test_has_factual_claim_numeric_units() -> None:
    assert _has_factual_claim("giá 350.000 đồng")
    assert _has_factual_claim("kéo dài 60 phút")
    assert _has_factual_claim("liệu trình 10 buổi")
    assert not _has_factual_claim("dạ em chưa rõ ạ")


def test_has_factual_claim_chunk_marker() -> None:
    assert _has_factual_claim("xem [chunk:foo-1]")
    assert not _has_factual_claim("xem chunk foo")


def test_has_factual_claim_length_heuristic() -> None:
    short = "x" * 100
    long = "x" * 400
    assert not _has_factual_claim(short)
    assert _has_factual_claim(long)


def test_has_factual_claim_hotline_carve_out() -> None:
    """R4 carve-out: bot answer with hotline number = factual claim.

    Locks the r2 Q13 / r3 Q06 R3 verdict §4 cases — fact-then-hedge hybrid
    where the fact is a hotline / address / maps reference. Without the
    carve-out, harness regex flipped these to RWD as false positives.
    """
    assert _has_factual_claim("liên hệ hotline 0926.559.268")
    assert _has_factual_claim("Hotline: 0926-559-268")
    assert _has_factual_claim("hotline 0123 456 7890")
    # Anchor on `\bhotline\s*\d` — bare "hotline" without number must NOT match
    assert not _has_factual_claim("vui lòng gọi hotline ạ")


def test_has_factual_claim_address_carve_out() -> None:
    """R4 carve-out: street-address-shaped tokens = factual claim."""
    assert _has_factual_claim("Spa nằm tại số 102 Vũ Trọng Phụng")
    assert _has_factual_claim("địa chỉ 123 Lê Lợi quận 1")
    assert _has_factual_claim("xem trên google maps")
    assert _has_factual_claim("link goo.gl/maps/abc")
    # No specific street ⇒ must NOT match
    assert not _has_factual_claim("vui lòng đến địa chỉ trực tiếp")
