# DocMind-AI

Self-hosted, document-grounded chatbot project. **Phase 1** implements account-based document workspaces and the service infrastructure. Document extraction, OCR, chunking, embeddings, retrieval and grounded AI answers will be added in later phases; they are **not implemented** in this repository yet.

## Stack

React/Vite/TypeScript, Express/TypeScript, Prisma 7/MySQL 8, RustFS (S3-compatible object storage), Redis, Qdrant, Python/FastAPI and Celery.

## Phase 1 features

- Register, log in, log out (Argon2id hashes and revocable database-backed sessions).
- Create organizations, add already-registered members, and manage OWNER/ADMIN/MEMBER access.
- Create, list, edit and delete **empty** knowledge bases.
- Upload PDF/DOCX/TXT files (250 MB maximum), list them, download from S3-compatible storage and archive them.
- Workspace membership is checked before accepting document upload payloads and before document reads.
- Managers may archive workspace documents; members may archive only their own uploads.
- **Archived documents remain physically stored.** They cannot be downloaded via the API; knowledge bases with any document history cannot be deleted until a later retention/purge workflow is implemented.
- FastAPI health endpoint and a Celery diagnostic ping task (no document processing yet).

## Quick start: Windows CMD

Prerequisites: Node.js 24, Python 3.12, Docker Desktop.

1. Copy the root `.env.example` to `.env`, and populate the existing MySQL/RustFS credentials. Keep this file private.
2. Start containers:

   ```cmd
   docker compose up -d
   docker compose ps
   ```

3. Create `backend/.env` from `backend/.env.example`. Use the **same** MySQL application password and RustFS credentials as the root file; Prisma URLs need URL-encoded credentials.

   ```cmd
   cd backend
   npm ci
   npx prisma migrate deploy
   npx prisma generate
   npm run typecheck
   npm run build
   npm test
   npm run storage:init
   npm run dev
   ```

4. In a separate CMD terminal:

   ```cmd
   cd frontend
   npm ci
   copy .env.example .env
   npm run dev
   ```

5. In a separate CMD terminal:

   ```cmd
   cd python-worker
   py -3.12 -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   python -m uvicorn worker.app:app --host 127.0.0.1 --port 8000 --reload
   ```

6. In a fourth CMD terminal:

   ```cmd
   cd python-worker
   .venv\Scripts\activate
   python -m celery -A worker.celery_app:celery_app worker --pool=solo --loglevel=info
   ```

URLs: React http://localhost:5173, Express http://localhost:4000/api/v1/health, MySQL health http://localhost:4000/api/v1/health/database, FastAPI http://127.0.0.1:8000/health.

To test an actual Celery task, keep the worker running and in another terminal execute:
```cmd
cd python-worker
.venv\Scripts\activate
python -c "from worker.celery_app import celery_app; print(celery_app.send_task('docmind.health.ping').get(timeout=30))"
```

## Verification

Backend: `npm test` runs session-security unit tests and MySQL-backed API integration tests. Integration tests require a running local test database with the schema migrated and should **never target production or shared databases**. CI provisions an isolated MySQL service. Frontend: `npm run lint` and `npm run build`. Python: `python -m compileall -q worker`.

## Security and deployment notes

Do not commit `.env` files or production credentials. The default setup binds container ports to loopback for local development. Rotate any previously shared passwords. The cookie-based session requires HTTPS in production and a consistent frontend origin; review CORS, request-origin checks and proxy settings before deployment. Rate limiting, object-store access control, storage retention/purge, malware scanning, large-file upload resilience, session cleanup, backups, monitoring and security reviews need further production hardening. **Passing Phase 1 checks does not imply the full AI pipeline is production ready.**
