"""Read-only DocMind-AI full ingestion rehearsal.

Usage: python -m worker.ingestion_rehearsal_probe JOB_ID [--language eng+ben]
Does not claim jobs, mark READY, enqueue Celery tasks or write Qdrant vectors.
"""
from __future__ import annotations

import argparse
import json

from .ingestion_rehearsal import rehearse_ingestion


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--language", default="eng")
    parser.add_argument("--max-ocr-pages", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        result = rehearse_ingestion(
            args.job_id, language=args.language, max_ocr_pages=args.max_ocr_pages
        )
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        # Intentionally suppress raw messages: SDK errors may contain storage
        # object keys, document text or credentials.
        print(f"Ingestion rehearsal failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
