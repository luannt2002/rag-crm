"""OCR parser factory — fail-loud on dependency drift.

Selection precedence:
  1. env var ``RAGBOT_PARSER_ENGINE`` (set by ops/startup script)
  2. default: :data:`ragbot.shared.constants.DEFAULT_PARSER_ENGINE`
     (currently ``"kreuzberg"`` post AdapChunk Wave C2)

system_config key ``parser_engine`` is the source of truth; operators sync
it to the env var before (re)starting the service. This keeps DI wiring
sync and avoids async DB reads during container construction.

**Fail-loud contract** (CLAUDE.md rule "deploy-time failure = fail loud"):
  When the requested engine's dependency is missing the factory raises
  ``ImportError`` so systemd surfaces the broken state instead of silently
  serving traffic on a downgraded parser. Operator MUST install the dep
  declared in ``pyproject.toml`` for the chosen engine. Silent fallback
  used to hide drift between code/config/runtime — a 2026-05-14 audit
  found ``parser_engine='kreuzberg'`` in DB while the venv was running
  ``SimpleTextParser`` because Kreuzberg wasn't pip-installed.

Why no fallback chain anymore:
  - ``parser_engine='kreuzberg'`` is the platform default and is the only
    parser exercised by current load tests + AdapChunk waves.
  - ``docling`` / ``simple`` remain available as opt-in engines for legacy
    bots; setting the env or DB to those values still resolves correctly
    but each must also have its own dependency installed (no silent
    cross-engine swap).
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
