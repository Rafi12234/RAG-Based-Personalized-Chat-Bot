"""Opt-in Celery task, registered ONLY when the operator includes this module.

Start a dedicated test worker with Celery --include=worker.live_ingestion_task
and --queues=docmind_phase2_live. Existing dispatcher is NOT modified.
"""
from __future__ import annotations

import os
from .celery_app import celery_app
from .live_ingestion_worker import PILOT_FLAG, PILOT_VALUE, run_pilot_ingestion


@celery_app.task(name="docmind.ingestion.live_pilot_v1", acks_late=True)
def live_pilot_ingestion(job_id: str):
    if os.environ.get(PILOT_FLAG) != PILOT_VALUE:
        return {"job_id": job_id, "result": "disabled", "automatic_dispatcher_enabled": False}
    return run_pilot_ingestion(job_id)
