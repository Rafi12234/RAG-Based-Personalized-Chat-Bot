"""Phase 2.11B TEST-KB recovery status and operator-supervised batch repair.

Defaults to READ-ONLY. --watch performs READ-ONLY periodic audits only.
--repair-once changes Qdrant/MySQL and REQUIRES all ingestion workers and
watchers stopped. Never run on customer or production knowledge bases.
"""
from __future__ import annotations

import argparse
import json
import logging
import time

from . import settings  # noqa: F401
from .recovery_batch import (
    DEFAULT_SCAN_LIMIT, REPAIR_CONFIRM, inspect_batch, repair_batch,
)

LOGGER = logging.getLogger("docmind.recovery_batch")


def watch_read_only(knowledge_base_id: str, *, interval_seconds: int = 60,
                    limit: int = DEFAULT_SCAN_LIMIT,
                    inspect=inspect_batch, wait=time.sleep) -> None:
    if type(interval_seconds) is not int or not 30 <= interval_seconds <= 3600:
        raise ValueError("Read-only interval must be between 30 and 3600 seconds")
    while True:
        report = inspect(knowledge_base_id, limit=limit)
        # Only IDs and counts, never document content or private tokens.
        LOGGER.info("Recovery audit: %s", json.dumps(report))
        wait(interval_seconds)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="Read-only recurring audit, Ctrl+C to stop")
    mode.add_argument("--repair-once", action="store_true", help="TEST-ONLY mutation with explicit opt-ins")
    parser.add_argument("--scan-limit", type=int, default=DEFAULT_SCAN_LIMIT)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--max-jobs", type=int, default=1)
    parser.add_argument("--confirm-knowledge-base-id")
    parser.add_argument("--confirm-phrase")
    parser.add_argument("--workers-stopped", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.watch:
            print("READ-ONLY recovery audit started. Ctrl+C to stop.", flush=True)
            watch_read_only(args.knowledge_base_id,
                            interval_seconds=args.interval_seconds,
                            limit=args.scan_limit)
            return 0
        if args.repair_once:
            report = repair_batch(
                args.knowledge_base_id,
                confirm_knowledge_base_id=args.confirm_knowledge_base_id,
                confirm_phrase=args.confirm_phrase,
                workers_stopped=args.workers_stopped,
                max_jobs=args.max_jobs,
            )
            print(json.dumps(report, indent=2))
            return 0 if report.get("result") == "completed" else 1
        print(json.dumps(inspect_batch(args.knowledge_base_id, limit=args.scan_limit), indent=2))
        return 0
    except KeyboardInterrupt:
        print("Recovery read-only audit stopped.")
        return 0
    except Exception as exc:
        print("Recovery operation refused or failed: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
