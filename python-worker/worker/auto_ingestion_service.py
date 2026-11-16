"""Opt-in local development multi-KB ingestion scheduler.

The existing scoped dispatcher and live-pilot worker do the actual work. This
process discovers eligible queued KBs and calls that dispatcher *per KB*;
it never bypasses the scope check, the Qdrant preflight, or worker lease fences.

Off by default. The caller must explicitly enable the local test flag AND
run --watch or --once. This is not a production deployment scheduler.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Callable

LOGGER = logging.getLogger("docmind.auto_ingestion")
ENABLE_FLAG = "DOCMIND_ENABLE_MULTI_KB_INGESTION"
ENABLE_VALUE = "YES_FOR_LOCAL_TESTING"
SCOPED_DISPATCH_FLAG = "DOCMIND_ENABLE_AUTODISPATCH"
SCOPED_DISPATCH_VALUE = "YES_FOR_TESTING"
# Legacy fixture KBs are permanently excluded from the local broad scheduler.
# This is a safety backstop, not a permission bypass for other KBs.
PROTECTED_QA_KBS = (
    "cmvw9ro3w000548l1gkuh2gx8",  # Phase 2 pilot
    "cmvxmqrea0000c8l1pcvmh7oa",  # Phase 3.3 QA
    "cmvz6m3h10000qol1hoahhd6r",  # Phase 3.8 QA
)
MAX_SCOPES = 50
DEFAULT_SCOPE_LIMIT = 20
DEFAULT_INTERVAL = 15
DEFAULT_BATCH_SIZE = 3


def validate_options(*, active: bool, interval: int = DEFAULT_INTERVAL,
                     scope_limit: int = DEFAULT_SCOPE_LIMIT,
                     batch_size: int = DEFAULT_BATCH_SIZE,
                     enabled: str | None = None,
                     scoped_enabled: str | None = None,
                     environment: str | None = None) -> None:
    if type(interval) is not int or not 10 <= interval <= 3600:
        raise ValueError("interval must be 10..3600 seconds")
    if type(scope_limit) is not int or not 1 <= scope_limit <= MAX_SCOPES:
        raise ValueError("scope limit must be 1..50")
    if type(batch_size) is not int or not 1 <= batch_size <= 10:
        raise ValueError("batch size must be 1..10")
    if active:
        enabled = os.environ.get(ENABLE_FLAG) if enabled is None else enabled
        scoped_enabled = os.environ.get(SCOPED_DISPATCH_FLAG) if scoped_enabled is None else scoped_enabled
        environment = os.environ.get("NODE_ENV", "") if environment is None else environment
        if environment.lower() == "production":
            raise RuntimeError("Local pilot auto-ingestion cannot run in production mode")
        if enabled != ENABLE_VALUE or scoped_enabled != SCOPED_DISPATCH_VALUE:
            raise RuntimeError("Local pilot auto-ingestion is disabled")


def list_pending_knowledge_bases(*, scope_limit: int = DEFAULT_SCOPE_LIMIT,
                                 protected_ids: tuple[str, ...] = (),
                                 connection_factory=None) -> list[str]:
    """Read only. Discover eligible KBs, excluding explicitly protected fixtures.

    Only queued, unarchived documents with retry budget and expired/absent
    dispatch leases are eligible. This query cannot authorize a job; the
    existing scoped reservation and preflight independently re-check it.
    """
    if type(scope_limit) is not int or not 1 <= scope_limit <= MAX_SCOPES:
        raise ValueError("Invalid scope limit")
    if any(not isinstance(kb, str) or not kb.strip() or len(kb) > 128
           for kb in protected_ids):
        raise ValueError("Invalid protected knowledge base ID")
    if connection_factory is None:
        from .db import get_connection
        connection_factory = get_connection
    exclusions = tuple(dict.fromkeys((*PROTECTED_QA_KBS, *protected_ids)))
    exclusion_clause = (
        " AND d.knowledgeBaseId NOT IN (" + ", ".join(["%s"] * len(exclusions)) + ")"
        if exclusions else ""
    )
    query = (
        """
        SELECT d.knowledgeBaseId AS knowledge_base_id
        FROM IngestionJob AS j
        JOIN DocumentVersion AS v ON v.id = j.documentVersionId
        JOIN Document AS d ON d.id = v.documentId
        WHERE j.status = 'QUEUED'
          AND v.status = 'QUEUED'
          AND d.deletedAt IS NULL
          AND j.attempts < 3
          AND (j.nextAttemptAt IS NULL OR j.nextAttemptAt <= UTC_TIMESTAMP(3))
          AND (j.dispatchLeaseUntil IS NULL OR j.dispatchLeaseUntil <= UTC_TIMESTAMP(3))
        """
        + exclusion_clause
        + """
        GROUP BY d.knowledgeBaseId
        ORDER BY MIN(j.createdAt), d.knowledgeBaseId
        LIMIT %s
        """
    )
    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (*exclusions, scope_limit))
            rows = cursor.fetchall()
        result = [row["knowledge_base_id"] for row in rows]
        if any(not isinstance(kb, str) or kb in exclusions for kb in result):
            raise RuntimeError("Knowledge-base discovery returned an invalid scope")
        return result
    finally:
        connection.rollback()  # also close an uncommitted read transaction
        connection.close()


def run_round(*, publish: bool = False,
              protected_ids: tuple[str, ...] = (),
              scope_limit: int = DEFAULT_SCOPE_LIMIT,
              batch_size: int = DEFAULT_BATCH_SIZE,
              enabled: str | None = None,
              scoped_enabled: str | None = None,
              discover: Callable | None = None,
              dispatch: Callable | None = None) -> dict:
    """One round. Preview never reserves or publishes; active mode is opt-in.

    Every dispatch uses the existing KB-bound lease + exact confirmation.
    A failing scope is recorded and cannot prevent other KBs from progressing.
    """
    validate_options(active=publish, scope_limit=scope_limit, batch_size=batch_size,
                     enabled=enabled, scoped_enabled=scoped_enabled)
    if discover is None:
        discover = list_pending_knowledge_bases
    ids = discover(scope_limit=scope_limit, protected_ids=tuple((*PROTECTED_QA_KBS, *protected_ids)))
    summary = {"mode": "active" if publish else "preview",
               "knowledge_bases_scanned": len(ids), "published": 0,
               "rejected_or_failed_scopes": 0}
    if not publish:
        return summary
    if dispatch is None:
        from .automatic_dispatcher import dispatch_scope_once
        dispatch = dispatch_scope_once
    protected = set((*PROTECTED_QA_KBS, *protected_ids))
    for knowledge_base_id in ids:
        if not isinstance(knowledge_base_id, str) or not knowledge_base_id.strip() or knowledge_base_id in protected:
            summary["rejected_or_failed_scopes"] += 1
            continue
        try:
            result = dispatch(
                knowledge_base_id,
                publish=True,
                confirm_knowledge_base_id=knowledge_base_id,
                batch_size=batch_size,
                env_flag=SCOPED_DISPATCH_VALUE,
            )
            summary["published"] += result["published"]
            summary["rejected_or_failed_scopes"] += result["failed"] + result["preflight_rejected"]
        except Exception:
            summary["rejected_or_failed_scopes"] += 1
            LOGGER.warning("Scoped dispatch refused a knowledge base (details hidden)")
    return summary


def watch(*, protected_ids: tuple[str, ...] = (),
          interval: int = DEFAULT_INTERVAL,
          scope_limit: int = DEFAULT_SCOPE_LIMIT,
          batch_size: int = DEFAULT_BATCH_SIZE,
          run: Callable = run_round,
          sleep: Callable = time.sleep) -> None:
    validate_options(active=True, interval=interval, scope_limit=scope_limit,
                     batch_size=batch_size)
    while True:
        try:
            summary = run(publish=True, protected_ids=protected_ids,
                          scope_limit=scope_limit, batch_size=batch_size)
            if summary["published"] or summary["rejected_or_failed_scopes"]:
                LOGGER.info("Ingestion dispatch: %s", json.dumps(summary))
        except Exception:
            LOGGER.exception("Auto-ingestion scan failed (no unsafe fallback)")
        sleep(interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="Explicit one-round dispatch")
    modes.add_argument("--watch", action="store_true", help="Dispatch new uploads continuously")
    parser.add_argument("--protect-knowledge-base-id", action="append", default=[],
                        help="Never dispatch jobs from this KB (repeatable)")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--scope-limit", type=int, default=DEFAULT_SCOPE_LIMIT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    protected = tuple(args.protect_knowledge_base_id)
    try:
        validate_options(active=args.once or args.watch, interval=args.interval_seconds,
                         scope_limit=args.scope_limit, batch_size=args.batch_size)
        if args.watch:
            print("Local test auto-ingestion enabled; Ctrl+C to stop.", flush=True)
            watch(protected_ids=protected, interval=args.interval_seconds,
                  scope_limit=args.scope_limit, batch_size=args.batch_size)
            return 0
        result = run_round(publish=args.once, protected_ids=protected,
                           scope_limit=args.scope_limit, batch_size=args.batch_size)
        print(json.dumps(result, indent=2))
        return int(result["rejected_or_failed_scopes"] > 0)
    except KeyboardInterrupt:
        print("Auto-ingestion watcher stopped.")
        return 0
    except Exception as exc:
        print("Auto-ingestion refused to start: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
