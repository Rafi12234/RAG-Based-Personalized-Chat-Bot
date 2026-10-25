"""Conservative Phase 2.11C automated expired-lease recovery for a disposable KB.

Default PREVIEW is read-only:
    python -m worker.safe_auto_recovery_operator --knowledge-base-id TEST_KB_ID

Explicit test-only SQL repair (no vector deletion, no Celery messages):
    set "DOCMIND_ENABLE_SAFE_AUTO_RECOVERY=YES_FOR_TESTING"
    python -m worker.safe_auto_recovery_operator --knowledge-base-id TEST_KB_ID \
        --run-once --confirm-knowledge-base-id TEST_KB_ID

Test-only polling (foreground, Ctrl+C stops): add --watch instead of --run-once.
Do not use with customer KBs or deploy as an unattended production daemon.
"""
from __future__ import annotations

import argparse
import json
import logging
import time

from . import settings  # noqa: F401
from .safe_auto_recovery import (
    DEFAULT_GRACE_SECONDS, DEFAULT_LIMIT, MAX_SCAN, preview_expired_jobs,
    recover_expired_batch, validate_enablement,
)

LOGGER = logging.getLogger("docmind.safe_auto_recovery")


def watch_recovery(knowledge_base_id: str, *, confirm_knowledge_base_id: str,
                   grace_seconds: int = DEFAULT_GRACE_SECONDS, max_jobs: int = DEFAULT_LIMIT,
                   interval_seconds: int = 60, recovery_round=recover_expired_batch,
                   wait=time.sleep, env_value: str | None = None) -> None:
    if type(interval_seconds) is not int or not 30 <= interval_seconds <= 3600:
        raise ValueError("interval_seconds must be between 30 and 3600")
    validate_enablement(knowledge_base_id, confirm_knowledge_base_id=confirm_knowledge_base_id,
                        grace_seconds=grace_seconds, max_jobs=max_jobs, env_value=env_value)
    while True:
        report = recovery_round(
            knowledge_base_id, confirm_knowledge_base_id=confirm_knowledge_base_id,
            grace_seconds=grace_seconds, max_jobs=max_jobs, env_value=env_value,
        )
        LOGGER.info("Expired-lease recovery round: %s", json.dumps(report))
        wait(interval_seconds)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-once", action="store_true", help="Opt-in MySQL recovery, no vector deletion")
    group.add_argument("--watch", action="store_true", help="Opt-in foreground polling, Ctrl+C to stop")
    parser.add_argument("--confirm-knowledge-base-id")
    parser.add_argument("--grace-seconds", type=int, default=DEFAULT_GRACE_SECONDS)
    parser.add_argument("--max-jobs", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--scan-limit", type=int, default=MAX_SCAN)
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.watch:
            print("TEST-ONLY expired-lease recovery watcher started; Ctrl+C to stop.", flush=True)
            watch_recovery(args.knowledge_base_id,
                           confirm_knowledge_base_id=args.confirm_knowledge_base_id,
                           grace_seconds=args.grace_seconds, max_jobs=args.max_jobs,
                           interval_seconds=args.interval_seconds)
            return 0
        if args.run_once:
            result = recover_expired_batch(
                args.knowledge_base_id,
                confirm_knowledge_base_id=args.confirm_knowledge_base_id,
                grace_seconds=args.grace_seconds, max_jobs=args.max_jobs,
            )
            print(json.dumps(result, indent=2))
            return 1 if result["requires_manual_review"] else 0
        result = preview_expired_jobs(
            args.knowledge_base_id,
            grace_seconds=args.grace_seconds, limit=args.scan_limit,
        )
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print("Stopped test recovery watcher.")
        return 0
    except Exception as exc:
        print("Safe recovery refused or failed: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
