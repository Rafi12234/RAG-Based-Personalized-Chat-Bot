# Phase 4.2B — Encrypted, cross-worker large-PDF extraction checkpoints

**Status:** Implemented for the explicitly gated disposable pilot path. **Not** production-ready ingestion, not a resumable upload feature, and not a completed large-book retrieval upgrade.

## What this code does

- Adds AES-256-GCM **client-side encryption** for complete PDF extraction shards stored in private RustFS/S3 objects. The encryption key stays in the operator environment, never in Git or in object metadata.
- Binds every immutable checkpoint to MySQL-derived **organization, knowledge base, document and version IDs**, the downloaded original file SHA-256, total page count, page range and exact extraction profile (parser/OCR language/DPI).
- Uses authenticated encryption with random nonces and strict source/page-level checks. Refuses malformed, oversized, duplicated, reordered, tampered, cross-tenant and mismatched checkpoints rather than silently indexing them.
- Uses S3 \`PutObject(IfNoneMatch="*")\` to prevent overwriting existing checkpoints. If a storage provider doesn't support conditional creation, writes fail closed; there is **no** unsafe fallback.
- Reuses existing page-range processing and aggregated OCR budget. Another authorized test worker with the same key, original source, version and extraction profile can reuse previously completed shards and avoid repeating extraction/OCR. The encrypted mode now completes and validates **all page checkpoints before any vector staging**, then indexes from a strict cached-only second pass. A missing shard stops indexing rather than silently re-extracting during publication.
- Keeps the local private-directory checkpoint option intact, disabled remote mode by default. Does not change vector/answer behavior for ordinary users, and adds no SQL migrations.

## Safe opt-in (only for a NEW disposable test KB/job)

This mode requires **both** existing pilot flags and a separate remote-checkpoint flag. It **must not** be used with protected QA or real customer data:

\`\`\`text
DOCMIND_ENABLE_PILOT_INGESTION=YES_FOR_TESTING
DOCMIND_ENABLE_LARGE_BOOK_PILOT=YES_FOR_TESTING
DOCMIND_ENABLE_ENCRYPTED_CHECKPOINTS=YES_FOR_TESTING
DOCMIND_CHECKPOINT_AES256_KEY_B64=<base64-encoded 32 cryptographically random bytes>
\`\`\`

The worker must already be authorized for that isolated document, pass the existing Qdrant clean-version preflight and hold its MySQL processing lease. Remote checkpoint reads/writes are fenced by the same fresh MySQL processing-lease guard as vector staging; any revoked lease aborts rather than continuing to persist pages. It uses the existing private RustFS/S3 client and bucket, with an internal \`docmind-private-checkpoints/v2/\` prefix. Server-side network access to the bucket must remain tightly scoped and encrypted in transit when not loopback.

Create a securely generated 256-bit key using a secrets manager or OS cryptography facility, and never store the raw/base64 key in shell history, source control, CI output or chat. **A lost/rotated key makes existing encrypted checkpoints unusable** until an explicitly designed migration or safe regeneration. Don't enable automatic GC until retention, version and legal-hold policies are implemented. There is no current key-versioning/KMS envelope encryption system.

The normal Celery pilot task currently passes a 10-page OCR budget by default. To process more OCR pages, that budget must be explicitly configured in a controlled isolated test runner; the remote flag alone does not lift all resource limits. Other current constraints remain: 250 MiB single upload, <=50,000 chunks, local CPU/Ollama capacity and pilot-only Qdrant staging.

## Recovery boundary

This batch makes **PDF page extraction** portable and resumable between workers. It **does not** make the whole SQL + Qdrant indexing transaction recoverable after a crash. In particular, the existing preflight refuses any old staged/ready vectors from an interrupted attempt and requires an authorized manual recovery audit. This is deliberate; silently adopting stale vectors or resetting a production collection would risk information leaks or corruption.

**Next engineering gate:** fully scoped non-destructive reconciliation of interrupted staged vectors and immutable per-stage manifests; structured PDF layout/tables with extraction quality; upload chunking/quotas; then hierarchical indexes, hybrid retrieval, reranking and large-book latency benchmarks.

## Test coverage

CI runs offline tests against an in-memory S3 stand-in, including a 5,000-page synthetic two-worker resume, encryption/non-plaintext storage, tamper and replay prevention, version/source/tenant/language scoping, wrong keys, missing vs access-denied differentiation, conditional-write races, OCR budgets and an actual PyMuPDF worker integration test. **No test calls real RustFS, changes MySQL or indexes vectors.**

No protected knowledge bases, SQL records, Qdrant points, user documents or local Docker volumes are modified by this commit.
