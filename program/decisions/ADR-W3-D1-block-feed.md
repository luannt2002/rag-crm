# ADR-W3-D1 — Block-feed end-to-end (un-flatten parser → smart_chunk_atomic)

> Phase 3 ADR · Wave W3 · Tier **[T1-Smartness / ĐỦ]** · Date 2026-06-10
> Nguồn: P2-B 🐛-A + Q9/Q11/Q12 · map `program/context/P3-BLOCKFEED-MAP.md` (call-graph chính xác)
> STANCE = EVOLVE: WIRE dead-code `smart_chunk_atomic` (feature-complete, 0 prod-caller); GIỮ `_split_into_blocks_with_atomic` làm fallback cho direct-text API. Charter-blessed "rewrite cục bộ parser-adapter".

## 1. Context (SỰ THẬT, map-verified)
- Parser kreuzberg LIVE (psql `parser_engine="kreuzberg"`) emit `list[Block]` thật (`document.py:41`, type+is_atomic+context).
- **2 flatten point giết Block stream**: `document_worker.py:298` (`"\n\n".join(b.content...)` → str) + `document_service.py:1321`. Worker truyền `ingest(content=str)` — KHÔNG `blocks=`.
- Mọi downstream **re-detect** block-type từ markdown qua regex `_is_table_line` → ~163/211 prose mis-narrated (🐛-B, đã vá tạm `2f89c46` nhưng gốc vẫn là flatten).
- `smart_chunk_atomic(blocks)→list[Chunk]` (`chunking.py:2758`) đủ tính năng, 0 prod-caller (chỉ test).

## 2. Decision — Option-A thin-adapter, A/B-gated flip
1. **S1 (land NGAY, zero behavior-change)**: thêm `blocks: list[Block]|None=None` vào `ingest()`; thread `blocks=parsed.blocks` từ worker; guard MỌI thứ sau `if blocks:`. Str-only caller (direct API) default None → đường legacy `smart_chunk(str)` nguyên vẹn. = plumbing nền, KHÔNG đổi chunk output.
2. **S2 (behavior, gated)**: B2 gate `parsed_blocks = blocks or []`; **reconcile strategy-vocab** (risk #2): smart_chunk_atomic chuẩn-hóa UPPERCASE `{HDT,SEMANTIC,PROPOSITION,HYBRID}` map `recursive`/`table_csv`→`HYBRID` (mất 50% corpus). FIX = mở rộng normalizer nhận lowercase `recursive`/`table_csv` giữ nguyên, KHÔNG silent→hybrid. Unit-test: recursive doc vẫn recursive, table Block atomic 1 chunk.
3. **S3 (narrate authoritative)**: adapter `Chunk.original_content`→`list[str]` + `block_type_by_idx`; narrate đọc `block_type_by_idx[i]` thay `_split_into_blocks_with_atomic` re-detect → hết mis-narration tận gốc (không chỉ vá regex).
4. **Flag honesty**: gate trên `if blocks:` (block-presence = switch thật) + `DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED` giữ nhưng feed thật; sửa T4 honesty-pin khi G4 land.
5. **PRODUCTION FLIP = A/B-gated** (no-guess-must-measure): S2/S3 đổi cách chunk 50% corpus → **PHẢI graded 91Q A/B (block-feed ON vs OFF) hold ≥85/91 + HALLU=0** TRƯỚC khi default ON. Cần graded stack (ops). Land code gated, flip sau khi đo.

## 3. Alternatives rejected
- **Option-B full Chunk-native pipeline** (rewrite CR/embed/INSERT `:2125-3400` nhận list[Chunk]): blast radius cả ingest tail; EVOLVE→A trước (map §3).
- **Blind-flip S2/S3 không A/B**: risk #2 có thể rớt 85/91 silent; vi phạm rule#0.
- **Xóa `_split_into_blocks_with_atomic`**: cần làm fallback direct-text API + narrate block-less (map §4).

## 4. Implementation (failing-test-first, staged)
- **S1** (THIS commit): `ingest(blocks=)` param + worker thread + `if blocks:` guard. Test: PDF ingest → blocks arrive non-empty; str-only caller unaffected (default None). Zero chunk-output change.
- S2/S3 (next, behind A/B): strategy-vocab reconcile + adapter + narrate-by-idx; pin T4 update; graded A/B gate.

## 5. Gate metric
- S1: blocks threaded, 0 behavior change, suite 0 regression.
- S2/S3: graded 91Q block-feed ON ≥85/91 + HALLU=0 + DB: prose-as-TABLE=0 + recursive-not-→hybrid. **Flip chỉ sau số.**

## 6. CLAUDE.md compliance
Rule#0 (A/B gate cho flip) ✅ · EVOLVE (wire dead-code, giữ fallback) ✅ · zero-hardcode ✅ · sacred#10 (ingest-path, không đụng answer) ✅ · T1 declared ✅ · no-version-ref (smart_chunk_atomic purpose-named) ✅.
