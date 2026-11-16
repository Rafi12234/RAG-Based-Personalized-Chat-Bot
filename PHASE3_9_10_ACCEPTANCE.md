# DocMind-AI — Combined Phase 3.9 + 3.10 Acceptance

Branch: feature/phase3-secure-retrieval
Scope: local Windows pilot, NOT production certification.
Isolation: Neither final browser probe edits or reindexes the original Phase 2 Knowledge Base.

## Phase 3.9 — Reliability changes

- MariaDB connectionLimit remains 5. connectTimeout is 10,000 ms and acquireTimeout is 20,000 ms. One bounded retry remains limited to idempotent scoped authorization SELECTs. Authorization revocations, writes and unrelated Prisma failures do not bypass validation.
- Ollama stays on approved loopback /api/generate, with strict JSON schema and verified citation checks. The model prompt no longer duplicates excerpts alongside identical server-selected evidence quotes. The pilot uses num_ctx=4096, num_predict=256, keep_alive=10m, and a 90-second timeout.
- Model transport returns sanitized development-only diagnostics separating HTTP 4xx/5xx, timeout, network failure, empty body, oversized response, invalid JSON and invalid envelope. Raw output, private source text and credentials are never logged.
- Evidence-first policy answers remain strictly sourced; arbitrary unknown facts do not turn into guessed citations.
- Longer timeouts and more specific diagnostics mitigate uncertainty but do not prove permanent MySQL or Ollama reliability.

## Phase 3.10 — One offline gate, one live gate

1. Open CMD in repository root on feature/phase3-secure-retrieval.
2. Run: tools\PHASE3_10_VERIFY.cmd
3. This verifies backend typecheck/build; 22 Phase 3 offline retrieval/security/answer test files; frontend lint/build; and Python worker unit-test discovery.
4. The local gate deliberately EXCLUDES backend/tests/api.integration.test.ts because it writes to its connected SQL database. GitHub Actions performs the API integration tests against a separate CI-only MySQL service.
5. Start backend with NODE_ENV=development and DOCMIND_ANSWER_DIAGNOSTICS=1 (development diagnostic headers are opt-in). Keep other services running.
6. Stop other benchmark scripts and answer clients (rate limit: four answer calls per minute).
7. In another CMD at repository root, copy the JavaScript with UTF-8-safe PowerShell (do NOT use `type ... | clip`, which can corrupt Bangla text):

   ```cmd
   powershell -NoProfile -Command "$s = Get-Content -LiteralPath 'tools\PHASE3_10_FINAL_LIVE.js' -Raw -Encoding UTF8; Set-Clipboard -Value $s; Write-Host ('Bangla preserved: ' + [bool]((Get-Clipboard -Raw) -match '[\u0980-\u09FF]'))"
   ```

   Confirm the output is `Bangla preserved: True`.
8. Paste into logged-in local Chrome F12 Console once. It checks 28 Phase 3.5, 3.6 and 3.8 cases with pacing and prints one combined table.
9. Controlled pilot acceptance requires 28/28 PASS, no failed or blocked cases, verified citations, and correct abstentions.

## Final live acceptance evidence — 2026-11-08

- Run location: logged-in local Chrome with original QA workspace membership.
- Script: `tools/PHASE3_10_FINAL_LIVE.js`, copied using UTF-8-safe PowerShell; Bangla preservation confirmed true.
- Runtime: `phase3.10-grounded-2026-11-04-r3`.
- Final console output: **`PHASE 3.9 + 3.10 FINAL LIVE: 28/28 PASS; 0 FAIL; 0 BLOCKED; complete=true.`**
- Controlled case groups: 3.5 (9 PASS), 3.6 (9 PASS), 3.8 (10 PASS).
- Results include verified citations for answered cases and correct insufficiency/conflict abstentions.
- This records the **live acceptance gate**, not independent proof of production readiness or arbitrary document entailment. Keep the offline verification and CI criteria separate.

## Go/no-go requirements

- GitHub Actions backend and frontend jobs pass on the current feature-branch HEAD.
- Windows offline gate reports OFFLINE ACCEPTANCE PASSED.
- One post-change live run reports 28/28 PASS, 0 FAIL, 0 BLOCKED.
- No original Phase 2 pilot KB reindexing/upload, no credentials or source excerpts leaked.
- No migration, data purge or Docker volume reset was required.

## Distinguishing failures

- HTTP 503 with ollama_timeout, ollama_network_error, ollama_http_5xx, ollama_http_4xx or ollama_invalid_envelope is an Ollama dependency/transport problem, NOT factual correctness.
- HTTP 503 with Database temporarily unavailable is a MariaDB/Prisma dependency problem, NOT factual correctness.
- HTTP 200 with insufficient_evidence is abstention and should count as correct ONLY for cases with no answerable fact or conflicting evidence.
- An answer with genuine citation metadata is not automatically semantically entailed; case-specific fact validation is still required.

## Known limitations

The fixed 28-case evaluation is a controlled small corpus, not proof of production reliability or general semantic entailment. The Phase 3.8 policy extractors recognize explicit structures, not arbitrary document reasoning. Live soak/scale testing, secret review, retention/purge, monitoring, upload malware protection, deployment TLS, incident response and recovery require further work before production release.

After QA, use set DOCMIND_ANSWER_DIAGNOSTICS=0 and restart the backend to disable extra development diagnostics.
