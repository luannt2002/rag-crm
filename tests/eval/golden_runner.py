"""Vertical-agnostic golden test runner.

The runner is intentionally generic: it knows nothing about the domain of
the questions it is evaluating. The caller supplies:

1. ``vertical`` — opaque string used to locate the fixture directory.
2. ``fixtures_dir`` — root path where ``<vertical>/questions.yaml`` lives.
3. ``bot_3key`` — the (tenant_id, bot_id, channel_type) triple per the
   project's mandatory 3-key identity rule.
4. ``thresholds`` — optional overrides for the floor block.

The runner loads the fixture YAML, sends each question to the chat
endpoint, scores the response against the per-question rubric (keyword
presence + hallucination guard), and computes aggregate metrics. It then
exposes ``assert_meets_floor()`` which raises if any metric is below the
configured floor.

This module imports nothing from ``ragbot`` production code. It only
talks to ragbot through HTTP, so it can be reused against any deployment
or replaced with a mock transport for unit tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx
import yaml

from tests.eval.constants import (
    DEFAULT_GOLDEN_CHAT_PATH,
    DEFAULT_GOLDEN_FAITH_FLOOR,
    DEFAULT_GOLDEN_HALLU_FLOOR,
    DEFAULT_GOLDEN_LATENCY_PERCENTILE,
    DEFAULT_GOLDEN_MS_PER_SECOND,
    DEFAULT_GOLDEN_P95_FLOOR_MS,
    DEFAULT_GOLDEN_PASS_RATE_FLOOR,
    DEFAULT_GOLDEN_REQUEST_TIMEOUT_S,
    DEFAULT_GOLDEN_TOP_SCORE_FLOOR,
)


class EvalFloorViolation(AssertionError):
    """Raised when a golden run is below at least one configured floor."""


@dataclass(frozen=True)
class QuestionResult:
    """Outcome of running a single fixture question."""

    intent: str
    text: str
    answer: str
    passed: bool
    faithfulness: float
    top_score: float
    latency_ms: float
    hallucinated: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalResult:
    """Aggregate metrics across all questions in a vertical fixture."""

    vertical: str
    total: int
    passed: int
    pass_rate: float
    mean_faithfulness: float
    mean_top_score: float
    p_latency_ms: float
    hallu_count: int
    floor: Mapping[str, float]
    per_question: tuple[QuestionResult, ...] = field(default_factory=tuple)

    def shortfalls(self) -> list[str]:
        """Return a list of human-readable floor violations (empty = OK)."""

        problems: list[str] = []
        floor = self.floor
        if self.pass_rate < float(floor["pass_rate"]):
            problems.append(
                f"pass_rate={self.pass_rate:.3f} < floor={float(floor['pass_rate']):.3f}"
            )
        if self.mean_faithfulness < float(floor["faithfulness"]):
            problems.append(
                f"faithfulness={self.mean_faithfulness:.3f} < floor={float(floor['faithfulness']):.3f}"
            )
        if self.mean_top_score < float(floor["top_score"]):
            problems.append(
                f"top_score={self.mean_top_score:.3f} < floor={float(floor['top_score']):.3f}"
            )
        if self.p_latency_ms > float(floor["p95_ms"]):
            problems.append(
                f"p95_ms={self.p_latency_ms:.0f} > floor={float(floor['p95_ms']):.0f}"
            )
        if self.hallu_count > int(floor["hallu"]):
            problems.append(
                f"hallu={self.hallu_count} > floor={int(floor['hallu'])}"
            )
        return problems


class GoldenTestRunner:
    """Drive a vertical fixture through ragbot and score the responses.

    Parameters
    ----------
    vertical:
        Opaque vertical identifier. Used only to locate the fixture file at
        ``<fixtures_dir>/<vertical>/questions.yaml``.
    fixtures_dir:
        Directory holding ``<vertical>/questions.yaml`` files.
    bot_3key:
        ``(tenant_id, bot_id, channel_type)`` — all three are required per
        project identity rule.
    base_url:
        Base URL of the ragbot HTTP service (e.g. ``http://localhost:3004``).
    chat_path:
        Path of the chat endpoint. Defaults to the project's test chat path.
    thresholds:
        Optional override of fixture floor block. Keys: ``pass_rate``,
        ``faithfulness``, ``top_score``, ``p95_ms``, ``hallu``.
    transport:
        Optional ``httpx.BaseTransport`` for tests (e.g. ``MockTransport``).
    request_timeout_s:
        Per-request timeout.
    """

    def __init__(
        self,
        *,
        vertical: str,
        fixtures_dir: Path,
        bot_3key: tuple[int, str, str],
        base_url: str,
        chat_path: str = DEFAULT_GOLDEN_CHAT_PATH,
        thresholds: Mapping[str, float] | None = None,
        transport: httpx.BaseTransport | None = None,
        request_timeout_s: float = DEFAULT_GOLDEN_REQUEST_TIMEOUT_S,
    ) -> None:
        if not vertical or not isinstance(vertical, str):
            raise ValueError("vertical must be a non-empty string")
        tenant_id, bot_id, channel_type = bot_3key
        if not isinstance(tenant_id, int):
            raise ValueError("bot_3key[0] tenant_id must be int")
        if not isinstance(bot_id, str) or not bot_id:
            raise ValueError("bot_3key[1] bot_id must be non-empty str")
        if not isinstance(channel_type, str) or not channel_type:
            raise ValueError("bot_3key[2] channel_type must be non-empty str")
        self._vertical = vertical
        self._fixtures_dir = Path(fixtures_dir)
        self._bot_3key = (tenant_id, bot_id, channel_type)
        self._base_url = base_url.rstrip("/")
        self._chat_path = chat_path
        self._threshold_override = dict(thresholds) if thresholds else {}
        self._transport = transport
        self._request_timeout_s = request_timeout_s
        self._fixture_cache: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Fixture loading
    # ------------------------------------------------------------------
    @property
    def fixture_path(self) -> Path:
        return self._fixtures_dir / self._vertical / "questions.yaml"

    def load_fixture(self) -> dict[str, Any]:
        """Load and cache the fixture YAML for this vertical."""

        if self._fixture_cache is not None:
            return self._fixture_cache
        path = self.fixture_path
        if not path.is_file():
            raise FileNotFoundError(f"fixture not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"fixture root must be a mapping, got {type(data).__name__}")
        if data.get("vertical") and data["vertical"] != self._vertical:
            raise ValueError(
                f"fixture declares vertical={data['vertical']!r} but runner expected {self._vertical!r}"
            )
        questions = data.get("questions") or []
        if not isinstance(questions, list) or not questions:
            raise ValueError("fixture must contain a non-empty 'questions' list")
        self._fixture_cache = data
        return data

    def _effective_floor(self) -> dict[str, float]:
        """Merge fixture ``floor`` block with constructor overrides + defaults."""

        fixture_floor: Mapping[str, Any] = self.load_fixture().get("floor") or {}
        merged: dict[str, float] = {
            "pass_rate": float(
                fixture_floor.get("pass_rate", DEFAULT_GOLDEN_PASS_RATE_FLOOR)
            ),
            "faithfulness": float(
                fixture_floor.get("faithfulness", DEFAULT_GOLDEN_FAITH_FLOOR)
            ),
            "top_score": float(
                fixture_floor.get("top_score", DEFAULT_GOLDEN_TOP_SCORE_FLOOR)
            ),
            "p95_ms": float(
                fixture_floor.get("p95_ms", DEFAULT_GOLDEN_P95_FLOOR_MS)
            ),
            "hallu": float(fixture_floor.get("hallu", DEFAULT_GOLDEN_HALLU_FLOOR)),
        }
        for key, value in self._threshold_override.items():
            if key in merged:
                merged[key] = float(value)
        return merged

    # ------------------------------------------------------------------
    # Per-question scoring (vertical-agnostic)
    # ------------------------------------------------------------------
    @staticmethod
    def _matches_any(answer: str, keywords: list[str]) -> bool:
        if not keywords:
            return True  # no required keywords = nothing to match
        haystack = answer.casefold()
        return any(kw.casefold() in haystack for kw in keywords if kw)

    @staticmethod
    def _matches_none(answer: str, banned: list[str]) -> bool:
        if not banned:
            return True
        haystack = answer.casefold()
        return not any(kw.casefold() in haystack for kw in banned if kw)

    @classmethod
    def _score_question(
        cls, question: Mapping[str, Any], response: Mapping[str, Any]
    ) -> tuple[bool, bool, list[str]]:
        """Return (passed, hallucinated, reasons)."""

        answer = str(response.get("answer", "") or "")
        reasons: list[str] = []
        expected = list(question.get("expected_keywords_any") or [])
        banned = list(question.get("must_not_contain") or [])

        keyword_ok = cls._matches_any(answer, expected)
        if not keyword_ok:
            reasons.append("missing_expected_keywords_any")
        clean_ok = cls._matches_none(answer, banned)
        hallucinated = not clean_ok
        if hallucinated:
            reasons.append("contains_banned_terms")
        passed = keyword_ok and clean_ok
        return passed, hallucinated, reasons

    # ------------------------------------------------------------------
    # HTTP transport
    # ------------------------------------------------------------------
    def _build_payload(self, question_text: str) -> dict[str, Any]:
        tenant_id, bot_id, channel_type = self._bot_3key
        return {
            "tenant_id": tenant_id,
            "bot_id": bot_id,
            "channel_type": channel_type,
            "message": question_text,
        }

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "base_url": self._base_url,
            "timeout": self._request_timeout_s,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _post_one(
        self, client: httpx.Client, question: Mapping[str, Any]
    ) -> tuple[QuestionResult, float, float]:
        payload = self._build_payload(str(question.get("text", "")))
        try:
            resp = client.post(self._chat_path, json=payload)
            resp.raise_for_status()
            body = resp.json() if resp.content else {}
        except httpx.HTTPError as exc:
            qr = QuestionResult(
                intent=str(question.get("intent", "")),
                text=str(question.get("text", "")),
                answer="",
                passed=False,
                faithfulness=0.0,
                top_score=0.0,
                latency_ms=float(self._request_timeout_s) * float(DEFAULT_GOLDEN_MS_PER_SECOND),
                hallucinated=False,
                reasons=(f"http_error:{type(exc).__name__}",),
            )
            return qr, 0.0, 0.0
        if not isinstance(body, dict):
            body = {}
        passed, hallucinated, reasons = self._score_question(question, body)
        latency_ms = float(body.get("latency_ms", 0.0) or 0.0)
        faith = float(body.get("faithfulness", 0.0) or 0.0)
        top_score = float(body.get("top_score", 0.0) or 0.0)
        qr = QuestionResult(
            intent=str(question.get("intent", "")),
            text=str(question.get("text", "")),
            answer=str(body.get("answer", "") or ""),
            passed=passed,
            faithfulness=faith,
            top_score=top_score,
            latency_ms=latency_ms,
            hallucinated=hallucinated,
            reasons=tuple(reasons),
        )
        return qr, faith, top_score

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        if len(ordered) == 1:
            return float(ordered[0])
        rank = (percentile / float(100)) * (len(ordered) - 1)
        lo = math.floor(rank)
        hi = math.ceil(rank)
        if lo == hi:
            return float(ordered[lo])
        frac = rank - lo
        return float(ordered[lo]) * (1.0 - frac) + float(ordered[hi]) * frac

    def run(self) -> EvalResult:
        """Execute the fixture against ragbot and return aggregate metrics."""

        fixture = self.load_fixture()
        questions: list[Mapping[str, Any]] = list(fixture.get("questions") or [])
        per_question: list[QuestionResult] = []
        faith_values: list[float] = []
        top_values: list[float] = []
        latencies: list[float] = []
        hallu_count = 0
        passed = 0

        with self._client() as client:
            for q in questions:
                qr, faith, top_score = self._post_one(client, q)
                per_question.append(qr)
                faith_values.append(faith)
                top_values.append(top_score)
                latencies.append(qr.latency_ms)
                if qr.passed:
                    passed += 1
                if qr.hallucinated:
                    hallu_count += 1

        total = len(per_question)
        pass_rate = (passed / total) if total else 0.0
        mean_faith = (sum(faith_values) / total) if total else 0.0
        mean_top = (sum(top_values) / total) if total else 0.0
        p_latency = self._percentile(latencies, DEFAULT_GOLDEN_LATENCY_PERCENTILE)

        return EvalResult(
            vertical=self._vertical,
            total=total,
            passed=passed,
            pass_rate=pass_rate,
            mean_faithfulness=mean_faith,
            mean_top_score=mean_top,
            p_latency_ms=p_latency,
            hallu_count=hallu_count,
            floor=self._effective_floor(),
            per_question=tuple(per_question),
        )

    def assert_meets_floor(self, result: EvalResult | None = None) -> EvalResult:
        """Run (if needed) and raise ``EvalFloorViolation`` if any floor missed."""

        outcome = result if result is not None else self.run()
        problems = outcome.shortfalls()
        if problems:
            joined = "; ".join(problems)
            raise EvalFloorViolation(
                f"vertical={outcome.vertical!r} floor violations: {joined}"
            )
        return outcome
