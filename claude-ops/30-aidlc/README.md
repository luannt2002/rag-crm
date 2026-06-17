# 30-aidlc — AI-Driven SDLC framework (CLI + VSCode extension)

> **Status**: ⏳ DOCS ONLY. Chưa install + wire. Anh đọc → quyết định ship hay không.
>
> **Source verified 2026-05-06**: `aidlc-io/aidlc` = **CLI framework + VSCode extension**, KHÔNG phải MCP server. License MIT. ~2,300 VSCode marketplace installs.
>
> **Correction**: file này đã update sau khi WebFetch verify thật scope aidlc.

---

## Bản chất aidlc (verified)

```
aidlc
├── @aidlc/core            (pure TypeScript, no VSCode dep)
├── @aidlc/cli             (~30 commands trong 9 nhóm)
└── aidlc (extension)      (VSCode marketplace hueanmy.aidlc)
```

- **CLI commands**: `aidlc run start`, `aidlc step done`, `aidlc agent run`, `aidlc skill load`, ...
- **Skills system**: skill definitions ở `.aidlc/skills/<name>.md` files
- **Agents**: LLM-driven role agents ở `.aidlc/agents/<name>.yaml`
- **Runner**: `WorkspaceSchema` + `SkillLoader` + `RunnerRegistry` (mirror Strategy+DI pattern)

→ KHÔNG MCP slash commands trong Claude Code. Wire = shell-out CLI qua Bash tool.

---

## Why aidlc cho Ragbot?

### Vấn đề "plan thủ công" hiện tại

Ragbot có ~20 plan ở `plans/YYMMDD-xxx/plan.md`. Workflow:

```
1. Anh viết plan tay → 1 file md
2. Claude session ship phase by phase
3. Em / anh review commits manually
4. Future session không biết "phase nào thuộc role gì"
```

**Pain point** quan sát được:
- Plan = monolith, không tách role rõ
- Bundling risk: nhiều stream / 1 commit
- Scope drift: commit subject claim paper N nhưng diff thật là subset
- Mỗi plan re-invent format

### aidlc giải quyết

**9-phase artifact gate**:
```
Phase 1: Spec       → 01-spec.md         (Architect)
Phase 2: Design     → 02-design.md       (Architect)
Phase 3: Tasks      → 03-tasks.md        (Architect, USER APPROVE)
Phase 4: Impl Plan  → 04-impl.md         (Implementer)
Phase 5: Code       → multi-commit       (Implementer)
Phase 6: Tests      → test files         (Implementer)
Phase 7: Review     → 07-review.md       (Reviewer, Quality Gate 11-item)
Phase 8: Verify     → 08-verify.md       (Auditor, load test)
Phase 9: Ship       → 09-ship.md         (Auditor)
```

Mỗi phase có schema check tự động qua aidlc Auto-Reviewer agent (Sonnet).

---

## Architecture aidlc

### Monorepo packages

```
packages/
├── core/         (@aidlc/core)        TypeScript, runner + schema validation
├── cli/          (@aidlc/cli)         CLI binary + 30 commands
└── extension/    (aidlc)              VSCode marketplace artifact
```

### CLI command groups (9 nhóm)

1. `aidlc run *` — workspace lifecycle (start, status, cancel, list)
2. `aidlc step *` — phase advance (done, next, rewind)
3. `aidlc agent *` — LLM agent invocation (run, list, define)
4. `aidlc skill *` — skill management (load, list, define)
5. `aidlc artifact *` — artifact validation (check, lint)
6. `aidlc config *` — workspace config
7. `aidlc init` — repo init
8. `aidlc audit *` — post-ship audit
9. `aidlc help` — docs

→ Tất cả qua CLI, KHÔNG slash command trong Claude Code.

---

## Runtime cost analysis

### Auto-Reviewer (Sonnet) per phase advance

```
9 phase × ~5K input + ~500 output Sonnet = ~9 calls per epic
~$0.15 per epic (Sonnet $3/$15 per MTok)
```

So với manual review: ~$1-2 per epic Opus. **Save 85-90%** cho review-only step.

### Caveat — Stream X harness limit

Tier policy CLAUDE.md cho phép Sonnet subagent. Nhưng Stream X audit: Opus-1M variant có thể KHÔNG honor `model="sonnet"` param. Auto-Reviewer có thể inline trên Opus → cost ~$1-2 per epic.

→ Verify cost thật bằng Anthropic Console post-setup.

---

## Đối chiếu với plan tay

| Aspect | Plan tay | aidlc 9-phase |
|---|---|---|
| Plan format | 1 file `.md` | 9 artifact files |
| Role separation | Implicit | Explicit (Architect/Implementer/Reviewer/Auditor) |
| Schema check | Manual (em đọc CLAUDE.md mỗi commit) | Auto-Reviewer (Sonnet) |
| Phase gate | Manual checkbox | CLI command + Auto-Reviewer |
| Commit pattern | "atomic per phase" rule trong CLAUDE.md | aidlc enforce qua step done gate |

**Recommend**: ship aidlc cho big-effort streams (Stream D PROPER, E). Plan tay vẫn OK cho small streams.

---

## Decision matrix (post-WebFetch verify)

| Question | Yes | No |
|---|---|---|
| Có >2 big-effort stream (>3 day) chờ ship? | ✅ Stream D + E | |
| Có cần multi-role coordination explicit hơn? | ⚠ tùy effort stream | |
| Anthropic Console verify Sonnet swap? | | ❌ Stream X chưa verify |
| Time install (~10 phút) + first epic (~30 phút)? | ✅ | |
| Plan tay đủ tốt cho stream nhỏ-vừa? | ✅ | |

**Score**: 3 yes / 1 no / 1 mixed.

**Recommend**: 
- **Conservative path**: defer aidlc install. Plan tay + CLAUDE.md atomic commit rule đủ cho hiện tại. Chỉ ship aidlc nếu drift vẫn xảy ra sau several focused sessions.
- **Aggressive path**: install aidlc cho Stream D PROPER NGAY. Test 9-phase flow. Ship hoặc revert sau 1 epic experiment.

---

## Setup steps

Xem `setup.md` chi tiết. Tóm tắt:

```bash
# Step 1: install
npm install -g aidlc           # khi published
# OR local dev:
git clone https://github.com/aidlc-io/aidlc.git && cd aidlc
pnpm install && pnpm build && cd packages/cli && npm link

# Step 2: init repo
cd /var/www/html/ragbot
aidlc init

# Step 3: define skills + agents
# Tạo .aidlc/skills/{architect,implementer,reviewer,auditor}.md
# Tạo .aidlc/agents/{architect,implementer,reviewer,auditor}.yaml

# Step 4: run epic
aidlc run start --stream stream-d-paper-26
aidlc agent run architect --stream stream-d-paper-26
aidlc step done 1 --stream stream-d-paper-26  # Auto-Reviewer Sonnet check

# Step 5: verify
aidlc run status --stream stream-d-paper-26
```

---

## Anthropic Claude Code patterns (related)

Per `claude-roadmap.html`:

| Pattern | Phase | aidlc apply |
|---|---|---|
| Skills | Phase 5 | ✅ aidlc skills system match |
| Subagents | Phase 5 | aidlc agents = subagent equivalent |
| Hooks | Phase 5 | aidlc artifact validation = hook-like |
| Slash commands | Phase 5 | aidlc dùng CLI thay vì slash |
| MCP | Phase 6 | aidlc KHÔNG dùng MCP (CLI-only) |
| Multi-agent teams | Phase 7 | aidlc native (4 role agents) |

→ aidlc compose với Claude Code: aidlc handle 9-phase epic, Claude Code handle execution + memory + hooks. Không trùng lặp.

---

## Reference

- Source: https://github.com/aidlc-io/aidlc
- VSCode extension repo: https://github.com/hueanmy/aidlc-extension
- VSCode marketplace: `hueanmy.aidlc`
- Open VSX: `hueanmy.aidlc`
- Claude Code roadmap (verified): https://hueanmy.github.io/claude-roadmap.html
- License: MIT
- Memory: `reference_hueanmy_repos.md`
