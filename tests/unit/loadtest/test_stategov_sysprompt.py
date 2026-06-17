"""Unit tests for stategov sysprompt v1.

Sacred constraints (Phase G3):
- File loads as text.
- Char count 1500-2500 (CLAUDE.md sysprompt rule).
- Contains required anti-HALLU keywords.
- No verbatim VN sentence > 8-token ngram match with corpus stub
  (output guardrail `system_leak` protection).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SYSPROMPT_PATH = Path(__file__).resolve().parents[3] / "tests" / "loadtest" / "stategov_sysprompt.md"

# Minimum and maximum sysprompt size (chars). Below = under-specified;
# above = bloat + token cost + cache-warm pollution.
MIN_CHARS = 1500
MAX_CHARS = 2500

# Max contiguous token run that may match a corpus sentence verbatim.
# >8 means sysprompt is parroting corpus text — risk of leaking corpus
# content through the system prompt itself (defeats output guardrail).
MAX_NGRAM_FROM_CORPUS = 8

# Required anti-HALLU + behavior keywords.
REQUIRED_KEYWORDS = (
    "REFUSE",
    "Điều",
    "Khoản",
    "NGUYÊN TẮC",
    "custom_vocabulary",
)

# Corpus stub: representative Vietnamese sentences from a hypothetical
# stategov circular. Used to ensure sysprompt does NOT copy verbatim
# from corpus. Real corpus lives in DB; this stub covers shape only.
CORPUS_STUB_SENTENCES = (
    "Tổ chức tín dụng phải báo cáo Ngân hàng Nhà nước Việt Nam về tình hình hoạt động định kỳ hàng quý.",
    "Mức dự trữ bắt buộc đối với tiền gửi không kỳ hạn bằng đồng Việt Nam là ba phần trăm trên tổng số dư tiền gửi.",
    "Thông tư này có hiệu lực thi hành kể từ ngày một tháng một năm hai nghìn hai mươi sáu.",
    "Ngân hàng thương mại phải duy trì tỷ lệ an toàn vốn tối thiểu theo quy định của Ngân hàng Nhà nước.",
    "Tổ chức tín dụng phi ngân hàng được phép thực hiện hoạt động cho vay theo giấy phép do Ngân hàng Nhà nước cấp.",
)


@pytest.fixture(scope="module")
def sysprompt_text() -> str:
    assert SYSPROMPT_PATH.exists(), f"sysprompt file missing: {SYSPROMPT_PATH}"
    text = SYSPROMPT_PATH.read_text(encoding="utf-8")
    assert text, "sysprompt file is empty"
    return text


def test_file_loads_as_text(sysprompt_text: str) -> None:
    """File loads, is non-empty, is unicode-decodable."""
    assert isinstance(sysprompt_text, str)
    assert len(sysprompt_text) > 0
    # Must be Vietnamese — at least one Vietnamese-specific character.
    vn_chars = set("ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậ")
    assert any(c in vn_chars for c in sysprompt_text), "sysprompt should be Vietnamese"


def test_char_count_in_range(sysprompt_text: str) -> None:
    """Sysprompt length is within 1500-2500 chars."""
    n = len(sysprompt_text)
    assert MIN_CHARS <= n <= MAX_CHARS, (
        f"sysprompt char count {n} out of range [{MIN_CHARS}, {MAX_CHARS}]"
    )


@pytest.mark.parametrize("keyword", REQUIRED_KEYWORDS)
def test_required_keywords_present(sysprompt_text: str, keyword: str) -> None:
    """Each required anti-HALLU keyword is present in the sysprompt."""
    assert keyword in sysprompt_text, f"required keyword missing: {keyword!r}"


def test_placeholder_present(sysprompt_text: str) -> None:
    """Doc-name placeholder is present (admin fills via env at apply time)."""
    assert "[TÊN DOC]" in sysprompt_text, "placeholder '[TÊN DOC]' missing"


def _tokens(text: str) -> list[str]:
    """Token = run of letters/digits (unicode word chars). Lowercased."""
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


def _max_common_ngram_len(a: str, b: str, cap: int) -> int:
    """Return the longest contiguous token-ngram length shared by a and b,
    bounded by `cap`. Bounded scan: O(len(a) * len(b) * cap)."""
    ta = _tokens(a)
    tb = _tokens(b)
    best = 0
    for i in range(len(ta)):
        for j in range(len(tb)):
            k = 0
            while (
                i + k < len(ta)
                and j + k < len(tb)
                and ta[i + k] == tb[j + k]
                and k < cap + 1
            ):
                k += 1
            if k > best:
                best = k
                if best > cap:
                    return best
    return best


@pytest.mark.parametrize("corpus_sentence", CORPUS_STUB_SENTENCES)
def test_no_verbatim_corpus_ngram(sysprompt_text: str, corpus_sentence: str) -> None:
    """Sysprompt must not contain a contiguous >8-token ngram from any
    representative corpus sentence — protects against leaking corpus
    content through the system prompt."""
    longest = _max_common_ngram_len(
        sysprompt_text, corpus_sentence, cap=MAX_NGRAM_FROM_CORPUS
    )
    assert longest <= MAX_NGRAM_FROM_CORPUS, (
        f"sysprompt shares {longest}-token contiguous run with corpus "
        f"sentence (max allowed {MAX_NGRAM_FROM_CORPUS}): {corpus_sentence!r}"
    )


def test_refusal_branches_documented(sysprompt_text: str) -> None:
    """All 4 refusal branches (HALLU trap, OOS-doc, jailbreak/personal,
    empty-context) are mentioned in the sysprompt."""
    # Branch keywords (Vietnamese) — order-independent.
    branches = (
        "không tồn tại",   # HALLU trap (non-existent entity)
        "văn bản pháp luật khác",  # OOS — other law
        "jailbreak",  # explicit jailbreak handling
        "trống",  # empty context
    )
    lower = sysprompt_text.lower()
    missing = [b for b in branches if b.lower() not in lower]
    assert not missing, f"refusal branches missing: {missing}"
