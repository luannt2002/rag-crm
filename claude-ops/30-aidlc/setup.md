# aidlc Setup — install CLI framework + wire vào Claude Code workflow

> **CORRECTION 2026-05-06**: Em ban đầu đoán aidlc là MCP server. **SAI**. aidlc là **CLI framework + VSCode extension** với ~30 commands tổ chức 9 nhóm. Setup này là phiên bản đã verify từ source repo `aidlc-io/aidlc`.

---

## Bản chất aidlc (verified)

| Aspect | Reality |
|---|---|
| Type | **CLI framework + VS Code extension** (KHÔNG phải MCP server) |
| Distribution | npm package `aidlc` (global install) + VSCode marketplace `hueanmy.aidlc` |
| Architecture | Monorepo với `@aidlc/core` (pure TS, no VSCode dep) + `@aidlc/cli` + `aidlc` (extension) |
| CLI commands | ~30 commands trong 9 nhóm (e.g. `aidlc run start`, `aidlc step done`, `aidlc agent run`) |
| Slash commands trong Claude Code | KHÔNG (aidlc là CLI, không expose MCP slash) |
| License | MIT |
| Install count | ~2,300 (VSCode marketplace) |

→ Wire vào Claude Code = shell-out qua Bash tool, KHÔNG phải MCP wire.

---

## Prerequisites

- [ ] Node.js >= 18
- [ ] pnpm hoặc npm
- [ ] Anthropic API key (cho LLM-driven agent run)
- [ ] Quyết định: ship aidlc cho stream nào? Recommend: Stream D PROPER hoặc Stream E (big-effort)

---

## Step 1 — Install aidlc CLI

### Option A — npm install global (preferred khi published)

```bash
npm install -g aidlc

# Verify
which aidlc
aidlc --version
aidlc --help
```

### Option B — local dev (nếu npm chưa published version mới)

```bash
cd /tmp
git clone https://github.com/aidlc-io/aidlc.git
cd aidlc
pnpm install
pnpm build

# Symlink CLI vào PATH
cd packages/cli
npm link
# Hoặc: ln -s $(pwd)/bin/aidlc /usr/local/bin/aidlc

# Verify
aidlc --help
```

### Option C — VSCode extension only (không CLI)

Install từ VSCode marketplace: `hueanmy.aidlc`.

→ Chỉ work bên trong VSCode UI, không integrate Claude Code session terminal.

**Recommend**: Option A hoặc B nếu anh dùng Claude Code CLI workflow.

---

## Step 2 — Init aidlc trong Ragbot repo

```bash
cd /var/www/html/ragbot
aidlc init
```

aidlc sẽ tạo:
- `.aidlc/` config folder
- `.aidlc/config.yaml` — runner config
- `.aidlc/skills/` — skill definitions per `.md` file
- `.aidlc/agents/` — agent role definitions

---

## Step 3 — Define skills (per role)

aidlc skills = `.md` files describing each role's responsibility. Map sang 4 role plan-by-role.md:

```bash
# Architect skill
cat > .aidlc/skills/architect.md << 'EOF'
# Architect Skill

## Responsibility
Write 01-spec.md / 02-design.md / 03-tasks.md per stream.

## Inputs
- User problem statement
- CLAUDE.md sacred rules
- Existing plans/<id>/plan.md (if any)

## Outputs
- claude-ops/30-aidlc/epics/<stream>/01-spec.md
- claude-ops/30-aidlc/epics/<stream>/02-design.md
- claude-ops/30-aidlc/epics/<stream>/03-tasks.md

## Schema check
Each artifact must have section X, Y, Z (per role-pipeline.md).
EOF

# Implementer skill
cat > .aidlc/skills/implementer.md << 'EOF'
# Implementer Skill

## Responsibility
Phase 0 TDD failing tests → Phase N+ surgical code → atomic commit per phase.

## Inputs
- 01-spec.md / 02-design.md / 03-tasks.md từ Architect

## Outputs
- Git commits theo pattern <type>(stream-X): Phase N — <summary>
- tests/unit/<related>/*.py
- src/ragbot/<files updated>

## Constraints (CLAUDE.md sacred)
- Atomic per phase commit
- HALLU=0 sacred preserve
- 4-key bot identity
- Domain-neutral
EOF

# Reviewer + Auditor tương tự
```

---

## Step 4 — Define agents (LLM-driven role)

```bash
cat > .aidlc/agents/architect.yaml << 'EOF'
name: architect
model: claude-opus-4-7
skill: architect
prompt_template: |
  You are the Architect for stream {{stream}}.
  Read CLAUDE.md sacred rules first.
  Output 01-spec.md adhering to schema in role-pipeline.md.
EOF

cat > .aidlc/agents/implementer.yaml << 'EOF'
name: implementer
model: claude-opus-4-7  # tier policy: write code = Opus
skill: implementer
prompt_template: |
  You are the Implementer.
  Read 01-spec.md, 02-design.md, 03-tasks.md.
  Phase 0: write failing tests TDD.
  Phase 1+: implement per file inventory.
  Atomic commit per phase.
EOF
```

---

## Step 5 — Run epic

### Start new epic

```bash
cd /var/www/html/ragbot
aidlc run start --stream stream-d-paper-26-rago-pareto

# aidlc tạo:
# claude-ops/30-aidlc/epics/stream-d-paper-26-rago-pareto/
#   ├── 01-spec.md (template, fill bằng aidlc agent run)
#   ├── 02-design.md
#   ├── ... 9 phase
```

### Spawn architect agent

```bash
aidlc agent run architect --stream stream-d-paper-26-rago-pareto

# Calls Anthropic API với prompt template + skill md
# Output: 01-spec.md filled
```

### Mark phase done

```bash
aidlc step done 1 --stream stream-d-paper-26-rago-pareto
# Auto-Reviewer (Sonnet) schema check 01-spec.md
# If pass: phase 2 unlocked
# If fail: REJECT, fix + retry
```

### Status

```bash
aidlc run status --stream stream-d-paper-26-rago-pareto
# Output: current phase + artifact list + Auto-Reviewer status
```

---

## Step 6 — Integrate với Claude Code session

aidlc CLI có thể chạy trong Claude Code session qua Bash tool:

```bash
# Em (Claude Opus) gọi:
aidlc run start --stream stream-d
aidlc agent run architect --stream stream-d
cat claude-ops/30-aidlc/epics/stream-d/01-spec.md  # review
aidlc step done 1 --stream stream-d
```

Workflow tích hợp:
1. Em đọc anh's request
2. Em chạy `aidlc run start` để init epic structure
3. Em (architect role) write 01-spec.md
4. Em chạy `aidlc step done 1` để Auto-Reviewer validate
5. Implementer agent ship code Phase 5+
6. Em chạy `aidlc step done 7` cho review phase
7. Anh / em (auditor) ship Phase 9 + push

---

## Step 7 — Verify install

```bash
# 1. CLI installed?
aidlc --version
which aidlc

# 2. Init done?
ls -la .aidlc/

# 3. Skills + agents defined?
ls -la .aidlc/skills/ .aidlc/agents/

# 4. Test run start
aidlc run start --stream test-aidlc-setup
ls -la claude-ops/30-aidlc/epics/test-aidlc-setup/
# Expect: 9 phase stub files

# 5. Cleanup
rm -rf claude-ops/30-aidlc/epics/test-aidlc-setup
aidlc run cancel test-aidlc-setup  # nếu có cleanup hook
```

---

## Limitations + Caveat

### 1. KHÔNG có MCP slash trong Claude Code

aidlc là CLI external. Em phải gọi qua Bash tool: `aidlc run start ...`. Không có `/aidlc-start` slash trong Claude Code session natively.

### 2. Stream X harness gap

Auto-Reviewer (Sonnet via aidlc agent) cost ~$0.15 per epic. Stream X harness limit có thể inline trên Opus → cost up. Verify Anthropic Console sau setup đầu tiên.

### 3. Agent file write requires Anthropic API key

`aidlc agent run architect` calls Anthropic API trực tiếp với key trong `ANTHROPIC_API_KEY` env. Cost trực tiếp vào billing dashboard.

### 4. Synergy với plan tay

aidlc 9-phase overkill cho:
- Stream L Phase 4 (2-3h)
- Doc-only changes
- Single-file refactor

→ Plan tay vẫn dùng cho small streams.

---

## Decision matrix updated

| Aspect | Reality | Verdict |
|---|---|---|
| Install effort | npm global hoặc local dev | 5-10 phút |
| Wire vào Claude Code | Shell-out CLI (no MCP) | Trivial |
| Per-epic cost | ~$0.15 (Sonnet review) | Acceptable |
| Time to first epic | ~30 phút setup + ~10 phút first 01-spec | 40 phút total |
| Gives benefit cho | Stream >3 day effort | Stream D PROPER, E |

**Recommend**: ship aidlc CHỈ cho Stream D PROPER (khi anh quyết). Skip cho Stream L Phase 4 (small).

---

## Anthropic Claude Code patterns liên quan (verify từ claude-roadmap)

Per `claude-roadmap.html` (hueanmy.github.io):

| Pattern | Status Ragbot |
|---|---|
| **Skills** (Phase 5) | aidlc dùng pattern này — `.aidlc/skills/*.md` |
| **Subagents** | Em đã spawn `Agent({subagent_type, model})` (Stream X harness limit) |
| **Hooks** | ✅ APPLIED — `.claude/settings.json` PostToolUse |
| **Slash commands** | Native Claude Code: `/help`, `/plan`, `/clear`, `/cost` |
| **MCP** (Phase 6) | aidlc KHÔNG dùng. Future option nếu wire MCP server riêng |

→ aidlc complement với Claude Code: aidlc handle 9-phase epic gate, Claude Code handle execution + memory + hooks.

---

## Reference

- aidlc source: https://github.com/aidlc-io/aidlc
- aidlc VSCode extension: https://github.com/hueanmy/aidlc-extension
- Claude roadmap: https://hueanmy.github.io/claude-roadmap.html
- Anthropic Claude Code docs: https://docs.anthropic.com/en/docs/claude-code
- License: MIT (aidlc) + permissive (claude-roadmap educational)
