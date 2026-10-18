"""Diagnostic task; Phase 2 will add extraction/chunk/embedding tasks."""
from .celery_app import celery_app


@celery_app.task(name="docmind.health.ping")
def ping():
    return {"success": True, "worker": "online"}
