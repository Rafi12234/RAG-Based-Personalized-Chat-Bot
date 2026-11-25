# Phase 4.3B — Durable encrypted hierarchical source ledgers

**Scope:** Phase 4.3B engineering foundation. This is a separate, opt-in library and **is not wired into the live query or ingestion dispatcher**. No main-branch production feature toggle is enabled. The v1 production index remains unchanged.

## What was built

1. **Immutable hierarchical locator ledger** (\`worker/hierarchy_manifest_store.py\`). Streaming \`HierarchyLedgerBuilder\` records each passage's exact original PDF page or DOCX section/table locator, parent/section IDs, neighbor, source block ID, character offsets and two SHA-256 fingerprints: original text and section-context embedding input. The ledger does **not** hold raw document text. Up to 50,000 passages are supported under the existing pilot constraint; the builder validates ordering, IDs, source relationship counts, section/page types, page coverage via the sealed manifest, and recomputes the v2 source manifest digest. It fails closed on missing/extra/modified provenance.
2. **Durable encrypted RustFS/S3 storage** (\`EncryptedHierarchyManifestStore\`). One immutable object per exact MySQL-derived organization/KB/document/version/job and v2 index generation. AES-256-GCM encryption with a fresh nonce and authenticated scope; no plaintext fallback, no logs of source locators, no object listing/deletion. Uses \`PutObject(IfNoneMatch="*")\` for atomic create-only semantics and verifies the encrypted object after writing. Storage providers lacking conditional creation must fail rather than overwrite. Every remote step is fenced by a caller-supplied fresh MySQL processing-lease callback. A second authorized worker can load and verify the same generation without repeating the entire hierarchy traversal.
3. **Read-only shadow reconciliation** (\`worker/hierarchy_shadow_audit.py\`). Given a separately authenticated ledger, MySQL-derived identity and a live lease callback, it scans only \`docmind_documents_hierarchy_e5_384_shadow_v2\` and checks the exact point UUID set, tenant/KB/document/version/job, processing token, generation, STAGING visibility, model/schema, page, parent, section, source locator, offsets, source text fingerprints and derived section-context embedding-input fingerprints. It refuses missing, extra, duplicated, conflicting or changed points and checks counts before/after. It requests **text but not vectors**, and does not print or return raw excerpts. The scan is *not a transactional snapshot*.
4. **Offline tests:** in-memory RustFS, staged shadow Qdrant and synthetic original pages, including 5,000-page encrypted restore, tamper, wrong key, foreign tenant, incomplete manifest, lost lease, bad token/source, missing/extra points and non-publication assertions.

## Security, performance and limitations

- **No automatic index activation or recovery.** Neither store nor auditor can make a vector READY, write/deleting staged points, swap an alias, modify MySQL or mutate existing production records. Even a successful shadow audit is insufficient for cutover: raw vector values, actual inference correctness, concurrent mutation, relational READY, and index-generation switching remain unproven.
- The ledger binds **fingerprints**, not the raw source bytes. It requires a trusted, sealed v2 manifest produced from the original source and explicit processing authorization. A compromised encryption key or worker could forge a new authenticated manifest. Deploy encryption keys with a secret manager/KMS and rotate them under a separate key-versioning plan.
- **50,000 passages** is the current memory/processing bound, not guaranteed sufficient for every 5,000-page book. The ledger is held in memory in this batch and the object size is capped at 48 MiB plaintext.
- Object-store reads require a backend that supports \`get_object\` and conditional \`put_object\` semantics compatible with the official S3 API. This code is exercised only against offline fakes, **not production RustFS**. Real object-store compatibility and concurrency load tests remain necessary.
- Original source text still exists in the shadow Qdrant payload; this ledger does not replace encryption at rest, tenant-safe search filters, source-level prompt injection defenses or retention management.

## Next work — Phase 4.3C / 4.4

- Integrate the encrypted ledger into a **new, isolated test-only v2 ingestion orchestration** (no automatic promotion). Persist and verify exact vector-generation manifest alongside checkpointed blocks.
- Design and implement a revocable generation activation record and a safe non-destructive rollback with a verified MySQL/Qdrant cutover protocol; do not mutate the v1 index.
- Implement hybrid dense+sparse retrieval, chapter navigation/global fallback, reranking and adaptive evidence budgets. Then measure actual 2,000–5,000-page retrieval recall, citation correctness and query p95.
- Build resource quotas, checkpoint retention/cleanup, and key-versioned encrypted persistence. Do not mark production-ready until a real large-document acceptance milestone passes.

**DO NOT** enable these experimental components against an existing protected QA KB. This commit adds no database migrations and changes no user data.
