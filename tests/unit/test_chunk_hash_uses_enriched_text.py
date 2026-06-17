"""Hash-based incremental re-index detector must fingerprint the text that
actually gets embedded.

The skip-re-embed decision keys off ``content_hash``: same hash → assume
the stored embedding is still valid → skip the embedder call. If the hash
fingerprints the *raw* chunk while the embedding is computed on the
*enriched* chunk, then a re-ingest with unchanged raw text but a changed
enrichment context (e.g. updated document summary feeding contextual
retrieval prefixes) silently keeps the stale embedding and poisons
retrieval.

These tests pin the contract: hashes must come from the same list that
feeds the embedder.
"""

from __future__ import annotations

import inspect
import re

from ragbot.application.services.document_service import DocumentService


def test_compute_chunk_hashes_helper_exists_and_is_deterministic() -> None:
    helper = getattr(DocumentService, "_compute_chunk_hashes", None)
    assert callable(helper), (
        "DocumentService must expose a _compute_chunk_hashes helper so the "
        "incremental-re-index decision is testable in isolation"
    )

    out = helper(["alpha", "beta"])
    assert isinstance(out, list)
    assert len(out) == 2
    assert all(isinstance(h, str) and len(h) == 64 for h in out)
    # Determinism — same input twice → identical output.
    assert helper(["alpha", "beta"]) == out


def test_compute_chunk_hashes_distinguishes_enrichment_variants() -> None:
    helper = DocumentService._compute_chunk_hashes
    raw = ["product X price"]
    enriched_a = ["[Doc summary v1]\nproduct X price"]
    enriched_b = ["[Doc summary v2 — updated]\nproduct X price"]

    h_raw = helper(raw)
    h_a = helper(enriched_a)
    h_b = helper(enriched_b)

    assert h_raw != h_a, "raw and enriched-A must hash differently"
    assert h_a != h_b, "two enrichment variants of the same raw text must hash differently"


def test_ingest_path_hashes_enriched_chunks_not_raw() -> None:
    """The hash-compute call site inside the ingest path must read from
    ``enriched_chunks``, not the raw ``chunks`` list — otherwise a
    re-ingest with new enrichment context skips re-embed and stores a
    stale vector under a misleading ``content_hash``.
    """
    # ingest() lives in the _IngestMixin (document_service package split) — scan
    # both the class skeleton and the ingest mixin for the call site.
    from ragbot.application.services.document_service.ingest_core import _IngestMixin
    src = inspect.getsource(DocumentService) + inspect.getsource(_IngestMixin)
    # Match invocations only — exclude the helper definition itself.
    helper_call_pat = re.compile(
        r"(?:self|cls|DocumentService)\._compute_chunk_hashes\(\s*([A-Za-z_][A-Za-z0-9_]*)",
    )
    matches = helper_call_pat.findall(src)
    assert matches, (
        "ingest path must call DocumentService._compute_chunk_hashes(...) — "
        "the inline sha256 loop is the cache-poisoning source"
    )
    assert all(arg == "enriched_chunks" for arg in matches), (
        f"_compute_chunk_hashes must be invoked with enriched_chunks; "
        f"saw {matches}"
    )
