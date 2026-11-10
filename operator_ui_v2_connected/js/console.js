/* LUXOR Dispatch Console -- phone-first operator product.

   The CRM is the memory; this is the product. Three operational
   stages -- TO DISPATCH / IN PROGRESS / TO CLOSE -- built entirely
   from the existing API routes (/api/new_jobs, /api/active,
   /api/closings). No new backend, no new framework.

   On a phone exactly one stage owns the viewport and the switcher above
   it is one tap. On a desktop all three columns show at once. Same code,
   same data; the CSS decides which shape applies.

   SAFETY: this file cannot transmit anything. It prepares text and
   copies it to the clipboard; a human pastes it into WhatsApp. Every
   state change goes through the same audited POST routes the previous
   UI used. There is no code path here that transmits a WhatsApp message
   or writes to Quo.

   BUSINESS LOGIC: none lives here. Waiting-state wording comes from the
   server (`operator_state`, computed by dispatcher/operator_stage.py
   over the shared message_patterns) so the phone can never disagree
   with the dispatcher brain about what a technician's message meant. */

(function () {
  "use strict";

  const POLL_MS = 5000;
  // The main board is CURRENT operational work. Pre-commit messages
  // older than this belong to Inbox Review in Admin, not to a live
  // dispatch board -- a 300-hour-old unresolved message must never
  // visually compete with something that arrived 30 seconds ago.
  const DISPATCH_MAX_AGE_MIN = 24 * 60;
  // One failed poll is a blip; two in a row is a connectivity problem
  // worth telling the operator about.
  const OFFLINE_AFTER_FAILURES = 2;

  const state = {
    dispatch: [], progress: [], close: [],
    progressStale: [],   // old open jobs, signposted not hidden
    candidates: [],      // WhatsApp jobs understood but not committed
    candidateReviews: [],// held back by a safety rule; visible, not actionable
    stage: "dispatch",    // which stage owns the phone viewport
    selected: null,       // { col, id }
    detail: null,         // resolved detail payload
    order: [],            // flat nav order for J/K
    paused: false,        // suspend polling while a detail panel is open
    lastPrepared: {},     // messageId -> prepared technician text
    failures: 0,          // consecutive failed polls
    offline: false,
    lastGood: null,       // Date of the last successful load
    screen: "today",      // which bottom-nav screen owns the viewport
    crm: null,            // last /api/crm/summary payload
    groups: null,         // last /api/groups payload
    groupFilter: "needs",
    jobsQuery: { range: "all", status: "", company: "", area: "", q: "" },
    reportRange: "today",
    candidatesAll: [],    // every evaluated candidate, for the shadow session
    quoShadow: { episodes: [] }, // read-only AGN/Lucky Quo -> WhatsApp episodes
    quoShadowReplay: false,
    liveOpen: false,      // Live screen is the visible one
    apprenticeOpen: false,// Apprentice screen is the visible one
    health: null,         // last /api/live_health payload
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function toast(msg) {
    const t = $("toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 2200);
  }

  /* ------------------------------------------------ time formatting --
     ONE formatter for every visible clock time in this UI. The operator
     reads 12-hour time, so "21:50" is wrong on their screen -- but this
     is a DISPLAY concern only. Nothing here touches stored values: the
     API keeps sending ISO/UTC, ordering and correlation still run on the
     raw instant, and this function is never in that path. Same instant,
     same timezone, different rendering.

     Seconds are omitted by default. An operator deciding what to do next
     does not need them; a debug view asks for them explicitly. */
  // The one business clock. kpi_read_model and crm_summary both call
  // this zone operational_timezone; the phone must not invent a second.
  const OPERATIONAL_TZ = "America/New_York";

  function formatDisplayTime(value, opts) {
    if (value == null || value === "") return "";
    const t = value instanceof Date ? value : new Date(value);
    if (isNaN(t.getTime())) return "";
    const o = { hour: "numeric", minute: "2-digit", hour12: true, timeZone: OPERATIONAL_TZ };
    if (opts && opts.seconds) o.second = "2-digit";
    // en-US pins the AM/PM rendering regardless of device locale, so the
    // owner never sees a 24-hour clock because of a phone setting.
    // The TIMEZONE is pinned for a harder reason: the server decides
    // which day a job belongs to in America/New_York, and a clock drawn
    // in the device's zone disagreed with it. On a laptop two hours
    // ahead, a 6:32am New York job read "8:32 AM", and near midnight a
    // job could show one date while being counted under another.
    return t.toLocaleTimeString("en-US", o);
  }

  /* ------------------------------------------------- display labels --
     The backend speaks in enums, and it should keep doing so -- they are
     stable identifiers that tests, correlation and the API all depend
     on. But "NEXT: HUMAN_REVIEW_NEEDED" is not a sentence an operator
     should have to decode mid-shift.

     This maps machine values to human wording for DISPLAY ONLY. Nothing
     here is ever sent back, compared, or stored: an unmapped value falls
     through unchanged, so a new backend state degrades to its raw name
     rather than vanishing from the screen. */
  const DISPLAY_LABELS = {
    "HUMAN_REVIEW_NEEDED": "Needs human review",
    "COMPANY UNRESOLVED": "Company needs confirmation",
    "ORDER UNDETERMINED": "Message order unclear",
    "REPOST": "Posted again after dispatch",
    "GROUP MAPPING REQUIRED": "Group not mapped yet",
    "WAITING FOR HUMAN DISPATCH": "Ready to dispatch",
    "ALREADY DISPATCHED": "Already dispatched",
    "REPOST AFTER PRIOR DISPATCH": "Posted again after dispatch",
    "CLOSING OBSERVED": "Closing seen",
    "MAPPING_REQUIRED": "Group not mapped yet",
    "open": "Open",
    "completed": "Completed",
    "needs_attention": "Attention",
  };

  function displayLabel(value) {
    if (value == null || value === "") return "";
    const raw = String(value);
    if (DISPLAY_LABELS[raw]) return DISPLAY_LABELS[raw];
    const upper = raw.toUpperCase();
    if (DISPLAY_LABELS[upper]) return DISPLAY_LABELS[upper];
    // Unknown machine value: make it readable without inventing meaning.
    return /^[A-Z0-9_ ]+$/.test(raw)
      ? raw.charAt(0) + raw.slice(1).toLowerCase().replace(/_/g, " ")
      : raw;
  }

  /* ------------------------------------------------- ACTIVE SHADOW --
     Banna runs the SAME deterministic pipeline the console already uses
     -- parser, company resolver, correlation, routing -- and shows the
     decision it WOULD take on real traffic. It is called active because
     the analysis genuinely runs on every captured job, and shadow
     because nothing leaves the building: there is no send path in this
     file, and the proposed text is display-only until a human copies it.

     No model is involved. Calling an LLM here would make the demo look
     clever while replacing the software that actually makes the
     decision, which is the opposite of what this is for.

     The session starts when the console loads. Jobs captured BEFORE
     that point still inform correlation (they are real evidence), but
     the visible shadow session follows current work rather than
     replaying months of history. */
  const SHADOW_SESSION_START = new Date();

  function shadowDecision(c) {
    const s = c.state || c.status;
    if (c.review_reason || s === "HUMAN_REVIEW_NEEDED")
      return { label: "Human review", tone: "rev" };
    if (s === "ALREADY DISPATCHED") return { label: "Already dispatched", tone: "done" };
    if (s === "REPOST AFTER PRIOR DISPATCH") return { label: "Human review", tone: "rev" };
    if (s === "CLOSING OBSERVED") return { label: "Waiting for closing", tone: "done" };
    if (s === "GROUP MAPPING REQUIRED") return { label: "Cannot route", tone: "na" };
    if (s === "WAITING_FOR_HUMAN_DISPATCH" || s === "WAITING FOR HUMAN DISPATCH") {
      return (c.recommended_technician || c.recommended) && (c.prepared_message || c.proposed_message)
        ? { label: "Dispatch", tone: "act" }
        : { label: "Cannot route", tone: "na" };
    }
    if (s === "WAITING_FOR_TECHNICIAN_ACK") return { label: "Human dispatch observed", tone: "done" };
    if (s === "TECHNICIAN_ACKNOWLEDGED") return { label: "Technician acknowledged", tone: "done" };
    if (s === "HUMAN_DISPATCH_OBSERVED") return { label: "Human dispatch observed", tone: "done" };
    if (s === "HUMAN_DISPATCH_AMBIGUOUS" || s === "ACKNOWLEDGEMENT_AMBIGUOUS")
      return { label: "Human review", tone: "rev" };
    return { label: displayLabel(s), tone: "na" };
  }

  // The API already enforces the process-session event-time boundary.
  // This display layer only enforces the AGN/Lucky product scope and
  // deduplicates episode ids; it must not hide a job that arrived after
  // the server session began but just before this browser tab opened.
  // The session the SERVER is observing, not when this page happened to
  // load. A refresh used to start a brand new window and throw away the
  // comparisons already collected.
  function shadowSessionStart() {
    const fromServer = state.quoShadow && state.quoShadow.session_started_at;
    const parsed = fromServer ? new Date(fromServer) : null;
    return parsed && !isNaN(parsed.getTime()) ? parsed : SHADOW_SESSION_START;
  }

  // Shadow covers BOTH intake channels. Quo episodes arrive already
  // shaped as episodes; a WhatsApp job is the same thing in a different
  // wrapper -- it carries the recommendation, the observed destination
  // and the server's own verdict, which is everything comparison()
  // reads. Showing only Quo meant every WhatsApp job Banna reasoned
  // about was learned from in private and never displayed.
  function whatsappShadowJobs(since) {
    const all = (state.candidates || []).concat(state.candidateReviews || []);
    const out = [];
    for (const c of all) {
      const when = new Date(c.event_at || c.received_at || 0);
      if (isNaN(when.getTime()) || when < since) continue;
      out.push({
        episode_id: "wa:" + c.message_id,
        channel: "WhatsApp",
        company: c.source || c.company || null,
        source: c.source || null,
        ticket: c.ticket || null,
        service: c.service || null,
        area: [c.city, c.state].filter(Boolean).join(", ") || null,
        received_at: c.event_at || c.received_at || null,
        recommended_technician: c.recommended || null,
        actual_destination: c.actual_destination || null,
        routing_match: c.routing_match || null,
        decision_reason: (c.why && c.why[0]) || c.detected || null,
        decision_confidence: c.confidence || null,
        state: c.status || null,
        review_reason: c.review_reason || null,
      });
    }
    return out;
  }

  function shadowJobs() {
    const since = shadowSessionStart();
    const quo = ((state.quoShadow && state.quoShadow.episodes) || []).map(
      (e) => Object.assign({ channel: "Quo" }, e));
    const seen = new Set();
    const out = [];
    for (const c of quo.concat(whatsappShadowJobs(since))) {
      if (!c || seen.has(c.episode_id)) continue;
      // Quo intake is AGN/Lucky only; WhatsApp carries every company,
      // so the company filter applies to the Quo half alone.
      if (c.channel === "Quo" && !["AGN", "Lucky"].includes(c.company || c.source)) continue;
      seen.add(c.episode_id);
      out.push(c);
    }
    out.sort((a, b) => String(b.received_at || "").localeCompare(String(a.received_at || "")));
    return out;
  }

  // What the human actually did, taken from evidence the system already
  // observes read-only: messages the owner sent by hand from the paired
  // account, plus sightings in confirmed technician groups.
  function humanAction(c) {
    // `actual_destination` is where the job was ACTUALLY seen going --
    // derived server-side from messages the owner sent by hand and from
    // sightings in confirmed technician groups. It is the human's real
    // decision, observed read-only, never asked for.
    const dest = c.actual_destination;
    if (!dest) return null;
    return { technician: dest, at: c.human_dispatch_timestamp || null,
             verified: c.routing_match || null };
  }

  function comparison(c) {
    const human = humanAction(c);
    if (!human) return { state: "waiting", text: "Waiting for human action" };
    const banna = c.recommended_technician || c.recommended || null;
    // The server already compared its own recommendation against the
    // observed destination; trust that verdict rather than re-deriving a
    // second opinion here.
    if (human.verified === "MATCH") return { state: "match", text: "Match" };
    if (human.verified === "DIFFERENT") return { state: "diff", text: "Different destination" };
    if (human.verified === "UNDETERMINED")
      return { state: "waiting", text: "Outgoing evidence is ambiguous" };
    if (!banna) {
      return { state: "waiting",
               text: "Human dispatched " + human.technician + " · Banna had no recommendation to compare" };
    }
    return { state: "waiting", text: "Waiting for comparable human evidence" };
  }

  /* ------------------------------------------------------ day/night --
     Two themes on one clock. The dispatcher works US hours, and the CRM
     already reports its business day in America/New_York, so the theme
     follows that same clock rather than the laptop's timezone -- the
     machine currently runs UTC+3, where a device-local switch would turn
     the screen dark in the middle of the shift.

     Day starts at 6am so the bright theme is already up before an 8am
     start; night starts at 6pm. Colour MEANING never changes between
     them, only surface and contrast, so nothing has to be relearned. */
  const DAY_STARTS_HOUR = 6;
  const NIGHT_STARTS_HOUR = 18;

  function shiftHour(now) {
    // Hour of day in the operational timezone, whatever the device is set to.
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", hour: "numeric", hour12: false,
    }).formatToParts(now || new Date());
    const hour = parts.find((p) => p.type === "hour");
    return hour ? parseInt(hour.value, 10) % 24 : new Date().getHours();
  }

  function themeForHour(hour) {
    return (hour >= DAY_STARTS_HOUR && hour < NIGHT_STARTS_HOUR) ? "day" : "night";
  }

  function applyTheme() {
    const theme = themeForHour(shiftHour());
    const root = document.documentElement;
    if (root.getAttribute("data-theme") !== theme) {
      root.setAttribute("data-theme", theme);
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute("content", theme === "night" ? "#11151B" : "#F2F4F7");
    }
    return theme;
  }

  /* ------------------------------------------------------- palettes --
     The owner picks the look; we never pick for them. Each palette
     redefines colour TOKENS only, so semantics are identical across all
     of them -- green still means done, amber still means a decision is
     owed. "auto" keeps the existing day/night pair. */
  const PALETTES = [
    { id: "auto", name: "Auto", note: "Bright by day, dark at night", a: "#F2F4F7", b: "#11151B" },
    { id: "daylight", name: "Daylight", note: "White cards, warm accent", a: "#FFFFFF", b: "#E2663B" },
    { id: "midnight", name: "Midnight", note: "Dark, low glare", a: "#161B23", b: "#6FB6A3" },
    { id: "parchment", name: "Parchment", note: "Warm stone and sage", a: "#F1ECE4", b: "#5D7462" },
    { id: "steel", name: "Steel", note: "Cool grey, strong blue", a: "#FFFFFF", b: "#1D4ED8" },
    { id: "graphite", name: "Graphite", note: "Near-black, amber", a: "#16181D", b: "#D9A14A" },
  ];

  function savedPalette() {
    try { return localStorage.getItem("banna.palette") || "auto"; } catch (e) { return "auto"; }
  }

  function applyPalette(id) {
    const root = document.documentElement;
    if (id && id !== "auto") {
      root.setAttribute("data-palette", id);
      root.removeAttribute("data-theme");       // a fixed choice outranks the clock
    } else {
      root.removeAttribute("data-palette");
      applyTheme();                             // back to day/night
    }
    try { localStorage.setItem("banna.palette", id || "auto"); } catch (e) { /* private mode */ }
    const note = $("appearanceNote");
    const chosen = PALETTES.find((p) => p.id === (id || "auto"));
    if (note && chosen) note.textContent = chosen.name;
  }

  function renderPalettes() {
    const grid = $("palGrid");
    if (!grid) return;
    const current = savedPalette();
    grid.innerHTML = PALETTES.map((p) => `
      <button class="pal${p.id === current ? " on" : ""}" data-palette="${esc(p.id)}">
        <span class="pal-sw" style="background:${esc(p.a)}"><i style="background:${esc(p.b)}"></i></span>
        <span><span class="pal-name">${esc(p.name)}</span><br>
          <span class="pal-note">${esc(p.note)}</span></span>
      </button>`).join("");
  }

  function age(mins) {
    if (mins == null) return "";
    // Under a minute matters on a phone: a job 40 seconds old is the
    // one the operator is actually reacting to, and "0m" hides that.
    if (mins < 1) return Math.max(0, Math.round(mins * 60)) + "s";
    const m = Math.round(mins);
    if (m < 60) return m + "m";
    const h = Math.floor(m / 60);
    if (h < 24) return h + "h";
    return Math.floor(h / 24) + "d";
  }
  function ageClass(mins) {
    if (mins == null) return "";
    if (mins > 240) return "bad";
    if (mins > 60) return "warn";
    return "";
  }

  // The API returns waiting_since as an ISO timestamp. An operator needs
  // "how long has this been sitting", not a machine timestamp, so it is
  // rendered as elapsed time -- the raw value is never shown on a card.
  function elapsedSince(iso) {
    if (!iso) return "";
    const t = Date.parse(iso);
    if (isNaN(t)) return "";
    return age((Date.now() - t) / 60000);
  }
  function elapsedMinutes(iso) {
    if (!iso) return null;
    const t = Date.parse(iso);
    return isNaN(t) ? null : (Date.now() - t) / 60000;
  }

  async function copy(text, label) {
    try {
      await navigator.clipboard.writeText(text);
      toast((label || "Copied") + " — paste into WhatsApp");
    } catch (e) {
      // Clipboard can be blocked; never silently fail an operator action.
      toast("Copy blocked by browser — select the text manually");
    }
  }

  /* ------------------------------------------------ connectivity ---- */

  // Truthfulness rule: the operator must never mistake a stale board for
  // live work. On repeated failure the banner appears and the board is
  // visibly dimmed until a poll succeeds again.
  function setOffline(on, why) {
    state.offline = on;
    $("offlineBar").hidden = !on;
    $("board").classList.toggle("stale", on);
    if (on) {
      const since = state.lastGood
        ? "last live " + formatDisplayTime(state.lastGood) : "never reached the server";
      $("offlineDetail").textContent = `not live · ${since}${why ? " · " + why : ""}`;
    }
  }

  /* ------------------------------------------------------ load ----- */

  async function load() {
    if (state.paused) return;
    try {
      const [newJobs, active, closings, wa, quoShadow] = await Promise.all([
        Api.getNewJobs(), Api.getActive(), Api.getClosings(),
        // WhatsApp jobs understood and routed but NOT committed jobs.
        // A failure here must never blank the real board.
        Api.getWhatsappCandidates().catch(() => ({ candidates: [] })),
        Api.getQuoDispatchShadow(state.quoShadowReplay).catch(() => ({ episodes: [] })),
      ]);

      // TO DISPATCH: live bucket first. `older` is deliberately excluded
      // from the board -- it is historical pre-commit material and lives
      // in Admin > Inbox Review.
      const live = (newJobs.live || []).filter(
        (j) => j.age_minutes == null || j.age_minutes <= DISPATCH_MAX_AGE_MIN);
      state.dispatch = live;
      state.staleCount = (newJobs.older || []).length;

      state.progress = (active.entries || []);
      // Old open jobs are NOT dropped -- they are signposted separately so
      // an 18-day-old record can never read as today's work.
      state.progressStale = (active.stale || []);
      state.close = (closings.entries || []);
      // Dispatchable candidates: the routing rules resolved them and no
      // downstream evidence says they were already sent.
      state.candidates = (wa.candidates || []).filter(
        (c) => c.status === "WAITING FOR HUMAN DISPATCH");
      // Held back by a SAFETY rule -- a repost after a prior dispatch,
      // an order that could not be established, or a company that could
      // not be resolved. These stay visible on purpose: a suppression
      // the operator cannot see is a decision taken on their behalf
      // without telling them. They carry no send or copy action.
      state.candidateReviews = (wa.candidates || []).filter((c) => c.review_reason);
      // The shadow session reports on EVERY real job Banna evaluated,
      // including the ones it decided not to act on -- that judgement is
      // exactly what the owner is here to watch.
      state.candidatesAll = (wa.candidates || []);
      state.quoShadow = quoShadow || { episodes: [] };
      state.quoShadowReplay = Boolean(quoShadow && quoShadow.historical_replay);

      state.failures = 0;
      state.lastGood = new Date();
      if (state.offline) setOffline(false);

      render();
      // Every CRM screen refreshes on the poll, not just Today. Jobs and
      // Reports used to load only when you navigated to them, so a job
      // arriving while you sat on the Jobs list simply never appeared --
      // the board behind it updated and the screen in front of you did
      // not. A dispatcher watching a list has to be able to trust that
      // the list is current.
      if (state.screen === "today") loadToday();
      if (state.screen === "jobs") loadJobs();
      if (state.screen === "reports") loadReports();
      loadDemo();
      $("updatedAt").textContent = formatDisplayTime(state.lastGood);
    } catch (e) {
      if (e.authRequired) return showLogin();
      state.failures += 1;
      if (state.failures >= OFFLINE_AFTER_FAILURES) setOffline(true, e.message);
      $("updatedAt").textContent = "error: " + e.message;
    }
  }

  // The owner demo. Rendered in its own amber-framed strip so a demo job
  // can never be mistaken for real work, and driven by /api/demo_console
  // which runs the same dispatcher brain over the two registered demo
  // chats only. Silent and hidden until those chats are registered.
  async function loadDemo() {
    try {
      const d = await Api.getDemoConsole();
      const strip = $("demoStrip");
      if (!d || !d.configured || !d.decisions.length) { strip.hidden = true; return; }
      strip.hidden = false;
      const j = d.job || {};
      $("demoState").textContent =
        `${j.ticket || "no ticket"} · ${j.state || ""}` +
        (j.closing_amount ? ` · $${j.closing_amount} ${j.closing_payment_method || ""}` : "");
      $("demoSteps").innerHTML = d.decisions.slice(-4).map((s) => `
        <div class="demo-step">
          <div class="ds-chat">${esc(s.chat)} chat${s.confidence ? " · " + esc(s.confidence) : ""}</div>
          <div class="ds-msg">${esc((s.text || "").slice(0, 70))}</div>
          <div class="ds-det">${esc(s.detected)}</div>
          ${(s.why || []).slice(0, 3).map((w) => `<div class="ds-why">· ${esc(w)}</div>`).join("")}
          <div class="ds-state">${esc(s.proposed_action)}${
            s.state_before !== s.state_after ? ` · ${esc(s.state_before)} → ${esc(s.state_after)}` : ""}</div>
          ${s.missing_fields && s.missing_fields.length
            ? `<div class="ds-why" style="color:var(--warn)">missing: ${esc(s.missing_fields.join(", "))} (never invented)</div>` : ""}
          ${s.proposed_message ? `<div class="ds-msgbox">${esc(s.proposed_message)}</div>` : ""}
        </div>`).join("");
    } catch (e) { /* demo is optional -- never break the real board */ }
  }

  // Real health, from /api/live_health. Every value here comes from a
  // real local source -- the adapter THREAD's liveness, the bridge's own
  // durable queue file, observation recency in the database. Nothing is
  // painted green because it looks better that way.
  async function health() {
    try {
      const h = await Api.getLiveHealth();
      state.health = h;

      if (h.product_name) {
        const parts = String(h.product_name).split(" ");
        $("brandProduct").innerHTML = parts.length > 1
          ? esc(parts[0]) + "<span>" + esc(parts.slice(1).join(" ")) + "</span>"
          : esc(h.product_name);
      }
      if (h.tenant_name) $("brandTenant").textContent = h.tenant_name;

      const allUp = (h.crm && h.crm.up) && (h.adapter && h.adapter.up) && (h.whatsapp && h.whatsapp.up);
      const conn = $("connState"), label = $("connLabel");
      if (conn && label) {
        conn.classList.toggle("bad", !allUp);
        label.textContent = allUp ? "Connected"
          : (h.whatsapp && !h.whatsapp.up) ? "WhatsApp not capturing" : "Degraded";
      }
      // The per-service dots only appear when something is actually wrong.
      const health = $("health"); if (health) health.hidden = allUp;
      $("dotCrm").className = "dot " + (h.crm && h.crm.up ? "up" : "down");
      $("dotAdapter").className = "dot " + (h.adapter && h.adapter.up ? "up" : "down");
      $("dotWa").className = "dot " + (h.whatsapp && h.whatsapp.up ? "up" : "down");
      $("health").title =
        `CRM: ${(h.crm || {}).detail || "?"}
` +
        `Adapter: ${(h.adapter || {}).detail || "?"}
` +
        `WhatsApp: ${(h.whatsapp || {}).detail || "?"} (${(h.whatsapp || {}).derived_from || ""})
` +
        `Database: ${(h.database || {}).integrity || "?"}, ` +
        `${(h.database || {}).foreign_key_violations} FK violations`;

      $("hbLast").textContent = h.seconds_since_last_observation == null
        ? "never" : relative(h.seconds_since_last_observation);
      $("hbToday").textContent = h.observations_today != null ? h.observations_today : "--";

      const q = h.queue || {};
      // An unreadable queue is UNKNOWN, never "healthy" -- a false green
      // here would hide exactly the backlog it exists to reveal.
      $("hbQueue").textContent = q.depth == null ? "unknown" : (q.depth === 0 ? "healthy" : q.depth);
      $("hbQueue").className = q.depth == null ? "warnv" : (q.depth === 0 ? "" : "badv");

      // The backend's capture mode is reported as-is; the console states
      // what is actually happening on top of it -- deterministic
      // analysis of every captured job, with no external action.
      $("hbShadow").textContent = h.shadow_mode
        ? h.shadow_mode + " capture · active shadow" : "--";
      if (state.screen === "more") renderMore();
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("dotCrm").className = "dot down";
      $("hbShadow").textContent = "unknown";
    }
  }

  function relative(seconds) {
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return s + " sec ago";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " hr ago";
    return Math.round(s / 86400) + " d ago";
  }

  /* ------------------------------------------------ live operations -- */

  // Renders the operator event stream projected by the server from
  // ALREADY-PERSISTED observations. No parsing happens here, and no
  // model is involved -- this keeps updating while Claude is idle.
  async function loadLive() {
    if (!state.liveOpen) return;
    try {
      const r = await Api.getLiveFeed();
      const events = (r && r.events) || [];
      // Compact by default: time, type, one line of substance. The
      // reasoning ("why") is real operational evidence and is kept, but
      // it waits behind a tap instead of burying the stream in prose.
      renderShadow();
      $("loBody").innerHTML = events.length ? events.map((ev) => `
        <div class="lo-ev lo-${esc((ev.type || "").toLowerCase().replace(/\s+/g, "-"))}${
             ev.needs_human ? " needs" : ""}" data-ev="1">
          <span class="lo-t">${esc(formatDisplayTime(ev.time))}</span>
          <span class="lo-type">${esc(ev.needs_human ? "Needs you" : displayLabel(ev.type))}</span>
          <span class="lo-title">${esc(displayLabel(ev.title))}</span>
          ${ev.subtitle ? `<span class="lo-sub">${esc(displayLabel(ev.subtitle))}</span>` : ""}
          ${ev.confidence ? `<span class="lo-conf">${esc(displayLabel(ev.confidence))}</span>` : ""}
          ${(ev.why || []).length ? `<div class="lo-why">${
            ev.why.map((w) => `<span>${esc(w)}</span>`).join("")}</div>` : ""}
        </div>`).join("")
        : `<div class="empty">No recent operational events.</div>`;
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("loBody").innerHTML = `<div class="empty">Live feed unavailable: ${esc(e.message)}</div>`;
    }
  }

  /* ------------------------------------------------------- the CRM --
     Banna's daily value is that real dispatch work becomes structured
     business history without anyone typing it in. TODAY answers the
     questions an owner asks out loud -- how many jobs, what state, whose
     work, which areas -- and stops there. Everything technical lives one
     tap away.

     All of it comes from ONE read-only endpoint. The phone does not join
     five sources and hope they agree. */
  function money(value, available) {
    // A number nobody can compute is not zero. Closing amounts are not
    // recorded yet, so this shows a dash rather than a $0 that reads
    // like a terrible day.
    if (!available || value == null) return { text: "—", unknown: true };
    return { text: "$" + Number(value).toLocaleString("en-US"), unknown: false };
  }

  function rankRows(items, emptyText, kind) {
    if (!items || !items.length) return `<div class="empty">${esc(emptyText)}</div>`;
    const top = items.slice(0, 5);
    const max = Math.max.apply(null, top.map((i) => i.count)) || 1;
    return top.map((i) => `
      <div class="rank-row" data-rank-kind="${esc(kind || "")}" data-rank-value="${esc(i.name)}">
        <span>${esc(i.name)}</span><span class="rank-n">${esc(i.count)}</span>
        <span class="rank-bar"><i style="width:${Math.round((i.count / max) * 100)}%"></i></span>
      </div>`).join("");
  }

  function jobRow(j) {
    const area = j.area || "area not stated";
    return `<div class="job-row" data-col="candidate" data-id="${esc(j.message_id)}">
      <span class="jr-t">${esc(formatDisplayTime(j.at))}</span>
      <span class="jr-co">${esc(j.company)}</span>
      <span class="jr-state ${esc(j.bucket)}">${esc(displayLabel(j.bucket))}</span>
      <span class="jr-main">${esc(area)}${j.service ? " · " + esc(j.service) : ""}</span>
      <span class="jr-sub">${esc(j.technician || "no technician yet")}${
        j.ticket ? " · " + esc(j.ticket) : ""}</span>
    </div>`;
  }

  /* ------------------------------------------------- service alarm --
     The CRM's own data cannot tell an outage from a quiet shift: both
     are an empty board. This is the one place Banna says out loud that
     it is not working, so it must never cry wolf and must never stay
     silent through a real fault.

     Three states, deliberately distinct:
       healthy          -> hidden entirely
       a service is down-> name it, and say what is NOT being captured
       supervisor stale -> say we do not know, which is its own fault
     "Unknown" is never rendered as healthy. */
  function renderServiceAlarm(health) {
    const box = $("serviceAlarm");
    if (!box) return;
    if (!health || health.healthy) { box.hidden = true; return; }

    let title, body;
    if (!health.known) {
      title = "Banna cannot confirm it is running";
      body = "No supervisor report was found, so the services may or may not be up. "
           + "An empty board right now does not mean a quiet shift.";
    } else if (!health.watchdog_alive) {
      title = "Banna may have stopped";
      body = "The supervisor that keeps the services alive " + esc(health.reason) + ". "
           + "Anything below may be out of date. Jobs arriving now may not be captured.";
    } else {
      const down = health.down || [];
      title = down.length === 1 ? "A Banna service has stopped" : "Banna services have stopped";
      body = "<b>" + esc(down.join(", ")) + "</b> "
           + (down.length === 1 ? "is" : "are") + " not running. "
           + (down.indexOf("WhatsApp capture") !== -1
              ? "Jobs sent on WhatsApp right now are NOT being recorded. "
              : "")
           + (down.indexOf("Quo polling") !== -1
              ? "Jobs arriving through Quo are NOT being recorded. " : "")
           + "It should restart by itself within 5 minutes.";
    }
    if (health.checked_at) {
      body += " Last checked " + esc(formatDisplayTime(health.checked_at)) + ".";
    }
    $("serviceAlarmTitle").textContent = title;
    $("serviceAlarmBody").innerHTML = body;
    box.hidden = false;
  }

  async function loadToday() {
    try {
      const r = await Api.getCrmSummary("today");
      state.crm = r;
      renderServiceAlarm(r.service_health);
      $("todayLabel").textContent = "Today · " + (r.range_label || "");
      $("todayTz").textContent = r.timezone;
      $("statJobs").textContent = r.jobs_received;
      $("statOpen").textContent = r.open;
      $("statDone").textContent = r.completed;
      $("statAttention").textContent = r.needs_attention;
      const m = money(r.verified_revenue, r.revenue_available);
      $("statRevenue").textContent = m.text;
      $("statRevenue").className = "stat-n" + (m.unknown ? " unknown" : "");
      $("revenueNote").textContent = m.unknown
        ? "Verified closing revenue — not recorded yet"
        : "Verified closing revenue";
      $("byCompany").innerHTML = rankRows(r.by_company, "No jobs recorded today.", "company");
      $("byArea").innerHTML = rankRows(r.by_area, "No areas recorded today.", "area");
      $("recentJobs").innerHTML = (r.recent_jobs || []).length
        ? r.recent_jobs.map(jobRow).join("")
        : `<div class="empty">No jobs captured today yet.</div>`;
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("recentJobs").innerHTML = `<div class="empty">Summary unavailable: ${esc(e.message)}</div>`;
    }
  }

  async function loadReports() {
    try {
      const r = await Api.getCrmSummary(state.reportRange);
      $("reportRange").textContent = r.timezone + " · " + r.range_start.slice(0, 10)
        + " to " + r.range_end.slice(0, 10);
      const m = money(r.verified_revenue, r.revenue_available);
      $("reportTotals").innerHTML = [
        ["Jobs received", r.jobs_received], ["Open", r.open],
        ["Completed", r.completed], ["Needs attention", r.needs_attention],
        ["Verified revenue", m.text],
      ].map(([k, v]) => `<div class="rank-row"><span>${esc(k)}</span><span class="rank-n">${esc(v)}</span></div>`).join("");
      $("reportCompany").innerHTML = rankRows(r.by_company, "No jobs in this range.");
      $("reportArea").innerHTML = rankRows(r.by_area, "No areas in this range.", "area");
      // Unassigned is shown, not hidden: it is the count of work nobody
      // could be routed to, which is the most actionable number here.
      $("reportTech").innerHTML = rankRows(r.by_technician, "No technicians in this range.");
      $("reportService").innerHTML = rankRows(r.by_service, "No services in this range.");
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("reportTotals").innerHTML = `<div class="empty">Reports unavailable: ${esc(e.message)}</div>`;
    }
  }

  async function loadHistoricalShadow() {
    try {
      state.quoShadow = await Api.getQuoDispatchShadow(true);
      state.quoShadowReplay = true;
      renderShadow();
    } catch (e) {
      if (e.authRequired) return showLogin();
      toast("Historical replay unavailable: " + e.message);
    }
  }

  // Renders only the dedicated AGN/Lucky Quo projection. Other Quo groups
  // remain in the shared ingestion queue, but cannot enter this demo view.
  function renderShadow() {
    const el = $("shadowBody2") || $("shadowBody");
    if (!el) return;
    const startEl = $("shadowStart2") || $("shadowStart");
    const sessionStart = shadowSessionStart();
    const jobs = shadowJobs();
    const replay = Boolean(state.quoShadow && state.quoShadow.historical_replay);
    if (startEl) startEl.textContent = replay ? "persisted real evidence" : formatDisplayTime(sessionStart);
    if ($("shadowWindowLabel")) $("shadowWindowLabel").textContent = replay ? "Evidence window" : "Session started";
    const replayBtn = $("shadowReplayBtn");
    if (replayBtn) replayBtn.hidden = replay || jobs.length > 0;

    const counts = { waiting: 0, review: 0, forwarded: 0, acknowledged: 0, diff: 0 };
    jobs.forEach((c) => {
      if (c.review_reason || c.state === "HUMAN_DISPATCH_AMBIGUOUS" ||
          c.state === "ACKNOWLEDGEMENT_AMBIGUOUS") counts.review += 1;
      if (c.human_dispatch_state === "WAITING_FOR_HUMAN_DISPATCH") counts.waiting += 1;
      if (c.human_dispatch_state === "HUMAN_DISPATCH_OBSERVED") counts.forwarded += 1;
      if (c.ack_state === "TECHNICIAN_ACKNOWLEDGED") counts.acknowledged += 1;
      if (c.routing_match === "DIFFERENT") counts.diff += 1;
    });
    const summaryEl = $("shadowSummary2") || $("shadowSummary");
    summaryEl.textContent = jobs.length
      ? `${jobs.length} real ${replay ? "historical " : ""}AGN/Lucky job${jobs.length === 1 ? "" : "s"} · `
        + `${counts.waiting} waiting · ${counts.forwarded} human forwarded · `
        + `${counts.acknowledged} K observed · ${counts.review} review · ${counts.diff} different`
      : "No new real jobs since this session started.";

    el.innerHTML = jobs.length ? jobs.map((c) => {
      const d = shadowDecision(c);
      const cmp = comparison(c);
      const human = humanAction(c);
      const flow = [];
      flow.push(`<b>QUO RECEIVED</b> · ${esc(formatDisplayTime(c.received_at))}`);
      if (c.company) flow.push(`<b>COMPANY IDENTIFIED</b> · ${esc(c.company)}`);
      if (c.area) flow.push(`<b>AREA RESOLVED</b> · ${esc(c.area)}`);
      if (c.recommended_technician && c.prepared_message) {
        flow.push(`<b>BANNA WOULD PREPARE FOR ${esc(c.recommended_technician)}</b>`);
      }
      if (c.human_dispatch_state === "WAITING_FOR_HUMAN_DISPATCH") {
        flow.push("<b>WAITING FOR HUMAN DISPATCH</b>");
      }
      if (human) {
        flow.push(`<b>HUMAN DISPATCH OBSERVED</b> · ${esc(human.technician)} · ${esc(c.routing_match || "")}`);
      }
      if (c.ack_text) {
        flow.push(`<b>TECH K OBSERVED</b> · ${esc(c.ack_text)} · ${esc(c.ack_confidence || "")}`);
      }
      return `<div class="sh-job">
        ${replay ? `<div class="shadow-replay-label">REAL HISTORICAL REPLAY</div>` : ""}
        <div class="sh-top">
          <span class="sh-company">${esc(c.company || c.source || "company unresolved")}</span>
          <span class="sh-ticket">${esc(c.ticket || "no ticket")}</span>
        </div>
        <div class="sh-title">${esc(c.service || "service not stated")}${c.area ? " · " + esc(c.area) : ""}</div>
        <span class="sh-decision ${d.tone}">${esc(d.label)}</span>
        <div class="sh-flow">${flow.join("<br>↓<br>")}</div>

        ${c.prepared_message ? `
        <div class="sh-would">
          <div class="sh-would-h">Banna would dispatch</div>
          <div class="sh-v"><b>${esc(c.recommended_technician)}</b> · ${esc(c.expected_destination)}</div>
          <div class="sh-msg">${esc(c.prepared_message)}</div>
          <div class="sh-sim">SIMULATION ONLY &mdash; NOTHING SENT</div>
        </div>` : `
        <div class="sh-would">
          <div class="sh-would-h">Banna would not dispatch</div>
          <div class="sh-v">${esc(c.review_reason || d.label)}</div>
          <div class="sh-sim">SIMULATION ONLY &mdash; NOTHING SENT</div>
        </div>`}

        <div class="sh-compare">
          ${human ? `Human dispatch observed at <b>${esc(human.technician || "unnamed destination")}</b>${
              human.at ? " · " + esc(formatDisplayTime(parseEventStamp(human.at))) : ""}<br>` : ""}
          <span class="sh-${cmp.state === "match" ? "match" : cmp.state === "diff" ? "diff" : "waiting"}">${esc(cmp.text)}</span>
          ${c.ack_text ? `<br>Technician reply <b>${esc(c.ack_text)}</b> · ${esc(c.ack_confidence)} · ${
              esc(formatDisplayTime(parseEventStamp(c.ack_timestamp)))}` : ""}
        </div>
        <details><summary>Technical evidence</summary>
          <div class="sh-v">${esc(c.decision_reason || "")} · ${esc(c.decision_confidence || "UNRESOLVED")}</div>
        </details>
      </div>`;
    }).join("") : `<div class="empty">No current-session AGN or Lucky job yet.
        Banna is watching read-only; use the real historical replay only when a live job is unavailable.</div>`;
  }

  // Evidence timestamps arrive either as an ISO string or the TEXT epoch
  // the bridge records. Both describe the same instant.
  function parseEventStamp(v) {
    if (v == null || v === "") return null;
    if (/^\d{9,}$/.test(String(v))) return new Date(parseInt(v, 10) * 1000);
    return v;
  }

  // Apprentice review. READ-ONLY by construction: there is no approve
  // control here and no endpoint to call if there were. Every card
  // states its evidence and its status so the owner judges the pattern,
  // not a verdict.
  async function loadApprentice() {
    if (!state.apprenticeOpen) return;
    try {
      const r = await Api.getApprenticeSuggestions();
      const list = (r && r.suggestions) || [];
      // READ-ONLY by construction: no approve control exists here and no
      // endpoint would accept one. The explanation lives behind VIEW
      // EVIDENCE so the card stays scannable -- repeating the same
      // paragraph on every card trains the owner to stop reading it.
      $("apBody").innerHTML = list.length ? list.map((s) => `
        <div class="ap-card ap-${esc(String(s.confidence || "").toLowerCase())}" data-ap="1">
          <div class="ap-top">
            <span class="ap-status">${esc(s.status)}</span>
            <span class="ap-conf">${esc(s.confidence)}</span>
          </div>
          <div class="ap-rule">
            ${esc(s.source || "?")} &middot; ${esc(s.area || "any area")} &middot;
            ${esc(s.service || "any service")}
            <div><span class="ap-arrow">&rarr;</span> <b>${esc(s.destination)}</b></div>
          </div>
          <div class="ap-ev">
            ${esc(s.evidence.distinct_tickets)} jobs &middot;
            ${esc(s.evidence.distinct_days)} days &middot;
            ${esc(s.conflicts)} conflicts
          </div>
          <div class="ap-evidence">
            ${(s.notes || []).map((n) => `<div class="ap-note">${esc(n)}</div>`).join("")}
            <div class="ap-note">Nothing here is active. This is a pattern Banna
              noticed in traffic you already handled &mdash; it changes no routing
              rule until you say so somewhere else.</div>
          </div>
          <div class="card-actions">
            <button class="btn ghost" data-act="ap-evidence">View evidence</button>
          </div>
        </div>`).join("")
        : `<div class="empty">No pattern has enough evidence yet. That is the correct
             result for thin evidence &mdash; one example never makes a rule.</div>`;
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("apBody").innerHTML = `<div class="empty">Apprentice unavailable: ${esc(e.message)}</div>`;
    }
  }

  // Technicians. Routing coverage, read from the same /api/technicians
  // the back office uses -- this screen states who can take work where,
  // and nothing here changes an assignment.
  async function loadTechs() {
    if (state.screen !== "techs") return;
    try {
      const r = await Api.getTechnicians();
      const list = (r && (r.technicians || r.entries)) || [];
      $("techBody").innerHTML = list.length ? list.map((t) => `
        <div class="tech-card">
          <div class="tech-name">${esc(t.name || "unnamed")}</div>
          <div class="tech-meta">${esc((t.areas || []).join(", ") || "no area set")}</div>
          ${t.skills && t.skills.length ? `<div class="tech-meta">${esc(t.skills.join(", "))}</div>` : ""}
          ${t.dispatch_group ? `<div class="tech-meta">${esc(t.dispatch_group)}</div>` : ""}
          <div class="tech-meta">${esc(techState(t))}</div>
        </div>`).join("")
        : `<div class="empty">No technicians configured.</div>`;
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("techBody").innerHTML = `<div class="empty">Technicians unavailable: ${esc(e.message)}</div>`;
    }
  }

  // Availability is only ever reported when the read model actually
  // states it. "Unknown" is the honest answer for a technician whose
  // status nobody has recorded -- inventing "Available" here would put a
  // job on someone who never said they could take it.
  function techState(t) {
    if (t.is_active === false) return "Inactive";
    const open = t.open_jobs != null ? t.open_jobs
      : (t.active_jobs != null ? t.active_jobs : null);
    if (open != null) return open === 0 ? "No open jobs" : open + " open job" + (open === 1 ? "" : "s");
    if (t.status) return displayLabel(t.status);
    return "Availability unknown";
  }

  // System screen. Diagnostics belong here, off the working board.
  function renderMore() {
    const h = state.health || {};
    const rows = [
      ["CRM", (h.crm || {}).detail],
      ["Adapter", (h.adapter || {}).detail],
      ["WhatsApp", (h.whatsapp || {}).detail],
      ["Database", (h.database || {}).integrity],
      ["FK violations", (h.database || {}).foreign_key_violations],
    ].filter(([, v]) => v != null && v !== "");
    $("moreBody").innerHTML = rows.length
      ? `<div class="tech-card">${rows.map(([k, v]) =>
          `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join("")}</div>`
      : `<div class="empty">Health not reported yet.</div>`;
  }

  /* ------------------------------------------------ bottom navigation --
     Jobs is home. Live Operations and Apprentice are their own screens:
     mixing them into the board is what made the phone unreadable, because
     telemetry always out-volumes the two or three jobs that actually
     need a decision. */
  function setScreen(name) {
    if (!name) return;
    state.screen = name;
    const map = { today: "screenToday", jobs: "screenJobs", reports: "screenReports",
                  live: "screenLive", apprentice: "screenApprentice",
                  techs: "screenTechs", shadow: "screenShadow",
                  groups: "screenGroups", appearance: "screenAppearance",
                  more: "screenMore" };
    Object.keys(map).forEach((k) => {
      const el = $(map[k]);
      if (el) { el.hidden = k !== name; el.classList.toggle("on", k === name); }
    });
    // Secondary screens are reached FROM More, so More stays lit while
    // one of its tools is open -- the operator is still "in" that section.
    const primary = ({ live: "more", apprentice: "more", techs: "more",
                       shadow: "more", groups: "more", appearance: "more" })[name] || name;
    document.querySelectorAll(".tab").forEach((b) =>
      b.classList.toggle("on", b.getAttribute("data-screen") === primary));
    state.liveOpen = name === "live";
    state.apprenticeOpen = name === "apprentice";
    if (name === "today") loadToday();
    if (name === "reports") loadReports();
    if (name === "jobs") loadJobs();
    if (name === "live") loadLive();
    if (name === "shadow") renderShadow();
    if (name === "groups") loadGroups();
    if (name === "appearance") renderPalettes();
    if (name === "apprentice") loadApprentice();
    if (name === "techs") loadTechs();
    if (name === "more") renderMore();
    window.scrollTo(0, 0);
  }

  /* ---------------------------------------------------- rendering -- */

  // The words come from the server (operator_state). The fallback below
  // is presentation-only and deliberately vague -- it never tries to
  // re-derive meaning from message text, which is the brain's job.
  function waitingLabel(e) {
    const os = e.operator_state;
    if (os && os.label) return { t: os.label, c: os.tone || "", next: os.next_expected || "" };
    const na = (e.next_action || "").toUpperCase();
    if (na.includes("HUMAN_REVIEW")) return { t: "Needs review", c: "rev", next: "" };
    if (e.bucket) return { t: String(e.bucket).replace(/_/g, " "), c: "", next: "" };
    return { t: e.status || "In progress", c: "", next: "" };
  }

  // latest_technician_update arrives from the read model as an object
  // ({update_type, raw_message, eta_minutes, created_at}), not a string.
  // Interpolating it directly rendered a literal "[object Object]" on the
  // card where the technician's own words belong.
  function lastUpdateText(e) {
    const u = e.latest_technician_update;
    if (!u) return "";
    if (typeof u === "string") return u;
    return u.raw_message || u.update_type || "";
  }

  function dispatchCard(j, isNext) {
    const sel = state.selected && state.selected.col === "dispatch" && state.selected.id === j.message_id;
    const review = j.needs_review || (j.review_problems && j.review_problems.length);
    const cls = ["card", isNext ? "next" : "", sel ? "sel" : "", review ? "review" : "",
                 ageClass(j.age_minutes) === "bad" ? "urgent" : ""].join(" ");
    const tech = j.technician_name || (j.eligible_count ? j.eligible_count + " eligible" : "no match");
    return `<div class="${cls}" data-col="dispatch" data-id="${esc(j.message_id)}">
      <div class="card-top">
        <span class="card-company">${esc(j.source || "source not stated")}</span>
        <span class="age ${ageClass(j.age_minutes)}">${age(j.age_minutes)}</span>
      </div>
      <div class="card-title">${esc(j.service || "service not stated")}</div>
      <div class="card-line">${esc([j.city, j.state].filter(Boolean).join(", ") || "location not stated")}</div>
      <div class="card-line"><span class="ticket">${esc(j.ticket || "no ticket")}</span></div>
      <div class="rec ${j.technician_name ? "" : "none"}">
        <span class="rec-l">Recommended</span>
        <span class="rec-v">${esc(tech)}</span>
        ${confidenceChip(j.technician_name ? "EXACT" : "AMBIGUOUS")}
      </div>
      ${review ? `<span class="waiting rev">Needs review</span>` : `<span class="waiting act">Ready to dispatch</span>`}
      <div class="card-actions">
        <button class="btn primary" data-act="dispatch" data-id="${esc(j.message_id)}">Dispatch</button>
      </div>
    </div>`;
  }

  // A WhatsApp job Banna understood and routed. Deliberately NOT a
  // dispatchable card: the audited acknowledge -> prepare -> confirm flow
  // belongs to a committed Quo message and there is none here. The
  // operator copies the prepared message and sends it themselves, which
  // is exactly the human-controlled step this product is built around.
  function candidateCard(c, isNext) {
    return `<div class="card candidate${isNext ? " next" : ""}" data-col="candidate" data-id="${esc(c.message_id)}">
      <div class="card-top">
        <span class="card-company">${esc(c.source || "source not stated")}</span>
        <span class="cand-tag">WhatsApp</span>
        <span class="age">${esc(elapsedSince(c.received_at))}</span>
      </div>
      <div class="card-title">${esc(c.service || "service not stated")}</div>
      <div class="card-line">${esc([c.city, c.state].filter(Boolean).join(", ") || "location not stated")}</div>
      <div class="card-line"><span class="ticket">${esc(c.ticket || "no ticket")}</span></div>
      <div class="rec">
        <span class="rec-l">Recommended</span>
        <span class="rec-v">${esc(c.recommended)}</span>
        ${confidenceChip(c.confidence)}
      </div>
      <span class="waiting act">${esc(displayLabel(c.status))}</span>${staleBadge(c)}
      <div class="card-actions">
        <button class="btn primary" data-act="copy-candidate" data-id="${esc(c.message_id)}">Copy message</button>
      </div>
    </div>`;
  }

  // A candidate a SAFETY rule held back. Deliberately has NO copy and NO
  // dispatch control: it is shown so the operator knows the decision was
  // made and can resolve it, not so they can act on it blind.
  function reviewCard(c) {
    return `<div class="card candidate review" data-col="candidate-review" data-id="${esc(c.message_id)}">
      <div class="card-top">
        <span class="card-company">Needs you</span>
        <span class="age">${esc(elapsedSince(c.received_at))}</span>
      </div>
      <div class="card-title">${esc(displayLabel(c.review_reason))}</div>
      <div class="card-line">${esc([c.city, c.state].filter(Boolean).join(", ") || "location not stated")}</div>
      <div class="card-line"><span class="ticket">${esc(c.ticket || "no ticket")}</span></div>
      ${c.actual_destination ? `<div class="card-line">Seen at: ${esc(c.actual_destination)}</div>` : ""}
      <div class="card-actions">
        <button class="btn ghost" data-act="review" data-id="${esc(c.message_id)}">Review</button>
      </div>
    </div>`;
  }

  /* ---------------------------------------------------- real dates --
     "83d" is not something a dispatcher can act on. An actual date plus
     the real gap since last activity is the difference between "an old
     record" and "a job from August nobody ever closed". */
  function formatDisplayDate(value, opts) {
    if (value == null || value === "") return "";
    const t = value instanceof Date ? value : new Date(value);
    if (isNaN(t.getTime())) return "";
    return t.toLocaleDateString("en-US",
      Object.assign({ month: "short", day: "numeric", timeZone: OPERATIONAL_TZ }, opts || {}));
  }

  // "What day is it" has to be asked in the operational timezone too,
  // or "Today"/"Yesterday" disagree with the counts beside them.
  function operationalDayKey(d) {
    const p = new Intl.DateTimeFormat("en-CA", {
      timeZone: OPERATIONAL_TZ, year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(d);
    const get = (type) => (p.find((x) => x.type === type) || {}).value;
    return Date.UTC(Number(get("year")), Number(get("month")) - 1, Number(get("day")));
  }

  function daysSince(value) {
    if (value == null || value === "") return null;
    const t = value instanceof Date ? value : new Date(value);
    if (isNaN(t.getTime())) return null;
    return Math.round((operationalDayKey(new Date()) - operationalDayKey(t)) / 86400000);
  }

  // "Today 6:14 PM" / "Yesterday 7:59 PM" / "Aug 16, 7:59 PM". Absolute
  // once it is older than yesterday, which is the point where a relative
  // age stops carrying information.
  function whenText(value) {
    if (value == null || value === "") return "";
    const t = value instanceof Date ? value : new Date(value);
    if (isNaN(t.getTime())) return "";
    const days = daysSince(t);
    const clock = formatDisplayTime(t);
    if (days === 0) return "Today " + clock;
    if (days === 1) return "Yesterday " + clock;
    const year = t.getFullYear() === new Date().getFullYear() ? {} : { year: "numeric" };
    return formatDisplayDate(t, year) + ", " + clock;
  }

  function stalenessText(value) {
    const days = daysSince(value);
    if (days == null || days <= 0) return "";
    if (days === 1) return "no activity since yesterday";
    return "no activity for " + days + " days";
  }

  // Age NEVER decides whether open work is shown. A job that is
  // genuinely open is open whether it arrived an hour ago or in August,
  // so it stays in the queue and carries a stale warning instead of
  // being filtered out. Age only separates HISTORICAL UNRESOLVED
  // INTAKE -- items nobody could act on because their company was never
  // resolved -- into a reconciliation list, so they stop masquerading
  // as new jobs waiting to be dispatched.
  const STALE_AFTER_DAYS = 2;

  function isStale(item) {
    const days = daysSince(item.event_at || item.received_at);
    return days != null && days > STALE_AFTER_DAYS;
  }

  // A visible, non-destructive warning. The job stays exactly where it
  // is; the operator is simply told how old it is.
  function staleBadge(item) {
    if (!isStale(item)) return "";
    return `<span class="stale-badge">${esc(stalenessText(item.event_at || item.received_at))}</span>`;
  }

  // A backlog row leads with WHEN, because that is the fact that decides
  // whether it still matters. Kept and labelled; never auto-closed.
  function backlogCard(c) {
    const stale = stalenessText(c.event_at || c.received_at);
    const title = c.service || (c.review_reason ? displayLabel(c.review_reason) : "service not stated");
    return `<div class="card candidate backlog" data-col="${
      c.review_reason ? "candidate-review" : "candidate"}" data-id="${esc(c.message_id)}">
      <div class="card-top">
        <span class="card-company">${esc(c.source || "company not stated")}</span>
        <span class="age">${esc(whenText(c.event_at || c.received_at))}</span>
      </div>
      <div class="card-title">${esc(title)}</div>
      <div class="card-line">${esc([c.city, c.state].filter(Boolean).join(", ") || "location not stated")}</div>
      <div class="card-line"><span class="ticket">${esc(c.ticket || "no ticket")}</span></div>
      ${stale ? `<div class="card-line stale-line">${esc(stale)}</div>` : ""}
    </div>`;
  }

  /* A zero that explains itself. "0 jobs" can mean a quiet morning, a
     bridge that stopped capturing, or groups nobody mapped -- three
     situations needing three different reactions. */
  function renderZeroState(r) {
    const box = $("zeroState");
    if (!box) return;
    if (!r || r.jobs_received > 0) { if (box) box.hidden = true; return; }
    const health = (r && r.intake_health) || {};
    const lines = [];

    const capturedDays = daysSince(health.whatsapp_last_observation_at);
    if (health.whatsapp_last_observation_at == null) {
      lines.push(["bad", "WhatsApp has never captured a message"]);
    } else if (capturedDays !== null && capturedDays >= 1) {
      lines.push(["bad", "WhatsApp last captured " +
        whenText(health.whatsapp_last_observation_at) + " — capture may be down"]);
    } else {
      lines.push(["ok", "WhatsApp capture live — last message " +
        formatDisplayTime(health.whatsapp_last_observation_at)]);
    }

    const quoDays = daysSince(health.quo_last_checkpoint);
    if (health.quo_last_checkpoint == null) {
      lines.push(["warn", "Quo ingestion has never run"]);
    } else if (quoDays !== null && quoDays >= 1) {
      lines.push(["warn", "Quo ingestion stopped " + whenText(health.quo_last_checkpoint)]);
    } else {
      lines.push(["ok", "Quo ingestion running"]);
    }

    if (health.intake_groups_with_company != null) {
      const mapped = health.intake_groups_with_company;
      lines.push([mapped > 0 ? "warn" : "bad",
        mapped + " group" + (mapped === 1 ? "" : "s") + " mapped as job intake" +
        (health.groups_unmapped ? " · " + health.groups_unmapped + " still unmapped" : "")]);
    }

    box.hidden = false;
    box.innerHTML = `<div class="zs-h">No jobs recorded today</div>`
      + lines.map(([tone, text]) => `<div class="zs-row zs-${tone}">${esc(text)}</div>`).join("")
      + `<button class="btn ghost" data-goto="groups">Map WhatsApp groups</button>`;
  }

  /* --------------------------------------------------- the Jobs list --
     The record a dispatcher opens instead of scrolling WhatsApp, and the
     destination for every number on Today. A counter that cannot be
     opened is a dead end. */
  function jobListRow(j) {
    const label = j.bucket === "needs_attention" ? "Attention"
      : j.bucket === "completed" ? "Completed" : "Open";
    const stale = stalenessText(j.at);
    return `<div class="job-row" data-col="candidate" data-id="${esc(j.message_id)}">
      <span class="jr-t">${esc(whenText(j.at))}</span>
      <span class="jr-co">${esc(j.company)}</span>
      <span class="jr-state ${esc(j.bucket)}">${esc(label)}</span>
      <span class="jr-main">${esc(j.area)}${j.service ? " · " + esc(j.service) : ""}</span>
      <span class="jr-sub">${esc(j.technician || "no technician yet")}${
        j.ticket ? " · " + esc(j.ticket) : ""}${stale ? " · " + esc(stale) : ""}</span>
    </div>`;
  }

  function describeFilter(q) {
    const bits = [];
    if (q.company) bits.push(q.company);
    if (q.area) bits.push(q.area);
    if (q.status) bits.push(displayLabel(q.status));
    return bits.join(" · ");
  }

  // Company chips come from the data, never a hardcoded list, so a
  // newly mapped company appears here the moment it has a job.
  function renderCompanyChips(companies) {
    const box = $("jobCompanies");
    if (!box) return;
    const current = state.jobsQuery.company || "";
    const all = [""].concat(companies || []);
    box.innerHTML = all.map((c) => `
      <button class="chip-btn${c === current ? " on" : ""}" data-jco="${esc(c)}">${
        esc(c || "All companies")}</button>`).join("");
  }

  async function loadJobs() {
    if (state.screen !== "jobs") return;
    const q = state.jobsQuery;
    const note = $("jobFilterNote");
    const described = describeFilter(q);
    if (note) {
      if (described) {
        note.hidden = false;
        note.innerHTML = `Filtered: ${esc(described)}<button data-act="jobs-clear">Clear</button>`;
      } else { note.hidden = true; }
    }
    try {
      const r = await Api.getCrmJobs(q);
      // Say what is NOT shown. A filtered-out job that is never
      // mentioned reads as a lost job.
      const hidden = r.hidden_by_go_live || 0;
      const aside = r.range === "backlog"
        ? ` · before go-live${hidden ? ` · ${hidden} newer` : ""}`
        : (hidden ? ` · ${hidden} older in Backlog` : "");
      $("jobsNote").textContent =
        `${r.total} job${r.total === 1 ? "" : "s"} · ${r.timezone}${aside}`;
      renderCompanyChips([...new Set((r.jobs || []).map((x) => x.company).filter(Boolean))].sort());
      $("jobList").innerHTML = (r.jobs || []).length
        ? r.jobs.map(jobListRow).join("")
        : `<div class="empty">No jobs match this view.</div>`;
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("jobList").innerHTML = `<div class="empty">Jobs unavailable: ${esc(e.message)}</div>`;
    }
  }

  // Tapping "AGN 9" lands on those nine rows.
  function openJobs(filter) {
    state.jobsQuery = Object.assign(
      { range: "all", status: "", company: "", area: "", q: "" }, filter || {});
    document.querySelectorAll("[data-jrange]").forEach((b) =>
      b.classList.toggle("on", b.getAttribute("data-jrange") === state.jobsQuery.range));
    document.querySelectorAll("[data-jstatus]").forEach((b) =>
      b.classList.toggle("on", b.getAttribute("data-jstatus") === state.jobsQuery.status));
    setScreen("jobs");
  }

  /* -------------------------------------------------- group mapping --
     The CRM is only as truthful as its group mapping: an unmapped group
     with real traffic is business Banna is silently mis-filing. Mapping
     is always the owner's explicit choice, never Banna's guess. */
  const ROLE_LABELS = {
    COMPANY_JOB_INTAKE: "Job intake (leads arrive here)",
    COMPANY_CLOSINGS: "Company closings",
    TECH_DISPATCH: "Technician dispatch",
    TECH_CLOSINGS: "Technician closings",
    UNMAPPED_WORK: "Not classified yet",
    IGNORE: "Ignore this group",
  };

  function groupNeedsMapping(g) {
    return !g.confirmed_by_owner || !g.role || g.role === "UNMAPPED_WORK";
  }

  function groupRow(g, payload) {
    const needs = groupNeedsMapping(g);
    const roles = (payload.roles || []).map((r) =>
      `<option value="${esc(r)}"${g.role === r ? " selected" : ""}>${esc(ROLE_LABELS[r] || r)}</option>`).join("");
    const companies = ['<option value="">— no company —</option>'].concat(
      (payload.companies || []).map((c) =>
        `<option value="${esc(c)}"${g.company === c ? " selected" : ""}>${esc(c)}</option>`)).join("");
    const techs = ['<option value="">— none —</option>'].concat(
      (payload.technicians || []).map((t) =>
        `<option value="${esc(t.id)}"${String(g.technician_id) === String(t.id) ? " selected" : ""}>${esc(t.name)}</option>`)).join("");
    const seen = g.last_message_at ? whenText(g.last_message_at) : null;
    return `<div class="grp ${needs ? "unmapped" : "mapped"}" data-group="${esc(g.group_id)}">
      <div class="grp-top" data-grp-toggle="1">
        <span class="grp-name">${esc(g.exact_name || g.group_id)}</span>
        <span class="grp-count">${esc(g.message_count)} msgs</span>
      </div>
      <div class="grp-state" data-grp-toggle="1">
        ${needs ? "Not mapped" : esc(ROLE_LABELS[g.role] || g.role)}${
          g.company ? " · " + esc(g.company) : ""}${
          g.service_area ? " · " + esc(g.service_area) : ""}${
          seen ? " · last " + esc(seen) : ""}
      </div>
      <div class="grp-form">
        <label>What is this group?</label>
        <select data-grp-role>${roles}</select>
        <label>Company</label>
        <select data-grp-company>${companies}</select>
        <label>Service area</label>
        <input type="text" data-grp-area value="${esc(g.service_area || "")}" placeholder="e.g. Charlotte, NC">
        <label>Technician (dispatch/closing groups)</label>
        <select data-grp-tech>${techs}</select>
        <div class="grp-hint" data-grp-hint hidden>A job-intake group needs a company.</div>
        <div class="grp-actions">
          <button class="btn primary" data-act="grp-save">Save mapping</button>
          <button class="btn ghost" data-act="grp-cancel">Cancel</button>
        </div>
      </div>
    </div>`;
  }

  function renderGroups() {
    const payload = state.groups;
    if (!payload) return;
    const filter = state.groupFilter;
    let list = payload.groups || [];
    if (filter === "needs") list = list.filter(groupNeedsMapping);
    else if (filter === "active") list = list.filter((g) => g.message_count > 0);
    $("groupsNote").textContent =
      `${payload.unclassified} of ${payload.total} groups still need mapping`;
    const note = $("groupsMenuNote");
    if (note) note.textContent = payload.unclassified
      ? `${payload.unclassified} groups need mapping` : "All groups mapped";
    $("groupList").innerHTML = list.length
      ? list.map((g) => groupRow(g, payload)).join("")
      : `<div class="empty">Nothing in this view.</div>`;
  }

  async function loadGroups() {
    if (state.screen !== "groups") return;
    try {
      state.groups = await Api.getGroups();
      renderGroups();
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("groupList").innerHTML = `<div class="empty">Groups unavailable: ${esc(e.message)}</div>`;
    }
  }

  async function saveGroupMapping(card) {
    const groupId = card.getAttribute("data-group");
    const role = card.querySelector("[data-grp-role]").value;
    const company = card.querySelector("[data-grp-company]").value;
    const area = card.querySelector("[data-grp-area]").value;
    const tech = card.querySelector("[data-grp-tech]").value;
    const hint = card.querySelector("[data-grp-hint]");
    if (role === "COMPANY_JOB_INTAKE" && !company) {
      hint.hidden = false;          // said before the request, not after
      return;
    }
    hint.hidden = true;
    try {
      await Api.mapGroup(groupId, {
        role, company, service_area: area,
        technician_id: tech === "" ? null : Number(tech),
      });
      toast("Mapping saved");
      await loadGroups();
      load();
    } catch (e) {
      if (e.authRequired) return showLogin();
      toast("Could not save: " + e.message);
    }
  }

  function progressCard(e) {
    const sel = state.selected && state.selected.col === "progress" && state.selected.id === e.id;
    const w = waitingLabel(e);
    const mins = elapsedMinutes(e.waiting_since);
    const cls = ["card", sel ? "sel" : "", w.c === "rev" ? "review" : "", w.c === "bad" ? "urgent" : ""].join(" ");
    return `<div class="${cls}" data-col="progress" data-id="${esc(e.id)}">
      <div class="card-top">
        <span class="ticket">${esc(e.ticket || "#" + e.id)}</span>
        <span class="age ${ageClass(mins)}">${esc(elapsedSince(e.waiting_since))}</span>
      </div>
      <div class="card-line">Tech: <b>${esc(e.technician_name || "unassigned")}</b></div>
      <div class="card-line">${esc(e.city || "")} ${esc(e.description ? "· " + e.description : "")}</div>
      ${lastUpdateText(e) ? `<div class="card-line">Last: ${esc(lastUpdateText(e))}</div>` : ""}
      <span class="waiting ${w.c}">${esc(displayLabel(w.t))}</span>
    </div>`;
  }

  // Reads the fields /api/closings actually returns (amount,
  // payment_method, missing, prepared_text, closing_recorded).
  function closeCard(e) {
    const sel = state.selected && state.selected.col === "close" && state.selected.id === e.id;
    const st = closingState(e);
    const cls = ["card", sel ? "sel" : "", st.ready ? "ready" : "review"].join(" ");
    return `<div class="${cls}" data-col="close" data-id="${esc(e.id)}">
      <div class="card-top">
        <span class="ticket">${esc(e.ticket || "#" + e.id)}</span>
        <span class="age">${esc(e.technician_name || "")}</span>
      </div>
      <div class="card-line">Amount: <b>${esc(e.amount ? "$" + e.amount : "not stated")}</b></div>
      <div class="card-line">Payment: <b>${esc(e.payment_method || "not stated")}</b></div>
      <span class="waiting ${st.tone}">${esc(st.label)}</span>
      ${st.ready ? `<div class="card-actions">
        <button class="btn primary" data-act="copy-closing" data-id="${esc(e.id)}">Copy closing</button>
      </div>` : ""}
    </div>`;
  }

  // A closing is READY only when the server says the amount and the
  // payment method were actually recorded and it could render the
  // company text. A bare "Done" leaves `missing` populated and must
  // never present a one-tap close.
  function closingState(e) {
    const missing = e.missing || [];
    if (!e.closing_recorded) {
      return { ready: false, tone: "", label: "Waiting for closing", missing: ["closing report"] };
    }
    if (missing.length) {
      return { ready: false, tone: "rev", label: "Missing: " + missing.join(", "), missing };
    }
    if (e.supported === false) {
      return { ready: false, tone: "rev", label: "Human review needed", missing: [e.unsupported_reason || "unsupported format"] };
    }
    if (!e.prepared_text) {
      return { ready: false, tone: "rev", label: "Human review needed", missing: ["prepared closing text"] };
    }
    return { ready: true, tone: "ok", label: "Ready to close", missing: [] };
  }

  function render() {
    const d = state.dispatch, p = state.progress, c = state.close;
    // Candidate queues must exist before any badge/count reads them.
    // `const` bindings are in JavaScript's temporal dead zone until their
    // declaration executes; reading cands/reviews above these lines crashed
    // the entire Jobs screen on mobile with "before initialization".
    const cands = state.candidates || [];
    const allReviews = state.candidateReviews || [];
    const reviews = allReviews.filter((candidate) => !isStale(candidate));
    const reconcile = allReviews.filter(isStale);
    [["countDispatch", d], ["countProgress", p], ["countClose", c],
     ["hdDispatch", d], ["hdProgress", p], ["hdClose", c],
     ["stDispatch", d], ["stProgress", p], ["stClose", c]]
      .forEach(([id, list]) => { const el = $(id); if (el) el.textContent = list.length; });
    // The badge counts CURRENT work only. Backlog keeps its own count so
    // nothing disappears, but a stale record never inflates "to dispatch".
    const dispatchTotal = d.length + cands.length + reviews.length;
    ["countDispatch", "hdDispatch", "stDispatch"].forEach((id) => {
      const el = $(id); if (el) el.textContent = dispatchTotal;
    });
    $("countDispatch").parentElement.classList.toggle("hot", dispatchTotal > 0);
    $("countClose").parentElement.classList.toggle("hot", c.length > 0);

    // Genuinely open work is NEVER filtered by age -- an open job from
    // August is still open. Only unresolved intake, which nobody can act
    // on until its company is mapped, is split out for reconciliation.
    // THE OPERATING LINE. The actionable queue is taken in the order the
    // backend already returned -- committed jobs first, then understood
    // WhatsApp candidates -- and the FIRST one is promoted to NEXT
    // ACTION. No sorting, no scoring, no new priority rule: the same
    // list, shown so the next move is unmissable.
    const actionable = [].concat(
      d.map((j) => ({ kind: "dispatch", item: j })),
      cands.map((c) => ({ kind: "candidate", item: c })));
    const cardFor = (row, isNext) => row.kind === "dispatch"
      ? dispatchCard(row.item, isNext) : candidateCard(row.item, isNext);

    let html = "";
    if (actionable.length) {
      html += `<div class="flow-h next">Next action</div>`
        + cardFor(actionable[0], true);
      if (actionable.length > 1) {
        html += `<div class="flow-h flow-up">Up next</div>`
          + actionable.slice(1).map((r) => cardFor(r, false)).join("");
      }
    }
    if (reviews.length) {
      html += `<div class="flow-h flow-up">Needs you</div>`
        + reviews.map(reviewCard).join("");
    }
    if (!actionable.length && !reviews.length) {
      html = `<div class="empty">No jobs need action right now.</div>`;
    }
    if (reconcile.length) {
      // Historical unresolved intake: real captured jobs that could never
      // be acted on because their group has no company mapped. Listed for
      // reconciliation, never closed, never reclassified.
      html += `<div class="flow-h flow-up backlog-h">Needs reconciliation · ${reconcile.length}</div>`
        + `<div class="backlog-note">Real captured intake that was never resolved &mdash; these could not be
             dispatched because their group has no company mapped. Nothing here is closed or
             reclassified automatically.</div>`
        + reconcile.map(backlogCard).join("");
    }
    $("listDispatch").innerHTML = html;
    const staleActive = state.progressStale || [];
    $("listProgress").innerHTML = (p.length ? p.map(progressCard).join("") : "")
      + (staleActive.length ? `<div class="stale-note">
          <b>${staleActive.length} STALE &mdash; REVIEW NEEDED</b><br>
          Open ${staleActive.length === 1 ? "job has" : "jobs have"} had no activity for over 24h.
          Still stored and actionable in Admin &rsaquo; Active Jobs.
        </div>` : "")
      + (!p.length && !staleActive.length ? `<div class="empty">No active jobs.</div>` : "");
    $("listClose").innerHTML = c.length ? c.map(closeCard).join("")
      : `<div class="empty">Nothing to close.</div>`;

    $("staleNote").textContent = state.staleCount
      ? state.staleCount + " older unresolved → Admin › Inbox Review" : "";

    state.order = [].concat(
      d.map((j) => ({ col: "dispatch", id: j.message_id })),
      p.map((e) => ({ col: "progress", id: e.id })),
      c.map((e) => ({ col: "close", id: e.id })));
  }

  /* ------------------------------------------------ stage switcher -- */

  function setStage(name) {
    if (!name) return;
    state.stage = name;
    $("board").setAttribute("data-stage", name);
    document.querySelectorAll(".stage").forEach((b) =>
      b.classList.toggle("on", b.getAttribute("data-stage") === name));
    // Desktop shows every column at once; bring the chosen one into view.
    const map = { dispatch: "colDispatch", progress: "colProgress", close: "colClose" };
    const el = $(map[name]);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  /* ------------------------------------------------------- detail -- */

  function confidenceChip(conf) {
    const k = String(conf || "").toUpperCase();
    const cls = k === "EXACT" ? "exact" : k === "STRONG" ? "strong"
      : k === "AMBIGUOUS" ? "amb" : "unm";
    return `<span class="chip ${cls}">${esc(k || "UNKNOWN")}</span>`;
  }

  function renderDispatchDetail(j) {
    const problems = j.review_problems || [];
    const proposed = state.lastPrepared[j.message_id] || j.luxor_message || "";
    return `
      <div class="sec">
        <div class="sec-h">Job</div>
        <div class="kv"><span class="k">Ticket</span><span class="v">${esc(j.ticket || "not stated")}</span></div>
        <div class="kv"><span class="k">Company</span><span class="v">${esc(j.source || "not stated")}</span></div>
        <div class="kv"><span class="k">Service</span><span class="v">${esc(j.service || "not stated")}</span></div>
        <div class="kv"><span class="k">Area</span><span class="v">${esc([j.city, j.state].filter(Boolean).join(", ") || "not stated")}</span></div>
        <div class="kv"><span class="k">Waiting</span><span class="v">${age(j.age_minutes) || "just now"}</span></div>
      </div>
      <div class="sec">
        <div class="sec-h">Decision</div>
        <div class="decision">
          <div class="d-detected">${esc(j.technician_name ? "Dispatch to " + j.technician_name : "No technician matched")}</div>
          <div class="d-why">Company: ${esc(j.source || "unknown")}</div>
          <div class="d-why">Area: ${esc(j.city || "not stated")}</div>
          <div class="d-why">Service: ${esc(j.service || "not stated")}</div>
          ${j.eligible_count != null ? `<div class="d-why">${esc(j.eligible_count)} technician(s) eligible by routing rule</div>` : ""}
          <div style="margin-top:7px">${confidenceChip(j.technician_name ? "EXACT" : "AMBIGUOUS")}</div>
        </div>
      </div>
      ${problems.length ? `<div class="sec"><div class="missing"><b>Needs review:</b> ${esc(problems.join(", "))}</div></div>` : ""}
      <div class="sec">
        <div class="sec-h">Proposed technician message</div>
        <div class="msgbox" id="msgBox">${esc(proposed || "Press Dispatch to prepare the message.")}</div>
      </div>`;
  }

  function renderProgressDetail(e) {
    const w = waitingLabel(e);
    const wf = e.workflow || {};
    return `
      <div class="sec">
        <div class="sec-h">Job</div>
        <div class="kv"><span class="k">Ticket</span><span class="v">${esc(e.ticket || "not stated")}</span></div>
        <div class="kv"><span class="k">Company</span><span class="v">${esc(e.source || "not stated")}</span></div>
        <div class="kv"><span class="k">Technician</span><span class="v">${esc(e.technician_name || "unassigned")}</span></div>
        <div class="kv"><span class="k">Area</span><span class="v">${esc(e.city || "not stated")}</span></div>
        <div class="kv"><span class="k">State</span><span class="v">${esc(e.status || "")}</span></div>
        <div class="kv"><span class="k">Waiting</span><span class="v">${esc(elapsedSince(e.waiting_since) || "just now")}</span></div>
      </div>
      <div class="sec">
        <div class="sec-h">Luxor</div>
        <div class="decision">
          <div class="d-detected">${esc(w.t)}</div>
          ${lastUpdateText(e) ? `<div class="d-why">Last technician update: ${esc(lastUpdateText(e))}</div>` : ""}
          ${w.next ? `<div class="d-why">Waiting for: ${esc(w.next)}</div>` : ""}
          ${e.company_update_owed ? `<div class="d-why">A company update is owed</div>` : ""}
        </div>
      </div>
      ${wf.next_message ? `
      <div class="sec">
        <div class="sec-h">Proposed company update</div>
        <div class="msgbox" id="msgBox">${esc(wf.next_message)}</div>
      </div>` : ""}
      <div class="sec">
        <div class="sec-h">Open in full workspace</div>
        <div class="detail-actions">
          <button class="btn ghost" data-act="admin-job" data-id="${esc(e.id)}">Admin &rsaquo; job #${esc(e.id)}</button>
        </div>
      </div>`;
  }

  function renderCloseDetail(e) {
    const st = closingState(e);
    return `
      <div class="sec">
        <div class="sec-h">Closing</div>
        <div class="kv"><span class="k">Ticket</span><span class="v">${esc(e.ticket || "not stated")}</span></div>
        <div class="kv"><span class="k">Technician</span><span class="v">${esc(e.technician_name || "not stated")}</span></div>
        <div class="kv"><span class="k">Amount</span><span class="v">${esc(e.amount ? "$" + e.amount : "not stated")}</span></div>
        <div class="kv"><span class="k">Payment</span><span class="v">${esc(e.payment_method || "not stated")}</span></div>
      </div>
      ${st.ready ? `
      <div class="sec">
        <div class="sec-h">Closing message</div>
        <div class="msgbox" id="msgBox">${esc(e.prepared_text)}</div>
      </div>` : `
      <div class="sec"><div class="missing">
          <b>Human review needed — missing: ${esc(st.missing.join(", "))}</b><br>
          These are never invented. A job is not closed on an implied amount.
          Enter them in the full workspace only if you actually know them.
        </div></div>`}
      <div class="sec">
        <div class="detail-actions">
          <button class="btn ghost" data-act="admin-job" data-id="${esc(e.id)}">Open in Admin</button>
        </div>
      </div>`;
  }

  function renderCandidateDetail(c) {
    return `
      <div class="sec">
        <div class="sec-h">Job (from WhatsApp)</div>
        <div class="kv"><span class="k">Ticket</span><span class="v">${esc(c.ticket || "not stated")}</span></div>
        <div class="kv"><span class="k">Company</span><span class="v">${esc(c.source || "not stated")}</span></div>
        <div class="kv"><span class="k">Service</span><span class="v">${esc(c.service || "not stated")}</span></div>
        <div class="kv"><span class="k">Area</span><span class="v">${esc([c.city, c.state].filter(Boolean).join(", ") || "not stated")}</span></div>
        <div class="kv"><span class="k">Group</span><span class="v">${esc(c.group_name || "")}</span></div>
      </div>
      <div class="sec">
        <div class="sec-h">Decision</div>
        <div class="decision">
          <div class="d-detected">${esc(c.detected)}</div>
          ${(c.why || []).map((w) => `<div class="d-why">${esc(w)}</div>`).join("")}
          <div style="margin-top:7px">${confidenceChip(c.confidence)}</div>
        </div>
      </div>
      <div class="sec">
        <div class="missing">Not a committed job yet &mdash; this is what Banna understood
          from WhatsApp. Copy the message and send it yourself.</div>
      </div>
      ${shadowSection(c)}
      <div class="sec">
        <div class="sec-h">Proposed technician message</div>
        <div class="msgbox" id="msgBox">${esc(c.proposed_message || "")}</div>
      </div>
      ${timelineSection(c)}`;
  }

  // What Banna decided, and what the human actually did. Display only:
  // the proposed text is shown, never transmitted, and a review hold
  // states its decision without offering a way to act on it.
  function shadowSection(c) {
    const d = shadowDecision(c);
    const cmp = comparison(c);
    const human = humanAction(c);
    return `
      <div class="sec">
        <div class="sec-h">Banna decision &mdash; simulation only</div>
        <div class="decision">
          <div class="d-detected">${esc(d.label)}${
            c.recommended ? " &middot; " + esc(c.recommended) : ""}</div>
          ${c.confidence ? `<div class="d-why">Confidence: ${esc(displayLabel(c.confidence))}</div>` : ""}
          ${(c.why || []).map((w) => `<div class="d-why">${esc(w)}</div>`).join("")}
          <div class="d-why"><b>Banna sent nothing.</b> Every message here is a
            draft for a human to send.</div>
        </div>
      </div>
      <div class="sec">
        <div class="sec-h">Human action</div>
        <div class="decision">
          ${human
            ? `<div class="d-detected">${esc(human.technician || "dispatched")}${
                 human.destination ? " &middot; " + esc(human.destination) : ""}</div>
               ${human.at ? `<div class="d-why">${esc(formatDisplayTime(parseEventStamp(human.at)))}</div>` : ""}
               ${human.channel ? `<div class="d-why">${esc(human.channel)}</div>` : ""}`
            : `<div class="d-detected">Waiting for human action</div>`}
          <div class="d-why sh-${cmp.state === "match" ? "match" : cmp.state === "diff" ? "diff" : "waiting"}">${esc(cmp.text)}</div>
        </div>
      </div>`;
  }

  // Operational trace. Real, and kept -- but collapsed, because it is
  // evidence for a question the operator only sometimes asks. Times are
  // rendered by the one shared 12-hour formatter.
  function timelineSection(item) {
    const rows = [
      ["Received", item.received_at, item.group_name],
      ["Event time", item.event_at, "as sent in WhatsApp"],
      ["Parsed", item.received_at, [item.service, item.city].filter(Boolean).join(" · ")],
      ["Correlated", item.event_at, item.ticket ? item.ticket + (item.confidence ? " · " + item.confidence : "") : ""],
      ["Routing", item.event_at, item.recommended || "no technician matched"],
    ].filter((r) => r[1]);
    if (!rows.length) return "";
    return `
      <div class="sec">
        <details class="timeline">
          <summary>Timeline</summary>
          ${rows.map(([label, t, detail]) => `
            <div class="tl-row">
              <span class="tl-t">${esc(formatDisplayTime(t))}</span>
              <span class="tl-type">${esc(label)}</span>
              ${detail ? `<span class="tl-d">${esc(detail)}</span>` : ""}
            </div>`).join("")}
        </details>
      </div>`;
  }

  // The one primary action, pinned to the bottom of the sheet where a
  // thumb reaches it. Copy commits nothing; Dispatch is the only button
  // here that changes state, and it stays an explicit labelled tap.
  function detailBar(col, item) {
    if (col === "candidate-review") return "";   // never actionable
    if (col === "candidate") {
      return item.proposed_message
        ? `<button class="btn primary" data-act="copy-candidate" data-id="${esc(item.message_id)}">Copy message</button>` : "";
    }
    if (col === "dispatch") {
      const proposed = state.lastPrepared[item.message_id] || item.luxor_message || "";
      return `<button class="btn primary" data-act="dispatch" data-id="${esc(item.message_id)}">Dispatch &amp; copy</button>`
        + (proposed ? `<button class="btn" data-act="copy">Copy (C)</button>` : "");
    }
    if (col === "progress") {
      const wf = item.workflow || {};
      return wf.next_message ? `<button class="btn primary" data-act="copy">Copy company update</button>` : "";
    }
    const st = closingState(item);
    return st.ready
      ? `<button class="btn primary" data-act="copy-closing" data-id="${esc(item.id)}">Copy closing</button>` : "";
  }

  // A row the current projection holds in memory opens instantly; one
  // it does not (any range beyond what the board loaded) is fetched.
  // Without this, every Jobs-list row outside today silently did
  // nothing when tapped.
  async function openDetailFetched(col, id) {
    try {
      const r = await Api.getCrmJob(id);
      if (!r || !r.job) return;
      state.candidates = (state.candidates || []).concat([r.job]);
      openDetail(col, id);
    } catch (e) {
      $("detailTitle").textContent = "Job";
      $("detailBody").innerHTML =
        `<div class="sec"><div class="missing">This job could not be loaded: ${esc(e.message || e)}</div></div>`;
      $("detail").hidden = false;
    }
  }

  function openDetail(col, id) {
    state.selected = { col, id };
    state.paused = true;                       // never re-render under the operator
    let item = null, html = "", title = "";
    if (col === "dispatch") {
      item = state.dispatch.find((j) => j.message_id === id);
      if (item) { html = renderDispatchDetail(item); title = item.ticket || "New job"; }
    } else if (col === "progress") {
      item = state.progress.find((e) => String(e.id) === String(id));
      if (item) { html = renderProgressDetail(item); title = item.ticket || "#" + item.id; }
    } else if (col === "candidate-review") {
      item = (state.candidateReviews || []).find((c) => c.message_id === id);
      if (item) { html = renderCandidateDetail(item); title = item.ticket || "Needs review"; }
    } else if (col === "candidate") {
      item = (state.candidates || []).find((c) => c.message_id === id);
      if (item) { html = renderCandidateDetail(item); title = item.ticket || "WhatsApp job"; }
    } else {
      item = state.close.find((e) => String(e.id) === String(id));
      if (item) { html = renderCloseDetail(item); title = item.ticket || "#" + item.id; }
    }
    // Not in memory -- fetch it rather than failing silently, which is
    // exactly what "I press a job and nothing happens" looked like.
    if (!item) {
      if (col === "candidate") openDetailFetched(col, id);
      return;
    }
    state.detail = item;
    $("detailTitle").textContent = title;
    $("detailBody").innerHTML = html;
    const bar = detailBar(col, item);
    $("detailBar").innerHTML = bar;
    $("detailBar").hidden = !bar;
    $("detail").hidden = false;
    render();
  }

  function closeDetail() {
    $("detail").hidden = true;
    state.detail = null; state.paused = false;
    render();
  }

  /* ------------------------------------------------------ actions -- */

  async function doDispatch(messageId) {
    // Reuses the existing audited flow exactly: acknowledge -> prepare
    // -> confirm. Nothing new, nothing automatic, nothing transmitted.
    try {
      const j = state.dispatch.find((x) => x.message_id === messageId);
      if (j && j.needs_review) {
        toast("This job needs review first — opening details");
        openDetail("dispatch", messageId);
        return;
      }
      toast("Preparing…");
      await Api.acknowledge(messageId);
      const prep = await Api.prepareDispatch(messageId);
      const text = (prep && (prep.luxor_message || prep.message)) || "";
      state.lastPrepared[messageId] = text;
      await Api.confirmDispatch(messageId);
      if (text) await copy(text, "Technician message ready");
      else toast("Dispatched — no message text returned");
      closeDetail();
      await load();
    } catch (e) {
      if (e.authRequired) return showLogin();
      toast("Dispatch failed: " + e.message);
    }
  }

  // Asks the server to render the closing text from persisted fields --
  // the client never composes a closing itself, and never fills a blank.
  async function doCopyClosing(jobId) {
    try {
      const entry = state.close.find((e) => String(e.id) === String(jobId));
      if (entry && !closingState(entry).ready) {
        toast("Not ready to close — missing facts");
        return openDetail("close", jobId);
      }
      if (entry && entry.prepared_text) return copy(entry.prepared_text, "Closing");
      const r = await Api.prepareClosing(jobId);
      const text = (r && (r.prepared_text || r.message || r.luxor_message)) || "";
      if (text) await copy(text, "Closing");
      else toast("No closing text available — open in Admin");
    } catch (e) {
      if (e.authRequired) return showLogin();
      toast("Could not prepare closing: " + e.message);
    }
  }

  function onClick(ev) {
    const stageEl = ev.target.closest("[data-stage]");
    if (stageEl && stageEl.classList.contains("stage")) {
      return setStage(stageEl.getAttribute("data-stage"));
    }
    const actEl = ev.target.closest("[data-act]");
    if (actEl) {
      ev.stopPropagation();
      const act = actEl.getAttribute("data-act");
      if (act === "dispatch") return doDispatch(actEl.getAttribute("data-id"));
      if (act === "copy-closing") return doCopyClosing(actEl.getAttribute("data-id"));
      if (act === "copy-candidate") {
        const c = (state.candidates || []).find((x) => x.message_id === actEl.getAttribute("data-id"));
        return c && c.proposed_message ? copy(c.proposed_message, "Technician message") : null;
      }
      if (act === "copy") {
        const box = $("msgBox");
        return box ? copy(box.textContent, "Message") : null;
      }
      if (act === "shadow-replay") return loadHistoricalShadow();
      if (act === "admin-job") { window.location.href = "admin.html"; return; }
      // A safety hold opens its detail. It is deliberately NOT a send,
      // NOT a copy, and NOT a dispatch -- the operator is being shown the
      // decision, not handed a way around it.
      if (act === "review") {
        return openDetail("candidate-review", actEl.getAttribute("data-id"));
      }
      if (act === "jobs-clear") {
        return openJobs({ range: state.jobsQuery.range });
      }
      if (act === "grp-save") {
        const card = actEl.closest(".grp");
        return card ? saveGroupMapping(card) : null;
      }
      if (act === "grp-cancel") {
        const card = actEl.closest(".grp");
        if (card) card.classList.remove("open");
        return;
      }
      if (act === "ap-evidence") {
        const card = actEl.closest(".ap-card");
        if (card) card.classList.toggle("open");
        return;
      }
    }
    const tabEl = ev.target.closest(".tab");
    if (tabEl) return setScreen(tabEl.getAttribute("data-screen"));
    const menuEl = ev.target.closest("[data-goto]");
    if (menuEl) return setScreen(menuEl.getAttribute("data-goto"));
    const grpToggle = ev.target.closest("[data-grp-toggle]");
    if (grpToggle) {
      const card = grpToggle.closest(".grp");
      if (card) card.classList.toggle("open");
      return;
    }
    const palEl = ev.target.closest("[data-palette]");
    if (palEl && palEl.classList.contains("pal")) {
      applyPalette(palEl.getAttribute("data-palette"));
      return renderPalettes();
    }
    const gfilterEl = ev.target.closest("[data-gfilter]");
    if (gfilterEl) {
      state.groupFilter = gfilterEl.getAttribute("data-gfilter");
      document.querySelectorAll("[data-gfilter]").forEach((b) =>
        b.classList.toggle("on", b === gfilterEl));
      return renderGroups();
    }
    const rangeEl = ev.target.closest("[data-range]");
    if (rangeEl) {
      state.reportRange = rangeEl.getAttribute("data-range");
      document.querySelectorAll("[data-range]").forEach((b) =>
        b.classList.toggle("on", b === rangeEl));
      return loadReports();
    }
    const statEl = ev.target.closest("[data-jump-jobs]");
    if (statEl) {
      const which = statEl.getAttribute("data-jump-jobs");
      return openJobs({ range: "today", status: which === "all" ? "" : which });
    }
    const rankEl = ev.target.closest("[data-rank-kind]");
    if (rankEl) {
      const kind = rankEl.getAttribute("data-rank-kind");
      const value = rankEl.getAttribute("data-rank-value");
      return openJobs(kind === "company" ? { range: "today", company: value }
                                         : { range: "today", area: value });
    }
    const jrangeEl = ev.target.closest("[data-jrange]");
    if (jrangeEl) {
      state.jobsQuery.range = jrangeEl.getAttribute("data-jrange");
      document.querySelectorAll("[data-jrange]").forEach((b) => b.classList.toggle("on", b === jrangeEl));
      return loadJobs();
    }
    const jcoEl = ev.target.closest("[data-jco]");
    if (jcoEl) {
      state.jobsQuery.company = jcoEl.getAttribute("data-jco");
      document.querySelectorAll("[data-jco]").forEach((b) => b.classList.toggle("on", b === jcoEl));
      return loadJobs();
    }
    const jstatusEl = ev.target.closest("[data-jstatus]");
    if (jstatusEl) {
      state.jobsQuery.status = jstatusEl.getAttribute("data-jstatus");
      document.querySelectorAll("[data-jstatus]").forEach((b) => b.classList.toggle("on", b === jstatusEl));
      return loadJobs();
    }
    const evEl = ev.target.closest("[data-ev]");
    if (evEl) { evEl.classList.toggle("open"); return; }
    // Matches on the data attributes, not on a class name. The Jobs
    // list renders .job-row and the board renders .card; keying this to
    // .card alone meant every row in the Jobs list was dead to the
    // touch -- the list looked like a product and behaved like a poster.
    const card = ev.target.closest("[data-col][data-id]");
    if (card) return openDetail(card.getAttribute("data-col"), card.getAttribute("data-id"));
  }

  function move(delta) {
    if (!state.order.length) return;
    let i = state.order.findIndex((o) => state.selected &&
      o.col === state.selected.col && String(o.id) === String(state.selected.id));
    i = i < 0 ? 0 : Math.min(state.order.length - 1, Math.max(0, i + delta));
    const target = state.order[i];
    state.selected = target;
    if (target.col !== state.stage) setStage(target.col);
    render();
    const el = document.querySelector(`.card[data-col="${target.col}"][data-id="${CSS.escape(String(target.id))}"]`);
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  function onKey(ev) {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName);
    if (typing) {
      if (ev.key === "Escape") ev.target.blur();
      return;
    }
    if (ev.key === "/") { ev.preventDefault(); $("searchInput").focus(); return; }
    if (ev.key === "Escape") return closeDetail();
    if (ev.key === "j" || ev.key === "J") return move(1);
    if (ev.key === "k" || ev.key === "K") return move(-1);
    if (ev.key === "Enter" && state.selected) return openDetail(state.selected.col, state.selected.id);
    if (ev.key === "1") return setStage("dispatch");
    if (ev.key === "2") return setStage("progress");
    if (ev.key === "3") return setStage("close");
    if (ev.key === "c" || ev.key === "C") {
      const box = $("msgBox");
      if (box) copy(box.textContent, "Message");
      return;
    }
    // Deliberately requires an already-open selection in the dispatch
    // column: a stray "d" must never dispatch something the operator is
    // not looking at.
    if ((ev.key === "d" || ev.key === "D") && state.selected && state.selected.col === "dispatch") {
      return doDispatch(state.selected.id);
    }
  }

  /* -------------------------------------------------------- search -- */
  let searchTimer = null;
  function onSearch() {
    clearTimeout(searchTimer);
    const q = $("searchInput").value.trim();
    if (q.length < 2) { $("searchResults").hidden = true; return; }
    searchTimer = setTimeout(async () => {
      try {
        const r = await Api.search(q);
        const items = (r && r.results) || [];
        $("searchResults").innerHTML = items.length
          ? items.map((it) => `<div class="sr-item">${esc(it.label || it.ticket || JSON.stringify(it))}</div>`).join("")
          : `<div class="sr-item">No matches</div>`;
        $("searchResults").hidden = false;
      } catch (e) { $("searchResults").hidden = true; }
    }, 220);
  }

  /* --------------------------------------------------------- boot -- */

  function showLogin() {
    applyTheme();
    $("loginScreen").hidden = false;
    $("app").hidden = true;
  }
  function showApp() {
    $("loginScreen").hidden = true;
    $("app").hidden = false;
    applyPalette(savedPalette());
    setInterval(() => { if (savedPalette() === "auto") applyTheme(); }, 60000);   // a shift outlives one render
    setStage(state.stage);
    setScreen(state.screen);
    loadToday();
    load(); health();
    setInterval(load, POLL_MS);
    setInterval(health, 15000);
    setInterval(loadLive, POLL_MS);
  }

  async function doLogin() {
    // Clear any previous failure first, so a stale "Invalid password."
    // cannot linger after a subsequent successful sign-in.
    $("loginError").textContent = "";
    try {
      await Api.login($("loginPassword").value);
      $("loginPassword").value = "";   // never leave the password in the DOM
      showApp();
    } catch (e) { $("loginError").textContent = e.message; }
  }

  // Application shell only -- see sw.js, which is forbidden from caching
  // anything under /api/. Registration needs a secure context, so over
  // plain LAN http this is skipped silently rather than logging an error
  // the operator would have no way to act on.
  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    if (!window.isSecureContext) return;
    navigator.serviceWorker.register("/sw.js").catch(() => { /* shell cache is optional */ });
  }

  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKey);
  $("loginBtn").addEventListener("click", doLogin);
  $("loginPassword").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  $("detailClose").addEventListener("click", closeDetail);
  $("adminBtn").addEventListener("click", () => { window.location.href = "admin.html"; });
  $("searchInput").addEventListener("input", onSearch);
  (function wireJobSearch() {
    const box = $("jobSearch");
    if (!box) return;
    let timer = null;
    box.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.jobsQuery.q = box.value.trim(); loadJobs(); }, 220);
    });
  })();
  document.querySelectorAll("[data-jump]").forEach((b) =>
    b.addEventListener("click", () => { setScreen("jobs"); setStage(b.getAttribute("data-jump")); }));

  registerServiceWorker();
  Api.checkSession().then(showApp).catch(showLogin);
})();
