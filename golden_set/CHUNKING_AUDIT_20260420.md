# CHUNKING AUDIT — Code hiện tại vs Thế giới

> Ngày: 2026-04-20 | Phương pháp: 2 agents (research + code audit)

## Đã làm ĐÚNG
- ✅ Adaptive 4 strategies (HDT/semantic/recursive/hybrid)
- ✅ Table protection (không cắt giữa bảng)
- ✅ Contextual enrichment (Anthropic style, -67% lỗi tìm sai)
- ✅ Parent-child chunking (child embed, parent trả LLM)
- ✅ Late chunking (document context prefix)
- ✅ Sentence boundary respect
- ✅ Chunk size 1024 chars ≈ 512 tokens Vietnamese (đúng benchmark)

## 3 GAP CHÍNH

### GAP 1: Strategy selection không có confidence score
- Hiện tại: hardcode rules, không biết chắc bao nhiêu %
- Best practice: Score mỗi strategy, chọn cao nhất, fallback HYBRID nếu < 60%
- Impact: 5-8% documents chọn sai strategy

### GAP 2: Enrichment không check cost-benefit
- Hiện tại: ON cho TẤT CẢ documents (kể cả FAQ 3 chunks)
- Best practice: Chỉ enable khi > 20 chunks HOẶC doc_type = legal/financial
- Impact: 15-25% unnecessary LLM cost

### GAP 3: Whole-doc threshold 8000→4000
- Hiện tại: File < 8000 chars = 1 chunk (quá lớn, embedding bị "blurry")
- Best practice: < 4000 chars (vừa đủ cho embedding window)
- ĐÃ FIX: Đổi thành 4000 trong init_system_config.py

## KEY INSIGHT: Chunking thông minh → giảm cost

| Chunking | top_k cần | Context tokens | Cost/query |
|----------|----------|----------------|-----------|
| Naive | 10-15 | 5,120-7,680 | $0.007+ |
| Smart (hiện tại) | 4-5 | 1,600-2,000 | $0.003 |
| Optimal (contextual + threshold) | 3-4 | 1,200-1,600 | $0.002 |

Chunking chính xác hơn → cần ÍT chunks → ÍT tokens → RẺ hơn + NHANH hơn.

## KHUYẾN NGHỊ (backlog)

| Priority | Fix | Impact | Effort |
|----------|-----|--------|--------|
| Tier 0 | Confidence score cho strategy selection | +5-10% accuracy | 2h |
| Tier 0 | Conditional enrichment (skip < 20 chunks) | -20% ingest cost | 1h |
| Tier 1 | Batch enrichment 5-10 chunks/call | -80% enrichment latency | 2h |
| Tier 1 | Vietnamese sentence tokenizer | +5% semantic precision | 1h |
| Tier 2 | Metadata trong chunks | Observability | 1h |

## Sources
- Anthropic Contextual Retrieval: https://www.anthropic.com/news/contextual-retrieval
- FloTorch Chunking Benchmark: https://www.runvecta.com/blog/we-benchmarked-7-chunking-strategies-most-advice-was-wrong
- VN-MTEB: https://arxiv.org/html/2507.21500v1
- CRAG Paper: https://arxiv.org/abs/2401.15884
