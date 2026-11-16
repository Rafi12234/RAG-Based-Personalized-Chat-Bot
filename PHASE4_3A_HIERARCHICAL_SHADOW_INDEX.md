# Phase 4.3A — Hierarchical passage indexing and isolated Qdrant shadow generation

**Status:** Implemented as an independent, testable v2 indexing path. Not in production retrieval, not automatically invoked by existing ingestion, and not an index cutover.

## Implemented

**Streaming source hierarchy** — \`python-worker/worker/hierarchical_index.py\`

- Builds deterministic passage/parent IDs from immutable source-block IDs and exact source offsets. Keeps document version, original PDF page and block box, DOCX section ancestors, table parent, source locator, previous neighbor in the same parent, and the original passage text.
- Chapter/section headings are carried into the **embedding input** (for example: \`Section: Chapter A > Policy\n...\`). Distinct chapters and table parents remain distinguishable even when their answers have similar wording.
- An exact E5 tokenizer enforces **448 tokens** by default and **950 embedding-context characters** (bounded even for a 200,000-character text block). The processor streams one source block at a time, with bounded overlapping child passages; no 5,000-page LLM prompt or giant chunk is constructed. It does not yet use advanced semantic merging across separate short blocks.
- Incremental immutable manifest records source SHA-256, model, document version, expected/observed PDF pages including blanks, unique source blocks, parents, expected v2 point IDs and a provenance checksum. Missing/unprocessed OCR pages, incomplete 5,000-page coverage, duplicate or out-of-order source blocks and >50,000 passages fail closed. **A valid manifest is a prerequisite for later cutover, not permission to mark READY.**
- Uses \`SourceBlock\` objects from the already added PDF/DOCX structure extractor; OCR-only pages still require an explicit, validated SourceBlock/geometry mapping in a future milestone.

**Isolated 384D multilingual E5 shadow indexing** — \`python-worker/worker/hierarchical_shadow.py\`

- Defines a **distinct** collection \`docmind_documents_hierarchy_e5_384_shadow_v2\`, separate from current production collection \`docmind_documents_e5_384_v1\`. Existing production vectors and r6 answering are unchanged.
- Read-only collection inspection by default; collection creation only when the caller passes \`explicit_create=True\`. Existing incompatible or incomplete collections are refused, never reset or silently repaired.
- Streams E5 embeddings in batches of eight with normalized vectors. Can stage only a complete, version-scoped passage manifest into the shadow index, with MySQL processing-token lease checks before/after operations and exact staged-generation count check. Partial stages never become visible; any existing same-generation points are refused for manual review.
- Shadow payload preserves tenant/KB/document/version/job, source pages, coordinates, section/table parent and block locator, source offsets and text. Every staged point remains \`index_state=staging\`; there is **no** ready promotion, alias switch, auto-delete or user-facing retrieval path.
- Maximum **50,000 passages per document version** remains a current pilot constraint, not a proven adequate bound for every 5,000-page book. Documents exceeding it fail closed rather than index only the first part.

## Tests

\`worker.test_hierarchical_index\` covers 5,000 ordered synthetic page records, missing and OCR-needed pages, deterministic IDs, Bengali/long passages, exact tokenizer budget, section/table ancestry and version mismatch.

\`worker.test_hierarchical_shadow\` checks isolated collection creation, schema/keyword filters, embedding batching, page/parent payload, staging-only visibility, missing embeddings, lease loss, immutable manifest ordering and existing-generation refusal.

**No new live jobs, protected knowledge-base changes, SQL migrations, Qdrant resets, or document uploads were performed.** Only GitHub test runners execute offline mocks/synthetic data.

## Next gate — Phase 4.3B

- Encrypted durable hierarchy manifests and page/block source maps (the v2 manifest is currently in-memory).
- Finalized OCR/page coverage, table-cell geometry and large-file resource quotas.
- Shadow generation verification against a complete source/embedding manifest plus non-destructive recovery.
- An explicitly authorized and reversible v2 cutover protocol. Do not mix v1/v2, do not enable user queries on staged points.
- After that, Phase 4.4 dense + lexical/BM25 hybrid search, navigation and cross-encoder reranking. Measure deep-page evidence recall and p95 latency on real 2,000–5,000-page files.

**No assurance of seconds-level answers from 5,000-page books yet.** The v2 search service and real-load benchmarks still need to be built.
