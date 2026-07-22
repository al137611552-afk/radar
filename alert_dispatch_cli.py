"""CLI entry point for bounded Watchman alert webhook delivery."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from alert_dispatch import dispatch_once


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="投递Watchman可靠预警发件箱")
    parser.add_argument(
        "--db", type=Path, default=Path("output/alerts/alerts.db"),
        help="SQLite告警发件箱路径",
    )
    parser.add_argument(
        "--webhook-url", default=os.environ.get("WATCHMAN_ALERT_WEBHOOK_URL"),
        help="Webhook地址；默认读取WATCHMAN_ALERT_WEBHOOK_URL",
    )
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--base-delay-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    if not args.webhook_url:
        parser.error(
            "--webhook-url or WATCHMAN_ALERT_WEBHOOK_URL is required"
        )
    return args


def main(argv=None):
    args = parse_args(argv)
    stats = dispatch_once(
        args.db,
        args.webhook_url,
        authorization=os.environ.get("WATCHMAN_ALERT_WEBHOOK_TOKEN"),
        batch_size=args.batch_size,
        lease_seconds=args.lease_seconds,
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        base_delay_seconds=args.base_delay_seconds,
    )
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
