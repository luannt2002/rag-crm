from __future__ import annotations
from typing import Final  # noqa: F401
from ._24_structural_markers_by_lang import *  # noqa: F401,F403

# --- Per-language NARRATE prompt templates (multi-language hardening) --------
# The Narrate-then-Embed adapter (``infrastructure/narrate/llm_narrate.py``)
# linearises non-prose blocks (TABLE / FORMULA / IMAGE) into 1-2 natural-
# language sentences BEFORE embedding (Anthropic Contextual Retrieval, Sep
# 2024). The per-block user-prompt scaffolds used to be hardcoded inline in
# the adapter and worded in Vietnamese ("thanh 1-2 cau tieng Viet"), which
# CONTRADICTED the adapter's own system instruction ("Preserve the source
# language exactly -- do not translate"): an English / Japanese table would
# be force-narrated into Vietnamese, translating the source and seeding a
# cross-lingual embedding mismatch. The scaffolds now live here, keyed by
# language code, so a document/bot locale selects a prompt that respects its
# own language instead of the Vietnamese one.
#
# Resolution is constants-only (NOT a runtime DB read): narration runs per
# ingest chunk, so a DB round-trip would add ingest latency for zero
# tunability gain (these are structural prompt vocabulary, not tenant
# content). Operators that need a new language extend this dict -- one entry,
# no code change (mirrors DEFAULT_STRUCTURAL_MARKERS_BY_LANG above).
#
# The ``"default"`` entry is LANGUAGE-AGNOSTIC: it instructs the model to
# describe the block in the SAME language as the input and explicitly NOT to
# translate. It backs every locale that has no dedicated entry, so adding a
# new language is optional -- the source-language-preserving prompt is always
# the safe fallback.
#
# CANONICAL FORM: each value is a ``str.format(content=...)`` template that
# MUST contain a single ``{content}`` placeholder. The ``vi`` entry MUST stay
# byte-identical to the pre-refactor hardcoded Vietnamese scaffolds -- changing
# it changes VN ingest behaviour (the wired default locale).
DEFAULT_NARRATE_PROMPT_TEMPLATES_BY_LANG: Final[dict[str, dict[str, str]]] = {
    # default: language-agnostic -- describe in the SOURCE/document language,
    # never translate. Backs any locale without a dedicated entry below.
    "default": {
        "TABLE": (
            "Describe the table/data row below in 1-2 natural sentences, "
            "written in the SAME language as the table content (do NOT "
            "translate), naming the key columns and what the row conveys. "
            "Return ONLY the description, no markdown, no prefix:\n\n{content}"
        ),
        "FORMULA": (
            "Describe the LaTeX formula/expression below in 1-2 natural "
            "sentences, written in the SAME language as the surrounding "
            "content (do NOT translate), naming the operation and the "
            "meaningful variables. Return ONLY the description, no markdown, "
            "no prefix:\n\n{content}"
        ),
        "IMAGE": (
            "Describe the image / OCR caption below in 1-2 natural sentences, "
            "written in the SAME language as the caption (do NOT translate). "
            "Return ONLY the description:\n\n{content}"
        ),
    },
    # vi: byte-identical to the prior hardcoded scaffolds in llm_narrate.py
    # (_BLOCK_PROMPTS). vi is the historical platform default locale, so a
    # Vietnamese document's narrate prompt is unchanged byte-for-byte.
    "vi": {
        "TABLE": (
            "Diễn giải bảng/dòng dữ liệu dưới đây thành 1-2 câu tiếng Việt tự nhiên, "
            "nêu rõ các cột chính và nội dung dòng truyền tải. CHỈ trả về câu mô tả, "
            "không markdown, không tiền tố:\n\n{content}"
        ),
        "FORMULA": (
            "Diễn giải công thức/biểu thức LaTeX dưới đây thành 1-2 câu tiếng Việt "
            "tự nhiên, gọi tên phép toán và các biến có ý nghĩa. CHỈ trả về câu "
            "mô tả, không markdown, không tiền tố:\n\n{content}"
        ),
        "IMAGE": (
            "Diễn giải nội dung hình ảnh / chú thích OCR dưới đây thành 1-2 câu "
            "tiếng Việt tự nhiên. CHỈ trả về câu mô tả:\n\n{content}"
        ),
    },
}

# Default narrate-prompt locale used when a caller does not thread a document
# language through. Vietnamese is the historical platform default, so omitting
# a language keeps the existing VN narrate behaviour byte-for-byte.
DEFAULT_NARRATE_PROMPT_LANG: Final[str] = "vi"
