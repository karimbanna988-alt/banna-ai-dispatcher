/* LUXOR Operator UI V2 -- connected build. Every job/technician value
   shown here comes from a real GET to operator_api.py, which itself
   only reads through dispatcher.storage/luxor_desk_read_model. Every
   write action calls one operator_api.py POST route, which calls
   exactly one existing DispatcherService method -- this file never
   writes to a database, never talks to WhatsApp, never simulates a
   click or keypress in another application. "Open WhatsApp" only ever
   navigates this browser tab/window -- an ordinary link, not
   automation. */

const state = {
  screen: "jobs",
  jobsSelected: null,
  activeSelected: null,
  kRevealed: {},          // message_id -> bool, local-only "Copy K" reveal
  activePreview: {},      // job_id -> last commercial preview/save result (client-side draft)
  activeRawText: {},      // job_id -> last pasted text, preserved verbatim across re-renders
};

let newJobs = [];
let activeJobs = [];
let technicians = [];

function esc(s) { return (s ?? "").toString().replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function isMobile() { return window.innerWidth < 900; }

function sourceChip(source) {
  const cls = source === "AGN" ? "chip-source-agn" : source === "Lucky" ? "chip-source-lucky" : "chip-source-mainleads";
  return `<span class="chip ${cls}">${esc(source)}</span>`;
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text || ""); }
  catch (e) { /* clipboard permission denied -- the visible msg-block is the fallback */ }
}

/* "Open WhatsApp" -- an ordinary best-effort app launch, not automation.
   Tries the WhatsApp Desktop URI scheme first; if the tab is still
   visible/focused shortly after (a strong signal no external app
   intercepted the navigation), falls back to WhatsApp Web. Never
   inspects any WhatsApp window, never reads a session, never claims
   the correct group was opened -- the destination is only ever shown
   to the human as text, right before this call. */
function openWhatsApp() {
  let handedOff = false;
  const markHandedOff = () => { handedOff = true; };
  document.addEventListener("visibilitychange", markHandedOff, { once: true });
  window.addEventListener("blur", markHandedOff, { once: true });
  window.location.href = "whatsapp://";
  setTimeout(() => {
    document.removeEventListener("visibilitychange", markHandedOff);
    window.removeEventListener("blur", markHandedOff);
    if (!handedOff && !document.hidden) {
      window.open("https://web.whatsapp.com/", "_blank", "noopener");
    }
  }, 900);
}

/* ============================================================ NAVIGATION */
function setScreen(name) {
  state.screen = name;
  document.querySelectorAll(".screen").forEach(el => el.classList.toggle("active", el.id === "screen-" + name));
  document.querySelectorAll(".desktop-sidebar .nav-item, .mobile-bottom-nav .nav-item").forEach(el => {
    el.classList.toggle("active", el.dataset.screen === name);
  });
  closeSheet();
  refreshAll();
}
document.querySelectorAll(".desktop-sidebar .nav-item, .mobile-bottom-nav .nav-item").forEach(el => {
  el.addEventListener("click", () => setScreen(el.dataset.screen));
});
document.getElementById("refreshBtn").addEventListener("click", () => refreshAll());

/* ============================================================ SHEET/MODAL */
function openSheet(html) {
  document.getElementById("bottomSheetContent").innerHTML = html;
  document.getElementById("bottomSheet").style.display = "block";
  document.getElementById("sheetBackdrop").classList.add("open");
}
function closeSheet() {
  document.getElementById("bottomSheet").style.display = "none";
  document.getElementById("sheetBackdrop").classList.remove("open");
}
document.getElementById("sheetBackdrop").addEventListener("click", closeSheet);

function showConfirmModal(html) {
  document.getElementById("confirmModal").innerHTML = html;
  document.getElementById("confirmModalBackdrop").classList.add("open");
}
function hideConfirmModal() {
  document.getElementById("confirmModalBackdrop").classList.remove("open");
}

/* ================================================================= JOBS */
async function refreshAll() {
  if (state.screen === "jobs") await loadJobs();
  else if (state.screen === "active") await loadActive();
  else if (state.screen === "technicians") await loadTechnicians();
}

async function loadJobs() {
  try {
    const data = await Api.getNewJobs();
    newJobs = data.entries;
  } catch (e) { newJobs = []; showTransientError(e.message); }
  renderJobsScreen();
}

function renderJobsScreen() {
  document.getElementById("jobsCount").textContent = `(${newJobs.length})`;
  const tbody = document.getElementById("jobsTableBody");
  tbody.innerHTML = newJobs.length ? newJobs.map(j => `
    <tr data-mid="${esc(j.message_id)}" class="${state.jobsSelected === j.message_id ? "selected" : ""}">
      <td>${j.age_minutes}m</td>
      <td><strong>${esc(j.ticket || "No ref")}</strong></td>
      <td>${sourceChip(j.source)}</td>
      <td>${esc(j.service || "Unclear")}</td>
      <td>${esc(j.city || "")}${j.state ? ", " + esc(j.state) : ""}</td>
      <td>${esc(j.technician_name || "Unassigned")}</td>
    </tr>`).join("") : `<tr><td colspan="6"><div class="empty-state">No new live jobs need attention.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-mid]").forEach(tr => tr.addEventListener("click", () => selectJob(tr.dataset.mid)));

  const mobileList = document.getElementById("jobsMobileList");
  mobileList.innerHTML = newJobs.length ? newJobs.map(j => `
    <div class="mobile-row ${j.dispatched ? "settled" : j.k_sent || j.copied ? "waiting" : "needs-action"}" data-mid="${esc(j.message_id)}">
      <div class="row-top">${sourceChip(j.source)} <strong>${esc(j.ticket || "No ref")}</strong> <span class="age">${j.age_minutes}m</span></div>
      <div class="row-bottom">${esc(j.service || "Unclear")} · ${esc(j.city || "")}${j.state ? ", " + esc(j.state) : ""}</div>
    </div>`).join("") : `<div class="empty-state">No new live jobs need attention.</div>`;
  mobileList.querySelectorAll(".mobile-row").forEach(row => row.addEventListener("click", () => selectJob(row.dataset.mid)));

  if (!newJobs.find(j => j.message_id === state.jobsSelected)) {
    state.jobsSelected = newJobs.length ? newJobs[0].message_id : null;
  }
  if (state.jobsSelected) renderJobDetail(state.jobsSelected);
  else { document.getElementById("jobsDetailPane").classList.remove("open"); document.getElementById("jobsDetailPane").innerHTML = ""; }
}

function selectJob(mid) {
  state.jobsSelected = mid;
  renderJobsScreen();
  if (isMobile()) {
    const j = newJobs.find(x => x.message_id === mid);
    if (j) { openSheet(jobDetailHtml(j)); wireJobDetailActions(j); }
  }
}

function jobDetailHtml(j) {
  let warn = "";
  if (!j.destination && !j.is_closings_stream) warn = `<div class="detail-warning">WhatsApp group not configured.</div>`;
  const techLine = j.eligible_count === 1
    ? `Technician: <strong>${esc(j.technician_name)}</strong>`
    : j.eligible_count > 1 ? `${j.eligible_count} technicians eligible` : "No technician configured";
  const destLine = j.destination ? `WhatsApp: <strong>${esc(j.destination)}</strong>` : `<span class="chip chip-warning">Not configured</span>`;

  let body = "";
  if (j.is_closings_stream) {
    body = `<div class="info-banner">Closings &amp; Replies -- use the Closings tool for this one (not connected in this build).</div>`;
  } else if (j.needs_review) {
    body = `<div class="review-banner">Please review before continuing -- ${esc(j.review_problems.join(", "))}.</div>` + secondaryActionsHtml(j);
  } else {
    body = `<div class="action-area" id="jobActionArea">${renderJobActions(j)}</div>` + secondaryActionsHtml(j);
  }

  return `
    <div class="detail-header">${sourceChip(j.source)}<span class="detail-ticket">${esc(j.ticket || "No ref")}</span><span class="detail-age">${j.age_minutes}m</span></div>
    <div class="detail-meta">${esc(j.service || "Unclear")} · ${esc(j.city || "")}${j.state ? ", " + esc(j.state) : ""}</div>
    <div class="detail-meta">${techLine} · ${destLine}</div>
    ${warn}
    ${body}
    <div class="detail-section">
      <details class="disclosure"><summary>Job details</summary>
        <div class="detail-meta">Customer: ${esc(j.customer || "—")}</div>
        <div class="detail-meta">Phone: ${esc(j.phone || "—")}</div>
        <div class="detail-meta">Address: ${esc(j.address || "—")}</div>
        ${j.vehicle ? `<div class="detail-meta">Vehicle: ${esc(j.vehicle)}</div>` : ""}
        ${j.notes ? `<div class="detail-meta">Notes: ${esc(j.notes)}</div>` : ""}
        <div class="subsection-label">Original message</div>
        <pre class="msg-block">${esc(j.original_message_masked)}</pre>
        <details class="disclosure"><summary>Preview technician message</summary>
          <pre class="msg-block">${esc(j.luxor_message)}</pre>
        </details>
      </details>
    </div>`;
}

function secondaryActionsHtml(j) {
  return `
    <div class="detail-section">
      <details class="disclosure"><summary>Pass or handle manually</summary>
        <div class="action-secondary-row">
          <button class="btn btn-secondary" data-jobaction="handle_manually" data-mid="${esc(j.message_id)}">Handle manually</button>
        </div>
        <div class="form-field">
          <label>Reason (required to pass)</label>
          <input type="text" id="passReason-${esc(j.message_id)}">
        </div>
        <button class="btn btn-secondary btn-block" data-jobaction="pass" data-mid="${esc(j.message_id)}">Pass / Cancel job</button>
      </details>
    </div>`;
}

function renderJobActions(j) {
  if (j.k_required && !j.k_sent) {
    if (state.kRevealed[j.message_id]) {
      return `<code class="msg-block">K</code>
        <button class="btn btn-primary btn-block" data-jobaction="acknowledge" data-mid="${esc(j.message_id)}">Mark K sent manually</button>`;
    }
    return `<button class="btn btn-primary btn-block" data-jobaction="reveal_k" data-mid="${esc(j.message_id)}">Copy K</button>`;
  }
  if (j.k_required) {
    return `<div class="checked-line">&#10003; K sent</div>` + dispatchAreaHtml(j);
  }
  return dispatchAreaHtml(j);
}

function dispatchAreaHtml(j) {
  if (j.dispatched) return `<div class="checked-line">&#10003; Dispatched -- moved to Active</div>`;
  if (!j.destination) {
    return `
      <div class="chip chip-warning">Group not configured</div>
      <button class="btn btn-secondary btn-block" data-jobaction="copy_only" data-mid="${esc(j.message_id)}">Copy message</button>
      <pre class="msg-block" style="margin-top:6px;">${esc(j.luxor_message)}</pre>`;
  }
  if (!j.copied) {
    return `<button class="btn btn-primary btn-block" data-jobaction="prepare" data-mid="${esc(j.message_id)}">Copy technician job</button>`;
  }
  return `
    <div class="detail-meta">Send to: <strong>${esc(j.destination)}</strong></div>
    <pre class="msg-block">${esc(j.luxor_message)}</pre>
    <button class="btn btn-primary btn-block" data-jobaction="open_whatsapp" data-mid="${esc(j.message_id)}">Open WhatsApp</button>
    <button class="btn btn-primary btn-block" style="margin-top:6px;" data-jobaction="confirm_prompt" data-mid="${esc(j.message_id)}">Confirm sent manually</button>`;
}

function wireJobDetailActions(j) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("jobsDetailPane");
  root.querySelectorAll("[data-jobaction]").forEach(el => {
    el.addEventListener("click", () => handleJobAction(el.dataset.jobaction, el.dataset.mid));
  });
}

async function handleJobAction(action, mid) {
  const j = newJobs.find(x => x.message_id === mid);
  if (!j) return;
  try {
    if (action === "reveal_k") {
      await copyText("K");
      state.kRevealed[mid] = true;
      refreshJobDetail(mid);
    } else if (action === "acknowledge") {
      await Api.acknowledge(mid);
      await loadJobs();
    } else if (action === "copy_only") {
      await copyText(j.luxor_message);
    } else if (action === "prepare") {
      await copyText(j.luxor_message);
      await Api.prepareDispatch(mid);
      await loadJobs();
    } else if (action === "open_whatsapp") {
      await copyText(j.luxor_message);
      openWhatsApp();
    } else if (action === "confirm_prompt") {
      showConfirmModal(confirmModalHtml(j));
    } else if (action === "handle_manually") {
      await Api.handleManually(mid);
      await loadJobs();
    } else if (action === "pass") {
      const input = document.getElementById(`passReason-${mid}`);
      const reason = input ? input.value.trim() : "";
      if (!reason) { showTransientError("A reason is required to pass a job."); return; }
      await Api.passJob(mid, reason);
      await loadJobs();
    }
  } catch (e) {
    showTransientError(e.message);
  }
}

function confirmModalHtml(j) {
  return `
    <h3>Did you send this message to <span class="confirm-group-name">${esc(j.destination)}</span>?</h3>
    <div class="detail-meta">${esc(j.source)} · ${esc(j.ticket || "No ref")}</div>
    <div class="confirm-actions">
      <button class="btn btn-primary btn-block" id="confirmYesBtn">Yes, sent manually</button>
      <button class="btn btn-secondary btn-block" id="confirmCopyAgainBtn">Copy again</button>
      <button class="btn btn-secondary btn-block" id="confirmNotYetBtn">Not yet</button>
    </div>`;
}

function wireConfirmModal(j) {
  const yes = document.getElementById("confirmYesBtn");
  const copyAgain = document.getElementById("confirmCopyAgainBtn");
  const notYet = document.getElementById("confirmNotYetBtn");
  if (yes) yes.addEventListener("click", async () => {
    try {
      await Api.confirmDispatch(j.message_id);
      hideConfirmModal();
      await loadJobs();
    } catch (e) { showTransientError(e.message); }
  });
  if (copyAgain) copyAgain.addEventListener("click", () => copyText(j.luxor_message));
  if (notYet) notYet.addEventListener("click", () => hideConfirmModal());
}

function refreshJobDetail(mid) {
  renderJobsScreen();
}

function renderJobDetail(mid) {
  const j = newJobs.find(x => x.message_id === mid);
  const pane = document.getElementById("jobsDetailPane");
  if (!j) { pane.classList.remove("open"); pane.innerHTML = ""; return; }
  pane.classList.add("open");
  pane.innerHTML = jobDetailHtml(j);
  wireJobDetailActions(j);
}

/* Re-wire the confirm modal every render pass since its buttons are
   re-created each time showConfirmModal() is called from a job action. */
const _origShowConfirmModal = showConfirmModal;
showConfirmModal = function (html) {
  _origShowConfirmModal(html);
  const mid = state.jobsSelected;
  const j = newJobs.find(x => x.message_id === mid);
  if (j) wireConfirmModal(j);
};

/* =============================================================== ACTIVE */
async function loadActive() {
  try {
    const data = await Api.getActive();
    activeJobs = data.entries;
  } catch (e) { activeJobs = []; showTransientError(e.message); }
  renderActiveScreen();
}

function renderActiveScreen() {
  document.getElementById("activeCount").textContent = `(${activeJobs.length})`;
  const tbody = document.getElementById("activeTableBody");
  tbody.innerHTML = activeJobs.length ? activeJobs.map(j => `
    <tr data-id="${j.id}" class="${state.activeSelected === j.id ? "selected" : ""}">
      <td><strong>${esc(j.ticket_number || "No ref")}</strong></td>
      <td>${esc(j.assigned_technician_name || "Unassigned")}</td>
      <td>${esc(j.status)}</td>
      <td>${j.latest_technician_update ? esc(j.latest_technician_update.update_type) : "—"}</td>
    </tr>`).join("") : `<tr><td colspan="4"><div class="empty-state">No active jobs.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-id]").forEach(tr => tr.addEventListener("click", () => selectActive(parseInt(tr.dataset.id, 10))));

  const mobileList = document.getElementById("activeMobileList");
  mobileList.innerHTML = activeJobs.length ? activeJobs.map(j => `
    <div class="mobile-row" data-id="${j.id}">
      <div class="row-top"><strong>${esc(j.ticket_number || "No ref")}</strong> <span class="age">${esc(j.status)}</span></div>
      <div class="row-bottom">${esc(j.assigned_technician_name || "Unassigned")}</div>
    </div>`).join("") : `<div class="empty-state">No active jobs.</div>`;
  mobileList.querySelectorAll(".mobile-row").forEach(row => row.addEventListener("click", () => selectActive(parseInt(row.dataset.id, 10))));

  if (!activeJobs.find(j => j.id === state.activeSelected)) state.activeSelected = activeJobs.length ? activeJobs[0].id : null;
  if (state.activeSelected) renderActiveDetail(state.activeSelected);
  else { document.getElementById("activeDetailPane").classList.remove("open"); document.getElementById("activeDetailPane").innerHTML = ""; }
}

function selectActive(id) {
  state.activeSelected = id;
  renderActiveScreen();
  if (isMobile()) {
    const j = activeJobs.find(x => x.id === id);
    if (j) { openSheet(activeDetailHtml(j)); wireActiveDetailActions(j); }
  }
}

const QUOTE_TYPES = ["FIXED", "STARTING", "RANGE", "ESTIMATE", "NO_AMOUNT", "UNKNOWN"];
const CUSTOMER_RESPONSES = ["ACCEPTED", "DECLINED", "WILL_CALL_BACK", "THINKING", "TOO_EXPENSIVE", "SHOPPING_AROUND", "REQUESTED_DISCOUNT", "NO_RESPONSE", "UNKNOWN"];

function activeDetailHtml(j) {
  const preview = state.activePreview[j.id];
  const rawText = state.activeRawText[j.id] || "";

  let interpretBlock = `
    <div class="form-field">
      <label>Paste technician's exact WhatsApp message</label>
      <textarea id="pasteText-${j.id}">${esc(rawText)}</textarea>
    </div>
    <button class="btn btn-primary btn-block" data-activeaction="interpret" data-id="${j.id}">Interpret</button>`;

  let editBlock = "";
  if (preview) {
    const reviewNeeded = preview.human_review_required || preview.quote_type === "UNKNOWN";
    editBlock = `
      ${reviewNeeded ? `<div class="review-banner">Please review before continuing.</div>` : ""}
      <div class="form-row">
        <div class="form-field">
          <label>Quote type</label>
          <select id="quoteType-${j.id}">${QUOTE_TYPES.map(t => `<option value="${t}" ${t === preview.quote_type ? "selected" : ""}>${t}</option>`).join("")}</select>
        </div>
        <div class="form-field">
          <label>Amount</label>
          <input type="text" id="quoteAmount-${j.id}" value="${esc(preview.quote_amount || "")}">
        </div>
      </div>
      <div class="form-field">
        <label>Customer response</label>
        <select id="customerResponse-${j.id}">${CUSTOMER_RESPONSES.map(t => `<option value="${t}" ${t === preview.customer_response ? "selected" : ""}>${t}</option>`).join("")}</select>
      </div>
      <button class="btn btn-primary btn-block" data-activeaction="save" data-id="${j.id}">Save verified update</button>`;
  }

  let replyBlock = "";
  if (preview && preview.preparedText) {
    replyBlock = `
      <div class="detail-section">
        <div class="subsection-label">Prepared company reply</div>
        <pre class="msg-block">${esc(preview.preparedText)}</pre>
        <button class="btn btn-secondary btn-block" data-activeaction="copy_reply" data-id="${j.id}">Copy response</button>
        <button class="btn btn-primary btn-block" style="margin-top:6px;" data-activeaction="confirm_reply" data-id="${j.id}">Confirm company response sent manually</button>
      </div>`;
  } else if (preview && preview.saved && !preview.preparedText) {
    replyBlock = `<div class="review-banner">No deterministic company reply available -- please review before continuing.</div>`;
  }

  const episode = j.shadow_episode;
  const shadowBlock = episode ? `
    <div class="shadow-panel">
      <div class="shadow-row"><span class="shadow-label">Shadow suggestion:</span> ${esc(episode.shadow_suggested_reply || "—")}</div>
      <div class="shadow-row"><span class="shadow-label">Dispatcher-confirmed reply:</span> ${esc(episode.dispatcher_actual_reply || "—")}</div>
      <div class="shadow-row"><span class="shadow-label">Sent:</span> ${episode.reply_sent_at ? `${esc(episode.reply_sent_at)} via ${esc(episode.reply_sent_channel)}` : "Not yet confirmed sent"}</div>
    </div>` : "";

  return `
    <div class="detail-header"><span class="detail-ticket">${esc(j.ticket_number || "No ref")}</span></div>
    <div class="detail-meta">${esc(j.assigned_technician_name || "Unassigned")} · Status: <strong>${esc(j.status)}</strong></div>
    <div class="detail-section">${interpretBlock}</div>
    <div class="detail-section" id="activeEditBlock-${j.id}">${editBlock}</div>
    <div id="activeReplyBlock-${j.id}">${replyBlock}</div>
    ${shadowBlock}`;
}

function wireActiveDetailActions(j) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("activeDetailPane");
  root.querySelectorAll("[data-activeaction]").forEach(el => {
    el.addEventListener("click", () => handleActiveAction(el.dataset.activeaction, parseInt(el.dataset.id, 10)));
  });
}

async function handleActiveAction(action, id) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("activeDetailPane");
  try {
    if (action === "interpret") {
      const text = document.getElementById(`pasteText-${id}`).value;
      if (!text.trim()) { showTransientError("Paste the technician's message first."); return; }
      state.activeRawText[id] = text;
      const suggestion = await Api.commercialPreview(id, text);
      state.activePreview[id] = suggestion;
      rerenderActiveDetail(id);
    } else if (action === "save") {
      const text = state.activeRawText[id] || "";
      const correctedFields = {
        quote_type: document.getElementById(`quoteType-${id}`).value,
        quote_amount: document.getElementById(`quoteAmount-${id}`).value || null,
        customer_response: document.getElementById(`customerResponse-${id}`).value,
      };
      const result = await Api.commercialSave(id, text, correctedFields);
      state.activePreview[id] = {
        ...state.activePreview[id], saved: true,
        preparedText: result.prepared_text, human_review_required: result.human_review_required,
      };
      rerenderActiveDetail(id);
    } else if (action === "copy_reply") {
      const preview = state.activePreview[id];
      if (preview && preview.preparedText) await copyText(preview.preparedText);
    } else if (action === "confirm_reply") {
      await Api.confirmReplySent(id, "whatsapp");
      await loadActive();
    }
  } catch (e) {
    showTransientError(e.message);
  }
}

function rerenderActiveDetail(id) {
  const j = activeJobs.find(x => x.id === id);
  if (!j) return;
  if (isMobile()) { openSheet(activeDetailHtml(j)); wireActiveDetailActions(j); }
  else renderActiveDetail(id);
}

function renderActiveDetail(id) {
  const j = activeJobs.find(x => x.id === id);
  const pane = document.getElementById("activeDetailPane");
  if (!j) { pane.classList.remove("open"); pane.innerHTML = ""; return; }
  pane.classList.add("open");
  pane.innerHTML = activeDetailHtml(j);
  wireActiveDetailActions(j);
}

/* =========================================================== TECHNICIANS */
async function loadTechnicians() {
  try {
    const data = await Api.getTechnicians();
    technicians = data.technicians;
  } catch (e) { technicians = []; showTransientError(e.message); }
  renderTechniciansScreen();
}

function renderTechniciansScreen() {
  document.getElementById("techCount").textContent = `(${technicians.length})`;
  const tbody = document.getElementById("techTableBody");
  tbody.innerHTML = technicians.map(t => `
    <tr>
      <td><strong>${esc(t.name)}</strong></td>
      <td>${(t.areas || []).map(esc).join(", ")}</td>
      <td>${t.active ? "Active" : "Inactive"}</td>
      <td>${esc(t.dispatch_group || "Not configured")} ${!t.dispatch_group ? '<span class="chip chip-warning">!</span>' : ""}</td>
      <td>${esc(t.closing_group || "Not configured")} ${!t.closing_group ? '<span class="chip chip-warning">!</span>' : ""}</td>
      <td><button class="btn btn-secondary" data-editid="${t.id}">Edit</button></td>
    </tr>
    <tr id="techEditRow-${t.id}" style="display:none;"><td colspan="6">${techEditFormHtml(t)}</td></tr>`).join("");

  tbody.querySelectorAll("[data-editid]").forEach(btn => {
    btn.addEventListener("click", () => {
      const row = document.getElementById(`techEditRow-${btn.dataset.editid}`);
      row.style.display = row.style.display === "none" ? "table-row" : "none";
    });
  });
  wireTechEditForms();

  const mobileList = document.getElementById("techMobileList");
  mobileList.innerHTML = technicians.map(t => `
    <div class="mobile-row">
      <div class="row-top"><strong>${esc(t.name)}</strong><span class="age">${t.active ? "Active" : "Inactive"}</span></div>
      <div class="row-bottom">${(t.areas || []).map(esc).join(", ")}</div>
      ${!t.configured ? `<div class="chip chip-warning" style="margin-top:4px;">WhatsApp group not configured.</div>` : ""}
      ${techEditFormHtml(t)}
    </div>`).join("");
  wireTechEditForms();
}

function techEditFormHtml(t) {
  return `
    <div class="tech-edit-row">
      <input type="text" id="editDispatch-${t.id}" placeholder="Dispatch group" value="${esc(t.dispatch_group || "")}">
      <input type="text" id="editClosing-${t.id}" placeholder="Closing group" value="${esc(t.closing_group || "")}">
      <button class="btn btn-primary" data-saveid="${t.id}">Save</button>
    </div>
    <div class="detail-meta" id="techMsg-${t.id}"></div>`;
}

function wireTechEditForms() {
  document.querySelectorAll("[data-saveid]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.saveid, 10);
      const dispatchGroup = document.getElementById(`editDispatch-${id}`).value.trim();
      const closingGroup = document.getElementById(`editClosing-${id}`).value.trim();
      const msgEl = document.getElementById(`techMsg-${id}`);
      try {
        const result = await Api.updateDestinations(id, dispatchGroup, closingGroup);
        if (msgEl) msgEl.textContent = result.user_message || "Saved.";
        await loadTechnicians();
      } catch (e) {
        if (msgEl) msgEl.textContent = e.message;
      }
    });
  });
}

/* =================================================================== ALL */
function showTransientError(message) {
  console.error(message);
  const existing = document.getElementById("transientError");
  if (existing) existing.remove();
  const el = document.createElement("div");
  el.id = "transientError";
  el.className = "detail-warning";
  el.style.position = "fixed";
  el.style.bottom = isMobile() ? "60px" : "10px";
  el.style.right = "10px";
  el.style.left = isMobile() ? "10px" : "auto";
  el.style.zIndex = "60";
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

window.addEventListener("resize", () => { if (!isMobile()) closeSheet(); });

setScreen("jobs");
