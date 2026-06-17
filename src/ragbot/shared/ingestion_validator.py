"""Ingestion quality validation — advisory checks after chunk+embed.

Runs automated quality checks on ingested chunks. Results are logged
but never block ingestion (advisory only).
"""

from __future__ import annotations

import structlog

from ragbot.shared.constants import DEFAULT_INGEST_VALIDATOR_MAX_CHUNK_CHARS

logger = structlog.get_logger(__name__)


def _jaccard_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two strings using word-level tokens."""
    set_a = set(a.lower().split())
    set_b = set(b.lower().split())
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


async def validate_ingestion(
    chunks: list[dict],
    document_name: str,
    *,
    original_content_length: int = 0,
    min_chunk_chars: int = 20,
    max_chunk_chars: int = DEFAULT_INGEST_VALIDATOR_MAX_CHUNK_CHARS,
) -> dict:
    """Validate ingestion quality. Returns {ok: bool, issues: list[str], score: float}.

    @param chunks: list of dicts with keys "content" (str) and optional "embedding" (list|None)
    @param document_name: document title for logging
    @param original_content_length: length of original document content (chars)
    @param min_chunk_chars: minimum acceptable chunk length
    @param max_chunk_chars: maximum acceptable chunk length (above = likely parse error)
    @return: dict with ok (bool), issues (list[str]), score (float 0.0-1.0)
    """
    issues: list[str] = []
    total_checks = 0
    passed_checks = 0

    if not chunks:
        return {"ok": False, "issues": ["no chunks produced"], "score": 0.0}

    # ── Check 1: No empty chunks ──
    for i, chunk in enumerate(chunks):
        total_checks += 1
        content = chunk.get("content", "")
        if len(content) == 0:
            issues.append(f"empty chunk at index {i}")
        else:
            passed_checks += 1

    # ── Check 2: No chunks below min_chunk_chars ──
    for i, chunk in enumerate(chunks):
        total_checks += 1
        content = chunk.get("content", "")
        if 0 < len(content) < min_chunk_chars:
            issues.append(
                f"chunk {i} too short ({len(content)} chars < {min_chunk_chars})"
            )
        else:
            passed_checks += 1

    # ── Check 3: No chunks above max_chunk_chars ──
    for i, chunk in enumerate(chunks):
        total_checks += 1
        content = chunk.get("content", "")
        if len(content) > max_chunk_chars:
            issues.append(
                f"chunk {i} too long ({len(content)} chars > {max_chunk_chars})"
            )
        else:
            passed_checks += 1

    # ── Check 4: No duplicate chunks (>95% Jaccard similarity) ──
    duplicate_threshold = 0.95
    seen_duplicates: set[tuple[int, int]] = set()
    for i in range(len(chunks)):
        for j in range(i + 1, len(chunks)):
            total_checks += 1
            content_i = chunks[i].get("content", "")
            content_j = chunks[j].get("content", "")
            if content_i and content_j:
                sim = _jaccard_similarity(content_i, content_j)
                if sim > duplicate_threshold:
                    pair = (i, j)
                    if pair not in seen_duplicates:
                        seen_duplicates.add(pair)
                        issues.append(
                            f"near-duplicate chunks {i} and {j} "
                            f"(Jaccard={sim:.2f})"
                        )
                else:
                    passed_checks += 1
            else:
                passed_checks += 1

    # ── Check 5: Vector norm check (embedding not all zeros) ──
    for i, chunk in enumerate(chunks):
        embedding = chunk.get("embedding")
        if embedding is not None:
            total_checks += 1
            try:
                if hasattr(embedding, "__iter__") and all(
                    v == 0 for v in embedding
                ):
                    issues.append(f"chunk {i} has all-zero embedding vector")
                else:
                    passed_checks += 1
            except (TypeError, ValueError):
                passed_checks += 1

    # ── Check 6: Total text coverage ──
    if original_content_length > 0:
        total_checks += 1
        total_chunk_chars = sum(
            len(chunk.get("content", "")) for chunk in chunks
        )
        coverage = total_chunk_chars / original_content_length
        if coverage < 0.5:
            issues.append(
                f"low text coverage: {coverage:.1%} of original "
                f"({total_chunk_chars}/{original_content_length} chars)"
            )
        else:
            passed_checks += 1

    # ── Compute score ──
    score = passed_checks / total_checks if total_checks > 0 else 1.0
    ok = len(issues) == 0

    return {"ok": ok, "issues": issues, "score": round(score, 3)}
