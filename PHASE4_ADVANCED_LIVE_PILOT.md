# Advanced RAG — connected local pilot

The normal upload and Ask your knowledge base workflow now has an opt-in
advanced path for ONE newly created disposable KB. This is not an unrestricted
production cutover; the existing SQL queryEnabled=FALSE CHECK stays intact.

## Connected lifecycle

1. Existing guarded Celery worker obtains a fresh processing lease, downloads
   original source and builds source-aware PDF (native or selective OCR),
   DOCX or TXT passages with the pinned E5 tokenizer.
2. A version-level encrypted S3 create-only reservation prevents another
   advanced attempt from reusing passage IDs. Build/stage only in the separate
   shadow collection. Persist a create-only authenticated encrypted ledger and
   original attempt token; independently verify every source locator/hash and
   recomputed vector; register pinned candidate and CAS-select revision 1.
3. Existing v1 indexing/publication still must pass before MySQL READY. No
   advanced result is visible for PROCESSING, FAILED, revoked or stale versions.
4. Existing authenticated Express /answer and /retrieve routes select the
   advanced engine only for the operator-selected KB. Signed loopback calls
   carry server-derived session/scope; Python checks current session, membership
   and all latest READY versions, authenticates the encrypted ledger, checks
   selected revisions, and uses the Phase 4.4–4.7 pipeline. Reranker and NLI
   must be configured. Ollama is local, digest checked and raw prompt token
   count checked; any mismatch fails closed.
5. Final Python and Express authorization and full selected document proofs
   precede returning answer and server-generated citation IDs, original quotes
   and source locators. The browser displays the actual engine marker.
   Rank is not a cosine score: advanced citations and sources have score=null.
6. One memory-only corpus cache per process, bound to session and exact ready
   selection, reused for at most 120 seconds, evicted on next mismatched/expired
   request or error. No shared/global cross-session corpus and no disk cache.

## Safe setup — do not change protected or existing KBs

Use only a new disposable KB in an isolated local test environment. Migrate
4.3C/E additive SQL on the isolated test database; do not reset existing data.
Before uploads, obtain trusted local ONNX files (token-level 384D E5,
scalar relevance multilingual reranker, 3-label multilingual NLI) and their
matching tokenizer JSONs. Model artifacts are not included or downloaded by
this repo. Pair model label order must match the actual export. Use the exact
installed Ollama model digest and corresponding raw tokenizer; the model
preflight fails on any observed prompt token count mismatch, including
uncalibrated special-token offsets. The example JSON is NOT working credentials
or a validated model recommendation. Save a filled copy of
advanced-models.example.json outside the repo with absolute local paths.

Add the following to private backend/.env and python-worker/.env (same KB ID):
 
    NODE_ENV=development
    DOCMIND_ENABLE_ADVANCED_LIVE=YES_FOR_TESTING
    DOCMIND_ADVANCED_LIVE_KB_ID=<NEW_DISPOSABLE_KB_ID>

Python only:

    DOCMIND_ENABLE_SHADOW_READER=YES_FOR_TESTING
    DOCMIND_ENABLE_SHADOW_REGISTRY=YES_FOR_TESTING
    DOCMIND_ENABLE_PINNED_SHADOW_AUDIT=YES_FOR_TESTING
    DOCMIND_ADVANCED_LIVE_CONFIG=<ABSOLUTE_PRIVATE_CONFIG_PATH>
    DOCMIND_ADVANCED_BUNDLE_KEY_B64=<32_RANDOM_BYTES_BASE64>

Keep bundle key backed up securely, never commit it. Existing Node/Python
DOCMIND_INTERNAL_RETRIEVAL_SECRET must match (>=32 chars).
Set optional DOCMIND_PILOT_OCR_LANGUAGE=eng+ben only with Tesseract language
data installed; default 10 OCR pages. Larger OCR budget uses the existing
explicit large-book mode and private checkpoint directory (or encrypted
checkpoint config), not a guarantee of speed or OCR quality.
Run a read-only local preflight from python-worker:

    .venv\Scripts\python.exe -m worker.advanced_live_preflight

Do not start ingestion before preflight succeeds. Start Docker, backend,
frontend, FastAPI and dedicated Celery worker as usual; use a scoped
automatic_dispatcher for this NEW KB only. Upload new PDF/DOCX/TXT and wait
for READY. Ask a question in the UI and inspect engineVersion:
phase4.8-advanced-local-v1 plus exact source locator/quote.
If any latest READY document in that KB lacks a valid selected generation,
the entire advanced request fails with 503, not a partial answer or hidden
v1 fallback. Stop and inspect, do not blindly reindex. Disable flag on both
processes and restart to return to v1. Existing QA data is untouched.

## Remaining limitations

CI covers actual route contract, HMAC replay rejection, encryption, isolated
MySQL discovery, staged lifecycle and synthetic model/PDF tests, not the
operator's real artifacts and live installed Ollama. Exact raw tokenizer
calibration may require a different supported model/export; preflight blocks
mismatch. No automatic recovery of partial v2 stage; reservation intentionally
prevents retry overwrite. Archive/reupload into new disposable version after
manual audit; no deletion script. No atomic multi-store commit; last-check
race remains. Lexical cache is rebuilt on expiry/session change; 50k passages,
8 documents, 64 MiB evidence limits remain. Current browser path supports
one question, not automatic subquery decomposition. OCR, table completeness,
real 5k-page dense/LLM performance and independent acceptance are still open.
Production publication and general Phase 4 acceptance must not be claimed
until actual end-to-end validation succeeds.
