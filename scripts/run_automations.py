"""Scheduled automations (cron / Kubernetes CronJob).

    python scripts/run_automations.py digest --since-hours 24   # daily digest
    python scripts/run_automations.py watches                    # check saved watches (e.g. every 15 min)

Watches are read from Redis (REDIS_URL), where the API stores them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.dependencies import _build_llm_service, _build_rag_service, _build_vector_store  # noqa: E402
from src.auth.identity import multi_tenant_enabled  # noqa: E402
from src.automations import Notifier, WatchStore, build_digest, run_watches  # noqa: E402
from src.redis_client import get_redis_client  # noqa: E402


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    digest = sub.add_parser("digest", help="Summarise newly indexed documents and notify")
    digest.add_argument("--since-hours", type=int, default=24)
    digest.add_argument("--dry-run", action="store_true", help="Print instead of notifying")
    sub.add_parser("watches", help="Check saved watches and notify new matches")
    args = parser.parse_args()

    notifier = Notifier()
    if args.command == "digest":
        since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
        result = build_digest(_build_vector_store(), _build_llm_service(), since=since)
        if result["documents"] and not args.dry_run:
            result["delivered"] = notifier.send(result["subject"], result["body"])
        print(json.dumps({k: v for k, v in result.items() if k != "documents"}, ensure_ascii=False, indent=2))
        return 0

    redis_client = get_redis_client()
    if redis_client is None:
        print("REDIS_URL is not set: watches created through the API are not visible to this process")
        return 2
    scope = (lambda tenant: {"tenant": tenant}) if multi_tenant_enabled() else None
    alerts = run_watches(WatchStore(redis_client), _build_rag_service(), notifier, scope_for_tenant=scope)
    print(json.dumps({"alerts": alerts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
