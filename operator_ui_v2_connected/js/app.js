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
  closingsSelected: null,
  historySelected: null,
  kRevealed: {},           // message_id -> bool, local-only "Copy K" reveal
  activePreview: {},       // job_id -> last commercial preview/save result (client-side draft)
  activeRawText: {},       // job_id -> last pasted text, preserved verbatim across re-renders
  closingDraft: {},        // job_id -> in-progress technician-closing form values
};

let newJobsLive = [];
let newJobsOlder = [];
let activeJobs = [];
let closingsEntries = [];
let historyEntries = [];
let technicians = [];

function esc(s) { return (s ?? "").toString().replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function notRecorded(v) { return (v === null || v === undefined || v === "") ? "Not recorded" : esc(v); }
function isMobile() { return window.innerWidth < 900; }

function sourceChip(source) {
  const cls = source === "AGN" ? "chip-source-agn" : source === "Lucky" ? "chip-source-lucky" : "chip-source-mainleads";
  return `<span class="chip ${cls}">${esc(source)}</span>`;
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text || ""); }
  catch (e) { /* clipboard permission denied -- the visible msg-block is the fallback */ }
}

/* "Open WhatsApp" -- an ordinary best-effort app launch, not automation. */
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

async function callOrHandleAuth(fn) {
  try {
    return await fn();
  } catch (e) {
    if (e.authRequired) { showLogin("Your session expired -- please log in again."); return null; }
    throw e;
  }
}

/* =================================================================== AUTH */
function showLogin(message) {
  clearAllLocalData();
  document.getElementById("appShell").style.display = "none";
  document.getElementById("loginScreen").style.display = "flex";
  const err = document.getElementById("loginError");
  if (message) { err.textContent = message; err.style.display = "block"; }
  else { err.style.display = "none"; }
  document.getElementById("loginPassword").value = "";
  document.getElementById("loginPassword").focus();
}

function showApp() {
  document.getElementById("loginScreen").style.display = "none";
  document.getElementById("appShell").style.display = "block";
  setScreen("jobs");
}

function clearAllLocalData() {
  /* No PII stays behind after logout / session expiry -- every
     in-memory array and rendered pane is wiped, not just hidden. */
  newJobsLive = []; newJobsOlder = []; activeJobs = []; closingsEntries = []; historyEntries = []; technicians = [];
  state.jobsSelected = null; state.activeSelected = null; state.closingsSelected = null; state.historySelected = null;
  state.kRevealed = {}; state.activePreview = {}; state.activeRawText = {}; state.closingDraft = {};
  ["jobsTableBody", "jobsMobileList", "olderJobsTableBody", "olderJobsMobileList", "jobsDetailPane",
   "activeTableBody", "activeMobileList", "activeDetailPane",
   "closingsTableBody", "closingsMobileList", "closingsDetailPane",
   "historyTableBody", "historyMobileList", "historyDetailPane",
   "reportTableBody", "reportMobileList", "techTableBody", "techMobileList"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
}

async function attemptLogin() {
  const password = document.getElementById("loginPassword").value;
  if (!password) { showLogin("Enter the password."); return; }
  try {
    await Api.login(password);
    showApp();
  } catch (e) {
    showLogin(e.message || "Login failed.");
  }
}
document.getElementById("loginSubmitBtn").addEventListener("click", attemptLogin);
document.getElementById("loginPassword").addEventListener("keydown", (e) => { if (e.key === "Enter") attemptLogin(); });

async function attemptLogout() {
  try { await Api.logout(); } catch (e) { /* already expired/logged out -- fine, still show login */ }
  showLogin();
}
document.getElementById("logoutBtn").addEventListener("click", attemptLogout);

(async function boot() {
  try {
    await Api.checkSession();
    // Session cookie is valid, but this fresh page load has no CSRF
    // token in memory yet (it is never persisted) -- re-establish one
    // by treating this as "needs login" only if a mutating action later
    // 401s; reads work immediately since GET routes need no CSRF token.
    showApp();
  } catch (e) {
    showLogin();
  }
})();

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
  else if (state.screen === "closings") await loadClosings();
  else if (state.screen === "history") await loadHistory();
  else if (state.screen === "reports") await loadReports();
  else if (state.screen === "technicians") await loadTechnicians();
}

async function loadJobs() {
  const data = await callOrHandleAuth(() => Api.getNewJobs());
  if (!data) return;
  newJobsLive = data.live;
  newJobsOlder = data.older;
  renderJobsScreen();
}

document.getElementById("syncBtn").addEventListener("click", async () => {
  const msgEl = document.getElementById("jobsSyncMsg");
  msgEl.style.display = "block";
  msgEl.textContent = "Syncing...";
  try {
    const result = await callOrHandleAuth(() => Api.sync());
    if (!result) return;
    msgEl.textContent = `Synced -- ${result.queued} new message(s).`;
    await loadJobs();
  } catch (e) {
    msgEl.textContent = e.message;
  }
});

function renderJobsScreen() {
  const allSelectable = newJobsLive.concat(newJobsOlder);
  document.getElementById("jobsCount").textContent = `(${allSelectable.length})`;

  const tbody = document.getElementById("jobsTableBody");
  const mobileList = document.getElementById("jobsMobileList");
  const emptyState = document.getElementById("jobsEmptyState");
  if (!newJobsLive.length) {
    tbody.innerHTML = "";
    mobileList.innerHTML = "";
    emptyState.style.display = "block";
  } else {
    emptyState.style.display = "none";
    tbody.innerHTML = newJobsLive.map(j => jobRowHtml(j, "jobsTableBody")).join("");
    mobileList.innerHTML = newJobsLive.map(j => jobMobileRowHtml(j)).join("");
  }
  wireJobRowClicks(tbody, mobileList);

  const olderDetails = document.getElementById("olderJobsDetails");
  if (newJobsOlder.length) {
    olderDetails.style.display = "block";
    document.getElementById("olderJobsSummary").textContent = `Older unresolved jobs (${newJobsOlder.length})`;
    const olderBody = document.getElementById("olderJobsTableBody");
    const olderMobile = document.getElementById("olderJobsMobileList");
    olderBody.innerHTML = newJobsOlder.map(j => jobRowHtml(j, "olderJobsTableBody")).join("");
    olderMobile.innerHTML = newJobsOlder.map(j => jobMobileRowHtml(j)).join("");
    wireJobRowClicks(olderBody, olderMobile);
  } else {
    olderDetails.style.display = "none";
  }

  // Never auto-select an older job while a live job exists.
  if (!allSelectable.find(j => j.message_id === state.jobsSelected)) {
    state.jobsSelected = newJobsLive.length ? newJobsLive[0].message_id : null;
  }
  if (state.jobsSelected) renderJobDetail(state.jobsSelected, allSelectable);
  else { document.getElementById("jobsDetailPane").classList.remove("open"); document.getElementById("jobsDetailPane").innerHTML = ""; }
}

function jobRowHtml(j, bodyId) {
  return `
    <tr data-mid="${esc(j.message_id)}" data-body="${bodyId}" class="${state.jobsSelected === j.message_id ? "selected" : ""}">
      <td>${j.age_minutes}m</td>
      <td><strong>${esc(j.ticket || "No ref")}</strong></td>
      <td>${sourceChip(j.source)}</td>
      <td>${esc(j.service || "Unclear")}</td>
      <td>${esc(j.city || "")}${j.state ? ", " + esc(j.state) : ""}</td>
      <td>${esc(j.technician_name || "Unassigned")}</td>
    </tr>`;
}

function jobMobileRowHtml(j) {
  return `
    <div class="mobile-row ${j.bucket === "needs_action" ? "needs-action" : "waiting"}" data-mid="${esc(j.message_id)}">
      <div class="row-top">${sourceChip(j.source)} <strong>${esc(j.ticket || "No ref")}</strong> <span class="age">${j.age_minutes}m</span></div>
      <div class="row-bottom">${esc(j.service || "Unclear")} · ${esc(j.city || "")}${j.state ? ", " + esc(j.state) : ""}</div>
    </div>`;
}

function wireJobRowClicks(tbody, mobileList) {
  tbody.querySelectorAll("tr[data-mid]").forEach(tr => tr.addEventListener("click", () => selectJob(tr.dataset.mid)));
  mobileList.querySelectorAll(".mobile-row").forEach(row => row.addEventListener("click", () => selectJob(row.dataset.mid)));
}

function selectJob(mid) {
  state.jobsSelected = mid;
  renderJobsScreen();
  if (isMobile()) {
    const j = newJobsLive.concat(newJobsOlder).find(x => x.message_id === mid);
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
    body = `<div class="info-banner">Closings &amp; Replies -- use the Closings screen for this one.</div>`;
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
  const j = newJobsLive.concat(newJobsOlder).find(x => x.message_id === mid);
  if (!j) return;
  try {
    if (action === "reveal_k") {
      await copyText("K");
      state.kRevealed[mid] = true;
      renderJobsScreen();
    } else if (action === "acknowledge") {
      const r = await callOrHandleAuth(() => Api.acknowledge(mid)); if (!r) return;
      await loadJobs();
    } else if (action === "copy_only") {
      await copyText(j.luxor_message);
    } else if (action === "prepare") {
      await copyText(j.luxor_message);
      const r = await callOrHandleAuth(() => Api.prepareDispatch(mid)); if (!r) return;
      await loadJobs();
    } else if (action === "open_whatsapp") {
      await copyText(j.luxor_message);
      openWhatsApp();
    } else if (action === "confirm_prompt") {
      showConfirmModal(confirmModalHtml(j));
    } else if (action === "handle_manually") {
      const r = await callOrHandleAuth(() => Api.handleManually(mid)); if (!r) return;
      await loadJobs();
    } else if (action === "pass") {
      const input = document.getElementById(`passReason-${mid}`);
      const reason = input ? input.value.trim() : "";
      if (!reason) { showTransientError("A reason is required to pass a job."); return; }
      const r = await callOrHandleAuth(() => Api.passJob(mid, reason)); if (!r) return;
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
      const r = await callOrHandleAuth(() => Api.confirmDispatch(j.message_id)); if (!r) return;
      hideConfirmModal();
      await loadJobs();
    } catch (e) { showTransientError(e.message); }
  });
  if (copyAgain) copyAgain.addEventListener("click", () => copyText(j.luxor_message));
  if (notYet) notYet.addEventListener("click", () => hideConfirmModal());
}

const _origShowConfirmModal = showConfirmModal;
showConfirmModal = function (html) {
  _origShowConfirmModal(html);
  const mid = state.jobsSelected;
  const j = newJobsLive.concat(newJobsOlder).find(x => x.message_id === mid);
  if (j) wireConfirmModal(j);
};

function renderJobDetail(mid, allSelectable) {
  const j = allSelectable.find(x => x.message_id === mid);
  const pane = document.getElementById("jobsDetailPane");
  if (!j) { pane.classList.remove("open"); pane.innerHTML = ""; return; }
  pane.classList.add("open");
  pane.innerHTML = jobDetailHtml(j);
  wireJobDetailActions(j);
}

/* =============================================================== ACTIVE */
async function loadActive() {
  const data = await callOrHandleAuth(() => Api.getActive());
  if (!data) return;
  activeJobs = data.entries;
  renderActiveScreen();
}

function renderActiveScreen() {
  document.getElementById("activeCount").textContent = `(${activeJobs.length})`;
  const tbody = document.getElementById("activeTableBody");
  tbody.innerHTML = activeJobs.length ? activeJobs.map(j => `
    <tr data-id="${j.id}" class="${state.activeSelected === j.id ? "selected" : ""}">
      <td><strong>${esc(j.ticket || "No ref")}</strong></td>
      <td>${sourceChip(j.source)}</td>
      <td>${esc(j.technician_name || "Unassigned")}</td>
      <td>${esc(j.city || "")}</td>
      <td>${esc(j.waiting_since ? j.waiting_since.slice(0, 16).replace("T", " ") : "—")}</td>
      <td>${esc(j.next_action)}</td>
    </tr>`).join("") : `<tr><td colspan="6"><div class="empty-state">No active jobs.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-id]").forEach(tr => tr.addEventListener("click", () => selectActive(parseInt(tr.dataset.id, 10))));

  const mobileList = document.getElementById("activeMobileList");
  mobileList.innerHTML = activeJobs.length ? activeJobs.map(j => `
    <div class="mobile-row ${j.bucket === "needs_action" ? "needs-action" : "waiting"}" data-id="${j.id}">
      <div class="row-top">${sourceChip(j.source)} <strong>${esc(j.ticket || "No ref")}</strong></div>
      <div class="row-bottom">${esc(j.technician_name || "Unassigned")} · ${esc(j.next_action)}</div>
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
    <div class="detail-header">${sourceChip(j.source)}<span class="detail-ticket">${esc(j.ticket || "No ref")}</span></div>
    <div class="detail-meta">${esc(j.technician_name || "Unassigned")} · ${esc(j.city || "")} · Status: <strong>${esc(j.status)}</strong></div>
    <div class="detail-meta">Next: <strong>${esc(j.next_action)}</strong></div>
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
  try {
    if (action === "interpret") {
      const text = document.getElementById(`pasteText-${id}`).value;
      if (!text.trim()) { showTransientError("Paste the technician's message first."); return; }
      state.activeRawText[id] = text;
      const suggestion = await callOrHandleAuth(() => Api.commercialPreview(id, text)); if (!suggestion) return;
      state.activePreview[id] = suggestion;
      rerenderActiveDetail(id);
    } else if (action === "save") {
      const text = state.activeRawText[id] || "";
      const correctedFields = {
        quote_type: document.getElementById(`quoteType-${id}`).value,
        quote_amount: document.getElementById(`quoteAmount-${id}`).value || null,
        customer_response: document.getElementById(`customerResponse-${id}`).value,
      };
      const result = await callOrHandleAuth(() => Api.commercialSave(id, text, correctedFields)); if (!result) return;
      state.activePreview[id] = {
        ...state.activePreview[id], saved: true,
        preparedText: result.prepared_text, human_review_required: result.human_review_required,
      };
      rerenderActiveDetail(id);
    } else if (action === "copy_reply") {
      const preview = state.activePreview[id];
      if (preview && preview.preparedText) await copyText(preview.preparedText);
    } else if (action === "confirm_reply") {
      const r = await callOrHandleAuth(() => Api.confirmReplySent(id, "whatsapp")); if (!r) return;
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

/* ============================================================= CLOSINGS */
async function loadClosings() {
  const data = await callOrHandleAuth(() => Api.getClosings());
  if (!data) return;
  closingsEntries = data.entries;
  renderClosingsScreen();
}

function closingStatusChip(c) {
  if (c.sent_confirmed) return `<span class="chip chip-settled">Sent -- ready to complete</span>`;
  if (!c.closing_recorded) return `<span class="chip chip-waiting">Waiting on technician</span>`;
  if (c.missing.length) return `<span class="chip chip-needs-action">Needs review</span>`;
  return `<span class="chip chip-needs-action">Ready to send</span>`;
}

function renderClosingsScreen() {
  document.getElementById("closingsCount").textContent = `(${closingsEntries.length})`;
  const tbody = document.getElementById("closingsTableBody");
  tbody.innerHTML = closingsEntries.length ? closingsEntries.map(c => `
    <tr data-id="${c.id}" class="${state.closingsSelected === c.id ? "selected" : ""}">
      <td><strong>${esc(c.ticket || "No ref")}</strong></td>
      <td>${sourceChip(c.source)}</td>
      <td>${esc(c.technician_name || "Unassigned")}</td>
      <td>${notRecorded(c.amount)}</td>
      <td>${notRecorded(c.payment_method)}</td>
      <td>${closingStatusChip(c)}</td>
    </tr>`).join("") : `<tr><td colspan="6"><div class="empty-state">No closings are waiting.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-id]").forEach(tr => tr.addEventListener("click", () => selectClosing(parseInt(tr.dataset.id, 10))));

  const mobileList = document.getElementById("closingsMobileList");
  mobileList.innerHTML = closingsEntries.length ? closingsEntries.map(c => `
    <div class="mobile-row" data-id="${c.id}">
      <div class="row-top">${sourceChip(c.source)} <strong>${esc(c.ticket || "No ref")}</strong></div>
      <div class="row-bottom">${esc(c.technician_name || "Unassigned")} · ${notRecorded(c.amount)}</div>
    </div>`).join("") : `<div class="empty-state">No closings are waiting.</div>`;
  mobileList.querySelectorAll(".mobile-row").forEach(row => row.addEventListener("click", () => selectClosing(parseInt(row.dataset.id, 10))));

  if (!closingsEntries.find(c => c.id === state.closingsSelected)) state.closingsSelected = closingsEntries.length ? closingsEntries[0].id : null;
  if (state.closingsSelected) renderClosingDetail(state.closingsSelected);
  else { document.getElementById("closingsDetailPane").classList.remove("open"); document.getElementById("closingsDetailPane").innerHTML = ""; }
}

function selectClosing(id) {
  state.closingsSelected = id;
  renderClosingsScreen();
  if (isMobile()) {
    const c = closingsEntries.find(x => x.id === id);
    if (c) { openSheet(closingDetailHtml(c)); wireClosingDetailActions(c); }
  }
}

function closingDetailHtml(c) {
  let step = "";
  if (!c.closing_recorded) {
    const draft = state.closingDraft[c.id] || {};
    step = `
      <div class="subsection-title">1. Review technician closing</div>
      <div class="detail-meta">No closing recorded yet for this job.</div>
      <div class="form-field"><label>Technician's actual closing (paste exactly)</label>
        <textarea id="closingRaw-${c.id}">${esc(draft.raw_closing || "")}</textarea></div>
      <div class="form-row">
        <div class="form-field"><label>2. Final amount (verbatim)</label><input type="text" id="closingAmount-${c.id}" value="${esc(draft.final_amount || "")}"></div>
        <div class="form-field"><label>3. Payment method</label><input type="text" id="closingPayment-${c.id}" value="${esc(draft.payment_method || "")}"></div>
      </div>
      <div class="form-field"><label>Parts amount (optional)</label><input type="text" id="closingParts-${c.id}" value="${esc(draft.parts || "")}"></div>
      <button class="btn btn-primary btn-block" data-closingaction="record" data-id="${c.id}">Save closing details</button>`;
  } else if (c.missing.length) {
    step = `<div class="review-banner">Please review this closing before sending. Missing: ${esc(c.missing.join(", "))}.</div>`;
  } else if (c.supported === false) {
    step = `<div class="review-banner">Please review this closing before sending.</div><div class="detail-meta">${esc(c.unsupported_reason)}</div>`;
  } else if (!c.sent_confirmed) {
    step = `
      <div class="subsection-title">4. Verify amount &amp; payment</div>
      <div class="checked-line">&#10003; Final amount: ${esc(c.amount)}</div>
      <div class="checked-line">&#10003; Payment method: ${esc(c.payment_method)}</div>
      <div class="subsection-title">5. Company closing (send manually in Quo -- this business never uses WhatsApp for company closings)</div>
      <pre class="msg-block">${esc(c.prepared_text)}</pre>
      <button class="btn btn-secondary btn-block" data-closingaction="prepare" data-id="${c.id}">Copy closing / mark prepared</button>
      <button class="btn btn-primary btn-block" style="margin-top:6px;" data-closingaction="confirm_sent" data-id="${c.id}">I sent this closing manually</button>`;
  } else {
    step = `
      <div class="checked-line">&#10003; Sent manually in Quo</div>
      <button class="btn btn-primary btn-block" data-closingaction="complete" data-id="${c.id}">Complete job</button>`;
  }

  return `
    <div class="detail-header">${sourceChip(c.source)}<span class="detail-ticket">${esc(c.ticket || "No ref")}</span></div>
    <div class="detail-meta">${esc(c.area)} · ${esc(c.description)}</div>
    <div class="detail-meta">Technician: <strong>${esc(c.technician_name || "Unassigned")}</strong></div>
    <div class="detail-section">${step}</div>`;
}

function wireClosingDetailActions(c) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("closingsDetailPane");
  root.querySelectorAll("[data-closingaction]").forEach(el => {
    el.addEventListener("click", () => handleClosingAction(el.dataset.closingaction, parseInt(el.dataset.id, 10)));
  });
}

async function handleClosingAction(action, id) {
  try {
    if (action === "record") {
      const fields = {
        raw_closing: document.getElementById(`closingRaw-${id}`).value,
        final_amount: document.getElementById(`closingAmount-${id}`).value || null,
        payment_method: document.getElementById(`closingPayment-${id}`).value || null,
        parts: document.getElementById(`closingParts-${id}`).value || null,
      };
      if (!fields.raw_closing.trim()) { showTransientError("Paste the technician's actual closing first."); return; }
      const r = await callOrHandleAuth(() => Api.recordClosing(id, fields)); if (!r) return;
      delete state.closingDraft[id];
      await loadClosings();
    } else if (action === "prepare") {
      const c = closingsEntries.find(x => x.id === id);
      if (c && c.prepared_text) await copyText(c.prepared_text);
      const r = await callOrHandleAuth(() => Api.prepareClosing(id)); if (!r) return;
      await loadClosings();
    } else if (action === "confirm_sent") {
      const r = await callOrHandleAuth(() => Api.confirmClosingSent(id)); if (!r) return;
      await loadClosings();
    } else if (action === "complete") {
      const r = await callOrHandleAuth(() => Api.completeClosing(id)); if (!r) return;
      await loadClosings();
    }
  } catch (e) {
    showTransientError(e.message);
  }
}

function renderClosingDetail(id) {
  const c = closingsEntries.find(x => x.id === id);
  const pane = document.getElementById("closingsDetailPane");
  if (!c) { pane.classList.remove("open"); pane.innerHTML = ""; return; }
  pane.classList.add("open");
  pane.innerHTML = closingDetailHtml(c);
  wireClosingDetailActions(c);
}

/* =============================================================== HISTORY */
let historyFilterOptions = { sources: [], technicians: [], statuses: [] };

async function loadHistory() {
  const filters = {
    search: document.getElementById("historySearch").value,
    source: document.getElementById("historySourceFilter").value,
    technician: document.getElementById("historyTechFilter").value,
    status: document.getElementById("historyStatusFilter").value,
    date_from: document.getElementById("historyDateFrom").value,
    date_to: document.getElementById("historyDateTo").value,
  };
  const data = await callOrHandleAuth(() => Api.getHistory(filters));
  if (!data) return;
  historyEntries = data.entries;
  historyFilterOptions = data.filter_options;
  populateHistoryFilterOptions();
  renderHistoryScreen(data.total);
}
document.getElementById("historyFilterBtn").addEventListener("click", () => loadHistory());

function populateHistoryFilterOptions() {
  const fill = (id, values) => {
    const select = document.getElementById(id);
    const current = select.value;
    select.innerHTML = `<option value="All">All</option>` + values.map(v => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(v)}</option>`).join("");
  };
  fill("historySourceFilter", historyFilterOptions.sources);
  fill("historyTechFilter", historyFilterOptions.technicians);
  fill("historyStatusFilter", historyFilterOptions.statuses);
}

function renderHistoryScreen(total) {
  document.getElementById("historyCount").textContent = `(${historyEntries.length} of ${total})`;
  const tbody = document.getElementById("historyTableBody");
  tbody.innerHTML = historyEntries.length ? historyEntries.map(h => `
    <tr data-id="${h.id}" class="${state.historySelected === h.id ? "selected" : ""}">
      <td><strong>${esc(h.ticket || "No ref")}</strong></td>
      <td>${sourceChip(h.source)}</td>
      <td>${esc(h.technician_name || "Unassigned")}</td>
      <td>${esc(h.status)}</td>
      <td>${esc(h.completed_at ? h.completed_at.slice(0, 10) : "—")}</td>
    </tr>`).join("") : `<tr><td colspan="5"><div class="empty-state">No jobs match these filters.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-id]").forEach(tr => tr.addEventListener("click", () => selectHistory(parseInt(tr.dataset.id, 10))));

  const mobileList = document.getElementById("historyMobileList");
  mobileList.innerHTML = historyEntries.length ? historyEntries.map(h => `
    <div class="mobile-row" data-id="${h.id}">
      <div class="row-top">${sourceChip(h.source)} <strong>${esc(h.ticket || "No ref")}</strong></div>
      <div class="row-bottom">${esc(h.technician_name || "Unassigned")} · ${esc(h.status)}</div>
    </div>`).join("") : `<div class="empty-state">No jobs match these filters.</div>`;
  mobileList.querySelectorAll(".mobile-row").forEach(row => row.addEventListener("click", () => selectHistory(parseInt(row.dataset.id, 10))));

  if (state.historySelected && historyEntries.find(h => h.id === state.historySelected)) renderHistoryDetail(state.historySelected);
  else { document.getElementById("historyDetailPane").classList.remove("open"); document.getElementById("historyDetailPane").innerHTML = ""; }
}

async function selectHistory(id) {
  state.historySelected = id;
  renderHistoryScreen(historyEntries.length);
  const detail = await callOrHandleAuth(() => Api.getHistoryDetail(id));
  if (!detail) return;
  const html = historyDetailHtml(detail);
  if (isMobile()) openSheet(html);
  else {
    const pane = document.getElementById("historyDetailPane");
    pane.classList.add("open");
    pane.innerHTML = html;
  }
}

function historyDetailHtml(d) {
  const timeline = d.timeline.map(e => `
    <div class="detail-meta"><strong>${esc(e.created_at ? e.created_at.slice(0, 16).replace("T", " ") : "")}</strong> — ${esc(e.label)}
      ${e.note ? `<div class="detail-meta">${esc(e.note)}</div>` : ""}
    </div>`).join("") || `<div class="detail-meta">No audit history recorded for this job.</div>`;
  const commercial = d.commercial_history ? `
    <div class="subsection-label">Commercial history</div>
    <div class="detail-meta">Suggestion: ${esc(d.commercial_history.shadow_suggested_reply || "—")}</div>
    <div class="detail-meta">Confirmed reply: ${esc(d.commercial_history.dispatcher_actual_reply || "—")}</div>` : "";
  return `
    <div class="detail-header">${sourceChip(d.job.source)}<span class="detail-ticket">${esc(d.job.ticket || "No ref")}</span></div>
    <div class="detail-meta">${esc(d.job.area)} · ${esc(d.job.description)}</div>
    <div class="detail-meta">Technician: <strong>${esc(d.job.technician_name || "Unassigned")}</strong> · Status: <strong>${esc(d.job.status)}</strong></div>
    <div class="detail-meta">Amount: <strong>${notRecorded(d.closing && d.closing.amount)}</strong> · ${notRecorded(d.closing && d.closing.payment_method)}</div>
    ${commercial}
    <div class="detail-section"><div class="subsection-label">Timeline</div>${timeline}</div>`;
}

function renderHistoryDetail(id) { /* handled inline by selectHistory's async fetch */ }

/* ============================================================== REPORTS */
async function loadReports() {
  const data = await callOrHandleAuth(() => Api.getReports());
  if (!data) return;
  renderReportsScreen(data.entries);
}

function renderReportsScreen(entries) {
  const tbody = document.getElementById("reportTableBody");
  tbody.innerHTML = entries.map(r => `
    <tr>
      <td><strong>${esc(r.job || "No ref")}</strong></td><td>${esc(r.company)}</td>
      <td>${esc(r.date ? r.date.slice(0, 10) : "Not recorded")}</td>
      <td>${esc(r.technician || "Unassigned")}</td><td>${esc(r.area || "Not recorded")}</td>
      <td>${notRecorded(r.quoted_amount)}</td><td>${notRecorded(r.final_amount)}</td><td>${notRecorded(r.payment_method)}</td>
    </tr>`).join("");

  const mobileList = document.getElementById("reportMobileList");
  mobileList.innerHTML = entries.map(r => `
    <div class="report-row-mobile">
      <div class="rrm-top"><span>${esc(r.job || "No ref")} · ${esc(r.company)}</span><span>${notRecorded(r.final_amount)}</span></div>
      <div class="rrm-bottom">${esc(r.date ? r.date.slice(0, 10) : "Not recorded")} · ${esc(r.technician || "Unassigned")} · ${esc(r.area || "Not recorded")}</div>
    </div>`).join("");
}

/* =========================================================== TECHNICIANS */
async function loadTechnicians() {
  const data = await callOrHandleAuth(() => Api.getTechnicians());
  if (!data) return;
  technicians = data.technicians;
  renderTechniciansScreen(data.missing_dispatch_group, data.missing_closing_group);
}

function renderTechniciansScreen(missingDispatch, missingClosing) {
  document.getElementById("techCount").textContent = `(${technicians.length})`;

  const banner = document.getElementById("techMissingBanner");
  const namesOnly = [...new Set([...(missingDispatch || []), ...(missingClosing || [])])];
  if (namesOnly.length) {
    banner.style.display = "block";
    banner.innerHTML = `Missing WhatsApp destination(s): <strong>${namesOnly.map(esc).join(", ")}</strong>`;
  } else {
    banner.style.display = "none";
  }

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
        const result = await callOrHandleAuth(() => Api.updateDestinations(id, dispatchGroup, closingGroup));
        if (!result) return;
        if (msgEl) msgEl.textContent = result.user_message || "Saved.";
        await loadTechnicians();
      } catch (e) {
        if (msgEl) msgEl.textContent = e.message;
      }
    });
  });
}

window.addEventListener("resize", () => { if (!isMobile()) closeSheet(); });
