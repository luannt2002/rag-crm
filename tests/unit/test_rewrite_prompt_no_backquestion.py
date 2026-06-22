"""Tests for rewrite prompt — verifies prompt_rewriter does NOT generate back-questions.

Regression guard: prompt_rewriter must instruct LLM to output search keywords only,
never clarification questions like "Bạn muốn biết điều gì..." or "Bạn có muốn...".
"""
from __future__ import annotations

import pytest

from ragbot.shared.i18n import get_pack


class TestRewritePromptNoBakQuestion:
    """Verify VI rewrite prompt enforces keyword-only output, no clarification questions."""

    def setup_method(self):
        self.vi_pack = get_pack("vi")
        self.en_pack = get_pack("en")

    # ------------------------------------------------------------------
    # Test 1: prompt must explicitly forbid back-questions
    # ------------------------------------------------------------------
    def test_vi_rewrite_prompt_forbids_back_questions(self):
        """VI prompt_rewriter must contain explicit prohibition on asking user back."""
        prompt = self.vi_pack.prompt_rewriter
        # Must contain "KHÔNG hỏi lại" or similar explicit prohibition
        assert "KHÔNG hỏi lại" in prompt or "KHÔNG generate câu hỏi" in prompt, (
            "VI prompt_rewriter must explicitly forbid asking back / clarification questions. "
            f"Got: {prompt[:200]}"
        )

    def test_en_rewrite_prompt_forbids_back_questions(self):
        """EN prompt_rewriter must contain explicit prohibition on asking user back."""
        prompt = self.en_pack.prompt_rewriter
        assert "Do NOT ask" in prompt or "do NOT ask" in prompt, (
            "EN prompt_rewriter must explicitly forbid asking back. "
            f"Got: {prompt[:200]}"
        )

    # ------------------------------------------------------------------
    # Test 2: prompt must instruct keyword/phrase output only
    # ------------------------------------------------------------------
    def test_vi_rewrite_prompt_instructs_keyword_output(self):
        """VI prompt_rewriter must instruct output to be keywords or short search phrase."""
        prompt = self.vi_pack.prompt_rewriter
        assert "KEYWORDS" in prompt or "keyword" in prompt.lower() or "cụm từ tìm kiếm" in prompt, (
            "VI prompt_rewriter must instruct LLM to output keywords / search phrase. "
            f"Got: {prompt[:200]}"
        )

    def test_en_rewrite_prompt_instructs_keyword_output(self):
        """EN prompt_rewriter must instruct output to be keywords or short search phrase."""
        prompt = self.en_pack.prompt_rewriter
        assert "keywords" in prompt.lower() or "search phrase" in prompt.lower(), (
            "EN prompt_rewriter must instruct LLM to output keywords / search phrase. "
            f"Got: {prompt[:200]}"
        )

    # ------------------------------------------------------------------
    # Test 3: prompt must NOT contain HyDE-only instruction (which was the old bug)
    # ------------------------------------------------------------------
    def test_vi_rewrite_prompt_not_only_hyde(self):
        """Old prompt was just 'Viet lai HyDE' with no explicit anti-backquestion rule.

        New prompt must be more than a one-liner HyDE instruction.
        """
        prompt = self.vi_pack.prompt_rewriter
        # Old value: "Viet lai cau hoi nguoi dung o dang HyDE ngan gon."
        assert len(prompt) > 100, (
            "VI prompt_rewriter is suspiciously short — likely reverted to old one-liner. "
            f"Length={len(prompt)}, content={prompt[:100]}"
        )

    def test_en_rewrite_prompt_not_only_hyde(self):
        """Old EN prompt was just 'Rewrite the user's question in a concise HyDE format.'"""
        prompt = self.en_pack.prompt_rewriter
        assert len(prompt) > 80, (
            "EN prompt_rewriter is suspiciously short — likely reverted to old one-liner. "
            f"Length={len(prompt)}, content={prompt[:100]}"
        )

    # ------------------------------------------------------------------
    # Test 4: prompt must contain at least one concrete example
    # ------------------------------------------------------------------
    def test_vi_rewrite_prompt_has_examples(self):
        """Prompt with concrete few-shot examples is more robust against LLM drift."""
        prompt = self.vi_pack.prompt_rewriter
        assert "User:" in prompt and "Output:" in prompt, (
            "VI prompt_rewriter should contain at least one few-shot example (User: ... → Output: ...). "
            f"Got: {prompt[:200]}"
        )

    def test_en_rewrite_prompt_has_examples(self):
        """EN prompt must also contain few-shot examples."""
        prompt = self.en_pack.prompt_rewriter
        assert "User:" in prompt and "Output:" in prompt, (
            "EN prompt_rewriter should contain at least one few-shot example (User: ... → Output: ...). "
            f"Got: {prompt[:200]}"
        )


class TestRewritePromptFewShotExamples:
    """Verify the few-shot examples in the VI prompt are syntactically correct."""

    def setup_method(self):
        self.vi_pack = get_pack("vi")

    def test_vi_examples_map_filler_removal(self):
        """Domain-neutral filler-removal example must be present (M24: no salon literal).

        The few-shot demonstrates stripping filler ('Anh ơi cho em hỏi … nhé ạ?')
        down to a clean query. Pinned on the DOMAIN-NEUTRAL example 'giá gói cơ bản'
        — not a tenant service name — so the platform stays industry-agnostic.
        """
        prompt = self.vi_pack.prompt_rewriter
        assert "giá gói cơ bản" in prompt, (
            "VI prompt_rewriter should contain a domain-neutral filler-removal example "
            f"(e.g. 'giá gói cơ bản'). Got: {prompt[:300]}"
        )

    def test_vi_examples_map_superlative(self):
        """Example 'rẻ nhất' must be preserved in few-shot example."""
        prompt = self.vi_pack.prompt_rewriter
        assert "rẻ nhất" in prompt, (
            "VI prompt_rewriter should contain superlative example 'rẻ nhất'. "
            f"Got: {prompt[:300]}"
        )

    def test_vi_negative_examples_listed(self):
        """Prompt must list the exact back-question patterns to avoid."""
        prompt = self.vi_pack.prompt_rewriter
        # Either "Bạn muốn biết" or "Bạn có muốn" should appear as negative example
        assert "Bạn muốn biết" in prompt or "Bạn có muốn" in prompt, (
            "VI prompt_rewriter should explicitly show the bad patterns to avoid. "
            f"Got: {prompt[:300]}"
        )
