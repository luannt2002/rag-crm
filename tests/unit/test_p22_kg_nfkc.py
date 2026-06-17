"""P22-VN3 (revised ): Verify knowledge_graph.py uses NFC.

History:
- P22 originally pinned NFKC, on the theory that compatibility folding
  (fullwidth -> ASCII, circled-digit -> digit) was desirable for KG dedup.
- hidden-bug audit reversed this: NFKC over-normalizes VN
  technical content (units like "㎏", circled enumerators "①" common in
  pricelist tables), and the cache-key mismatch between NFKC ingest +
  NFKC query was a non-issue once both sides agree on a single canonical
  form. NFC is the canonical-equivalence form best practice for VN
  diacritic. Composed-vs-decomposed dedup (which P22 cared about) is
  served correctly by NFC — only width/compatibility folding is dropped.

This test is now the regression guard ensuring KG stays on NFC.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path


_KG_FILE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ragbot"
    / "infrastructure"
    / "graph"
    / "knowledge_graph.py"
)


def test_no_nfkc_normalize_remaining() -> None:
    """Source file must not contain ``"NFKC"`` normalize() calls (S13 P1)."""
    source = _KG_FILE.read_text(encoding="utf-8")
    # Comments/docstrings mentioning NFKC are allowed; only call-form is banned.
    assert 'unicodedata.normalize("NFKC"' not in source, (
        "knowledge_graph.py reintroduced NFKC normalize() — pipeline "
        "requires NFC via ragbot.shared.text_normalization.normalize_vn."
    )
    assert "unicodedata.normalize('NFKC'" not in source
    # Sanity: the helper import must be present (we didn't accidentally
    # delete the normalization layer entirely).
    assert (
        "from ragbot.shared.text_normalization import normalize_vn"
        in source
    ), "Expected normalize_vn import in knowledge_graph.py."


def test_nfc_dedup_across_composed_decomposed() -> None:
    """Two Unicode-distinct encodings of the same VN word dedup under NFC.

    This is the core dedup property KG cares about — it survives the
    NFKC -> NFC migration because both forms collapse composed + decomposed
    diacritics. Only width/compatibility folding is sacrificed (acceptable
    per S13 P1 audit: compatibility chars are rare and meaningful in VN
    technical corpora).
    """
    # "Hà Nội" — composed form: "Hà" = U+00E0, "Nội" includes U+1ED9
    composed = "Hà Nội"
    # Decomposed form: base + combining marks
    decomposed = unicodedata.normalize("NFD", composed)

    # Sanity: byte-different before normalization
    assert composed != decomposed

    # Under NFC, both collapse to the same canonical form
    norm_a = unicodedata.normalize("NFC", composed).lower()
    norm_b = unicodedata.normalize("NFC", decomposed).lower()
    assert norm_a == norm_b, (
        f"NFC must unify composed and decomposed VN forms: "
        f"{norm_a!r} vs {norm_b!r}"
    )


def test_compatibility_form_preserved_under_nfc() -> None:
    """NFC preserves compatibility chars; NFKC would have folded them.

    Expected behaviour after S13 P1: '①' stays '①', '㎏' stays '㎏'.
    The previous P22 expectation (fullwidth -> ASCII) is intentionally
    dropped — those characters are rare in VN content but, when present,
    are usually meaningful (circled enumerators in tables, unit symbols
    in pricelists) and folding them silently destroys information.
    """
    fullwidth_one = "１"  # FULLWIDTH DIGIT ONE
    ascii_one = "1"

    # NFC leaves them distinct (compatibility decomposition NOT applied)
    assert unicodedata.normalize("NFC", fullwidth_one) != ascii_one
    # The KG path now keys off NFC, so these stay distinct entities — a
    # safer default than collapsing them together.
    key_a = unicodedata.normalize("NFC", fullwidth_one).lower()
    key_b = unicodedata.normalize("NFC", ascii_one).lower()
    assert key_a != key_b
