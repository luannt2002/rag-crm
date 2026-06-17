# README-ROLE — Format Template cho dự án RAGbot

> File này define format chuẩn cho `README.md`. KHÔNG xoá file này — anh em tham
> chiếu khi rewrite README mỗi version.

---

## Mục đích

`README.md` = **bộ mặt dự án** — file đầu tiên user / leader / opensource
contributor đọc. Phải đạt 4 chuẩn:

1. **UI/UX đẹp** — emoji vừa đủ, badges, table align, code block syntax highlight
2. **Detail + dẫn chứng thật** — mọi claim có file:line OR `reports/*.md` reference
3. **Cool + thuyết phục** — show skill + keyword + battle-tested production
4. **Hấp dẫn** — opensource quality, Vietnamese-friendly, ready to fork

---

## CẤU TRÚC BẮT BUỘC (đúng thứ tự, KHÔNG đảo)

### 1. Hero Section (10-20 dòng)

- Tên dự án + 1 dòng tagline (≤120 chars, có emoji)
- 1-2 paragraph về **dự án LÀ gì + dùng để LÀM gì + dành cho AI** (≤500 chars)
- Badges row (6-10 badges):
  - Python version, Tests, Migrations, Score (link benchmark), Faithfulness, License
  - Optional: build, coverage, downloads, issues, PRs
- Status banner: VERSION X — pilot/beta/production

### 2. ⚡ Numbers that matter (REAL measured)

Bảng metrics REAL với **3 cột**: Metric | Value | Source (file:line OR reports/*)

```markdown
| Metric | Value | Source |
|---|---|---|
| Faithfulness (DeepEval 100q) | **98.5%** | reports/deepeval_run_*.json |
```

KHÔNG bịa số. Nếu chưa đo → ghi "TBD load test Sprint X".

### 3. 🏗 Architecture (T1 → T2 → T3 organization)

- ASCII pipeline diagram (15 nodes)
- T1 capabilities table (status: ✅ ON / ⏳ Ready / ❌ Off)
- T2 capabilities table
- T3 architecture bullets

### 4. 🚀 Project structure + ADR highlights (gần nhau)

```
src/ragbot/
  application/
    ports/        ← 15 ports (Strategy registry pattern)
    services/    ← business logic
  ...
```

**ADR highlights** (3-5 bullets max — link tới `docs/adr/` nếu chi tiết):
- Why LangGraph (15 conditional nodes) over linear chain
- Why pgvector instead of dedicated vector DB (cost + tenant isolation)
- Why Redis Streams over Kafka (lighter footprint, multi-tenant)

### 5. 📚 Truth-of-record (mỗi key XUỐNG DÒNG, KHÔNG inline gộp)

```markdown
- **Truth-of-record**: [STATE_SNAPSHOT.md](STATE_SNAPSHOT.md)  
  Always-current snapshot, read first in any new session.
- **Architecture spec**: [RAGBOT_MASTER.md](RAGBOT_MASTER.md) (v1.6)  
  → split into [docs/master/](docs/master/) sub-files A-M.
- **Plan changelog**: [plans/PLAN_V0_CHANGELOG.md](plans/PLAN_V0_CHANGELOG.md).
- **Audit verdict**: [reports/SPRINT9_AUDIT_VERDICT.md](reports/SPRINT9_AUDIT_VERDICT.md).
- **Benchmark rubric**: [reports/BEST_PRACTICE_BENCHMARK_2026.md](reports/BEST_PRACTICE_BENCHMARK_2026.md).
```

### 6. 🛡 Security + Multi-tenancy (1 paragraph + bullet)

Highlight 3-key identity, RBAC, RLS, JWT. Brief — chi tiết link
`docs/master/05-security.md`.

### 7. 📊 Quality gates — 8-axis score

Bảng axis score (post-load-test data). Cite source mỗi axis.

### 8. 🎯 What's NOT ready yet (HONEST)

3-7 bullets về gap đang chờ fix. KHÔNG self-promote.

### 9. 🗺 Roadmap (compact)

Sprint 13 / Sprint 14+ headlines. Link `plans/2604XX-Sprint-XX-roadmap/plan.md`.

### 10. 📖 Docs split (CHỈ note + description, KHÔNG paste content)

```markdown
- **Quickstart**: [docs/QUICKSTART.md](docs/QUICKSTART.md)  
  Setup, env vars, first chat in 5 minutes.
- **API Reference**: [docs/API.md](docs/API.md)  
  All endpoints grouped by RBAC level. OpenAPI at `/docs`.
- **Testing**: [docs/TESTING.md](docs/TESTING.md)  
  pytest, DeepEval, kịch bản test, load test.
- **Dev workflow**: [docs/DEV_WORKFLOW.md](docs/DEV_WORKFLOW.md)  
  Plan-before-code, validate-before-edit, mindset rules.
- **Contributing**: [CONTRIBUTING.md](CONTRIBUTING.md)
```

### 11. License + Credits

---

## QUY TẮC FORMATTING TUYỆT ĐỐI

### Markdown style

- **Headers**: H1 = title only, H2 = section, H3 = subsection. KHÔNG nhảy cấp.
- **Tables**: column align với `:---:` cho center, `:---` cho left, `---:` cho right
- **Code blocks**: language tag mọi block (`bash`, `python`, `sql`, `markdown`)
- **Links**: relative path cho repo files, absolute URL cho external
- **Lists**: 2-space indent cho sub-list. KHÔNG mix bullet styles (-/*/+)
- **Emphasis**: `**bold**` cho metric value, `*italic*` cho note, `\`code\`` cho identifier

### Numbers + claims

- **Mọi số PHẢI có source** (file:line, reports/*, DeepEval JSON)
- **KHÔNG self-promote** — score 9/10 chỉ ghi nếu axis score breakdown justify
- **HONEST** — gap → liệt kê rõ trong section "What's NOT ready"

### Brand-neutral

- 0 brand literal trong README (CLAUDE.md domain-neutral rule)
- Ví dụ dùng `<demo-bot-slug>` placeholder

### Length budget

- Total: **400-600 lines** (KHÔNG quá 700)
- Hero + numbers: 60 lines
- Architecture: 150 lines
- Truth-of-record + docs split: 50 lines
- Quality gates + roadmap + license: 150 lines
- Buffer: 100-200 lines

### Sub-files BẮT BUỘC tách

KHÔNG paste content full vào README. Tách:

- `docs/QUICKSTART.md` — setup, env, first chat
- `docs/API.md` — all routes + RBAC + curl examples + OpenAPI link
- `docs/TESTING.md` — pytest, DeepEval, load test, kịch bản
- `docs/DEV_WORKFLOW.md` — plan-before-code, validate-before-edit
- `docs/SECURITY.md` — 3-key, RBAC, RLS, JWT, threat model
- `docs/PERFORMANCE.md` — load test results, SLA, capacity planning

README chỉ note + description + link.

---

## VERIFY CHECKLIST trước khi commit README

```bash
# 1. Brand-neutral
grep -niE "thula|innocom|medispa|0926" README.md

# 2. Links not broken
grep -oE "\]\(([^)]+)\)" README.md | grep -oE "\(([^)]+)\)" | tr -d '()' | grep -v "^http" | while read f; do test -e "$f" || echo "BROKEN: $f"; done

# 3. Numbers cite source (mỗi metric có file:line OR reports/*)
# Manual review

# 4. Length 400-600 lines
wc -l README.md
```

---

## VERSION HISTORY

- v1 (2026-04-29): T1/T2/T3 framing, brand scrub, 8-axis 8.5/10 self-rated
- v1.x (2026-04-30 planned): post load-test data, 9.5+/10 path, sub-files split
