# Phase 3 Answer Reliability Fix — 2026-11-04

## Scope and evidence

Current feature branch: `feature/phase3-secure-retrieval`.
All work is additive or code-only. **Do not reindex, redispatch or reupload** the established pilot Knowledge Bases. Do not merge to main without explicit approval.

Historical live acceptance: **25/28**; three Bangla/mixed-language questions returned HTTP 200 and `insufficient_evidence`. Separate read-only retrieval diagnostics showed all three correct source facts available in three verified READY documents. That supports an answer-path failure, not a Qdrant retrieval miss.

A full-stack regression check had been missing: earlier unit tests passed with clean synthetic excerpts. An added HTTP test reproduced a concrete parser error when an evidence excerpt contained literal escaped newlines (`\\nRustFS`): the extractor produced `nRustFS`. The parser now normalizes escaped line boundaries within the already authorized, bounded evidence span before extraction. This does not establish that the same formatting defect caused every historical live abstention; local runtime confirmation is still needed.

## Code changes

- `backend/src/services/groundedAnswer.ts`: structured, bounded source extraction normalizes actual/escaped line breaks; keeps conflict and unrelated-source abstention. Optional internal resolution trace identifies whether an answer used verified named evidence, another direct extractor, or a model path.
- `backend/src/lib/answerRuntime.ts`: stable public `ANSWER_ENGINE_VERSION` constant for deployment/runtime parity.
- `backend/src/routes/retrieval.ts`: authenticated `/answer` response includes `engineVersion` on success; optional allowlisted `diagnostic` only when `NODE_ENV=development` and `DOCMIND_ANSWER_DIAGNOSTICS=1`. No prompts, source excerpts, document text, secrets or raw errors are sent in this diagnostic.
- `backend/src/app.ts`: authenticated, read-only `GET /api/v1/answer-engine` returns the safe version marker, allowing frontend parity checks **before** any question is sent.
- `backend/src/server.ts`: prints backend answer engine version and PID on startup, so multiple/stale Windows processes can be identified.
- `backend/tests/answerApiSecurity.test.ts`: HTTP tests execute real authenticated/verified routing with three ranked, READY source records, the three multilingual questions, and a provider that deliberately throws if invoked. Also checks citations, version, revocation and diagnostic visibility.
- `frontend/src/components/AnswerPanel.tsx`: usable question form, citations, abstention/error states, abort-on-unmount, KB scoping, engine version and development-only reason display.
- `frontend/src/App.tsx` and `frontend/src/App.css`: Q&A integrated into the existing authenticated Knowledge Base page; no DevTools necessary.

## Normal user workflow (no browser Console)

1. In a Windows CMD in repository root, confirm branch and pull the latest feature code with `git pull --ff-only origin feature/phase3-secure-retrieval`.
2. Stop the existing backend server. Confirm that any other old Node process listening on port 4000 is stopped before launching a new one. Do not kill unrelated Windows processes.
3. In `backend`, run `set NODE_ENV=development`, `set DOCMIND_ANSWER_DIAGNOSTICS=1` (only for local diagnosis), then `npm run dev`.
4. Backend CMD must print `DocMind answer engine phase3.10-grounded-2026-11-04-r1 (pid=...)`. If this line is missing, do **not** trust a subsequent test as validating the new code.
5. Start/keep running the existing local Python retrieval API, local Ollama, Docker infrastructure and the frontend (`cd frontend && npm run dev`). Never rerun ingestion, indexing or Docker volume resets.
6. Open `http://localhost:5173`, sign in, select the separate Phase 3.5/3.6 QA Knowledge Base and use **Ask your knowledge base**.
7. Before asking, the question panel must show `Answer backend connected · current engine verified`. It performs a read-only authenticated `GET /api/v1/answer-engine` preflight; if an older server is running, it shows a restart warning and disables the ask button. After answering, the card also shows `Answer engine: phase3.10-grounded-2026-11-04-r1`. In opted-in development mode it reports a safe resolution code.

Recommended first smoke questions:
- `ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?` -> sourced **Qdrant**.
- `আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?` -> sourced **RustFS**.
- `DocMind-AI source documents কোন object store-এ থাকে?` -> sourced **RustFS**.

The number in `[S1]`, `[S2]` or `[S3]` reflects actual source ranking. Verify the **document title and factual value**, not a fixed source position.

## If any live question still abstains

**Do not patch another regex blindly.** Observe these fields in the answer card:
- `engineVersion` missing: wrong/outdated running backend; identify the listener on port 4000, stop it and restart the correct branch.
- `diagnostic: named_infrastructure` + wrong result: extraction or citation correctness bug; capture a sanitized, synthetic reproduction for a regression test.
- `diagnostic: named_fact_missing_or_conflicting`: the updated branch ran but found no unambiguous named fact in the exact **verified 800-character excerpts passed to /answer**. Compare the answer route's verified source selection against the retrieval route at the same topK; test actual structural formatting without printing private excerpts.
- `diagnostic: model_path`: question did not match the restricted named infrastructure intent; diagnose intent classification and verify the model response/contract.
- `Answer service unavailable` / `Database temporarily unavailable`: separate Ollama or MySQL dependency error, not a factual answer. Use the established sanitized diagnostics; never return invented citations.

Do not claim success on the user machine until a post-pull frontend run verifies the engine marker, three previously failing facts and original no-evidence/conflict safety cases.

## Automated acceptance already performed

GitHub Actions CI runs backend typecheck/build, API integration tests against isolated CI MySQL, the entire offline Phase 3 security/grounding/transport suite, frontend lint/build and Python syntax checks. The new HTTP test failed on the escaped-newline bug and passed after the normalization fix.

**Limitations:** GitHub cannot connect to the user's private `localhost:4000`, so CI success cannot prove that Windows is running the correct process or that the specific live KB has no additional source-formatting differences. The new user-facing Q&A is a Phase 3 pilot, not yet a fully featured chat-history product. Phase 4 may add conversation sessions and source navigation, and production hardening remains later work.

## Addendum: Named infrastructure resolution r2 (November 4, 2026)

A live screenshot on the verified r1 answer engine showed a Bangla vector-database question returning insufficient evidence and the coarse code `named_fact_missing_or_conflicting`. That rules out an outdated backend for the screenshot. It does not yet prove whether the exact verified /answer excerpts failed pattern matching or contained conflicting names.

- The shared narrow extractor now lives in `backend/src/lib/namedInfrastructure.ts` and reads only verified, source-scoped excerpts.
- It normalizes actual and literal escaped line breaks, requires a supported name and relationship in the same source sentence, and never hardcodes Qdrant, RustFS, or knowledge-base identifiers.
- It distinguishes missing evidence (`named_fact_missing`) from contradictory names (`named_fact_conflict`); conflict still abstains.
- Regression tests include the original Phase 3 QA corpus files, literal line breaks, changed source order, negation, and conflicts.
- The answer engine marker is `phase3.10-grounded-2026-11-04-r2` and the frontend checks it before permitting questions.
- The answer result card identifies the selected knowledge base. Local opt-in diagnostics additionally display verified passage count and a sanitized resolution code.

**Status:** CI verified these changes. The original live 25/28 score remains the last confirmed end-to-end acceptance result until the user pulls r2 and tests the three failures through the normal frontend. Do not claim 28/28 prematurely.

**Normal verification:** pull only `feature/phase3-secure-retrieval`; restart Node and React from the updated directory; leave existing Python retrieval and Docker services running; select the QA knowledge base containing `phase3_3d_vectors.txt` and `phase3_3d_storage.txt`; ask via the frontend, not browser DevTools. Check the selected KB, engine version, citation document title, and any safe diagnostics.
