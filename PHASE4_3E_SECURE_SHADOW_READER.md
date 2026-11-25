# Phase 4.3E — Pinned provenance and permission-fenced shadow search

DocMind AI's goal is source-correct answers over private, multilingual and
very large documents. This milestone is a **test-only single-document dense
retrieval reader**, not an activated v2 engine or a new public endpoint.

- Add nullable modelPinSha256, attemptTokenSha256, verifiedAttempt columns
  to ShadowIndexCandidate. All are NULL on existing registrations, which the
  new reader refuses. A CHECK requires all-or-none and a positive attempt.
  No change to the existing queryEnabled=FALSE database constraint.
- register_pinned_candidate runs both complete source and pinned numerical
  vector audits, then uses an INSERT SELECT with current lease, exact scope,
  latest version and single-job fences. Immutable insert, no backfill,
  update, selection, publication or job completion.
- search_verified_shadow checks an actual active AuthSession, active User,
  organization membership, latest READY document version, completed same
  attempt, nonrevoked selected generation, revision, exact pin and source
  ledger hashes. Three independent short-lived SQL reads surround model
  inference, Qdrant query and the final return. Session removal, membership
  removal, archive, stale version, retry, or selection changes abort.
- Search uses ONLY the isolated shadow collection, exact scope, generation,
  job, token, STAGING state and model/schema filters. Each candidate is
  independently checked against the authenticated ledger, including original
  passage text hash and source location. Any malformed, foreign, duplicate
  or modified result aborts the entire response. No writes or fallbacks.
- Only the explicitly enabled development/test library path may inspect
  non-queryable staged data for benchmarking. It cannot enable user traffic.
  Protected legacy QA KBs are refused. Use a new disposable test database,
  documents and independently authorized server session. Raw session tokens,
  processing tokens and source text must never be logged.

## Remaining gates / design choices

This reader accepts a single trusted authenticated ledger, original processing
token and verified local model from an operator; no user input may supply
those objects. The original token is not stored in SQL (only its SHA-256).
A future service must design a secure token-independent artifact locator and
authenticated ledger loading path after the processing lease ends. This
implementation restricts each version to one ingestion job; it does not
orchestrate a live end-to-end worker.

Relational checks are fresh observations, not an atomic MySQL/Qdrant snapshot.
There remains a race after the final check, and Qdrant search ranking/vector
values could change after the full audit. Source payload integrity is checked
on each hit, but vector values are not recomputed on every online search.
Production activation, revision-fenced publication, immutable index identity
across multiple generations, model/runtime parity and real workload tests
are not yet complete. The v1 pipeline remains unchanged. Next: benchmark
the isolated reader with real E5 and disposable documents, complete safe
generation orchestration, then hybrid retrieval and reranking.
