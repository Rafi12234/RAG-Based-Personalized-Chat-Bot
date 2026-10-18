"""FastAPI service boundary for future ingestion APIs."""
from fastapi import FastAPI

app = FastAPI(title="DocMind-AI Ingestion Service", version="0.1.0")


@app.get("/health")
def health():
    return {"success": True, "service": "docmind-python-worker", "status": "ready"}
