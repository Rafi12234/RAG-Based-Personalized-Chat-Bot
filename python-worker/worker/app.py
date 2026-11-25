"""DocMind-AI Python service: existing health plus protected internal retrieval."""
from fastapi import FastAPI
from .retrieval_api import router as retrieval_router

app = FastAPI(title="DocMind-AI Ingestion Service", version="0.1.0")
app.include_router(retrieval_router)
from .advanced_live_api import router as advanced_router
app.include_router(advanced_router)


@app.get("/health")
def health():
    return {"success": True, "service": "docmind-python-worker", "status": "ready"}
