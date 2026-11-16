# Local automatic document ingestion (Phase 2 pilot integration)

This is a **development-only** way to stop manually dispatching each upload.
It reuses the existing verified ingestion pipeline, rather than setting document
statuses by hand. When the backend accepts a PDF, DOCX or TXT upload, it stores
the source in RustFS and atomically records the QUEUED document version and job.
The scheduler polls MySQL every 15 seconds and discovers eligible jobs across
knowledge bases. For each KB, it calls the existing guarded scope-specific
dispatcher. A separate Celery live-pilot worker pulls the job from Redis,
extracts text, chunks, embeds, stages and verifies vectors, and updates MySQL
to READY only after successful Qdrant publication.

## Windows setup (one-time per development session)

1. Check out branch \`feature/phase3-secure-retrieval\` and pull updates.
2. Keep Docker MySQL, Redis, Qdrant and RustFS running; start the existing
   Node backend and React frontend as usual. The Python worker \`.venv\` and
   \`python-worker/.env\` must already be configured for the local services.
3. Stop **any previous** \`docmind_phase2_live\` Celery worker and old
   \`worker.automatic_dispatcher --watch\` windows. The launcher supplies
   its own dedicated worker and dispatcher.
4. From the repository root, run:
   
   \`\`\`cmd
   tools\START_AUTO_INGESTION_LOCAL.cmd
   \`\`\`

   The launcher opens two terminal windows:
   - **DocMind Local Ingestion Worker:** Celery \`docmind.ingestion.live_pilot_v1\`,
     with \`DOCMIND_ENABLE_PILOT_INGESTION=YES_FOR_TESTING\`.
   - **DocMind Local Auto Dispatcher:** \`worker.auto_ingestion_service --watch\`,
     with two test-only opt-in flags. It discovers new eligible uploads every
     15 seconds. Neither runs automatically after machine restart.
5. Upload a **new** supported document into a disposable local test KB. The
   Documents table refreshes every 5 seconds. Expect
   \`QUEUED -> PROCESSING -> READY\` after the pipeline finishes (no guaranteed
   duration). The worker's log and document status distinguish successful
   ingestion from failures.

## Safety and behavior

- The scheduler is **off unless both local flags are set explicitly**.
- The three existing protected QA knowledge bases are always excluded:
  Phase 2 \`cmvw9ro3w000548l1gkuh2gx8\`, Phase 3.3
  \`cmvxmqrea0000c8l1pcvmh7oa\`, and Phase 3.8
  \`cmvz6m3h10000qol1hoahhd6r\`. Additional IDs can be excluded
  with repeated \`--protect-knowledge-base-id ID\`.
- The CLI refuses to run in \`NODE_ENV=production\`. This is **not**
  production-grade autonomous ingestion, and it must not be used with
  untrusted customer data. Production needs service supervision, secure
  deployment configuration, alarms, durable recovery/audit procedures,
  resource controls, and a policy for old queued jobs.
- Only new eligible \`QUEUED\` jobs can be dispatched: no archived docs,
  no exhausted retries, no active dispatch lease, and no existing vectors
  on the version. The Qdrant preflight, exact KB scope check, transactional
  lease reservation and worker lease fencing from the existing pilot remain
  in force. Failed or partially published jobs do **not** get forced to READY.
- Queue transport via Redis can be retried safely because the worker's
  processing claim is transactional and scoped; dangling staged vectors
  still require an audit before reprocessing.
- A broad watcher also considers **other eligible local queued jobs**
  outside the three protected fixture KBs. Run this only in an isolated
  development database.
- To stop dispatch: Ctrl+C in the auto-dispatcher terminal. To stop
  ingestion: Ctrl+C in the worker terminal. Existing queued jobs stay in
  the database and are picked up when services restart if still eligible.
- Preview without dispatch:
  
  \`\`\`cmd
  cd python-worker
  .venv\Scripts\python.exe -m worker.auto_ingestion_service
  \`\`\`

## Tests

Run \`cd python-worker\` followed by
\`.venv\Scripts\python.exe -m unittest worker.test_auto_ingestion_service worker.test_automatic_dispatcher\`.
These safety tests use mocks; they do not create MySQL records or send Redis
messages. CI runs the new scheduler unit tests without a live database.

**Note:** This development service does not imply every PDF/DOCX/TXT file
will become READY; corrupt or unreadable files, failed OCR, unavailable
dependencies, vector conflicts and exhausted retries must remain visible
as failures for operator follow-up.
