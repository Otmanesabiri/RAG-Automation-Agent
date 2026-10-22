"""Synchronise configured connectors into the index (for cron / Kubernetes CronJob).

python scripts/sync_sources.py            # every connector in CONNECTORS_CONFIG
python scripts/sync_sources.py docs-share # one connector
python scripts/sync_sources.py --full     # re-index everything
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.dependencies import _build_pipeline, _build_vector_store  # noqa: E402
from src.connectors import SyncService, SyncStateStore, load_connectors  # noqa: E402
from src.redis_client import get_redis_client  # noqa: E402


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="Connector names (default: all)")
    parser.add_argument("--full", action="store_true", help="Ignore the stored versions and re-index everything")
    args = parser.parse_args()

    connectors = load_connectors()
    unknown = set(args.names) - set(connectors)
    if unknown:
        print(f"Unknown connector(s): {', '.join(sorted(unknown))}. Configured: {', '.join(connectors) or 'none'}")
        return 2

    store = _build_vector_store()
    store.ensure_index()
    service = SyncService(_build_pipeline(), store, SyncStateStore(get_redis_client()))
    failed = False
    for name in args.names or connectors:
        report = service.sync(connectors[name], full=args.full)
        print(json.dumps(report.as_dict(), ensure_ascii=False))
        failed = failed or report.failed > 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
