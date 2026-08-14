/* LUXOR Dispatch Console.

   The CRM is the memory; this is the product. Three operational
   columns -- TO DISPATCH / IN PROGRESS / TO CLOSE -- built entirely
   from the existing API routes (/api/new_jobs, /api/active,
   /api/closings). No new backend, no new framework.

   SAFETY: this file cannot send anything. It prepares text and copies
   it to the clipboard; a human pastes it into WhatsApp. Every state
   change goes through the same audited POST routes the previous UI
   used. There is no code path here that sends a WhatsApp message or
   writes to Quo. */

(function () {
  "use strict";

  const POLL_MS = 5000;
  // The main board is CURRENT operational work. Pre-commit messages
  // older than this belong to Inbox Review in Admin, not to a live
  // dispatch board -- a 300-hour-old unresolved message must never
  // visually compete with something that arrived 30 seconds ago.
  const DISPATCH_MAX_AGE_MIN = 24 * 60;

  const state = {
    dispatch: [], progress: [], close: [],
    selected: null,       // { col, id }
    detail: null,         // resolved detail payload
    order: [],            // flat nav order for J/K
    paused: false,        // suspend polling while a detail panel is open
    lastPrepared: {},     // messageId -> prepared technician text
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

  /* ------------------------------------------------------ load ----- */

  async function load() {
    if (state.paused) return;
    try {
      const [newJobs, active, closings] = await Promise.all([
        Api.getNewJobs(), Api.getActive(), Api.getClosings(),
      ]);

      // TO DISPATCH: live bucket first. `older` is deliberately excluded
      // from the board -- it is historical pre-commit material and lives
      // in Admin > Inbox Review.
      const live = (newJobs.live || []).filter(
        (j) => j.age_minutes == null || j.age_minutes <= DISPATCH_MAX_AGE_MIN);
      state.dispatch = live;
      state.staleCount = (newJobs.older || []).length;

      state.progress = (active.entries || []);
      state.close = (closings.entries || []);

      render();
      loadDemo();
      $("updatedAt").textContent = new Date().toLocaleTimeString();
    } catch (e) {
      if (e.authRequired) return showLogin();
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

  async function health() {
    try {
      const s = await Api.getWhatsappBridgeStatus();
      $("dotWa").className = "dot " + (s && s.paired ? "up" : "down");
      $("dotAdapter").className = "dot " + (s ? "up" : "down");
    } catch (e) { $("dotWa").className = "dot down"; }
    $("dotCrm").className = "dot up"; // reaching this code means the CRM answered
  }

  /* ---------------------------------------------------- rendering -- */

  function waitingLabel(e) {
    const na = (e.next_action || "").toUpperCase();
    if (na.includes("HUMAN_REVIEW")) return { t: "Needs review", c: "rev" };
    if (na.includes("K")) return { t: "Waiting for tech K", c: "act" };
    if (na.includes("UPDATE_COMPANY")) return { t: "Update company", c: "act" };
    if (na.includes("CLOSING")) return { t: "Waiting for closing", c: "" };
    if (na.includes("REASSIGN") || na.includes("DECLINED")) return { t: "Needs reassignment", c: "bad" };
    if (e.bucket) return { t: String(e.bucket).replace(/_/g, " "), c: "" };
    return { t: e.status || "In progress", c: "" };
  }

  function dispatchCard(j) {
    const sel = state.selected && state.selected.col === "dispatch" && state.selected.id === j.message_id;
    const review = j.needs_review || (j.review_problems && j.review_problems.length);
    const cls = ["card", sel ? "sel" : "", review ? "review" : "", ageClass(j.age_minutes) === "bad" ? "urgent" : ""].join(" ");
    const tech = j.technician_name || (j.eligible_count ? j.eligible_count + " eligible" : "no match");
    return `<div class="${cls}" data-col="dispatch" data-id="${esc(j.message_id)}">
      <div class="card-top">
        <span class="ticket">${esc(j.ticket || "no ticket")}</span>
        <span class="src">${esc(j.source || "")}</span>
        <span class="age ${ageClass(j.age_minutes)}">${age(j.age_minutes)}</span>
      </div>
      <div class="card-line"><b>${esc(j.service || "service not stated")}</b></div>
      <div class="card-line">${esc([j.city, j.state].filter(Boolean).join(", ") || "location not stated")}</div>
      <div class="card-line">Tech: <b>${esc(tech)}</b></div>
      ${review ? `<span class="waiting rev">Needs review</span>` : `<span class="waiting act">Ready to dispatch</span>`}
      <div class="card-actions">
        <button class="btn primary" data-act="dispatch" data-id="${esc(j.message_id)}">Dispatch</button>
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
        <span class="src">${esc(e.source || "")}</span>
        <span class="age ${ageClass(mins)}">${esc(elapsedSince(e.waiting_since))}</span>
      </div>
      <div class="card-line">Tech: <b>${esc(e.technician_name || "unassigned")}</b></div>
      <div class="card-line">${esc(e.city || "")} ${esc(e.description ? "· " + e.description : "")}</div>
      ${e.latest_technician_update ? `<div class="card-line">Last: ${esc(e.latest_technician_update)}</div>` : ""}
      <span class="waiting ${w.c}">${esc(w.t)}</span>
    </div>`;
  }

  function closeCard(e) {
    const sel = state.selected && state.selected.col === "close" && state.selected.id === e.id;
    const missing = e.missing_fields || [];
    const ready = missing.length === 0;
    const cls = ["card", sel ? "sel" : "", ready ? "ready" : "review"].join(" ");
    return `<div class="${cls}" data-col="close" data-id="${esc(e.id)}">
      <div class="card-top">
        <span class="ticket">${esc(e.ticket || "#" + e.id)}</span>
        <span class="age">${esc(e.technician_name || "")}</span>
      </div>
      <div class="card-line">Amount: <b>${esc(e.closing_amount || "not stated")}</b></div>
      <div class="card-line">Payment: <b>${esc(e.closing_payment_method || "not stated")}</b></div>
      ${ready ? `<span class="waiting ok">Ready to close</span>`
              : `<span class="waiting rev">Missing: ${esc(missing.join(", "))}</span>`}
    </div>`;
  }

  function render() {
    const d = state.dispatch, p = state.progress, c = state.close;
    $("countDispatch").textContent = d.length;
    $("countProgress").textContent = p.length;
    $("countClose").textContent = c.length;
    $("hdDispatch").textContent = d.length;
    $("hdProgress").textContent = p.length;
    $("hdClose").textContent = c.length;
    $("countDispatch").parentElement.classList.toggle("hot", d.length > 0);
    $("countClose").parentElement.classList.toggle("hot", c.length > 0);

    $("listDispatch").innerHTML = d.length ? d.map(dispatchCard).join("")
      : `<div class="empty">Nothing waiting to dispatch.</div>`;
    $("listProgress").innerHTML = p.length ? p.map(progressCard).join("")
      : `<div class="empty">No active jobs.</div>`;
    $("listClose").innerHTML = c.length ? c.map(closeCard).join("")
      : `<div class="empty">Nothing to close.</div>`;

    $("staleNote").textContent = state.staleCount
      ? state.staleCount + " older unresolved → Admin › Inbox Review" : "";

    state.order = [].concat(
      d.map((j) => ({ col: "dispatch", id: j.message_id })),
      p.map((e) => ({ col: "progress", id: e.id })),
      c.map((e) => ({ col: "close", id: e.id })));
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
        <div class="kv"><span class="k">Company</span><span class="v">${esc(j.source || "")}</span></div>
        <div class="kv"><span class="k">Service</span><span class="v">${esc(j.service || "not stated")}</span></div>
        <div class="kv"><span class="k">Area</span><span class="v">${esc([j.city, j.state].filter(Boolean).join(", ") || "not stated")}</span></div>
        <div class="kv"><span class="k">Age</span><span class="v">${age(j.age_minutes)}</span></div>
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
        <div class="detail-actions">
          <button class="btn primary" data-act="dispatch" data-id="${esc(j.message_id)}">Dispatch &amp; copy</button>
          ${proposed ? `<button class="btn" data-act="copy">Copy (C)</button>` : ""}
        </div>
      </div>`;
  }

  function renderProgressDetail(e) {
    const w = waitingLabel(e);
    const wf = e.workflow || {};
    return `
      <div class="sec">
        <div class="sec-h">Job</div>
        <div class="kv"><span class="k">Ticket</span><span class="v">${esc(e.ticket || "")}</span></div>
        <div class="kv"><span class="k">Company</span><span class="v">${esc(e.source || "")}</span></div>
        <div class="kv"><span class="k">Technician</span><span class="v">${esc(e.technician_name || "unassigned")}</span></div>
        <div class="kv"><span class="k">Area</span><span class="v">${esc(e.city || "")}</span></div>
        <div class="kv"><span class="k">State</span><span class="v">${esc(e.status || "")}</span></div>
        <div class="kv"><span class="k">Waiting</span><span class="v">${esc(elapsedSince(e.waiting_since) || "just now")}</span></div>
      </div>
      <div class="sec">
        <div class="sec-h">What we are waiting for</div>
        <div class="decision">
          <div class="d-detected">${esc(w.t)}</div>
          ${e.latest_technician_update ? `<div class="d-why">Last technician update: ${esc(e.latest_technician_update)}</div>` : ""}
          ${e.next_action ? `<div class="d-why">Next action: ${esc(e.next_action)}</div>` : ""}
          ${e.company_update_owed ? `<div class="d-why">A company update is owed</div>` : ""}
        </div>
      </div>
      ${wf.next_message ? `
      <div class="sec">
        <div class="sec-h">Proposed company update</div>
        <div class="msgbox" id="msgBox">${esc(wf.next_message)}</div>
        <div class="detail-actions"><button class="btn primary" data-act="copy">Copy update (C)</button></div>
      </div>` : ""}
      <div class="sec">
        <div class="sec-h">Open in full workspace</div>
        <div class="detail-actions">
          <button class="btn ghost" data-act="admin-job" data-id="${esc(e.id)}">Admin &rsaquo; job #${esc(e.id)}</button>
        </div>
      </div>`;
  }

  function renderCloseDetail(e) {
    const missing = e.missing_fields || [];
    return `
      <div class="sec">
        <div class="sec-h">Closing</div>
        <div class="kv"><span class="k">Ticket</span><span class="v">${esc(e.ticket || "")}</span></div>
        <div class="kv"><span class="k">Technician</span><span class="v">${esc(e.technician_name || "")}</span></div>
        <div class="kv"><span class="k">Amount</span><span class="v">${esc(e.closing_amount || "not stated")}</span></div>
        <div class="kv"><span class="k">Payment</span><span class="v">${esc(e.closing_payment_method || "not stated")}</span></div>
      </div>
      ${missing.length ? `<div class="sec"><div class="missing">
          <b>Missing: ${esc(missing.join(", "))}</b><br>
          These are never invented. Enter them in the full workspace only if you actually know them.
        </div></div>` : ""}
      <div class="sec">
        <div class="detail-actions">
          <button class="btn ghost" data-act="admin-job" data-id="${esc(e.id)}">Open in Admin to complete</button>
        </div>
      </div>`;
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
    } else {
      item = state.close.find((e) => String(e.id) === String(id));
      if (item) { html = renderCloseDetail(item); title = item.ticket || "#" + item.id; }
    }
    if (!item) return;
    state.detail = item;
    $("detailTitle").textContent = title;
    $("detailBody").innerHTML = html;
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
    // -> confirm. Nothing new, nothing automatic, nothing sent.
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

  function onClick(ev) {
    const actEl = ev.target.closest("[data-act]");
    if (actEl) {
      ev.stopPropagation();
      const act = actEl.getAttribute("data-act");
      if (act === "dispatch") return doDispatch(actEl.getAttribute("data-id"));
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
    load(); health();
    setInterval(load, POLL_MS);
    setInterval(health, 15000);
  }

  async function doLogin() {
    try {
      await Api.login($("loginPassword").value);
      showApp();
    } catch (e) { $("loginError").textContent = e.message; }
  }

  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKey);
  $("loginBtn").addEventListener("click", doLogin);
  $("loginPassword").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  $("detailClose").addEventListener("click", closeDetail);
  $("adminBtn").addEventListener("click", () => { window.location.href = "admin.html"; });
  $("searchInput").addEventListener("input", onSearch);
  document.querySelectorAll("[data-jump]").forEach((b) => b.addEventListener("click", () => {
    const map = { dispatch: "colDispatch", progress: "colProgress", close: "colClose" };
    const el = $(map[b.getAttribute("data-jump")]);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }));

  Api.checkSession().then(showApp).catch(showLogin);
})();
