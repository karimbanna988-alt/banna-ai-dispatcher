# Security Audit

Focused review of `operator_api.py` (the connected CRM's JSON API +
static file server) against the 20-point checklist below. Each item
lists severity, evidence (file:line), impact, fix (if any was made this
pass), and how to verify. Nothing here is theoretical where a concrete
file:line citation is given — every claim was checked against the
actual code, not assumed from a prior sprint's documentation.

Two items this session was explicitly told **not** to touch are noted
as such, not silently omitted: the duplicate `OPERATOR_API_SESSION_SECRET`
lines in `.env`, and `.env` itself.

## Findings

### 1. Client-facing 500 errors echoed the raw exception message (MEDIUM — fixed)

**Evidence:** `operator_api.py`'s `Handler._dispatch()`, the generic
`except Exception` branch, previously did
`self._send_json(500, {"error": f"Internal error: {exc}"})`.

**Impact:** an unhandled exception's `str()` can contain a file path, a
raw SQL fragment, or other internal detail never meant to leave the
process — classic information-disclosure via verbose error messages.
`ApiError` (the sanctioned, deliberate way to send a specific message
to the client) was unaffected; this only applied to genuinely
*unexpected* exceptions.

**Fix:** the client now always receives a generic `"Internal error."`;
the full exception and traceback still go to the server log via the
existing `traceback.print_exc()`, unchanged.

**Verify:** `tests/test_operator_api.py::test_internal_error_response_is_generic_not_the_raw_exception`.

### 2. No Content-Security-Policy header (LOW/MEDIUM — fixed, defense in depth)

**Evidence:** `SECURITY_HEADERS` previously had no `Content-Security-Policy`
entry.

**Impact:** no CSP meant a successful XSS injection (none was found —
see #3) would have had an unrestricted blast radius (arbitrary inline
script, remote script/style loading). Defense in depth, not a response
to a found vulnerability.

**Fix:** added `default-src 'self'; script-src 'self'; style-src 'self'
'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors
'none'; base-uri 'self'; form-action 'self'`. `script-src 'self'` (no
`'unsafe-inline'`) is safe because `index.html` has zero inline
`<script>` tags — both `app.js` and `api.js` are external files.
`style-src` keeps `'unsafe-inline'` because the UI uses inline
`style="..."` attributes extensively; eliminating those is a real
refactor, out of scope here, so this stays a pragmatic middle ground
rather than blocking the app.

**Verify:** live-verified in the browser (login + full navigation, zero
console CSP violations) and `tests/test_operator_api.py::test_content_security_policy_header_present`.

### 3. XSS / unsafe HTML interpolation — audited, no finding

**Evidence:** every `<pre class="msg-block">` and `detail-meta`
rendering site in `operator_ui_v2_connected/js/app.js` that inserts
free text (raw incoming messages, technician-typed updates, customer
name/address/notes, prepared company replies) passes through `esc()`
first — checked via targeted grep across every occurrence of
`prepared_text`, `luxor_message`, `raw_update`, `original_message_masked`,
`customer`, `notes`, `vehicle`, `address` in the file; zero unescaped
occurrences found.

**No fix needed.** The CSP addition above is additional defense in
depth on top of this, not a substitute for it.

### 4. SQL parameterization — audited, no finding

**Evidence:** two f-string-constructed SQL statements exist in
`storage.py` (`_get_shadow_episode_row`'s `WHERE {where_clause}` at
line ~2899, `update_shadow_episode`'s `SET {...}` at line ~3010). Both
were traced to their call sites: `where_clause` is always one of 4
fixed, hardcoded string literals in the code (never derived from
input); the `SET` clause's column names are validated against
`_SHADOW_EPISODE_UPDATABLE_FIELDS` (raises `ValueError` on any unknown
key) *before* the f-string runs. All actual *values* in both go through
parameterized `?` placeholders. Neither is an injection vector.

**No fix needed.**

### 5. Session/CSRF/cookie handling — mechanism audited clean; one real usability/reliability bug found and fixed (MEDIUM — fixed)

**Evidence (mechanism, unchanged):** `HttpOnly`, `SameSite=Lax`, and
conditional `Secure` (when `X-Forwarded-Proto: https`) on the session
cookie; CSRF enforced via a per-session HMAC token checked with
`hmac.compare_digest` on every non-GET, non-`skip_csrf` route; the login
password itself is compared with `hmac.compare_digest` (constant-time);
session tokens are invalidated server-side on logout (not just
cookie-cleared — see `test_logout_invalidates_session_not_just_clears_cookie`,
pre-existing). None of this changed and none of it was wrong.

**Bug found in a later sprint (Operator Speed Implementation), while
live-testing unrelated Fast Path changes in the browser:** `app.js`'s
`boot()` calls `Api.checkSession()` (`GET /api/me`), never `Api.login()`,
whenever the session cookie is already valid — the common case on an
ordinary page reload. `checkSession()` never populated the frontend's
in-memory CSRF token (only `login()` did), so on that path every
mutating request 403'd with "Missing or invalid CSRF token" until the
operator explicitly logged out and back in. This is not an
authentication bypass or a CSRF *weakness* — the gate itself was working
exactly as designed, correctly rejecting requests with no valid token —
it's the opposite failure mode: a reliability bug that silently broke
every write path after any browser reload, which is a direct hit to
dispatch speed (the entire point of that sprint) and confusing enough
in practice (a cryptic technical error toast, not a clear "please log in
again") that it's worth tracking here alongside the CSRF mechanism it's
adjacent to.

**Fix:** `route_me` now also returns `csrf_token_for(session_token)` —
the identical pure function `route_login` already used, re-derived from
a session token this request already proved possession of via its
cookie, so this creates no new secret and no new capability. `checkSession()`
stores it exactly like `login()` does.

**Verify:** `tests/test_operator_api.py::test_api_me_returns_a_working_csrf_token_for_an_already_valid_session`,
`::test_api_me_csrf_token_matches_the_one_login_issued`. Live-verified in
the browser: simulated a fresh reload (cleared the in-memory token,
called `checkSession()`, confirmed a subsequent write succeeded using
only the token that call returned).

### 6. Login brute-force throttling — audited, no finding

**Evidence:** `LOGIN_MAX_ATTEMPTS = 5`, enforced per-IP via
`_check_login_rate_limit`/`_record_failed_login` before the password is
even compared; a rate-limited client is rejected with 429 even for the
*correct* password (`test_login_rate_limited_after_repeated_failures`,
pre-existing).

**No fix needed.**

### 7. Static-file path traversal — audited, no finding

**Evidence:** `_serve_static()` resolves the requested path and checks
`candidate.relative_to(STATIC_DIR.resolve())`, rejecting with 403 on
any `ValueError` (i.e., any path that resolves outside `STATIC_DIR`) —
canonical-path comparison, not string prefix matching, so `..`
sequences and symlink escapes are both caught.

**No fix needed.**

### 8. API authorization consistency — audited, no finding

**Evidence:** exactly 2 of ~50 routes are marked `"public": True`
(`/api/login` — must be, to log in at all; `/api/demo_status` —
deliberately exposes only a boolean so the login screen itself can show
a DEMO MODE banner before authentication). Every other route requires
`_require_auth()` by default — an allowlist of public routes, not a
denylist of protected ones, which is the safer default direction.

**No fix needed.**

### 9. Server bind address (MEDIUM — documented, not changed)

**Evidence:** `run()` binds `ThreadingHTTPServer(("0.0.0.0", port), Handler)`
— all network interfaces, not just loopback. The separate WhatsApp
bridge adapter explicitly binds loopback-only with a comment
("never reachable through the public tunnel") — the main API server has
no equivalent comment explaining why `0.0.0.0` was chosen over `127.0.0.1`.

**Impact:** anyone on the same LAN (or, without a host firewall, more
broadly) can reach the login endpoint and full API surface directly,
not just through whatever tunnel is set up. The app's own auth layer
(password + rate limiting) still applies, so this is not a full bypass
— but it's a real, avoidable increase in attack surface if the intent
was always "reachable only via the Cloudflare tunnel," since
`cloudflared`'s local proxy target only needs `127.0.0.1`.

**Not changed this session.** This affects the real, live server's
startup behavior — a deployment-affecting change I'm not making
unilaterally without confirming whether `0.0.0.0` was an intentional
choice (e.g., LAN access from another device was wanted at some point).
**Recommendation:** if LAN access was never actually required, change
to `("127.0.0.1", port)` — a one-line change, but the owner should
confirm intent first.

### 10. `X-Forwarded-Proto` trust for the `Secure` cookie flag (LOW — documented, not changed)

**Evidence:** `_is_https()` trusts `self.headers.get("X-Forwarded-Proto")`
verbatim, with no check that the request actually came through a
trusted reverse proxy that sets this header itself (vs. a client
setting it directly).

**Impact:** low. The only thing gated by this is whether the `Secure`
flag is added to the session cookie. A client spoofing this header on a
genuine plain-HTTP connection would cause the server to mark the cookie
`Secure`, which browsers then refuse to store over that same plain-HTTP
connection — a self-defeating spoof (breaks the attacker's own session,
not a way to steal someone else's). Not exploitable in the way
`X-Forwarded-Proto` trust issues usually are (e.g., request smuggling
around a WAF), because nothing else in this codebase branches on it.

**Not changed.** Documented for completeness; real-world impact is
negligible given this server's actual deployment shape (Cloudflare
tunnel terminates TLS and is the only realistic source of this header
in practice).

### 11. Demo/production isolation, fail-closed DB, test network isolation — already covered by prior sprints, re-verified

**Evidence:** `dispatcher.db_safety.resolve_db_path()` (fail-closed for
demo/test modes), the `conftest.py` session-wide `_real_database_untouched_tripwire`
and `_no_ambient_quo_api_key` autouse fixtures, and the WhatsApp
bridge's structural receive-only guarantee (no `whatsapp-web.js`-style
send-capable import anywhere in this repo) — all pre-existing work from
earlier sprints this session, re-verified still in place and still
passing their own tests.

**No fix needed.**

### 12. Secret exposure in logs — audited, no finding

**Evidence:** every place a secret is generated
(`_get_or_create_session_secret`, the WhatsApp adapter secret) prints
only a confirmation message ("value not printed"), never the value
itself; `test_no_password_or_session_secret_ever_logged` (pre-existing)
asserts this at the HTTP-server level across both a failed and a
successful login.

**No fix needed.**

### 13. Backup permissions/documentation — not reviewed this pass

`scripts/backup_database.py` was not audited for file permissions on
the produced backup (e.g., whether it's created world-readable on a
multi-user machine). Out of scope for this pass; flagged for a future
one.

### 14. Duplicate `OPERATOR_API_SESSION_SECRET` in `.env` — explicitly out of scope this sprint

Known, previously disclosed (see earlier sprint reports). This
session's brief explicitly says "do not resolve the duplicate
session-secret issue" — not touched, not re-investigated.

## Summary

| # | Item | Severity | Status |
|---|------|----------|--------|
| 1 | Verbose 500 error to client | MEDIUM | Fixed |
| 2 | No CSP header | LOW/MEDIUM | Fixed (defense in depth) |
| 3 | XSS escaping | — | Audited, no finding |
| 4 | SQL parameterization | — | Audited, no finding |
| 5 | Session/CSRF/cookies | MEDIUM | Mechanism clean; reload-loses-CSRF-token bug found & fixed (Operator Speed sprint) |
| 6 | Login throttling | — | Audited, no finding |
| 7 | Path traversal | — | Audited, no finding |
| 8 | Route authorization | — | Audited, no finding |
| 9 | 0.0.0.0 bind address | MEDIUM | Documented, needs owner decision |
| 10 | X-Forwarded-Proto trust | LOW | Documented, negligible impact |
| 11 | Demo/test isolation | — | Re-verified, already solid |
| 12 | Secret log exposure | — | Audited, no finding |
| 13 | Backup file permissions | — | Not reviewed |
| 14 | Duplicate session secret | — | Explicitly out of scope |

Two genuine, safely-scoped fixes shipped this pass (#1, #2). One real
finding (#9) documented but deliberately not auto-fixed because it
changes live server startup behavior.

**Update (Operator Speed Implementation sprint):** a third real,
safely-scoped fix shipped — #5's reload-loses-CSRF-token bug, found
while live-testing unrelated speed changes and fixed in the same
sprint rather than left for later, since a broken write path after any
page reload directly undermines that sprint's entire goal.
