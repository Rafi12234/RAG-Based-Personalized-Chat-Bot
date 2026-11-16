# Phase 4.2A — Large-book extraction and checkpoint foundation

**Status:** Implemented behind a test-only opt-in in the existing pilot worker.
This is **not** final production-scale RAG, not a new fast-preview query API
and not permission to reprocess any protected knowledge base.

## What works in this batch

- The existing PDF OCR iterator now supports original-numbered page ranges,
  allowing bounded shards without silently renumbering citations.
- Streaming page methods (native text, OCR, blank) are reported without logging
  or returning raw source text. Tesseract language data is resolved once per
  PDF range, not on every OCR page.
- A new source-SHA256 + document-version-scoped private local checkpoint store
  persists complete page shards (32 pages by default). It rejects corrupt,
  missing, duplicated, reordered and source-mismatched pages. A restarted
  extraction can reuse already validated shards.
- OCR budgets are accounted **across cached and newly extracted pages**.
  Each full PDF page is counted, including genuinely blank pages. Per-shard
  work does not hold a whole book in RAM.
- Existing pilot behavior remains limited to 10 OCR pages by default.
  Optional test-only large-book extraction can explicitly process up to 5,000
  OCR pages subject to the original 250 MiB upload limit and 50,000-chunk limit.
- DOCX table rows include their column names on data rows when the first row
  is a plausible header. This improves the retrievability of table values.

## Boundaries and security

The large-book pilot still downloads an immutable source to a temporary file
and ultimately stages and publishes vectors using the existing MySQL lease +
Qdrant READY version guard. Only a new, isolated test job with no preexisting
production points is eligible. Three legacy protected KB IDs are explicitly
refused by the pilot worker.

The checkpoint files contain **the private extracted text in plaintext**.
Their root must be an operator-owned private directory (POSIX 0700); use disk
encryption and strict Windows filesystem ACLs where applicable. The code
refuses symlinked roots/version directories/known checkpoint files. It creates
only version-scoped files under the explicitly configured root and never
clears existing user/benchmark data. Do not enable for production customers.
Manage local retention until encrypted RustFS checkpointing and cleanup policy
are implemented.

**Important:** The extraction resume store does *not* make the entire indexing
transaction crash-resumable. The current production staging preflight rejects
orphaned vectors, so a crash after vector staging still requires a verified
recovery procedure. Phase 4.2B must persist page/block manifests and integrate
durable, versioned checkpoint recovery before declaring full 5,000-page
ingestion dependable.

## Opt-in for disposable test documents only

Disabled by default. Example environment variables for an already authorized,
new, isolated disposable test KB:

- \`DOCMIND_ENABLE_PILOT_INGESTION=YES_FOR_TESTING\`
- \`DOCMIND_ENABLE_LARGE_BOOK_PILOT=YES_FOR_TESTING\`
- \`DOCMIND_LARGE_BOOK_CACHE_DIR=<absolute-path-to-private-folder>\`

The job runner must still be explicitly invoked with a newly queued test job
and an appropriate \`max_ocr_pages\` value (up to 5000). Do **not** enable this
flag with the general multi-KB dispatcher or existing protected QA data.
A full 5,000-page scanned PDF on a CPU may require hours of OCR processing.
There is no promised few-second ingestion performance.

## Automated coverage

\`worker.test_large_document_shards\` checks exact 5,000-page shard coverage,
resume/no-repeat, source identity, checksum tampering, page order, blank pages,
OCR budget across cached pages, symlink/private-directory controls.

\`worker.test_docx_table_rows\` checks header relevance and bounded, lazy row
formatting. \`worker.test_live_ingestion_worker\` checks opt-in safety and worker
lease/UUID/READY preflight safeguards. These run in GitHub CI without invoking
any local services or changing user data.

## Remaining Phase 4.2B/4.3 work

1. Multipart/resumable large uploads with per-tenant quotas and malware scanning.
2. Structured layout/section/page/table geometry; selective scanned/table OCR
   and extraction confidence/quality visibility.
3. Encrypted durable object-store checkpoints and idempotent staged-vector
   recovery, with completed-shard manifest and READY cutover.
4. Accurate chapter-aware chunking, source position maps and generation-aware
   indexing, without touching the existing r6 collection.
5. Real 500/2,000/5,000-page isolated fixtures and hardware-specific measurements.

This work is a functioning extraction foundation, not a production-ready
5,000-page search implementation. No protected KB, SQL, Qdrant or RustFS
content is modified by this commit.
