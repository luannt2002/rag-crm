"""Vietnamese text tokenization cho BM25 search.

Dùng underthesea nếu có, fallback giữ nguyên text (lowercase).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any
from uuid import UUID

import structlog

from ragbot.shared.constants import (
    DEFAULT_ABBREVIATIONS_CACHE_TTL_S,
    DEFAULT_LANGUAGE,
    DEFAULT_VI_COMPOUND_SEGMENTATION_THROUGHPUT_CHARS_PER_S,
    DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S,
    VI_DOMAIN_LANGUAGES,
)

logger = structlog.get_logger(__name__)

# Product/spec codes that underthesea would shatter into loose tokens
# ("195/65R15" → "195 / 65R15", "2-R17" → "2 - R17"). We mask them with a
# pure-letter placeholder, segment, then restore — so a code stays ONE BM25
# token in both the corpus index and the query. Domain-neutral: any
# separator-joined alphanumeric or digit+letter run (tire spec, SKU, part
# number, "91H"). Plain numbers (no letter / no separator) are NOT matched, so
# prices and years tokenize normally.
_CODE_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]+(?:[/.\-][A-Za-z0-9]+)+"   # 195/65R15, 2-R17, 1.200.000
    r"|[0-9]+[A-Za-z]+[A-Za-z0-9]*"          # 91H, 245R17
)


def _idx_to_alpha(i: int) -> str:
    """Encode an index as lowercase letters (no digits — underthesea-safe)."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(97 + r) + s
    return s


def _protect_codes(text: str) -> tuple[str, dict[str, str]]:
    """Replace code-like tokens with pure-letter placeholders.

    Returns the masked text plus a placeholder→original map for restoration.
    Placeholders are letter-only ("zqcodeaaazq") so the segmenter keeps each
    as a single token and cannot be confused with real corpus words.
    """
    mapping: dict[str, str] = {}

    def _sub(m: re.Match[str]) -> str:
        ph = f"zqcode{_idx_to_alpha(len(mapping))}zq"
        mapping[ph] = m.group(0)
        return ph

    return _CODE_TOKEN_RE.sub(_sub, text), mapping


_lock = threading.Lock()
_tokenize_fn: Any = None
_initialized = False


def _init_tokenizer() -> None:
    """Eager-load underthesea + force model warmup INSIDE the lock.

    Race condition in underthesea (verified 2026-05-18): ``word_tokenize``
    instantiates a module-level ``word_tokenize_model = FastCRFSequenceTagger()``
    on first call, then ``.load(...)`` populates the model file (~10 MB).
    Two concurrent callers can see ``word_tokenize_model is not None`` after
    instantiation but **before** load completes — the second caller hits
    ``model.predict(...)`` against a partially-initialised tagger and raises::

        AttributeError: 'NoneType' object has no attribute 'process'

    Fix: invoke ``word_tokenize`` once with a non-empty Vietnamese sample
    INSIDE the ``_lock`` block so all subsequent callers see a fully-loaded
    model. The warmup cost (~1 s on cold start) is paid once per process.
    """
    global _tokenize_fn, _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        try:
            from underthesea import word_tokenize
            # Force model load to completion BEFORE we publish the
            # callable to ``_tokenize_fn``. The actual return value is
            # discarded; we only need the side-effect of populating
            # ``underthesea.pipeline.word_tokenize.word_tokenize_model``.
            word_tokenize("xin chào", format="text")
            _tokenize_fn = word_tokenize
            logger.info("vi_tokenizer_loaded", backend="underthesea")
        except ImportError:
            _tokenize_fn = None
            logger.info("vi_tokenizer_loaded", backend="fallback_lowercase")
        except (AttributeError, OSError, ValueError, RuntimeError) as exc:
            # Model file missing / corrupt / version drift — degrade to
            # fallback rather than crashing the entire ingest pipeline.
            _tokenize_fn = None
            logger.warning(
                "vi_tokenizer_warmup_failed",
                backend="fallback_lowercase",
                error_type=type(exc).__name__,
                error=str(exc)[:120],
            )
        _initialized = True


def segment_vi_compounds(
    text: str,
    *,
    timeout_s: int = DEFAULT_VI_COMPOUND_SEGMENTATION_TIMEOUT_S,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """P22 Option B — pre-segment Vietnamese compounds for BM25 indexing.

    Wraps ``underthesea.word_tokenize(text, format="text")`` so multi-word
    Vietnamese compounds become single underscore-joined tokens
    (``"chăm sóc da"`` → ``"chăm_sóc da"``). When the segmented text is fed
    into Postgres ``to_tsvector('simple', ...)`` the compound becomes ONE
    BM25 token instead of three loose tokens — matching how the query side
    can search for compound terms.

    Behaviour:
    - ``language`` outside ``VI_DOMAIN_LANGUAGES``: NO-OP — input returned
      unchanged. Underthesea is a Vietnamese-only segmenter; running it on
      EN/JP/KO/ZH/etc. content burns ~100-300 ms per chunk and risks
      corrupting non-VN tokens. Multi-industry / multi-language safe by
      default — Empty / whitespace-only / None: returns input unchanged.
    - underthesea unavailable: returns input unchanged + debug log.
    - underthesea raises: returns input unchanged + warn log (graceful fallback).
    - Soft timeout: underthesea is fast (sync, ~ms) but very large inputs
      could spike. ``timeout_s`` is a *length-based* guard rather than a wall
      clock — we accept any input below ``timeout_s * 200_000`` chars
      (~200KB/s is conservative for underthesea on commodity CPUs). Larger
      inputs fall back to original to keep ingest bounded.

    @param text: raw Vietnamese (or mixed) text from a document chunk.
    @param timeout_s: budget in seconds (used as a length guard).
    @param language: bot language tag. Defaults to ``DEFAULT_LANGUAGE`` for
        backward-compat; non-VN tenants pass their own and skip.
    @return: segmented text (compound joined with ``_``) or original on fallback.
    """
    if not text or not text.strip():
        return text if text is not None else ""

    # Language gate — non-VN tenants skip underthesea entirely.
    if language not in VI_DOMAIN_LANGUAGES:
        return text

    # Length-based budget guard (sync API has no wall-clock).
    _max_chars = max(1, timeout_s) * DEFAULT_VI_COMPOUND_SEGMENTATION_THROUGHPUT_CHARS_PER_S
    if len(text) > _max_chars:
        logger.warning(
            "vi_compound_segment_skipped_oversize",
            text_chars=len(text),
            limit_chars=_max_chars,
        )
        return text

    _init_tokenizer()
    if _tokenize_fn is None:
        logger.debug("vi_compound_segment_no_backend", text_chars=len(text))
        return text

    try:
        # Protect product/spec codes from being shattered by the segmenter.
        masked, _code_map = _protect_codes(text)
        result = _tokenize_fn(masked, format="text")
        if not isinstance(result, str):
            result = str(result) if result is not None else masked
        # Restore the original codes intact (one BM25 token each).
        for _ph, _orig in _code_map.items():
            result = result.replace(_ph, _orig)
        return result
    except Exception as exc:  # noqa: BLE001 — fallback graceful
        logger.warning(
            "vi_compound_segment_failed",
            error=str(exc),
            text_preview=text[:60],
        )
        return text


def remove_diacritics(text: str) -> str:
    """Remove Vietnamese diacritics for fuzzy matching.

    'gội đầu' → 'goi dau', 'triệt lông' → 'triet long'

    Used as an ADDITIONAL BM25 search variant alongside the original
    query to improve recall for diacritic-insensitive searches.
    """
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# BOOT FALLBACK ONLY — DO NOT EDIT.
#
# This seed is the platform's last-resort default for ``language == 'vi'``
# when neither ``system_config.default_abbreviations_by_language`` nor a
# per-bot ``bots.custom_vocabulary.abbreviations`` is populated.
#
# To add or change an abbreviation at runtime use one of:
#   1. per-bot: ``bots.custom_vocabulary.abbreviations`` JSONB (highest priority)
#   2. platform-wide: ``system_config.default_abbreviations_by_language``
#      (JSON: ``{"vi": {"abbr": "expansion", ...}, "en": {...}}``)
#
# Both are merged on top of this seed by ``get_abbreviations()`` below.
# Editing this dict requires a code deploy and forces every tenant onto the
# new mapping — almost never what you want.
_VI_ABBREVIATIONS_SEED: dict[str, str] = {
    "ko": "không", "k": "không", "hk": "không", "hem": "không",
    "dc": "được", "đc": "được",
    "mk": "mình", "mn": "mọi người", "ns": "nói",
    "j": "gì", "gj": "gì", "r": "rồi", "vs": "với",
    "ntn": "như thế nào", "bn": "bao nhiêu", "bnh": "bao nhiêu",
    "tks": "cảm ơn", "ib": "inbox", "rep": "trả lời", "tl": "trả lời",
    "fb": "facebook", "zl": "zalo", "oki": "ok", "okie": "ok",
    "sdt": "số điện thoại", "đt": "điện thoại",
    "nv": "nhân viên", "kh": "khách hàng",
    "sp": "sản phẩm", "dv": "dịch vụ",
    "ck": "chuyển khoản", "tk": "tài khoản",
    "km": "khuyến mãi", "gt": "giới thiệu",
    "lh": "liên hệ", "đk": "đăng ký", "cs": "chính sách",
    "dn": "doanh nghiệp", "cn": "chủ nhật",
    "vd": "ví dụ", "ib": "inbox", "pm": "nhắn tin",
    "cx": "cũng", "cg": "cũng", "ms": "mới",
    "trc": "trước", "nc": "nói chuyện", "bt": "bình thường",
    "nma": "nhưng mà", "thik": "thích", "lm": "làm", "bik": "biết",
    "thui": "thôi", "nha": "nhé",
    "z": "vậy", "v": "vậy",
    "bhxh": "bảo hiểm xã hội", "bhyt": "bảo hiểm y tế",
    "ubnd": "ủy ban nhân dân", "hdld": "hợp đồng lao động",
    "nld": "người lao động", "hđ": "hợp đồng",
}

# Back-compat alias — DO NOT add keys; mutate system_config / per-bot vocabulary.
_DEFAULT_ABBREVIATIONS: dict[str, str] = _VI_ABBREVIATIONS_SEED


# Diacritic restoration map — empty default; mappings come from system_config
# / per-bot custom_vocabulary.diacritics. ML-based path requires heavy
# transformers stack and is gated until a maintained package is adopted.
_DIACRITIC_MAP: dict[str, str] = {}


def _build_diacritic_pattern(dmap: dict[str, str]) -> re.Pattern[str] | None:
    """Build regex pattern for diacritic restoration from a merged map."""
    if not dmap:
        return None
    return re.compile(
        r"\b(" + "|".join(
            re.escape(k) for k in sorted(dmap.keys(), key=len, reverse=True)
        ) + r")\b",
        re.IGNORECASE | re.UNICODE,
    )


async def restore_diacritics(
    text: str,
    *,
    use_model: bool = False,
    custom_map: dict[str, str] | None = None,
) -> str:
    """Restore Vietnamese diacritics on accent-free text.

    When *use_model* is ``True``, uses ML model (requires ``vn-accent`` package).
    When *use_model* is ``False`` (default), uses rule-based fallback from
    the merged diacritic map (``_DIACRITIC_MAP`` + *custom_map*).

    *custom_map* entries override/merge over the built-in defaults.
    """
    if not text or not text.strip():
        return text

    if use_model:
        # No-op fallback — ML accent restoration not wired (see _DIACRITIC_MAP comment).
        logger.debug("diacritic_restore_model_not_available", fallback="rule_based")

    merged = dict(_DIACRITIC_MAP)
    if custom_map:
        merged.update(custom_map)

    if not merged:
        return text

    pattern = _build_diacritic_pattern(merged)
    if pattern is None:
        return text

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        return merged.get(match.group(0).lower(), match.group(0))

    return pattern.sub(_replace, text)


def expand_abbreviations(
    text: str,
    abbrev_dict: dict[str, str] | None = None,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """Expand Vietnamese abbreviations / teencode (whole-word, case-insensitive).

    Language gate: the VN seed contains bare ASCII tokens (e.g. ``"k"``) that
    would corrupt EN/ZH/JP queries — skipped for non-VN bots.

    @param text: input.
    @param abbrev_dict: caller-supplied merged dict; overrides built-in seed.
    @param language: bot language tag.
    @return: expanded text.
    """
    if not text or not text.strip():
        return text

    if language in VI_DOMAIN_LANGUAGES:
        merged = dict(_VI_ABBREVIATIONS_SEED)
    else:
        merged = {}
    if abbrev_dict:
        merged.update(abbrev_dict)

    if not merged:
        return text

    # Sort longest-first so longer matches win.
    sorted_keys = sorted(merged.keys(), key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in sorted_keys) + r")\b",
        re.IGNORECASE | re.UNICODE,
    )

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        return merged.get(match.group(0).lower(), match.group(0))

    return pattern.sub(_replace, text)


# ── DB-backed abbreviation resolution ───────────────────────────────────
#
# Per-(record_bot_id, language) merged map cache.  In-process only — every
# worker resolves independently.  TTL is intentionally short
# (``DEFAULT_ABBREVIATIONS_CACHE_TTL_S``, 30 s) so bot owners see
# ``custom_vocabulary`` edits without a restart.

_ABBREV_CACHE: dict[tuple[str | None, str], tuple[float, dict[str, str]]] = {}
_ABBREV_CACHE_LOCK = threading.Lock()


def _abbrev_cache_get(key: tuple[str | None, str]) -> dict[str, str] | None:
    """Return a cached merged map if still within TTL, else ``None``."""
    with _ABBREV_CACHE_LOCK:
        entry = _ABBREV_CACHE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            _ABBREV_CACHE.pop(key, None)
            return None
        return value


def _abbrev_cache_put(key: tuple[str | None, str], value: dict[str, str]) -> None:
    """Store a merged map with the configured TTL."""
    expires_at = time.monotonic() + DEFAULT_ABBREVIATIONS_CACHE_TTL_S
    with _ABBREV_CACHE_LOCK:
        _ABBREV_CACHE[key] = (expires_at, value)


def clear_abbreviation_cache() -> None:
    """Drop the in-process abbreviation cache (tests / config-changed events)."""
    with _ABBREV_CACHE_LOCK:
        _ABBREV_CACHE.clear()


async def get_abbreviations(
    language: str,
    *,
    record_bot_id: UUID | None = None,
    config_service: Any = None,
    bot_repo: Any = None,
) -> dict[str, str]:
    """Resolve the abbreviation map for ``language`` (+ optional bot override).

    4-tier merge — later layers WIN over earlier ones:

    1. ``_VI_ABBREVIATIONS_SEED``                       (only when ``language == 'vi'``)
    2. ``system_config.default_abbreviations_by_language[language]``
    3. ``bots.custom_vocabulary.abbreviations``          (per-bot)
    4. ``{}``                                            (final fallback for non-VN langs)

    The result is cached in-process by ``(record_bot_id, language)`` for
    ``DEFAULT_ABBREVIATIONS_CACHE_TTL_S`` seconds.

    The ``config_service`` parameter is duck-typed: any object exposing
    ``await get(key, default)`` returning a JSON-decoded ``dict`` works
    (see ``application.services.system_config_service.SystemConfigService``).

    The ``bot_repo`` parameter is also duck-typed: any object exposing
    ``await get(record_bot_id)`` returning a bot row with a
    ``custom_vocabulary`` attribute (or dict-key) works.

    Both DB lookups are best-effort — transport errors degrade silently
    to the next tier so a flaky Redis / DB never blocks a chat turn.

    @param language: bot language tag (e.g. ``"vi"``, ``"en"``).
    @param record_bot_id: optional internal bot UUID for per-bot override.
    @param config_service: optional ``SystemConfigService``-like port.
    @param bot_repo: optional bot repository exposing ``get(record_bot_id)``.
    @return: merged abbreviation dict (may be empty for non-VN language).
    """
    cache_key = (str(record_bot_id) if record_bot_id is not None else None, language)
    cached = _abbrev_cache_get(cache_key)
    if cached is not None:
        return dict(cached)

    merged: dict[str, str] = dict(_VI_ABBREVIATIONS_SEED) if language == "vi" else {}

    if config_service is not None:
        try:
            sys_data = await config_service.get("default_abbreviations_by_language", {})
            if isinstance(sys_data, dict):
                lang_abbr = sys_data.get(language, {})
                if isinstance(lang_abbr, dict):
                    merged.update({str(k): str(v) for k, v in lang_abbr.items()})
        except Exception as exc:  # noqa: BLE001 — graceful degrade to seed
            logger.debug(
                "abbreviation_config_fetch_failed",
                language=language,
                error=str(exc),
            )

    if bot_repo is not None and record_bot_id is not None:
        try:
            bot = await bot_repo.get(record_bot_id)
            if bot is not None:
                vocab = getattr(bot, "custom_vocabulary", None)
                if vocab is None and isinstance(bot, dict):
                    vocab = bot.get("custom_vocabulary")
                if isinstance(vocab, dict):
                    bot_abbr = vocab.get("abbreviations", {})
                    if isinstance(bot_abbr, dict):
                        merged.update({str(k): str(v) for k, v in bot_abbr.items()})
        except Exception as exc:  # noqa: BLE001 — graceful degrade to platform map
            logger.debug(
                "abbreviation_bot_fetch_failed",
                record_bot_id=str(record_bot_id),
                error=str(exc),
            )

    _abbrev_cache_put(cache_key, merged)
    return dict(merged)


async def expand_abbreviations_async(
    text: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    record_bot_id: UUID | None = None,
    config_service: Any = None,
    bot_repo: Any = None,
) -> str:
    """DB-merged equivalent of :func:`expand_abbreviations`.

    Resolves a merged abbreviation dict via :func:`get_abbreviations`
    (seed + system_config + per-bot) and applies the same whole-word,
    case-insensitive substitution as the sync helper.

    Hot-path callers (query rewrite, chunking pre-process) should use this
    so bot owners can override teencode through ``custom_vocabulary``
    without a code deploy.  Cold-path / boot-time callers may keep the
    sync :func:`expand_abbreviations` (seed-only).
    """
    if not text or not text.strip():
        return text

    abbrev_dict = await get_abbreviations(
        language,
        record_bot_id=record_bot_id,
        config_service=config_service,
        bot_repo=bot_repo,
    )
    if not abbrev_dict:
        return text

    # Re-use the sync expander by passing the merged dict in as override and
    # forcing language to ``"vi"`` only when we already have VN-style keys;
    # otherwise pass through with a non-VN language so the seed branch is
    # skipped (we already merged it ourselves above).
    sync_lang = "vi" if language in VI_DOMAIN_LANGUAGES else language
    if sync_lang in VI_DOMAIN_LANGUAGES:
        # Sync helper would re-add the seed — pass it the already-merged
        # dict and a non-VN language tag to bypass its own seed branch.
        return _apply_abbreviations(text, abbrev_dict)
    return _apply_abbreviations(text, abbrev_dict)


def _apply_abbreviations(text: str, abbrev_dict: dict[str, str]) -> str:
    """Whole-word, case-insensitive replacement using the supplied map only."""
    if not abbrev_dict:
        return text
    sorted_keys = sorted(abbrev_dict.keys(), key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in sorted_keys) + r")\b",
        re.IGNORECASE | re.UNICODE,
    )

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        return abbrev_dict.get(match.group(0).lower(), match.group(0))

    return pattern.sub(_replace, text)


def tokenize_vi(text: str) -> str:
    """Tokenize Vietnamese text for BM25 (DEPRECATED on query side).

    Query path now passes raw text to to_tsvector('simple', ...) for symmetry
    with ingest. Retained for legacy callers; do not reintroduce on the query path.

    @param text: raw text.
    @return: tokenized text (compounds joined with underscore via underthesea).
    """
    if not text or not text.strip():
        return ""
    _init_tokenizer()
    if _tokenize_fn is None:
        return re.sub(r"\s+", " ", text.strip().lower())
    try:
        result = _tokenize_fn(text)
        if isinstance(result, list):
            return " ".join(result).lower()
        return str(result).lower()
    except Exception:  # noqa: BLE001 — fallback to raw text on any tokenizer failure
        logger.debug("vi_tokenize_failed", text=text[:50])
        return text.lower()
