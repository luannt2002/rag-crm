"""Wave M3.3-E — pin: no integer-literal defaults for top_k / rerank knobs.

The mega-sprint-G21 rename swapped legacy ``top_k_retrieve`` /
``top_k_rerank`` for the canonical ``rag_top_k`` / ``rag_rerank_top_n``,
but the fallback literals in ``chat_worker._build_pipeline_config`` and
``test_chat._build_pipeline_config`` kept the OLD numbers (``5`` and
``20``) rather than the constants ``DEFAULT_RERANK_TOP_N=7`` /
``DEFAULT_TOP_K=20``. When the ``system_config`` row was missing, the
fallback silently regressed the Z2 retrieval-tuning seed.

This test scans both builders for the pattern
``rag_(rerank_)?top_(k|n).*[, )]\\s*\\d+\\s*[,)]`` (last positional arg
to a coerce/get helper). Any int-literal default fails the test —
the call must reference ``DEFAULT_TOP_K`` or ``DEFAULT_RERANK_TOP_N``.

Regression guard, not a stylistic rule: a future refactor that breaks
the canonical link silently regresses production quality on a missing
config row, which is a real-world failure mode this repo has hit.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ``chat_worker`` was split from a single module into a package; scan every
# file in the package so the grepped pattern is found wherever it landed.
_CW_PKG = REPO_ROOT / "src/ragbot/interfaces/workers/chat_worker"

_TARGET_FILES: tuple[Path, ...] = (
    *sorted(_CW_PKG.glob("*.py")),
    REPO_ROOT / "src/ragbot/interfaces/http/routes/test_chat/_pipeline_config.py",
)

# Match e.g. ``_cfg_int(_cfg, "rag_rerank_top_n", 5)`` or
# ``_coerce_int(raw.get("rag_top_k"), 20)`` — bare int after the key.
_BAD_PATTERN = re.compile(
    r'(?:_cfg_int|_coerce_int)\([^)]*?["\']rag_(?:rerank_)?top_[kn]["\'][^)]*?,\s*(\d+)\s*\)'
)


def test_no_literal_int_default_for_top_k_or_rerank_top_n() -> None:
    """``chat_worker`` + ``test_chat`` builders MUST use constants, not
    raw integer literals, for ``rag_top_k`` / ``rag_rerank_top_n`` fallback.

    Pre-fix violators (now corrected):
    - ``chat_worker.py:752``  ``_cfg_int(_cfg, "rag_rerank_top_n", 5)``
    - ``chat_worker.py:765``  ``_cfg_int(_cfg, "rag_top_k", 20)``
    - ``test_chat.py:463``    ``_coerce_int(raw.get("rag_rerank_top_n"), 5)``
    - ``test_chat.py:459``    ``_coerce_int(raw.get("rag_top_k"), 20)``

    Wave M3.3-A/B replaced all four with ``DEFAULT_RERANK_TOP_N`` /
    ``DEFAULT_TOP_K``. This regression guard pins that.
    """
    violations: list[str] = []
    for path in _TARGET_FILES:
        text = path.read_text(encoding="utf-8")
        for match in _BAD_PATTERN.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            violations.append(
                f"{path.relative_to(REPO_ROOT)}:{line_no}  literal={match.group(1)}  "
                f"snippet={match.group(0)!r}"
            )
    assert not violations, (
        "Integer-literal default for rag_top_k / rag_rerank_top_n found.\n"
        "Replace the literal with DEFAULT_TOP_K or DEFAULT_RERANK_TOP_N "
        "(import from ragbot.shared.constants):\n  "
        + "\n  ".join(violations)
    )


def test_no_prior_topk_keys_in_system_config_audit() -> None:
    """Migration 010o deleted legacy ``top_k_retrieve`` / ``top_k_rerank``
    rows from ``system_config``. Source must not re-introduce them.

    Live runtime callsites are the production builders (chat_worker +
    test_chat) plus the Pareto sweep harness. Migration files are
    historical and exempt — they may name the legacy keys in their
    upgrade/downgrade body. Analytics scripts may mention the legacy
    keys in comments to reference the rename.
    """
    bad = []
    pattern = re.compile(r'["\']top_k_(?:retrieve|rerank)["\']')
    scan_paths = [
        *sorted(_CW_PKG.glob("*.py")),
        REPO_ROOT / "src/ragbot/interfaces/http/routes/test_chat.py",
        REPO_ROOT / "src/ragbot/interfaces/http/routes/chat_stream.py",
        REPO_ROOT / "src/ragbot/orchestration/query_graph.py",
    ]
    for path in scan_paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            bad.append(f"{path.relative_to(REPO_ROOT)}:{line_no}  {match.group(0)}")
    assert not bad, (
        "Legacy top_k_retrieve / top_k_rerank reference in production runtime path. "
        "Migration 010o removed the corresponding system_config rows; readers must "
        "use rag_top_k / rag_rerank_top_n instead.\n  "
        + "\n  ".join(bad)
    )
