# DocMind-AI Frontend

React/TypeScript dashboard for secure account access, workspaces, knowledge bases, and PDF/DOCX/TXT document management. This is the Phase 1 UI; document question-answering and RAG search are not yet implemented.

## Run locally

From `frontend/`:

```cmd
npm install
npm run dev
```

Open `http://localhost:5173`. The backend must already be running at `http://localhost:4000`.

Optional `frontend/.env` (copy `.env.example`):

```dotenv
VITE_API_URL=http://localhost:4000
```

Do not place secret tokens, storage keys, or passwords in Vite environment variables because they are bundled into the client.

## Build

```cmd
npm run build
npm run lint
```

For complete Docker, Prisma, and Python setup, see the root `PHASE1_SETUP_WINDOWS.md`.
