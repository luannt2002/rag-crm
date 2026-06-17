# 20-roadmap-hooks — Claude Code lifecycle hooks

> **Status**: ✅ APPLIED (1 hook, variant từ `hueanmy/claude-roadmap` Phase 5 example).
>
> **Where**: `.claude/settings.json` PostToolUse hook fires sau mỗi Edit/Write trên `src/ragbot/shared/constants.py`. Runs `scripts/validate_constants.sh` để guard zero-hardcode + no-version-ref.

---

## Why hook?

### Race-lesson từ memory `project_fix_all_complete.md`

> "Race-lesson: serialise `shared/constants.py` edits when multiple orchestrators run."

Nếu Claude session A edit constants.py + chưa kịp commit + Claude session B cũng edit cùng file → conflict / drift. Hook tự động validate sau mỗi Edit, fail-loud nếu break rule.

### Pattern source

`hueanmy/claude-roadmap` Phase 5 ship `hooks-example/settings.json` chạy `ruff format` sau mỗi Edit. Em adapt:
- Same trigger pattern (PostToolUse + matcher Edit|Write)
- File-path filter (chỉ fire khi đụng `shared/constants.py`)
- Custom validator (không phải ruff — em viết `validate_constants.sh` cho zero-hardcode + no-version-ref guard)

---

## Hook hiện tại

### `.claude/settings.json` (gitignored — personal experiment)

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
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

### `scripts/validate_constants.sh` (committed)

3 check:
1. Version-ref grep: `(_v[0-9]|_legacy|EMBEDDING_COLUMN_(V[0-9]|LEGACY))` → 0 hits
2. Sprint/temporal grep: `Sprint S?[0-9]+|V[0-9]+\.[0-9]+\.[0-9]+|Round V[A-Z]|post-V[0-9]+` → 0 hits
3. ruff format check (best-effort, skip nếu ruff không có)

Exit 0 = clean, exit 1 = block.

---

## Verify hook hoạt động

```bash
# Trigger hook (em sẽ Edit constants.py)
echo "DEFAULT_TEST_v1: Final[int] = 1" >> src/ragbot/shared/constants.py
# Hook should fire sau Claude Edit-event:
#   [validate_constants] ✗ version-ref tokens in constants.py:
#   299:DEFAULT_TEST_v1: Final[int] = 1

# Manual verify:
bash scripts/validate_constants.sh
```

---

## Khi nào hook fire?

| Trigger | Action |
|---|---|
| Claude Edit tool trên `src/ragbot/shared/constants.py` | hook fires → validate_constants.sh → block nếu fail |
| Claude Write tool trên cùng file | same |
| Edit file khác | hook NOT fired (matcher path filter) |

---

## Limitation hiện tại

- 1 hook only (constants.py guard)
- Personal `.claude/settings.json` (gitignored) — agent session khác không inherit
- Không có Pre-commit version (chỉ react sau Edit)

---

## Add hook mới — xem `hook-cookbook.md`

Hook patterns:
- **PreToolUse** — chặn Edit nếu condition không met
- **PostToolUse** — validate sau Edit (current pattern)
- **Stop** — emit summary sau Claude session
- **UserPromptSubmit** — augment context khi user gửi prompt

---

## Reference

- Source: https://github.com/hueanmy/claude-roadmap (Phase 5 hooks-example)
- Anthropic docs: https://docs.anthropic.com/en/docs/claude-code/hooks
- Implementation: `.claude/settings.json` + `scripts/validate_constants.sh`
- Memory: `project_cost_audit_shipped.md` (companion validate_constants section)
