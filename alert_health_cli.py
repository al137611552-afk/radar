"""Sanitized read-only CLI for durable alert delivery health."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from alert_store import load_alert_delivery_health


def _utc_now():
    return datetime.now(timezone.utc)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Read aggregate Watchman alert delivery health"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("output/alerts/alerts.db"),
        help="alert outbox SQLite path",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        health = load_alert_delivery_health(args.db, now=_utc_now())
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        print(
            json.dumps({"error": "ALERT_HEALTH_UNAVAILABLE"}, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(health, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
