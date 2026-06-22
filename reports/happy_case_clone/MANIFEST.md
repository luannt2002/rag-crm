# Happy-case rewritten data — 3 bots, 9 files (styling-only, data-preserving)

> Source: real DB documents (raw_content) → normalized via
> scripts/normalize_to_happy_case.py to the happy-case template format. Real data
> preserved (only formatting/styling added). git-ignored (tenant content).
> Verify: `python scripts/verify_happy_case_pipeline.py` → ALL L1→L7 GREEN.

| Bot | File | Kind | Verdict | L7 |
|-----|------|------|---------|-----|
| spa | spa-1.csv | sheet/catalog | ✅ HAPPY | 18/18 priced (100%) |
| spa | spa-2.csv | sheet/catalog | 🟡 minor | 44/51 (86% — rest are package-only) |
| spa | spa-3.csv | sheet/catalog | ✅ HAPPY | 12/12 priced (100%) |
| spa | spa-4.md  | doc/script | ✅ HAPPY | prose SOP (prices stay in-sentence) |
| legal | thongtu-09-2020.csv | doc | ✅ HAPPY | 87 headings + 12 tables |
| xe | xe-1.csv | sheet/inventory | ✅ HAPPY | 209 entities (no price col — correct) |
| xe | xe-2.csv | sheet/manifest | 🟡 minor | 66 entities (no price col — correct) |
| xe | xe-3.csv | sheet/catalog | ✅ HAPPY | 171/172 priced (99%) + 62 synonyms kept in Aliases |
| xe | xe-4.md  | doc/policy | ✅ HAPPY | warranty doc, 11 sections |

Rewrite actions (styling only, 0 business-data loss):
- xe-3: search-index export → catalog columns (Tên,Giá,Mã,SL,Ngày,Ảnh,Aliases); 62 synonyms preserved.
- spa-4: consultation script → DOC (Bước N: → ## headings); all prose kept.
- xe-4: prose policy → added ## headings.
- xe-2: added a header row.
- others: cloned unchanged (already happy-case).
