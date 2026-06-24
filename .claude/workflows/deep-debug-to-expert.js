export const meta = {
  name: 'deep-debug-to-expert',
  description: 'Per-flow deep-debug → expert: 5-axis scorecard + adversarial verify + TDD fix in an isolated git worktree, for every audited flow',
  whenToUse: 'Run to take each ragbot flow to expert-grade (codifies the Ingest reference run). Diagnose is read-only; fixes land in per-flow worktrees you review before merging.',
  phases: [
    { title: 'Diagnose', detail: 'read-only 5-axis scorecard + fix-list per flow' },
    { title: 'Verify', detail: 'adversarially verify each CRIT/HIGH finding' },
    { title: 'Fix', detail: 'TDD fixes to expert in an isolated git worktree per flow' },
  ],
}

// Protocol: docs/dev/DEEP_DEBUG_TO_EXPERT_PROTOCOL.md
const PROTOCOL = `Run the deep-debug→expert protocol (docs/dev/DEEP_DEBUG_TO_EXPERT_PROTOCOL.md):
5-axis per file — (1) Functional CHUẨN/THIẾU/THỪA/LỆCH/LỖI + score/10 + bugs (evidence file:line);
(2) Comment/doc (VN→EN, strip version/temporal refs, docstring coverage);
(3) Clean-code/OOP/pattern (SOLID, helper reuse, Strategy+Port+Registry+Null+DI, god-file LOC/fn);
(4) Dead-code (0 callers — VERIFY it is not a route handler / Protocol method before flagging);
(5) Perf/tech-debt. rule#0: evidence for every claim; label SỰ THẬT (runtime-verified) vs GIẢ THUYẾT.
Sacred: no app-inject/override answer, domain-neutral, 4-key, zero-hardcode, narrow-except, no-version-ref, model-tier.`

const SCORECARD = {
  type: 'object',
  required: ['flow', 'flow_grade', 'summary', 'files'],
  additionalProperties: false,
  properties: {
    flow: { type: 'string' },
    flow_grade: { type: 'string', enum: ['EXPERT', 'OK_MINOR', 'HAS_GAPS', 'BROKEN'] },
    summary: { type: 'string' },
    files: {
      type: 'array',
      items: {
        type: 'object',
        required: ['path', 'score', 'verdict', 'action'],
        additionalProperties: false,
        properties: {
          path: { type: 'string' },
          score: { type: 'number' },
          verdict: { type: 'string', description: 'CHUẨN/THIẾU/THỪA/LỆCH/LỖI per axis, short' },
          vn_comments: { type: 'number' },
          version_refs: { type: 'number' },
          clean_note: { type: 'string', description: 'SOLID/OOP/pattern/helper/god-file note' },
          dead_funcs: { type: 'string', description: 'verified-unused funcs, or "none"' },
          action: { type: 'string', enum: ['LEAVE', 'CLEAN', 'FIX'] },
        },
      },
    },
    fixes: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'file', 'severity', 'used', 'expert_fix'],
        additionalProperties: false,
        properties: {
          id: { type: 'string' },
          file: { type: 'string' },
          severity: { type: 'string', enum: ['CRIT', 'HIGH', 'MED', 'LOW'] },
          used: { type: 'boolean', description: 'true → fix; false → comment-out + note (do NOT delete)' },
          evidence: { type: 'string' },
          expert_fix: { type: 'string' },
          target_score: { type: 'number' },
          ab_metric: { type: 'string' },
        },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['verdict', 'reason'],
  additionalProperties: false,
  properties: {
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'downgraded'] },
    corrected_severity: { type: 'string', enum: ['CRIT', 'HIGH', 'MED', 'LOW', 'NONE'] },
    reason: { type: 'string' },
  },
}

const FIX_RESULT = {
  type: 'object',
  required: ['flow', 'files_changed', 'verification', 'rescore'],
  additionalProperties: false,
  properties: {
    flow: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    tests_added: { type: 'array', items: { type: 'string' } },
    verification: { type: 'string', description: 'pytest subset result + ruff HEAD==NOW + AST-identical for comment-only files' },
    rescore: { type: 'string', description: 'per-file score before→after' },
    worktree_note: { type: 'string', description: 'branch/worktree path for review + merge' },
    skipped: { type: 'string', description: 'anything deferred + why (e.g. needs ADR / runtime load-test)' },
  },
}

// Flows to take to expert (Ingest is the reference run; A-I2/A-I6/A-I1 already landed in main).
const FLOWS = [
  { key: 'ingest-remainder', files: 'application/services/document_service/{ingest_stages_store,__init__}.py + infrastructure/parser/{docx,excel_openpyxl,google_sheets,kreuzberg_markdown}_parser.py + shared/late_chunking.py', focus: 'A-I4 bound late_chunking whole-doc memory (DEFAULT_EMBED_DOC_BATCH_SIZE slices + max-chunks guard); A-I5 emit typed Block from DocumentParserPort for the 4 structured parsers so ctx.blocks populates (unblocks AdapChunk L2). Redo VN→EN comments on ingest_core/google_link/tabular if reverted.' },
  { key: 'chunking-adapchunk', files: 'shared/chunking/* + infrastructure/{doc_profile,chunking_strategy,narrate,chunk_quality}/*', focus: 'B-1 wire-or-delete orphan LLM selector; B-2 block-pipeline no-op (needs ingest A-I5 blocks); B-3 atomic-protect default OFF; B-4 L3 entity not fed to selector.' },
  { key: 'answer-generate', files: 'orchestration/nodes/{generate,guard_output,critique_parser,persist}.py + system_prompts/* + guardrails/*', focus: 'Already sacred-10 clean (A-/9.2). C-1 price_buoi_le legacy dict-key (jsonb_conversation_state); C-2 stale math-lockdown docstrings. Mostly hygiene to 9.7.' },
  { key: 'chat-testchat', files: 'interfaces/http/routes/{chat,chat_async,chat_stream}.py + routes/test_chat/*', focus: 'D-B1 destructive test endpoints ungated RBAC; D-B2 harness never-external env-gate; D-A2 tenant strictness divergence; D-A1 streaming token_ledger (see cost-log).' },
  { key: 'retrieval', files: 'orchestration/nodes/{retrieve,rerank,rrf_round_robin,mmr_dedup,grade}.py + infrastructure/{retrieval,reranker,hyde,query_router,metadata_filter,vector}/*', focus: 'E-1 wire-or-delete entity-fairness rrf_round_robin (comparison coverage); E-2 bm25_flags=5 hardcode ×3; E-3 safety-net stamp vs CRAG floor.' },
  { key: 'multitenant-rls', files: 'infrastructure/db/{session,engine}.py + bot_registry_service.py + repositories/* + security/*', focus: 'F-1 IDOR-write fenced UPDATE in document/conversation save(); F-5 re-assert RLS CREATE POLICY in a git migration + pg_policies test; F-2 job_repo tenant fence; F-4 stats_index session_with_tenant.' },
  { key: 'costlog-crm', files: 'infrastructure/token_ledger/* + repositories/token_ledger_analytics_repository.py + routes/{admin_metrics,admin_analytics}.py + observability/*', focus: 'G-1 streaming emit; G-2 Port-boundary emit decorator (all providers); G-3 per-ws/tenant rollup + cross-tenant admin endpoint; G-4/G-6 request_id+purpose+duration; reconciliation test.' },
  { key: 'multilang', files: 'text_normalizer/* + tokenizer/* + i18n.py + narrate/llm_narrate.py + sysprompt_assembler.py', focus: 'I-1 llm_narrate VN-hardcoded block-prompts + thread bot language (language_packs, EN fallback); I-2 get_pack VN fallback→EN for unseeded locales.' },
  { key: 'cost-perf', files: 'orchestration/query_graph.py gating + nodes/* + infrastructure/{cache,proximity_cache,resilience}/* + shared/perf.py', focus: 'J-1 flip async grounding for factoid (measure p95); J-3 bound multi-query fan-out gather (semaphore); J-4 seed rerank_skip_intents; J-5 instrument cache hit/miss.' },
]

phase('Diagnose')
const results = await pipeline(
  FLOWS,
  (f) => agent(
    `You are a Principal engineer. READ-ONLY deep-debug of the **${f.key}** flow in /var/www/html/ragbot.\nFiles: ${f.files}\nKnown fix focus (from the 2026-06-23 audit, reports/EXPERT_DEEP_AUDIT_20260623.md): ${f.focus}\n\n${PROTOCOL}\n\nReturn the 5-axis scorecard for every file + a prioritized fix-list. Be a harsh expert reviewer; bar = expert, not "good". Self-verify each CRIT/HIGH at file:line before listing it.`,
    { label: `diagnose:${f.key}`, phase: 'Diagnose', schema: SCORECARD },
  ),
  (sc, f) => {
    if (!sc || !sc.fixes) return { flow: f.key, scorecard: sc, verified_fixes: [] }
    const high = sc.fixes.filter((x) => x.severity === 'CRIT' || x.severity === 'HIGH')
    return parallel(
      high.map((fx) => () =>
        agent(
          `Adversarially VERIFY this fix finding by re-reading the cited code. Default to refuted if the evidence does not hold or the severity is inflated.\nFLOW ${f.key} · ${fx.id} · ${fx.severity} · ${fx.file}\nEVIDENCE ${fx.evidence}\nCLAIM ${fx.expert_fix}`,
          { label: `verify:${f.key}:${fx.id}`, phase: 'Verify', schema: VERDICT },
        ).then((v) => ({ ...fx, verdict: v })),
      ),
    ).then((vf) => ({ flow: f.key, scorecard: sc, verified_fixes: vf.filter(Boolean) }))
  },
)

phase('Fix')
const fixed = await parallel(
  results.filter(Boolean).map((r) => () =>
    agent(
      `You are a Principal engineer fixing the **${r.flow}** flow to EXPERT in an ISOLATED git worktree (you have your own copy — safe to edit + run tests; NEVER run git stash/restore on the shared tree).\n\n${PROTOCOL}\n\nConfirmed fix-list (apply the confirmed ones, at the correct layer):\n${JSON.stringify((r.verified_fixes || []).filter((x) => x.verdict?.verdict !== 'refuted'), null, 1)}\n\nFull scorecard for context:\n${JSON.stringify(r.scorecard, null, 1)}\n\nRULES: TDD — failing test FIRST, then minimum code, then green. used→fix / genuinely-unused→comment-out + note in the flow report (NEVER delete now). Comment-only edits must stay AST-identical (prove it). Verify per touched file: pytest subset green (0 regression) + ruff HEAD==NOW (0 new). Defer anything that needs an ADR or a runtime load-test — say so in 'skipped'. set -a && source .env && set +a first.`,
      { label: `fix:${r.flow}`, phase: 'Fix', schema: FIX_RESULT, isolation: 'worktree' },
    )
  ),
)

return { diagnoses: results.filter(Boolean), fixes: fixed.filter(Boolean) }
