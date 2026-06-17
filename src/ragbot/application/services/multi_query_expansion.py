"""MultiQueryExpansionService — Replace HyDE single-shot with N paraphrase variants + RRF merge. Best-practice
04/2026 (BEIR / MIRACL) shows multi-query (3-5 paraphrases) outperforms a single
HyDE hypothetical document on Vietnamese paraphrase robustness (Gate 3a equiv).

Contract:
    expand_query(query, *, n_variants, model_id, timeout_s,
                 llm_complete_fn, system_prompt=None,
                 include_original=True) -> list[str]

The service is purposely transport-agnostic: the caller passes a coroutine
``llm_complete_fn`` that accepts ``(model_id: str, messages: list[dict],
timeout_s: int)`` and returns ``{"text": str, ...}``. Wiring lives in
``orchestration/query_graph.py`` so the resolver / invocation_logger contracts
remain a graph-layer concern.

Variant-0 safety net (R4 root-cause fix):
    When ``include_original=True`` (default), the user's verbatim query is
    ALWAYS emitted as variant 0 before any LLM-generated rewrites. This
    guards against stochastic rewriters dropping critical signal terms in
    every paraphrase — RRF then merges at least one branch retaining the
    user's exact wording, so rare/brand/numeric tokens cannot vanish from
    all variants and collapse retrieval to zero chunks.

Fallback rule: if the LLM call fails, times out, or returns unparseable text,
return ``[query]`` when ``include_original=True`` (always include original —
graceful degrade preserves the single-query flow) or ``[]`` when
``include_original=False``. Duplicate paraphrases are de-duplicated by
case-folded comparison while preserving first occurrence (so the original
query is always emitted first).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import structlog

from ragbot.shared.constants import (
    DEFAULT_ENTITY_GROUNDING_ENABLED,
    DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES,
    DEFAULT_LANGUAGE,
    DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD,
    DEFAULT_MULTI_QUERY_INCLUDE_ORIGINAL,
    DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    DEFAULT_MULTI_QUERY_N_VARIANTS,
    DEFAULT_MULTI_QUERY_PROMPT_KEY,
    DEFAULT_MULTI_QUERY_TIMEOUT_S,
    DEFAULT_RRF_K,
    MULTI_QUERY_INTENT_PROMPT_KEYS,
)

if TYPE_CHECKING:
    from ragbot.application.ports.entity_extractor_port import EntityExtractorPort
    from ragbot.application.ports.language_pack_port import LanguagePackPort

logger = structlog.get_logger(__name__)


# Type alias — caller supplies an async fn matching this contract.
LLMCompleteFn = Callable[..., Awaitable[dict[str, Any]]]


async def _resolve_intent_prompt(
    intent: str | None,
    *,
    language: str,
    language_pack_service: "LanguagePackPort | None",
) -> str:
    """Resolve the rewrite-prompt template for ``(intent, language)``.

    Single source of truth for the per-intent prompt; DB row from the
    ``language_packs`` table (seeded by alembic 0099). Unknown / None
    intent falls back to the default paraphrase prompt key. When no
    ``language_pack_service`` is supplied (legacy caller / unit test
    that omits DI), returns ``""`` — the caller then falls through to
    the LLM with an empty system prompt which graceful-degrades to a
    no-rewrite fallback path in ``expand_query``.

    Open-Closed: adding a new intent template = (1) seed row in
    ``language_packs``, (2) entry in ``MULTI_QUERY_INTENT_PROMPT_KEYS``
    — no edit to this function body or the orchestrator.
    """
    prompt_key = MULTI_QUERY_INTENT_PROMPT_KEYS.get(
        intent or "", DEFAULT_MULTI_QUERY_PROMPT_KEY
    )
    if language_pack_service is None:
        return ""
    try:
        return await language_pack_service.get(language, prompt_key)
    except Exception as exc:  # noqa: BLE001 — fail-soft: empty prompt → fallback
        logger.warning(
            "multi_query_intent_prompt_resolve_failed",
            intent=intent,
            language=language,
            prompt_key=prompt_key,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return ""


def _dedup_preserve_order(items: list[str]) -> list[str]:
    """Case-fold + whitespace-collapse de-dup preserving first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        if not raw:
            continue
        norm = " ".join(raw.split()).strip().casefold()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(raw.strip())
    return out


def _jaccard_token_similarity(a: str, b: str) -> float:
    """Token-set Jaccard similarity in ``[0, 1]`` — cheap fallback when no
    embedder is available for cosine. Empty strings return ``0.0``.

    Tokenisation is whitespace + casefold; suitable for the near-duplicate
    paraphrase signal the rewriter pipeline needs (variants rarely differ
    in punctuation/casing alone).
    """
    sa = {t for t in a.casefold().split() if t}
    sb = {t for t in b.casefold().split() if t}
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return float(inter) / float(union) if union else 0.0


def _cosine_similarity(va: list[float], vb: list[float]) -> float:
    """Cosine similarity between two equal-length dense vectors. Returns
    ``0.0`` on shape mismatch / empty inputs / zero-norm vector — the
    caller treats < threshold as "different enough", so a defensive 0.0
    keeps both variants instead of silently collapsing on degenerate input.
    """
    if not va or not vb or len(va) != len(vb):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(va, vb):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return float(dot) / ((na**0.5) * (nb**0.5))


async def dedup_variants(
    variants: list[str],
    *,
    embedder: Any | None = None,
    threshold: float = DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD,
    embed_one_fn: Callable[[str], Awaitable[list[float]]] | None = None,
) -> tuple[list[str], int]:
    """Drop near-duplicate paraphrases above ``threshold`` similarity.

    Strategy:
      * If ``embed_one_fn`` is supplied, embed every variant once and
        compare via cosine — accurate but pays 1 embedding call per
        variant. Caller passes a closure capturing ``record_tenant_id``
        + ``EmbeddingSpec`` so this helper stays domain-neutral.
      * Otherwise (``embedder=None`` or no closure provided), fall back
        to token-set Jaccard similarity — zero-cost lexical signal,
        accurate enough for the paraphrase rewriter.

    Order preservation: the first occurrence wins; later variants whose
    similarity-vs-any-kept variant exceeds ``threshold`` are dropped.

    Returns ``(kept_variants, dropped_count)`` so callers can drive the
    Prometheus counter without re-counting.

    Fail-soft: any embedding failure for a single variant downgrades that
    pairwise comparison to Jaccard. The function never raises.
    """
    if not variants:
        return [], 0
    # Trivial single-variant case — nothing to dedup against.
    if len(variants) == 1:
        return list(variants), 0

    # Optional embedding path — run upfront so the inner loop is O(N²)
    # vector dot-products rather than re-embedding each comparison.
    # Wave M3.5-A4 2026-05-20: 3 embedding calls run in parallel via
    # ``asyncio.gather`` instead of a sequential ``for`` loop. Pre-fix
    # the loop added ~100-300ms (one round-trip per variant); post-fix
    # the wall-clock cost equals a single round-trip regardless of
    # variant count. Per-variant exceptions still degrade to None
    # (Jaccard fallback) just like before.
    embeddings: list[list[float] | None] = [None] * len(variants)
    if embed_one_fn is not None:
        async def _embed_safe(idx: int, txt: str) -> tuple[int, list[float] | None]:
            try:
                vec = await embed_one_fn(txt)
                return idx, vec
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
                logger.debug(
                    "mq_dedup_embed_failed",
                    variant_idx=idx,
                    variant_preview=txt[:80],
                )
                return idx, None

        results = await asyncio.gather(
            *[_embed_safe(i, t) for i, t in enumerate(variants)],
            return_exceptions=False,
        )
        for idx, vec in results:
            embeddings[idx] = vec

    kept_indices: list[int] = []
    for idx, text in enumerate(variants):
        is_dup = False
        for kept_idx in kept_indices:
            v_a = embeddings[idx]
            v_b = embeddings[kept_idx]
            if v_a is not None and v_b is not None:
                sim = _cosine_similarity(v_a, v_b)
            else:
                sim = _jaccard_token_similarity(text, variants[kept_idx])
            if sim >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept_indices.append(idx)
    kept = [variants[i] for i in kept_indices]
    dropped = len(variants) - len(kept)
    return kept, dropped


def _parse_paraphrases(text: str, *, max_variants: int) -> list[str]:
    """Best-effort parse of LLM output to list[str].

    Tries strict JSON first, then falls back to a JSON-array regex extract,
    then to line-based parsing. Returns at most ``max_variants`` items;
    callers are expected to merge with the original query.
    """
    if not text:
        return []
    cleaned = text.strip()
    # Strip code fences ``` or ```json
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()

    # Strict JSON.
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            items = [str(x) for x in parsed if isinstance(x, (str, int, float))]
            return items[:max_variants]
    except (json.JSONDecodeError, ValueError):
        pass

    # Regex array extract (LLM wrapped JSON in prose).
    match = re.search(r"\[.*?\]", cleaned, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                items = [str(x) for x in parsed if isinstance(x, (str, int, float))]
                return items[:max_variants]
        except (json.JSONDecodeError, ValueError):
            pass

    # Line-based fallback: numbered or bulleted list.
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    extracted: list[str] = []
    for line in lines:
        # Strip leading numbering / bullets / quotes.
        stripped = re.sub(r"^[\d\.\)\-\*\s\"']+", "", line)
        stripped = re.sub(r"[\"']\s*,?\s*$", "", stripped)
        if stripped:
            extracted.append(stripped)
    return extracted[:max_variants]


async def expand_query(
    query: str,
    *,
    n_variants: int = DEFAULT_MULTI_QUERY_N_VARIANTS,
    model_id: str,
    timeout_s: int = DEFAULT_MULTI_QUERY_TIMEOUT_S,
    llm_complete_fn: LLMCompleteFn,
    system_prompt: str | None = None,
    max_variants: int = DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    include_original: bool = DEFAULT_MULTI_QUERY_INCLUDE_ORIGINAL,
    intent: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    language_pack_service: "LanguagePackPort | None" = None,
) -> list[str]:
    """Expand a user query into N paraphrase variants for multi-query retrieval.

    Args:
        query: Raw user query (already condensed/rewritten upstream).
        n_variants: Total queries desired in output (including original when
            ``include_original`` is True).
        model_id: LLM model identifier passed through to ``llm_complete_fn``.
        timeout_s: Max seconds the LLM call may take before fallback.
        llm_complete_fn: Async callable performing the actual completion.
        system_prompt: Optional override for the paraphrase instruction.
        max_variants: Hard ceiling — never return more than this.
        include_original: When True (default), the user's verbatim query is
            ALWAYS emitted as variant 0 before LLM-generated rewrites. This
            is the safety net against stochastic rewriters dropping critical
            signal — RRF gets at least one branch with original wording so
            rare/brand/numeric tokens cannot vanish from all variants. Set
            False only for diagnostic A/B isolating rewriter recall.
        intent: Optional intent label (``factoid`` / ``multi_hop`` /
            ``comparison`` / ``aggregation`` / ``synthesis``). When supplied
            and ``system_prompt`` is None, resolves the matching template
            from the ``language_packs`` table via ``language_pack_service``
            (Multi-HyDE non-equivalent variants). Unknown intent falls back
            to the default paraphrase key. ``system_prompt`` override
            always wins.
        language: ISO language code used to look up the per-intent
            rewrite prompt in ``language_packs``. Falls back to
            ``DEFAULT_LANGUAGE`` ('vi') so legacy callers continue to work.
        language_pack_service: DI-injected ``LanguagePackPort`` resolver
            for the rewrite prompt. When ``None`` (test stub / legacy
            caller), the function emits an empty system prompt — the LLM
            still receives the user query and returns paraphrases, then
            the contract fallback (``[query]`` if ``include_original``)
            kicks in on any failure. Pass a real service in production.

    Returns:
        List with the original query first (when ``include_original``),
        followed by unique paraphrases. Length is in [0, max_variants].
        On any LLM failure → ``[query]`` if ``include_original`` else ``[]``.
    """
    base = (query or "").strip()
    if not base:
        return []

    # Hard ceiling — clamp n_variants to allowed range.
    n_variants = max(1, min(int(n_variants or 1), int(max_variants)))

    def _finalise(rewrites: list[str]) -> list[str]:
        """Apply variant-0 safety net + dedup + cap.

        Centralised so EVERY return path (success, timeout, parse-fail) uses
        the same rule: if ``include_original`` is True, the verbatim query
        is ALWAYS variant 0; otherwise only the (deduplicated) rewrites are
        returned. Capped at ``max_variants`` after dedup.
        """
        if include_original:
            merged = _dedup_preserve_order([base, *rewrites])
        else:
            merged = _dedup_preserve_order(list(rewrites))
        return merged[:max_variants]

    # Optimisation: when only 1 variant requested, skip LLM entirely.
    if n_variants <= 1:
        return _finalise([])

    # When include_original is True we reserve slot 0 for the user's
    # verbatim query and ask the LLM for (n_variants - 1) paraphrases.
    # When False we ask for the full n_variants paraphrases.
    n_paraphrases = (n_variants - 1) if include_original else n_variants
    if n_paraphrases <= 0:
        # Edge case: include_original=True + n_variants=1 already returned
        # above; here only triggers if a future caller passes weird args.
        return _finalise([])

    # Per-intent template dispatch (Multi-HyDE). Explicit ``system_prompt``
    # override always wins; otherwise the language-pack lookup falls back
    # to the default paraphrase key for unknown / None intents — no
    # ``if intent ==`` ladder, Open-Closed by construction. Template text
    # lives in the ``language_packs`` table (seeded by alembic 0099), NOT
    # inline in code — Quality Gate #10 / Application MINDSET compliant.
    if system_prompt is not None:
        _template = system_prompt
    else:
        _template = await _resolve_intent_prompt(
            intent,
            language=language,
            language_pack_service=language_pack_service,
        )
    prompt = _template.format(n=n_paraphrases) if _template else ""
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": base},
    ]

    try:
        result = await asyncio.wait_for(
            llm_complete_fn(model_id=model_id, messages=messages, timeout_s=timeout_s),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "multi_query_expand_timeout",
            timeout_s=timeout_s,
            model_id=model_id,
            query_preview=base[:80],
        )
        return _finalise([])
    except Exception as exc:  # noqa: BLE001 — fallback is the contract
        logger.warning(
            "multi_query_expand_failed",
            error=str(exc),
            model_id=model_id,
            query_preview=base[:80],
        )
        return _finalise([])

    text = (result or {}).get("text", "") if isinstance(result, dict) else ""
    paraphrases = _parse_paraphrases(text, max_variants=n_paraphrases)
    if not paraphrases:
        logger.info(
            "multi_query_expand_no_paraphrases",
            model_id=model_id,
            query_preview=base[:80],
            raw_preview=(text or "")[:120],
        )
        return _finalise([])

    return _finalise(paraphrases)


async def expand_query_with_entities(
    query: str,
    *,
    n_variants: int = DEFAULT_MULTI_QUERY_N_VARIANTS,
    model_id: str,
    timeout_s: int = DEFAULT_MULTI_QUERY_TIMEOUT_S,
    llm_complete_fn: LLMCompleteFn,
    system_prompt: str | None = None,
    max_variants: int = DEFAULT_MULTI_QUERY_MAX_VARIANTS,
    include_original: bool = DEFAULT_MULTI_QUERY_INCLUDE_ORIGINAL,
    entity_extractor: "EntityExtractorPort | None" = None,
    language: str = DEFAULT_LANGUAGE,
    entity_grounding_enabled: bool = DEFAULT_ENTITY_GROUNDING_ENABLED,
    max_entities: int = DEFAULT_ENTITY_GROUNDING_MAX_ENTITIES,
    intent: str | None = None,
    language_pack_service: "LanguagePackPort | None" = None,
) -> list[str]:
    """T3 entity-grounded multi-query expansion.

    Wraps :func:`expand_query` and additionally appends per-entity
    variants extracted from the query. Each entity is emitted as a
    *standalone short BM25-friendly variant* (e.g. ``"0901234567"``,
    ``"Apple iPhone"``) so at least one branch of the multi-query fan-out
    matches the entity verbatim, regardless of what the paraphrase LLM
    produced. Predicted +8-12pp factoid_in_corpus PASS lift on the
    paraphrase-poor query mix.

    Backward compat: when ``entity_extractor`` is ``None`` OR
    ``entity_grounding_enabled`` is ``False`` OR the extractor returns
    no entities, the output is byte-identical to ``expand_query()`` with
    the same arguments — existing callers see no behaviour change.

    Variant ordering (after dedup + cap):
        ``[original, paraphrase_1, ..., paraphrase_K, entity_1, ..., entity_M]``

    The original (variant-0 safety net) and paraphrases are emitted by
    delegating to :func:`expand_query`. Entities are then appended,
    deduplicated against earlier variants (case-folded), and the
    combined list is capped at ``max_variants`` so the worst-case
    fan-out (1 + K + M) cannot blow past the per-bot budget.

    @param query: raw user query (already condensed/rewritten upstream).
    @param n_variants: number of paraphrase variants requested
        (forwarded to :func:`expand_query`).
    @param model_id: LLM model identifier.
    @param timeout_s: paraphrase LLM timeout.
    @param llm_complete_fn: async LLM completion callable.
    @param system_prompt: optional override for paraphrase instruction.
    @param max_variants: hard ceiling on the *combined* list length.
    @param include_original: variant-0 safety net flag (forwarded).
    @param entity_extractor: opt-in NER strategy. ``None`` falls back
        to plain :func:`expand_query` behaviour.
    @param language: bot language hint forwarded to the extractor.
    @param entity_grounding_enabled: master toggle. Falsey = entity
        path bypassed (forwards to :func:`expand_query`).
    @param max_entities: cap on entity variants merged into the list
        before the global ``max_variants`` cap kicks in. Avoids one
        verbose query (``"địa chỉ chi nhánh ABC123 ở Hà Nội"``) starving
        the paraphrase slots when ``max_variants`` is small.
    @return: variant list capped at ``max_variants``; empty list iff
        ``query`` is empty/whitespace-only.
    """
    base = (query or "").strip()
    if not base:
        return []

    # Step 1: paraphrase + variant-0 (existing, untouched contract).
    paraphrase_variants = await expand_query(
        base,
        n_variants=n_variants,
        model_id=model_id,
        timeout_s=timeout_s,
        llm_complete_fn=llm_complete_fn,
        system_prompt=system_prompt,
        max_variants=max_variants,
        include_original=include_original,
        intent=intent,
        language=language,
        language_pack_service=language_pack_service,
    )

    # Step 2: short-circuit when entity grounding is OFF or no extractor.
    if not entity_grounding_enabled or entity_extractor is None:
        return paraphrase_variants

    # Step 3: extract entities, fail-soft on any extractor error.
    try:
        raw_entities = await entity_extractor.extract(base, language=language)
    except Exception as exc:  # noqa: BLE001 — fallback graceful per Port contract
        logger.warning(
            "entity_extractor_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            provider=getattr(entity_extractor, "get_provider_name", lambda: "?")(),
            query_preview=base[:80],
        )
        return paraphrase_variants

    if not raw_entities:
        return paraphrase_variants

    # Cap entity count BEFORE dedup vs paraphrase variants — cheaper to
    # trim at the source than to discover overlap dedup wiped them all.
    capped = [e for e in raw_entities if isinstance(e, str) and e.strip()][
        : max(0, int(max_entities or 0))
    ]
    if not capped:
        return paraphrase_variants

    # Step 4: dedup + cap. We pass the paraphrase variants in first so
    # the original / paraphrases keep their existing order; entities
    # whose verbatim already appears in a paraphrase are dropped.
    merged = _dedup_preserve_order([*paraphrase_variants, *capped])

    logger.info(
        "entity_grounded_expansion",
        provider=getattr(entity_extractor, "get_provider_name", lambda: "?")(),
        language=language,
        entities_extracted=len(raw_entities),
        entities_used=min(len(capped), max(0, len(merged) - len(paraphrase_variants))),
        paraphrase_count=len(paraphrase_variants),
        final_variant_count=min(len(merged), max_variants),
    )

    return merged[:max_variants]


def rrf_merge_chunks(
    result_lists: list[list[dict]],
    *,
    rrf_k: int = DEFAULT_RRF_K,
    chunk_id_key: str = "chunk_id",
) -> list[dict]:
    """Reciprocal-Rank-Fusion merge over multiple chunk-list results.

    Cormack et al. 2009: ``score(d) = Σ_q 1 / (k + rank_q(d))``.

    Each result list is treated as already ordered by relevance (rank 0 = best).
    Chunks are deduplicated by ``chunk_id_key``. The first occurrence wins for
    payload data; only the ``score`` field is overwritten with the RRF score.
    Returns chunks ordered by RRF score (highest first).

    Args:
        result_lists: One list of chunk dicts per paraphrase query.
        rrf_k: Penalty constant. Default 60 (Cormack canonical).
        chunk_id_key: Field used to identify identical chunks across lists.
    """
    if not result_lists:
        return []

    # Drop empty lists upfront — they contribute nothing.
    non_empty = [lst for lst in result_lists if lst]
    if not non_empty:
        return []

    # Identity-preserving fallback: if only one list survived, return it
    # unchanged so single-query flow is bit-exact (no score rewriting).
    if len(non_empty) == 1:
        return list(non_empty[0])

    scores: dict[str, float] = {}
    chunks_by_id: dict[str, dict] = {}
    for results in non_empty:
        for rank, chunk in enumerate(results):
            cid = str(chunk.get(chunk_id_key) or chunk.get("id") or "")
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
            if cid not in chunks_by_id:
                chunks_by_id[cid] = dict(chunk)

    ordered = sorted(chunks_by_id.values(), key=lambda c: scores[str(c.get(chunk_id_key) or c.get("id") or "")], reverse=True)
    for c in ordered:
        cid = str(c.get(chunk_id_key) or c.get("id") or "")
        c["score"] = scores.get(cid, 0.0)
    return ordered


class MultiQueryExpansionService:
    """Class-shaped facade over the module-level multi-query helpers.

    The free-function :func:`expand_query` predates the Strategy + DI mindset
    push (CLAUDE.md 2026-05): it accepts a raw ``language_pack_service`` port
    in every call. The class wraps that port + the per-intent prompt-key map
    so callers in the orchestration layer can inject a single configured
    service rather than threading the language pack through every node.

    The class is intentionally thin — heavy lifting (LLM call, parse, dedup,
    variant-0 safety net) stays in the module-level functions which remain
    the unit-test target for the algorithmic surface area. The class is the
    *DI boundary*; the functions are the *algorithm*.

    Open-Closed: adding a new intent template = (1) seed the
    ``language_packs`` row, (2) add an entry to
    ``MULTI_QUERY_INTENT_PROMPT_KEYS`` in ``shared/constants.py``. Neither
    this class nor :func:`expand_query` changes.
    """

    def __init__(
        self,
        *,
        language_pack: "LanguagePackPort | None" = None,
        default_language: str = DEFAULT_LANGUAGE,
        intent_prompt_keys: dict[str, str] | None = None,
        default_prompt_key: str = DEFAULT_MULTI_QUERY_PROMPT_KEY,
    ) -> None:
        self._language_pack = language_pack
        self._default_language = default_language
        # Copy the map so per-instance overrides cannot leak into the
        # module-level constant — supports per-tenant intent registries
        # without polluting global state.
        self._intent_prompt_keys = dict(
            intent_prompt_keys
            if intent_prompt_keys is not None
            else MULTI_QUERY_INTENT_PROMPT_KEYS
        )
        self._default_prompt_key = default_prompt_key

    async def _resolve_prompt(
        self,
        language: str,
        intent: str | None,
    ) -> str:
        """Look up the rewrite-prompt template for ``(language, intent)``.

        Single source of truth for the per-intent rewrite prompt; resolves
        the template from the ``language_packs`` DB row via the injected
        :class:`LanguagePackPort`. Unknown / ``None`` intent falls back to
        the default paraphrase key — Open-Closed: no ``if intent ==`` ladder.

        Returns the empty string when no language pack is injected (legacy
        caller / unit test stub) so the caller can fall through to its own
        empty-prompt fallback rather than raising.
        """
        prompt_key = self._intent_prompt_keys.get(
            intent or "", self._default_prompt_key
        )
        if self._language_pack is None:
            return ""
        try:
            return await self._language_pack.get(language, prompt_key)
        except Exception as exc:  # noqa: BLE001 — fail-soft: empty prompt → fallback
            logger.warning(
                "multi_query_intent_prompt_resolve_failed",
                intent=intent,
                language=language,
                prompt_key=prompt_key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ""

    async def expand(
        self,
        query: str,
        *,
        n_variants: int = DEFAULT_MULTI_QUERY_N_VARIANTS,
        model_id: str,
        timeout_s: int = DEFAULT_MULTI_QUERY_TIMEOUT_S,
        llm_complete_fn: LLMCompleteFn,
        system_prompt: str | None = None,
        max_variants: int = DEFAULT_MULTI_QUERY_MAX_VARIANTS,
        include_original: bool = DEFAULT_MULTI_QUERY_INCLUDE_ORIGINAL,
        intent: str | None = None,
        language: str | None = None,
    ) -> list[str]:
        """Bound :func:`expand_query` call using the injected language pack.

        Convenience wrapper so orchestration nodes can call
        ``await service.expand(query, ...)`` instead of threading the
        language pack through every call site.
        """
        return await expand_query(
            query,
            n_variants=n_variants,
            model_id=model_id,
            timeout_s=timeout_s,
            llm_complete_fn=llm_complete_fn,
            system_prompt=system_prompt,
            max_variants=max_variants,
            include_original=include_original,
            intent=intent,
            language=language if language is not None else self._default_language,
            language_pack_service=self._language_pack,
        )


__all__ = [
    "expand_query",
    "expand_query_with_entities",
    "rrf_merge_chunks",
    "dedup_variants",
    "LLMCompleteFn",
    "MultiQueryExpansionService",
]
