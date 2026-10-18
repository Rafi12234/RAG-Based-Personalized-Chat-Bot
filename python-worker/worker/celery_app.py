"""Celery/Redis scaffolding: no document-processing task is enabled in Phase 1."""
from celery import Celery
from .settings import REDIS_URL

celery_app = Celery("docmind", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    imports=("worker.tasks",),
)
