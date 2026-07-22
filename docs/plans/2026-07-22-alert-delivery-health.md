# Alert Delivery Health Observability Implementation Plan

> **For Hermes:** Use strict test-driven development and implement each vertical slice before the next.

**Goal:** Add read-only, fail-closed delivery-health metrics for the durable alert outbox, expose them through a sanitized CLI and the Dashboard预警中心, and verify them against real SQLite state transitions.

**Architecture:** Compute aggregate health from one read-only SQLite snapshot without loading payloads or mutating/migrating the database. Classify pending rows by retry schedule and lease state using an explicit UTC `now`; expose only bounded aggregate counts and timestamps. A dedicated CLI and Dashboard consume the same store API so operational definitions cannot drift.

**Tech Stack:** Python 3.11+, sqlite3, pandas timestamp validation, unittest, existing vanilla JavaScript Dashboard.

---

### Task 1: Define aggregate delivery-health contract

**Objective:** Return deterministic aggregate metrics from a valid current outbox schema.

**Files:**
- Modify: `alert_store.py`
- Test: `tests/test_alert_store.py`

**Steps:**
1. Write a failing test that creates ready-pending, retry-waiting, active-lease, stale-lease, failed and delivered rows.
2. Require `load_alert_delivery_health(path, now)` to return only aggregate values: total, ready, retry_waiting, active_leases, stale_leases, failed, delivered, oldest_undelivered_at, oldest_undelivered_age_seconds and last_delivered_at.
3. Run the targeted test and verify RED because the API is absent.
4. Implement one read-only `mode=ro` snapshot with fixed static SQL and strict timezone-aware timestamp validation.
5. Run the targeted test and verify GREEN.

### Task 2: Fail closed on invalid time and corrupt state

**Objective:** Ensure health metrics cannot silently misclassify malformed databases.

**Files:**
- Modify: `alert_store.py`
- Test: `tests/test_alert_store.py`

**Steps:**
1. Add failing tests for missing DB, non-finite/invalid `now`, malformed timestamps, impossible lease combinations and unknown delivery status.
2. Define missing DB as a valid empty health snapshot without creating a file.
3. Require malformed existing databases to raise a controlled validation/database error.
4. Implement minimal validation and rerun targeted tests.
5. Verify the database schema and file metadata remain unchanged after reads.

### Task 3: Add sanitized alert-health CLI

**Objective:** Give operators a scriptable aggregate status command without exposing payloads, URLs, tokens, paths or internal errors.

**Files:**
- Create: `alert_health_cli.py`
- Create: `tests/test_alert_health_cli.py`

**Steps:**
1. Write failing CLI tests for successful JSON output, explicit `--db`, fixed UTC time injection and sanitized nonzero failure.
2. Verify RED because the module is absent.
3. Implement a minimal argparse CLI calling the shared store API.
4. Catch expected filesystem, SQLite and validation errors and emit only `{"error":"ALERT_HEALTH_UNAVAILABLE"}` to stderr.
5. Verify targeted tests pass.

### Task 4: Expose health metrics in Dashboard API

**Objective:** Add safe aggregate metrics without expanding the alert row whitelist.

**Files:**
- Modify: `dashboard.py`
- Test: `tests/test_dashboard.py`

**Steps:**
1. Write a failing Dashboard payload test requiring an `alert_health` object.
2. Verify RED.
3. Call the shared health loader with Dashboard's current time and return a fixed aggregate field whitelist.
4. Preserve the existing safe degraded state when the database is corrupt or unavailable.
5. Assert `payload_json`, `last_error`, lease tokens and raw errors never appear in the API.

### Task 5: Render operational health in预警中心

**Objective:** Make retry and lease health visible in the existing read-only visual panel.

**Files:**
- Modify: `web/index.html`
- Modify: `web/assets/dashboard.js`
- Test: `tests/test_dashboard.py`

**Steps:**
1. Add failing static-asset assertions for ready, retry-waiting, active-lease, stale-lease and oldest-backlog labels.
2. Verify RED.
3. Render aggregate cards with `textContent`/`replaceChildren`; do not use `innerHTML`.
4. Keep the wide event table inside its existing horizontal scroll container and bump static asset versions.
5. Run JavaScript syntax and Dashboard tests.

### Task 6: Document and verify end to end

**Objective:** Prove metrics track real claim, retry, stale lease, dead-letter, replay and delivery transitions.

**Files:**
- Modify: `README.md`
- Test: relevant existing test modules

**Steps:**
1. Document metric definitions and CLI examples without credentials.
2. Run all unit tests, Ruff, compileall, JavaScript syntax checks and Git diff checks.
3. Run a real temporary SQLite/Webhook E2E and assert metrics before and after each state transition.
4. Scan staged files for credentials, unsafe SQL interpolation, shell execution, eval/exec, pickle and `innerHTML`.
5. Stage the final diff and request an independent fail-closed review.
6. Commit and push only when review returns `passed=true`, `security_concerns=[]`, and `logic_errors=[]`; verify local and remote SHA match.
