"""ViUnderthesseaExtractor — Vietnamese NER via underthesea POS+NER hybrid.

Strategy for Vietnamese-language queries. Combines three signal sources
(in priority order, deduped + ranked):

1. **Named entities (NER tag)** — underthesea NER returns ``B-PER``,
   ``B-LOC``, ``B-ORG``, ``B-MISC`` BIO labels per token. Adjacent
   ``B-X`` + ``I-X`` tokens are stitched into a single entity. These
   are highest priority because they're the most specific signal in
   typical user queries (proper nouns, places, organisations).

2. **Proper-noun POS tag (``Np``)** — single tokens tagged ``Np`` that
   the NER pass missed (e.g. brand-like ``ABC123`` in a noisy query).
   Underthesea's NER occasionally misses these so the POS pass is a
   safety net.

3. **Numeric tokens (``M`` POS)** — phone numbers, dates, IDs. These
   are critical BM25-anchor tokens in factoid queries (``"phone 0901234567"``,
   ``"đơn hàng 12345"``) and BM25 matches them verbatim with 100% recall.

Vertical-agnostic: NO industry / brand / product literals. The same
strategy serves spa / finance / healthcare / education tenants
uniformly. Domain-specific synonyms still belong in
``bots.custom_vocabulary`` per-bot.

Fail-soft: if underthesea is not installed OR raises, returns ``[]``
so the caller falls back to plain paraphrase variants without an
exception bubbling up.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable

import structlog

from ragbot.shared.constants import VI_DOMAIN_LANGUAGES

logger = structlog.get_logger(__name__)


# Module-level lazy-load — share underthesea backend across all
# ViUnderthesseaExtractor instances within a process. Same pattern as
# ``shared.vi_tokenizer`` so we don't pay the import cost twice.
_lock = threading.Lock()
_ner_fn: Any = None
_pos_fn: Any = None
_initialised = False


def _init_backend() -> None:
    global _ner_fn, _pos_fn, _initialised
    if _initialised:
        return
    with _lock:
        if _initialised:
            return
        try:
            from underthesea import ner as _ner  # type: ignore[import-untyped]
            from underthesea import pos_tag as _pos
            _ner_fn = _ner
            _pos_fn = _pos
            logger.info("vi_entity_extractor_loaded", backend="underthesea")
        except ImportError:
            _ner_fn = None
            _pos_fn = None
            logger.info(
                "vi_entity_extractor_load_failed",
                reason="underthesea_not_installed",
            )
        _initialised = True


# BIO-prefix labels we treat as "entity start". Anything in
# {PER, LOC, ORG, MISC} is a useful BM25 anchor.
_NER_ENTITY_TYPES: frozenset[str] = frozenset({"PER", "LOC", "ORG", "MISC"})

# POS tags worth promoting on the safety-net pass.
_POS_PROPER_NOUN: str = "Np"
_POS_NUMERIC: str = "M"


def _stitch_ner_entities(tagged: Iterable[tuple]) -> list[str]:
    """Stitch BIO-tagged tokens into entity strings, in source order.

    Underthesea NER yields tuples ``(word, pos, chunk, ner_tag)`` where
    ``ner_tag`` follows the BIO scheme (``B-PER``, ``I-PER``, ``O``, ...).
    We collapse contiguous ``B-X`` + zero-or-more ``I-X`` runs into a
    single space-joined entity. ``B-X`` immediately followed by ``B-Y``
    starts a new entity (no stitching across types).
    """
    entities: list[str] = []
    current: list[str] = []
    current_type: str | None = None
    for tup in tagged:
        # underthesea returns 4-tuples; tolerate odd lengths defensively.
        if not isinstance(tup, tuple) or len(tup) < 4:
            continue
        word = str(tup[0]) if tup[0] else ""
        ner_tag = str(tup[3]) if tup[3] else "O"
        if not word.strip():
            continue
        if ner_tag == "O" or "-" not in ner_tag:
            if current:
                entities.append(" ".join(current))
                current = []
                current_type = None
            continue
        prefix, _, etype = ner_tag.partition("-")
        if etype not in _NER_ENTITY_TYPES:
            if current:
                entities.append(" ".join(current))
                current = []
                current_type = None
            continue
        if prefix == "B" or current_type != etype:
            if current:
                entities.append(" ".join(current))
            current = [word]
            current_type = etype
        else:  # prefix == "I"
            current.append(word)
    if current:
        entities.append(" ".join(current))
    return entities


def _collect_pos_safety_net(
    tagged: Iterable[tuple],
    *,
    skip: set[str],
) -> list[str]:
    """Pick ``Np`` (proper noun) + ``M`` (numeric) tokens missed by NER.

    Iterates over POS-tagged tuples ``(word, pos)`` (or longer tuples
    where pos is at index 1). Tokens whose case-folded form already
    appears in ``skip`` (case-folded match) are dropped — avoids
    returning the same entity twice via NER+POS double-counting.
    """
    out: list[str] = []
    for tup in tagged:
        if not isinstance(tup, tuple) or len(tup) < 2:
            continue
        word = str(tup[0]) if tup[0] else ""
        pos = str(tup[1]) if tup[1] else ""
        if not word.strip():
            continue
        if pos not in (_POS_PROPER_NOUN, _POS_NUMERIC):
            continue
        if word.casefold() in skip:
            continue
        out.append(word)
    return out


def _dedup_preserve_order(items: Iterable[str]) -> list[str]:
    """Case-fold dedup preserving the first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        if not raw:
            continue
        norm = " ".join(raw.split()).strip()
        key = norm.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


class ViUnderthesseaExtractor:
    """Vietnamese entity extractor backed by underthesea NER + POS tag.

    Returns NER entities first (most specific), then proper-noun /
    numeric POS-tag safety net. Both stages are deduped (case-folded).

    Language gate: refuses to run on non-VN languages (returns ``[]``)
    so a misconfigured bot ``language="en"`` cannot trigger the heavy
    underthesea backend on English text.
    """

    def __init__(self, **_: object) -> None:
        # Lazy backend init — actual import happens on first call.
        return

    @staticmethod
    def get_provider_name() -> str:
        return "vi_underthesea"

    async def extract(self, query: str, *, language: str) -> list[str]:
        if not query or not query.strip():
            return []
        # Language gate — multi-tenant safety. Non-VN bot must not get
        # VN-specific NER applied to (potentially) EN/ZH/JP queries.
        if language not in VI_DOMAIN_LANGUAGES:
            return []

        _init_backend()
        if _ner_fn is None or _pos_fn is None:
            logger.debug(
                "vi_entity_extractor_no_backend",
                query_chars=len(query),
            )
            return []

        try:
            ner_tagged = _ner_fn(query)
        except Exception as exc:  # noqa: BLE001 — fallback graceful
            logger.warning(
                "vi_entity_extractor_ner_failed",
                error=str(exc),
                query_preview=query[:80],
            )
            return []

        ner_entities = _stitch_ner_entities(ner_tagged or [])
        # Build skip-set of case-folded NER hits so the POS safety net
        # does not re-emit the same proper noun.
        skip = {e.casefold() for e in ner_entities}
        # Walk every word that participated in a NER stitched entity
        # too — a multi-word entity ``Hà Nội`` should suppress the
        # solo ``Hà Nội`` Np tag from re-appearing.
        for ent in ner_entities:
            for tok in ent.split():
                skip.add(tok.casefold())

        try:
            pos_tagged = _pos_fn(query)
        except Exception as exc:  # noqa: BLE001 — fallback graceful
            logger.warning(
                "vi_entity_extractor_pos_failed",
                error=str(exc),
                query_preview=query[:80],
            )
            pos_tagged = []

        pos_safety = _collect_pos_safety_net(pos_tagged or [], skip=skip)

        merged = _dedup_preserve_order([*ner_entities, *pos_safety])
        return merged


__all__ = ["ViUnderthesseaExtractor"]
