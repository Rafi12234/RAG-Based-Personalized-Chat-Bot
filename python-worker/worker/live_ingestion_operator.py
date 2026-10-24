"""Read-only preview or explicitly submit ONE job to a dedicated pilot queue.

Usage:
  python -m worker.live_ingestion_operator JOB_ID
  python -m worker.live_ingestion_operator JOB_ID --submit --confirm-job-id JOB_ID

The --submit option changes Redis by publishing a message. A dedicated test
worker with explicit environment opt-in must be running to execute the job.
"""
from __future__ import annotations

import argparse
import json

from . import settings  # loads existing .env
from .live_ingestion_worker import preview_pilot

LIVE_PILOT_QUEUE = "docmind_phase2_live"
LIVE_PILOT_TASK = "docmind.ingestion.live_pilot_v1"


def operator_action(job_id: str, *, submit: bool = False,
                    confirm_job_id: str | None = None) -> dict:
    result = preview_pilot(job_id)
    if not submit:
        return result
    if not isinstance(confirm_job_id, str) or confirm_job_id != job_id:
        raise ValueError("Explicit --confirm-job-id must exactly match job ID")
    from .celery_app import celery_app
    task = celery_app.send_task(
        LIVE_PILOT_TASK, args=[job_id], queue=LIVE_PILOT_QUEUE, retry=False
    )
    return {
        "job_id": job_id,
        "result": "task_published_to_dedicated_test_queue",
        "celery_task_id": task.id,
        "queue": LIVE_PILOT_QUEUE,
        "job_completed": False,
        "automatic_dispatcher_enabled": False,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("job_id")
    p.add_argument("--submit", action="store_true", help="Publish task to dedicated pilot queue")
    p.add_argument("--confirm-job-id", help="Must equal job_id when submitting")
    a = p.parse_args(argv)
    try:
        print(json.dumps(operator_action(a.job_id, submit=a.submit,
                                         confirm_job_id=a.confirm_job_id), indent=2))
        return 0
    except Exception as exc:
        print("Pilot operator failed: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
