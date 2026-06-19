# docs/ — Index

Single navigable entry point for `docs/`. Project-root truth-of-record files
(`README.md`, `CLAUDE.md`, `RAGBOT_MASTER.md`, `RAGBOT_STEP_PIPELINE.md`,
`STATE_SNAPSHOT.md`) live at the repo root, not here.

---

## 🚀 Getting started
- [QUICKSTART.md](QUICKSTART.md) — fastest path to a running stack
- [ONBOARDING_GUIDE.md](ONBOARDING_GUIDE.md) — new-contributor walkthrough
- [README-ROLE.md](README-ROLE.md) — role / responsibility map

## 🏗️ Architecture canon (`master/` — A→P)
- [master/01-A-foundation-architecture.md](master/01-A-foundation-architecture.md) · [02-B-seven-layers](master/02-B-seven-layers.md) · [03-C-cross-axes](master/03-C-cross-axes.md)
- [master/04-D-pipeline-orchestration.md](master/04-D-pipeline-orchestration.md) · [05-E-cross-cutting-patterns](master/05-E-cross-cutting-patterns.md) · [06-F-python-build-spec](master/06-F-python-build-spec.md)
- [master/07-G-legacy-insights.md](master/07-G-legacy-insights.md) · [08-H-enforcement-acceptance](master/08-H-enforcement-acceptance.md) · [09-I-kickoff-reference](master/09-I-kickoff-reference.md)
- [master/10-J-channel-integration.md](master/10-J-channel-integration.md) · [11-K-pipeline-code-mapping](master/11-K-pipeline-code-mapping.md) · [12-L-research-competitive](master/12-L-research-competitive.md)
- [master/13-M-roadmap-history.md](master/13-M-roadmap-history.md) · [14-N-ingest-model-upgrade-analysis](master/14-N-ingest-model-upgrade-analysis.md) · [15-O-anti-hallu-tuning](master/15-O-anti-hallu-tuning.md) · [16-P-rago-schema](master/16-P-rago-schema.md)
- [master/DB_SCHEMA_AND_MIGRATION_MINDSET.md](master/DB_SCHEMA_AND_MIGRATION_MINDSET.md)

## 🔀 Pipeline & flows
- [FLOW_INGEST_DETAIL.md](FLOW_INGEST_DETAIL.md) · [FLOW_QUERY_DETAIL.md](FLOW_QUERY_DETAIL.md)
- [PROJECT_FLOWS.md](PROJECT_FLOWS.md) · [PROJECT_FLOW_OVERVIEW.md](PROJECT_FLOW_OVERVIEW.md) · [naming-convention-flow.md](naming-convention-flow.md)

## 🛠️ Dev runbooks (`dev/`)
- Local/test: [CODER_LOCAL_DOCKER_RUNBOOK](dev/CODER_LOCAL_DOCKER_RUNBOOK.md) · [CODER_LOADTEST_RUNBOOK](dev/CODER_LOADTEST_RUNBOOK.md) · [LOAD_TEST_RUNBOOK](dev/LOAD_TEST_RUNBOOK.md) · [TEST_ISOLATION_GUIDE](dev/TEST_ISOLATION_GUIDE.md) · [RAGAS_METRICS_RUNBOOK](dev/RAGAS_METRICS_RUNBOOK.md)
- DB/ops: [BACKUP_RESTORE_RUNBOOK](dev/BACKUP_RESTORE_RUNBOOK.md) · [RLS_ACTIVATION_RUNBOOK](dev/RLS_ACTIVATION_RUNBOOK.md) · [NON_SUPERUSER_DSN_RUNBOOK](dev/NON_SUPERUSER_DSN_RUNBOOK.md) · [CORPUS_CLEAN_RUNBOOK](dev/CORPUS_CLEAN_RUNBOOK.md) · [HEALTH_PROBE_RUNBOOK](dev/HEALTH_PROBE_RUNBOOK.md)
- Diagnose/audit: [DIAGNOSTIC_GUIDE](dev/DIAGNOSTIC_GUIDE.md) · [AUDITOR_WORKFLOW](dev/AUDITOR_WORKFLOW.md) · [CROSS_TENANT_AUDIT_RUNBOOK](dev/CROSS_TENANT_AUDIT_RUNBOOK.md) · [PIPELINE_ANALYSIS_FULL](dev/PIPELINE_ANALYSIS_FULL.md)
- Reference/mindset: [CONFIG_REFERENCE](dev/CONFIG_REFERENCE.md) · [RBAC_PERMISSIONS](dev/RBAC_PERMISSIONS.md) · [PHAN_QUYEN_BUSINESS](dev/PHAN_QUYEN_BUSINESS.md) · [CONSULTANT_BOT_BEHAVIOR_RULES](dev/CONSULTANT_BOT_BEHAVIOR_RULES.md) · [EMBEDDING_RERANKER_ANALYSIS](dev/EMBEDDING_RERANKER_ANALYSIS.md) · [RAG_STACK_VIETNAM_RECOMMENDATION](dev/RAG_STACK_VIETNAM_RECOMMENDATION.md)
- **Sacred (CLAUDE.md-referenced)**: [IDENTITY_RULE_DETAIL](dev/IDENTITY_RULE_DETAIL.md) · [ZERO_HARDCODE_DETAIL](dev/ZERO_HARDCODE_DETAIL.md) · [SECRET_SCRUB_WORKFLOW](dev/SECRET_SCRUB_WORKFLOW.md)

## ⚙️ Ops (`ops/`)
- [ops/RUNBOOK.md](ops/RUNBOOK.md) · [ops/DISASTER_RECOVERY.md](ops/DISASTER_RECOVERY.md) · [ops/WAVE_A_ROLLOUT_RUNBOOK.md](ops/WAVE_A_ROLLOUT_RUNBOOK.md)
- [OPS_POOL_SIZING.md](OPS_POOL_SIZING.md) · [PERFORMANCE_TUNING.md](PERFORMANCE_TUNING.md)

## 🔒 Security
- [SECURITY.md](SECURITY.md) · [SECURITY_POLICIES.md](SECURITY_POLICIES.md) · [JWT_KEY_ROTATION.md](JWT_KEY_ROTATION.md)

## 🤖 Bot owner & sysprompt templates (`templates/`)
- [BOT_OWNER_TOOLKIT.md](BOT_OWNER_TOOLKIT.md) · [BOT_SYSTEM_PROMPT_TEMPLATE.md](BOT_SYSTEM_PROMPT_TEMPLATE.md)
- [templates/SYSPROMPT_TEMPLATE.md](templates/SYSPROMPT_TEMPLATE.md) · [TENANT_GUIDE_SYSPROMPT_AND_CORPUS](templates/TENANT_GUIDE_SYSPROMPT_AND_CORPUS.md) · [SYSPROMPT_FAITHFULNESS_BUDGET](templates/SYSPROMPT_FAITHFULNESS_BUDGET.md) · [RAG_FRIENDLY_SHEET_TEMPLATE](templates/RAG_FRIENDLY_SHEET_TEMPLATE.md) · [AUTO_ENRICH_CONTENT_REVIEW](templates/AUTO_ENRICH_CONTENT_REVIEW.md)
- Examples: [healthcare](templates/sysprompt_examples/healthcare.md) · [finance](templates/sysprompt_examples/finance.md)
- [sysprompt/self_rag_critique_template.md](sysprompt/self_rag_critique_template.md)

## 📡 Channels & API
- [MULTI_CHANNEL_INTEGRATION.md](MULTI_CHANNEL_INTEGRATION.md) · [channels/ZALO_MASTER.md](channels/ZALO_MASTER.md)
- [API_REFERENCE_V2.md](API_REFERENCE_V2.md) · [api/UPLOAD_STREAMING.md](api/UPLOAD_STREAMING.md)

## 🧪 Testing & troubleshooting
- [TESTING.md](TESTING.md) · [testing/KICH_BAN_TEST_RAGBOT_v1.md](testing/KICH_BAN_TEST_RAGBOT_v1.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [V2_MIGRATION_BUG_LESSONS.md](V2_MIGRATION_BUG_LESSONS.md) · [V9_PHASE4_LOAD_TEST_RUNBOOK.md](V9_PHASE4_LOAD_TEST_RUNBOOK.md)

## 📐 ADR (Architecture Decision Records — `adr/`)
- [adr/0001-rls-enforce-app-role.md](adr/0001-rls-enforce-app-role.md) · [adr/0001-metadata-extraction-hybrid.md](adr/0001-metadata-extraction-hybrid.md)

---

> Archived (out of the live tree, recoverable via git): `docs/_archive_stale_20260619/`
> — superseded sysprompt backup/history snapshots. CLAUDE.md forbids
> sysprompt backup files; bot prompts are tracked via alembic migrations.
