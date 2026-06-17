"""Lock test — F14-CRIT-2 OCR parsers no longer hardcode language="vi".

Asserts that ``SimpleTextParser`` and ``DoclingParser`` source the default
``ParsedDocument.language`` from ``DEFAULT_LANGUAGE`` constant — so changing
the deployment-wide default (e.g. an EN-first install) flows through without
patching parser source files.

Domain-neutral: no brand / industry / language-content assumption.
"""

from __future__ import annotations

import inspect

from ragbot.infrastructure.ocr import simple_text_parser as stp_module
from ragbot.shared.constants import DEFAULT_LANGUAGE


def test_simple_text_parser_imports_default_language_constant() -> None:
    """Source file must import DEFAULT_LANGUAGE — no inline 'vi' literal."""
    src = inspect.getsource(stp_module)
    assert "DEFAULT_LANGUAGE" in src, (
        "F14-CRIT-2 regression — simple_text_parser must import DEFAULT_LANGUAGE"
    )
    # Hardcoded literal must be gone in the parse() path.
    assert 'language="vi"' not in src, (
        "F14-CRIT-2 regression — hardcode language=\"vi\" reintroduced"
    )


def test_docling_parser_imports_default_language_constant() -> None:
    from ragbot.infrastructure.ocr import docling_parser as dlp_module
    src = inspect.getsource(dlp_module)
    assert "DEFAULT_LANGUAGE" in src, (
        "F14-CRIT-2 regression — docling_parser must import DEFAULT_LANGUAGE"
    )
    assert 'language="vi"' not in src, (
        "F14-CRIT-2 regression — hardcode language=\"vi\" reintroduced"
    )


def test_default_language_constant_exists() -> None:
    """Sanity: DEFAULT_LANGUAGE constant lives in shared.constants."""
    assert isinstance(DEFAULT_LANGUAGE, str)
    assert len(DEFAULT_LANGUAGE) >= 2
