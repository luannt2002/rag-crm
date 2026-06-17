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
# Reason: proximity_cache infra never wired in bootstrap or graph.
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

# """LSHProximityCache — in-memory random-projection LSH bucketed cache.

# Reference adapter for the proximity-cache strategy. Buckets candidate cached
# queries by sign-pattern of random hyperplane projections (a standard cosine
# LSH scheme); on lookup, the incoming embedding is hashed into the same
# bucket-set and a linear cosine scan over the bucket members surfaces the best
# match above the configured threshold.

# Real Redis-backed wiring is deferred — this in-process variant is sufficient
# for unit tests and proves the Port's wire shape. Operators flip
# ``proximity_cache_provider="lsh"`` to opt in; ``"null"`` is the default.

# Implementation notes:
#     * Random hyperplanes seeded from ``hash`` of the embedding length so
#       buckets are deterministic given a dimension. No global random state.
#     * No eviction beyond TTL — bucket-level rebuild left to the production
#       Redis adapter. ``ttl_s == 0`` means do-not-store.
#     * No tenant scoping inside the adapter — the caller is expected to build
#       a per-tenant instance at DI time.
# """

# from __future__ import annotations

# import math
# import time
# from dataclasses import dataclass

# import structlog

# from ragbot.application.ports.proximity_cache_port import CacheHit
# from ragbot.shared.constants import (
#     DEFAULT_PROXIMITY_CACHE_LSH_BUCKETS,
#     DEFAULT_PROXIMITY_CACHE_SIMILARITY_THRESHOLD,
# )

# logger = structlog.get_logger(__name__)


# @dataclass(slots=True)
# class _Entry:
#     """One cached (embedding, answer) pair plus expiry timestamp."""

#     embedding: tuple[float, ...]
#     answer: str
#     original_query: str
#     expires_at: float


# def _normalise(vector: list[float]) -> list[float]:
    # Cosine LSH operates on unit-length vectors so the dot-product equals
    # the cosine similarity. Zero vectors are passed through unchanged
    # (any subsequent similarity will be 0.0 and miss the threshold).
#     norm = math.sqrt(sum(component * component for component in vector))
#     if norm == 0.0:
#         return list(vector)
#     return [component / norm for component in vector]


# def _cosine(a: list[float], b: tuple[float, ...] | list[float]) -> float:
    # Inputs are unit-normalised at insertion / lookup, so dot-product is the
    # cosine similarity. Length mismatch is a programmer error and surfaces
    # as ValueError rather than silent zero.
#     if len(a) != len(b):
#         raise ValueError(
#             f"embedding dimension mismatch: lookup={len(a)} cached={len(b)}",
#         )
#     return sum(x * y for x, y in zip(a, b, strict=False))


# class LSHProximityCache:
#     """Random-projection LSH proximity cache (in-memory)."""

#     def __init__(
#         self,
#         *,
#         similarity_threshold: float = DEFAULT_PROXIMITY_CACHE_SIMILARITY_THRESHOLD,
#         num_buckets: int = DEFAULT_PROXIMITY_CACHE_LSH_BUCKETS,
#     ) -> None:
        # ``num_buckets`` controls the bit-width of the bucket key; more bits
        # = sharper bucket separation but lower recall. Threshold gates the
        # post-bucket cosine scan so a misconfigured large bucket-width does
        # not silently degrade hit quality.
#         if num_buckets <= 0:
#             raise ValueError(
#                 f"num_buckets must be positive, got {num_buckets}",
#             )
#         if not -1.0 <= similarity_threshold <= 1.0:
#             raise ValueError(
#                 f"similarity_threshold must be in [-1.0, 1.0], "
#                 f"got {similarity_threshold}",
#             )
#         self._threshold = float(similarity_threshold)
#         self._num_buckets = int(num_buckets)
        # bucket_key -> list[_Entry]
#         self._buckets: dict[tuple[int, ...], list[_Entry]] = {}
        # Cached random hyperplanes per embedding dimension. Deterministic
        # seeding by dimension means two LSHProximityCache instances of the
        # same dimension hash identically — useful for tests, not load-bearing.
#         self._planes_by_dim: dict[int, list[list[float]]] = {}

#     @staticmethod
#     def get_provider_name() -> str:
#         return "lsh"

#     def _planes(self, dim: int) -> list[list[float]]:
#         cached = self._planes_by_dim.get(dim)
#         if cached is not None:
#             return cached
        # Pseudo-random hyperplanes: a Mulberry32-style integer hash seeded
        # with (dim, plane_index, component_index). Avoids any reliance on
        # ``random.seed`` which is process-global state.
#         planes: list[list[float]] = []
#         for plane_index in range(self._num_buckets):
#             row: list[float] = []
#             for component_index in range(dim):
#                 seed = (dim * 73856093) ^ (plane_index * 19349663) ^ (
#                     component_index * 83492791
#                 )
                # Map the 32-bit seed into [-1.0, 1.0).
#                 raw = (seed & 0xFFFFFFFF) / 0xFFFFFFFF
#                 row.append((raw * 2.0) - 1.0)
#             planes.append(row)
#         self._planes_by_dim[dim] = planes
#         return planes

#     def _bucket_key(self, normalised: list[float]) -> tuple[int, ...]:
#         planes = self._planes(len(normalised))
#         return tuple(
#             1 if sum(p * v for p, v in zip(plane, normalised, strict=False)) >= 0.0
#             else 0
#             for plane in planes
#         )

#     def _expire_in_place(self, bucket: list[_Entry], now: float) -> None:
        # Lazy eviction: drop expired entries the next time the bucket is
        # touched. Cheap because buckets are small in the in-memory variant.
#         if not bucket:
#             return
#         bucket[:] = [entry for entry in bucket if entry.expires_at > now]

#     def lookup(self, query_embedding: list[float]) -> CacheHit | None:
#         if not query_embedding:
#             return None
#         normalised = _normalise(query_embedding)
#         key = self._bucket_key(normalised)
#         bucket = self._buckets.get(key)
#         if not bucket:
#             return None
#         now = time.monotonic()
#         self._expire_in_place(bucket, now)
#         best: _Entry | None = None
#         best_score = -1.0
#         for entry in bucket:
#             score = _cosine(normalised, entry.embedding)
#             if score > best_score:
#                 best_score = score
#                 best = entry
#         if best is None or best_score < self._threshold:
#             logger.debug(
#                 "lsh_proximity_cache_miss",
#                 bucket_size=len(bucket),
#                 best_score=best_score,
#                 threshold=self._threshold,
#             )
#             return None
#         logger.debug(
#             "lsh_proximity_cache_hit",
#             similarity=best_score,
#             threshold=self._threshold,
#         )
#         return CacheHit(
#             answer=best.answer,
#             similarity=best_score,
#             original_query=best.original_query,
#         )

#     def store(self, query_embedding: list[float], answer: str, ttl_s: int) -> None:
        # ``ttl_s == 0`` is the explicit do-not-store sentinel (mirrors the
        # NullProximityCache behaviour). Negative TTLs are also rejected as
        # a programmer error to avoid storing already-expired entries.
#         if ttl_s <= 0 or not query_embedding:
#             return
#         normalised = _normalise(query_embedding)
#         key = self._bucket_key(normalised)
#         entry = _Entry(
#             embedding=tuple(normalised),
#             answer=answer,
            # Without the original query string the adapter cannot satisfy
            # CacheHit.original_query — this is a scaffolding limitation and
            # will tighten when the Redis variant ships with a richer schema.
#             original_query="",
#             expires_at=time.monotonic() + float(ttl_s),
#         )
#         self._buckets.setdefault(key, []).append(entry)


# __all__ = ["LSHProximityCache"]
