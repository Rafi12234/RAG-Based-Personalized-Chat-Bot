"""Phase 2.11A: read-only recovery audit or explicit test-only single-job repair.

Preview (safe default):
    python -m worker.guarded_recovery_operator JOB_ID --knowledge-base-id KB_ID

Mutation (STOP dedicated Celery workers + watcher FIRST, disposable TEST KB ONLY):
    set "DOCMIND_ENABLE_TEST_RECOVERY=YES_FOR_TESTING"
    python -m worker.guarded_recovery_operator JOB_ID --knowledge-base-id KB_ID \
      --repair --confirm-job-id JOB_ID --confirm-knowledge-base-id KB_ID --workers-stopped

No background scheduling, SQL migrations, Redis messages or collection deletes.
"""
from __future__ import annotations

import argparse
import json

from . import settings  # noqa: F401
from .guarded_recovery import inspect_one, repair_one


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--repair", action="store_true", help="Modify a single test job / its orphan vectors")
    parser.add_argument("--confirm-job-id")
    parser.add_argument("--confirm-knowledge-base-id")
    parser.add_argument("--workers-stopped", action="store_true",
                        help="Affirm all test ingestion workers and dispatcher were stopped")
    args = parser.parse_args(argv)
    try:
        if args.repair:
            result = repair_one(
                args.job_id, args.knowledge_base_id,
                confirm_job_id=args.confirm_job_id,
                confirm_knowledge_base_id=args.confirm_knowledge_base_id,
                workers_stopped=args.workers_stopped,
            )
        else:
            result = inspect_one(args.job_id, args.knowledge_base_id)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        # No raw exception strings: they can include documents/keys/paths.
        print("Test recovery operator refused or failed: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
