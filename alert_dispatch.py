"""Lease-based reliable webhook delivery for the Watchman alert outbox."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from datetime import datetime, timezone
from numbers import Integral, Real
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from alert_store import claim_alerts, mark_alert_delivered, mark_alert_failed

_PUBLIC_FIELDS = (
    "id",
    "source",
    "logical_slot",
    "entity_code",
    "alert_type",
    "severity",
    "title",
    "message",
    "created_at",
    "payload",
)


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_WEBHOOK_PROXY_HANDLER = ProxyHandler({})
_WEBHOOK_OPENER = build_opener(_WEBHOOK_PROXY_HANDLER, _RejectRedirects)
_BEARER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/\-]+=*")


def _positive_integer(value, name) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _positive_number(value, name) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be positive")
    return float(value)


def validate_webhook_url(value) -> str:
    """Accept HTTPS endpoints and loopback HTTP endpoints without URL credentials."""
    url = str(value or "").strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("webhook URL contains control characters")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("webhook URL must be absolute HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("webhook URL must not contain embedded credentials")
    if parsed.fragment:
        raise ValueError("webhook URL must not contain a fragment")
    if parsed.scheme == "http":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise ValueError(
                "plain HTTP is allowed only for literal loopback addresses"
            ) from error
        if not address.is_loopback:
            raise ValueError("plain HTTP is allowed only for loopback webhooks")
    return url


def _authorization_header(value) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    if not token:
        return None
    if len(token) > 4096 or _BEARER_TOKEN_PATTERN.fullmatch(token) is None:
        raise ValueError("invalid webhook authorization token")
    return f"Bearer {token}"


def _public_event(event) -> dict:
    return {field: event.get(field) for field in _PUBLIC_FIELDS}


def idempotency_key(event) -> str:
    """Derive a stable key from the outbox's durable business identity."""
    fields = ("source", "logical_slot", "entity_code", "alert_type")
    identity = []
    for field in fields:
        value = event.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"event {field} is required for idempotency")
        identity.append(value)
    canonical = json.dumps(
        identity, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return f"watchman-alert-{digest}"


def post_webhook(url, event, authorization=None, timeout: float = 10) -> None:
    """POST one JSON event; any non-2xx response is a delivery failure."""
    endpoint = validate_webhook_url(url)
    timeout_seconds = _positive_number(timeout, "timeout")
    body = json.dumps(
        _public_event(event), ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Watchman-Alert-Dispatcher/1",
        "Idempotency-Key": idempotency_key(event),
    }
    auth_header = _authorization_header(authorization)
    if auth_header:
        headers["Authorization"] = auth_header
    request = Request(endpoint, data=body, headers=headers, method="POST")
    with _WEBHOOK_OPENER.open(request, timeout=timeout_seconds) as response:
        status = int(response.status)
        if not 200 <= status < 300:
            raise HTTPError(endpoint, status, "non-success webhook response", {}, None)


def _error_code(error) -> str:
    if isinstance(error, HTTPError):
        return f"HTTP_{error.code}"
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, URLError):
        return "NETWORK_ERROR"
    return "DELIVERY_ERROR"


def dispatch_once(
    path: Path,
    webhook_url,
    authorization=None,
    now=None,
    batch_size=20,
    lease_seconds=60,
    timeout=10,
    max_attempts=5,
    base_delay_seconds=60,
) -> dict[str, int]:
    """Deliver at most one bounded batch without sleeping between retries."""
    endpoint = validate_webhook_url(webhook_url)
    _authorization_header(authorization)
    batch = _positive_integer(batch_size, "batch_size")
    lease = _positive_integer(lease_seconds, "lease_seconds")
    timeout_seconds = _positive_number(timeout, "timeout")
    maximum = _positive_integer(max_attempts, "max_attempts")
    base_delay = _positive_integer(base_delay_seconds, "base_delay_seconds")
    if lease <= timeout_seconds:
        raise ValueError("lease_seconds must exceed timeout")
    fixed_now = now
    stats = {
        "claimed": 0, "delivered": 0, "retrying": 0,
        "dead_lettered": 0, "lease_lost": 0,
    }
    for _ in range(batch):
        current = fixed_now or datetime.now(timezone.utc)
        events = claim_alerts(path, now=current, limit=1, lease_seconds=lease)
        if not events:
            break
        event = events[0]
        stats["claimed"] += 1
        try:
            post_webhook(
                endpoint, event, authorization=authorization, timeout=timeout_seconds
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            finished_at = fixed_now or datetime.now(timezone.utc)
            updated = mark_alert_failed(
                path,
                event["id"],
                event["lease_token"],
                failed_at=finished_at,
                error_code=_error_code(error),
                max_attempts=maximum,
                base_delay_seconds=base_delay,
            )
            if not updated:
                stats["lease_lost"] += 1
            elif event["attempts"] >= maximum:
                stats["dead_lettered"] += 1
            else:
                stats["retrying"] += 1
        else:
            finished_at = fixed_now or datetime.now(timezone.utc)
            if mark_alert_delivered(
                path, event["id"], event["lease_token"], delivered_at=finished_at
            ):
                stats["delivered"] += 1
            else:
                stats["lease_lost"] += 1
    return stats
