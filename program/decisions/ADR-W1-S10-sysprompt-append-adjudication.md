# ADR-W1-S10 — Phân xử sacred #10: SysPromptAssembler append platform rules

> Status: **DRAFT → trình user** · Date 2026-06-10 · Author: main-session (adjudication = không delegate)
> Nguồn: P2-H 🐛 SP-1/SP-2 + verify trực tiếp `sysprompt_assembler.py` (đọc full file main-session 2026-06-10).

## 1. Context — SỰ THẬT đã verify

- `sysprompt_assembler.py:126` — `return base + platform_rules`: application **append** text vào system prompt SAU `bot.system_prompt`.
- Nguồn text: `language_packs[locale].sysprompt_default_rules` (DB, seed qua alembic 0146 — tracked, no-psql-hotfix ĐÚNG).
- Nội dung: rules 15-19 (`SYNTHESIS_COMPLETE / COMPARISON_VERDICT / ANTI_CSV_ROW_CONFLATE / INLINE_SLOT_CAPTURE / STRICT_PROMO_BINDING`) — **behavior rules ảnh hưởng trực tiếp answer**, không phải config kỹ thuật. ~6KB.
- Cơ chế: per-bot **opt-OUT** qua `plan_limits.sysprompt_rules_disabled` (`:120-124`); graceful degrade → trả nguyên `bot.system_prompt` khi port fail (`:107-114`).
- Wired LIVE answer-path: `chat_worker.py:1436` + `chat_stream.py:295` (P2-H evidence).
- Vì sao engine-audit không thấy: 9 lock-test (`test_generate_no_app_injection.py`) assert ở **generate node** — node nhận prompt ĐÃ-lắp-ráp từ upstream, nên "verbatim" pass trong khi append xảy ra ở assembler.
- Lý do ra đời (docstring `:6-12`): J1 multi-tenant scaling — tránh per-bot alembic ship (alembic 0142-0145 per-bot = anti-pattern khi onboard N bot).

## 2. Tension — vì sao đây là vi phạm-theo-nghĩa-đen

CLAUDE.md Application MINDSET #1: *"Application KHÔNG inject text vào LLM prompt. KHÔNG prepend platform/docs-only rules… Bot owner's `system_prompt` is THE single source of truth."* Append ≠ prepend về vị trí nhưng = về bản chất: **LLM nhận text owner không viết, owner không thấy, mặc định bật**. Quality Gate #10 cùng nội dung. Hai sự thật cùng đúng:
- (A) **Vi phạm tinh thần consent**: owner-prompt không còn single source; không có preview (SP-2) → owner sửa prompt "mù" trong khi app lắp thêm 6KB.
- (B) **Không phải hot-fix lậu**: tracked alembic, domain-neutral (đã kiểm text — không brand/ngành), opt-out per-bot, degrade an toàn, và **đang đóng góp vào trạng thái 85/91 + HALLU=0 hiện tại** (rules synthesis/anti-conflate là fix từ các vòng load-test).

## 3. Decision — RULING: **governed-exception CÓ ĐIỀU KIỆN, 3 điều kiện bắt buộc + lộ trình consent**

KHÔNG gỡ trong W1 (gỡ ngay = regression risk trực tiếp lên 85/91 + HALLU=0 mà chưa đo phần đóng góp — vi phạm no-guess-must-measure). Hiện trạng được phép TẠM TỒN TẠI với 3 điều kiện, miss bất kỳ điều kiện nào = chuyển trạng thái VIOLATION phải gỡ:

1. **Transparency (W1, code-side ~2h)**: endpoint read-only `GET /admin/bots/{id}/effective-prompt` trả prompt LẮP-RÁP-CUỐI (base + rules sau opt-out strip) + diff-marker phần platform-append. Owner phải THẤY được cái LLM thấy. (Đóng luôn P2-H SP-2 preview-gap.)
2. **Codify giới hạn (W1, doc-side ~30')**: amend CLAUDE.md sacred #10 thêm exception-clause CHẶT: *chỉ* `language_packs.sysprompt_default_rules`, *chỉ* alembic-tracked, *chỉ* domain-neutral, *chỉ* append-sau (không bao giờ prepend/chèn giữa), per-bot opt-out tồn tại, và **cấm mở rộng** key mới tương tự không qua ADR. Không codify = slippery-slope (math_lockdown bài học cũ).
3. **Đo đóng góp (Phase 5 ablation, gate có kill-date)**: A/B 91Q graded với rules-on vs rules-off (per-bot opt-out sẵn có = cơ chế A/B miễn phí). Nếu lift không đo được → rules chuyển vào **bot-creation template** (text vào `bots.system_prompt` lúc tạo bot, owner thấy + sửa được) và assembler GỠ khỏi answer-path → single-source phục hồi tuyệt đối.

**Lộ trình consent (W6)**: khi dashboard + preview live → flip mặc định **opt-out → opt-in** (`sysprompt_platform_rules_enabled`, owner bật chủ động); bot hiện hữu backfill `=true` qua alembic để không đổi behavior giữa chừng.

## 4. Alternatives rejected

| Option | Vì sao reject |
|---|---|
| Gỡ ngay W1 (strict) | Regression risk chưa đo trên 85/91+HALLU=0; mất cơ chế platform-wide rule update (quay lại per-bot alembic anti-pattern J1 đã trả giá) |
| Hợp pháp hóa vô điều kiện (amend sacred, giữ nguyên) | Mất tinh thần owner-owns-everything; không preview = owner mù; slippery-slope không kill-date |
| Chuyển rules vào mỗi `bots.system_prompt` ngay (copy-per-bot) | Drift N bot khi rule update; chính là anti-pattern J1 giải; CHỈ làm nếu ablation chứng minh lift→0 (điều kiện 3) |

## 5. Test matrix (Phase 4, failing-first)
- `test_effective_prompt_endpoint.py`: trả base+rules đúng, diff-marker đúng, opt-out strip phản ánh, RBAC level đúng, tenant-scoped.
- Pin test: `test_sysprompt_assembler_pin.py` — append CHỈ sau base, KHÔNG bao giờ prepend; degrade trả base nguyên; rule-strip regex đúng block-boundary.
- Ablation harness flag (Phase 5): chạy graded với `sysprompt_rules_disabled=[all]` per-bot.

## 6. Gate metric
- Endpoint live + 9 lock-test giữ xanh + pin test mới xanh.
- CLAUDE.md amendment merged.
- Phase 5: A/B verdict ghi vào `program/eval/` — quyết giữ-có-điều-kiện hay chuyển-template.

## 7. Consequences
- Sacred #10 được làm RÕ thay vì bị lách im lặng; mọi audit sau có ranh giới đúng để chấm.
- Engine-audit lesson: lock-test phải đặt ở **boundary lắp ráp** (assembler) chứ không chỉ ở generate — thêm 1 lock-test assembler vào bộ 9.
