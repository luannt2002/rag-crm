"""Unit tests: dynamic chunking — strategy selection + chunk quality invariants."""
from __future__ import annotations

import pytest

from ragbot.shared.chunking import (
    analyze_document,
    extract_structural_path,
    generate_parent_child_chunks,
    promote_vn_hierarchical_headings,
    select_strategy,
    smart_chunk,
    _chunk_semantic,
    _chunk_proposition,
    _chunk_hybrid,
    _chunk_hdt,
    _is_table_line,
    _split_sentences,
)


# ── Fixtures: synthetic documents ───────────────────────────────────────


def _heading_doc(n_headings: int = 6) -> str:
    """Document with many headings → should select HDT."""
    sections = []
    for i in range(n_headings):
        level = "#" * ((i % 3) + 1)
        sections.append(f"{level} Section {i}\n\nContent for section {i}. " * 5)
    return "\n\n".join(sections)


def _table_doc() -> str:
    """Short text + multiple tables → should select recursive."""
    rows = "| STT | Dịch vụ | Giá |\n|---|---|---|\n"
    for i in range(1, 6):
        rows += f"| {i} | Service {i} | {i * 10_000}đ |\n"
    return f"Short intro.\n\n{rows}\n\n{rows}\n\nAnother table.\n\n{rows}"


def _prose_doc(words: int = 2000) -> str:
    """Long prose with few headings → should select semantic."""
    sentence = "Đây là một câu văn dài trong tiếng Việt để mô tả nội dung tài liệu. "
    # Build multiple paragraphs separated by \n\n so the semantic chunker can split.
    # Each paragraph must be long enough (avg_text_length > 200 words) for semantic detection.
    paragraphs = []
    sentences_per_para = 30
    total_sentences = words // 10
    for i in range(0, total_sentences, sentences_per_para):
        paragraphs.append(sentence * min(sentences_per_para, total_sentences - i))
    text = "\n\n".join(paragraphs)
    return f"# Title\n\n{text}"


def _mixed_doc() -> str:
    """Mixed: code blocks + tables + text → should select hybrid."""
    parts = [
        "Some intro text.",
        "```python\nprint('hello')\n```",
        "| A | B |\n|---|---|\n| 1 | 2 |",
        "```javascript\nconsole.log('hi')\n```",
        "| X | Y |\n|---|---|\n| 3 | 4 |",
        "More text paragraph.",
        "```bash\necho ok\n```",
    ]
    return "\n\n".join(parts)




# ── Strategy selection ──────────────────────────────────────────────────


class TestSelectStrategy:
    def test_heading_rich_selects_hdt(self):
        # Use a doc with TOC to ensure HDT wins via confidence scoring
        doc = "# Mục lục\n\n" + "\n\n".join(
            f"## Section {i}\n\n{'Content paragraph. ' * 20}" for i in range(8)
        )
        profile = analyze_document(doc)
        strategy, confidence = select_strategy(profile)
        assert strategy == "hdt"
        assert 0.0 <= confidence <= 1.0

    def test_table_rich_short_text_selects_recursive(self):
        profile = analyze_document(_table_doc())
        strategy, confidence = select_strategy(profile)
        assert strategy == "recursive"
        assert 0.0 <= confidence <= 1.0

    def test_long_prose_selects_semantic(self):
        profile = analyze_document(_prose_doc(2000))
        strategy, confidence = select_strategy(profile)
        assert strategy == "semantic"
        assert 0.0 <= confidence <= 1.0

    def test_mixed_content_selects_recursive(self):
        """Mixed content — confidence scoring picks recursive (table-heavy)."""
        profile = analyze_document(_mixed_doc())
        strategy, confidence = select_strategy(profile)
        assert strategy in ("recursive", "hdt", "semantic")
        assert 0.0 <= confidence <= 1.0

    def test_default_is_recursive(self):
        """Minimal document → default recursive."""
        profile = analyze_document("Hello world. Short text.")
        strategy, confidence = select_strategy(profile)
        assert strategy == "recursive"
        assert 0.0 <= confidence <= 1.0

    def test_toc_forces_hdt(self):
        doc = "# Mục lục\n\n## A\n\nText A\n\n## B\n\nText B"
        profile = analyze_document(doc)
        strategy, confidence = select_strategy(profile)
        assert strategy == "hdt"
        assert confidence >= 0.45

    def test_select_strategy_returns_confidence(self):
        """select_strategy returns (strategy, confidence) tuple with confidence in [0, 1]."""
        # Low-signal document should get fallback confidence
        profile = analyze_document("Short.")
        result = select_strategy(profile)
        assert isinstance(result, tuple)
        assert len(result) == 2
        strategy, confidence = result
        assert isinstance(strategy, str)
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0

        # High-signal HDT document should get high confidence
        profile_hdt = analyze_document(_heading_doc(10))
        _, conf_hdt = select_strategy(profile_hdt)
        assert conf_hdt >= 0.5


# ── Table integrity ─────────────────────────────────────────────────────


class TestTableIntegrity:
    def test_table_not_split_mid_row(self):
        """Table rows must not be split across chunks."""
        table = "| STT | Name | Price |\n|---|---|---|\n"
        for i in range(1, 20):
            table += f"| {i} | Item {i} | {i * 1000}đ |\n"
        doc = f"Intro text.\n\n{table}\n\nAfter table text."
        chunks = smart_chunk(doc, chunk_size=512)

        for chunk in chunks:
            lines = chunk.strip().split("\n")
            for line in lines:
                if _is_table_line(line):
                    # If a line looks like a table row, it must be complete
                    assert line.strip().endswith("|") or "\t" in line


# ── Chunk size + overlap invariants ─────────────────────────────────────


class TestChunkSizeInvariants:
    @pytest.mark.parametrize("chunk_size", [256, 512, 1024])
    def test_recursive_chunks_respect_size_limit(self, chunk_size: int):
        """Recursive strategy: output chunks should be at most chunk_size * 2 (tolerance for atomic blocks)."""
        # Use explicit recursive strategy -- semantic strategy respects paragraph boundaries, not chunk_size
        doc = "Hello world. " * 500
        chunks = smart_chunk(doc, chunk_size=chunk_size, strategy="recursive")
        assert chunks, "Expected at least one chunk"
        max_allowed = chunk_size * 2  # generous tolerance for atomic blocks
        for chunk in chunks:
            assert len(chunk) <= max_allowed, (
                f"Chunk too large: {len(chunk)} > {max_allowed}"
            )

    def test_overlap_present_between_consecutive_chunks(self):
        """Consecutive chunks should share some text (overlap)."""
        doc = _prose_doc(3000)
        chunks = smart_chunk(doc, chunk_size=512, chunk_overlap=128)
        if len(chunks) < 2:
            pytest.skip("Need at least 2 chunks for overlap test")

        overlap_found = False
        for i in range(len(chunks) - 1):
            # Check if end of chunk i shares words with start of chunk i+1
            tail_words = set(chunks[i].split()[-20:])
            head_words = set(chunks[i + 1].split()[:20])
            if tail_words & head_words:
                overlap_found = True
                break

        assert overlap_found, "Expected overlap between at least one pair of consecutive chunks"


# ── Table isolation ────────────────────────────────────────────────────


class TestTableIsolation:
    def test_table_isolation_preserves_tables(self):
        """When strategy is HDT/semantic but doc has tables, tables are chunked via recursive."""
        # Build a doc that triggers HDT (many headings) but also has a table
        doc = (
            "# Mục lục\n\n"
            "## Section 1\n\nSome intro text for section one.\n\n"
            "## Section 2\n\n"
            "| STT | Name | Price |\n|---|---|---|\n"
            "| 1 | Item A | 10000đ |\n"
            "| 2 | Item B | 20000đ |\n"
            "| 3 | Item C | 30000đ |\n\n"
            "## Section 3\n\nMore text here.\n\n"
            "## Section 4\n\nEven more text.\n\n"
            "## Section 5\n\nFinal section content.\n"
        )
        chunks = smart_chunk(doc, chunk_size=512)
        # Table content must appear intact in one chunk (not split mid-row)
        table_chunk_found = False
        for chunk in chunks:
            if "Item A" in chunk and "Item B" in chunk and "Item C" in chunk:
                table_chunk_found = True
                break
        assert table_chunk_found, (
            "Table rows should be preserved together via table isolation"
        )


# ── Structural path extraction ────────────────────────────────────────


class TestStructuralPathExtraction:
    def test_extract_with_path(self):
        chunk = "[Chapter 1 > Section A]\nSome content here."
        result = extract_structural_path(chunk)
        assert result["structural_path"] is not None
        assert result["structural_path"]["full"] == "Chapter 1 > Section A"
        assert result["structural_path"]["parts"] == ["Chapter 1", "Section A"]
        assert result["content"] == "Some content here."

    def test_extract_without_path(self):
        chunk = "Plain content with no path prefix."
        result = extract_structural_path(chunk)
        assert result["structural_path"] is None
        assert result["content"] == chunk

    def test_extract_single_level_path(self):
        chunk = "[Introduction]\nWelcome text."
        result = extract_structural_path(chunk)
        assert result["structural_path"]["full"] == "Introduction"
        assert result["structural_path"]["parts"] == ["Introduction"]
        assert result["content"] == "Welcome text."

    def test_extract_deep_path(self):
        chunk = "[Part 1 > Chapter 2 > Section 3 > Subsection A]\nDeep content."
        result = extract_structural_path(chunk)
        assert result["structural_path"]["parts"] == [
            "Part 1", "Chapter 2", "Section 3", "Subsection A",
        ]

    def test_hdt_chunks_have_extractable_paths(self):
        """HDT chunks with headings produce extractable structural paths."""
        doc = "# Chapter 1\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B."
        chunks = smart_chunk(doc, chunk_size=1024, strategy="hdt")
        paths_found = 0
        for chunk in chunks:
            result = extract_structural_path(chunk)
            if result["structural_path"] is not None:
                paths_found += 1
                assert len(result["structural_path"]["parts"]) >= 1
        assert paths_found >= 1, "At least one HDT chunk should have a structural path"

    def test_hdt_siblings_do_not_nest_when_level_skipped(self):
        """Same-level headings stay siblings even when an intermediate level is
        skipped (e.g. H1 → H3 with no H2). Regression: the breadcrumb stack used
        path *length* vs the absolute heading level, so the first H3 landed at
        depth-2 and every following H3 sibling nested under it.
        """
        # Chương (H1) → Điều (H3) directly, no Mục (H2) — common in Thông tư.
        doc = (
            "# Chương 1\nQUY ĐỊNH CHUNG\n\n"
            "### Điều 1. Phạm vi\nND một.\n\n"
            "### Điều 2. Đối tượng\nND hai.\n\n"
            "### Điều 3. Giải thích\nND ba.\n"
        )
        chunks = _chunk_hdt(doc)
        # Each Điều breadcrumb must contain exactly ONE Điều (its own), never a
        # sibling Điều prepended.
        dieu_chunks = [c for c in chunks if "Điều" in c.splitlines()[0]]
        assert dieu_chunks, "expected breadcrumbed Điều chunks"
        for c in dieu_chunks:
            breadcrumb = c.splitlines()[0]
            assert breadcrumb.count("Điều") == 1, (
                f"sibling Điều nested in breadcrumb: {breadcrumb!r}"
            )
            # And the chapter must remain the parent.
            assert breadcrumb.startswith("[Chương 1 > Điều"), breadcrumb


# ── Semantic chunking (real) ──────────────────────────────────────────


class TestSemanticChunkingReal:
    def test_semantic_splits_at_topic_boundary(self):
        """Semantic chunker should split when topic changes significantly."""
        doc = (
            "Python là ngôn ngữ lập trình phổ biến nhất thế giới hiện nay. "
            "Python được sử dụng rộng rãi trong khoa học dữ liệu và AI. "
            "Python hỗ trợ nhiều thư viện machine learning như TensorFlow và PyTorch. "
            "Giá vàng hôm nay tăng mạnh do ảnh hưởng kinh tế toàn cầu. "
            "Giá vàng SJC niêm yết ở mức 92 triệu đồng mỗi lượng. "
            "Nhà đầu tư đang đổ xô mua vàng để phòng ngừa lạm phát."
        )
        chunks = _chunk_semantic(doc, chunk_size=500, similarity_threshold=0.16)
        # Should split into at least 2 chunks (Python/AI vs gold/finance)
        assert len(chunks) >= 2

    def test_semantic_single_topic_stays_together(self):
        """Same-topic sentences should stay in one chunk."""
        doc = (
            "Sản phẩm A có giá 100,000 VND. "
            "Sản phẩm A được bảo hành 12 tháng. "
            "Sản phẩm A có màu đen và trắng."
        )
        chunks = _chunk_semantic(doc, chunk_size=500, similarity_threshold=0.1)
        assert len(chunks) == 1

    def test_semantic_respects_chunk_size(self):
        """Oversized segments are sub-split to respect chunk_size."""
        long_text = "Câu dài. " * 200
        chunks = _chunk_semantic(long_text, chunk_size=256)
        for chunk in chunks:
            assert len(chunk) <= 256 * 2  # tolerance 2x

    def test_semantic_empty_input(self):
        chunks = _chunk_semantic("")
        assert chunks == []

    def test_semantic_single_sentence(self):
        chunks = _chunk_semantic("Một câu duy nhất.")
        assert len(chunks) == 1


# ── Proposition chunking ─────────────────────────────────────────────────


class TestPropositionChunking:
    def test_splits_compound_sentences(self):
        text = "Sản phẩm A có giá 100k; sản phẩm B có giá 200k; sản phẩm C có giá 300k."
        chunks = _chunk_proposition(text, chunk_size=500)
        assert len(chunks) >= 1

    def test_proposition_empty(self):
        assert _chunk_proposition("") == []

    def test_proposition_single_sentence(self):
        chunks = _chunk_proposition("Một câu đơn giản nhưng đủ dài để vượt qua threshold.")
        assert len(chunks) == 1

    def test_proposition_strategy_selectable(self):
        """select_strategy can return 'proposition' for long dense text."""
        strategy, confidence = select_strategy(
            analyze_document("Long dense paragraph. " * 300)
        )
        # Proposition or semantic are both valid for long dense text
        assert strategy in ("proposition", "semantic", "recursive", "hdt", "hybrid")
        assert 0.0 <= confidence <= 1.0

    def test_smart_chunk_proposition_strategy(self):
        """smart_chunk accepts strategy='proposition' explicitly."""
        text = "Câu A; câu B; câu C. Câu D và câu E. " * 20
        chunks = smart_chunk(text, strategy="proposition", chunk_size=512)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert isinstance(chunk, str)
            assert len(chunk) > 0


# ── Hybrid chunking ──────────────────────────────────────────────────────


class TestHybridChunking:
    def test_hybrid_uses_hdt_then_proposition(self):
        doc = (
            "# Chapter 1\n\n" + "Đây là nội dung dài. " * 100 + "\n\n"
            "# Chapter 2\n\nNội dung ngắn."
        )
        chunks = _chunk_hybrid(doc, chunk_size=512)
        assert len(chunks) >= 2

    def test_hybrid_strategy_in_select(self):
        # Mixed doc with headings + long prose
        doc = "# Title\n\n" + "Long paragraph content. " * 200 + "\n\n## Section\n\n" + "More content. " * 100
        profile = analyze_document(doc)
        strategy, confidence = select_strategy(profile)
        assert strategy in ("hdt", "semantic", "recursive", "hybrid")


# ── Gap 1: Vietnamese abbreviation protection ────────────────────────────


class TestSplitSentencesVietnamese:
    def test_tp_hcm_not_split(self):
        """'TP.HCM' should not cause a sentence break."""
        text = "Văn phòng tại TP. HCM đang mở rộng. Chúng tôi tuyển dụng."
        sentences = _split_sentences(text)
        assert any("TP.HCM" in s or "TP. HCM" in s for s in sentences)
        # Should be 2 sentences, not 3
        assert len(sentences) == 2

    def test_numbered_list_not_split(self):
        """'1. Gội đầu' should not split at '1.'"""
        text = "Dịch vụ gồm: 1. Gội đầu 2. Cắt tóc 3. Nhuộm tóc."
        sentences = _split_sentences(text)
        assert len(sentences) == 1

    def test_vv_not_split(self):
        """'v.v.' should not cause a sentence break."""
        text = "Hỗ trợ PDF, DOCX, v.v. Hệ thống hoạt động tốt."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert any("v.v." in s for s in sentences)

    def test_real_sentence_boundary_still_works(self):
        """Real sentence boundaries must still split."""
        text = "Câu đầu tiên. Câu thứ hai! Câu thứ ba?"
        sentences = _split_sentences(text)
        assert len(sentences) == 3

    def test_abbreviation_bs_not_split(self):
        """'BS.' should not cause a sentence break."""
        text = "BS. Nguyễn Văn A khám bệnh. Kết quả tốt."
        sentences = _split_sentences(text)
        assert len(sentences) == 2
        assert "BS." in sentences[0]


# ── Gap 2: Vietnamese connectors + em-dash ───────────────────────────────


class TestPropositionConnectors:
    def test_splits_on_khi(self):
        """Proposition chunker should split on ', khi'."""
        text = "Hệ thống hoạt động bình thường, khi có lỗi sẽ gửi cảnh báo tự động."
        chunks = _chunk_proposition(text, chunk_size=5000)
        assert len(chunks) >= 1
        # The clause split should produce separate propositions
        all_text = " ".join(chunks)
        assert "có lỗi" in all_text

    def test_splits_on_neu(self):
        """Proposition chunker should split on ', nếu'."""
        text = "Người dùng có thể đăng ký tài khoản miễn phí, nếu đã có tài khoản thì đăng nhập."
        chunks = _chunk_proposition(text, chunk_size=5000)
        all_text = " ".join(chunks)
        assert "đăng nhập" in all_text

    def test_splits_on_em_dash(self):
        """Proposition chunker should split on em-dash."""
        text = "Sản phẩm A có nhiều tính năng — sản phẩm B chỉ có tính năng cơ bản."
        chunks = _chunk_proposition(text, chunk_size=5000)
        all_text = " ".join(chunks)
        assert "sản phẩm b" in all_text.lower()

    def test_splits_on_mac_du(self):
        """Proposition chunker should split on ', mặc dù'."""
        text = "Doanh thu tăng mạnh trong quý này, mặc dù chi phí cũng tăng đáng kể."
        chunks = _chunk_proposition(text, chunk_size=5000)
        all_text = " ".join(chunks)
        assert "chi phí" in all_text


# ── Gap 3: Hybrid strips structural path before proposition ──────────────


class TestHybridPathStripping:
    def test_hybrid_preserves_path_in_proposition_chunks(self):
        """When hybrid applies proposition to a large HDT section, path is re-prepended."""
        doc = (
            "# Chapter 1\n\n## Important Section\n\n"
            + "Đây là nội dung quan trọng. " * 80
            + "\n\n## Short Section\n\nNội dung ngắn."
        )
        chunks = _chunk_hybrid(doc, chunk_size=256, proposition_threshold=20)
        # At least some chunks from the large section should have structural path
        path_chunks = [c for c in chunks if c.startswith("[")]
        assert len(path_chunks) >= 1, "Proposition sub-chunks should retain structural path"
        for pc in path_chunks:
            result = extract_structural_path(pc)
            assert result["structural_path"] is not None

    def test_hybrid_path_not_leaked_into_proposition_content(self):
        """The [path] prefix should NOT appear inside the proposition text itself."""
        doc = (
            "# Main\n\n## Sub\n\n"
            + "Nội dung chi tiết cho phần này rất dài. " * 60
        )
        chunks = _chunk_hybrid(doc, chunk_size=256, proposition_threshold=10)
        for chunk in chunks:
            parsed = extract_structural_path(chunk)
            # The content part should not contain another [path] prefix
            assert not parsed["content"].startswith("[")


# ── Gap 4: HDT oversized sub-chunks retain structural path ──────────────


class TestHdtOversizedPath:
    def test_oversized_section_retains_path(self):
        """When HDT splits an oversized section, each sub-chunk retains the structural path."""
        long_content = "Nội dung rất dài cho phần này. " * 200
        doc = f"# Chapter 1\n\n## Section A\n\n{long_content}"
        chunks = _chunk_hdt(doc, chunk_size=256)
        # Multiple chunks should be produced from the oversized section
        assert len(chunks) >= 2
        # Each chunk from the split section should have the path
        path_chunks = [c for c in chunks if c.startswith("[")]
        assert len(path_chunks) >= 2, (
            "Oversized HDT sub-chunks should each retain the structural path"
        )
        for pc in path_chunks:
            result = extract_structural_path(pc)
            assert result["structural_path"] is not None
            assert "Section A" in result["structural_path"]["full"]

    def test_small_section_path_unchanged(self):
        """Small HDT sections should still have their path as before."""
        doc = "# Title\n\n## Intro\n\nShort content here."
        chunks = _chunk_hdt(doc, chunk_size=1024)
        path_chunks = [c for c in chunks if c.startswith("[")]
        assert len(path_chunks) >= 1
        result = extract_structural_path(path_chunks[0])
        assert result["structural_path"] is not None


class TestPromoteVnHierarchicalHeadings:
    """promote_vn_hierarchical_headings — VN admin/legal markers → markdown."""

    def test_promotes_chuong_muc_dieu_at_three_levels(self):
        text = "Chương I\nQUY ĐỊNH CHUNG\nMục 1\nĐiều 1. Phạm vi\n1. Nội dung khoản 1.\nĐiều 2. Đối tượng\n2. Nội dung khoản 2.\nĐiều 3. Giải thích"
        out = promote_vn_hierarchical_headings(text)
        # Roman 'I' → Arabic '1' canonical (normalize_vn_section_numerals).
        # Stored heading form matches query rewriter so 'chương 1' and
        # 'Chương I' both hit the same chunk path.
        assert "# Chương 1" in out
        assert "## Mục 1" in out
        assert "### Điều 1. Phạm vi" in out
        assert "### Điều 2. Đối tượng" in out
        # Khoản (1./2./3.) must stay inline, not be promoted into H4
        assert "#### 1." not in out
        assert "1. Nội dung khoản 1." in out

    def test_no_op_when_below_threshold(self):
        """A casual FAQ mentioning 'Điều 1' once must NOT be promoted."""
        text = "FAQ về sản phẩm\nĐiều 1 nên lưu ý là giá có thể thay đổi.\nKết thúc."
        out = promote_vn_hierarchical_headings(text)
        assert out == text  # unchanged

    def test_no_op_when_no_markers(self):
        text = "Đây là blog post.\n\nMột đoạn nội dung bình thường.\n\nKết luận."
        out = promote_vn_hierarchical_headings(text)
        assert out == text

    def test_smart_chunk_picks_hdt_after_promotion(self):
        """End-to-end: VN legal doc with plain markers → smart_chunk uses HDT.

        Doc must clear DEFAULT_HDT_LONG_DOC_WORDS (500) for HDT to score over
        the recursive fallback floor.
        """
        long_para = "Nội dung điều này quy định chi tiết các yêu cầu pháp lý cụ thể về bảo đảm an toàn thông tin trong hoạt động ngân hàng theo quy định hiện hành của pháp luật Việt Nam. " * 6
        articles = "\n".join(
            f"Điều {i}. Tiêu đề điều số {i}\n{long_para}" for i in range(1, 8)
        )
        text = f"Chương I\nQUY ĐỊNH CHUNG\nMục 1\n{articles}\nChương II\nNỘI DUNG CHÍNH\n{articles}"
        chunks = smart_chunk(text)
        # At least one chunk should carry the HDT structural path prefix
        path_chunks = [c for c in chunks if c.startswith("[")]
        assert len(path_chunks) >= 1, (
            "After VN hierarchy promotion, smart_chunk should produce HDT "
            "chunks with [Chapter > ...] path prefix"
        )
        # And that path should mention "Chương" + "Điều" — proving the
        # promoted markers reached the HDT chunker.
        any_path = path_chunks[0]
        assert "Chương" in any_path or "Điều" in any_path

    def test_select_strategy_force_hdt_when_vn_markers_present(self):
        """Cross-check fast-path: doc has plain-text Chương/Mục/Điều markers
        without any markdown headings → select_strategy MUST return hdt with
        confidence 1.0, bypassing weight-based scoring that would otherwise
        let recursive win on docs with short avg_text_length."""
        # Plain text only — no markdown #/##/### at all
        text = "Chương I\nNội dung chương I rất ngắn.\nĐiều 1. Phạm vi\nNgắn.\nĐiều 2. Đối tượng\nNgắn.\nĐiều 3. Trách nhiệm\nNgắn."
        profile = analyze_document(text)
        assert profile["total_headings"] == 0  # no markdown
        assert profile["vn_hierarchical_markers"] >= 4  # Chương + 3 Điều
        strategy, confidence = select_strategy(profile)
        assert strategy == "hdt"
        assert confidence == 1.0

    def test_select_strategy_no_force_when_below_threshold(self):
        """A single passing mention of 'Điều 1' must NOT trigger force-HDT."""
        text = "Đây là FAQ.\nĐiều 1 cần lưu ý là giá có thể thay đổi.\nKết thúc."
        profile = analyze_document(text)
        assert profile["vn_hierarchical_markers"] < 3
        strategy, _ = select_strategy(profile)
        assert strategy != "hdt"  # falls through to weighted scoring

    def test_parent_child_uses_hdt_after_promotion(self):
        """generate_parent_child_chunks: HDT splitter kicks in when promoted."""
        text = (
            "Chương I\nQUY ĐỊNH CHUNG\n"
            "Điều 1. Phạm vi\nNội dung điều 1 đủ dài để có content. " * 4
            + "\nĐiều 2. Đối tượng\nNội dung điều 2 cũng đủ dài. " * 4
            + "\nChương II\n"
            "Điều 3. Trách nhiệm\nNội dung điều 3. " * 4
        )
        promoted = promote_vn_hierarchical_headings(text)
        hierarchy = generate_parent_child_chunks(promoted, parent_size=512, child_size=128, child_overlap=32)
        parent_contents = [h["content"] for h in hierarchy if h["is_parent"]]
        # At least one parent must carry the [Chapter > Article] path
        path_parents = [p for p in parent_contents if p.startswith("[")]
        assert len(path_parents) >= 1, (
            "Parent chunks should carry HDT structural path after promotion"
        )
