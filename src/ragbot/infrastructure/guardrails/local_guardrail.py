"""Local pattern-based guardrail — DB-driven (Agent J refactor).

Production should layer Llama Guard 3 (via LiteLLM) + Lakera. This module
implements regex-based input / output rules, logs every hit to
``guardrail_events`` via ``GuardrailRepository``, and raises
``GuardrailBlocked`` when any rule emits severity='block'.

Regex patterns are NO LONGER hard-compiled at module load. They live in
``guardrail_rules`` (alembic 010f) and are served by
``application/services/guardrail_rule_loader.GuardrailRuleLoader``. When
the loader is wired (production / bootstrap-tested integration), check_input
and check_output iterate the loader's RuleSet. When the loader is absent
(legacy unit tests that build LocalGuardrail directly), the static
``InputGuardrail`` / ``OutputGuardrail`` methods fall back to compiling
patterns from the SSoT module ``_default_patterns`` — same regex strings
the alembic seed migration inserted.

Privacy 2.B: raw user content NEVER persisted; ``details`` JSONB stores
metadata (match_count, pattern names) — không raw text / snippet.
"""

from __future__ import annotations

import hashlib
import logging
import re

import structlog
from typing import TYPE_CHECKING, Any, Callable, Coroutine
from uuid import UUID

from ragbot.application.dto.llm_schemas import GroundingVerdictsOutput
from ragbot.application.ports.guardrail_port import GuardrailPort, ModerationOutcome
from ragbot.infrastructure.guardrails._default_patterns import (
    get_classic_injection_compiled,
    get_default_compiled,
)
from ragbot.infrastructure.observability.metrics import (
    grounding_degraded_total,
    grounding_fail_total,
    guardrail_triggered_total,
)
from ragbot.shared.constants import (
    DEFAULT_GROUNDING_CONTEXT_PREVIEW_CHARS,
    DEFAULT_GROUNDING_NUMERIC_OVERLAP_ENABLED,
    DEFAULT_GROUNDING_SUBSTRING_MIN,
    DEFAULT_GROUNDING_USE_STRUCTURED,
    DEFAULT_PII_REDACT_MASK,
    DEFAULT_PII_REDACTABLE_RULE_IDS,
    DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT,
    DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE,
    DEFAULT_GUARDRAIL_MAX_INPUT_LENGTH,
    DEFAULT_GUARDRAIL_MIN_ALPHA_CHARS,
    DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD,
    DEFAULT_GUARDRAIL_TIMEOUT_S,
)
from ragbot.shared.types import TenantId

if TYPE_CHECKING:
    from ragbot.application.services.guardrail_rule_loader import (
        GuardrailRuleLoader,
    )

# ---------------------------------------------------------------------------
# Helper regex constants — NOT moderation rules.
# Kept inline because they are not user-overridable policy; they describe
# the citation marker syntax + sentence boundary the grounding helpers
# assume. Moving these to DB would invert ownership (the platform owns
# the citation grammar, not bot owners).
# ---------------------------------------------------------------------------
_CITATION_MARKER_RE = re.compile(r"\[[a-zA-Z0-9_\-]{1,64}\]")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]\s+")

# Use structlog logger (not stdlib logging.getLogger). Production wires
# the root logger through structlog.stdlib.ProcessorFormatter which
# strips ``extra={}`` payloads from stdlib log records (verified via
# journalctl 2026-05-29 hoa-hoc-10 trace). structlog's BoundLogger
# natively accepts kwargs and renders them in the JSON event dict, so
# the surface here is ``_logger.info("event", key=value)`` rather than
# ``_logger.info("event", extra={"key": value})``.
_logger = structlog.get_logger(__name__)
# Stdlib alias for callsites that intentionally want the raw logging
# module surface (legacy fmt-string warnings + exc_info=True paths).
_stdlib_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types — single source of truth lives in the application port so
# orchestration can ``raise GuardrailBlocked`` without importing from
# infrastructure. Re-exported here so existing
# ``from ragbot.infrastructure.guardrails.local_guardrail import
# (GuardrailBlocked, GuardrailHit)`` imports keep working unchanged.
# ---------------------------------------------------------------------------
from ragbot.application.ports.guardrail_port import (  # noqa: E402
    GuardrailBlocked as GuardrailBlocked,
    GuardrailHit as GuardrailHit,
)


# ---------------------------------------------------------------------------
# Input rules (pure functions / class container)
# ---------------------------------------------------------------------------
class InputGuardrail:
    """Pure-function input rule evaluators. Safe for unit tests without DB."""

    @staticmethod
    def length_limit(
        text: str,
        max_len: int = DEFAULT_GUARDRAIL_MAX_INPUT_LENGTH,
    ) -> GuardrailHit | None:
        n = len(text or "")
        if n > max_len:
            return GuardrailHit(
                rule_id="length_limit",
                severity="block",
                action="block",
                details={"length": n, "max_len": max_len},
            )
        return None

    @staticmethod
    def prompt_injection_patterns(text: str) -> GuardrailHit | None:
        pattern = get_default_compiled("prompt_injection")
        if pattern is None:
            return None
        matches = pattern.findall(text or "")
        if matches:
            # findall with groups returns tuples; normalize
            flat: list[str] = []
            for m in matches:
                if isinstance(m, tuple):
                    flat.append(next((x for x in m if x), ""))
                else:
                    flat.append(m)
            return GuardrailHit(
                rule_id="prompt_injection",
                severity="block",
                action="block",
                details={
                    "match_count": len(matches),
                    "patterns": list({p for p in flat if p})[:5],
                },
            )
        return None

    @staticmethod
    def pii_vi(text: str) -> GuardrailHit | None:
        phone_re = get_default_compiled("pii_vi_phone")
        email_re = get_default_compiled("pii_vi_email")
        cmnd_re = get_default_compiled("pii_vi_cmnd")
        hits: dict[str, int] = {}
        if phone_re is not None and (c := len(phone_re.findall(text or ""))):
            hits["phone"] = c
        if email_re is not None and (c := len(email_re.findall(text or ""))):
            hits["email"] = c
        if cmnd_re is not None and (c := len(cmnd_re.findall(text or ""))):
            hits["cmnd"] = c
        if not hits:
            return None
        # Pick rule_id based on dominant category (phone first).
        if "phone" in hits:
            rule_id = "pii_vi_phone"
        elif "email" in hits:
            rule_id = "pii_vi_email"
        else:
            rule_id = "pii_vi_cmnd"
        return GuardrailHit(
            rule_id=rule_id,
            severity="warn",
            action="redact",
            details={"match_count": sum(hits.values()), "categories": hits},
        )

    @staticmethod
    def pii_en(text: str) -> GuardrailHit | None:
        pattern = get_default_compiled("pii_en_ssn")
        if pattern is None:
            return None
        matches = pattern.findall(text or "")
        if matches:
            return GuardrailHit(
                rule_id="pii_en_ssn",
                severity="warn",
                action="redact",
                details={"match_count": len(matches)},
            )
        return None

    @staticmethod
    def too_short(text: str, min_alpha: int = 2) -> GuardrailHit | None:
        """Block empty, whitespace-only, or emoji-only queries.

        Requires at least ``min_alpha`` alphanumeric characters after strip.
        ``min_alpha`` is configurable via pipeline_config key
        ``guardrail_min_alpha_chars`` (default 2).
        """
        stripped = (text or "").strip()
        if not stripped:
            return GuardrailHit(
                rule_id="too_short",
                severity="block",
                action="block",
                details={
                    "length": 0,
                    "min_alpha": min_alpha,
                },
            )
        alpha_count = sum(1 for c in stripped if c.isalnum())
        if alpha_count < min_alpha:
            return GuardrailHit(
                rule_id="too_short",
                severity="block",
                action="block",
                details={
                    "length": len(stripped),
                    "alpha_count": alpha_count,
                    "min_alpha": min_alpha,
                },
            )
        return None

    @staticmethod
    def sql_injection(text: str) -> GuardrailHit | None:
        pattern = get_default_compiled("sql_injection")
        if pattern is None:
            return None
        matches = pattern.findall(text or "")
        if matches:
            return GuardrailHit(
                rule_id="sql_injection",
                severity="block",
                action="block",
                details={"match_count": len(matches)},
            )
        return None


# ---------------------------------------------------------------------------
# PII redaction — the executable form of the rules' ``action="redact"``
# ---------------------------------------------------------------------------
def redact_pii(text: str) -> tuple[str, int]:
    """Mask PII spans in *text*; return ``(redacted_text, n_masked)``.

    Only rules in :data:`DEFAULT_PII_REDACTABLE_RULE_IDS` rewrite the text —
    patterns that are an unambiguous PII SHAPE (VN/intl phone, email, US SSN).
    ``pii_vi_cmnd`` is deliberately NOT in that set: its pattern matches ANY bare
    9- or 12-digit number, which in a catalog corpus includes PRICES and SKUs, so
    masking on it would corrupt legitimate questions. It still FLAGS (the hit is
    recorded), it just never rewrites.

    Only the matched span is replaced — the rest of the question is untouched, so
    the retrieval/answer path still sees the user's actual intent.
    """
    out = text or ""
    n = 0
    for rule_id in sorted(DEFAULT_PII_REDACTABLE_RULE_IDS):
        pattern = get_default_compiled(rule_id)
        if pattern is None:
            continue
        out, hits = pattern.subn(DEFAULT_PII_REDACT_MASK, out)
        n += hits
    return out, n


# ---------------------------------------------------------------------------
# Grounding pre-check helpers (3)
# ---------------------------------------------------------------------------
def _grounding_substring_match(answer: str, chunk: str, min_len: int) -> bool:
    """Return True if any contiguous substring of *answer* with length ≥ *min_len*
    appears verbatim in *chunk*.  Used as a fast pre-check before LLM/NLI.

    Rationale: when the bot copies a fact like "0900111222" or an address
    directly from a chunk, even without a [chunk_id] marker the answer is
    factually grounded.  Scanning for a verbatim segment avoids a false
    ``grounding_fail`` that would otherwise fire on every short answer that
    does not contain citation brackets.
    """
    if not answer or not chunk or len(answer) < min_len:
        return False
    for i in range(len(answer) - min_len + 1):
        if answer[i : i + min_len] in chunk:
            return True
    return False


def _is_oos_refusal(
    answer: str, oos_template: str, similarity_threshold: float
) -> bool:
    """Return True when `answer` is substantially the bot's OOS refusal text.

    Compares case-insensitive Jaccard similarity on whitespace-tokenised word
    sets. Exact match short-circuits to True.
    """
    a = (answer or "").strip()
    t = (oos_template or "").strip()
    if not a or not t:
        return False
    if a == t:
        return True
    a_words = set(a.lower().split())
    t_words = set(t.lower().split())
    if not a_words or not t_words:
        return False
    intersection = len(a_words & t_words)
    union = len(a_words | t_words)
    if union == 0:
        return False
    return (intersection / union) >= similarity_threshold


def _extract_numbers(text: str) -> set[str]:
    """Extract all digit-only tokens ≥ 2 chars from *text*.

    Covers Vietnamese phone numbers (10 digits), prices (3–7 digits), dates,
    hotlines, etc.  Returns a ``set`` so the caller can do subset-check in O(n).
    """
    return set(re.findall(r"\d{2,}", text))


# ---------------------------------------------------------------------------
# Output rules
# ---------------------------------------------------------------------------
class OutputGuardrail:
    """Pure-function output rule evaluators."""

    @staticmethod
    def system_prompt_leak(
        answer: str,
        system_prompt_hash: str | list[str] | None,
        shingle_size: int = DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE,
        *,
        oos_template: str | None = None,
        oos_similarity_threshold: float = DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD,
        min_match_count: int = DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT,
    ) -> GuardrailHit | None:
        """Check if `answer` leaks any known hashed n-gram of system prompt.

        Caller pre-hashes system-prompt n-grams (sha256 of each shingle).
        We hash same-size shingles of `answer` and intersect.

        Skip detection when `answer` is substantially the bot's
        ``oos_answer_template`` — the refusal text shares vocabulary with
        ``system_prompt`` (per-bot phrasing) and would otherwise produce
        shingle-collision false positives.

        Block only when at least ``min_match_count`` shingles match. The guard
        targets prompt EXTRACTION (a sysprompt dump reproduces dozens of
        contiguous shingles); a single customer-facing refusal/persona sentence
        the owner wrote in the sysprompt yields only a few and is NOT a leak —
        blocking it would replace a graceful refusal with a generic template.
        """
        if not system_prompt_hash or not answer:
            return None
        if oos_template and _is_oos_refusal(
            answer, oos_template, oos_similarity_threshold
        ):
            return None
        known: set[str] = (
            {system_prompt_hash}
            if isinstance(system_prompt_hash, str)
            else set(system_prompt_hash)
        )
        words = answer.split()
        if len(words) < shingle_size:
            return None
        found: list[str] = []
        for i in range(0, len(words) - shingle_size + 1):
            shingle = " ".join(words[i : i + shingle_size])
            h = hashlib.sha256(shingle.encode("utf-8")).hexdigest()
            if h in known:
                found.append(h)
        if len(found) >= max(1, min_match_count):
            return GuardrailHit(
                rule_id="system_leak",
                severity="block",
                action="block",
                details={"match_count": len(found)},
            )
        return None

    @staticmethod
    def secret_scanner(answer: str) -> GuardrailHit | None:
        pattern = get_default_compiled("secret_leak")
        if pattern is None:
            return None
        matches = pattern.findall(answer or "")
        if matches:
            return GuardrailHit(
                rule_id="secret_leak",
                severity="block",
                action="block",
                details={"match_count": len(matches)},
            )
        return None

    @staticmethod
    def grounding_check(
        answer: str,
        retrieved_chunks: list[Any] | None,
        *,
        substring_min: int = DEFAULT_GROUNDING_SUBSTRING_MIN,
        numeric_overlap_enabled: bool = DEFAULT_GROUNDING_NUMERIC_OVERLAP_ENABLED,
    ) -> GuardrailHit | None:
        """Grounding check: answer must be anchored to retrieved chunks.

        Pass order (first pass = grounded, no further checks):
        1. Citation marker ([chunk_id]) present in answer → grounded.
        2. Substring pre-check: any ≥substring_min-char verbatim span of the
           answer appears in any chunk → grounded (catches direct quotes, phone
           numbers, addresses copied without a bracket marker).
        3. Numeric-overlap pre-check: every digit-sequence ≥2 chars found in
           the answer is present in at least one chunk → grounded (catches
           hotlines, prices, dates that are inherently factual).
        4. None of the above → fire grounding_fail WARN.

        This eliminates false positives such as Q6 "0900111222" where the
        answer IS grounded but the bot emits no citation bracket.
        """
        if not retrieved_chunks:
            return None
        answer = answer or ""
        # Pass 1 — citation marker
        if _CITATION_MARKER_RE.search(answer):
            return None
        # Pass 2 — substring verbatim match
        chunk_texts = [c.get("text") or c.get("content") or "" for c in retrieved_chunks]
        if any(_grounding_substring_match(answer, ct, substring_min) for ct in chunk_texts):
            return None
        # Pass 3 — numeric token overlap (only when enabled)
        if numeric_overlap_enabled:
            nums_answer = _extract_numbers(answer)
            if nums_answer:
                nums_chunks: set[str] = set()
                for ct in chunk_texts:
                    nums_chunks.update(_extract_numbers(ct))
                if nums_answer.issubset(nums_chunks):
                    return None
        return GuardrailHit(
            rule_id="grounding_fail",
            severity="warn",
            action="hitl",
            details={"retrieved_count": len(retrieved_chunks)},
        )

    @staticmethod
    async def llm_grounding_check(
        answer: str,
        context_chunks: list[dict],
        llm_complete_fn: Callable[..., Coroutine[Any, Any, dict]] | None = None,
        *,
        max_sentences: int = 5,
        threshold: float = 0.3,
        structured_judge_fn: (
            Callable[..., Coroutine[Any, Any, GroundingVerdictsOutput | None]] | None
        ) = None,
        use_structured: bool = DEFAULT_GROUNDING_USE_STRUCTURED,
    ) -> GuardrailHit | None:
        """Verify each answer sentence is supported by retrieved context.

        Two execution paths:

        - Structured (preferred): ``structured_judge_fn`` receives the
          messages list + ``GroundingVerdictsOutput`` schema and returns a
          validated Pydantic instance. Locale-proof — the schema enforces
          the literal verdict values regardless of model temperament.
        - Legacy text parse: ``llm_complete_fn`` returns a free-form ``text``
          and the function regex-parses ``"N. SUPPORTED"`` /
          ``"N. NOT_SUPPORTED"`` lines. Kept as fallback for callers without
          a structured router or when ``use_structured=False``.

        Returns ``GuardrailHit`` when the unsupported ratio strictly exceeds
        ``threshold``; otherwise ``None``.
        """
        if not answer or not context_chunks:
            return None
        if structured_judge_fn is None and llm_complete_fn is None:
            return None

        # Split into sentences and take first N
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(answer) if s.strip()]
        if not sentences:
            return None
        sentences = sentences[:max_sentences]

        # Build context block from chunks. Whole-doc / tabular chunks bypass
        # the per-chunk truncation — judge needs full text to verify claims.
        context_parts: list[str] = []
        for c in context_chunks:
            meta = c.get("metadata") or {}
            is_full_doc = c.get("is_full_document") or meta.get("is_full_document", False)
            raw = c.get("text") or c.get("content") or ""
            text = raw if is_full_doc else raw[:DEFAULT_GROUNDING_CONTEXT_PREVIEW_CHARS]
            if text:
                context_parts.append(text)
        if not context_parts:
            return None
        context_block = "\n---\n".join(context_parts)

        # Build numbered sentence list (claim_index = i, 0-based for schema).
        sentence_list = "\n".join(
            f"{i}. {s}" for i, s in enumerate(sentences)
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a grounding verifier. Given reference context and a "
                    "numbered list of claims, decide for each claim whether the "
                    "context supports it. Reply in English only with the literal "
                    "values SUPPORTED or NOT_SUPPORTED. Do not translate. Use "
                    "claim_index matching the input numbering (0-based)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Context:\n{context_block}\n\n"
                    f"Claims:\n{sentence_list}\n\n"
                    "Return one verdict per claim."
                ),
            },
        ]

        use_structured_path = bool(use_structured) and structured_judge_fn is not None

        try:
            if use_structured_path:
                checked, unsupported = await OutputGuardrail._run_structured_judge(
                    structured_judge_fn=structured_judge_fn,
                    messages=messages,
                    n_sentences=len(sentences),
                )
            else:
                checked, unsupported = await OutputGuardrail._run_text_parse_judge(
                    llm_complete_fn=llm_complete_fn,
                    messages=messages,
                    n_sentences=len(sentences),
                )
        except (AttributeError, TypeError, KeyError):
            # Programmer errors must not be masked as "answer passes grounding".
            raise
        except Exception:  # noqa: BLE001 — judge LLM failure must not crash pipeline
            # Judge died → answer passes UNVERIFIED. Count it so a rising
            # degraded rate is distinguishable from a clean PASS (P2-E 🐛-3) —
            # otherwise the HALLU net is silently OFF.
            grounding_degraded_total.labels(reason="error").inc()
            _logger.warning("llm_grounding_check_error", exc_info=True)
            return None

        if checked == 0:
            # Judge returned no checkable claims → also a pass-through, not a
            # verified PASS. Surface it (the empty path was previously silent).
            grounding_degraded_total.labels(reason="empty").inc()
            _logger.info("grounding_check_degraded", reason="empty")
            return None

        ratio = unsupported / checked
        _logger.info(
            "grounding_check_result",
            checked=checked,
            unsupported=unsupported,
            ratio=round(ratio, 3),
            threshold=threshold,
            path="structured" if use_structured_path else "legacy",
        )

        if ratio > threshold:
            grounding_fail_total.inc()
            return GuardrailHit(
                rule_id="llm_grounding_fail",
                severity="warn",
                action="hitl",
                details={
                    "checked": checked,
                    "unsupported": unsupported,
                    "ratio": round(ratio, 3),
                    "threshold": threshold,
                    "path": "structured" if use_structured_path else "legacy",
                },
            )
        return None

    @staticmethod
    async def _run_structured_judge(
        *,
        structured_judge_fn: Callable[..., Coroutine[Any, Any, GroundingVerdictsOutput | None]],
        messages: list[dict],
        n_sentences: int,
    ) -> tuple[int, int]:
        """Invoke the structured grounding judge; return (checked, unsupported)."""
        import asyncio as _asyncio

        try:
            parsed = await _asyncio.wait_for(
                structured_judge_fn(messages, GroundingVerdictsOutput),
                timeout=DEFAULT_GUARDRAIL_TIMEOUT_S,
            )
        except _asyncio.TimeoutError:
            _logger.warning("grounding_check_timeout", path="structured")
            return 0, 0

        if parsed is None or not parsed.verdicts:
            return 0, 0

        seen_indices: set[int] = set()
        unsupported = 0
        for verdict in parsed.verdicts:
            if verdict.claim_index < 0 or verdict.claim_index >= n_sentences:
                continue
            if verdict.claim_index in seen_indices:
                continue
            seen_indices.add(verdict.claim_index)
            if verdict.verdict == "NOT_SUPPORTED":
                unsupported += 1
        return len(seen_indices), unsupported

    @staticmethod
    async def _run_text_parse_judge(
        *,
        llm_complete_fn: Callable[..., Coroutine[Any, Any, dict]] | None,
        messages: list[dict],
        n_sentences: int,
    ) -> tuple[int, int]:
        """Text-parse grounding judge; returns (checked, unsupported)."""
        import asyncio as _asyncio

        if llm_complete_fn is None:
            return 0, 0

        try:
            result = await _asyncio.wait_for(
                llm_complete_fn(messages),
                timeout=DEFAULT_GUARDRAIL_TIMEOUT_S,
            )
        except _asyncio.TimeoutError:
            _logger.warning("grounding_check_timeout", path="legacy")
            return 0, 0

        response_text = (result.get("text") or "").upper()
        unsupported = 0
        checked = 0
        for i in range(n_sentences):
            for line in response_text.split("\n"):
                line_stripped = line.strip()
                # Match both 0-based "0." (new prompt) and 1-based "1." (old)
                if (
                    line_stripped.startswith(f"{i}.")
                    or line_stripped.startswith(f"{i} ")
                    or line_stripped.startswith(f"{i + 1}.")
                    or line_stripped.startswith(f"{i + 1} ")
                ):
                    checked += 1
                    if "NOT_SUPPORTED" in line_stripped:
                        unsupported += 1
                    break
        return checked, unsupported


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class LocalGuardrail(GuardrailPort):
    """Orchestrator: runs all input/output rules, logs to repo, raises on block.

    `guardrail_repository` is optional (may be None in unit tests).
    """

    def __init__(
        self,
        guardrail_repository: Any = None,
        *,
        max_input_length: int = DEFAULT_GUARDRAIL_MAX_INPUT_LENGTH,
        min_alpha_chars: int = DEFAULT_GUARDRAIL_MIN_ALPHA_CHARS,
        config_service: Any = None,
        rule_loader: "GuardrailRuleLoader | None" = None,
    ) -> None:
        self._repo = guardrail_repository
        self._max_input_length = max_input_length
        self._min_alpha_chars = min_alpha_chars
        self._cfg = config_service
        # Optional: when wired, check_input/check_output iterate the
        # loader's RuleSet (DB-driven). When None, fall back to the
        # static-method path which reads from the SSoT default-patterns
        # module — keeps legacy tests green without DB.
        self._loader = rule_loader

    async def _resolved_min_alpha(self) -> int:
        """Resolve at request time so system_config edits land without redeploy.

        Returns init default when config_service is unwired (test path).
        """
        if self._cfg is None:
            return self._min_alpha_chars
        try:
            return await self._cfg.get_int(
                "guardrail_min_alpha_chars", self._min_alpha_chars,
            )
        except Exception:  # noqa: BLE001 — config read fail = use init default
            return self._min_alpha_chars

    # ---- legacy GuardrailPort surface -----------------------------------
    async def moderate_input(
        self,
        text: str,
        *,
        record_tenant_id: TenantId,  # noqa: ARG002
    ) -> ModerationOutcome:
        if await self.detect_prompt_injection(text):
            return ModerationOutcome(
                kind="blocked",
                reason="prompt_injection_detected",
                categories=("injection",),
            )
        return ModerationOutcome(kind="safe")

    async def moderate_output(
        self,
        text: str,
        *,
        record_tenant_id: TenantId,  # noqa: ARG002
    ) -> ModerationOutcome:
        del text
        return ModerationOutcome(kind="safe")

    async def detect_prompt_injection(self, text: str) -> bool:
        if not text:
            return False
        # Prefer the loader's compiled view (tenant-scoped, DB-backed). When
        # no loader is wired (legacy unit tests) fall back to the SSoT
        # default-patterns module so behaviour is unchanged.
        #
        # NOTE: the loader handles its own DB/Redis/re.compile failures
        # (returns empty RuleSet, logs warning). We don't wrap the call
        # in another try/except — adding one would inflate the project's
        # broad-except budget without protecting anything the loader
        # doesn't already protect.
        if self._loader is not None:
            ruleset = await self._loader.get_rules(record_tenant_id=None)
            for rule in ruleset.input_rules:
                if rule.metadata.get("classic") is True and rule.pattern.search(text):
                    return True
            return False
        return any(p.search(text) for p in get_classic_injection_compiled())

    async def check_canary_leak(self, output: str, canary: str) -> bool:
        if not canary:
            return False
        return canary in output

    # ---- extended v0.3.0 surface ----------------------------------------
    async def _run_db_input_regex_rules(
        self,
        text: str,
        *,
        tenant_id: UUID | None,
    ) -> list[GuardrailHit] | None:
        """Loader-driven input regex evaluation.

        Returns the hits list (possibly empty) when the loader is wired
        and a RuleSet is available; ``None`` to signal the caller should
        fall back to the legacy static-method path.

        Rules with ``metadata['classic'] is True`` are skipped — they
        belong to ``detect_prompt_injection``, not ``check_input``.
        Classic prompt-injection coverage is preserved by the active
        ``prompt_injection`` rule (rule_id without ``classic`` flag).
        """
        if self._loader is None or not text:
            return None
        # Loader's own DB/Redis errors are absorbed by its internal try/except
        # and return an empty RuleSet. No extra wrapper here — see
        # detect_prompt_injection comment for rationale.
        ruleset = await self._loader.get_rules(record_tenant_id=tenant_id)
        hits: list[GuardrailHit] = []
        for rule in ruleset.input_rules:
            if rule.metadata.get("classic") is True:
                continue
            matches = rule.pattern.findall(text)
            if not matches:
                continue
            hits.append(
                GuardrailHit(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    action=rule.action,
                    details={"match_count": len(matches)},
                ),
            )
        return hits

    async def _run_db_output_regex_rules(
        self,
        answer: str,
        *,
        tenant_id: UUID | None,
    ) -> list[GuardrailHit] | None:
        """Loader-driven output regex evaluation (secret_leak, custom rules).

        Returns ``None`` to signal "no loader — caller falls back". The
        system-prompt-leak and grounding checks stay in the static path
        because they need extra parameters (system_prompt_hash, chunks)
        that don't map to a simple regex pattern row.
        """
        if self._loader is None or not answer:
            return None
        # See _run_db_input_regex_rules comment — loader handles its
        # own faults internally; no extra wrapper needed here.
        ruleset = await self._loader.get_rules(record_tenant_id=tenant_id)
        hits: list[GuardrailHit] = []
        for rule in ruleset.output_rules:
            matches = rule.pattern.findall(answer)
            if not matches:
                continue
            hits.append(
                GuardrailHit(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    action=rule.action,
                    details={"match_count": len(matches)},
                ),
            )
        return hits

    async def check_input(
        self,
        text: str,
        *,
        tenant_id: UUID | None,
        message_id: int,
        request_id: UUID | None = None,
    ) -> list[GuardrailHit]:
        # min_alpha=0 disables too_short — short/empty queries pass through to
        # LLM so the bot owner's sysprompt handles them per chitchat rule.
        _min_alpha = await self._resolved_min_alpha()
        too_short_hit = (
            InputGuardrail.too_short(text, min_alpha=_min_alpha)
            if _min_alpha > 0
            else None
        )
        length_hit = InputGuardrail.length_limit(text, max_len=self._max_input_length)
        # When the loader is wired, the regex-based rules come from DB
        # (tenant override + platform default). Otherwise fall back to
        # the static-method path so legacy callers / unit tests that
        # build LocalGuardrail without a loader keep working.
        db_hits = await self._run_db_input_regex_rules(text, tenant_id=tenant_id)
        if db_hits is None:
            db_hits = [
                h
                for h in (
                    InputGuardrail.prompt_injection_patterns(text),
                    InputGuardrail.pii_vi(text),
                    InputGuardrail.pii_en(text),
                    InputGuardrail.sql_injection(text),
                )
                if h is not None
            ]
        hits = [h for h in (too_short_hit, length_hit) if h is not None]
        hits.extend(db_hits)
        for h in hits:
            try:
                guardrail_triggered_total.labels(
                    rule_id=h.rule_id, severity=h.severity, action=h.action,
                ).inc()
            except Exception:  # noqa: BLE001
                pass
        await self._persist(
            hits,
            guardrail_type="input",
            tenant_id=tenant_id,
            message_id=message_id,
            request_id=request_id,
        )
        if any(h.severity == "block" for h in hits):
            raise GuardrailBlocked(hits)
        return hits

    async def check_output(
        self,
        answer: str,
        *,
        system_prompt_hash: str | list[str] | None = None,
        shingle_size: int = DEFAULT_GUARDRAIL_LEAK_SHINGLE_SIZE,
        retrieved_chunks: list[Any] | None = None,
        tenant_id: UUID | None,
        message_id: int,
        request_id: UUID | None = None,
        grounding_check_enabled: bool = False,
        grounding_check_threshold: float = 0.3,
        citation_marker_required: bool = False,
        llm_complete_fn: Callable[..., Coroutine[Any, Any, dict]] | None = None,
        structured_judge_fn: (
            Callable[..., Coroutine[Any, Any, Any]] | None
        ) = None,
        grounding_use_structured: bool = DEFAULT_GROUNDING_USE_STRUCTURED,
        oos_template: str | None = None,
        oos_similarity_threshold: float = DEFAULT_GUARDRAIL_OOS_SIMILARITY_THRESHOLD,
        leak_min_match_count: int = DEFAULT_GUARDRAIL_LEAK_MIN_MATCH_COUNT,
    ) -> list[GuardrailHit]:
        # The regex-based grounding_check enforces `[chunk_id]` markers
        # in the answer. Flow/template bots (spa consultation, sales,
        # guided script) don't emit brackets — that's UX, not a bug.
        # Gate this check on `citation_marker_required` (off by default).
        # Audit-heavy bots (legal, medical) flip it on.
        #
        # system_prompt_leak + grounding_check stay in the static path:
        # they need extra parameters (system_prompt_hash, retrieved_chunks)
        # that don't map to a simple regex-row schema.
        leak_hit = OutputGuardrail.system_prompt_leak(
            answer,
            system_prompt_hash,
            shingle_size=shingle_size,
            oos_template=oos_template,
            oos_similarity_threshold=oos_similarity_threshold,
            min_match_count=leak_min_match_count,
        )
        # When the loader is wired, secret_leak (and any future tenant
        # output regex rules) come from DB. Otherwise fall back.
        db_output_hits = await self._run_db_output_regex_rules(
            answer, tenant_id=tenant_id,
        )
        if db_output_hits is None:
            secret_hit = OutputGuardrail.secret_scanner(answer)
            db_output_hits = [secret_hit] if secret_hit is not None else []
        rule_results: list[GuardrailHit | None] = [leak_hit, *db_output_hits]
        if citation_marker_required:
            rule_results.append(OutputGuardrail.grounding_check(answer, retrieved_chunks))
        hits = [h for h in rule_results if h is not None]

        # LLM-based grounding check (feature-flagged). Structured path
        # preferred; falls back to legacy text-parse when no structured
        # callable is available or the bot owner disabled the structured
        # toggle.
        _has_judge_fn = llm_complete_fn is not None or structured_judge_fn is not None
        if grounding_check_enabled and _has_judge_fn:
            llm_hit = await OutputGuardrail.llm_grounding_check(
                answer,
                retrieved_chunks or [],
                llm_complete_fn,
                threshold=grounding_check_threshold,
                structured_judge_fn=structured_judge_fn,
                use_structured=grounding_use_structured,
            )
            if llm_hit is not None:
                hits.append(llm_hit)
        for h in hits:
            try:
                guardrail_triggered_total.labels(
                    rule_id=h.rule_id, severity=h.severity, action=h.action,
                ).inc()
                if h.rule_id == "grounding_fail":
                    grounding_fail_total.inc()
            except Exception:  # noqa: BLE001
                pass
        await self._persist(
            hits,
            guardrail_type="output",
            tenant_id=tenant_id,
            message_id=message_id,
            request_id=request_id,
        )
        if any(h.severity == "block" for h in hits):
            raise GuardrailBlocked(hits)
        return hits

    async def _persist(
        self,
        hits: list[GuardrailHit],
        *,
        guardrail_type: str,
        tenant_id: UUID | None,
        message_id: int,
        request_id: UUID | None,
    ) -> None:
        if not hits or self._repo is None:
            return
        for h in hits:
            try:
                await self._repo.insert(
                    {
                        "message_id": message_id,
                        "tenant_id": tenant_id,
                        "request_id": request_id,
                        "guardrail_type": guardrail_type,
                        "rule_id": h.rule_id,
                        "severity": h.severity,
                        "action_taken": h.action,
                        "details": h.details,
                    }
                )
            except Exception as exc:  # noqa: BLE001 — persist is best-effort, must not block the pipeline
                # A silently-swallowed guardrail_events INSERT makes a
                # compliance audit read 0 events and wrongly conclude the bot
                # was unguarded. Log the failure (never block the pipeline).
                _logger.warning(
                    "guardrail_persist_failed",
                    rule_id=h.rule_id,
                    guardrail_type=guardrail_type,
                    error_type=type(exc).__name__,
                )


__all__ = [
    "GuardrailBlocked",
    "GuardrailHit",
    "InputGuardrail",
    "LocalGuardrail",
    "OutputGuardrail",
]
