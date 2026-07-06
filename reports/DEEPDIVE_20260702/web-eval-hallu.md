# DEEPDIVE web-eval-hallu — 2025–2026 SOTA: RAG Evaluation + Hallucination Prevention

**Date**: 2026-07-02 · **Slug**: `web-eval-hallu` · **Mode**: READ-ONLY web research (rule#0: every claim carries a URL or `file:line`; FACT vs HYPOTHESIS labelled explicitly)

**Scope**: RAGAS evolution · faithfulness/groundedness metrics · LLM-as-judge reliability · hallucination detection (Lynx, HHEM, MiniCheck, grounding verification) · refusal calibration ("answer-when-you-know") · coverage metrics · golden-set + synthetic QA generation · CI eval gates — with recommendations for ragbot's **HALLU=0 sacred + Coverage≥0.95** targets.

---

## 0. Executive summary

1. The field has split evaluation into **three tiers**: (a) cheap deterministic/classifier checks that run on every request or every CI run (HHEM-2.1 ~0.6s/4K-tokens on consumer GPU; MiniCheck at "GPT-4 accuracy for 400× lower cost"), (b) claim-level LLM-decomposition metrics (RAGAS faithfulness, RAGChecker bidirectional entailment) for offline diagnosis, and (c) frontier-LLM judge ensembles (FACTS Grounding uses 3 judges) reserved for audits — because LLM-as-judge alone is now known-unreliable (kappa deflation 33–41pp; >50% of frontier models fail bias tests).
2. **Refusal is now a measured, two-sided skill**, not a binary: RefusalBench (EACL 2026) formalizes **False Refusal Rate (over-refusal) vs Missed Refusal Rate (under-refusal)** plus calibration (ECE), and shows even the best frontier model reaches only 73% single-doc refusal accuracy and 47.4% multi-doc. Ragbot's HALLU=0 gate covers only the under-refusal side; the V15-1 "refuse oan" problem is the unmeasured FRR side.
3. **Google's "Sufficient Context" (ICLR 2025)** is the single most directly applicable idea for Coverage≥0.95: label each eval item by whether retrieved context is sufficient, which cleanly splits coverage failures into *retrieval gap* vs *generation gap* — exactly the diagnosis CLAUDE.md's 2026-06-03 case study did by hand.
4. **OpenAI's "Why Language Models Hallucinate" (2509.04664)** gives the theoretical backing for ragbot's scoring scheme: binary-graded evals reward guessing; scoring must credit calibrated abstention — ragbot's trap-question design is aligned with SOTA, but should adopt explicit answered/abstained/wrong 3-way scoring.
5. Ragbot's current eval stack (verified in-repo): a real deterministic gate (`scripts/eval_gate.py`), a per-bot golden regression harness (`scripts/eval_per_bot_golden.py`), a DeepEval runner (`scripts/deepeval_runner.py`), but a **stubbed RAGAS adapter** (`ragas_metric_adapter.py` returns a fixed stub score) and **zero specialized grounding-verifier models** (grep `hhem|minicheck|lynx` in `src/`+`scripts/` = 0 hits). The biggest SOTA gap is the missing middle tier: an NLI/classifier grounding verifier between the regex trap-check and the expensive LLM judge.
6. **Vietnamese is a real constraint**: HHEM-2.1-Open supports English/French/German only; BabelJudge (June 2026) documents cross-lingual judge degradation. Any grounding-verifier adoption must be validated on Vietnamese first (label: measured risk, not blocker).

---

## 1. RAGAS evolution (2023 → 2026)

**FACT (sources below):**
- RAGAS began as a 4-metric framework (faithfulness, answer relevancy, context precision/recall) in the 2023 paper ([arXiv:2309.15217](https://arxiv.org/abs/2309.15217)). By 2025–2026 the stable docs list a much larger metric surface: Context Precision, Context Recall, Context Entities Recall, **Noise Sensitivity**, Response Relevancy, Faithfulness (incl. **Multimodal Faithfulness**), NVIDIA-style Answer Accuracy / Context Relevance / **Response Groundedness**, Topic Adherence, Tool-call Accuracy and agent metrics ([Ragas available metrics docs](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)).
- Faithfulness is computed by **claim decomposition**: the answer is split into statements, each verified against retrieved context ([Ragas docs](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/); [Atlan framework comparison 2026](https://atlan.com/know/llm-evaluation-frameworks-compared/)).
- Testset generation moved from "evolution heuristics" to a **KnowledgeGraph + transformations + query-synthesizer distribution** design: default mix `SingleHopSpecificQuerySynthesizer` 0.5 / `MultiHopAbstractQuerySynthesizer` 0.25 / `MultiHopSpecificQuerySynthesizer` 0.25, plus `generate_with_chunks` to reuse the app's own chunker ([Ragas testset generation docs](https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/); [v0.3.0 docs](https://docs.ragas.io/en/v0.3.0/getstarted/rag_testset_generation/); [Ragas repo](https://github.com/vibrantlabsai/ragas)).
- Current README positions Ragas around "objective metrics, intelligent test generation, and data-driven insights", adds a `DiscreteMetric` custom-criteria system, Apache-2.0 ([Ragas README](https://github.com/vibrantlabsai/ragas)).
- Known limitation (independent 2026 review): "A RAG system can score 0.95 faithfulness and produce wrong business answers if the retrieved content is stale or incorrect… no framework can distinguish a factually wrong context from a correct one" ([FutureAGI RAG evaluation 2026](https://futureagi.com/blog/what-is-rag-evaluation-2026/)). I.e. faithfulness ≠ correctness — matches ragbot's own "Faithfulness 1.0 + Coverage 0.5 = still FAIL UX" doctrine (CLAUDE.md Coverage section).

**Repo state (FACT, file:line):**
- `src/ragbot/application/services/ragas_metric_adapter.py:9-11,60,68` — the RAGAS adapter is an explicit **deterministic stub** ("stub today, real `ragas` provider tomorrow", `stub_score = DEFAULT_RAGAS_STUB_SCORE`); `scripts/eval_ragas_metrics.py:1-12` confirms the CLI calls the stub. The Port+Registry seam for the real package already exists — good T3 posture, missing T1 substance.

---

## 2. Faithfulness / groundedness metrics — claim-level is the 2025 norm

**FACT:**
- **RAGChecker** (Amazon Science, NeurIPS/2025-cited) decomposes both the model answer and the ground-truth answer into **atomic claims** via an LLM extractor, then runs **bidirectional entailment** — yielding overall + diagnostic-retriever + diagnostic-generator metrics, so unsupported claims are attributed to generation (hallucination) vs retrieval ([arXiv:2408.08067](https://arxiv.org/html/2408.08067v1); [GitHub amazon-science/RAGChecker](https://github.com/amazon-science/RAGChecker)). Domain-specific derivatives exist for biomedicine ([MedRAGChecker, arXiv:2601.06519](https://arxiv.org/html/2601.06519)) and law ([claim-level law RAG benchmark, arXiv:2605.21071](https://arxiv.org/pdf/2605.21071)).
- **FACTS Grounding** (Google DeepMind): 1,719 examples (860 public / 859 private), long-form documents up to 32K tokens; the factuality score uses **three different frontier-LLM judges** with a two-phase eligibility+grounding rubric; response must be *fully* grounded in the provided document ([DeepMind blog](https://deepmind.google/blog/facts-grounding-a-new-benchmark-for-evaluating-the-factuality-of-large-language-models/); [arXiv:2501.03200](https://arxiv.org/pdf/2501.03200); [Kaggle leaderboard](https://www.kaggle.com/benchmarks/google/facts-grounding); [public dataset on HF](https://huggingface.co/datasets/google/FACTS-grounding-public)). In Dec 2025 Google extended this into a full **FACTS Benchmark Suite** covering parametric factuality + grounding ([suite paper](https://storage.googleapis.com/deepmind-media/FACTS/FACTS_benchmark_suite_paper.pdf); [arXiv:2512.10791](https://arxiv.org/html/2512.10791v1)).
- **HalluLens** (Meta, [arXiv:2504.17550](https://arxiv.org/abs/2504.17550)) formalizes the taxonomy: **extrinsic** (inconsistent with training data / provided context) vs **intrinsic** hallucination, explicitly **disentangling hallucination from factuality**, and regenerates test sets dynamically to resist leakage. This maps directly onto ragbot's 4-type anti-HALLU taxonomy (fabricate / misinterpret / extrapolate / conflate) — ragbot's taxonomy is a finer split of HalluLens's extrinsic class (label: HYPOTHESIS/interpretation, mapping is mine).
- Practitioner consensus for 2025: prioritize faithfulness, context utilization, answer completeness, cost/latency; standardize offline test runs + node-level evals + CI gates ([Maxim complete guide 2025](https://www.getmaxim.ai/articles/complete-guide-to-rag-evaluation-metrics-methods-and-best-practices-for-2025/); [Confident AI RAG metrics](https://www.confident-ai.com/blog/rag-evaluation-metrics-answer-relevancy-faithfulness-and-more)).

**Implication for ragbot (HYPOTHESIS, grounded in above):** claim-level decomposition is what ragbot's load-test judging currently approximates by substring `expect` matching (`scripts/eval_gate.py:6-10`). Substring match under-credits paraphrase and over-credits accidental echo; RAGChecker-style claim extraction on the ~50–150-turn load-test sets is affordable offline and would make Coverage measurement paraphrase-robust.

---

## 3. Hallucination detection models (the missing middle tier)

**FACT:**
- **HHEM-2.1 (Vectara)** — pure classifier (not LLM-judge), outputs Factual Consistency Score 0–1; ≥1.5× better than GPT-3.5-Turbo and >30% relative better than GPT-4 on RAGTruth subsets; **4,096-token context in ~0.6 s on an RTX 3090** (vs "RAGAS may take 35 s with GPT-4"); runs on CPU under 1.5 s; **English/French/German only**; HHEM-2.1-Open on HuggingFace ([Vectara HHEM-2.1 blog](https://www.vectara.com/blog/hhem-2-1-a-better-hallucination-detection-model)).
- **Vectara Hallucination Leaderboard next-gen (late 2025)** — dataset refreshed from 1,000 to 7,700+ articles, documents up to **32K tokens**, spanning law/medicine/finance/tech/education; headline finding: **reasoning models got worse** — GPT-5, Claude Sonnet 4.5, Grok-4, Gemini-3-Pro all exceeded 10% hallucination rates on grounded summarization; best was gemini-2.5-flash-lite at 3.3% ([Vectara next-gen leaderboard blog](https://www.vectara.com/blog/introducing-the-next-generation-of-vectaras-hallucination-leaderboard); [GitHub leaderboard](https://github.com/vectara/hallucination-leaderboard); [CodingFleet 2026 summary](https://codingfleet.com/blog/ai-model-hallucination-rates-2026/); [Suprmind 2026 hub](https://suprmind.ai/hub/ai-hallucination-rates-and-benchmarks/)). Companion paper: **FaithJudge** — an LLM-judge framework anchored by human hallucination annotations, arguing static leaderboards obsolesce and must evolve ([arXiv:2505.04847](https://arxiv.org/pdf/2505.04847)).
- **Lynx (Patronus AI)** — open 8B/70B hallucination-judge models; Lynx-70B beats GPT-4o at hallucination detection (e.g., +8.3% accuracy on PubMedQA); ships with **HaluBench**, a real-world-domain faithfulness benchmark; both on HuggingFace ([Patronus announcement](https://www.patronus.ai/announcements/patronus-ai-launches-lynx-state-of-the-art-open-source-hallucination-detection-model); [arXiv:2407.08488](https://arxiv.org/html/2407.08488v1); [Patronus docs](https://docs.patronus.ai/docs/research_and_differentiators/Lynx/base)).
- **MiniCheck / Bespoke-MiniCheck-7B** — "GPT-4-level fact-checking at 400× lower cost"; MiniCheck-Flan-T5-Large (770M) reaches GPT-4 accuracy; Bespoke-MiniCheck-7B is SOTA on the **LLM-AggreFact** leaderboard (11 aggregated factual-consistency datasets, 32 models ranked); benchmark note: "fact-checking the examples can cost $100 with GPT-4 but $1 with small on-prem models" ([EMNLP 2024 paper](https://aclanthology.org/2024.emnlp-main.499/); [arXiv:2404.10774](https://arxiv.org/abs/2404.10774); [GitHub Liyan06/MiniCheck](https://github.com/Liyan06/MiniCheck); [Bespoke-MiniCheck-7B on HF](https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B); [LLM-AggreFact leaderboard blog](https://llm-aggrefact.github.io/blog); [dataset](https://huggingface.co/datasets/lytang/LLM-AggreFact)). Newer compact contender: **Paladin-mini**, claiming better-balanced real-world BACC 79.31% vs 77.86% ([arXiv:2506.20384](https://arxiv.org/html/2506.20384v1)).
- **HalluMix** ([arXiv:2505.00506](https://arxiv.org/html/2505.00506v1)) — task-agnostic multi-domain hallucination-detection benchmark; groups Lynx-8B, HHEM-2.1-Open, Bespoke-MiniCheck-7B as the specialized fine-tuned detector class; long-form + multi-document tracking is the hard part.
- **Uncertainty-based detection**: **semantic entropy** (Farquhar et al., Nature 2024) clusters sampled answers by bidirectional entailment and computes entropy over meaning-classes — no labels needed; **Semantic Entropy Probes** approximate it from a single forward pass, removing the 5–10× sampling overhead ([arXiv:2406.15927](https://arxiv.org/abs/2406.15927); follow-ups [Semantic Energy, arXiv:2508.14496](https://arxiv.org/html/2508.14496v2)). Requires logits/hidden-state access — for ragbot (API-based LLMs via LiteLLM) this class is mostly **not applicable**; entailment-classifier approaches are (label: FACT for the method, HYPOTHESIS for the ragbot applicability inference).
- Closed-source alternative: **Google Vertex AI grounding/hallucination check API** (2025) ([HalluMix survey mention](https://arxiv.org/html/2505.00506v1)).

**Repo state (FACT):** `grep -rn "hhem|HHEM|minicheck|MiniCheck|lynx|Lynx" src/ scripts/` = **0 hits** (run 2026-07-02). Ragbot has **no grounding-verifier tier** — HALLU is enforced only by (a) trap-question refusal regex in `scripts/eval_gate.py:44-55` and (b) sysprompt rules (owner-owned, per CLAUDE.md sacred #10).

---

## 4. LLM-as-judge reliability — trust but verify the judge

**FACT:**
- Comprehensive surveys: [A Survey on LLM-as-a-Judge (arXiv:2411.15594)](https://arxiv.org/html/2411.15594v6) and [The Innovation 2025 survey](https://www.cell.com/the-innovation/fulltext/S2666-6758(25)00456-4) catalog **position bias, verbosity bias, self-enhancement bias**; standard mitigations = swap order + average, shuffle candidates (Auto-J, JudgeLM patterns).
- **Frontier models fail >50% of bias tests** in an industry audit ([Adaline LLM-as-judge reliability](https://www.adaline.ai/blog/llm-as-a-judge-reliability-bias)).
- **Kappa deflation**: raw human-agreement overstates chance-corrected discrimination by **33–41pp across all 21 evaluated judge models** — headline agreement numbers on JudgeBench-style suites are inflated ([Reliability without Validity, arXiv:2606.19544](https://arxiv.org/html/2606.19544); [JudgeBench overview](https://www.emergentmind.com/topics/judgebench-dataset); [Judge's Verdict, arXiv:2510.09738](https://arxiv.org/html/2510.09738)).
- **BabelJudge (June 2026)** measures judge reliability **across languages** — position bias, verbosity bias, order inconsistency, and **cross-lingual degradation** without human preference labels ([arXiv:2606.22329](https://arxiv.org/pdf/2606.22329)). Directly relevant: ragbot corpora/answers are Vietnamese; an English-centric judge is a measured risk, not an assumption.
- Robust-judging patterns that survived 2025 scrutiny: (a) **ensemble of ≥3 heterogeneous judges** (FACTS Grounding, above); (b) **judge anchored to human annotations** (FaithJudge); (c) **dual verification** — automated check + LLM secondary check, no free-floating single judge ([JudgeBench construction](https://www.emergentmind.com/topics/judgebench-dataset)); (d) psychometric calibration / IRT framing ([survey v6](https://arxiv.org/html/2411.15594v6)).

**Repo alignment (FACT):** ragbot's harness already embodies the strongest form of judge-skepticism — `scripts/eval_gate.py:4-5`: "scores DETERMINISTICALLY (no LLM judge — the user's 'no ChatGPT scoring' rule)". The `rag-loadtest` skill likewise agent-scores without an LLM judge. SOTA does **not** say "never use LLM judges"; it says *deterministic first, judge audited second*. The DeepEval runner (`scripts/deepeval_runner.py:27-29`, judge model via `DEEPEVAL_JUDGE_MODEL` env) exists but there is no meta-eval validating that judge against human/deterministic labels — if it's ever used for gating, kappa-style validation is mandatory first (HYPOTHESIS/recommendation).

---

## 5. Refusal calibration — "answer when you know" is now benchmarked

**FACT:**
- **RefusalBench** ([arXiv:2510.10390](https://arxiv.org/html/2510.10390); [EACL 2026](https://aclanthology.org/2026.eacl-long.321.pdf)) — *generative* (not static) evaluation of selective refusal in grounded LMs: 6 perturbation classes (**ambiguity, contradiction, missing info, false premise, granularity mismatch, epistemic mismatch**) × 3 intensities = 176 strategies. Metrics: Refusal Detection F1, **False Refusal Rate (FRR)** = over-refusal on answerable, **Missed Refusal Rate (MRR)** = under-refusal on unanswerable, category accuracy, **ECE**, combined Calibrated Refusal Score. Findings: best single-doc refusal accuracy 73% (Claude-4-Sonnet); **multi-doc collapses to 47.4%** (DeepSeek-R1 on RefusalBench-GaRAGe); >73% of predictions at max confidence despite 40–69% accuracy; refusal skill scales independently of model size; extended reasoning adds <1pp; **DPO-style targeted alignment beats scale**.
- **UAEval4RAG** (Salesforce) — taxonomy + pipeline to **synthesize unanswerable requests for any knowledge base** and auto-evaluate whether the RAG system rejects them ([ACL 2025](https://aclanthology.org/2025.acl-long.415/); [arXiv:2412.12300](https://arxiv.org/pdf/2412.12300); [MarkTechPost summary](https://www.marktechpost.com/2025/05/19/salesforce-ai-researchers-introduce-uaeval4rag-a-new-benchmark-to-evaluate-rag-systems-ability-to-reject-unanswerable-queries/)).
- **GaRAGe** — large-scale benchmark for grounding long-form answers in noisy multi-document contexts, with a deflection (refusal) subset; established that frontier models struggle at general refusal ([RefusalBench discussion](https://arxiv.org/html/2510.10390)). **AbstentionBench** shows mainstream LLMs fail to abstain appropriately across diverse settings ([Confidence-Based Abstention overview](https://www.emergentmind.com/topics/confidence-based-abstention)); survey: [Know Your Limits — abstention in LLMs](https://www.researchgate.net/publication/393331033_Know_Your_Limits_A_Survey_of_Abstention_in_Large_Language_Models).
- **Sufficient Context (Google, ICLR 2025)** ([arXiv:2411.06037](https://arxiv.org/abs/2411.06037); [Google Research blog](https://research.google/blog/deeper-insights-into-retrieval-augmented-generation-the-role-of-sufficient-context/); [GitHub](https://github.com/hljoren/sufficientcontext)): defines an **autorater for "does retrieved context suffice to answer"**; key findings — big models (Gemini 1.5 Pro, GPT-4o, Claude 3.5) answer well when context is sufficient but **hallucinate instead of abstaining when it isn't**; small models abstain/hallucinate even with sufficient context; a **selective-generation framework** combining the sufficient-context signal with self-rated confidence lifts selective accuracy **2–10pp at equal coverage**.
- **OpenAI, "Why Language Models Hallucinate"** (Kalai, Nachum, Vempala, Zhang, Sept 2025; [arXiv:2509.04664](https://arxiv.org/abs/2509.04664); [OpenAI page](https://openai.com/index/why-language-models-hallucinate/)): hallucination persists because **binary-graded evals reward guessing and penalize humility**; the fix is socio-technical — rework primary metrics to credit calibrated uncertainty, not add one more hallucination eval.
- Production recipes: **HALT-RAG** (calibrated NLI ensembles + abstention gating, [arXiv:2509.07475](https://arxiv.org/pdf/2509.07475)); refusal-calibration datasets built from real conversations pairing queries with topically-relevant-but-insufficient context, target output = explicit IDK ([FinRAG-12B, arXiv:2605.05482](https://arxiv.org/pdf/2605.05482)); self-aware trust-or-abstain gating ([arXiv:2605.18792](https://arxiv.org/pdf/2605.18792)); abstaining on the lowest-confidence ~10% buys large precision gains for small F1 loss ([Confidence-Based Abstention](https://www.emergentmind.com/topics/confidence-based-abstention)).

**Repo alignment (FACT):** ragbot measures the under-refusal side (traps must refuse: `scripts/eval_gate.py:7-9`) and coverage (`--coverage-floor 0.80`, `eval_gate.py:130,145`), and its history documents the over-refusal failure mode (V15-1 "rule 1 quá nghiêm, ~10 turn refuse oan" — memory `project_v15_stream_z_done`; CLAUDE.md's "REFUSE SAI ≠ honest" anti-pattern). What's missing vs SOTA: (a) **FRR/MRR as separate first-class gate numbers**, (b) trap sets are static hand-written files → RefusalBench/UAEval4RAG show generative perturbation of the bot's own corpus is the 2025-standard way to keep traps unsaturated, (c) no sufficient-context labeling to attribute refusals.

---

## 6. Coverage / completeness metrics

**FACT:**
- The industry stack measures coverage via: **Context Recall** (did retrieval bring everything needed — [Ragas docs](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)), **answer completeness** (does the answer address all aspects — [Maxim 2025 guide](https://www.getmaxim.ai/articles/complete-guide-to-rag-evaluation-metrics-methods-and-best-practices-for-2025/); [Cobbai precision/recall/faithfulness](https://cobbai.com/blog/evaluate-rag-answers)), **claim-recall vs ground truth** (RAGChecker generator-recall, [arXiv:2408.08067](https://arxiv.org/html/2408.08067v1)), and **retrieval Hit@K/Recall@K/MRR** upstream ([LangCopilot RAG metrics 101](https://langcopilot.com/posts/2025-09-17-rag-evaluation-101-from-recall-k-to-answer-faithfulness)).
- The **sufficient-context autorater** (ICLR 2025, §5 above) is the cleanest published operationalization of ragbot's Coverage: for each question with a corpus answer, label sufficiency of retrieved context → *insufficient* = retrieval gap; *sufficient but wrong/refused* = generation gap.

**Repo alignment (FACT):** Coverage is already a named sacred metric in CLAUDE.md (`Coverage = answer_correct_when_corpus_has_answer / total_corpus_has_answer`, blocker <0.95) and implemented as substring-match in `scripts/eval_gate.py:6-10`; retrieval-side hit-rate exists (`scripts/eval_retrieval_hit_at_k.py`, 583 lines). Missing: the sufficiency label joining the two, so a coverage failure today doesn't say *which layer* to fix without a manual `debug-trace` session — precisely the cost the 2026-06-03 "3 alembic sai tầng" lesson paid (CLAUDE.md Lessons Learned).

---

## 7. Golden sets + synthetic QA generation

**FACT:**
- **Ragas TestsetGenerator**: knowledge-graph from corpus + transformations + persona/synthesizer distributions; supports pre-chunked docs (`generate_with_chunks`) so the eval set follows the app's real chunking ([docs](https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/); example golden dataset: [dwb2023/ragas-golden-dataset](https://huggingface.co/datasets/dwb2023/ragas-golden-dataset); practitioner walkthroughs: [TheDataGuy pt.4](https://thedataguy.pro/writing/2025/04/generating-test-data-with-ragas/), [jakobs.dev technical-domain synthetic QA](https://jakobs.dev/evaluating-rag-synthetic-dataset-generation/)).
- **DataMorgana** (SIGIR 2025 LiveRAG Challenge): config-driven synthetic benchmark generation with **question-type and user-persona configuration** for diversity/realism; the challenge released **LiveRAG**, 895 synthetic Q&A with difficulty levels ([LiveRAG challenge report, arXiv:2507.04942](https://arxiv.org/pdf/2507.04942); [LiveRAG dataset paper, arXiv:2511.14531](https://arxiv.org/pdf/2511.14531)).
- **Know Your RAG** (dataset taxonomy + generation strategies for RAG eval, [arXiv:2411.19710](https://arxiv.org/pdf/2411.19710)); multi-agent private synthetic generation for RAG eval ([arXiv:2508.18929](https://arxiv.org/pdf/2508.18929)); difficulty targeting ≈30% medium / 20% hard ([FutureAGI synthetic datasets guide](https://futureagi.com/blog/synthetic-datasets-rag-2025/)).
- Unanswerable-set synthesis: UAEval4RAG + RefusalBench perturbations (§5) — the golden set should contain **generated traps derived from the tenant's own corpus**, not only hand-written ones.

**Repo alignment (FACT):** golden assets exist (`golden_set/golden_questions_v2.json`, `kich_ban_questions_v1.json` — `ls golden_set/` 2026-07-02) and per-bot golden files keyed by `record_bot_id` with baseline-regression gating (`scripts/eval_per_bot_golden.py:1-18`). All are hand-written; no corpus-derived synthetic generation pipeline exists (no `TestsetGenerator`/DataMorgana-style tooling found in `scripts/`). For a multi-tenant platform this is the scaling bottleneck: every new tenant/bot/format needs its own golden set, and CLAUDE.md already mandates "Golden test questions → file riêng per bot". Synthetic generation per-bot from the bot's own ingested chunks is the SOTA answer (HYPOTHESIS/recommendation).

---

## 8. CI eval gates

**FACT:**
- **DeepEval** is the reference open-source pattern: pytest-native `assert_test()` + `deepeval test run` in GitHub Actions; failing metric thresholds break the build; supports parallelism, caching, component-level (span) metrics, multi-turn simulation ([DeepEval CI/CD docs](https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd); [DeepEval RAG quickstart](https://deepeval.com/docs/getting-started-rag); [GitHub](https://github.com/confident-ai/deepeval); tool-comparison context: [Confident AI 2026 comparison](https://www.confident-ai.com/knowledge-base/compare/best-llm-evaluation-tools), [Inference.net comparison](https://inference.net/content/llm-evaluation-tools-comparison/)).
- 2025 best practice = layered gates: offline golden-set runs on PR, node-level evals, automated log evals in prod, CI/CD thresholds per metric ([Maxim 2025](https://www.getmaxim.ai/articles/complete-guide-to-rag-evaluation-metrics-methods-and-best-practices-for-2025/); [FutureAGI 2026 gates](https://futureagi.com/blog/what-is-rag-evaluation-2026/)).
- Evolving-leaderboard doctrine (FaithJudge, HalluLens, RefusalBench): static eval sets saturate/leak → regenerate dynamically ([arXiv:2505.04847](https://arxiv.org/pdf/2505.04847); [arXiv:2504.17550](https://arxiv.org/abs/2504.17550); [arXiv:2510.10390](https://arxiv.org/html/2510.10390)).

**Repo alignment (FACT):** ragbot has the gate *shape* (exit-code contracts in `eval_gate.py:130-147`, `eval_ragas_metrics.py:15-19`, baseline regression in `eval_per_bot_golden.py`) but they are operator-run scripts; nothing indicates a CI workflow invokes them on PR (no `.github/workflows` reference found to eval_gate — HYPOTHESIS: unverified, needs `ls .github/workflows`).

---

## 9. Recommendations for ragbot (HALLU=0 sacred + Coverage≥0.95)

All are HYPOTHESIS (design recommendations) grounded in the FACTs above. Ordered by T1→T2→T3. None touch the answer path (sacred #10 — evaluation is offline/sidecar; no app-side answer override).

### R1 [T1] Add the missing middle tier: NLI/classifier grounding verifier in the eval harness
Wire **Bespoke-MiniCheck-7B or HHEM-2.1-Open** (both open, on-prem, ~$1 per full benchmark vs $100 GPT-4 — [LLM-AggreFact blog](https://llm-aggrefact.github.io/blog)) as an *offline eval verifier* behind the existing Port+Registry seam (`ragas_metric_adapter.py` already defines the Strategy contract at `:43`). Every **answered** load-test turn gets claim-vs-retrieved-context verification, catching hallucinations on *non-trap* questions that today's gate cannot see (it only checks traps + substring). **Vietnamese caveat is blocking-first**: HHEM-2.1 = en/fr/de only ([Vectara blog](https://www.vectara.com/blog/hhem-2-1-a-better-hallucination-detection-model)); MiniCheck is English-trained ([arXiv:2404.10774](https://arxiv.org/abs/2404.10774)). Step 1 must be a 50-example Vietnamese validation against hand labels; if BACC is poor, fall back to an LLM-judge entailment prompt validated per R4.

### R2 [T1] Split Coverage failures with a sufficient-context autorater
Implement the [Sufficient Context](https://arxiv.org/abs/2411.06037) autorater in the load-test analyzer: for each FAIL, label retrieved-context sufficiency → report `coverage_fail_retrieval` vs `coverage_fail_generation` separately. This automates the layer-attribution that CLAUDE.md's 5-step bug protocol currently does manually and prevents repeat of the "3 alembic sai tầng" class of waste. It also directly measures the CLAUDE.md anti-pattern "corpus CÓ đáp án nhưng retrieval miss".

### R3 [T1] Make refusal calibration two-sided: report FRR and MRR every load test
Adopt RefusalBench metric names: **MRR (trap answered = HALLU breach; must stay 0)** and **FRR (answerable question refused; the V15-1 'refuse oan' number)** ([arXiv:2510.10390](https://arxiv.org/html/2510.10390)). Coverage already approximates 1−FRR-and-wrongness combined; separating "refused" from "answered wrong" in `eval_gate.py`'s FAIL bucket makes the sysprompt-tuning trade-off (stricter rules ↔ higher FRR) visible per run. Note RefusalBench's multi-doc finding (47.4% best) — ragbot's multi-doc corpora are the hard case, so per-bot FRR matters.

### R4 [T1→T2] If/when an LLM judge gates anything, meta-evaluate it first
Before `deepeval_runner.py` scores are used for any pass/fail decision: validate the judge on ~100 human/deterministically-labelled Vietnamese examples, report **Cohen's kappa not raw agreement** (kappa deflation 33–41pp — [arXiv:2606.19544](https://arxiv.org/html/2606.19544)), swap-order to cancel position bias ([survey](https://arxiv.org/html/2411.15594v6)), and prefer a ≥2-judge ensemble for HALLU adjudication (FACTS Grounding uses 3 — [arXiv:2501.03200](https://arxiv.org/pdf/2501.03200)). Watch cross-lingual degradation (BabelJudge — [arXiv:2606.22329](https://arxiv.org/pdf/2606.22329)).

### R5 [T1] Generate per-bot synthetic golden sets + traps from each bot's own corpus
Replace hand-written-only golden files with a corpus-derived generator: Ragas KnowledgeGraph testset generation with `generate_with_chunks` over the bot's real chunks ([docs](https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/)) + UAEval4RAG/RefusalBench-style **unanswerable perturbations** (false premise, granularity mismatch, contradiction — [ACL 2025](https://aclanthology.org/2025.acl-long.415/), [arXiv:2510.10390](https://arxiv.org/html/2510.10390)) for traps. This is the only approach that scales to multi-tenant onboarding (every new bot gets a golden set + trap set automatically) and keeps traps unsaturated (HalluLens dynamic-generation doctrine — [arXiv:2504.17550](https://arxiv.org/abs/2504.17550)). Keep them per-bot files keyed by `record_bot_id` (existing convention, `eval_per_bot_golden.py:9-13`), domain-neutral engine.

### R6 [T1] Score 3-way, not binary — credit calibrated abstention
Per [arXiv:2509.04664](https://arxiv.org/abs/2509.04664): grade every turn as CORRECT / ABSTAINED / WRONG. Wrong-on-answerable costs more than abstained-on-answerable; abstained-on-trap is the only trap pass. `eval_gate.py`'s `_is_refusal` regex (`:44-55`) is the fragile point — the docstring itself admits "markers always lag"; the sturdier 2025 pattern is asking the *bot's own structured refusal channel* (ragbot already has `oos_answer_template` per-bot) or a verified classifier rather than regexing free text.

### R7 [T2] Wire the existing gates into CI
DeepEval-style: run `eval_gate.py` (fast subset) on PR, full per-bot golden regression (`eval_per_bot_golden.py` baseline compare) nightly; failing HALLU/Coverage thresholds break the merge ([DeepEval CI/CD docs](https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd)). Thresholds from `shared/constants.py`/`system_config` (zero-hardcode rule already followed by the scripts).

### R8 [T2] Replace the RAGAS stub with the real package — but only for the two metrics that pay
Real `ragas` Faithfulness + Context Recall via the existing registry seam (`ragas_metric_adapter.py:43-60`). Skip the long tail of RAGAS metrics; faithfulness≥0.9 and coverage attribution are the declared targets. Independent reviews warn faithfulness alone cannot detect stale/wrong corpus content ([FutureAGI 2026](https://futureagi.com/blog/what-is-rag-evaluation-2026/)) — which is why R1/R2 come first.

### Explicit non-recommendations
- **No runtime answer-override guardrail** (e.g., HHEM inline blocking answers): violates sacred #10 (app never overrides LLM answers). All verifiers above are eval-side. If runtime grounding-check is ever wanted, it must be a per-bot opt-in surfaced to the owner, via ADR.
- **No semantic-entropy machinery**: needs token-level access ragbot's API-based LLM routing doesn't have ([arXiv:2406.15927](https://arxiv.org/abs/2406.15927)); entailment classifiers deliver the same goal cheaper here.
- **Don't chase leaderboard models for generation**: the 2025 Vectara refresh shows "most capable" reasoning models hallucinate *more* on grounded tasks ([Vectara next-gen blog](https://www.vectara.com/blog/introducing-the-next-generation-of-vectaras-hallucination-leaderboard)) — model choice for HALLU=0 must be validated on ragbot's own harness, not marketing tiers.

---

## 10. Source index (primary)

| Topic | Source |
|---|---|
| RAGAS paper | https://arxiv.org/abs/2309.15217 |
| RAGAS metrics docs | https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ |
| RAGAS testset generation | https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/ |
| RAGAS repo | https://github.com/vibrantlabsai/ragas |
| RAGChecker | https://arxiv.org/html/2408.08067v1 · https://github.com/amazon-science/RAGChecker |
| FACTS Grounding | https://arxiv.org/pdf/2501.03200 · https://deepmind.google/blog/facts-grounding-a-new-benchmark-for-evaluating-the-factuality-of-large-language-models/ · https://www.kaggle.com/benchmarks/google/facts-grounding |
| FACTS Suite (Dec 2025) | https://arxiv.org/html/2512.10791v1 |
| HalluLens | https://arxiv.org/abs/2504.17550 |
| HHEM-2.1 | https://www.vectara.com/blog/hhem-2-1-a-better-hallucination-detection-model |
| Vectara leaderboard next-gen | https://www.vectara.com/blog/introducing-the-next-generation-of-vectaras-hallucination-leaderboard · https://github.com/vectara/hallucination-leaderboard |
| FaithJudge / evolving leaderboards | https://arxiv.org/pdf/2505.04847 |
| Lynx + HaluBench | https://arxiv.org/html/2407.08488v1 · https://www.patronus.ai/announcements/patronus-ai-launches-lynx-state-of-the-art-open-source-hallucination-detection-model |
| MiniCheck / Bespoke | https://arxiv.org/abs/2404.10774 · https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B · https://llm-aggrefact.github.io/blog |
| Paladin-mini | https://arxiv.org/html/2506.20384v1 |
| HalluMix | https://arxiv.org/html/2505.00506v1 |
| Semantic entropy probes | https://arxiv.org/abs/2406.15927 |
| LLM-as-judge survey | https://arxiv.org/html/2411.15594v6 · https://www.cell.com/the-innovation/fulltext/S2666-6758(25)00456-4 |
| Kappa deflation | https://arxiv.org/html/2606.19544 |
| BabelJudge | https://arxiv.org/pdf/2606.22329 |
| Judge bias audit | https://www.adaline.ai/blog/llm-as-a-judge-reliability-bias |
| RefusalBench | https://arxiv.org/html/2510.10390 · https://aclanthology.org/2026.eacl-long.321.pdf |
| UAEval4RAG | https://aclanthology.org/2025.acl-long.415/ · https://arxiv.org/pdf/2412.12300 |
| Sufficient Context | https://arxiv.org/abs/2411.06037 · https://research.google/blog/deeper-insights-into-retrieval-augmented-generation-the-role-of-sufficient-context/ |
| Why LMs Hallucinate (OpenAI) | https://arxiv.org/abs/2509.04664 · https://openai.com/index/why-language-models-hallucinate/ |
| HALT-RAG | https://arxiv.org/pdf/2509.07475 |
| Abstention survey | https://www.researchgate.net/publication/393331033_Know_Your_Limits_A_Survey_of_Abstention_in_Large_Language_Models |
| DataMorgana / LiveRAG | https://arxiv.org/pdf/2507.04942 · https://arxiv.org/pdf/2511.14531 |
| Know Your RAG | https://arxiv.org/pdf/2411.19710 |
| DeepEval CI/CD | https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd · https://github.com/confident-ai/deepeval |
| RAG eval guides 2025/26 | https://www.getmaxim.ai/articles/complete-guide-to-rag-evaluation-metrics-methods-and-best-practices-for-2025/ · https://futureagi.com/blog/what-is-rag-evaluation-2026/ · https://www.confident-ai.com/blog/rag-evaluation-metrics-answer-relevancy-faithfulness-and-more |

**Repo evidence index**: `scripts/eval_gate.py:1-19,44-55,105-147` · `scripts/eval_ragas_metrics.py:1-19` · `src/ragbot/application/services/ragas_metric_adapter.py:9-11,43,60,68` · `scripts/eval_per_bot_golden.py:1-18` · `scripts/deepeval_runner.py:1-29` · `scripts/eval_retrieval_hit_at_k.py` (exists, 583 lines) · `golden_set/` listing · grep `hhem|minicheck|lynx` in `src/`+`scripts/` = 0 hits (all runs 2026-07-02).
