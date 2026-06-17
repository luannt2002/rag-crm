# SECRET_SCRUB_WORKFLOW — Domain-neutral & tenant-literal scrub

> Detail file for the **Domain-neutral rule** and **Tenant-identifier / secret literals** sections of `CLAUDE.md`. Reference the rule there; reach for this file when you actually have to clean up a violation.

---

## Rule recap (TUYỆT ĐỐI)

Code hệ thống KHÔNG support riêng bất kỳ khách hàng, ngành, hay lĩnh vực nào.

Forbidden in any tracked `.py / .md / .json / .yml / .yaml / .sh / .toml / .cfg / .ini` file:

- Tên công ty / khách hàng / thương hiệu (brand hostnames, customer subdomains)
- Credential bất kỳ dạng nào: password, API key, DB DSN có password inline, bearer token
- Hostname / IP nội bộ thuộc tenant (ví dụ `*.<brand>.vn`, IP LAN cụ thể)
- Tên user DB cụ thể của tenant (nếu không phải generic `postgres` / `app`)
- Domain-specific abbreviations (spa, education, finance) hardcoded trong code chung
- Golden test questions trong code chung — phải file riêng per bot

**Storage layer for secrets**: `.env` (gitignored). Code đọc qua `os.getenv()` hoặc pydantic `BaseSettings(env_file=".env")`. Add placeholder (NOT real value) to `.env.example`.

---

## Code patterns

```python
# WRONG — tenant literal in code
BASE_URL = "https://backendsg.<brand>.vn:3004"
password = "<brand>.vn123"
dsn = "postgresql://postgres:<brand>.vn123@10.0.1.160:5432/ragbot_v2_dev"

# WRONG — even in a comment, docstring, or report markdown
# Demo trên backendsg.<brand>.vn:3004

# RIGHT — env-driven
import os
BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
dsn = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC")
if not dsn:
    raise RuntimeError("DATABASE_URL env var required")
```

For **docs / plans / reports markdown**: use generic placeholders (`<server-host>`, `<prior-project>`) instead of real names. Historical reports already containing real names → redact in-place; do NOT rewrite unrelated content.

---

## Scrub workflow when a violation is found

1. **Enumerate** all hits:
   ```bash
   grep -rn -i "<brand>" . --exclude-dir=.venv --exclude-dir=.git
   ```
2. **Fix** each file:
   - Code → read from env (`os.getenv(...)`).
   - Docs → redact to placeholder.
3. **Update `.env.example`** with the new env-var name (placeholder only — never real value).
4. **Verify** grep returns 0 hits.
5. **If credential already pushed to remote**: rotate the credential FIRST. Only then consider `git filter-repo` / BFG to remove from history. History rewrite = destructive force-push, requires explicit user approval.
6. **Commit honestly**, e.g. `refactor: move tenant-specific config to env per domain-neutral rule`. Do NOT disguise scope in the commit message — git history is a shared record; deceptive commits = trust violation.

---

## CẤM (forbidden behaviors)

- Commit with vague/misleading message to hide a scrub (e.g. `chore: cleanup` while actually removing brand names).
- "Quiet" scrub to "avoid embarrassment". If scrubbing is correct, an honest commit is correct.
- Adding a literal for *another* tenant — even if user asks. Push it into env, done.
- Inventing a "shared util" file that secretly holds tenant constants — same violation, longer name.

---

## Pre-commit recommendation

Run from project root before each commit on tracked files:

```bash
# Replace <brand> with the real brand list from .env (do NOT inline it here).
ALLOWED="postgres app ragbot localhost"
PATTERN="$(grep -E '^TENANT_BRAND_[A-Z]+=' .env | cut -d= -f2 | paste -sd'|')"
[ -z "$PATTERN" ] || git diff --cached --name-only \
  | xargs -I{} grep -lE "$PATTERN" {} 2>/dev/null
```

If the command lists any file → STOP, scrub, re-stage, re-commit.
