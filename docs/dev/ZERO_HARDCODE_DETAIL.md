# ZERO_HARDCODE_DETAIL — sweep patterns, grep helpers, exceptions

> Detail file for the **Zero hardcode rule** section of `CLAUDE.md`. Use the short rule there; come here for grep snippets, common violation patterns, and the whitelist.

---

## Rule recap (TUYỆT ĐỐI)

KHÔNG MỘT CON SỐ NÀO được inline trong file code ngoài `shared/constants.py`.

- ALL config from `system_config` DB table (Redis cached).
- ALL thresholds configurable via `pipeline_config`.
- ALL default values MUST be defined in `shared/constants.py` and imported.
- NO hardcoded role strings — use `shared/rbac.py` numeric levels (`require_min_level(60)` etc.).
- `shared/constants.py` = SINGLE SOURCE OF TRUTH for ALL defaults.
- `0` and `0.0` are OK as literals (zero = disabled/none, not magic).
- String literals in SQL column names, event subjects = OK (defined in constants).

### Mini WRONG vs RIGHT

```python
# WRONG — inline number
def chunk(text, chunk_size=1024): ...
timeout = 30
max_tokens = 450

# RIGHT — import from constants
from ragbot.shared.constants import DEFAULT_CHUNK_SIZE, DEFAULT_LLM_TIMEOUT_S
def chunk(text, chunk_size=DEFAULT_CHUNK_SIZE): ...
timeout = DEFAULT_LLM_TIMEOUT_S
```

---

## Self-verification BEFORE COMMIT

Run on every `git commit` touching hot-path code (`src/ragbot/**`):

```bash
# 1. Magic numbers in staged code (excluding constants.py):
git diff --cached --name-only | grep '\.py$' | grep -v 'constants.py' \
  | xargs grep -nE '\b(1024|256|500|1000|2000|3000|4000|5000|8000|450|300|60|30)\b' 2>/dev/null

# 2. AI model names hardcoded (excluding constants.py / settings.py):
git diff --cached --name-only | grep '\.py$' | grep -v 'constants\|settings' \
  | xargs grep -nE '"gpt-\d|"claude-\d|"text-embedding-|"cohere/' 2>/dev/null
```

If either grep prints anything → STOP, lift the literal into `shared/constants.py`, import it, re-stage, re-commit.

---

## Common violation patterns (seen in this codebase)

- **Fallback default in config call**: `cfg.get_int("foo", 1024)` — always import `DEFAULT_FOO_*` from constants and pass it instead.
- **Duplicate if/else default**: `if cfg is not None: x = cfg.get(k, 1024) else: x = 1024` — DRY through a single constant.
- **Algorithm weights inside a formula**: `score = 0.4*a + 0.35*b` — lift into a module-level dict (`_WEIGHTS`) at minimum.
- **Signature defaults**: `def foo(x: int = 500)` — constant.
- **Threshold compares**: `if n > 1500` — `DEFAULT_X_THRESHOLD`.
- **Repeated string literals** for SQL columns / event subjects — define once in `constants.py`, import.

---

## Exceptions (whitelist allowed inline)

- `0`, `0.0` — disabled / none / zero semantics.
- `1`, `1.0` — identity / initialization / percentage max.
- `100` — percentage semantics (0-100%).
- Indices: `items[0]`, `lines[:5]`.
- `range(10)` in tests.
- Alembic migration files (immutable history) — DDL may reference whatever literal was in the prior schema.

If a literal is not on this whitelist and not "magic" → push it into `constants.py` BEFORE committing.

---

## Pre-commit hook (recommended)

A reusable hook would chain the two grep blocks above and fail the commit on any non-zero hit. Hook lives in `scripts/pre-commit-hook.sh` — extend it there rather than inventing a new path.
