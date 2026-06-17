# ============================================================
# DEAD-CODE NOTICE — 2026-06-03
# ============================================================
# This module is NOT reachable from any production entry point.
# Verified via:
#   * AST import-graph reachability scan (entry: FastAPI app +
#     workers + middlewares + routes)
#   * 10-agent multi-trace audit (Agent 9 vulture + Agent 10
#     runtime-path)
#
# Reason: Helper functions copy-pasted inline into document_service.py. Module itself never imported.
#
# Status:
#   * Code kept INTACT (reversible — remove this header to reactivate)
#   * Safe to delete physically; defer to operator decision
#
# To reactivate:
#   1. Confirm a runtime caller is intentional (search registry
#      strings, dynamic imports)
#   2. Remove this header block
#   3. Wire the registry / DI binding in bootstrap.py
# ============================================================

# """Diff-based re-ingest helpers (T2 Cost+Perf).

# Re-ingesting an unchanged document should not pay for re-embedding the same
# chunks. The ingest pipeline in :mod:`document_service` already SHA-256
# fingerprints every post-enrichment chunk (see ``_compute_chunk_hashes``); this
# module surfaces the diff as a small, pure, *feature-flagged* helper so:

# 1. Cost saving is observable from structlog (event
#    ``diff_reingest_skip`` with ``chunks_skipped`` and ``cost_saved_usd``),
#    matching the Master Observability Matrix step name
#    ``diff_reingest_skip``.
# 2. Other ingest paths (future bulk reingest tools, replay jobs) can compute
#    the same diff without copy-pasting the inline loop.
# 3. Unit tests can assert idempotency (same content → 0 embed calls) and
#    selective re-embed (1 changed section → only that index re-embedded)
#    without spinning up the full async ingest stack.

# The module is *pure*: it never touches the DB, it never opens an embedding
# client, and it does not read any tenant-scoped state. Callers MUST scope the
# ``existing_hashes`` dict to a single ``(record_tenant_id, record_bot_id,
# record_document_id)`` triple — the helper only operates on the inputs it is
# given, which keeps the 4-key bot identity boundary inviolate.

# Proof citation:
#     Chunk-level hash dedup is a standard incremental-indexing pattern; the
#     expected cost reduction on partial-update workloads is in the
#     -70..-90% range vs naive full-reembed (see e.g. LlamaIndex
#     "Incremental Indexing" docs and the Pinecone "Hybrid update" guide).

# Feature flag:
#     ``diff_based_reingest_enabled`` (system_config bool, default
#     :data:`ragbot.shared.constants.DEFAULT_DIFF_REINGEST_ENABLED`).

# Telemetry:
#     structlog event ``diff_reingest_skip``
#         fields: ``record_bot_id``, ``record_document_id``, ``chunks_total``,
#         ``chunks_skipped``, ``chunks_to_embed``, ``chunks_stale``,
#         ``cost_saved_usd``, ``embed_cost_usd_per_1m_tokens``.
# """

# from __future__ import annotations

# import hashlib
# from dataclasses import dataclass
# from typing import Mapping

# import structlog

# from ragbot.shared.constants import (
#     DEFAULT_CHARS_PER_TOKEN_ESTIMATE,
#     DEFAULT_CONTENT_HASH_HEX_LEN,
#     DEFAULT_EMBED_COST_USD_PER_1M_TOKENS,
#     TOKENS_PER_MILLION,
# )

# logger = structlog.get_logger(__name__)


# @dataclass(frozen=True)
# class DiffResult:
#     """Outcome of comparing newly chunked text against persisted chunk hashes.

#     Attributes:
#         to_embed: ``(chunk_index, hash)`` pairs that MUST be re-embedded —
#             either new (no entry in ``existing_hashes``) or changed (hash
#             mismatch).
#         unchanged: indices whose persisted hash equals the new hash. Caller
#             keeps the existing row, skips the embedder.
#         stale: indices present in ``existing_hashes`` whose index is now out
#             of range (document shrank) — caller deletes them.
#         chunks_total: ``len(new_hashes)`` mirrored for downstream logging.
#         cost_saved_usd: estimated USD that *would* have been spent
#             re-embedding ``unchanged`` chunks under the cost model.
#     """

#     to_embed: tuple[tuple[int, str], ...]
#     unchanged: tuple[int, ...]
#     stale: tuple[int, ...]
#     chunks_total: int
#     cost_saved_usd: float

#     @property
#     def chunks_skipped(self) -> int:
#         """Alias for ``len(unchanged)`` — the headline saving metric."""
#         return len(self.unchanged)


# def compute_chunk_hashes(
#     texts: list[str],
#     *,
#     hex_len: int = DEFAULT_CONTENT_HASH_HEX_LEN,
# ) -> list[str]:
#     """Return SHA-256 hex fingerprints for ``texts``, truncated to ``hex_len``.

#     Caller MUST pass the **post-enrichment** chunk strings (the bytes the
#     embedder will actually see). Feeding raw chunks here would keep the
#     cached hash stable across enrichment-context changes and silently let
#     the incremental re-index skip a re-embed even when the prefix changed
#     — leaving a stale vector under a misleading fingerprint.

#     The default ``hex_len`` matches the persisted
#     ``document_chunks.content_hash`` column width so the values compare
#     byte-for-byte against rows already in the database.
#     """
#     return [
#         hashlib.sha256((t or "").encode("utf-8")).hexdigest()[:hex_len]
#         for t in texts
#     ]


# def estimate_embed_cost_usd(
#     chunk_texts: list[str],
#     *,
#     cost_per_1m_tokens: float = DEFAULT_EMBED_COST_USD_PER_1M_TOKENS,
#     chars_per_token: float = DEFAULT_CHARS_PER_TOKEN_ESTIMATE,
# ) -> float:
#     """Order-of-magnitude USD estimate for embedding ``chunk_texts``.

#     The estimate is a *reporting* figure (used only for the
#     ``cost_saved_usd`` line in the structlog event) — it is never a billing
#     input. The formula is::

#         total_chars   = sum(len(t) for t in chunk_texts)
#         total_tokens  = total_chars / chars_per_token
#         cost_usd      = total_tokens / TOKENS_PER_MILLION * cost_per_1m_tokens

#     A negative or zero ``chars_per_token`` is treated as the constant
#     default so a misconfigured override cannot produce a divide-by-zero or
#     nonsensical negative saving headline.
#     """
#     if chars_per_token <= 0:
#         chars_per_token = DEFAULT_CHARS_PER_TOKEN_ESTIMATE
#     total_chars = sum(len(t or "") for t in chunk_texts)
#     total_tokens = total_chars / chars_per_token
#     return (total_tokens / TOKENS_PER_MILLION) * cost_per_1m_tokens


# def compute_diff(
#     new_chunk_texts: list[str],
#     new_chunk_hashes: list[str],
#     existing_hashes: Mapping[int, str],
#     *,
#     cost_per_1m_tokens: float = DEFAULT_EMBED_COST_USD_PER_1M_TOKENS,
#     chars_per_token: float = DEFAULT_CHARS_PER_TOKEN_ESTIMATE,
# ) -> DiffResult:
#     """Return the (to_embed, unchanged, stale, cost_saved) decomposition.

#     Args:
#         new_chunk_texts: post-enrichment chunk strings the embedder would
#             see. Used only to size ``cost_saved_usd`` for the unchanged
#             chunks (the embedder is fed ``to_embed`` upstream; this helper
#             never embeds).
#         new_chunk_hashes: SHA-256 fingerprints of ``new_chunk_texts``,
#             usually produced by :func:`compute_chunk_hashes`. Must be the
#             same length as ``new_chunk_texts``.
#         existing_hashes: ``{chunk_index: persisted_hash}`` for the document
#             being re-ingested. Empty dict on first ingest (no skips
#             possible) — the caller is expected to pass ``{}`` then.
#         cost_per_1m_tokens: USD per 1M embedding tokens. Defaults to
#             :data:`DEFAULT_EMBED_COST_USD_PER_1M_TOKENS`; deployments may
#             inject a per-bot or per-tenant override here without touching
#             the ingest pipeline.
#         chars_per_token: chars-per-token tokenisation heuristic, see
#             :func:`estimate_embed_cost_usd`.

#     Raises:
#         ValueError: when ``new_chunk_texts`` and ``new_chunk_hashes`` have
#             different lengths — a programmer error that would otherwise
#             silently mis-skip embeds.
#     """
#     if len(new_chunk_texts) != len(new_chunk_hashes):
#         raise ValueError(
#             "new_chunk_texts and new_chunk_hashes length mismatch: "
#             f"{len(new_chunk_texts)} vs {len(new_chunk_hashes)}",
#         )

#     to_embed: list[tuple[int, str]] = []
#     unchanged: list[int] = []
#     unchanged_texts: list[str] = []
#     for i, h in enumerate(new_chunk_hashes):
#         prev = existing_hashes.get(i)
#         if prev is not None and prev == h:
#             unchanged.append(i)
#             unchanged_texts.append(new_chunk_texts[i])
#         else:
#             to_embed.append((i, h))

#     stale = tuple(
#         sorted(idx for idx in existing_hashes if idx >= len(new_chunk_hashes))
#     )

#     cost_saved = estimate_embed_cost_usd(
#         unchanged_texts,
#         cost_per_1m_tokens=cost_per_1m_tokens,
#         chars_per_token=chars_per_token,
#     )

#     return DiffResult(
#         to_embed=tuple(to_embed),
#         unchanged=tuple(unchanged),
#         stale=stale,
#         chunks_total=len(new_chunk_hashes),
#         cost_saved_usd=cost_saved,
#     )


# def log_diff_event(
#     diff: DiffResult,
#     *,
#     enabled: bool,
#     record_bot_id: str | None,
#     record_document_id: str | None,
#     cost_per_1m_tokens: float = DEFAULT_EMBED_COST_USD_PER_1M_TOKENS,
# ) -> None:
#     """Emit ``diff_reingest_skip`` if the feature flag is on and any chunk skipped.

#     The event is intentionally a *no-op* when the flag is off so that
#     ``cost_audit.py per-feature`` reports a clean zero (no event = feature
#     inactive) — never a phantom non-zero saving on a deployment that
#     hasn't opted in. Likewise when ``chunks_skipped == 0`` we still emit
#     an event because a load-test asserting "0 chunks skipped on a
#     fresh-ingest" needs the negative observation in the log.
#     """
#     if not enabled:
#         return
#     logger.info(
#         "diff_reingest_skip",
#         record_bot_id=record_bot_id,
#         record_document_id=record_document_id,
#         chunks_total=diff.chunks_total,
#         chunks_skipped=diff.chunks_skipped,
#         chunks_to_embed=len(diff.to_embed),
#         chunks_stale=len(diff.stale),
#         cost_saved_usd=round(diff.cost_saved_usd, 6),
#         embed_cost_usd_per_1m_tokens=cost_per_1m_tokens,
#     )


# __all__ = [
#     "DiffResult",
#     "compute_chunk_hashes",
#     "compute_diff",
#     "estimate_embed_cost_usd",
#     "log_diff_event",
# ]
