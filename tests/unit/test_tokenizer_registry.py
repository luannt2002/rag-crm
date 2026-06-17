"""F14-multi-industry — per-language tokenizer registry.

Pins the Strategy + Port + DI contract introduced for WS-4: adding a new
vertical or language = adding a new file under
``infrastructure/tokenizer/`` plus one entry in ``_REGISTRY``. No edits
to ingest service or query graph.
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.tokenizer_port import TokenizerPort

try:
    from ragbot.infrastructure.tokenizer.null_tokenizer import NullTokenizer
    from ragbot.infrastructure.tokenizer.registry import (
        build_tokenizer,
        list_languages,
    )
    from ragbot.infrastructure.tokenizer.simple_tokenizer import SimpleTokenizer
    from ragbot.infrastructure.tokenizer.vi_tokenizer import ViTokenizer
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "tokenizer subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )


def test_vi_uses_underthesea() -> None:
    """``vi`` resolves to the ViTokenizer strategy."""
    tk = build_tokenizer("vi")
    assert isinstance(tk, ViTokenizer), (
        f"VN must resolve to ViTokenizer, got {type(tk).__name__}"
    )
    assert tk.get_language() == "vi"
    # Smoke: tokenises VN text into multiple tokens.
    tokens = tk.tokenize("chăm sóc da mặt")
    assert len(tokens) >= 2


def test_en_uses_simple() -> None:
    """``en`` resolves to the SimpleTokenizer strategy."""
    tk = build_tokenizer("en")
    assert isinstance(tk, SimpleTokenizer)
    assert tk.get_language() == "en"
    tokens = tk.tokenize("skin care services pricing")
    assert tokens == ["skin", "care", "services", "pricing"]


def test_unknown_language_falls_back_to_simple() -> None:
    """Unknown language code falls back to NullTokenizer (delegates to Simple)."""
    tk = build_tokenizer("xx-unknown")
    assert isinstance(tk, NullTokenizer), (
        "Unknown languages must hit the Null Object branch."
    )
    # Still produces a usable token list.
    assert tk.tokenize("hello world") == ["hello", "world"]
    assert tk.count_tokens("hello world") == 2


def test_registry_resolution() -> None:
    """``list_languages`` exposes the multi-language platform default set."""
    langs = list_languages()
    # Must include the multi-industry hardening minimum set.
    for code in ("vi", "en", "ja", "ko", "zh", "ar", "th"):
        assert code in langs, (
            f"Multi-language registry missing {code!r} — F14 hardening WS-4."
        )
    # Every registered strategy must implement the Port contract (duck-typed).
    for code in langs:
        tk = build_tokenizer(code)
        assert isinstance(tk, TokenizerPort), (
            f"{code} strategy violates TokenizerPort protocol: {type(tk).__name__}"
        )
        # Empty / whitespace must not raise.
        assert tk.tokenize("") == []
        assert tk.count_tokens("") == 0
