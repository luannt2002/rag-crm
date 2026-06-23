"""OCR parser factory — resolve the configured engine, degrade observably.

Selection precedence:
  1. env var ``RAGBOT_PARSER_ENGINE`` (set by ops/startup script)
  2. default: :data:`ragbot.shared.constants.DEFAULT_PARSER_ENGINE`
     (currently ``"kreuzberg"``)

``system_config.parser_engine`` is the source of truth; operators sync it to
the env var before (re)starting the service. Reading the env keeps DI wiring
synchronous and avoids async DB reads during container construction.

Degradation contract:
  - ``kreuzberg`` (the platform default): if the Kreuzberg dependency is not
    installed, the factory degrades to ``SimpleTextParser`` and emits an
    ``ocr_parser_fallback`` WARNING carrying ``requested_engine`` /
    ``resolved_engine`` so ops can detect code/config/runtime drift — a
    missing optional dep must not take ingest fully offline. Operators
    restore the configured engine with ``pip install 'ragbot[parsers]'``.
  - ``docling`` / ``simple``: explicit opt-in engines; each requires its own
    dependency and is constructed directly (no silent cross-engine swap).
  - Unknown engine token: raised as ``ValueError`` — a config typo must
    surface loudly instead of landing on a silent downgrade.

The kreuzberg→simple fallback is a deliberate, test-pinned behaviour
(``tests/unit/test_kreuzberg_parser.py::test_ocr_factory_falls_back_to_simple_when_kreuzberg_missing``);
switching it to fail-loud would be an ADR-level decision, not a doc edit.
"""

from __future__ import annotations

import os

import structlog

from ragbot.application.ports.ocr_port import OCRPort
from ragbot.shared.constants import (
    DEFAULT_PARSER_ENGINE,
    DOCLING_PARSER_ENGINE_KEY,
    KREUZBERG_PARSER_ENGINE_KEY,
    RAGBOT_PARSER_ENGINE_ENV,
    SIMPLE_PARSER_ENGINE_KEY,
)

logger = structlog.get_logger(__name__)


def build_ocr_parser() -> OCRPort:
    engine = (
        os.environ.get(RAGBOT_PARSER_ENGINE_ENV) or DEFAULT_PARSER_ENGINE
    ).strip().lower()

    if engine == KREUZBERG_PARSER_ENGINE_KEY:
        try:
            from ragbot.infrastructure.ocr.kreuzberg_parser import KreuzbergParser
            parser = KreuzbergParser()
        except ImportError as exc:
            # Kreuzberg lib not installed in this venv — fall back to
            # the always-available SimpleTextParser so ingest keeps
            # serving on a downgraded engine. Logged at WARN so ops
            # see the drift; operators MUST run ``pip install
            # 'ragbot[parsers]'`` to restore the configured engine.
            from ragbot.infrastructure.ocr.simple_text_parser import (
                SimpleTextParser,
            )
            logger.warning(
                "ocr_parser_fallback",
                requested_engine=KREUZBERG_PARSER_ENGINE_KEY,
                resolved_engine=SIMPLE_PARSER_ENGINE_KEY,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            parser = SimpleTextParser()
            return parser
        logger.info("ocr_parser_selected", engine=KREUZBERG_PARSER_ENGINE_KEY)
        return parser

    if engine == DOCLING_PARSER_ENGINE_KEY:
        from ragbot.infrastructure.ocr.docling_parser import DoclingParser
        parser = DoclingParser()
        logger.info("ocr_parser_selected", engine=DOCLING_PARSER_ENGINE_KEY)
        return parser

    if engine == SIMPLE_PARSER_ENGINE_KEY:
        from ragbot.infrastructure.ocr.simple_text_parser import SimpleTextParser
        parser = SimpleTextParser()
        logger.info("ocr_parser_selected", engine=SIMPLE_PARSER_ENGINE_KEY)
        return parser

    # Unknown engine token — surface the typo loudly instead of silently
    # landing on a downgrade. CLAUDE.md fail-loud rule applies here too.
    raise ValueError(
        f"Unknown parser engine '{engine}' — set system_config.parser_engine to "
        f"one of: {KREUZBERG_PARSER_ENGINE_KEY} / {DOCLING_PARSER_ENGINE_KEY} / "
        f"{SIMPLE_PARSER_ENGINE_KEY}.",
    )


__all__ = ["build_ocr_parser"]
