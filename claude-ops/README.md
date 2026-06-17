# claude-ops/ — Cách Ragbot vận hành Claude (Opus/Sonnet/Haiku)

> **Mục đích**: tách phần "Claude Code operations" (cost audit, hooks, aidlc, role-based plans) ra khỏi `src/ragbot/` business code. Vừa **tutorial** cho anh hiểu Claude work như nào, vừa **runtime tooling** đã/đang ship.
>
> **Không phải**: source business của Ragbot RAG bot. Cũng không phải CI/CD pipeline (đó là `.github/workflows/` riêng).

---

## Đọc theo thứ tự

| Folder | Status | Read order |
|---|---|---|
| `00-overview/` | Tutorial | **ĐỌC TRƯỚC** — nền tảng mental model |
| `10-cost-audit/` | ✅ APPLIED (port từ `emtyty/claude-token-monitor`) | Doc cho `scripts/cost_audit.py` đã ship |
| `20-roadmap-hooks/` | ✅ APPLIED (1 hook variant từ `hueanmy/claude-roadmap`) | Doc cho `.claude/settings.json` PostToolUse hook |
| `30-aidlc/` | ⏳ DOCS ONLY — chưa wire MCP | Setup guide, em chưa apply runtime |
| `40-qa-golden/` | ⏭ DEFERRED | Tại sao chưa apply (mâu thuẫn stack) |
| `_runtime/` | gitignored | Local scratch, session dumps |

---

## Tóm tắt 1 dòng

> `claude-ops/` = **operations layer** cho Claude Code agent (Opus/Sonnet/Haiku) chạy trên Ragbot. Tách khỏi `src/ragbot/` để clear scope: business code = `src/`, agent-orchestration tooling = `claude-ops/`.

---

## Source repos đối chiếu

| Source | URL | Status apply |
|---|---|---|
| `emtyty/claude-token-monitor` | https://github.com/emtyty/claude-token-monitor | ✅ Port (variant) → `scripts/cost_audit.py` 6 sub-cmd; 4 advisor rule chưa port (TODO list ở `10-cost-audit/advisor-rules-todo.md`) |
| `hueanmy/claude-roadmap` | https://github.com/hueanmy/claude-roadmap | ✅ Hook pattern → `.claude/settings.json` PostToolUse cho `shared/constants.py` validate |
| `aidlc-io/aidlc` + `hueanmy/aidlc-extension` | https://github.com/hueanmy/aidlc-extension | ⏳ DOCS ONLY ở `30-aidlc/` — wire MCP defer |
| `hueanmy/qa-playwright-agent` | https://github.com/hueanmy/qa-playwright-agent | ⏭ DEFER — playwright JS/browser không match Ragbot backend stack |
| `hueanmy/ai-shorts-generator` | (skipped) | Skip — video gen không liên quan backend |
| `hueanmy/tech-radar` | (skipped) | Skip — cron digest cá nhân, ROI thấp |

---

## Khi anh dùng folder này

1. **Đọc `00-overview/`** trước nếu lần đầu — hiểu Claude tier, cache, token cost.
2. **Run tools đã ship**:
   ```bash
   python scripts/cost_audit.py today        # daily cost
   python scripts/cost_audit.py model-mix    # tier compliance
   python scripts/check_state_snapshot.py    # drift check
   bash scripts/loadtest_kick.sh <script>    # async fire-and-forget
   ```
3. **Setup aidlc** (sau khi đọc `30-aidlc/setup.md` + anh duyệt) — wire MCP cho 9-phase epic gate.
4. **Add hook mới** theo cookbook ở `20-roadmap-hooks/hook-cookbook.md`.

---

## Phase shipping plan

- **Phase 1** (commit này): full docs + folder structure + 1 line `.gitignore`. Em không đụng `src/`, không đụng alembic, không đụng test suite.
- **Phase 2** (chờ anh duyệt): wire aidlc MCP + tạo `docs/core-business/*.md` cho 9-phase epic gate.
- **Phase 3** (incremental): port 4 advisor rule còn lại vào `cost_audit.py` (~1-2h).

---

## Sacred contracts intact

Folder này **PURE DOCS + TOOLING**. Không đụng:
- HALLU=0 path (`src/ragbot/orchestration/`)
- 4-key bot identity (schema)
- Sysprompt templates (`docs/templates/`)
- Domain-neutral mandate (chỉ quote tool names, không hardcode brand)

Co-exist với `docs/`, `plans/`, `scripts/` — không thay thế.
