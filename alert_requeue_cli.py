"""Fail-closed operator CLI for replaying Watchman alert dead letters."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from alert_store import requeue_failed_alerts

_MAX_REQUEUE_BATCH = 1000
_SQLITE_MAX_INTEGER = 2**63 - 1


def _bounded_limit(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= parsed <= _MAX_REQUEUE_BATCH:
        raise argparse.ArgumentTypeError(
            f"limit must be between 1 and {_MAX_REQUEUE_BATCH}"
        )
    return parsed


def _positive_id(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("id must be an integer") from exc
    if not 1 <= parsed <= _SQLITE_MAX_INTEGER:
        raise argparse.ArgumentTypeError("id must be a positive SQLite integer")
    return parsed


def _utc_now():
    return datetime.now(timezone.utc)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="安全重放Watchman Webhook死信告警"
    )
    parser.add_argument(
        "--db", type=Path, default=Path("output/alerts/alerts.db"),
        help="SQLite告警发件箱路径",
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--id", dest="event_ids", action="append", type=_positive_id,
        help="重放指定告警ID；可重复传入",
    )
    selector.add_argument(
        "--all", dest="all_failed", action="store_true",
        help="重放最早的一批失败告警",
    )
    parser.add_argument("--limit", type=_bounded_limit, default=100)
    parser.add_argument(
        "--confirm", action="store_true",
        help="确认执行会修改SQLite投递状态的重放操作",
    )
    args = parser.parse_args(argv)
    if not args.confirm:
        parser.error("--confirm is required")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        replayed = requeue_failed_alerts(
            args.db,
            now=_utc_now(),
            event_ids=None if args.all_failed else args.event_ids,
            limit=args.limit,
        )
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        print(
            json.dumps({"error": "REQUEUE_FAILED"}, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    summary = {
        "event_ids": replayed,
        "requeued": len(replayed),
        "selector": "all" if args.all_failed else "ids",
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
