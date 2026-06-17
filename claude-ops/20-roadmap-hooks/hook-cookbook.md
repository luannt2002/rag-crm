# Hook Cookbook — Add hook mới cho Claude Code session

> **Mục đích**: 6 mẫu hook chuẩn cho Ragbot. Anh copy + adapt vào `.claude/settings.json`.

---

## Hook lifecycle events

| Event | Khi fire | Use case |
|---|---|---|
| `PreToolUse` | Trước khi Claude chạy tool | Block tool nếu condition không met |
| `PostToolUse` | Sau khi tool finish | Validate output, run linter |
| `UserPromptSubmit` | Khi user gửi prompt | Augment context, log usage |
| `Stop` | Sau khi Claude finish session | Emit summary, push notification |
| `Notification` | Khi Claude gửi notification | Custom alert routing |

---

## Mẫu 1 — PostToolUse: validate file edit (current pattern)

**Use case**: validate `shared/constants.py` sau Edit.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'jq -r \".tool_input.file_path // empty\" | grep -q \"src/ragbot/shared/constants.py\" && bash scripts/validate_constants.sh || exit 0'"
          }
        ]
      }
    ]
  }
}
```

**Adapt cho file khác** (vd: `query_graph.py` — orchestration hot path):

```bash
jq -r ".tool_input.file_path // empty" | grep -q "src/ragbot/orchestration/query_graph.py" \
  && bash scripts/validate_query_graph.sh || exit 0
```

---

## Mẫu 2 — PreToolUse: block force push to main

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r \".tool_input.command\" | grep -E \"git push.*--force.*main\" && { echo 'Force push to main blocked'; exit 2; } || exit 0"
          }
        ]
      }
    ]
  }
}
```

Exit 2 = block + show message to Claude.

---

## Mẫu 3 — PostToolUse: post-commit hooks

**Use case**: sau git commit, kick `cost_audit.py sonnet-leak` để verify CI gate.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'jq -r \".tool_input.command\" | grep -q \"git commit\" && python scripts/cost_audit.py sonnet-leak || exit 0'"
          }
        ]
      }
    ]
  }
}
```

---

## Mẫu 4 — UserPromptSubmit: log session start

**Use case**: log mỗi prompt vào `_runtime/session_log.jsonl` để audit.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "jq -c '{ts: now|todate, prompt: .prompt[:200]}' >> claude-ops/_runtime/session_log.jsonl"
          }
        ]
      }
    ]
  }
}
```

---

## Mẫu 5 — Stop: end-of-session cost report

**Use case**: sau Claude finish, in cost summary của session.

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python scripts/cost_audit.py today | tail -10"
          }
        ]
      }
    ]
  }
}
```

---

## Mẫu 6 — PostToolUse: auto-test sau Edit src/

**Use case**: chạy unit test liên quan sau mỗi Edit `src/ragbot/`.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'fp=$(jq -r \".tool_input.file_path // empty\"); [[ \"$fp\" =~ src/ragbot/shared/(.+)\\.py ]] && .venv/bin/pytest tests/unit/test_${BASH_REMATCH[1]}.py -q 2>/dev/null; exit 0'"
          }
        ]
      }
    ]
  }
}
```

**Lưu ý**: nếu test slow → hook block Claude. Chỉ enable nếu test < 5s/file.

---

## Anti-pattern hook

| ❌ Sai | Vì sao | Đúng |
|---|---|---|
| Hook chạy LLM call (Anthropic API) | Cost up runtime mỗi Edit | Pure local script |
| Hook write file ngoài repo | Side effect, owner khó debug | Stay trong `claude-ops/_runtime/` |
| Hook >5s | Block Claude, UX kém | Async background process hoặc skip |
| Hook silent fail | Bug ẩn không catch | exit code rõ + log |

---

## Test hook trước khi commit

```bash
# Manual fire hook payload
echo '{"tool_input": {"file_path": "src/ragbot/shared/constants.py"}}' | \
  bash -c 'jq -r ".tool_input.file_path // empty" | grep -q "constants.py" && bash scripts/validate_constants.sh'
```

---

## Limitation Anthropic Claude Code hooks

1. `.claude/settings.json` là **personal**, gitignored — không share giữa devs.
2. **Team-shared hooks** ở `.claude/settings.json` (commit), nhưng có thể conflict với personal.
3. Hook event không bao gồm Subagent (Agent tool) invocation events (per Anthropic CLI 2026 spec).
4. Hook timeout: default 30s — nếu hook chạy >30s sẽ kill.

---

## Reference

- Anthropic Claude Code hooks docs: https://docs.anthropic.com/en/docs/claude-code/hooks
- Source: `hueanmy/claude-roadmap` Phase 5 example
- Current Ragbot hooks: `.claude/settings.json` (personal, gitignored)
- Validator scripts: `scripts/validate_constants.sh`
