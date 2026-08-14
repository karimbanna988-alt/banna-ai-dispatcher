# Phone Dispatch Pilot

How to run LUXOR Dispatch from a phone today, what is genuinely
installable, and what is not. Nothing in this document claims a
capability that has not been verified — where something is unverified it
says so.

## Today's phone URL

```
http://192.168.1.105:8512/
```

The IP is this laptop's current Wi-Fi DHCP address. **It can change on
reconnect or router reboot** — re-read it before a session with:

```bash
python -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print('http://%s:8512/'%s.getsockname()[0])"
```

The server already binds `0.0.0.0`, and Windows Firewall already has an
inbound allow rule for `python.exe`, so no network change was made or is
expected to be needed. Phone and laptop must be on the same Wi-Fi.

**No firewall rule, router setting, port forward or public tunnel was
created during this sprint.** If the phone cannot reach the URL, that is
the one owner action to consider — and it should be considered, not
performed blind.

## What is installable today, precisely

| Piece | State |
|---|---|
| Web app manifest | **Written and served correctly** (`application/manifest+json`, verified over HTTP) |
| Standalone display, name, theme colour | Declared in the manifest |
| App icons | **Placeholder only.** Locally generated dev tiles (`scripts/make_dev_icons.py`). No approved brand mark exists — **icon finalization is pending the owner** |
| iOS home-screen metadata | `apple-touch-icon`, `apple-mobile-web-app-capable`, status-bar style, safe-area viewport — all present |
| Service worker (`sw.js`) | **Written and served, runtime-unverified.** See below |
| Installable as a real app | **NO — not over LAN http** |

### Why it is not installable today

Installation and service workers both require a **secure context**:
HTTPS, or `localhost`. `http://192.168.1.105:8512/` is neither from the
phone's point of view. So today:

- Safari will open the console fine, and "Add to Home Screen" will
  create a bookmark-style launcher.
- It will **not** be a true installed PWA, and the service worker will
  **not** register. `console.js` checks `window.isSecureContext` and
  skips registration rather than logging an error nobody can act on.

That is expected and correct for a single-machine LAN pilot. A true
installable phone app needs **Stage 2** below.

### Service worker: what was and was not verified

`sw.js` is written to a strict contract — application shell only, a hard
bail-out on anything under `/api/`, no background sync, no offline write
queue, no IndexedDB (all pinned by tests in
`tests/test_phone_console_ui.py`).

Its **runtime behaviour is unverified.** Registration could not be
exercised: in the local browser surface used for testing, registration
fails with the same generic error for a script that exists and one that
does not, which means service workers are unavailable there, not that
`sw.js` is broken. It will first genuinely run under Stage 2.

Until then the console works as an ordinary web page, which is the
honest state: **online-only, always live, never cached.**

## Deployment plan — two stages, nothing more

### Stage 1 — controlled single-machine phone pilot (now)

- Laptop runs 8512 (console + embedded 8513 adapter) and 8514 (bridge).
- Phone uses the LAN URL above, on the same Wi-Fi.
- Read/act from the phone; the laptop stays the only machine.
- No HTTPS, no install, no offline. Losing Wi-Fi shows **OFFLINE**.

Limits to accept: the phone must be on home Wi-Fi, and the session drops
whenever `operator_api.py` restarts (sessions are in-memory by design).

### Stage 2 — reviewed HTTPS deployment (before real remote use)

Needed for: install-to-home-screen, service worker, use away from home
Wi-Fi, and `Secure` session cookies.

Requires an owner decision and a security review covering at minimum:
a real hostname and TLS certificate, where the box lives, how the
operator password is managed, and what is exposed to the internet.

**Not in scope and not wanted:** Kubernetes, Docker orchestration, cloud
microservices, or any enterprise infrastructure. The first deployment
stays one machine and one process.

## What the pilot measures

Derived from data the system already records — no analytics project.

- jobs handled
- median time from intake to dispatcher action
- recommendation accepted vs overridden
- HUMAN_REVIEW rate
- unmatched / ambiguous correlation rate
- missed or duplicated jobs
- closing completeness (amount + payment method present)
- operator copy actions
- actual serious errors

The question these answer: is dispatch **faster, safer and more
reliable** with LUXOR than without it? Nothing else is worth counting.

## Owner iPhone acceptance — five minutes

Claude cannot pass this from desktop emulation. Only the owner can.

1. On the iPhone, on home Wi-Fi, open `http://192.168.1.105:8512/`.
2. Sign in. The login card must **disappear** and the board appear.
3. Tap **TO DISPATCH → IN PROGRESS → TO CLOSE**. Each is one tap and
   fills the screen; counts sit next to each label.
4. Tap a job card. The detail sheet opens full screen.
5. Tap the bottom button (**Dispatch & copy**, **Copy company update**,
   or **Copy closing**). A toast confirms the copy.
6. Scroll the whole page. Nothing may scroll sideways at any point.
7. Switch to WhatsApp, paste, then come back. The same job must still
   be open.
8. Lock the phone, unlock, reopen. Confirm what is on screen matches the
   server, not a stale snapshot.
9. Pull down / refresh. Sign-in may be requested again — expected.
10. Turn Wi-Fi off for ~15 seconds. The red **OFFLINE** bar must appear
    and the board must dim. Turn Wi-Fi back on; it must clear by itself.

Step 10 is the most important one. If a stale board ever looks live,
stop the pilot and report it.

**The owner confirms acceptance. It is not claimed in advance.**
