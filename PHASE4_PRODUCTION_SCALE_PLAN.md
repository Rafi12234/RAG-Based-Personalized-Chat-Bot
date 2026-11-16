# DocMind-AI — Phase 4 Production-Scale RAG Redesign (2,000–5,000 pages)

**Design status:** Proposed implementation plan, not a production-readiness claim.  
**Audit baseline:** feature/phase4-production-hardening at 74b4e9e, answer engine \`phase4.1-grounded-2026-11-16-r6\`.  
**Scope:** Design and build Phase 4; keep Phase 5 release, security and product integration gates separate.

## 1. Mission

Index individual 2,000–5,000-page books and documents *once*, asynchronously; then answer questions quickly through a reliable, tenant-scoped index. Accuracy requires retrieving the **right page, section, table or passage** even when the fact is deep inside the book, then grounding every answer claim in original source spans.

The app must not rely on a single vector for a whole book, five arbitrarily ranked chunks, stuffing thousands of pages into an LLM prompt, answer-key regexes, or an assumption that a bigger embedding model alone yields accurate RAG. OCR-heavy ingestion is inherently slower than search over an already indexed book.

**No absolute accuracy or latency guarantee is possible.** Publish accuracy/latency distributions and observed limitations on unseen benchmarks, with source and question provenance. Do not convert failed examples to green by modifying expected answers or hiding refusals.

**Safety invariants:** The current protected KBs and their SQL/vector/storage data are never touched during development. No destructive reindexing, migration, database/volume reset, bulk cleanup or deployment against local existing user data without explicit authorization. Only isolated new disposable fixtures are allowed for write tests. Preserve MySQL membership/READY/version verification and final authorization checks.

## 2. Current code audit and main gaps

| Path | Verified condition | Scale gap |
|---|---|---|
| \`backend/src/routes/documents.ts\` | 250 MiB single PDF/DOCX/TXT Multer upload, temporary disk then RustFS | A scanned 5,000-page file may exceed the byte limit. Need resumable multipart, upload quotas, preflight and cancellation. |
| \`python-worker/worker/document_extraction.py\` | PyMuPDF page streaming, PDF maximum **5,000 pages**; DOCX iterates python-docx document; TXT buffered reads | Page allowance is not a proof of fast, reliable processing. PDF plain text misses layout; DOCX opens whole object and flattens tables. |
| \`document_ocr.py\` + \`live_ingestion_worker.py\` | Page-level Tesseract candidate OCR; active worker accepts **maximum 10 OCR pages** | Large scans exceed current pilot capacity. Weak/garbled native text layers and mixed text/image pages may go unnoticed; no per-page quality record. |
| \`document_chunking.py\` + \`token_safe_chunking.py\` | 1,000-char/150-overlap chunks; token-safe E5 480-token subchunks; IDs/page provenance | Poor chapter/section/table structure, no parent-child navigator; character-first splitting loses context. |
| \`document_embeddings.py\` | Quantized ONNX multilingual E5-small, 384D; \`query: \` and \`passage: \` prefixes, 8-vector batches | Correct compact baseline, but not benchmarked for rare facts, numeric tables, 5k books and Bengali/code-switching; model has ~512-token input limit. |
| \`production_qdrant.py\` + \`production_index_staging.py\` | 384D cosine collection; tenant/KB/READY filters, staged and verified publication | Only one dense representation. No BM25/sparse branch, hierarchical index, generation alias migration, page/block navigator. |
| \`live_ingestion_worker.py\` | Pilot-only job, at most **50,000 chunks**, lease heartbeat, local download, vector verification | Not a checkpointed multi-shard large-book pipeline; crashed jobs require audit/recovery and OCR/embedding can be resource-heavy. |
| \`retrieval_service.py\` + \`retrievalPostFilter.ts\` | One dense nearest-neighbor query, up to \`min(topK*4,40)\` candidates; MySQL relational verification | No lexical or hybrid retrieval, no reranker, no chapter routing, no local neighbor expansion. |
| \`groundedAnswer.ts\` | Normally **at most 5 verified excerpts**, each <=1600 characters; Ollama 4096 context; bounded structured attribution | Bottleneck for cross-chapter synthesis and deeper facts. Special-purpose pattern extractors have brittle paraphrase/negation coverage. |
| \`retrievalClient.ts\` / \`groundedAnswer.ts\` | 20s retrieval timeout and 90s model timeout per attempt, possible second attempt | End-to-end p95 may be large; no benchmarked hardware SLO. |
| \`backend/prisma/schema.prisma\` | Document, DocumentVersion, IngestionJob | Missing page/block/section/geometry, processing manifest, per-page quality and checkpoints. |
| \`docker-compose.yml\` | Local single-host MySQL/Redis/Qdrant/RustFS; Qdrant/RustFS tagged \`latest\` | Development setup, not HA/backup/rollback/version-pinned production topology. |

**Preserve existing strengths:** version-scoped IDs, staged READY publication, tenant filtering and rechecking, token safety, model normalization, bounded batches, explicit citations, offline tests and controlled r6 57/57 regression. The newly started 12-question holdout identified missed facts; freeze the recorded baseline, don't tune generic production code against those specific question keys.

## 3. Target architecture

\`\`\`text
Browser → secure resumable upload / user query API
  │                      │
  ▼                      ▼
RustFS original     Authenticated tenant/KB query scope (MySQL)
  │                      │
Durable job queue          Query parse, language/identifier handling
  │                      │
File preflight       Scoped book/section navigation + global fallback
  │                      │
Page-range worker     Dense E5 + sparse/BM25 retrieval in parallel
  │                      │
Native text / OCR     Scoped fusion (RRF) and relational verification
Layout / tables            │
  │                Dedup, page diversity, multilingual reranking
Canonical page/block/section representation
  │                      │
Section parent chunks      Neighbor/parent expansion, evidence budget
+ token-safe child chunks  │
  │                  Grounded claim-by-claim answer
Dense+sparse staging        │
+ manifest validation      Citation/entailment check, abstention
  │                      │
Versioned Qdrant generation ─→ User answer + exact source/page locator
\`\`\`

The index must support *both* full-book semantic matching and exact terms (names, acronyms, part numbers, dates, rare words). Each retrieval branch is tenant/KB/READY/index-generation filtered. MySQL revalidates every selected candidate **before** rerank/generation and again on final response.

### Ingestion and data model

- Create immutable \`IngestionRun\` (parser/OCR/chunker/model/index revision, job status, per-stage progress, quotas, retry, checkpoint), \`DocumentPage\` (page index, text quality, OCR decision/confidence, checksum and errors), \`ContentBlock\` (type, ordered text, page/section locator, table row/col/header relations, optional bounding box), \`DocumentSection\` (chapter hierarchy, title, page span), \`IndexManifest\` (generation ID, expected/verified counts, checksums and active cutover).
- Do **not** claim DOCX page numbers from unpaginated python-docx. Cite stable section/paragraph/block IDs or a separate renderer's verified page map.
- Add authenticated resumable multipart uploads and format/page/byte/tenant quotas; maintain immutable original, checksum and source version. Account for antivirus, ZIP bombs and resource exhaustion.
- Preflight page count and quality; partition huge books into page-range shards, initially 25–100 pages **as an experiment**, with idempotent progress/checkpoint/retry. Separate heavy OCR/layout pools from online query serving.
- Choose native PyMuPDF extraction for simple pages and selective local layout/OCR/table understanding for complex pages. Compare a locally run Docling pipeline on representative scans/tables; it is an option, not a mandatory blanket rewrite. Record missing/failed/partial pages; never silently mark partial books READY.
- Use bounded batch embedding with exact tokenizer, deterministic versioned chunk IDs, content hashes and source coverage checks. Reject silent token truncation and broken table/heading mapping.
- Stage a **new generation** of both dense and lexical/sparse artifacts; verify all expected shards and coverage before an explicit logical READY/active-generation cutover. Implement non-destructive rollback. Existing live collection remains untouched until an authorized migration decision.

### Search and context

- Embed **child passages** (experiment with 256/384/448 model-token windows and 32–64-token overlap) and index **parent sections/chapter navigator** separately. Every child carries section path, block type, page offsets and parent/neighbor IDs; preserve original source spans.
- Run multilingual dense + BM25/sparse retrieval, initially 50–150 scoped candidates per branch. Use Qdrant's prefetch/fusion API where pinned version permits; start with RRF and compare DBSF/weighted RRF using *development* labels, not final holdout.
- Use chapter/document navigator for efficiency, but retain a global fallback so a wrong chapter selection cannot hide the correct page. Protect rare numeric and entity terms through lexical matching.
- Dedup candidate passages and locally rerank approximately 30–80 candidates with a multilingual cross-encoder selected by measured evidence recall and latency. Pack 8–20 verified evidence chunks after section/neighbor expansion **as an experimental starting point**; adapt the LLM token budget to the query.
- Decompose cross-chapter/multi-document questions into bounded subqueries; cite each independent claim. If evidence conflicts, is missing, or comes from a not-READY version, do not confidently invent a single answer.

### Grounding and response

- Model receives only verified, source-labeled evidence and clear separation between data and instructions; document instructions are never executed.
- Validate **claim → original passage and page/block locator** for numerical facts, negation, comparisons and contradictions, not merely the appearance of \`[S1]\`. Differentiate service timeout from genuine insufficient evidence.
- Favor a generic evidenced fact representation over a growing library of fixture-specific regex patterns. Structured extraction is useful for dates/units/IDs but cannot replace robust semantic retrieval.
- Long summaries, whole-book counts and chapter comparisons must use explicit coverage workflows; a five-snippet answer must never imply full-book review.

## 4. Implementation sequence (build, then milestone-level acceptance)

| Milestone | Engineering deliverable | Acceptance evidence |
|---|---|---|
| **4.1 Baseline freeze** | Keep verified r6 pipeline and 57/57 **controlled** test; log 12-question holdout misses as known limitations | Versioned baseline/splits; no tuning to holdout labels; no rerun of 57 after each code change |
| **4.2 Large-book ingestion and understanding** | Resumable uploads, quotas, PDF/DOCX native/layout/table/OCR processing, block/page schema, persisted shard jobs/progress/recovery | 2k and 5k-page synthetic files processed with all pages accounted for; separate OCR and native-text throughput/accuracy; no silent partial READY |
| **4.3 Hierarchical chunking and versioned index** | Section/block lineage, token-safe windows, dense/sparse artifact manifests, shadow generation, safe READY cutover and rollback | Full supported-text coverage, correct page/table locators, reproducible IDs, crash/retry, zero stale-version visibility |
| **4.4 Hybrid retrieval** | Tenant-filtered dense + sparse/BM25, section navigation with global fallback, fusion and query handling | Evidence Recall@10/@50, MRR, nDCG at pages 1/middle/5000 and across languages; beats dense-only without leakage |
| **4.5 Reranking and context** | Local multilingual reranker, neighbor expansion, diverse and adaptive evidence packing | Gains in page/span-level evidence recall and bounded p95 on documented hardware |
| **4.6 General grounded reasoning** | Claim-to-span support, negation, numeric/tables, conflict/abstention, multi-hop and source-injection defenses | Independent adjudicated factual accuracy, refusal quality and citation entailment; no benchmark-key hardcoding |
| **4.7 Multilingual parity** | Bengali/English/mixed, normalization, transliteration, multilingual OCR and cross-language grounding | Per-language and per-format results; no English-only average hiding failures |
| **4.8 Scale, soak and AI freeze** | Worker isolation and quotas, recovery/rollback, telemetry, GPU/CPU capacity and concurrency, long-document benchmarks | 5,000-page end-to-end proof, publishing actual p50/p95 and page/minute on stated hardware; no tenant disclosure |

**Development rule:** Build each milestone as a cohesive batch. Run CI/unit/integration/security tests continuously, *small automated smoke checks* at the end of a batch, and substantial manually reviewed large-document evaluations only at milestones. No repetitive Chrome Console audit loop.

## 5. Non-negotiable test corpus and success measures

- Freeze >=100 independently labeled **Phase 4** queries across >=10 documents, including 500, 2,000, and 5,000-page native, OCR-heavy and table-heavy sources. For Phase 5, extend to >=300 human-reviewed questions over diverse documents.
- Hide gold passages at deliberate varied page positions (1, 250, 1000, 2500, 4999), including repeated words, contradictory instructions, arbitrary identifiers and cross-chapter dependencies.
- Measure **ingestion page/block coverage**, OCR/text correctness, table structure fidelity, checkpoint recovery, token coverage and pages/minute; **retrieval page/span Recall@10/@50**, MRR/nDCG and multi-part evidence coverage; **answer factual and citation precision, abstention, hallucination**; **p50/p95/p99** warm/cold latency and CPU/RAM/GPU/disk under concurrent load.
- Provisional Phase 4 quality gates (not currently achieved): **Recall@10 ≥90%, citation support precision ≥95%, appropriate abstention ≥95%, grounded answer correctness ≥90%**, with full denominators and per-language/document-size breakdowns. Determine numeric latency SLOs *after* hardware-specific benchmarking; scanned 5,000-page books may need hours of CPU preprocessing.
- Required failure tests: malformed/corrupt PDFs and DOCXs, mixed text/image pages, garbled OCR, table header loss, service timeouts, tenant revocation during a query, stale/archived generation, duplicate jobs, crash and rollback, source prompt injection.
- “Production-grade 5,000-page RAG” is not accepted until real **5,000-page ingest, source-correct deep-page answers, recoverability and bounded measured query latency** all pass together.

## 6. Decisions needing experiments, not guesses

1. Compare PyMuPDF+Tesseract to selective local Docling layouts/tables/OCR on a frozen varied page set.
2. Compare E5-small vs larger multilingual embedder on same labels/hardware; any new vector dimension requires a **new versioned index** and deliberate migration.
3. Compare section-aware chunk sizes and parent expansion using evidence recall and answer support.
4. Compare dense-only, hybrid RRF/DBSF, and reranked hybrid search with per-query timing.
5. Establish hardware class, query concurrency, local LLM model/context budget and storage limits before promising milliseconds or 5,000-page OCR turnaround.

**Public references:** [Qdrant Hybrid/Multi-Stage Queries](https://qdrant.tech/documentation/hybrid-queries/), [multilingual E5-small model card](https://huggingface.co/intfloat/multilingual-e5-small), [Docling Pipeline Options](https://docling-project.github.io/docling/reference/pipeline_options/), [Docling Advanced Options](https://docling-project.github.io/docling/usage/advanced_options/).

**On ChatGPT internals:** The assistant does not have access to OpenAI's proprietary ingestion/embedding/retrieval implementation and cannot claim to reproduce it. The proposed multi-stage architecture is based on publicly described RAG/search patterns.

**Design only — no engine, SQL, Qdrant, KB or document mutations in this commit.**
