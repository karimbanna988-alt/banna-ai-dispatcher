# Desktop App Options -- Recommendation (Document Only, Not Implemented)

This is a comparison of four ways to make the dispatcher feel more like
a normal Windows/phone app, written after inspecting the current
architecture: a Streamlit server process (`luxor_desk.py`) rendering to
a browser, reached locally at `http://localhost:8506` or remotely
through a `cloudflared` quick tunnel. Nothing in this document was
implemented -- it's a recommendation to act on later, not a plan
executed now.

## A. Browser shortcut / installed website (a `.url` file, or "Install
as app" from Chrome/Edge's menu)

- **User experience:** A desktop icon that opens straight to the
  dispatcher's current tunnel URL (or, once port-forwarded, a stable
  local-network address) in a browser window with no address bar --
  looks close to a real app.
- **Python/Streamlit underneath?** Yes, unchanged -- this is purely a
  bookmark/shortcut to the existing server.
- **Offline/local-network behavior:** Requires `luxor_desk.py` to
  already be running somewhere reachable (locally or via the tunnel).
  No offline mode -- it's a thin client to a live server, same as today.
  On the same LAN, could point at `http://<machine-ip>:8506` directly
  without needing a tunnel at all.
- **Phone access:** Already works today via the tunnel URL. An
  "installed" bookmark works the same way on a phone (Chrome's "Add to
  Home Screen").
- **Packaging/update complexity:** Essentially zero. A shortcut file
  never goes stale in a way that needs "shipping an update" -- the
  server side just needs to keep running.
- **Security implications:** Same as today's setup exactly -- the
  tunnel URL is the only new surface area, and it already exists.
- **Timing:** Can happen immediately, in parallel with anything else --
  it changes nothing about the app itself.

## B. One-click local launcher (what this task just built)

- **User experience:** Double-click an icon, the server starts if it
  isn't already running, the browser opens automatically. Still a
  browser tab, not a separate app window.
- **Python/Streamlit underneath?** Yes, unchanged -- the launcher only
  starts/stops the existing `streamlit run luxor_desk.py` process and a
  `cloudflared` tunnel.
- **Offline/local-network behavior:** Same as option A once running;
  the launcher's only job is making "already running" true without a
  terminal.
- **Phone access:** Unchanged -- still via the tunnel URL the launcher
  detects and can display.
- **Packaging/update complexity:** Low -- a handful of small scripts,
  no build step, no installer. Updating the app is still just editing
  the Python source in place.
- **Security implications:** Same as option A; the launcher adds no new
  network surface, it only manages local OS processes.
- **Timing:** Already done (this task). Reasonable to keep using
  indefinitely -- it doesn't block or conflict with C or D happening
  later.

## C. Desktop wrapper using `pywebview` (or an equivalent lightweight
webview)

- **User experience:** A real, borderless application window (no
  visible browser chrome at all), a taskbar icon, feels like a native
  Windows app while still rendering the same Streamlit UI inside an
  embedded webview.
- **Python/Streamlit underneath?** Yes, unchanged -- `pywebview` (or
  similar) just launches `luxor_desk.py` as a subprocess (exactly like
  the launcher already does) and points its own window at
  `http://localhost:<port>` instead of a browser tab.
- **Offline/local-network behavior:** Same as options A/B -- still
  needs the local Streamlit server running; a webview doesn't remove
  that requirement, it just hides the browser.
- **Phone access:** None by itself -- `pywebview` produces a Windows
  desktop window, not a phone app. Phone access would still go through
  the existing tunnel URL in a normal mobile browser, same as today.
- **Packaging/update complexity:** Moderate -- needs a build step
  (e.g. PyInstaller) to produce a distributable `.exe`, plus a way to
  get updates to the one machine running it (manual re-install, or a
  simple version-check-and-replace script). Meaningfully more moving
  parts than A/B, but still far short of a full app-store pipeline.
- **Security implications:** Similar to A/B (still a local server +
  local UI), plus the added surface of a packaged executable that needs
  to be trusted/signed if distributed beyond one machine.
- **Timing:** Reasonable *after* the operator workflow itself (New
  Jobs, Active Jobs, Closings, etc.) is fully approved and stable --
  wrapping a UI that's still actively changing means re-testing the
  wrapper every time the UI does, for a purely cosmetic win at this
  stage.

## D. Full native/mobile rewrite (a real iOS/Android app, or a native
Windows app not embedding a webview at all)

- **User experience:** The best possible -- true native performance,
  push notifications, offline caching, App Store/Play Store
  distribution, home-screen presence without a browser or tunnel at
  all.
- **Python/Streamlit underneath?** No -- this replaces the UI layer
  entirely. The backend (`DispatcherService`, storage, Quo ingestion,
  Shadow Mode) could stay as-is behind a real API, but everything
  currently in `luxor_desk_pages/*.py` would need to be rebuilt in a
  native UI framework.
- **Offline/local-network behavior:** Could genuinely support offline
  queuing/sync if built for it -- the only option here that can.
- **Phone access:** First-class, by design -- this is the option built
  specifically for phones.
- **Packaging/update complexity:** High -- app store review cycles,
  platform-specific builds, ongoing maintenance across iOS/Android/
  Windows, a real release process.
- **Security implications:** Needs a proper authenticated API boundary
  (today's password-gated Streamlit session model doesn't translate
  directly), token-based auth, and everything else that comes with a
  real multi-client backend.
- **Timing:** Only worth starting once the operator workflow itself has
  been fully proven out in production for a while -- this is a
  multi-week-or-more investment that should target a *stable, already-
  validated* workflow, not one still being reshaped by phone-inspection
  feedback (as this session's New Jobs rebuild just was).

## Recommendation

**Stay on B (the one-click launcher just built) for now.** It already
solves the actual friction (a non-technical user needing a terminal)
at essentially zero cost, doesn't touch the app itself, and doesn't
foreclose any later option -- A, C, and D are all still available
later, and C in particular reuses B's exact process-management logic
almost unchanged. Move to C only once the operator workflow (New Jobs,
Active Jobs, Closings, etc.) has stopped changing week-to-week; jumping
to C or D earlier would mean re-wrapping or re-building around a UI
that's still actively being corrected from real phone feedback, which
is wasted effort until the workflow itself is settled.
