# Security Review

Read-only review performed this sprint. Only low-risk, clearly-justified
fixes were applied directly; everything else is reported here with a
disposition. No credentials were rotated as part of this review.

## Findings

### 1. Tracked secrets — none found

**Check:** `git ls-files | grep -i "\.env$"` and equivalents for `.db`/
`.bak` files.

**Result:** Only `.env.example` and
`whatsapp_shadow_bridge/.env.example` are tracked (templates, no real
values, correct). No real `.env`, no database file, no `.bak` file is
tracked.

**Disposition:** No action needed.

### 2. Logs that could expose secrets — none found

**Check:** Both HTTP servers' `log_message` handlers.

**Result:** `operator_api.py:1200` and `whatsapp_bridge_adapter.py:202`
both override `log_message` to a no-op, specifically to avoid logging
headers/cookies/body content. The Node bridge's `console.error` calls
were checked for accidental secret/token logging — none found.

**Disposition:** No action needed.

### 3. Customer PII in fixtures — none found in tracked/reviewed files

**Check:** grepped tracked source for real-looking phone number
patterns outside the `555` test range; manually reviewed
`scripts/seed_command_center_demo_db.py`'s fixture data (all names/
addresses/phones use `Demo`/`Fake`/`555` markers).

**Result:** Clean. **Not evaluated this round:** the pre-existing
untracked files present at the start of this sprint (`Exports/`,
`CURRENT_STATUS.md`, `PROJECT_FILE_INDEX.md`, `PROJECT_HANDOFF.md`,
`luxor_prototype*.py`, `tests/test_prototype_ui.py`) — out of this
sprint's declared scope (pre-existing, unrelated to the commercial-
readiness work), so this review neither touched nor audited them.
Disclosed here rather than silently skipped.

**Disposition:** Requires owner decision — if those files are intended
to ship or be shared, they should get the same PII/secret review
applied to everything else in this document.

### 4. Hardcoded passwords — none found

**Check:** grepped for `password = "..."`-shaped literals outside
tests/docs/`.env.example`.

**Result:** Clean. Every password/secret comes from an environment
variable.

**Disposition:** No action needed.

### 5. Unsafe default bind address — `operator_api.py` binds `0.0.0.0`

**Check:** grepped for `0.0.0.0` across the codebase.

**Result:** The main CRM HTTP server
(`operator_api.py:1348`, `ThreadingHTTPServer(("0.0.0.0", port), ...)`)
binds to all network interfaces, not loopback-only. By contrast, both
WhatsApp-adjacent servers (`whatsapp_bridge_adapter.py`'s adapter,
`whatsapp_shadow_bridge`'s health server) are explicitly loopback-only
with comments stating they must never be exposed. The CRM does have
password auth, rate-limited login, HttpOnly+SameSite session cookies,
and CSRF protection on mutations — this is not an open door — but on a
machine with a routable/public IP and no firewall, the login page
itself becomes directly reachable from the network without going
through any tunnel's own access control.

**Disposition:** Requires owner decision, not fixed this round.
Changing the bind address to `127.0.0.1` looks low-risk (a
`cloudflared` tunnel run on the same machine reaches the origin service
over loopback regardless of whether the service itself is bound to
`0.0.0.0` or `127.0.0.1`), but it would also cut off any current LAN-
only access pattern (e.g., reaching the CRM from a phone on the same
WiFi via its LAN IP, without a tunnel) if that's something currently
relied on. Flagging for the owner to decide rather than assuming.

### 6. Debug endpoints — none found

**Check:** grepped for `debug = True` / `/debug` routes.

**Result:** No debug mode, no debug endpoint, in either the Python or
Node codebases.

**Disposition:** No action needed.

### 7. Permissive CORS — none found

**Check:** grepped for `Access-Control-Allow-Origin` / CORS middleware.

**Result:** No CORS headers are set anywhere — both the CRM's static
frontend and its API are same-origin only by construction (confirmed
by `operator_ui_v2_connected/js/api.js`'s own comment: "Same-origin
only -- this file never talks to any other host").

**Disposition:** No action needed.

### 8. Generated runtime/session files accidentally tracked — none found

**Check:** `git ls-files` against `.runtime/`, WhatsApp session/allowlist/
dedupe file patterns.

**Result:** Clean — `.gitignore` correctly excludes `.runtime/`,
`whatsapp_shadow_bridge/.env`, `whatsapp_shadow_bridge/*.log`,
`.wwebjs_cache/`, `.wwebjs_auth/`. The only tracked files matching
"session"/"allowlist" in their names are legitimate source files
(`allowlist.js`, its test) — not runtime state.

**Disposition:** No action needed.

### 9. Production database accidentally tracked — none found

**Check:** `git ls-files | grep -i "\.db$"`.

**Result:** Clean — `data/*.db` is gitignored.

**Disposition:** No action needed.

### 10. Backup files accidentally tracked — none found

**Check:** `git ls-files | grep -i "\.bak$"`.

**Result:** Clean. Backups are written outside the repo tree entirely
(`../ai-dispatcher-db-backups/`, a sibling directory), so they can't be
accidentally tracked by this repo's own `.gitignore` at all.

**Disposition:** No action needed.

### 11. Demo data separation — verified, newly enforced this sprint

**Check:** `scripts/seed_command_center_demo_db.py`'s live-database
guard; `scripts/start_demo.ps1`'s isolated database path.

**Result:** The seed script structurally refuses to target
`storage.DB_PATH` (raises `ValueError`, tested in
`tests/test_demo_isolation.py`). The demo launcher always uses
`.runtime\demo\demo.db`.

**Disposition:** No action needed (new tooling built this sprint; see
`docs/DEMO_RUNBOOK.md`).

### 12. Dependency lockfiles — Node has one, Python does not (by design)

**Check:** presence of `package-lock.json` / Python lockfile equivalent.

**Result:** `whatsapp_shadow_bridge/package-lock.json` exists and is
tracked. The Python side uses `requirements.txt` without pinned exact
versions or a lockfile (no `requirements.lock`/`poetry.lock`/
`Pipfile.lock`).

**Disposition:** Requires owner decision. Pinning exact Python
dependency versions (or adopting a lockfile tool) would make installs
more reproducible across machines, but changing `requirements.txt`'s
format is a real workflow decision, not applied this round.

### 13. Obsolete launcher pointing to a retired application

**Check:** `tools/windows_launcher/launcher.py` (customer-facing) vs.
`scripts/start_crm.ps1` (current, canonical).

**Result:** Confirmed via `tools/system_readiness.py`'s new check:
`tools/windows_launcher/launcher.py` still starts
`streamlit run luxor_desk.py` — the retired interface — not
`operator_api.py`, the current CRM. This is customer-facing (unlike
`.claude/launch.json`, a development-only tool convenience file which
also has some stale entries but is never shipped or used by a
customer).

**Disposition:** Requires owner decision (a real code fix — repoint or
retire `tools/windows_launcher/` — judged out of this sprint's declared
scope as unrelated feature-adjacent work, not a documentation/tooling
gap). Already disclosed in
`docs/COMMERCIAL_READINESS.md` category 10,
`docs/INSTALLATION.md`, `docs/KNOWN_LIMITATIONS.md`, and surfaced live
by `tools/system_readiness.py` on every run.

### 14. Session-secret auto-write-on-import — real incident this sprint

**Check:** discovered live, not by static grep — see
`docs/OPERATIONS_RUNBOOK.md`'s "Known operational risk" section for the
full account.

**Result:** `operator_api.py`'s `SESSION_SECRET = _get_or_create_session_secret()`
runs unconditionally at module import time (not gated behind
`if __name__ == "__main__"`). A test written during this sprint that
blanked `OPERATOR_API_SESSION_SECRET` before importing the module
caused it to silently generate and append a second, real
`OPERATOR_API_SESSION_SECRET=` line to the actual repo-root `.env`.
The test itself was fixed to never do this again
(`tests/test_system_readiness.py`), but the underlying auto-write
behavior in `operator_api.py` is unchanged.

**Disposition:** Fixed now (the test that triggered it) / requires
owner decision (the underlying auto-provisioning behavior in
`operator_api.py` itself — a guard rail such as requiring an explicit
opt-in flag, or moving the call behind `if __name__ == "__main__"`, is
recommended but not implemented this sprint, since it touches
security-relevant startup code and the sprint's own safety rules call
for caution there). The duplicate `.env` line from the incident itself
is being cleaned up by the repository owner directly, per their own
choice when this was disclosed mid-sprint.

## Summary

| # | Finding | Disposition |
|---|---|---|
| 1 | Tracked secrets | None found |
| 2 | Secret-exposing logs | None found |
| 3 | Customer PII in fixtures | None found (reviewed files); pre-existing untracked files not evaluated — owner decision |
| 4 | Hardcoded passwords | None found |
| 5 | `operator_api.py` binds `0.0.0.0` | Owner decision |
| 6 | Debug endpoints | None found |
| 7 | Permissive CORS | None found |
| 8 | Tracked runtime/session files | None found |
| 9 | Tracked production DB | None found |
| 10 | Tracked backup files | None found |
| 11 | Demo/production data separation | Verified, enforced |
| 12 | Python dependency lockfile | Owner decision |
| 13 | Stale Windows launcher | Owner decision (code fix, out of scope) |
| 14 | Session-secret auto-write-on-import | Test fixed; underlying behavior — owner decision |
