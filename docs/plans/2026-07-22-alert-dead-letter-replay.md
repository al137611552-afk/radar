# Alert Dead-letter Replay Implementation Plan

> **For Hermes:** Use strict TDD and complete each vertical slice before the next.

**Goal:** Allow operators to safely requeue terminal Webhook failures while preserving replay audit history and keeping the Web Dashboard read-only.

**Architecture:** Extend `alert_events` with additive replay audit columns migrated inside the existing `BEGIN IMMEDIATE` initialization transaction. A store function will atomically select only `failed` rows and reset delivery state for a new bounded attempt cycle while incrementing a durable replay counter. A dedicated CLI will require explicit confirmation and either explicit IDs or a bounded `--all`; Dashboard/API will expose only replay count and time as read-only metadata.

**Tech Stack:** Python 3.11+, sqlite3/WAL, pandas, unittest, existing vanilla JavaScript Dashboard.

---

### Task 1: Persist replay audit state

**Files:**
- Modify: `tests/test_alert_store.py`
- Modify: `alert_store.py`

1. Add a failing migration test requiring `requeue_count` and `last_requeued_at` on old databases.
2. Run the targeted test and verify the expected missing-column failure.
3. Add fixed additive migrations and defaults.
4. Run the targeted test and existing alert-store suite.

### Task 2: Atomically requeue failed alerts

**Files:**
- Modify: `tests/test_alert_store.py`
- Modify: `alert_store.py`

1. Add failing tests for explicit-ID replay, bounded all-failed replay, non-failed rejection, idempotent repeated replay, and concurrent replay exclusivity.
2. Verify each test fails because the API does not exist.
3. Implement `requeue_failed_alerts(path, now, event_ids=None, limit=100)` with `BEGIN IMMEDIATE` and parameter-bound SQL.
4. Reset `delivery_status`, `attempts`, errors, retry/lease/delivery timestamps; increment `requeue_count`; set `last_requeued_at`.
5. Run targeted and store regression tests.

### Task 3: Add a fail-closed operator CLI

**Files:**
- Create: `tests/test_alert_requeue_cli.py`
- Create: `alert_requeue_cli.py`

1. Add failing tests requiring exactly one selector (`--id` or `--all`), mandatory `--confirm`, positive bounded `--limit`, JSON-only summary, and no secret/error leakage.
2. Implement argument validation and call the store API.
3. Treat zero selected rows as a successful no-op; configuration and database failures remain non-zero.
4. Run CLI tests and a real temporary-database CLI invocation.

### Task 4: Expose replay audit in the read-only Dashboard

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `dashboard.py`
- Modify: `web/index.html`
- Modify: `web/assets/dashboard.js`
- Modify: `README.md`

1. Add failing API and static asset tests for `requeue_count` and `last_requeued_at`.
2. Add fields to the backend public whitelist and strict row validation.
3. Add read-only replay columns to the alert table using `textContent`/DOM nodes only.
4. Document replay safety and exact CLI examples.
5. Run Dashboard tests and `node --check`.

### Task 5: Verify and ship

1. Run all unittests.
2. Run Ruff on changed Python files, compileall, JavaScript syntax, diff checks, ignore checks, and security scans.
3. Run a real end-to-end sequence: enqueue → force dead-letter → CLI requeue → dispatcher deliver → Dashboard readback.
4. Perform independent fail-closed review of the final staged diff.
5. Commit, push `main`, and verify local SHA equals `origin/main`.