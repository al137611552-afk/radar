"""Persistent signal diffing for incremental scanner alerts."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd


def _json_value(value):
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _record_payload(record):
    return {str(key): _json_value(value) for key, value in record.items()}


def _change_type(previous, current):
    cross_fields = ("ma_cross_time", "macd_cross_time")
    for field in cross_fields:
        if current.get(field) is not None and current.get(field) != previous.get(field):
            return "新金叉"
    confirmation_fields = (
        "double_confirmed", "ma_direction_confirmed",
        "macd_direction_confirmed",
    )
    if any(current.get(field) != previous.get(field) for field in confirmation_fields):
        return "确认变化"
    return "状态变化"


def load_state(path):
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def save_state(path, state):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(target)


def diff_signals(
    current: pd.DataFrame, previous_state, fingerprint_fields, scope,
    key_field="code",
):
    """Return changed signal rows and a replacement state snapshot."""
    previous = previous_state if previous_state.get("scope") == scope else {}
    prior_signals = previous.get("signals", {})
    alerts = []
    signals = {}
    for record in current.to_dict("records"):
        key = str(record[key_field])
        fingerprint = {
            field: _json_value(record.get(field)) for field in fingerprint_fields
        }
        payload = _record_payload(record)
        prior = prior_signals.get(key)
        signals[key] = {"fingerprint": fingerprint, "payload": payload}
        if prior is None:
            alerts.append({**record, "alert_type": "首次命中"})
        elif prior.get("fingerprint") != fingerprint:
            alerts.append({
                **record,
                "alert_type": _change_type(prior.get("fingerprint", {}), fingerprint),
            })
    for key in sorted(set(prior_signals) - set(signals)):
        payload = prior_signals[key].get("payload", {key_field: key})
        alerts.append({**payload, "alert_type": "信号失效"})
    state = {"version": 1, "scope": scope, "signals": signals}
    return pd.DataFrame(alerts), state
