"""Generic vocabulary expander — universal abbreviations + common synonyms.

DOMAIN-NEUTRAL: works for any tenant (spa/finance/edu/legal/e-commerce).
Tenant-specific vocab → bots.custom_vocabulary JSONB column (per-tenant override).

Application layer enrich state["context_base"]["vocabulary"] with expansion
context. LLM + system_prompt decide the final answer.
KHÔNG modify state["answer"].

Architecture:
    Layer 1 — Retrieval (vector search + BM25 RRF)
    Layer 2 — Application Context Base (THIS MODULE)
               Generic vocab expand: "ko" → "không"
               Per-bot custom_vocabulary override
               Returns ENRICHED context_base (KHÔNG text answer)
    Layer 3 — Generate node (LLM + chunks + context_base)
    Layer 4 — System Prompt (per-bot, OWNS answer)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import structlog

from ragbot.shared.constants import (
    DEFAULT_GENERIC_VOCAB_ENABLED,
    DEFAULT_GENERIC_VOCAB_MAX_EXPANSIONS_PER_MATCH,
    DEFAULT_GENERIC_VOCAB_MAX_MATCHES_PER_QUERY,
    DEFAULT_LANGUAGE,
    DEFAULT_VN_NEGATION_TOKENS,
    DEFAULT_VOCAB_NEGATION_LOOKBACK_TOKENS,
    DEFAULT_VOCAB_NGRAM_MAX_N,
    VI_DOMAIN_LANGUAGES,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Generic vocabulary — ~100 entries covering VN+EN mixing patterns.
# Categories: abbreviations, English mix, comparison/intent, quality,
# time, status, question words.
#
# DOMAIN-NEUTRAL: NO spa/finance/edu/legal/brand-specific terms.
# Domain vocab → bots.custom_vocabulary JSONB (per-bot override).
# ---------------------------------------------------------------------------
GENERIC_VOCABULARY: Final[dict[str, list[str]]] = {
    # ─── Viết tắt VN phổ biến (abbreviations) ───
    "ko": ["không"],
    "k": ["không"],
    "kh": ["không"],
    "đc": ["được"],
    "dc": ["được"],
    "vs": ["với", "versus"],
    "với": ["vs"],
    "ntn": ["như thế nào"],
    "nt": ["như thế"],
    "kp": ["không phải"],
    "j": ["gì"],
    "z": ["vậy"],
    "tks": ["cảm ơn", "thanks"],
    "thx": ["cảm ơn"],
    "ok": ["được", "đồng ý", "ổn"],
    "ad": ["admin"],
    "ib": ["nhắn tin", "message"],
    "rep": ["trả lời", "phản hồi"],
    "tn": ["tin nhắn"],
    "sđt": ["số điện thoại"],
    "đt": ["điện thoại"],
    "cn": ["chi nhánh"],
    "hd": ["hợp đồng"],
    "tk": ["tài khoản"],
    "ck": ["chuyển khoản"],
    "vn": ["việt nam"],
    "tphcm": ["thành phố hồ chí minh", "sài gòn"],
    "hn": ["hà nội"],
    "nv": ["nhân viên"],
    "kh": ["khách hàng"],
    "sp": ["sản phẩm"],
    "dv": ["dịch vụ"],
    "tt": ["thanh toán"],
    "gd": ["giao dịch"],

    # ─── English phổ biến trộn vào tiếng Việt (Viet-English mix) ───
    "price": ["giá", "chi phí"],
    "cost": ["giá", "chi phí"],
    "fee": ["phí"],
    "service": ["dịch vụ"],
    "product": ["sản phẩm"],
    "package": ["gói"],
    "combo": ["gói"],
    "promo": ["khuyến mãi", "ưu đãi", "sale"],
    "promotion": ["khuyến mãi", "ưu đãi"],
    "sale": ["khuyến mãi", "giảm giá"],
    "discount": ["giảm giá"],
    "voucher": ["phiếu giảm giá"],
    "coupon": ["mã giảm"],
    "info": ["thông tin"],
    "contact": ["liên hệ"],
    "hotline": ["số điện thoại", "tổng đài"],
    "address": ["địa chỉ"],
    "open": ["mở cửa", "hoạt động"],
    "close": ["đóng cửa"],
    "schedule": ["lịch", "thời gian"],
    "booking": ["đặt lịch"],
    "appointment": ["hẹn", "lịch hẹn"],
    "review": ["đánh giá"],
    "feedback": ["phản hồi"],
    "demo": ["thử"],
    "warranty": ["bảo hành"],
    "available": ["có sẵn", "có"],
    "cancel": ["huỷ"],
    "refund": ["hoàn tiền"],
    "policy": ["chính sách"],

    # ─── Comparison + intent markers (generic superlatives) ───
    "nhất": ["most"],
    "hơn": ["more than", "above"],
    "ít hơn": ["less than", "under"],
    "trên": ["above", "over"],
    "dưới": ["below", "under"],
    "tối đa": ["max", "maximum", "cao nhất"],
    "tối thiểu": ["min", "minimum", "thấp nhất"],
    "ít": ["few", "small"],
    "nhiều": ["many", "much"],
    "khác": ["different", "versus"],
    "giống": ["same", "similar"],
    "tương tự": ["similar"],

    # ─── Generic quality markers ───
    "tốt": ["good", "well"],
    "xấu": ["bad"],
    "lớn": ["big", "large"],
    "nhỏ": ["small", "tiny"],
    "mới": ["new", "fresh"],
    "cũ": ["old", "outdated"],

    # ─── Generic time markers ───
    "lâu": ["long", "lengthy"],
    "nhanh": ["fast", "quick"],
    "ngắn": ["short", "brief"],
    "dài": ["long"],
    "hôm nay": ["today"],
    "hôm qua": ["yesterday"],
    "ngày mai": ["tomorrow"],
    "tuần": ["week"],
    "tháng": ["month"],
    "năm": ["year"],

    # ─── Generic status ───
    "có": ["available", "yes"],
    "không": ["unavailable", "no"],
    "đang": ["currently", "now"],
    "sắp": ["soon", "upcoming"],
    "vừa": ["just", "recent"],
    "đã": ["already"],
    "sẽ": ["will"],

    # ─── Generic question words ───
    "gì": ["what"],
    "nào": ["which"],
    "đâu": ["where"],
    "khi nào": ["when"],
    "tại sao": ["why"],
    "vì sao": ["why"],
    "thế nào": ["how"],
    "bao nhiêu": ["how many", "how much"],
    "mấy": ["how many"],
    "ai": ["who"],
    "ở đâu": ["where", "address"],
}


@dataclass
class VocabularyMatch:
    """One token (or n-gram phrase) matched in GENERIC_VOCABULARY or bot-custom vocab.

    ``n`` is 1 for single-token (unigram) matches and 2/3 for n-gram phrases.
    ``original_token`` carries the surface phrase verbatim (whitespace-joined for
    n-grams) so downstream consumers can reason uniformly across arities.
    """

    original_token: str
    position: int          # token index in the whitespace-split query (start position)
    expansions: list[str]
    source: str            # "generic" | "bot_custom"
    n: int = 1             # arity: 1 = unigram, 2 = bigram, 3 = trigram


class VocabularyExpander:
    """Detect abbreviations + generic synonyms → enrich context_base.

    KHÔNG modify query. KHÔNG compose answer.
    Provide structured hint for LLM in [CONTEXT BASE] block so the model
    can reason about abbreviated / mixed-language queries.

    Per-bot custom vocab (from bots.custom_vocabulary JSONB) is merged at
    call time; it takes priority over the generic dict when keys conflict.

    Language gate: the built-in ``GENERIC_VOCABULARY`` is Vietnamese-centric
    (teencode + VN↔EN mixing patterns). Bots whose ``language`` is outside
    ``VI_DOMAIN_LANGUAGES`` skip the built-in dict entirely so an EN/ZH/JP
    bot does not get spurious matches on shared ASCII tokens (e.g.
    ``"k"`` → ``"không"``). Per-bot ``custom_vocabulary`` is still applied
    regardless of language so bot owners can supply their own dict.
    """

    def __init__(
        self,
        base_vocab: dict[str, list[str]] | None = None,
        max_matches: int = DEFAULT_GENERIC_VOCAB_MAX_MATCHES_PER_QUERY,
        max_expansions: int = DEFAULT_GENERIC_VOCAB_MAX_EXPANSIONS_PER_MATCH,
        enabled: bool = DEFAULT_GENERIC_VOCAB_ENABLED,
        language: str = DEFAULT_LANGUAGE,
        ngram_max_n: int = DEFAULT_VOCAB_NGRAM_MAX_N,
        negation_tokens: frozenset[str] = DEFAULT_VN_NEGATION_TOKENS,
        negation_lookback: int = DEFAULT_VOCAB_NEGATION_LOOKBACK_TOKENS,
    ) -> None:
        # When ``base_vocab`` is None we use the platform default. Apply the
        # language gate: non-VN languages skip the built-in dict (treated as
        # empty). Caller can still provide an explicit ``base_vocab`` (e.g.
        # injected per-bot from system_config) — that bypasses the gate.
        if base_vocab is not None:
            self._base = base_vocab
        elif language in VI_DOMAIN_LANGUAGES:
            self._base = GENERIC_VOCABULARY
        else:
            self._base = {}
        self._language = language
        self._max_matches = max_matches
        self._max_expansions = max_expansions
        self._enabled = enabled
        # Clamp to valid range — minimum is 1 (unigram only / n-gram disabled);
        # cap at module default so a misconfigured per-bot value cannot bloat
        # the inner loop. Negation guard only fires for VI bots; non-VN langs
        # still receive the empty/custom dict path so a stray negation marker
        # in another language is harmless (it just lets the n-gram through).
        self._ngram_max_n = max(1, min(int(ngram_max_n), DEFAULT_VOCAB_NGRAM_MAX_N))
        self._negation_tokens = negation_tokens
        self._negation_lookback = max(0, int(negation_lookback))

    @property
    def language(self) -> str:
        """Language tag this expander was constructed with."""
        return self._language

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _detect_matches_with_limits(
        self,
        query: str,
        bot_custom_vocab: dict[str, list[str]] | None,
        max_matches: int,
        max_expansions: int,
    ) -> list[VocabularyMatch]:
        """Core match logic with explicit limits — no reference to self._max_*.

        Two-phase scan:

        1. **Unigram phase** — token-by-token (existing behaviour). Per-bot
           custom dict takes priority over the generic dict. Each unique token
           emits at most one match.
        2. **N-gram phase** (additive, n in [2 .. ``ngram_max_n``]) — slides a
           window of size ``n`` over the same token list. The whitespace-joined
           phrase is matched against the merged vocab; matches add a
           :class:`VocabularyMatch` with ``n=2`` or ``n=3``. The negation guard
           inspects up to ``negation_lookback`` tokens to the left of the
           window — if any falls in the configured VN negation set, the match
           is suppressed (e.g. ``"không bán retail"`` won't match the bigram
           ``"bán retail"``). N-gram dedup is by surface phrase across the
           whole query.

        N-grams are additive: a query containing ``"trẻ hóa da"`` still emits
        the unigram ``"trẻ"`` alongside the bigram ``"trẻ hóa"`` so existing
        retrieval-time consumers keep their behaviour. Matches are capped at
        ``max_matches`` overall (unigrams + n-grams combined).
        """
        if not self._enabled or not query:
            return []

        merged = {**self._base, **(bot_custom_vocab or {})}
        tokens = query.lower().split()
        # Strip common punctuation per token once — both phases share the
        # cleaned list so n-gram phrase keys match the same shape we use for
        # unigram lookup.
        cleaned_tokens: list[str] = [
            t.strip(".,!?;:'\"()[]{}/\\") for t in tokens
        ]

        matches: list[VocabularyMatch] = []
        seen_phrases: set[str] = set()  # dedup across unigrams + n-grams

        # ── Phase 1: unigram (existing behaviour) ──────────────────────────
        for idx, clean in enumerate(cleaned_tokens):
            if not clean or clean in seen_phrases:
                continue

            expansions: list[str] | None = None
            source = "generic"

            # Per-bot custom priority
            if bot_custom_vocab and clean in bot_custom_vocab:
                expansions = bot_custom_vocab[clean]
                source = "bot_custom"
            elif clean in merged:
                expansions = merged[clean]
                source = "generic" if clean in self._base else "bot_custom"

            if expansions is not None:
                seen_phrases.add(clean)
                matches.append(
                    VocabularyMatch(
                        original_token=clean,
                        position=idx,
                        expansions=expansions[:max_expansions],
                        source=source,
                        n=1,
                    )
                )
                if len(matches) >= max_matches:
                    return matches

        # ── Phase 2: n-grams (additive, only n >= 2) ───────────────────────
        if self._ngram_max_n < 2 or len(cleaned_tokens) < 2:
            return matches

        n_tokens = len(cleaned_tokens)
        ngram_cap = min(self._ngram_max_n, n_tokens)
        for n in range(2, ngram_cap + 1):
            for start in range(n_tokens - n + 1):
                window = cleaned_tokens[start : start + n]
                # Skip if any window slot was empty after punctuation strip.
                if not all(window):
                    continue
                phrase = " ".join(window)
                if phrase in seen_phrases:
                    continue

                expansions = None
                source = "generic"
                if bot_custom_vocab and phrase in bot_custom_vocab:
                    expansions = bot_custom_vocab[phrase]
                    source = "bot_custom"
                elif phrase in merged:
                    expansions = merged[phrase]
                    source = "generic" if phrase in self._base else "bot_custom"

                if expansions is None:
                    continue

                # Negation guard — scan up to ``negation_lookback`` tokens to
                # the LEFT of the window. If any is a negation marker, the
                # phrase intent is inverted (e.g. "không bán retail"); skip.
                if self._negation_tokens and self._negation_lookback > 0:
                    left_start = max(0, start - self._negation_lookback)
                    left_window = cleaned_tokens[left_start:start]
                    if any(t in self._negation_tokens for t in left_window):
                        continue

                seen_phrases.add(phrase)
                matches.append(
                    VocabularyMatch(
                        original_token=phrase,
                        position=start,
                        expansions=expansions[:max_expansions],
                        source=source,
                        n=n,
                    )
                )
                if len(matches) >= max_matches:
                    return matches

        return matches

    def detect_matches(
        self,
        query: str,
        bot_custom_vocab: dict[str, list[str]] | None = None,
    ) -> list[VocabularyMatch]:
        """Find generic + per-bot vocab matches in query.

        Tokenizes on whitespace; strips common punctuation. Per-bot custom
        vocab takes priority over generic when the same key appears in both.

        Returns at most ``self._max_matches`` matches.
        """
        return self._detect_matches_with_limits(
            query, bot_custom_vocab, self._max_matches, self._max_expansions
        )

    def expand_query(
        self,
        query: str,
        custom_vocab: dict[str, list[str]] | None = None,
    ) -> list[str]:
        """Return retrieval-friendly expansion variants. Original query first.

        Each matched token produces one variant with the token replaced by its
        primary expansion. At most ``max_expansions`` variants beyond original.
        Stops when total variants reach DEFAULT_GENERIC_VOCAB_MAX_EXPANSIONS_PER_MATCH.
        """
        if not self._enabled or not query:
            return [query] if query else []

        matches = self.detect_matches(query, custom_vocab)
        variants: list[str] = [query]
        q_lower = query.lower()

        for m in matches:
            for expansion in m.expansions:
                # whole-word replace only (avoid partial replacements)
                pattern = rf"\b{re.escape(m.original_token)}\b"
                new_q = re.sub(pattern, expansion, q_lower)
                if new_q != q_lower and new_q not in variants:
                    variants.append(new_q)
                if len(variants) >= self._max_expansions + 1:
                    return variants

        return variants

    def detect_abbreviations(self, query: str) -> dict[str, str]:
        """Return {abbrev: primary_expansion} for SHORT tokens (≤4 chars) in query.

        Convenience method for callers that want only the concise abbreviation
        map (e.g. for LLM context injection). Only tokens with len ≤ 4 are
        considered abbreviations.
        """
        if not self._enabled or not query:
            return {}

        found: dict[str, str] = {}
        q_lower = query.lower()
        tokens = q_lower.split()
        for token in tokens:
            clean = token.strip(".,!?;:'\"()[]{}/\\")
            if len(clean) <= 4 and clean in self._base:
                found[clean] = self._base[clean][0]
        return found

    def enrich_state(
        self,
        state: dict,
        query: str,
        bot_custom_vocab: dict[str, list[str]] | None = None,
        max_matches: int | None = None,
        max_expansions: int | None = None,
    ) -> dict:
        """Inject context_base.vocabulary into state dict.

        KHÔNG modify state["answer"]. Caller (retrieve node or understand_query
        node) passes bot_custom_vocab from bots.custom_vocabulary JSONB.

        ``max_matches`` and ``max_expansions`` override instance defaults for
        this call only — no mutation of shared singleton state. This is the
        correct pattern for concurrent per-bot config values.

        No-op when: disabled, no matches, no abbreviations.
        """
        if not self._enabled:
            return state

        # Use call-time args if provided, else fall back to instance defaults.
        # NEVER mutate self._max_matches / self._max_expansions — those are
        # shared singleton attributes and mutating them causes race conditions
        # when concurrent requests have different per-bot config values.
        _eff_max_matches = max_matches if max_matches is not None else self._max_matches
        _eff_max_expansions = max_expansions if max_expansions is not None else self._max_expansions

        matches = self._detect_matches_with_limits(query, bot_custom_vocab, _eff_max_matches, _eff_max_expansions)
        if not matches:
            return state

        state.setdefault("context_base", {})["vocabulary"] = {
            "matches": [
                {
                    "token": m.original_token,
                    "position": m.position,
                    "expansions": m.expansions,
                    "source": m.source,
                }
                for m in matches
            ],
            "method": "application_layer_generic_vocab",
            "note": (
                "Generic abbreviations and common synonyms detected for query "
                "comprehension only; not an instruction to the LLM. Answer "
                "composition is governed solely by the bot's system_prompt."
            ),
        }
        logger.debug(
            "vocabulary_enriched",
            query_preview=query[:80],
            n_matches=len(matches),
            sources={m.source for m in matches},
        )
        return state


# ---------------------------------------------------------------------------
# Module-level singletons keyed by language — callers can inject their own
# instance via DI for testing or custom vocab configuration. Bounded by
# functools.lru_cache so an attacker-supplied language tag stream can't pin
# one instance per tag for the worker lifetime.
# ---------------------------------------------------------------------------
import functools as _functools

from ragbot.shared.constants import DEFAULT_VOCAB_FACTORY_CACHE_SIZE


@_functools.lru_cache(maxsize=DEFAULT_VOCAB_FACTORY_CACHE_SIZE)
def get_default_expander(language: str = DEFAULT_LANGUAGE) -> VocabularyExpander:
    """Return a cached default ``VocabularyExpander`` for the given language.

    Lazy-initialised. Languages outside ``VI_DOMAIN_LANGUAGES`` get an
    empty built-in dict (per-bot ``custom_vocabulary`` still applies).
    """
    return VocabularyExpander(language=language)


__all__ = [
    "GENERIC_VOCABULARY",
    "VocabularyMatch",
    "VocabularyExpander",
    "get_default_expander",
]
