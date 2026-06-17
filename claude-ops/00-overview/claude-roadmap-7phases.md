# Claude Roadmap — 7 Phase Reference

> **Source verified**: https://hueanmy.github.io/claude-roadmap.html (`hueanmy/claude-roadmap` GitHub).
>
> **Mục đích**: anh đọc qua nắm tổng thể Claude ecosystem từ Foundation → Production. Map từng phase vào Ragbot status hiện tại.

---

## Phase 1 — Foundation

**Content**: Hiểu LLM, so sánh Claude/GPT/Gemini, context window, token & pricing.

**Ragbot apply**:
- ✅ Tier policy: Opus 4.7 main / Sonnet 4.6 subagent / Haiku banned (CLAUDE.md MODEL TIER POLICY)
- ✅ Pricing baseline: 30-day $11k breakdown trong `claude-ops/00-overview/token-economics.md`
- ✅ Cache TTL 5min hiểu rõ trong `claude-ops/00-overview/claude-mental-model.md`

**Memory map**: `feedback_model_tier_policy.md`, `project_cost_audit_shipped.md`.

---

## Phase 2 — Prompting

**Content**: System prompt, few-shot examples, chain of thought, XML tags.

**Ragbot apply**:
- ✅ Sysprompt template generic + 3 industry skeleton (`docs/templates/SYSPROMPT_TEMPLATE.md`)
- ✅ XML wrap chunks trong prompt (Anthropic XML prompt principles paper #07 APPLIED)
- ✅ Validator: `scripts/validate_sysprompt.py` 10-item checklist
- ⏳ Few-shot examples: chưa apply systematically (defer Sprint 3)

**Memory map**: `feedback_validate_master_docs.md`, paper #07 academic-papers.

---

## Phase 3 — API Integration

**Content**: SDK (Python/JS), Messages API, streaming, batch API, vision/multimodal.

**Ragbot apply**:
- ✅ LiteLLM wrap (`litellm.acompletion`) — OpenAI-compatible interface
- ✅ Anthropic Messages API qua LiteLLM
- ✅ Streaming support trong chat handler
- ❌ Batch API: chưa dùng (Anthropic Batch API cho async non-realtime)
- ❌ Vision/multimodal: chưa applicable (text-only RAG)

**Memory map**: V2 migration bug lessons (`feedback_v2_bug_lessons.md`).

---

## Phase 4 — Tools & Agents

**Content**: Tool calling, agentic loop, computer use, web search, human-in-the-loop.

**Ragbot apply**:
- ✅ Tool calling: `Edit/Write/Read/Bash/Grep/...` standard Claude Code tools
- ✅ Agentic loop: LangGraph orchestration (24-step pipeline trong `query_graph.py`)
- ✅ Subagent pattern: `Agent({subagent_type, model})` invocation (Stream X harness limit chưa verify)
- ❌ Computer use: không applicable
- ❌ Web search: WebFetch/WebSearch tools dùng cho research, không production

**Memory map**: `project_pipeline_24step_status.md`.

---

## Phase 5 — Claude Code

**Content**: CLI commands (`/help`, `/plan`, `/clear`, `/compact`, `/memory`, `/cost`, `/doctor`) + features (Skills, subagents, hooks, MCP integration, slash commands) + shortcuts (Ctrl+C, Ctrl+R, Shift+Tab, Esc+Esc, @ mention, multiline) + modes (Plan mode, headless mode, permission modes, git worktrees).

**Ragbot apply**:
- ✅ `/plan` mode: Anh dùng cho non-trivial tasks
- ✅ Memory: auto-memory `/root/.claude/projects/-var-www-html-ragbot/memory/`
- ✅ Hooks: `.claude/settings.json` PostToolUse (`claude-ops/20-roadmap-hooks/`)
- ✅ Skills: aidlc-style `.aidlc/skills/*.md` (defer install)
- ✅ Subagents: spawn qua `Agent` tool
- ✅ Permission modes: `.claude/settings.json` allow list
- ⏳ git worktrees: anh có thể dùng cho parallel Claude session (KHÔNG ép)
- ⏳ MCP integration: defer (xem `30-aidlc/`)

**Memory map**: `feedback_no_permission.md`, hook examples ở `claude-ops/20-roadmap-hooks/`.

---

## Phase 6 — MCP (Model Context Protocol)

**Content**: MCP architecture, build server stdio, integrate Claude Code.

**Ragbot apply**:
- ❌ Chưa wire MCP server. aidlc dùng CLI shell-out thay vì MCP.
- ⏳ Future option: wire `cost_audit.py`/`reclassify_loadtest.py`/`analyze_score_distribution.py` thành MCP server để slash command native.

**Memory map**: chưa.

---

## Phase 7 — Advanced

**Content**: Agent teams, evals, fine-tuning, cost optimization, security, observability.

**Ragbot apply**:
- ✅ Agent teams: Claude main session + Subagent (`Agent` tool) coordination via CLAUDE.md MODEL TIER POLICY
- ✅ Evals: `scripts/eval_multi_hop.py` (Paper 14), `scripts/eval_vn_recall.py` (Paper 25), `scripts/reclassify_loadtest.py`
- ❌ Fine-tuning: không applicable (Anthropic chưa expose fine-tune cho Opus 4.7)
- ✅ Cost optimization: `scripts/cost_audit.py` 6 sub-cmd, tier policy v2
- ✅ Security: 4-key bot identity, RBAC `require_min_level`, JWT bearer, app-no-inject sacred
- ✅ Observability: `request_steps` table 28+ step_names instrumented, `scripts/check_state_snapshot.py` drift check
- ⏳ Production scale features: pending (load test thực production, multi-tenant >10 bot)

**Memory map**: `feedback_autonomous_chief.md`, `project_v4_validate_v2.md`, `project_v4_ga_hardening.md`.

---

## Phase coverage summary

| Phase | Coverage | Notes |
|---|---|---|
| 1 Foundation | ✅ 100% | Tier policy + cache + pricing all documented |
| 2 Prompting | ⚠ 70% | Few-shot examples deferred |
| 3 API Integration | ⚠ 60% | Batch API + vision không applicable |
| 4 Tools & Agents | ✅ 90% | Computer use không applicable |
| 5 Claude Code | ✅ 85% | MCP integration pending (xem Phase 6) |
| 6 MCP | ❌ 0% | aidlc chọn CLI-only path; MCP server riêng pending |
| 7 Advanced | ⚠ 75% | Fine-tuning N/A, observability đầy đủ |

→ Phase 6 MCP là gap chính. Khi Anthropic stable MCP server tooling, có thể wire `cost_audit.py` / `reclassify_loadtest.py` thành MCP server để slash command native.

---

## Reference

- Source: https://hueanmy.github.io/claude-roadmap.html
- Repo: `hueanmy/claude-roadmap` GitHub
- Anthropic Claude Code docs: https://docs.anthropic.com/en/docs/claude-code
- License: educational/permissive (Vietnamese journal style)
