# DocMind-AI Frontend

React 19/Vite dashboard for Phase 1 account access, workspaces, team permissions, knowledge base management, and PDF/DOCX/TXT document upload/download/archive.

Owners and admins can edit or delete **empty** knowledge bases; any knowledge base with document history (including archived documents) is retained. Document extraction, search, and chatbot answers are future phases.

## Run
From `frontend/`:
```cmd
npm ci
copy .env.example .env
npm run dev
```
Open http://localhost:5173; the API must run on http://localhost:4000.

## Verify
```cmd
npm run lint
npm run build
```

Never place secrets into `VITE_*` variables, which are public to the browser. See the root README for the backend/database/worker setup.
