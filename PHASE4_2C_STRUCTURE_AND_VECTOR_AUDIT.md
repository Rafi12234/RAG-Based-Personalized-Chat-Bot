# Phase 4.2C — Structural extraction and read-only large-vector audit

**Status:** Code implemented in feature branch; Phase 4.2/4.3 production scale requirements are still in progress.

## 1. Structured source representation

\`python-worker/worker/document_structure.py\` now extracts:

- PDF: native text blocks (not just one flat text string), exact 1-based page numbers, coordinates, stable source-block IDs and per-page OCR-candidate indicators; supports original page-range extraction up to 5,000 PDF pages.
- DOCX: ordered headings/paragraphs/list-style blocks, ancestor chapter/section paths, table row IDs and rows with column-header context. No false PDF-like page-number citations for an unrendered DOCX.
- Legacy chunker compatibility: read-only page-to-segment and DOCX-to-segment adapters. Opt-in large-book DOCX ingestion uses section-context segments to make closely worded policies from different chapters distinguishable.

**Limitations:** PDF block geometry is currently computed from **native text**; OCR geometry and reliable complex table-cell detection are **not yet implemented**. Sections and block coordinates are **not yet persisted in Qdrant v1**, and DOCX still loads through python-docx, which is not demonstrated to be memory-bounded for huge files. No changes to the normal r6 retrieval/generation behavior.

## 2. Large-version Qdrant reconciliation audit (read-only)

\`python-worker/worker/large_vector_audit.py\` validates up to **50,000** exact org/KB/document/version scoped points with paginated, metadata-only reads. It checks canonical point IDs, duplicate IDs, token consistency, staged/ready state, index fields, exact counts before and after scanning, and optionally compares the observed IDs to a supplied expected manifest. It does **not** return text, embeddings or processing tokens, and never changes any datastore.

The optional detailed operator diagnostic is:

\`\`\`cmd
cd python-worker
python -m worker.publication_audit_probe JOB_ID --detailed
\`\`\`

Do **not** run this on protected knowledge bases or real users' jobs during development. The report is a non-transactional snapshot; even an exact ID match **does not authorize recovery**, because semantic/vector/content integrity, lease exclusivity and source generation must also be proven. Automatic replay/cleanup remains disabled.

Existing \`guarded_recovery.py\` retains the narrower, manually confirmed disposable-test-only recovery with its 2,048-point cap. This commit does **not** raise that destructive repair cap or provide any new delete path.

## 3. Tests and safeguards

GitHub CI adds offline PDF/DOCX structure and header-aware retrieval-context tests, plus synthetic 50,000-vector scan tests for mixed processing attempts, foreign scopes, duplicate/invalid IDs, changed counts and mismatched manifests. No test alters existing MySQL/RustFS/Qdrant data.

## 4. Work still necessary before 5,000-page production readiness

1. A persistent block/section/geometry schema and versioned index manifest, with complete structural/OCR coverage metrics.
2. Non-destructive, lease-fenced safe recovery of staged/ready vectors; an exact content/vector hash check and transactionally validated cutover or rollback (without touching protected KBs).
3. Large multipart/resumable file uploads; resource quotas, malware/content validation and explicit storage retention.
4. Hybrid dense + lexical retrieval, hierarchical navigation, multilingual reranking, claim-grounded answers, and real 5,000-page benchmarks.
5. Measured 95th-percentile query latency and proof of accuracy for facts buried deep in books.

**No SQL migrations, Qdrant updates, protected KB writes or background ingestion jobs were performed by this commit.**
