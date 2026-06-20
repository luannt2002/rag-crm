================================================================
DEBUG: So sánh output markdown PDF — Technical Support vs Dự án hiện tại
Ngày: 2026-06-19 | PDF nguồn: Thông tư 09/2020/TT-NHNN
================================================================

FILE SO SÁNH:
  [A] SUPPORT (chuẩn, từ dự án khác): /home/luannt/Downloads/huy_markdown.md
  [B] DỰ ÁN MÌNH (convert qua Kreuzberg): docs/PDF_1111111_TT09_MARKDOWN.md

----------------------------------------------------------------
1) ENCODING — KHÔNG mất ký tự
----------------------------------------------------------------
  - Cả 2 file trên ĐĨA đều UTF-8 ĐÚNG (đọc Python ra tiếng Việt chuẩn:
      A: 'NGÂN HÀNG NHÀ NƯỚC\nVIỆT NAM\n\nCỘNG HÒA XÃ'
      B: '# `1111111_TT09.pdf` → Markdown\n\n> Outpu'
  - Mojibake 'NGÃN HÃNG NHÃ NÆ¯á»C' chỉ xuất hiện khi file UTF-8 bị
    ĐỌC/HIỂN THỊ nhầm bằng Latin-1 (lỗi viewer/copy-transfer),
    KHÔNG phải file hỏng. => Không mất ký tự nào, sửa = mở đúng UTF-8.

----------------------------------------------------------------
2) KHÁC BIỆT CHÍNH = CẤU TRÚC HEADING (markdown)
----------------------------------------------------------------
  Số heading theo cấp (#, ##, ###, ...):
    [A] SUPPORT : #=5  ##=78  ###=10  ####=1  #####=3  (TỔNG 97)
    [B] MÌNH    : #=1  ##=0  ###=0  ####=0  #####=0  (TỔNG 1)

  => SUPPORT: markdown PHÂN CẤP (# Chương > ## Điều/Mục > ### khoản...).
  => MÌNH   : TEXT PHẲNG, không có heading (chỉ 1 dòng title em thêm).

  Ví dụ cùng đoạn 'Điều 1':
    [A] SUPPORT:
        ### Điều 1. Phạm vi điều chỉnh và đối tượng áp dụng
        
        1. Thông tư này quy định những yêu cầu tối thiểu về bảo đảm an toàn hệ thống thông tin trong hoạt động ngân hàng.
    [B] MÌNH:
        Điều 1. Phạm vi điều chỉnh và đối tượng áp dụng
        1. Thông tư này quy định những yêu cầu tối thiểu về bảo đảm an toàn hệ thống 
        thông tin trong hoạt động ngân hàng.
    -> Support có '### Điều 1...', mình KHÔNG có '###'.

----------------------------------------------------------------
3) NHƯNG DỰ ÁN MÌNH VẪN HIỂU CẤU TRÚC — chỉ áp dụng CHỖ KHÁC
----------------------------------------------------------------
  - Có hàm promote_vn_hierarchical_headings() (src/ragbot/shared/chunking/
    vn_structural.py:267) — promote 'Chương/Mục/Điều' -> markdown heading.
  - Chunker (AdapChunk strategy=hdt) tạo breadcrumb [Chương 1 > Điều 1...]
    cho TỪNG mẩu (đã thấy ở output 112 mẩu).
  => Cấu trúc được áp dụng ở bước CHUNK (lúc cắt mẩu để search),
     KHÔNG export ra file markdown.
  - Parser PDF của mình (infrastructure/parser/pdf_parser.py, pypdfium)
    chỉ emit '## Page N' + text phẳng theo TRANG (không theo Điều).
  - File [B] ở trên dùng Kreuzberg.extract_file -> text phẳng, không heading.

----------------------------------------------------------------
4) KẾT LUẬN — KHÁC GÌ VỚI CODE DỰ ÁN HIỆN TẠI
----------------------------------------------------------------
  (a) FORMAT: support = markdown phân cấp theo Chương/Điều (97 heading);
      mình = text phẳng (Kreuzberg) HOẶC theo trang '## Page N' (pypdfium).
  (b) ENCODING: cả 2 UTF-8 đúng; mojibake chỉ là lỗi hiển thị, KHÔNG phải bug file.
  (c) CẤU TRÚC: mình CÓ sẵn logic promote heading + breadcrumb, nhưng dùng
      lúc CHUNK (retrieval) chứ KHÔNG xuất ra markdown như support.

----------------------------------------------------------------
5) ĐỂ KHỚP 'FORMAT CHUẨN' CỦA SUPPORT
----------------------------------------------------------------
  - CÁCH 1 (tận dụng code có sẵn): sau parse, chạy
    promote_vn_hierarchical_headings(text) RỒI emit ra markdown
    (Chương->#, Mục->##, Điều->###...), giữ UTF-8. Mình ĐÃ có hàm này.
  - CÁCH 2: thay bước parse bằng PDF->markdown tool cấu trúc
    (vd marker / pymupdf4llm) để có heading sẵn, vẫn UTF-8.
  - LƯU Ý: với RAG, cấu trúc heading KHÔNG bắt buộc cho chất lượng search
    (mình đã có breadcrumb [Chương>Điều] ở chunk). Heading markdown chủ yếu
    để CON NGƯỜI đọc/đối chiếu đẹp như format của support.


================================================================
PHẦN 2 — SO SÁNH CẤU TRÚC THƯ MỤC: AdapChunk (support) vs Dự án mình
Nguồn họ: ~/Documents/CĐ1/adapchunk/  (reference AdapChunk của technical support)
================================================================

A) CẤU TRÚC CỦA HỌ (flat modular — 1 concern / 1 file):
   adapchunk/
     chunking/   -> 1 strategy / file: base, fixed, hdt, hybrid, proposition, semantic
     models/     -> 1 component / file: block_detector, ocr_client, narrator,
                    feature_extractor, strategy_selector, cross_checker, metadata_utils,
                    answer_generator, retriever, rag_chain, vector_store, pipeline, config

B) CẤU TRÚC CỦA MÌNH (hexagonal/DDD — trải nhiều layer, CÓ CHỦ ĐÍCH):
   shared/chunking/        analyze.py, strategies.py, blocks.py, csv_chunker.py, vn_structural.py
   infrastructure/parser/  pdf_parser.py        (= ocr_client)
   infrastructure/vector/  pgvector_store.py    (= vector_store)
   application/services/   narrate_dispatch.py  (= narrator) ; document_service/ (= pipeline ingest)
   orchestration/          query_graph.py (= rag_chain) ; nodes/retrieve.py (= retriever) ; nodes/generate.py (= answer_generator)
   shared/constants/ + system_config(DB)   (= config)

C) MAP 1:1 + TRẠNG THÁI:
   AdapChunk (họ)                | File của mình                                  | Trạng thái
   -----------------------------|------------------------------------------------|---------------------------
   chunking/{hdt,semantic,...}  | shared/chunking/strategies.py (GỘP 5-6 strategy)| GỘP -> nên tách per-file
   feature_extractor.py         | analyze.py::analyze_document                    | GỘP trong analyze.py
   strategy_selector.py         | analyze.py::select_strategy                     | GỘP trong analyze.py
   cross_checker.py             | analyze.py::apply_cross_check                   | GỘP trong analyze.py
   block_detector.py            | shared/chunking/blocks.py (atomic-block CÓ)     | CÓ code, pipeline NO-OP (passthrough)
   narrator.py                  | application/services/narrate_dispatch.py        | CÓ, no-op mặc định
   ocr_client.py                | infrastructure/parser/pdf_parser.py             | OK (layer khác)
   vector_store.py              | infrastructure/vector/pgvector_store.py         | OK (layer khác)
   retriever.py                 | orchestration/nodes/retrieve.py (1888 dòng)     | OK nhưng GOD-FILE
   answer_generator.py          | orchestration/nodes/generate.py (975 dòng)      | OK
   rag_chain.py                 | orchestration/query_graph.py (2828 dòng)        | OK nhưng GOD-FILE
   metadata_utils.py            | rải (vn_structural + strategies.extract_path)   | rải
   config.py                    | shared/constants/ + system_config DB            | OK (mạnh hơn: DB-driven)
   pipeline.py                  | document_service/ + query_graph (2 graph)       | OK

D) KHÁC BIỆT CỐT LÕI (mình bỏ/thiếu gì so với họ):
   1. KIẾN TRÚC: họ = flat monolith (mọi thứ trong models/). Mình = hexagonal/DDD layered.
      => CHỦ ĐÍCH theo CLAUDE.md sacred (Port+Adapter+DI). KHÔNG nên flatten về models/ phẳng.
   2. GỘP FILE trong chunking/: strategies.py gộp 5-6 strategy; analyze.py gộp 3 concern
      (feature/selector/crosscheck). Họ tách rõ -> MÌNH NÊN TÁCH cho sạch & dễ đọc.
   3. STUB (làm dở, chưa bật): block_detector + narrator của mình CÓ code nhưng NO-OP
      (parser chạy passthrough flat-text). Họ chạy thật -> markdown có heading Chương/Điều.
   4. GOD-FILE: retrieve.py 1888 / generate.py 975 / query_graph.py 2828 dòng — to hơn nhiều
      so với module nhỏ gọn của họ (đang refactor dở: query_graph 3945->2828).

E) CÁCH CLEAN + TÁCH LUỒNG (KHÔNG phá kiến trúc sacred):
   BƯỚC 1 (an toàn, trong shared/chunking/):
     - Tách strategies.py -> strategies/{recursive,hdt,semantic,proposition,hybrid,fixed}.py
     - Tách analyze.py    -> feature_extractor.py + strategy_selector.py + cross_checker.py
   BƯỚC 2 (hoàn thiện stub = bằng họ):
     - Wire block_detector (blocks.py) vào ingest (bỏ "passthrough") — Wave B1-B4 charter
     - Wire narrator (narrate_dispatch) — bỏ no-op  => mình cũng ra markdown # Chương/## Điều
   BƯỚC 3 (tiếp tục đã làm dở): tách god-file query_graph 2828-> nhỏ, retrieve.py 1888, generate.py 975
   GIỮ NGUYÊN: ocr->infrastructure/parser, vector->infrastructure/vector,
     retriever/answer->orchestration (đúng layer; KHÔNG gộp về models/ phẳng).
   => Map khái niệm 1:1 với AdapChunk NHƯNG đặt đúng layer hexagonal của mình.
