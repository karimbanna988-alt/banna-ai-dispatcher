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
    stage: "dispatch",    // which stage owns the phone viewport
    selected: null,       // { col, id }
    detail: null,         // resolved detail payload
    order: [],            // flat nav order for J/K
    paused: false,        // suspend polling while a detail panel is open
    lastPrepared: {},     // messageId -> prepared technician text
    failures: 0,          // consecutive failed polls
    offline: false,
    lastGood: null,       // Date of the last successful load
    liveOpen: false,      // Live Operations panel visibility
    apprenticeOpen: false,// Apprentice review panel visibility
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
        ? "last live " + state.lastGood.toLocaleTimeString() : "never reached the server";
      $("offlineDetail").textContent = `not live · ${since}${why ? " · " + why : ""}`;
    }
  }

  /* ------------------------------------------------------ load ----- */

  async function load() {
    if (state.paused) return;
    try {
      const [newJobs, active, closings, wa] = await Promise.all([
        Api.getNewJobs(), Api.getActive(), Api.getClosings(),
        // WhatsApp jobs understood and routed but NOT committed jobs.
        // A failure here must never blank the real board.
        Api.getWhatsappCandidates().catch(() => ({ candidates: [] })),
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
      // Only the ones the routing rules actually resolved reach the
      // board; the rest stay in Live Operations as human-review items.
      state.candidates = (wa.candidates || []).filter(
        (c) => c.status === "WAITING FOR HUMAN DISPATCH");

      state.failures = 0;
      state.lastGood = new Date();
      if (state.offline) setOffline(false);

      render();
      loadDemo();
      $("updatedAt").textContent = state.lastGood.toLocaleTimeString();
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

      $("hbShadow").textContent = h.shadow_mode || "--";
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
      $("loBody").innerHTML = events.length ? events.map((ev) => `
        <div class="lo-ev lo-${esc((ev.type || "").toLowerCase().replace(/\s+/g, "-"))}">
          <span class="lo-t">${esc(clockOf(ev.time))}</span>
          <span class="lo-type">${esc(ev.type)}</span>
          <span class="lo-title">${esc(ev.title)}</span>
          ${ev.confidence ? `<span class="lo-conf">${esc(ev.confidence)}</span>` : ""}
          ${ev.needs_human ? `<span class="lo-needs">needs you</span>` : ""}
          ${(ev.why || []).length ? `<div class="lo-why">${
            ev.why.map((w) => `<span>${esc(w)}</span>`).join("")}</div>` : ""}
        </div>`).join("")
        : `<div class="empty">No recent operational events.</div>`;
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("loBody").innerHTML = `<div class="empty">Live feed unavailable: ${esc(e.message)}</div>`;
    }
  }

  function clockOf(iso) {
    if (!iso) return "";
    const t = new Date(iso);
    return isNaN(t.getTime()) ? "" : t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
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
      $("apBody").innerHTML = list.length ? list.map((s) => `
        <div class="ap-card ap-${esc(String(s.confidence || "").toLowerCase())}">
          <div class="ap-top">
            <span class="ap-status">${esc(s.status)}</span>
            <span class="ap-conf">${esc(s.confidence)}</span>
          </div>
          <div class="ap-rule">
            ${esc(s.source || "?")} &middot; ${esc(s.area || "any area")} &middot;
            ${esc(s.service || "any service")} &rarr; <b>${esc(s.destination)}</b>
          </div>
          <div class="ap-ev">
            ${esc(s.evidence.distinct_tickets)} tickets &middot;
            ${esc(s.evidence.distinct_days)} days &middot;
            ${esc(s.conflicts)} conflicts
          </div>
          ${(s.notes || []).map((n) => `<div class="ap-note">${esc(n)}</div>`).join("")}
        </div>`).join("")
        : `<div class="empty">No pattern has enough evidence yet. That is the correct
             result for thin evidence &mdash; one example never makes a rule.</div>`;
    } catch (e) {
      if (e.authRequired) return showLogin();
      $("apBody").innerHTML = `<div class="empty">Apprentice unavailable: ${esc(e.message)}</div>`;
    }
  }

  function toggleApprentice() {
    state.apprenticeOpen = !state.apprenticeOpen;
    $("apprentice").hidden = !state.apprenticeOpen;
    $("apprenticeToggle").setAttribute("aria-expanded", String(state.apprenticeOpen));
    $("apprenticeToggle").classList.toggle("on", state.apprenticeOpen);
    if (state.apprenticeOpen) loadApprentice();
  }

  function toggleLive() {
    state.liveOpen = !state.liveOpen;
    $("liveops").hidden = !state.liveOpen;
    $("liveToggle").setAttribute("aria-expanded", String(state.liveOpen));
    $("liveToggle").classList.toggle("on", state.liveOpen);
    if (state.liveOpen) loadLive();
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

  function dispatchCard(j) {
    const sel = state.selected && state.selected.col === "dispatch" && state.selected.id === j.message_id;
    const review = j.needs_review || (j.review_problems && j.review_problems.length);
    const cls = ["card", sel ? "sel" : "", review ? "review" : "", ageClass(j.age_minutes) === "bad" ? "urgent" : ""].join(" ");
    const tech = j.technician_name || (j.eligible_count ? j.eligible_count + " eligible" : "no match");
    return `<div class="${cls}" data-col="dispatch" data-id="${esc(j.message_id)}">
      <div class="card-top">
        <span class="ticket">${esc(j.ticket || "no ticket")}</span>
        <span class="age ${ageClass(j.age_minutes)}">${age(j.age_minutes)}</span>
      </div>
      <div class="card-line"><b>${esc(j.service || "service not stated")}</b></div>
      <div class="card-line">${esc(j.source || "source not stated")}</div>
      <div class="card-line">${esc([j.city, j.state].filter(Boolean).join(", ") || "location not stated")}</div>
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
  function candidateCard(c) {
    return `<div class="card candidate" data-col="candidate" data-id="${esc(c.message_id)}">
      <div class="card-top">
        <span class="ticket">${esc(c.ticket || "no ticket")}</span>
        <span class="cand-tag">WhatsApp</span>
        <span class="age">${esc(elapsedSince(c.received_at))}</span>
      </div>
      <div class="card-line"><b>${esc(c.service || "service not stated")}</b></div>
      <div class="card-line">${esc(c.source || "source not stated")}</div>
      <div class="card-line">${esc([c.city, c.state].filter(Boolean).join(", ") || "location not stated")}</div>
      <div class="rec">
        <span class="rec-l">Recommended</span>
        <span class="rec-v">${esc(c.recommended)}</span>
        ${confidenceChip(c.confidence)}
      </div>
      <span class="waiting act">${esc(c.status)}</span>
      <div class="card-actions">
        <button class="btn primary" data-act="copy-candidate" data-id="${esc(c.message_id)}">Copy message</button>
      </div>
    </div>`;
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
      <span class="waiting ${w.c}">${esc(w.t)}</span>
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
    [["countDispatch", d], ["countProgress", p], ["countClose", c],
     ["hdDispatch", d], ["hdProgress", p], ["hdClose", c],
     ["stDispatch", d], ["stProgress", p], ["stClose", c]]
      .forEach(([id, list]) => { const el = $(id); if (el) el.textContent = list.length; });
    const dispatchTotal = d.length + (state.candidates || []).length;
    ["countDispatch", "hdDispatch", "stDispatch"].forEach((id) => {
      const el = $(id); if (el) el.textContent = dispatchTotal;
    });
    $("countDispatch").parentElement.classList.toggle("hot", dispatchTotal > 0);
    $("countClose").parentElement.classList.toggle("hot", c.length > 0);

    const cands = state.candidates || [];
    $("listDispatch").innerHTML = (d.length ? d.map(dispatchCard).join("") : "")
      + (cands.length ? cands.map(candidateCard).join("") : "")
      + (!d.length && !cands.length
          ? `<div class="empty">Nothing waiting to dispatch.</div>` : "");
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
      <div class="sec">
        <div class="sec-h">Proposed technician message</div>
        <div class="msgbox" id="msgBox">${esc(c.proposed_message || "")}</div>
      </div>`;
  }

  // The one primary action, pinned to the bottom of the sheet where a
  // thumb reaches it. Copy commits nothing; Dispatch is the only button
  // here that changes state, and it stays an explicit labelled tap.
  function detailBar(col, item) {
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
    } else if (col === "candidate") {
      item = (state.candidates || []).find((c) => c.message_id === id);
      if (item) { html = renderCandidateDetail(item); title = item.ticket || "WhatsApp job"; }
    } else {
      item = state.close.find((e) => String(e.id) === String(id));
      if (item) { html = renderCloseDetail(item); title = item.ticket || "#" + item.id; }
    }
    if (!item) return;
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
      if (act === "admin-job") { window.location.href = "admin.html"; return; }
    }
    const card = ev.target.closest(".card");
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
    $("loginScreen").hidden = false;
    $("app").hidden = true;
  }
  function showApp() {
    $("loginScreen").hidden = true;
    $("app").hidden = false;
    setStage(state.stage);
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
  $("liveToggle").addEventListener("click", toggleLive);
  $("apprenticeToggle").addEventListener("click", toggleApprentice);
  document.querySelectorAll("[data-jump]").forEach((b) =>
    b.addEventListener("click", () => setStage(b.getAttribute("data-jump"))));

  registerServiceWorker();
  Api.checkSession().then(showApp).catch(showLogin);
})();
