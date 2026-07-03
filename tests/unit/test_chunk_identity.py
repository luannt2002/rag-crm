"""Unit tests — M21 deterministic chunk UUID5 helper (Agent A4).

Validates the idempotent chunk-id derivation introduced in
``ragbot.shared.chunk_identity``. Key invariants:

* **Determinism**: same (bot_id, doc_id, content) → same UUID always.
* **Bot scoping**: two tenants ingesting identical content get distinct
  UUIDs (no cross-tenant collision).
* **Document scoping**: two documents with identical content also get
  distinct UUIDs.
* **Content strip**: trailing whitespace doesn't break idempotency
  (parser version drift tolerance).
* **Input validation**: missing bot_id / document_id raises early.

All assertions are real value/behavior checks per CLAUDE.md test rules.
"""

from __future__ import annotations

import uuid

import pytest

from ragbot.shared.chunk_identity import deterministic_chunk_id


def test_deterministic_chunk_id_idempotent():
    """Same inputs → same UUID. The fundamental idempotency contract."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    content = "the quick brown fox"
    u1 = deterministic_chunk_id(bot_id, doc_id, content)
    u2 = deterministic_chunk_id(bot_id, doc_id, content)
    assert u1 == u2
    assert isinstance(u1, uuid.UUID)
    assert u1.version == 5  # UUID5 derived


def test_deterministic_chunk_id_distinct_bot():
    """Two tenants ingesting the same public document MUST get distinct
    chunk IDs — no cross-tenant namespace collision."""
    doc_id = uuid.uuid4()
    content = "shared public PDF excerpt"
    bot_a = uuid.uuid4()
    bot_b = uuid.uuid4()
    u_a = deterministic_chunk_id(bot_a, doc_id, content)
    u_b = deterministic_chunk_id(bot_b, doc_id, content)
    assert u_a != u_b


def test_deterministic_chunk_id_distinct_document():
    """Two documents inside the same bot get distinct chunk IDs even
    when content is byte-identical (rare but legal — e.g. two scans of
    the same form)."""
    bot_id = uuid.uuid4()
    content = "identical content"
    doc_a = uuid.uuid4()
    doc_b = uuid.uuid4()
    u_a = deterministic_chunk_id(bot_id, doc_a, content)
    u_b = deterministic_chunk_id(bot_id, doc_b, content)
    assert u_a != u_b


def test_deterministic_chunk_id_distinct_content():
    """Trivial sanity: different content → different UUID."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    u_a = deterministic_chunk_id(bot_id, doc_id, "content A")
    u_b = deterministic_chunk_id(bot_id, doc_id, "content B")
    assert u_a != u_b


def test_deterministic_chunk_id_index_disambiguates_identical_content():
    """I15: two byte-identical chunks at different positions in one doc must
    get DISTINCT UUIDs — otherwise the PK UPSERT silently overwrites the
    first with the second (data loss on any doc that repeats a line/row)."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    same = "Có"  # a repeated table cell / boilerplate line
    u0 = deterministic_chunk_id(bot_id, doc_id, same, chunk_index=0)
    u1 = deterministic_chunk_id(bot_id, doc_id, same, chunk_index=1)
    assert u0 != u1, "identical content at different index must not collide"


def test_deterministic_chunk_id_same_index_same_content_idempotent():
    """Idempotency preserved: same (doc, index, content) → same UUID, so a
    normal re-ingest (same chunker, same order) UPSERTs in place."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    u_a = deterministic_chunk_id(bot_id, doc_id, "row text", chunk_index=3)
    u_b = deterministic_chunk_id(bot_id, doc_id, "row text", chunk_index=3)
    assert u_a == u_b


def test_deterministic_chunk_id_index_none_is_legacy_seed():
    """chunk_index=None keeps the legacy position-independent seed
    (backward compatibility for callers that guarantee unique content)."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    legacy = deterministic_chunk_id(bot_id, doc_id, "x")
    explicit_none = deterministic_chunk_id(bot_id, doc_id, "x", chunk_index=None)
    assert legacy == explicit_none
    # And it differs from the indexed seed (index 0 is NOT the same as absent).
    assert legacy != deterministic_chunk_id(bot_id, doc_id, "x", chunk_index=0)


def test_deterministic_chunk_id_strip_tolerance():
    """Leading/trailing whitespace is stripped before hashing — parser
    version drift on trailing newlines must NOT shuffle UUIDs."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    u_a = deterministic_chunk_id(bot_id, doc_id, "core text")
    u_b = deterministic_chunk_id(bot_id, doc_id, "\n  core text  \n")
    assert u_a == u_b


def test_deterministic_chunk_id_case_sensitive():
    """Content is NOT lowercased before hashing — case carries meaning
    in citation text. ``Hello`` and ``hello`` must hash differently."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    u_a = deterministic_chunk_id(bot_id, doc_id, "Hello")
    u_b = deterministic_chunk_id(bot_id, doc_id, "hello")
    assert u_a != u_b


def test_deterministic_chunk_id_str_uuid_equivalent():
    """Passing ``str(uuid)`` and the ``UUID`` object produce the same
    output — important for ingest call sites that have either form."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    content = "X"
    u_obj = deterministic_chunk_id(bot_id, doc_id, content)
    u_str = deterministic_chunk_id(str(bot_id), str(doc_id), content)
    assert u_obj == u_str


def test_deterministic_chunk_id_rejects_empty_bot():
    """Empty bot_id is a programming error — fail loud."""
    with pytest.raises(ValueError):
        deterministic_chunk_id("", uuid.uuid4(), "x")


def test_deterministic_chunk_id_rejects_empty_doc():
    """Empty document_id is a programming error — fail loud."""
    with pytest.raises(ValueError):
        deterministic_chunk_id(uuid.uuid4(), "", "x")


def test_deterministic_chunk_id_empty_content_allowed():
    """Empty content is legal — corresponds to an empty chunk row
    (rare but not erroneous). UUID is still deterministic."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    u_a = deterministic_chunk_id(bot_id, doc_id, "")
    u_b = deterministic_chunk_id(bot_id, doc_id, "   ")  # strips to ""
    assert u_a == u_b


def test_deterministic_chunk_id_internal_whitespace_matters():
    """Only boundary stripping is forgiven — real content drift (an
    extra space between words) MUST surface as a different UUID so the
    re-ingest path picks it up rather than silently treating it as
    identical."""
    bot_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    one_space = deterministic_chunk_id(bot_id, doc_id, "hello world")
    two_space = deterministic_chunk_id(bot_id, doc_id, "hello  world")
    assert one_space != two_space


def test_deterministic_chunk_id_string_bot_slug_deterministic():
    """``record_bot_id`` slot accepts non-UUID strings too (some call
    sites carry slugs, not UUIDs). Same slug + inputs → same UUID;
    distinct slug → distinct UUID."""
    doc_id = uuid.uuid4()
    content = "abc"
    first = deterministic_chunk_id("bot-slug-alpha", doc_id, content)
    second = deterministic_chunk_id("bot-slug-alpha", doc_id, content)
    third = deterministic_chunk_id("bot-slug-beta", doc_id, content)
    assert first == second
    assert first != third
