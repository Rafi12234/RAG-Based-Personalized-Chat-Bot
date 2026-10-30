"""Read-only production Qdrant schema preview; optional explicit empty setup.

Run from python-worker:
    python -m worker.production_qdrant_probe
    python -m worker.production_qdrant_probe --initialize

With --initialize, a *different* empty local production-named collection is
created/indexed. No points are inserted, and the diagnostic collection is
untouched. MySQL and RustFS are never accessed by this script.
"""
from __future__ import annotations

import argparse
import json

from .production_qdrant import initialize_collection, inspect_collection, local_client


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize", action="store_true", help="Explicitly create/repair only the separate empty production collection")
    args = parser.parse_args(argv)
    client = None
    try:
        client = local_client()
        result = (initialize_collection(client) if args.initialize else inspect_collection(client)).as_dict()
        result.update({
            "mode": "initialize" if args.initialize else "preview",
            "mysql_modified": False,
            "vectors_written": False,
            "existing_points_deleted": False,
            "automatic_ingestion_enabled": False,
        })
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        # Suppress raw URLs or access tokens in third-party exceptions.
        print(f"Production Qdrant schema probe failed: {type(exc).__name__}")
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
