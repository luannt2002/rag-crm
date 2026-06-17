"""Application Context Base for superlative queries.

Detect superlative intent → parse + sort chunks → enrich state["context_base"].
LLM receives the enriched context + still composes answer per system_prompt.

KHÔNG compose answer text. KHÔNG decide which item to recommend.
LLM (per-bot persona) decides everything.

Domain-neutral: works for any service/finance/legal/edu corpus.

Multi-language: Vietnamese + English regex packs ship with the platform.
Bots whose ``language`` is outside ``SUPERLATIVE_SUPPORTED_LANGUAGES`` skip
detection entirely (fail-soft no-op) so a Mandarin/Japanese bot does not
mis-classify queries against Vietnamese phrases. Add a new language pack by
editing ``_SUPERLATIVE_PATTERNS_BY_LANG`` + ``SUPERLATIVE_SUPPORTED_LANGUAGES``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Literal

from ragbot.shared.constants import (
    DEFAULT_LANGUAGE,
    DEFAULT_SUPERLATIVE_PARSER_TIMEOUT_MS,
    DEFAULT_SUPERLATIVE_TOP_K,
    SUPERLATIVE_SUPPORTED_LANGUAGES,
)

IntentKey = Literal[
    "max_price",
    "min_price",
    "longest_duration",
    "shortest_duration",
    "max_discount",
    "min_discount",
    "max_bonus",
]


@dataclass
class RankedItem:
    """One parsed item from retrieved chunks. All numeric fields optional."""

    name: str
    price: int | None = None
    duration_minutes: int | None = None
    discount_percent: int | None = None
    bonus_count: int | None = None
    source_chunk_id: str = field(default="")


# Domain-neutral, LANGUAGE-GATED patterns.
# Add new families inside an existing language → orchestrator untouched
# (Open-Closed). Add a new language → new key here + new tag in
# ``SUPERLATIVE_SUPPORTED_LANGUAGES`` (constants.py).
#
# Language gate is required for multi-industry / multi-tenant safety: a bot
# whose ``bots.language`` is e.g. ``"zh"`` MUST NOT match Vietnamese regex
# (would mis-classify Mandarin queries as Vietnamese superlative intent).
_SUPERLATIVE_PATTERNS_BY_LANG: Final[dict[str, dict[str, list[str]]]] = {
    "vi": {
        "max_price": [
            r"cao nhất",
            r"đắt nhất",
            r"cao cấp nhất",
            r"premium nhất",
            r"vip nhất",
            r"luxury nhất",
            r"sang nhất",
            r"xịn nhất",
        ],
        "min_price": [
            r"rẻ nhất",
            r"thấp nhất",
            r"phải chăng nhất",
            r"giá tốt nhất",
            r"giá dễ chịu nhất",
            r"economic nhất",
        ],
        "longest_duration": [
            r"lâu nhất",
            r"dài nhất",
            r"thời gian dài nhất",
        ],
        "shortest_duration": [
            r"nhanh nhất",
            r"ngắn nhất",
            r"thời gian ít nhất",
        ],
        "max_discount": [
            r"giảm giá nhiều nhất",
            r"giảm nhiều nhất",
            r"khuyến mãi nhất",
            r"sale nhiều nhất",
            r"ưu đãi nhất",
        ],
        "min_discount": [
            r"giảm ít nhất",
        ],
        "max_bonus": [
            r"tặng nhiều nhất",
            r"nhiều quà nhất",
            r"bonus cao nhất",
        ],
    },
    "en": {
        "max_price": [
            r"\bmost expensive\b",
            r"\bhighest price\b",
            r"\bhighest[- ]priced\b",
            r"\bpremium(?:[- ]tier)?\b",
            r"\btop[- ]tier\b",
            r"\bluxury\b",
            r"\bvip\b",
        ],
        "min_price": [
            r"\bcheapest\b",
            r"\blowest price\b",
            r"\blowest[- ]priced\b",
            r"\bmost affordable\b",
            r"\bbest price\b",
            r"\beconomic(?:al)?\b",
        ],
        "longest_duration": [
            r"\blongest\b",
            r"\bmost time\b",
            r"\bgreatest duration\b",
        ],
        "shortest_duration": [
            r"\bshortest\b",
            r"\bquickest\b",
            r"\bfastest\b",
            r"\bleast time\b",
        ],
        "max_discount": [
            r"\bbiggest discount\b",
            r"\bhighest discount\b",
            r"\bbest deal\b",
            r"\bbiggest sale\b",
            r"\bmost off\b",
        ],
        "min_discount": [
            r"\bsmallest discount\b",
            r"\blowest discount\b",
        ],
        "max_bonus": [
            r"\bmost bonus(?:es)?\b",
            r"\bmost gifts?\b",
            r"\bhighest bonus\b",
        ],
    },
}


# Backward-compat export — module-level constant defaults to the language pack
# of ``DEFAULT_LANGUAGE`` so existing callers (tests, downstream tooling) keep
# working without specifying a language. Prefer instance-level
# ``SuperlativeContextEnricher(language=...)`` for new code.
SUPERLATIVE_PATTERNS: Final[dict[str, list[str]]] = _SUPERLATIVE_PATTERNS_BY_LANG.get(
    DEFAULT_LANGUAGE, {}
)


class SuperlativeContextEnricher:
    """Detect superlative intent and provide pre-ranked context.

    Contract:
    - detect_intent()  → IntentKey | None
    - parse_chunks()   → list[RankedItem]  (domain-neutral parser)
    - rank_for_intent() → list[RankedItem] (sorted, top-K)
    - enrich_state()   → dict  (adds context_base.superlative — KHÔNG sets answer)

    NO answer composition. LLM composes the final answer per bot system_prompt.

    Language gate: ``language`` selects the regex pack from
    ``_SUPERLATIVE_PATTERNS_BY_LANG``. Languages outside
    ``SUPERLATIVE_SUPPORTED_LANGUAGES`` get an empty pack → ``detect_intent``
    always returns ``None`` and ``enrich_state`` is a no-op (fail-soft).
    Default language follows ``DEFAULT_LANGUAGE`` for backward compat with
    existing callers that do not pass an explicit language.
    """

    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        self._language = language
        # Empty pack for unsupported languages — fail-soft, no raise.
        self._patterns: dict[str, list[str]] = (
            _SUPERLATIVE_PATTERNS_BY_LANG.get(language, {})
            if language in SUPERLATIVE_SUPPORTED_LANGUAGES
            else {}
        )

    @property
    def language(self) -> str:
        """Language tag this enricher was constructed with."""
        return self._language

    @property
    def patterns(self) -> dict[str, list[str]]:
        """Active regex pack — empty dict for unsupported languages."""
        return self._patterns

    def detect_intent(self, query: str) -> str | None:
        """Return intent key if superlative phrase detected, else None.

        Uses case-insensitive regex match. First match wins.
        Longer/more-specific patterns should appear earlier in the list.
        Returns ``None`` for unsupported languages (empty pack).
        """
        if not self._patterns:
            return None
        query_lower = query.lower()
        for intent, patterns in self._patterns.items():
            for p in patterns:
                if re.search(p, query_lower):
                    return intent
        return None

    def parse_chunks(self, chunks: list[str | dict]) -> list[RankedItem]:
        """Domain-neutral parser — extract price / duration / discount from text.

        Supports chunks as plain strings or dicts with "content"/"text" + "chunk_id".
        Deduplicates by item name (keeps highest price when name seen twice).

        Timeout note: ``DEFAULT_SUPERLATIVE_PARSER_TIMEOUT_MS`` is a soft
        reference — pure-Python regex is fast enough that no explicit timeout
        enforcement is needed for typical chunk sizes.
        """
        _ = DEFAULT_SUPERLATIVE_PARSER_TIMEOUT_MS  # reference for linting / future hard timeout

        items: dict[str, RankedItem] = {}

        for chunk in chunks:
            if isinstance(chunk, str):
                text = chunk
                cid = ""
            else:
                text = chunk.get("content") or chunk.get("text") or ""
                cid = str(chunk.get("chunk_id") or chunk.get("id") or "")

            # ---- Price pattern -----------------------------------------------
            # Matches: "Gói VIP: 1.500.000đ" / "Liệu trình A: 700,000 VND"
            for m in re.finditer(
                r"(?P<name>[^:\n,]{4,80}?):\s*"
                r"(?P<price>\d{1,3}(?:[.,]\d{3})+)\s*(?:đ|đồng|VND)?",
                text,
            ):
                name = m.group("name").strip()
                try:
                    price = int(m.group("price").replace(".", "").replace(",", ""))
                except (ValueError, TypeError):
                    continue
                if not name:
                    continue
                if name not in items or price > (items[name].price or 0):
                    items[name] = RankedItem(name=name, price=price, source_chunk_id=cid)

            # ---- Duration pattern -------------------------------------------
            # Matches: "Liệu trình A 90 phút" / "Gói B 2 giờ"
            for m in re.finditer(
                r"(?P<service>[^:\n,]{4,60}?)\s+(?P<duration>\d+)\s*(?:phút|giờ|buổi)",
                text,
            ):
                name = m.group("service").strip()
                if name in items and items[name].duration_minutes is None:
                    try:
                        items[name].duration_minutes = int(m.group("duration"))
                    except (ValueError, TypeError):
                        continue

            # ---- Discount pattern -------------------------------------------
            # Matches: "Giảm 30%" / "Sale 20%" / "Ưu đãi 15%"
            for m in re.finditer(
                r"(?:giảm|sale|ưu đãi|discount)\s*(?P<pct>\d+)\s*%",
                text,
                re.IGNORECASE,
            ):
                # Naive association: attach discount to last item seen in chunk.
                if items:
                    last_name = list(items.keys())[-1]
                    if items[last_name].discount_percent is None:
                        try:
                            items[last_name].discount_percent = int(m.group("pct"))
                        except (ValueError, TypeError):
                            continue

            # ---- Bonus pattern ----------------------------------------------
            # Matches: "Tặng 3 buổi" / "Bonus 2 lần"
            for m in re.finditer(
                r"(?:tặng|bonus)\s+(?P<cnt>\d+)\s*(?:buổi|lần|quà|voucher)?",
                text,
                re.IGNORECASE,
            ):
                if items:
                    last_name = list(items.keys())[-1]
                    if items[last_name].bonus_count is None:
                        try:
                            items[last_name].bonus_count = int(m.group("cnt"))
                        except (ValueError, TypeError):
                            continue

        return list(items.values())

    def rank_for_intent(
        self,
        items: list[RankedItem],
        intent: str,
    ) -> list[RankedItem]:
        """Sort items by the intent dimension and return top-K.

        K is taken from ``DEFAULT_SUPERLATIVE_TOP_K`` (constants.py).
        Items missing the target dimension are excluded from ranking.
        """
        k: int = DEFAULT_SUPERLATIVE_TOP_K

        if intent == "max_price":
            return sorted(
                [i for i in items if i.price is not None],
                key=lambda i: i.price,  # type: ignore[return-value]
                reverse=True,
            )[:k]

        if intent == "min_price":
            return sorted(
                [i for i in items if i.price is not None],
                key=lambda i: i.price,  # type: ignore[return-value]
            )[:k]

        if intent == "longest_duration":
            return sorted(
                [i for i in items if i.duration_minutes is not None],
                key=lambda i: i.duration_minutes,  # type: ignore[return-value]
                reverse=True,
            )[:k]

        if intent == "shortest_duration":
            return sorted(
                [i for i in items if i.duration_minutes is not None],
                key=lambda i: i.duration_minutes,  # type: ignore[return-value]
            )[:k]

        if intent == "max_discount":
            return sorted(
                [i for i in items if i.discount_percent is not None],
                key=lambda i: i.discount_percent,  # type: ignore[return-value]
                reverse=True,
            )[:k]

        if intent == "min_discount":
            return sorted(
                [i for i in items if i.discount_percent is not None],
                key=lambda i: i.discount_percent,  # type: ignore[return-value]
            )[:k]

        if intent == "max_bonus":
            return sorted(
                [i for i in items if i.bonus_count is not None],
                key=lambda i: i.bonus_count,  # type: ignore[return-value]
                reverse=True,
            )[:k]

        # Unknown intent — return all items up to K
        return items[:k]

    def enrich_state(
        self,
        state: dict,
        query: str,
        chunks: list,
    ) -> dict:
        """Add context_base.superlative to state.

        No-op if:
        - Enricher language not in ``SUPERLATIVE_SUPPORTED_LANGUAGES``
        - No superlative intent detected in query
        - No parseable items found in chunks
        - No items qualify for the detected intent dimension

        IMPORTANT: KHÔNG set state["answer"]. LLM composes the answer.
        Application layer = CONTEXT PROVIDER only.
        """
        intent = self.detect_intent(query)
        if intent is None:
            return state

        items = self.parse_chunks(chunks)
        if not items:
            return state

        ranked = self.rank_for_intent(items, intent)
        if not ranked:
            return state

        state.setdefault("context_base", {})["superlative"] = {
            "intent": intent,
            "language": self._language,
            "ranked_items": [
                {
                    "name": i.name,
                    "price": i.price,
                    "duration": i.duration_minutes,
                    "discount_pct": i.discount_percent,
                    "bonus": i.bonus_count,
                    "source_chunk_id": i.source_chunk_id,
                }
                for i in ranked
            ],
            "method": "application_layer_enrichment",
            "note": (
                "Items pre-sorted by superlative dimension. "
                "LLM compose answer per system_prompt persona."
            ),
        }
        return state


# ---------------------------------------------------------------------------
# Factory — caches one enricher per language. Bounded so an attacker-supplied
# language tag stream can't pin one instance per tag for the worker lifetime.
# ---------------------------------------------------------------------------
import functools as _functools

from ragbot.shared.constants import DEFAULT_VOCAB_FACTORY_CACHE_SIZE


@_functools.lru_cache(maxsize=DEFAULT_VOCAB_FACTORY_CACHE_SIZE)
def get_enricher_for_language(language: str = DEFAULT_LANGUAGE) -> SuperlativeContextEnricher:
    """Return a cached SuperlativeContextEnricher for the given language.

    Stateless cache (regex packs are immutable) — safe to reuse across
    requests. Unknown languages get an empty-pack instance (no-op enricher).
    """
    return SuperlativeContextEnricher(language=language)


__all__ = [
    "IntentKey",
    "RankedItem",
    "SUPERLATIVE_PATTERNS",
    "SuperlativeContextEnricher",
    "get_enricher_for_language",
]
