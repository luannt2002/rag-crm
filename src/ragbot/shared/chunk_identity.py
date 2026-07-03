"""M21 — Deterministic chunk UUID5 helper.

Inspired by RAG-Anything's content-hash idempotent chunk ID strategy
(``raganything/operate.py``, HKUDS 2025) and LlamaIndex's
``DocstoreStrategy.UPSERT`` pattern. The motivation is simple: when a
document is re-ingested with byte-identical content, the ingest
pipeline should be **idempotent** — emit the same chunk row IDs so the
DB UPSERT path replaces in place instead of duplicating rows + bumping
downstream caches.

Why UUID5, not SHA256?
----------------------
The ``document_chunks.id`` column is ``UUID`` (PG ``uuid`` type) per the
existing schema. UUID5 is a deterministic hash projected into the UUID
namespace via SHA-1 internally — so we get content-derived IDs without
changing the column type or migration cost. Collision probability is
identical to SHA-1 for distinct inputs, which is negligible at our
scale (single-tenant doc counts are O(10⁵)).

Why include ``record_bot_id`` in the seed?
------------------------------------------
Two tenants can ingest the exact same public document (e.g. a shared
PDF datasheet). Without the bot scope in the seed they would collide
on chunk IDs and write into each other's rows. The ``record_bot_id``
prefix isolates the namespace per bot, matching the 4-key identity
contract — and stays consistent under workspace lifts because
``record_bot_id`` is already workspace-scoped at the registry layer.

When is it used?
----------------
Per-bot opt-in via ``bots.plan_limits.chunk_hash_id_enabled``. Default
OFF preserves the legacy ``uuid.uuid4()`` path so existing bots keep
their existing chunk IDs. Bot owners flip to True when they want
idempotent re-ingest semantics — at which point the next ingest will
write fresh deterministic UUIDs (UUID4 rows from prior ingests are not
migrated; they age out via normal re-ingest cycles).
"""

from __future__ import annotations

import uuid


# Stable namespace for chunk-id derivation. Using ``NAMESPACE_OID``
# rather than minting a project-specific UUID4 keeps the helper
# zero-state — any caller can reproduce the same UUID from the same
# inputs without consulting a registry. The actual entropy comes from
# the bot-id + document-id + content tuple, not the namespace.
_CHUNK_NAMESPACE: uuid.UUID = uuid.NAMESPACE_OID


def deterministic_chunk_id(
    record_bot_id: str | uuid.UUID,
    document_id: str | uuid.UUID,
    content: str,
    *,
    chunk_index: int | None = None,
) -> uuid.UUID:
    """Return a deterministic UUID5 for the (bot, doc, [index,] content) tuple.

    Args:
        record_bot_id: Internal UUID of the bot — the tenant-scope
            namespace. ``str`` and ``UUID`` are both accepted; both
            collapse to the canonical hex form before hashing so a
            caller that passes ``UUID`` and a caller that passes
            ``str(uuid)`` get the same result.
        document_id: The document UUID this chunk belongs to.
        chunk_index: Position of the chunk within the document. When
            supplied it is folded into the seed so two chunks with
            byte-identical content at different positions get DISTINCT
            IDs — without it they collapse to the same UUID5 and the PK
            UPSERT silently overwrites the first with the second (data
            loss on any doc that repeats a line/row). ``None`` keeps the
            legacy position-independent seed for callers that guarantee
            unique content. Idempotency holds for the normal re-ingest
            (same chunker → same order → same index per content); only a
            chunker-strategy change reshuffles indices, which re-ingests
            the doc anyway.
        content: The chunk text content. Stripped of leading/trailing
            whitespace before hashing so trivial whitespace edits at
            the chunk boundary (e.g. trailing newline drift between
            parser versions) don't break idempotency. **Not**
            lowercased — case carries meaning in citation text.

    Returns:
        A deterministic ``uuid.UUID`` (version 5). Same inputs always
        yield the same UUID. Tested in
        ``tests/unit/test_chunk_identity.py``.

    Note on content normalization:
        The strip-only normalisation is intentional. Aggressive
        normalisation (collapse-whitespace, NFC-normalise) would
        improve idempotency under cosmetic edits but at the cost of
        masking real content drift — which we want surfaced as a
        re-ingest, not silently identical IDs.
    """
    if not record_bot_id:
        raise ValueError(
            "deterministic_chunk_id requires record_bot_id (got empty)"
        )
    if not document_id:
        raise ValueError(
            "deterministic_chunk_id requires document_id (got empty)"
        )
    # Canonical form: ``str()`` works for both UUID and string inputs
    # without an explicit isinstance branch — UUID.__str__ already
    # returns the lower-hex hyphenated form.
    if chunk_index is None:
        seed = f"{record_bot_id!s}|{document_id!s}|{content.strip()}"
    else:
        seed = f"{record_bot_id!s}|{document_id!s}|{chunk_index}|{content.strip()}"
    return uuid.uuid5(_CHUNK_NAMESPACE, seed)


def time_ordered_chunk_id() -> uuid.UUID:
    """Return a time-ordered UUIDv7 as a stdlib ``uuid.UUID``.

    Replaces random ``uuid.uuid4()`` for the non-deterministic chunk-id
    path. UUIDv7 embeds a Unix-millisecond timestamp in its high bits, so
    successive ids are monotonically increasing. On the ``document_chunks.id``
    primary-key B-tree this gives sequential insert locality (new rows append
    to the right-most leaf) instead of v4's random scatter — fewer page
    splits, better cache behaviour, faster bulk INSERT on large ingests
    (RFC 9562 §5.7). Same 122-bit randomness tail → uniqueness unchanged.

    stdlib ``uuid`` has no ``uuid7`` (3.12); we bridge ``uuid_utils.uuid7()``
    into a stdlib ``uuid.UUID`` via raw bytes so SQLAlchemy/asyncpg bind it
    as the native PG ``uuid`` type with no adapter change.
    """
    import uuid_utils  # noqa: PLC0415 — optional dep, imported at call site
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)


__all__ = ["time_ordered_chunk_id", "deterministic_chunk_id"]
