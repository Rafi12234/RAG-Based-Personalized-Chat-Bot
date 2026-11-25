# Phase 4.3C — Numerical vector verification and revocable v2 candidates

**Scope:** Real Phase 4.3C engineering implementation in an isolated test-only
path. This is **not** a production activation of v2 retrieval. The existing
v1 production Qdrant collection and MySQL document READY flow remain unchanged.

## 1. Independent stored-vector proof

\`worker/hierarchy_vector_fidelity.py\` performs a bounded, read-only Qdrant
scan of **every** expected v2 point (up to 50,000). The scan uses strict
organization/KB/document/version authorization scope derived by the caller
from MySQL, and a fresh MySQL processing-lease callback on every operation.

It independently validates tenant, document, version, job, attempt token,
index generation, STAGING state, source page, section, parent, locator, text
and embedding-input SHA-256 fingerprints against the encrypted ledger.
It then **recomputes every stored 384D multilingual-E5 embedding** using the
real model supplied by the operator and compares vector direction with cosine
similarity at least 0.999. Numerical dimension, finiteness and unit norm
are checked. The model runs in batches of at most 32. No source text, token
or vectors are logged or returned in an audit report.

Full-book recomputation can be slow; it is a **one-time verification gate,
not an online question-answering step**. A passing report is non-transactional
and only certifies what was observed during the scan. It does not by itself
authorize publication or ensure the model artifact is immutable on disk.
A model-artifact checksum/pinning and a transactionally safe cutover remain
future work.

## 2. Additive MySQL generation registry

The additive migration \`20261125000000_shadow_index_generation_registry\`
creates exactly two new tables:

- \`ShadowIndexCandidate\`: immutable one-time metadata for a previously
  verified document-version/generation; includes exact tenant/KB/job scope,
  source and ledger hashes, passage count and verification timestamp.
- \`ShadowIndexSelection\`: a revocable, revision-checked pointer to one
  candidate for a document version. Its \`queryEnabled\` field is always
  **FALSE** in Phase 4.3C, enforced by a MySQL CHECK constraint. The selection
  revision is likewise constrained to be nonnegative. No reader uses this table yet.

\`worker/hierarchy_generation_registry.py\` provides isolated functions to
register a candidate after **both** the prior text/structure audit and new
independent numerical vector audit; select it with a transactional
compare-and-swap revision and an active MySQL processing token/lease; or
revoke the pointer with an exact digest + revision check. Every query matches
the real MySQL tenant, KB, document, version and job relationship; protects
the three legacy QA KBs; and fails closed on stale claims and partial failure.
No Qdrant writes or deletes, no alias switch and no DocumentVersion READY
mutation occur. Revocation needs no expired worker lease, making it usable
for operator emergency rollback in a future workflow.

This library is **off by default**, cannot run when NODE_ENV=production,
requires the explicit test-only environment flag
\`DOCMIND_ENABLE_SHADOW_REGISTRY=YES_FOR_TESTING\`, and is not invoked by the
normal worker or public API. Do not enable it on real or protected user data.

## 3. Test coverage

GitHub CI validates the additive migration against a clean throwaway MySQL
service and runs offline fake-Qdrant/fake-MySQL tests for:

- Correct recomputed vectors, altered numerical vectors, NaN/wrong dimensions
  and norms, corrupted source text and foreign processing attempts.
- A synthetic 5,000-page numeric verification with bounded embedding batches.
- Revoked leases, wrong manifest or generation, missing/extra points.
- Immutable candidate registration, relational tenant/job/lease SQL fencing,
  stale claim denial, CAS revision races and emergency revocation.
- Query-enabled flags always staying false; protected legacy QA KBs refused.

**No real MySQL/Qdrant/RustFS documents or data were changed during this work.**

## 4. Next production-relevant work

1. Add model artifact SHA/revision pinning and verify stable numerical results
   on your actual hardware, plus full source/index checksum and concurrent
   mutation handling.
2. Implement a dedicated v2 retrieval **reader** with MySQL-backed
   authorization, generation filtering and the same strict READY/tenant
   eligibility as r6; build a transactionally safe enabling/revocation
   protocol before any v2 user traffic.
3. Phase 4.4: hybrid dense + exact lexical retrieval, chapter/section
   navigation, multilingual reranking, evidence expansion and grounded
   citation verification.
4. Validate actual 2,000- and 5,000-page OCR/layout books under concurrent
   load; measure retrieval recall and p95/p99 answer latency.

A passing CI test alone does **not** establish 5,000-page production
performance, quality or full recovery safety.
