"""Tests for shared.text_normalization (NFKC->NFC sweep).

Guards:
- ``normalize_vn`` returns NFC canonical form (not NFKC).
- VN diacritics survive a roundtrip from decomposed (NFD) input.
- Compatibility-only characters (e.g. circled digits, unit symbols, halfwidth)
  are NOT folded — they would be folded by NFKC but must be preserved by NFC.
- ``normalize_for_hash`` is hash-stable: same VN content -> same NFC bytes ->
  same hash regardless of input form.
- Regression-prevent: production source under ``src/ragbot/`` contains zero
  ``unicodedata.normalize("NFKC", ...)`` calls. New code that re-introduces
  NFKC must fail this test.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

import pytest

from ragbot.shared.constants import DEFAULT_NORMALIZATION_FORM
from ragbot.shared.text_normalization import (
    normalize_for_hash,
    normalize_vn,
)


def test_default_form_is_nfc() -> None:
    """Constants pin the canonical form to NFC."""
    assert DEFAULT_NORMALIZATION_FORM == "NFC"


def test_normalize_vn_uses_nfc() -> None:
    """``normalize_vn`` produces NFC-equivalent output for arbitrary input.

    Compose 'ế' from base 'e' + combining circumflex + combining acute (NFD-ish)
    and verify it folds to the precomposed single codepoint per NFC.
    """
    decomposed = "e" + "̂" + "́"  # NFD form of "ế"
    out = normalize_vn(decomposed)
    assert out == "ế"
    assert unicodedata.is_normalized("NFC", out)
    # NFC of an already-NFC string is idempotent.
    assert normalize_vn(out) == out


def test_vn_diacritic_preserved() -> None:
    """Vietnamese diacritics survive the normalization unchanged in glyph."""
    sentence_nfd = unicodedata.normalize(
        "NFD", "Tiếng Việt có dấu phức hợp: ổn, ữ, ặc, ợt"
    )
    sentence_nfc = unicodedata.normalize(
        "NFC", "Tiếng Việt có dấu phức hợp: ổn, ữ, ặc, ợt"
    )
    assert normalize_vn(sentence_nfd) == sentence_nfc
    # Verify a representative precomposed VN char is preserved as a single cp.
    assert "ế" in normalize_vn(sentence_nfd)


def test_no_width_fold() -> None:
    """NFC must NOT fold compatibility chars that NFKC would fold.

    Specifically: '①' must stay '①' (NFKC -> '1'), '㎏' must stay '㎏'
    (NFKC -> 'kg'), and halfwidth katakana must stay halfwidth.
    """
    samples = [
        "①",   # '①' — circled digit one
        "㎏",   # '㎏' — square kg
        "Ａ",   # 'Ａ' — fullwidth A
        "ｱ",   # 'ｱ' — halfwidth katakana A
    ]
    for ch in samples:
        out = normalize_vn(ch)
        assert out == ch, (
            f"NFC must preserve compatibility char {ch!r}; got {out!r}. "
            "Did someone swap normalize_vn back to NFKC?"
        )
        # Sanity: NFKC really would fold these (proof the test isn't vacuous).
        assert unicodedata.normalize("NFKC", ch) != ch


def test_hash_key_consistent() -> None:
    """Same VN content via different input forms -> same hash through normalize_for_hash."""
    nfd_input = unicodedata.normalize("NFD", "Bảng giá dịch vụ năm 2026")
    nfc_input = unicodedata.normalize("NFC", "Bảng giá dịch vụ năm 2026")
    assert nfd_input != nfc_input  # raw bytes differ
    h1 = hashlib.sha256(normalize_for_hash(nfd_input).encode("utf-8")).hexdigest()
    h2 = hashlib.sha256(normalize_for_hash(nfc_input).encode("utf-8")).hexdigest()
    assert h1 == h2


def test_normalize_for_hash_returns_nfc() -> None:
    """The hash-stable form is itself NFC (not NFKC)."""
    out = normalize_for_hash("① Bảng giá")  # circled-1 + VN
    # Compatibility char preserved (would not survive NFKC).
    assert "①" in out
    assert unicodedata.is_normalized("NFC", out)


def test_no_nfkc_in_codebase_grep_guard() -> None:
    """Regression guard: zero NFKC normalize() calls under src/ragbot/.

    Any future PR that re-introduces ``unicodedata.normalize("NFKC", ...)``
    in production code must fail here. Comments/docstrings mentioning
    "NFKC" are allowed (migration history).
    """
    # Resolve src/ragbot/ relative to this test file: tests/unit/<file>.
    src_root = Path(__file__).resolve().parents[2] / "src" / "ragbot"
    assert src_root.is_dir(), f"Expected source root at {src_root}"

    # Match call expressions only — comments + docstrings are fine.
    nfkc_call = re.compile(r"""unicodedata\.normalize\(\s*["']NFKC["']""")

    offenders: list[str] = []
    for py_path in src_root.rglob("*.py"):
        # Allow text_normalization.py to mention NFKC in its docstring; the
        # regex above only matches actual call sites so the docstring is safe.
        for lineno, line in enumerate(
            py_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if nfkc_call.search(line):
                offenders.append(f"{py_path}:{lineno}: {line.strip()}")

    assert not offenders, (
        "NFKC normalize() call(s) re-introduced in production source. "
        "Use ragbot.shared.text_normalization.normalize_vn instead.\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ascii only",
        "Tiếng Việt",
        "Mixed 한글 + Tiếng Việt + 123",
    ],
)
def test_normalize_vn_idempotent(raw: str) -> None:
    once = normalize_vn(raw)
    twice = normalize_vn(once)
    assert once == twice
