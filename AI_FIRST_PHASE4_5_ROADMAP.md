# DocMind-AI — AI-first Phase 4 and Phase 5 roadmap

**Status:** Roadmap redesign, 2026-11-09. Planning document, not implementation evidence.
**Working branch:** `feature/phase4-production-hardening` (keep the existing branch name to avoid disrupting the user's current checkout).
**Priority:** Finish and validate RAG, document understanding, answer grounding, multilingual reasoning, and AI user experience **before** general product/operations hardening.
**Operating style:** One small Windows CMD step at a time; preserve previously accepted QA data; changes validated in separate disposable KBs and CI.

## Where the project is today — baseline, not a claim of universal AI completion

- Phase 3 controlled local acceptance: **28/28 PASS; 0 FAIL; 0 BLOCKED** on `phase3.10-grounded-2026-11-04-r3` (see `PHASE3_9_10_ACCEPTANCE.md`).
- Existing pipeline: upload/ingest PDF, DOCX, TXT; extract/OCR where supported; chunk; E5 multilingual embeddings; Qdrant vectors; scope-aware retrieval through Python; MySQL verified membership/document/version/READY checks in Express; grounded Ollama answers with citations/abstention; frontend Q&A.
- Known boundary: 28 controlled fixtures do not establish broad retrieval recall, citation **entailment**, scanned/table-heavy document handling, multi-document synthesis, high-scale throughput, or production readiness.
- Phase 4.1 password recovery was *already started* on this branch (API, UI, hashed token migration and isolated tests). Its local database migration and Prisma client were applied/generated; the user reported local backend typecheck/build passing. Email delivery requires provider configuration and end-to-end validation. **Do not undo or re-run the migration.** Park remaining account-recovery work under Phase 5.7 until AI gates pass.

## Phase 4 — Complete the advanced RAG and AI **core**

| Milestone | Focus and work | Primary deliverable / exit evidence |
|---|---|---|
| **4.1** | **RAG evaluation foundation first.** Create a source-labeled benchmark from **separate disposable KBs**, covering answerable, unanswerable, conflicting, cross-document, multilingual, OCR, table, adversarial, and update/delete cases. Label expected answer, exact supporting passages, document/page, and permission boundary. Preserve the 28-case regression as a distinct legacy gate. | Reproducible evaluation runner; per-case pass/fail; baseline retrieval Recall@K, MRR/nDCG, grounded correctness, citation support, abstention, and latency. Start with at least 100 independently labeled questions, expand during Phase 5. |
| **4.2** | **Document understanding.** Audit and improve extraction of PDF layout, scanned pages/OCR, DOCX tables, headings, lists, page references and TXT; track skipped/failed/partial content instead of silently marking READY. | Fixture tests for dense layouts, tables, multilingual scans, partial OCR, corrupt/protected files; traceable source spans/pages. |
| **4.3** | **Chunking and indexing.** Compare existing page-aware chunking to token-budgeted, layout-/section-aware chunks with measured overlap. Maintain immutable document-version provenance, safe staged publication, and no stale-archived/old-version retrieval. | Benchmark-backed choice of chunk strategy; reproducible versioned index/update/delete tests on disposable KBs. |
| **4.4** | **Retrieval quality.** Measure existing multilingual E5 + Qdrant baseline; introduce **tenant-filtered hybrid sparse/keyword + dense search** if it improves recall; evaluate fusion, query rewriting/normalization, and configurable topK. | Retrieval experiments (recall/MRR/nDCG and latency); strict scoped filter + MySQL postfilter preserved; no leakage. |
| **4.5** | **Reranking and context selection.** Evaluate a local multilingual cross-encoder reranker, deduplication, diversity, query-aware passage packing and a strict token budget. Select by measured gain vs hardware latency. | Ablation report: dense-only vs hybrid vs reranked; citations still reference authorized original passages. |
| **4.6** | **Grounded answer reasoning.** Improve answer generation for multi-document comparisons, multi-hop references, conflicts, numeric/date/duration facts, tables and explicit uncertainty. Validate source-to-claim support rather than merely citation IDs; separate unsupported facts from service errors; resist instructions embedded in documents. | Regression tests for semantic entailment, conflicting evidence, prompt injection, cross-source contamination, and correct abstentions. |
| **4.7** | **Bangla/English/code-switching robustness.** Compare retrieval and answer quality across languages, Unicode/script variants, mixed queries and translated questions without assuming English term overlap. | Balanced multilingual benchmark and targeted improvements with source-correct answers and abstentions. |
| **4.8** | **AI optimization and Phase 4 gate.** Benchmark local embedding/reranker/LLM options within realistic hardware; measure memory, time-to-first-token, p50/p95 latency, throughput, failures and answer quality. Retain model/config versioning. | Selected pipeline with a measured quality/latency tradeoff, reproducible CI/offline tests, and documented limitation cases. |

### Phase 4 acceptance gate

- Keep **28/28 Phase 3 controlled cases passing** without changing the protected QA KBs.
- For new, independent labeled sets, publish denominators and methodology. **Provisional targets** (to confirm after baseline): retrieval Recall@10 >= 90%; citation-support precision >= 95%; correct abstention >= 95%; grounded answer correctness >= 90%. Targets are goals, **not achieved results**; no cherry-picking or tuning to held-out test cases.
- **Zero observed cross-tenant disclosure** under dedicated negative/adversarial authorization tests; never relax the already implemented MySQL/READY scope enforcement.
- Record p50/p95 end-to-end answer latency and resource use on documented local hardware; set acceptable threshold from baseline and realistic UX, not an invented generic number.
- Each improvement must be compared to the previous pipeline; revert or disable features that decrease real benchmark quality without an acceptable tradeoff.

## Phase 5 — Prove the AI works end to end, then ship it safely

| Milestone | Focus and work | Exit evidence |
|---|---|---|
| **5.1** | **Independent real-world AI validation.** Expand to at least **300 human-reviewed questions** on varied and previously unseen documents (e.g., ≥30 documents spanning PDF/DOCX/TXT, scans, tables, long files). Separate development and held-out evaluation splits; review citation entailment and failure taxonomy manually. | Published frozen benchmark, mistake categories, per-language/per-document scores, confidence intervals where meaningful. |
| **5.2** | **Practical AI experience.** Citation click-through to exact document/page/quote, scoped conversations/follow-up questions, source inspection, answer feedback, clear abstention and conflict explanations. Preserve user/workspace boundaries across turns. | End-to-end UI/API tests with references, conversation scope, follow-up grounding and accessibility checks. |
| **5.3** | **RAG lifecycle quality.** Test new uploads, retries, re-uploads, version switches, updates, archived documents, failed OCR and re-indexing in **disposable KBs only**, with vector/metadata consistency and recoverable failures. | No stale-version retrieval, no partially published READY documents, operator-visible failure reasons. |
| **5.4** | **AI safety and regression discipline.** Document prompt-injection tests, fabricated citation probes, privacy and authorization attacks, multilingual hallucination tests, regression dashboards and redacted diagnostics. | Repeatable CI quality gates; no raw document text, credentials or tokens in monitoring logs. |
| **5.5** | **Hardware and reliability validation.** Soak/concurrent query tests, model restart/recovery, vector outages, cold starts, capacity profiling, throughput and quality/performance tradeoffs on intended deployment machine. | Repeatable load report, bounded failures/timeout/queues, and operational budgets. |
| **5.6** | **AI acceptance freeze.** Run full Phase 3 + Phase 4 + Phase 5 AI suites; review unresolved edge cases and known limitations; freeze embeddings, index schema, retriever/reranker, prompt/config/model IDs and evaluation corpus. | Signed-off AI/RAG release candidate based on measurable gates, not a claim that arbitrary questions are always answerable. |
| **5.7** | **Minimum production safety (only after AI core acceptance).** Finish and test email-based password recovery already started; secrets/TLS, session/CSRF and upload security, malware/retention policy, minimal telemetry/alerts, backup + restore rehearsal, container/service deployment and rollback. | Secure pilot deployment and recovery checklists; production is **not** declared ready until these essential controls pass. |

### Sequencing and non-negotiable constraints

1. **Immediately next:** Phase 4.1 dataset + retrieval/answer *baseline*. Do not start by tuning model parameters blindly.
2. After baseline, choose whether chunking, OCR, hybrid retrieval or reranking is the bottleneck; prioritize evidence over fashion.
3. Multi-document answer quality and genuine citation support come before polish or general infrastructure.
4. Do **not** fine-tune/train a model unless measured errors justify it over retrieval, prompt/context or reranking fixes; base pipeline uses local existing models.
5. No main-branch merge without user approval. No SQL purge, Docker volume reset, reingestion, or changes to the protected Phase 2 KB `cmvw9ro3w000548l1gkuh2gx8`, Phase 3.3 KB `cmvxmqrea0000c8l1pcvmh7oa`, or Phase 3.8 KB `cmvz6m3h10000qol1hoahhd6r`.
6. New integration tests that write to SQL run **only against isolated CI/test databases**; local validation on the existing development DB uses read-only/offline tests unless explicitly reviewed.
7. Existing Phase 4.1 auth code and database migration remain intact; defer provider setup to 5.7, not delete completed work.
8. The user prefers **one clear CMD action at a time** and no redundant reruns of the completed Phase 3 gate.
9. "Complete AI/RAG" here means the specified validated **release scope and measurable gates**, never a guarantee that every arbitrary document/question will work or that the product is production-ready.

## Immediate implementation backlog (Phase 4.1)

- Inventory existing Phase 3 retrieval and answer test harnesses; choose a consistent JSONL case schema with question, scope, expected evidence IDs, correct answer or abstention, language, document category and flags.
- Create new fixtures only in a disposable KB and synthetic documents; avoid copying protected QA data.
- Implement a **read-only** evaluation command that runs (a) retrieval-only Recall@K and ranking metrics, (b) full answer groundedness/abstention/citation metrics where labels are available, (c) latency metrics, with machine-readable JSON/CSV and a readable summary.
- Establish baseline scores before modifying embedding, chunking, retrieval and answer pipelines.
