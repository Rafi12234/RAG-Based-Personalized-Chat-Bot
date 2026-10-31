DocMind-AI Phase 3.2 — Query Embedding Compatibility (FIRST INCREMENT)
Baseline GitHub: feature/phase3-secure-retrieval @ 799a4eac4e905524c63d18fa726266f0570297a8

THIS ARCHIVE ADDS TWO NEW FILES ONLY. It does not replace app.py, app.ts,
Prisma schema, migrations, existing tests, or existing Phase 2 modules.
No MySQL, Qdrant, Redis or RustFS writes occur in this probe.

1) Verify Git branch, from project root in CMD:
   git branch --show-current
   git status --short

2) Extract this ZIP's python-worker folder into the existing project root,
   merging folders. STOP if Windows asks to replace an existing file.

3) Run OFFLINE automated unit tests in CMD:
   cd /d C:\Users\shaji\Desktop\Projects\RAG-Based-Personalized-Chat-Bot\python-worker
   .venv\Scripts\activate.bat
   python -m unittest worker.test_phase3_query_compatibility -v

4) Run REAL read-only embedding smoke test (may load cached model):
   python -m worker.phase3_query_compatibility --case all

5) Check that all English/Bangla/mixed cases return 384 dimensions,
   normalized: true, and all_cases_passed: true. It deliberately does
   NOT assert an exact model revision: ingestion metadata only pins the
   model name, not a full revision hash. Revision pinning requires a
   reviewed compatibility decision; do not re-embed existing documents.

6) Then run the complete prior Python suite:
   python -m unittest discover -s worker -t . -p "test_*.py"

Do not run --initialize, --publish-once, migrations, or destructive resets.
Never share .env values, private source text, cookies, or model caches.
