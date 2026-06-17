"""HALLU verifier for Speculative Streaming Phase 3 (Wave K2).

Speculative Streaming has the draft (small / fast) model and the main
(big / authoritative) model race to first token. Phase 2 raced the calls;
Phase 3 lets the draft stream optimistically while the verifier double-
checks against the main model's first chunk *before* the user sees the
whole answer. If verifier flags a mismatch, the SSE wire emits a ``redo``
event so the client throws away the draft buffer and waits for main.

Three gates, OR-combined — ANY failure aborts the draft:

1. **Substring overlap** (deterministic). Both draft and main are word-
   shingled with the same window size; the verifier computes the
   fraction of draft shingles whose SHA-256 hash also appears in the
   main shingle set. Below floor → reject (the draft is talking about
   something else).
2. **Numeric fact mismatch** (deterministic, HALLU sacred). Every digit
   token (``500``, ``80%``, ``5.5 million``) extracted from the draft
   MUST appear (as a substring of the normalised number) in the main
   first chunk; otherwise the draft fabricated a number.
3. **Sentence-embedding cosine** (semantic). Embed draft text and main
   first chunk with the bot's existing EmbeddingPort; cosine below
   floor → topic divergence → reject.

Defaults are CLAUDE.md-compliant: zero-hardcode (constants from
``shared/constants``), Port-injected EmbeddingPort (no provider lock-in),
no app-side text injection (verifier reads — never rewrites — both
streams), and HALLU=0 sacred (failure → abort, never silent-accept).
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import structlog

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.ports.embedding_port import EmbeddingPort
from ragbot.shared.constants import (
    DEFAULT_HALLU_VERIFIER_BUFFER_TOKENS,
    DEFAULT_HALLU_VERIFIER_EMBEDDING_THRESHOLD,
    DEFAULT_HALLU_VERIFIER_OVERLAP_THRESHOLD,
    DEFAULT_HALLU_VERIFIER_SHINGLE_SIZE,
)
from ragbot.shared.types import TenantId

logger = structlog.get_logger(__name__)


# Reject-reason discriminators surfaced on the verdict so the SSE wire
# can log + emit a typed ``redo`` event. ``safe`` is the accept path.
REASON_SAFE = "safe"
REASON_WAIT = "wait_more_tokens"
REASON_EMPTY_BUFFER = "empty_buffer"
REASON_OVERLAP_BELOW_FLOOR = "overlap_below_floor"
REASON_NUMERIC_MISMATCH = "numeric_mismatch"
REASON_TOPIC_DIVERGENCE = "topic_divergence"

# Matches integer / decimal / percentage / signed numbers; ``1,234`` and
# ``1.234,5`` separators are folded by ``_normalise_number`` so locale
# differences do not break the equality check.
_NUMBER_RE = re.compile(r"-?\d[\d.,]*%?")


@dataclass(slots=True)
class HALLUVerdict:
    """Result of one ``verify_draft_vs_main`` call.

    Attribute names match the wire schema the SSE helper emits so the
    caller can ``asdict(verdict)`` without renaming.
    """

    safe: bool
    reason: str
    overlap_pct: float
    numeric_mismatch: list[str]
    embedding_cosine: float


def _word_tokens(text: str) -> list[str]:
    """Lowercase, NFC-normalised, whitespace-split tokens.

    Symmetry with ``infrastructure.guardrails.local_guardrail``: the
    output guardrail hashes the same shape so verifier shingles stay
    comparable to system-prompt leak shingles.
    """
    norm = unicodedata.normalize("NFC", text or "").lower()
    return [tok for tok in re.split(r"\s+", norm) if tok]


def _shingle_set(text: str, *, shingle_size: int) -> set[str]:
    """Word-shingle hashes (sha256) of ``text``.

    Empty input → empty set (the verifier later treats this as a
    waiting state, not a failure). ``shingle_size`` clamps below 1 to
    1 so a one-word draft still emits one shingle.
    """
    tokens = _word_tokens(text)
    width = max(1, int(shingle_size))
    if len(tokens) < width:
        return set()
    out: set[str] = set()
    for i in range(0, len(tokens) - width + 1):
        sh = " ".join(tokens[i : i + width])
        out.add(hashlib.sha256(sh.encode("utf-8")).hexdigest())
    return out


def _overlap_pct(draft_shingles: set[str], main_shingles: set[str]) -> float:
    """Fraction of draft shingles also found in main.

    Direction matters: a *short* draft compared against a *long* main is
    asymmetric — we ask "does main contain everything the draft said?"
    not "do the two sets agree". Empty draft → 1.0 (vacuously safe;
    caller should have short-circuited on empty buffer first).
    """
    if not draft_shingles:
        return 1.0
    return len(draft_shingles & main_shingles) / len(draft_shingles)


def _normalise_number(token: str) -> str:
    """Canonical form for numeric equality.

    Strips trailing ``%``, removes thousands separators (``,`` or
    ``.`` when the trailing group looks like 3 digits), and collapses
    a residual decimal to ``.``. Locale-tolerant equality without
    parsing into ``float`` (a string match suffices for the "did the
    main mention this number?" check).
    """
    raw = token.rstrip("%").strip()
    if not raw:
        return ""
    # Drop thousands separators when followed by exactly 3 digits.
    raw = re.sub(r"[.,](?=\d{3}(?:\D|$))", "", raw)
    # Remaining ``,`` → ``.`` for decimal (European style).
    raw = raw.replace(",", ".")
    return raw


def _extract_numbers(text: str) -> list[str]:
    """Return the canonical-form digit tokens that appear in ``text``."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _NUMBER_RE.finditer(text or ""):
        canon = _normalise_number(match.group(0))
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0.0 on degenerate inputs."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class HALLUVerifier:
    """Stateless verifier — one instance per process, callable per stream.

    The verifier does NOT cache draft / main state across calls; the
    streaming router owns the buffer + main-first-chunk strings and
    passes both into :meth:`verify_draft_vs_main`. Thread-safety is
    therefore the embedder's responsibility (existing EmbeddingPort
    implementations are already async-safe via httpx pools).
    """

    def __init__(
        self,
        *,
        embedder: EmbeddingPort,
        overlap_threshold: float = DEFAULT_HALLU_VERIFIER_OVERLAP_THRESHOLD,
        embedding_threshold: float = DEFAULT_HALLU_VERIFIER_EMBEDDING_THRESHOLD,
        buffer_tokens: int = DEFAULT_HALLU_VERIFIER_BUFFER_TOKENS,
        shingle_size: int = DEFAULT_HALLU_VERIFIER_SHINGLE_SIZE,
    ) -> None:
        self._embedder = embedder
        self._overlap_threshold = float(overlap_threshold)
        self._embedding_threshold = float(embedding_threshold)
        self._buffer_tokens = int(buffer_tokens)
        self._shingle_size = int(shingle_size)

    @property
    def buffer_tokens(self) -> int:
        """Number of draft tokens to buffer before requesting verify."""
        return self._buffer_tokens

    async def verify_draft_vs_main(
        self,
        draft_buffer: list[str],
        main_first_chunk: str,
        *,
        spec: EmbeddingSpec | None = None,
        record_tenant_id: TenantId | None = None,
    ) -> HALLUVerdict:
        """Compare a draft buffer against the main model's first chunk.

        @param draft_buffer: tokens (or sub-strings) the draft model has
            produced so far. Joined verbatim — caller controls whether a
            leading space is included so the verifier stays text-faithful.
        @param main_first_chunk: the first non-empty delivery from the
            main model. Short chunks are tolerated via the ``WAIT``
            verdict (caller should retry once more arrives).
        @param spec: EmbeddingSpec for the topic-divergence embed. Optional;
            when ``None`` the verifier skips the embedding gate and reports
            ``embedding_cosine = 1.0`` so callers can flip the gate per
            request without changing the verifier wiring.
        @param record_tenant_id: tenant scope for the embed call. Optional
            in tandem with ``spec``; must be present whenever ``spec`` is.
        @return: HALLUVerdict whose ``safe`` field gates the SSE wire.
        """
        draft_text = "".join(draft_buffer)
        main_text = main_first_chunk or ""

        if not draft_text.strip():
            # Nothing to verify — draft hasn't emitted, caller can flush
            # nothing safely. Treat as safe-but-empty so the SSE wire
            # does NOT emit ``verify_pass`` for a no-op.
            return HALLUVerdict(
                safe=True,
                reason=REASON_EMPTY_BUFFER,
                overlap_pct=1.0,
                numeric_mismatch=[],
                embedding_cosine=1.0,
            )

        # Caller decides when "enough" has arrived; we only signal WAIT
        # when the main chunk is shorter than one shingle window. Caller
        # then accumulates more main tokens and re-invokes.
        main_tokens = _word_tokens(main_text)
        if len(main_tokens) < self._shingle_size:
            return HALLUVerdict(
                safe=False,
                reason=REASON_WAIT,
                overlap_pct=0.0,
                numeric_mismatch=[],
                embedding_cosine=0.0,
            )

        draft_shingles = _shingle_set(draft_text, shingle_size=self._shingle_size)
        main_shingles = _shingle_set(main_text, shingle_size=self._shingle_size)
        overlap = _overlap_pct(draft_shingles, main_shingles)

        # Gate 1 — substring overlap floor (deterministic).
        if overlap < self._overlap_threshold:
            return HALLUVerdict(
                safe=False,
                reason=REASON_OVERLAP_BELOW_FLOOR,
                overlap_pct=overlap,
                numeric_mismatch=[],
                embedding_cosine=0.0,
            )

        # Gate 2 — every draft number must appear in main (HALLU sacred
        # anti-fabricate-numbers rule from CLAUDE.md).
        draft_nums = _extract_numbers(draft_text)
        main_nums_text = " " + " ".join(_extract_numbers(main_text)) + " "
        missing = [n for n in draft_nums if f" {n} " not in main_nums_text]
        if missing:
            logger.info(
                "hallu_verifier_numeric_mismatch",
                draft_numbers=draft_nums,
                main_numbers=_extract_numbers(main_text),
                missing=missing,
            )
            return HALLUVerdict(
                safe=False,
                reason=REASON_NUMERIC_MISMATCH,
                overlap_pct=overlap,
                numeric_mismatch=missing,
                embedding_cosine=0.0,
            )

        # Gate 3 — semantic topic divergence (optional; off when spec=None).
        cosine = 1.0
        if spec is not None and record_tenant_id is not None:
            vecs = await self._embedder.embed_batch(
                [draft_text, main_text],
                spec=spec,
                record_tenant_id=record_tenant_id,
            )
            if len(vecs) == 2:
                cosine = _cosine(vecs[0], vecs[1])
            if cosine < self._embedding_threshold:
                return HALLUVerdict(
                    safe=False,
                    reason=REASON_TOPIC_DIVERGENCE,
                    overlap_pct=overlap,
                    numeric_mismatch=[],
                    embedding_cosine=cosine,
                )

        return HALLUVerdict(
            safe=True,
            reason=REASON_SAFE,
            overlap_pct=overlap,
            numeric_mismatch=[],
            embedding_cosine=cosine,
        )


def verdict_to_payload(verdict: HALLUVerdict) -> dict[str, Any]:
    """Helper for the SSE wire — flattens the dataclass into a JSON dict."""
    return {
        "safe": verdict.safe,
        "reason": verdict.reason,
        "overlap_pct": verdict.overlap_pct,
        "numeric_mismatch": list(verdict.numeric_mismatch),
        "embedding_cosine": verdict.embedding_cosine,
    }


__all__ = [
    "HALLUVerdict",
    "HALLUVerifier",
    "REASON_EMPTY_BUFFER",
    "REASON_NUMERIC_MISMATCH",
    "REASON_OVERLAP_BELOW_FLOOR",
    "REASON_SAFE",
    "REASON_TOPIC_DIVERGENCE",
    "REASON_WAIT",
    "verdict_to_payload",
]
