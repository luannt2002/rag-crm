# Deep-dive: `_external_refs/PDF2Audio` + `_external_refs/llama-cookbook` — PDF-processing patterns & RAG recipe lessons

- **Slug**: refs-pdf2audio-cookbook
- **Date**: 2026-07-02
- **Scope**: read-only study of two external references, mapped against ragbot's CLAUDE.md mandates (multi-format ingest first-class, domain-neutral, zero-hardcode, Strategy+DI, HALLU=0, no app-inject/override).
- **Method**: full read of PDF2Audio `app.py` (978 lines) + README; llama-cookbook local sparse checkout fully read, plus the actual RAG recipes fetched read-only from the repo's git object store (`git show HEAD:<path>` — blob:none partial clone fetches blobs on demand; working tree untouched).

## Evidence & labeling convention

Every claim is labeled **FACT** (verified against `file:line` or git-blob content) or **HYPOTHESIS** (inference, not runtime-verified). Line numbers for notebooks refer to cell numbers (`CELL n`) inside the `.ipynb` JSON, since notebooks have no stable line numbers.

---

# PART 0 — What is actually in these checkouts (inventory facts)

**FACT**: `PDF2Audio` = single-file Gradio app.
- `_external_refs/PDF2Audio/app.py` — 978 lines (verified `wc -l`), plus `PDF2Audio.ipynb` (same app as Colab), `README.md` (121 lines), `requirements.txt` (6 lines).
- Origin: `https://github.com/lamm-mit/PDF2Audio.git` (verified `git remote -v`), MIT-family LICENSE, by the lamm-mit (Buehler/MIT) group — same authors as SciAgents (README.md:104-112 bibtex).

**FACT**: `llama-cookbook` local copy is a **sparse, blob-filtered checkout**, NOT the full repo.
- `git config`: `origin ... [blob:none]`; `.git/info/sparse-checkout` limits the working tree to `/end-to-end-use-cases/` (root files), `/getting-started/` (root files), `/end-to-end-use-cases/NotebookLlama/` (verified by reading `.git/info/sparse-checkout`).
- Working-tree notebook/md inventory (verified `find`): NotebookLlama Steps 1–4 + README + TTS_Notes.md, `end-to-end-use-cases/video_summary.ipynb`, `end-to-end-use-cases/README.md`, `getting-started/build_with_llama_4.ipynb`, `getting-started/build_with_llama_api.ipynb`, `getting-started/README.md`, root README/UPDATES/CONTRIBUTING/CODE_OF_CONDUCT.
- **FACT**: the RAG recipes referenced by `getting-started/README.md:23` ("The [RAG](./RAG/) folder contains a simple Retrieval-Augmented Generation application") and `end-to-end-use-cases/README.md:104-105` (RAG chatbot) are **not in the working tree**; they exist in the git tree (`git ls-tree -r HEAD`) and were fetched via `git show` for this report:
  - `getting-started/RAG/hello_llama_cloud.ipynb`
  - `end-to-end-use-cases/Contextual-Chunking-RAG/{README.md,Tutorial.ipynb,helper.py,embedding.py,config.py}`
  - `end-to-end-use-cases/RAFT-Chatbot/{README.md,raft_utils.py,raft_eval.py}`
  - `end-to-end-use-cases/Multi-Modal-RAG/README.md`
  - `end-to-end-use-cases/customerservice_chatbots/RAG_chatbot/RAG_Chatbot_Example.ipynb`
  - `3p-integrations/togetherai/{llama_contextual_RAG.ipynb,text_RAG_using_llama_on_together.ipynb}`
  - `3p-integrations/langchain/langgraph_rag_agent.ipynb`
  - `3p-integrations/llamaindex/dlai_agentic_rag/README.md`

---

# PART 1 — PDF2Audio: PDF-processing patterns (app.py, 978 lines)

## 1.1 Pipeline shape

**FACT**: architecture is a linear 4-stage pipeline: (1) multi-file text extraction → (2) one big LLM call producing a structured `Dialogue` → (3) per-line parallel TTS → (4) MP3 concat + temp-file serve (app.py:572-716).

## 1.2 Extraction: extension-switch, no byte-sniff (anti-pattern vs ragbot)

**FACT** (app.py:609-624): file-type detection is `file_path.suffix.lower()` only:
```python
if suffix == ".pdf":
    reader = PdfReader(f)
    text = "\n\n".join(page.extract_text() for page in reader.pages if page.extract_text())
elif suffix in [".txt", ".md", ".mmd"]:
    text = f.read()   # utf-8
```
- pypdf `page.extract_text()` → flat text; pages with no extractable text are silently skipped (`if page.extract_text()` filter). No OCR path, no table/heading preservation, no structured markdown.
- **Contrast with ragbot mandate** (CLAUDE.md "Robust type-detection": `mime → file-ext → byte-sniff`): PDF2Audio would mis-route an extensionless URL download or `application/octet-stream` PDF. Ragbot's layered sniff is strictly stronger; nothing to adopt here, only confirmation that extension-only detection is the naive baseline ragbot already surpasses.
- **FACT**: multiple files are concatenated with `"\n\n"` into one `combined_text` (app.py:597-624) — multi-doc = trivial concat, no per-doc identity, no dedup. Fine for a podcast; exactly what a multi-tenant RAG platform must NOT do.

## 1.3 The genuinely reusable patterns

1. **Pydantic-schema-constrained generation + retry-until-valid** — **FACT** (app.py:509-515, 626-627):
   ```python
   class DialogueItem(BaseModel):
       text: str
       speaker: Literal["speaker-1", "speaker-2"]
   class Dialogue(BaseModel):
       scratchpad: str
       dialogue: List[DialogueItem]
   @retry(retry=retry_if_exception_type(ValidationError))
   @conditional_llm(model=text_model, api_base=api_base, api_key=openai_api_key)
   def generate_dialogue(...) -> Dialogue:
   ```
   tenacity retries **only** on `ValidationError` — i.e., regenerate until the LLM output parses into the schema, don't retry transport errors here. Narrow-exception retry gating is CLAUDE.md-compatible (broad-except policy). Relevance to ragbot: same shape as structured-output nodes (intent enum, CRAG grade) — retry-on-schema-violation is a cheap robustness win over one-shot parse-or-fail. **HYPOTHESIS**: ragbot decomposer/grader nodes would benefit; not measured here.

2. **Docstring-as-prompt-template with tagged sections** — **FACT** (app.py:628-652): the promptic `@llm` decorator turns the function docstring into the prompt; input text and stages are wrapped in XML-ish tags `<input_text>`, `<scratchpad>`, `<podcast_dialogue>`, `<edited_transcript>`, `<requested_improvements>` (app.py:636-651, 655-659). Tag-delimited untrusted input inside an instruction prompt = same pattern as ragbot's XML wrap (config-gated, memory: V4 GA-hardening).

3. **Visible chain-of-thought as a schema field** — **FACT** (app.py:514): `scratchpad: str` is part of the structured output — the model brainstorms *inside the same call* and the app discards it from the final product but keeps it inspectable. One call does plan+produce, versus ragbot's multi-node plan/act split. Cost-efficient single-call CoT is a **T2** pattern worth remembering for low-stakes generation nodes.

4. **Instruction templates as data, per language** — **FACT** (app.py:33-427): `INSTRUCTION_TEMPLATES` dict has 9 templates (podcast / SciAgents summary / lecture / summary / short summary / podcast French / German / Spanish / Portuguese / Hindi / Chinese). Language variants are **complete separate prompt packs, not translated wrappers** — the whole 5-part prompt (intro/text_instructions/scratch_pad/prelude/dialog) is re-authored per locale. This mirrors ragbot's `language_packs` design and the multilingual-no-vocab rule (language as DATA per locale). Also every template is user-editable in the UI before the run (app.py:853-883) — "bot owner owns the prompt", the same philosophy as sacred rule #10's single-source-of-truth `system_prompt`.

5. **Human-in-the-loop regeneration** — **FACT** (app.py:654-659, 890-896): edited transcript + free-text feedback are appended as `<edited_transcript>` + `<requested_improvements>` blocks to the *same* generation prompt, letting the model revise its previous output. Iterative-refine loop with the previous artifact as context; relevant to future ragbot ingest-enrichment QC tooling, not to answer-path (would violate rule #10 there).

6. **Ordered fan-out/fan-in concurrency for TTS** — **FACT** (app.py:682-695): submit one `get_mp3` per dialogue line to a `ThreadPoolExecutor`, keep `(future, transcript_line)` tuples in submission order, then join in order — parallel execution, deterministic ordered assembly. Same principle as ragbot Async Rule 1/4 (gather independent awaits, ordered reduce), thread-pool flavor.

7. **Streaming TTS bytes + temp-file lifecycle** — **FACT** (app.py:539-548, 699-714): streamed response chunks into `BytesIO`; output written to `NamedTemporaryFile(delete=False)` because "Gradio's audio component doesn't work with raw bytes in Safari" (app.py:702); ad-hoc GC deletes `.mp3` older than 24h on every run (app.py:711-714). Poor-man's TTL cleanup — ragbot equivalents should use real lifecycle management, but the "artifact + TTL sweep at write time" trick is a fine zero-infra default.

## 1.4 Defects & anti-patterns catalog (what NOT to copy)

- **FACT — dead function with latent NameError**: `edit_and_regenerate` (app.py:729-734) returns `validate_and_generate_audio(*new_args)` but the `new_args = list(args)` assignment is commented out (app.py:731) → `NameError` if ever called. It is currently unused (the regenerate button uses a lambda, app.py:939-944), i.e. dead code hiding a crash.
- **FACT — broad except swallowing everything**: `except Exception as e: return None, None, None, str(e)` (app.py:725-727) — converts any failure (including bugs) into a UI string. Violates ragbot broad-except policy (would need `exc_info=True` + narrow types).
- **FACT — hardcoded model/voice lists**: `STANDARD_TEXT_MODELS` (app.py:458-478), `STANDARD_AUDIO_MODELS` (488-492), `STANDARD_VOICES` (494-507, with duplicate `"nova"` at 499 and 505). Zero-hardcode violation by ragbot standards; ragbot's `ai_models`/`bot_model_bindings` DB registry is the correct inversion.
- **FACT — duplicated import block**: identical imports at app.py:1-19 and app.py:439-455 (copy-paste of notebook cells into one file).
- **FACT — no chunking at all**: the entire combined multi-PDF text goes into one prompt (app.py:597-675). Works only because target models are long-context; no token budget guard, no truncation. A 500-page PDF would blow the context. NotebookLlama (below) fixes exactly this.

## 1.5 PDF2Audio → ragbot takeaway matrix

| Pattern | Verdict for ragbot |
|---|---|
| ext-only type detection | REJECT — ragbot's mime→ext→byte-sniff already stronger (CLAUDE.md) |
| pypdf flat `extract_text()` | REJECT — loses structure; ragbot uses structured-markdown parsers |
| Pydantic + retry(ValidationError) | ADOPT-consider for structured-output nodes (T2, cheap robustness) |
| prompt-pack-per-language as data | CONFIRMS ragbot `language_packs` design |
| scratchpad-in-schema single-call CoT | NOTE — cost-efficient alternative to multi-node plan/act |
| tagged `<input_text>` isolation | CONFIRMS ragbot XML-wrap approach |
| ordered fan-out TTS | CONFIRMS Async Rule 4 layered gather |
| hardcoded model lists / broad except / dead code | REJECT — catalogued as anti-patterns |

---

# PART 2 — llama-cookbook: every RAG-relevant recipe lesson

## 2.1 NotebookLlama Step-1: "LLM as text pre-processor" for PDFs (in working tree)

Source: `end-to-end-use-cases/NotebookLlama/Step-1 PDF-Pre-Processing-Logic.ipynb`.

- **FACT** (CELL 10): PyPDF2 page-loop extraction with a **hard `max_chars=100000` budget guard** — stops mid-page when the cap is hit and reports it. Simple, explicit ingestion budget; ragbot ingest has per-doc limits in `plan_limits`, same idea.
- **FACT** (CELL 8): validation = exists + `.endswith('.pdf')` — again extension-only (anti-pattern per ragbot CLAUDE.md).
- **FACT** (CELL 18): **word-boundary chunking**, not char-boundary: `create_word_bounded_chunks(text, target_chunk_size)` accumulates words until `target_chunk_size` chars — rationale in CELL 17: "One issue with passing chunks counted by characters is, we lose meaning of words so instead we chunk by words". Baseline lesson: never cut mid-word/mid-token; ragbot's structural chunker is strictly stronger, but this is the floor any chunker must clear.
- **FACT** (CELL 16): the cleanup system prompt contains explicit **anti-summarize guards**: "Remember DO NOT START SUMMARIZING THIS, YOU ARE ONLY CLEANING UP THE TEXT AND RE-WRITING WHEN NEEDED" and "start your response directly with processed text and NO ACKNOWLEDGEMENTS". Lesson for ragbot ingest-enrichment prompts (Haiku enrich): a cleanup/enrich LLM will silently drift into summarizing — the guard must be explicit, and (per ragbot rules) verified by span/length checks, not trusted.
- **FACT** (CELL 21): per-chunk cleanup call capped at `max_new_tokens=512` for ~1000-char input chunks — the output budget bounds worst-case cost per chunk.
- **FACT** (CELL 25): **incremental write + flush per processed chunk** (`out_file.write(processed_chunk + "\n"); out_file.flush()`) — crash-resumable streaming ingestion; matches ragbot's soft-failure/canonical-ingest sensibility.
- **FACT — notebook ordering bugs** (quality signal about cookbook code): CELL 22 calls `create_word_bounded_chunks(text, CHUNK_SIZE)` before `text` is read (read happens in CELL 24); CELL 25 does `processed_text += processed_chunk` but `processed_text` is never initialized in the notebook source. Runs only if executed out of order / re-run. Lesson: cookbook code is *pattern* evidence, not production evidence.
- **FACT** (README.md:19-22 of NotebookLlama): deliberate **model-tier laddering per stage**: 1B (cleanup) → 70B (creative transcript) → 8B (rewrite to strict tuple format) → TTS. "Small model for mechanical transform, big model for creative synthesis, small model for format discipline." This directly mirrors ragbot's tiering memory (Haiku only decomposer/HyDE/ingest-enrich; 4.1-mini for answer).
- **FACT** (Step-3 CELL 3): the rewrite model is forced to emit a **machine-parseable artifact** ("STRICTLY RETURN YOUR RESPONSE AS A LIST OF TUPLES") which Step-4 parses via `ast.literal_eval(PODCAST_TEXT)` (Step-4 CELL 35/37). Prompt-enforced format + strict parser at the consumer boundary — brittle (no retry-on-parse-failure, unlike PDF2Audio's tenacity pattern), but the "artifact contract between pipeline stages" idea is right. Also FACT: Step-2/3 pass artifacts via pickle files (`data.pkl`, `podcast_ready_data.pkl`) — stage decoupling via serialized artifacts.
- **FACT** (Step-2 CELL 8): encoding-fallback reader — UTF-8 first, then `latin-1`, `cp1252`, `iso-8859-1` — "to avoid issues with generic PDF(s)" (CELL 7 note). Multi-encoding tolerance is a real multi-format-ingest concern ragbot's TXT/CSV path should keep in mind.

## 2.2 Contextual-Chunking-RAG (fetched from git): batch contextual keywords — the cheap alternative to Anthropic Contextual Retrieval

Source: `end-to-end-use-cases/Contextual-Chunking-RAG/{README.md,Tutorial.ipynb,helper.py}`.

- **FACT** (README.md:3-5): problem statement = "Independent chunking … leads to the loss of contextual information between chunks… Generate keywords for each chunk to fulfill missing contextual information. These keywords (e.g., 'BMW, X5, pricing') enrich the chunk … bridges gaps between related chunks."
- **FACT — the key efficiency twist** (README.md:9): "**This method does not require calling LLM for each chunk separately, which makes it efficient.**" Implementation (Tutorial.ipynb CELL 5-8, helper.py:82-93): concatenate ALL chunks as `### Chunk N ###` sections into **one** prompt; one LLM call returns `Chunk N: kw1, kw2` lines for every chunk; keywords are prepended to each chunk before embedding (`chunk = #{keywords}\n{chunk}`, Tutorial CELL 11, helper.py:108).
  - Contrast with Anthropic-style per-chunk contextualization (see §2.3): 1 call per *document* vs 1 call per *chunk*. For ragbot's AdapChunk/enrichment pipeline this is the **T2 cost lever**: same "context restoration" goal at ~1/N the calls, at the price of shorter context strings (keywords vs sentences).
- **FACT** (Tutorial CELL 5): chunking is naive token-window split via tiktoken `o200k_base`, 400-token chunks, no overlap — because keyword enrichment is expected to compensate for dumb chunking.
- **FACT — LLM output parsing is defensive by necessity** (helper.py:51-78 + the `temp()` self-test at helper.py:118-131): the keyword-list parser handles THREE observed header formats (`**Chunk 1**`, `### Chunk 2 ###`, `** Chunk 3 **`) because the LLM does not emit a stable format; the file even embeds a regression fixture of the drift. Lesson: any "LLM returns a list" contract needs a tolerant parser + fixtures of real drift (ragbot decomposer/FAQ generator parsers should keep such fixtures).
- **FACT** (helper.py:44-47): their grounding prompt is one line: "Is answer is not given below, say that you don't know it. **Make sure to copy answers from documents without changing them.**" — verbatim-copy instruction as an anti-hallucination measure (same family as ragbot's anti-fabricate sysprompt rules; here it's app-injected, which ragbot forbids — in ragbot this text belongs to the bot owner's `system_prompt`).
- **FACT** (helper.py:96-114): synthetic eval-question generation *per chunk* (1-3 full-context-free questions), sampled over `min(n//5, 60)` chunks — i.e., generate eval questions from a random 20% sample capped at 60. Cheap corpus-grounded QA-set bootstrap = same idea as ragbot's auto-FAQ candidate generator.
- **FACT** (embedding.py:8-17): local `jinaai/jina-embeddings-v2-base-en` via mean-pooled `last_hidden_state`; note at embedding.py:35 a commented confession: llama-index's stock `HuggingFaceEmbedding` wrapper "did not produce reasonable results for some reason" — i.e., embedding wrapper choice changed retrieval quality. Echoes ragbot memory lesson "ZE zembed-1 default 2560-dim must pass dimensions:1280" — always verify the actual vector path, wrappers are not interchangeable.

## 2.3 Together "Open Contextual RAG" (fetched): the full Anthropic Contextual-Retrieval pipeline in open source

Source: `3p-integrations/togetherai/llama_contextual_RAG.ipynb`.

- **FACT** (CELL 1): five-step canonical pipeline stated explicitly: (1) per-chunk context snippet via small LLM, (2) hybrid sparse+dense embedding, (3) **RRF** fusion, (4) "Retrieve top 150 chunks → Reranker → top 20", (5) generate. (The demo itself uses smaller k's: top 6 hybrid → rerank top 3, CELL 50-51.)
- **FACT** (CELL 8): "We can get away with **naive fixed sized chunking as the context generation will add meaning to these chunks**" — chunk 250 chars, overlap 30. The recipe's thesis: chunk-augmentation buys back what dumb chunking loses. Ragbot's position (structural chunking + optional enrichment) is the stronger both-ends approach.
- **FACT** (CELL 12): the contextualization prompt: full document + chunk → "Answer ONLY with a succinct explanation of the meaning of the chunk in the context of the whole document."
- **FACT — cost math worked out in-recipe** (CELL 21-22): Llama-3.2-3B at $0.06/1M tokens, ~1660 tokens per context generation → "**we can generate 10,000 contexts for a $1.00**"; even at 130k-token documents, ~128 contexts/$1. Also names **prompt caching** of the repeated document KV as the enabler (CELL 10). This is the reference budget envelope if ragbot ever turns on per-chunk contextual enrichment (currently ragbot uses Haiku enrich — same tier logic).
- **FACT** (CELL 45): textbook RRF implementation with `K=60`, `rrf_map[item] += 1/(rank + K)` — identical constant family to ragbot's RRF fuse node.
- **FACT** (CELL 39): BM25 via `bm25s` library over the *contextualized* chunks — the context snippet participates in the sparse index too, not just the dense one. Subtle and important: if ragbot enriches chunks, the enrichment must be visible to BM25 as well as pgvector, or half the hybrid benefit disappears. **HYPOTHESIS**: worth checking whether ragbot's BM25 tsvector is built from enriched or raw chunk text.
- **FACT** (CELL 51): reranker = `Salesforce/Llama-Rank-V1` via Together rerank API, `top_n=3`.

## 2.4 Simple/text RAG recipes (fetched): the baselines

Sources: `getting-started/RAG/hello_llama_cloud.ipynb`, `3p-integrations/togetherai/text_RAG_using_llama_on_together.ipynb`, `end-to-end-use-cases/customerservice_chatbots/RAG_chatbot/RAG_Chatbot_Example.ipynb`.

- **FACT** (hello_llama_cloud CELL 18): `RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)` + FAISS + `all-mpnet-base-v2`; guidance in CELL 19: "**use larger chunk sizes for highly structured text such as code and smaller size for less structured text**… experiment with different chunk sizes and overlap values." Chunk size as per-doctype tunable — matches ragbot's per-doctype template chunking skill.
- **FACT** (hello_llama_cloud CELL 22-27): explicit demonstration that a follow-up question **without chat history** produces an unrelated/hallucinated answer ("no context passed so Llama 3 doesn't have enough context to answer so it lets its imagination go wild"), fixed by `ConversationalRetrievalChain` + caller-maintained `chat_history` list. Condense-question + history threading = ragbot's condense node rationale.
- **FACT** (RAG_Chatbot_Example CELL 13-14): `chunk_size=500, chunk_overlap=10`, with the note "these two parameters can directly affect the quality of the LLM's answers"; retriever `k=6` (CELL 33); FAISS chosen over Chroma for production because it handles indexes "that may not fit in RAM" (CELL 17). Embedding downgrade to `all-MiniLM-L6-v2` (384-dim) purely "so indexing runs faster" (CELL 15) — explicit speed/quality trade.
- **FACT** (RAG_Chatbot_Example CELL 33): the QA template embeds a fallback-behavior rule *in the application*: "Use the following pieces of context to answer the question. **If no context provided, answer like a AI assistant.**" — i.e., silently answer from parametric memory when retrieval is empty. **Direct counter-example to ragbot sacred rule #10 and HALLU=0**: ragbot must never inject such a rule, and "no context → free-associate" is precisely the hallucination amplifier ragbot's refuse-template design prevents.
- **FACT** (text_RAG CELL 11): embedding-input construction concatenates `title + overview + tagline` per record — "makes the text that will be embedded … more informative … embeddings will be richer". Multi-field concat before embedding = same trick as ragbot's heading-path re-attachment for tabular/atomic chunks.
- **FACT** (text_RAG CELL 2): plainly states RAG's three benefits: hallucination reduction because "relevant context is provided directly in the prompt", **citability of source material**, on-prem data. Baseline framing only.

## 2.5 LangGraph RAG agent (fetched): Adaptive + Corrective + Self-RAG in one graph — the closest analog to ragbot's query pipeline

Source: `3p-integrations/langchain/langgraph_rag_agent.ipynb`.

- **FACT** (CELL 2): explicit synthesis of three papers: **Adaptive RAG** (routing, arXiv 2403.14403), **Corrective RAG** (fallback to web search when docs irrelevant, arXiv 2401.15884), **Self-RAG** (self-correcting hallucinated/unresponsive answers, arXiv 2310.11511), and maps them to agent concepts: reflection / planning / tool-use.
- **FACT** (CELL 5-9): FOUR structured-output LLM graders, each a tiny pydantic model with a binary field:
  1. `RouteQuery` — `datasource: Literal["vectorstore","web_search"]` picked from a one-line corpus description in the system prompt;
  2. `GradeDocuments` — per-document relevance yes/no, with prompt calibration: "**It does not need to be a stringent test. The goal is to filter out erroneous retrievals**";
  3. `GradeHallucinations` — "is the generation grounded in the retrieved facts" yes/no;
  4. `GradeAnswer` — "does the answer address the question" yes/no.
- **FACT** (CELL 11-12) graph wiring: entry router → `retrieve` → `grade_documents` (drops irrelevant docs; if ANY doc was irrelevant set `web_search="Yes"`) → either `generate` or `websearch`→`generate`; then post-generation conditional: hallucination-grade fail → **loop back to `generate`**; answer-grade fail → `websearch`; both pass → END.
- **FACT — no retry cap**: the `"not supported": "generate"` edge (CELL 12) creates an unbounded regenerate loop; no counter in `GraphState` (CELL 11: only question/generation/web_search/documents). A persistent hallucination-grade failure = infinite loop. Lesson for ragbot: every self-correction edge needs a bounded retry budget (ragbot's rewrite_retry instrumentation implies it has one — this recipe shows the failure mode of omitting it).
- **Direct relevance to a known ragbot failure** (memory: multi-query fix 2026-05-15 — "CRAG grader rejects all → chunks_used=0"): this recipe's answer to "grader rejected everything" is an explicit **fallback retrieval source** (web search) rather than refusing. Ragbot, being corpus-bound, can't web-search, but the structural lesson holds: `grade_documents → all rejected` must route to a recovery node (retrieval retry with different strategy / rewrite / threshold relaxation), never fall through to generate-with-zero-chunks. **HYPOTHESIS**: wiring a bounded "all-rejected → rewrite+retry" edge would reduce ragbot's refuse-when-corpus-has-answer rate; needs load-test measurement per rule#0.
- **FACT** (CELL 6): grader-calibration language ("not a stringent test") is itself a tunable — CRAG-grader strictness lives in prompt wording. Ragbot's grader prompt should treat that phrase as config, not constant.

## 2.6 RAFT-Chatbot (fetched): fine-tune × RAG, refusal training, and the most honest eval writeup in the repo

Source: `end-to-end-use-cases/RAFT-Chatbot/{README.md,raft_utils.py,raft_eval.py}`.

**Recipe**: Retrieval-Augmented Fine-Tuning — train an 8B model on (question, oracle doc D* + 4 distractor docs Di, CoT answer) triplets so it learns to use relevant docs and ignore distractors (README.md:11-27, RAFT paper arXiv 2403.10131).

- **FACT — dataset construction** (raft_utils.py:79-139, README.md:70-74): docs → `RecursiveCharacterTextSplitter` chunks of 1000 chars, overlap `chunk_size/10`, custom separators `["----------","\n\n","\n"," "]`; **dedup by exact page_content + drop chunks <100 chars** (raft_utils.py:96-101); 4 questions generated per chunk; CoT answer generated per (chunk, question); each training row gets 4 random distractor chunks, oracle included with P=80%.
- **FACT — quote-anchored CoT answers** (README.md:109): generated answers must embed `##begin_quote## … ##end_quote##` extractive quotes from the oracle doc, final answer after `<ANSWER>:`. Citation-anchored generation as a *training-data format* — the same grounding-by-quotation idea ragbot enforces at inference.
- **FACT — engineered refusal examples** (README.md:76-78, raft_utils.py:226-240): with 5% probability the oracle doc is replaced by another distractor and the label becomes "Sorry, I don't know the answer to this question because related documents are not found." Rationale: "In real-world production scenarios, **we prefer that the chatbot refuses to answer when not enough context is provided**, so that we can detect this refusal signal and mitigate the risk of producing wrong or misleading answers." Refusal as a *deliberately trained, detectable signal* — the fine-tuning mirror of ragbot's refusal-trap design in load tests.
- **FACT — eval harness** (raft_eval.py:98-182): three metrics computed side-by-side: ROUGE, exact match, and **LLM-as-judge** (Llama-3-70B given question+prediction+gold, correctness = `"YES" in judge_response`, raft_eval.py:157-162). Retriever for the eval RAG runs: FAISS cosine + `all-mpnet-base-v2`, configurable top-k (raft_eval.py:44-69).
- **FACT — results** (README.md:175-202):
  - non-RAG baselines: 8B = 47.9%, 70B = 59.2% judge-score;
  - with RAG, RAFT-8B ≈ 8B-RAG baseline and *below* 70B-RAG at top_k ≤ 5, but at **top_k = 7 the `all_data` RAFT-8B jumps to 76.06%, beating 70B-RAG's 74.65%** — retrieval depth interacts non-monotonically with model training; a single top_k sweep can flip conclusions.
  - **Precision metric**: `LLMScore / (1 - numRefusal/totalQA)` (README.md:198) — "likelihood of producing correct answers **when the model decides to respond**"; RAFT `all_data` reaches 82.97% precision at top_k=7. This is the exact complement of ragbot's **Coverage** metric (CLAUDE.md): cookbook measures precision-when-answering, ragbot measures answered-when-answerable — a complete eval needs BOTH axes.
  - refusal-behavior finding (README.md:192): the small-data `llama_only` model "**did not learn to refuse at all, likely due to the limited dataset size**" — refusal behavior needs sufficient training signal.
- **FACT — Key Takeaways verbatim** (README.md:224-229): (1) "Few thousand RAFT examples are insufficient, at least 10K examples recommended"; (2) "**The LLM_as_judge is not always reliable**, … answers were scored incorrectly"; (3) "**The chunk_size for RAFT documents and RAG documents should be the same**" — train/serve chunking consistency; (4) RAFT helps the model *differentiate related docs from distractors* rather than memorize.
- **FACT — motivation cites the fine-tuning hallucination risk** (README.md:7): fine-tuning to inject knowledge "is correlated with hallucinations w.r.t. preexisting knowledge" (arXiv 2405.05904) — supports ragbot's architecture choice (RAG + per-bot sysprompt, no per-tenant fine-tunes).

## 2.7 Multi-Modal RAG (fetched README): image corpus → text-description index

Source: `end-to-end-use-cases/Multi-Modal-RAG/README.md`.

- **FACT** (lines 5-9, 31-56): pipeline = Llama-3.2-11B-Vision captions 5000 images → **clean the synthetic labels** → embed the *text descriptions* (`BAAI/bge-large-en-v1.5`) into **LanceDB** → retrieve by text similarity; the vision model describes the *query image* at runtime and retrieval runs on that description. I.e., multimodal RAG reduced to text-RAG through a captioning normalizer — the same "single canonical representation" philosophy as ragbot's every-format→structured-markdown funnel, extended to images.
- **FACT** (lines 64-67): synthetic-label reality check: "even after some fun prompt engineering, **the model faces some hallucinations — there are some issues with the JSON formatting and we notice that it hallucinates the label categories**", requiring a rebalancing/correction pass. LLM-generated metadata is dirty by default; any ragbot LLM-enrichment output needs validation against a closed vocabulary/schema before indexing.
- **FACT** (line 99): retrieval-quality note — descriptions that lead with the item's *title* caused retrieval of "similar" instead of requested "complementary" items; embedding-input composition changes retrieval *semantics*, not just recall.

## 2.8 Agentic RAG ladder (dlai_agentic_rag README, fetched)

**FACT** (README lines 5-11): the LlamaIndex course ports define the canonical agentic-RAG capability ladder: **L1 router** (pick QA vs summarization tool per query) → **L2 tool calling** (LLM also infers tool arguments) → **L3 agent reasoning loop** (multi-step over one document with memory) → **L4 multi-document agent** (scale to many docs / increasing complexity). Useful as a maturity yardstick: ragbot today ≈ L1–L2 territory (intent routing, retrieval strategy dispatch) with graph-orchestrated loops rather than free agent loops. (Notebooks themselves not fetched — README only; contents beyond this labeled n/a.)

## 2.9 Long-context patterns as RAG alternative/complement (working tree)

- **FACT** (`getting-started/build_with_llama_4.ipynb` CELL 4-9): Llama-4-Scout demo ingests a **whole flattened repo (~900k tokens) in one prompt** (vLLM, `max_model_len=1100000`, `attn_temperature_tuning: True` "for best long context performance") and writes a guide from it, "less than 3 minutes". The notebook contains **no RAG content** — its answer to "know a big corpus" is brute-force long context. Boundary lesson: single-artifact synthesis tolerates full-stuffing; ragbot's multi-tenant per-query economics (T2: token/turn, p95) do not — retrieval stays mandatory; long-context is the tool for *ingest-time* jobs (whole-doc contextualization à la §2.3, doc-level summaries), not per-turn answering. Cf. `end-to-end-use-cases/README.md:35-44` (Research-Paper-Analyzer and Book-Mind-Map are both "long context instead of RAG" use cases).
- **FACT** (`end-to-end-use-cases/video_summary.ipynb` CELL 7-27): explicit demonstration that exceeding context (~40k tokens into an 8k model) returns an **empty/failed result**; remedies compared: `stuff` (fails, CELL 26-27), **`refine`** (sequential rolling summary — 33 LLM calls, ~10 min, CELL 22-23), **`map_reduce`** (parallel sub-summaries + combine — ~3 min, CELL 24-25); chunking for summarization via `RecursiveCharacterTextSplitter.from_tiktoken_encoder(chunk_size=1000, chunk_overlap=0)` (CELL 20). Latency shape (sequential refine 10min vs parallel map-reduce 3min) is the same asyncio-gather lesson ragbot codified (RAGAS-parallel rule).
- **FACT** (`getting-started/build_with_llama_api.ipynb` CELL 22-25): JSON-schema-constrained responses (`response_format={"type":"json_schema", ...}` from a pydantic schema) and standard two-turn tool-calling loop; CELL 26-27 a **moderations endpoint** run on BOTH user prompts and model responses — input+output guardrail symmetry, same shape as ragbot's guardrail nodes. No RAG content otherwise.

---

# PART 3 — Consolidated lessons for ragbot (ranked)

1. **Grader-rejects-all needs a recovery edge, and every self-correction loop needs a retry cap** — LangGraph recipe wires corrective fallback but omits the cap (infinite `generate` loop possible); ragbot has the opposite gap history (CRAG rejects all → chunks_used=0 refuse). Combine both: bounded corrective retry. (T1; §2.5)
2. **Contextual enrichment has two price points**: per-chunk context (Anthropic CR style, ~10k contexts/$1 with a 3B model + prompt caching, §2.3) vs one-call-per-document batch keywords (§2.2). If ragbot ships chunk enrichment, benchmark the batch-keyword variant first (T2), and ensure enrichment text feeds **both** BM25 and vector indexes (§2.3 FACT).
3. **Precision-when-answering (RAFT) + Coverage (ragbot) are complementary eval axes**; also from RAFT: LLM-judge is unreliable (their words), top_k sweeps can flip verdicts (top_k=7 flipped 8B vs 70B), and **train/serve chunk_size consistency matters**. (test-health; §2.6)
4. **Refusal as engineered, detectable signal**: RAFT's 5% synthetic refusal rows with a fixed refusal string = the ingest/training mirror of ragbot's refusal-trap + `oos_answer_template` design; small corpora fail to learn refusal at all. (T1/HALLU; §2.6)
5. **"If no context, answer like an AI assistant" is the canonical anti-pattern** (RAG_Chatbot_Example CELL 33) — direct violation of ragbot sacred rule #10 / HALLU=0 if copied; keep as a negative example in reviews. (§2.4)
6. **LLM-as-preprocessor needs anti-summarize guards + tolerant parsers + drift fixtures** (NotebookLlama Step-1 prompt; Contextual-Chunking parser with 3 header formats embedded as a self-test). (multi-format ingest; §2.1, §2.2)
7. **Model-tier laddering per pipeline stage** (1B clean → 70B create → 8B format; 3B for context generation) independently confirms ragbot's Haiku-partial/4.1-mini-answer split. (T2; §2.1, §2.3)
8. **Structured-output robustness**: pydantic schema + retry-only-on-ValidationError (PDF2Audio) > prompt-enforced format + bare `ast.literal_eval` (NotebookLlama). (§1.3, §2.1)
9. **Multimodal RAG = normalize to text descriptions, then text-RAG** — validates ragbot's single-canonical-representation ingest funnel and warns that LLM-generated metadata hallucinates categories/JSON and must be schema-validated before indexing. (§2.7)
10. **Long-context ≠ RAG replacement for a platform**: cookbook uses full-stuffing only for single-artifact, ingest-like jobs; per-turn answering economics keep retrieval mandatory. (§2.9)
11. **PDF2Audio's extraction layer is a catalog of ingest anti-patterns ragbot already legislates against** (ext-only detection, flat text, hardcoded model lists, broad except, multi-doc blind concat) — its *value* is in the prompt/structure patterns (language packs as data, tagged input isolation, scratchpad-in-schema, ordered fan-out). (§1.2–1.5)

---

# Appendix — coverage honesty (rule#0)

- **FACT**: everything in Parts 1–2 was read from actual file contents (working tree or `git show HEAD:<path>` blobs of commit `2f22a9e`).
- **NOT read** (out of budget, listed for completeness from `git ls-tree`): `3p-integrations/llamaindex/dlai_agentic_rag/*.ipynb` bodies, `langgraph_rag_agent_local.ipynb`, groq/pinecone RAG templates, `multimodal_RAG_with_nvidia_investor_slide_deck.ipynb`, Multi-Modal-RAG notebook bodies, `raft.py`/`format.py`/`raft.yaml` bodies, benchmarks/llm_eval_harness. Claims about those files are limited to their existence and README descriptions.
- **HYPOTHESES flagged inline**: §2.3 (whether ragbot BM25 indexes enriched text — needs code check), §2.5 (all-rejected→rewrite edge would lift coverage — needs load test), §1.3 (retry-on-ValidationError benefit for ragbot nodes — needs measurement).
