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
  screen: "commandcenter",
  jobsSelected: null,
  activeSelected: null,
  closingsSelected: null,
  historySelected: null,
  kRevealed: {},           // message_id -> bool, local-only "Copy K" reveal
  activePreview: {},       // job_id -> last commercial preview/save result (client-side draft)
  activeRawText: {},       // job_id -> last pasted text, preserved verbatim across re-renders
  closingDraft: {},        // job_id -> in-progress technician-closing form values
  statusUpdateDraft: {},   // job_id -> in-progress technician-status-update form values
  statusUpdatePrepared: {}, // job_id -> {text} once a company reply has been prepared, not yet confirmed sent
  learningSelected: null,
  jobsShowAllLive: false,  // "Show N more" toggle for the live-jobs landing list
};

let newJobsLive = [];
let activeJobs = [];
let closingsEntries = [];
let historyEntries = [];
let technicians = [];
let learningEpisodes = [];

function esc(s) { return (s ?? "").toString().replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function notRecorded(v) { return (v === null || v === undefined || v === "") ? "Not recorded" : esc(v); }
function isMobile() { return window.innerWidth < 900; }

function sourceChip(source) {
  const cls = source === "AGN" ? "chip-source-agn" : source === "Lucky" ? "chip-source-lucky" : "chip-source-mainleads";
  return `<span class="chip ${cls}">${esc(source)}</span>`;
}

// Deterministic color + initials badge for a technician name -- same
// person always gets the same badge color across screens, no photo
// upload or backend field needed.
const TECH_AVATAR_COLORS = ["#2955d8", "#1f7d6f", "#b3541e", "#5b4fc4", "#1e6b3a", "#9a2f6b"];
function techAvatarHtml(name) {
  if (!name) return "";
  const initials = name.trim().split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase();
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  const color = TECH_AVATAR_COLORS[hash % TECH_AVATAR_COLORS.length];
  return `<span class="tech-avatar" style="background:${color}">${esc(initials)}</span>`;
}
function technicianWithAvatarHtml(name) {
  return name ? `${techAvatarHtml(name)}${esc(name)}` : "Unassigned";
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

function showTransientMessage(message, kind) {
  if (kind !== "info") console.error(message);
  const existing = document.getElementById("transientError");
  if (existing) existing.remove();
  const el = document.createElement("div");
  el.id = "transientError";
  el.className = kind === "info" ? "checked-line" : "detail-warning";
  el.style.position = "fixed";
  el.style.bottom = isMobile() ? "60px" : "10px";
  el.style.right = "10px";
  el.style.left = isMobile() ? "10px" : "auto";
  el.style.zIndex = "60";
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}
function showTransientError(message) { showTransientMessage(message, "error"); }

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

function showApp(demoMode) {
  document.getElementById("loginScreen").style.display = "none";
  document.getElementById("appShell").style.display = "block";
  document.getElementById("demoModeBanner").style.display = demoMode ? "block" : "none";
  document.getElementById("headerStatusText").textContent = demoMode
    ? "Demo -- synthetic data only, not connected to Quo or WhatsApp"
    : "Connected -- real data · manual WhatsApp bridge";
  document.getElementById("headerDateContext").textContent = new Date().toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  setScreen("commandcenter");
}

function clearAllLocalData() {
  /* No PII stays behind after logout / session expiry -- every
     in-memory array and rendered pane is wiped, not just hidden. */
  newJobsLive = []; newJobsTotalPending = 0; activeJobs = []; closingsEntries = []; historyEntries = []; technicians = []; learningEpisodes = []; whatsappPending = [];
  archiveState.page = 1; archiveState.search = ""; archiveState.unmatchedOnly = false; archiveState.total = 0;
  state.jobsSelected = null; state.activeSelected = null; state.closingsSelected = null; state.historySelected = null; state.learningSelected = null;
  state.kRevealed = {}; state.activePreview = {}; state.activeRawText = {}; state.closingDraft = {};
  state.statusUpdateDraft = {}; state.statusUpdatePrepared = {};
  state.jobsShowAllLive = false;
  ["jobsTableBody", "jobsMobileList", "jobsDetailPane",
   "archiveTableBody", "archiveMobileList",
   "activeTableBody", "activeMobileList", "activeDetailPane",
   "closingsTableBody", "closingsMobileList", "closingsDetailPane",
   "historyTableBody", "historyMobileList", "historyDetailPane",
   "reportTableBody", "reportMobileList", "techTableBody", "techMobileList",
   "learningTableBody", "learningMobileList", "learningDetailPane",
   "whatsappPendingList", "whatsappBridgeBanner",
   "ccKpiGrid", "ccClosedAmount", "ccAttentionList", "ccActiveTableBody", "ccActiveMobileList",
   "ccSourceMixChart", "ccPipelineChart", "ccTechActivityChart", "ccGeneratedAt"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
}

async function attemptLogin() {
  const password = document.getElementById("loginPassword").value;
  if (!password) { showLogin("Enter the password."); return; }
  const btn = document.getElementById("loginSubmitBtn");
  const label = document.getElementById("loginSubmitLabel");
  btn.disabled = true;
  label.textContent = "Logging in...";
  try {
    const result = await Api.login(password);
    showApp(result && result.demo_mode);
  } catch (e) {
    // Distinguish "the server said no" (a real, specific message like
    // "Invalid password.") from "we couldn't reach the server at all"
    // (a generic browser-level fetch failure) -- so a dispatcher isn't
    // told "invalid password" when the real problem is connectivity.
    const unreachable = e instanceof TypeError || /failed to fetch|networkerror|load failed/i.test(e.message || "");
    showLogin(unreachable ? "Can't reach the server. Check your connection and try again." : (e.message || "Login failed."));
  } finally {
    btn.disabled = false;
    label.textContent = "Log in";
  }
}
document.getElementById("loginSubmitBtn").addEventListener("click", attemptLogin);
document.getElementById("loginPassword").addEventListener("keydown", (e) => { if (e.key === "Enter") attemptLogin(); });
document.getElementById("loginPasswordToggle").addEventListener("click", () => {
  const input = document.getElementById("loginPassword");
  const btn = document.getElementById("loginPasswordToggle");
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  btn.textContent = showing ? "\u{1F441}" : "\u{1F576}";
  btn.setAttribute("aria-label", showing ? "Show password" : "Hide password");
  input.focus();
});

async function attemptLogout() {
  try { await Api.logout(); } catch (e) { /* already expired/logged out -- fine, still show login */ }
  showLogin();
}
document.getElementById("settingsLogoutItem").addEventListener("click", () => { closeSettingsMenu(); attemptLogout(); });

// Fire-and-forget: shows the DEMO MODE indicator on the login screen
// itself, before any credential is entered. Never blocks or gates
// login -- if this call fails for any reason, the banner just stays
// hidden and login proceeds normally.
Api.getDemoStatus().then(status => {
  document.getElementById("loginDemoModeBanner").style.display = (status && status.demo_mode) ? "block" : "none";
}).catch(() => {});

(async function boot() {
  try {
    // checkSession() also re-establishes the CSRF token for this fresh
    // page load (see Api.checkSession's comment) -- reads would have
    // worked either way since GET routes need no CSRF token, but writes
    // need this.
    const me = await Api.checkSession();
    showApp(me && me.demo_mode);
  } catch (e) {
    showLogin();
  }
})();

/* ============================================================ NAVIGATION */
function setScreen(name, opts) {
  state.screen = name;
  document.querySelectorAll(".screen").forEach(el => el.classList.toggle("active", el.id === "screen-" + name));
  document.querySelectorAll(".desktop-sidebar .nav-item, .mobile-bottom-nav .nav-item[data-screen]").forEach(el => {
    el.classList.toggle("active", el.dataset.screen === name);
  });
  closeSheet();
  closeSettingsMenu();
  // { skipRefresh: true } is for a caller about to do its own explicit,
  // awaited load-then-select right after this call -- letting this
  // function ALSO fire its own unawaited refreshAll() would start a
  // second, uncontrolled concurrent fetch of the same data. Both
  // resolve independently and can call the same screen's render
  // function in either order; if the fire-and-forget one lands last it
  // silently overwrites a just-made selection with its own
  // default-first-item fallback. This was a real, reproduced bug even
  // after switching the racing caller from setTimeout(0) to a plain
  // await -- awaiting the SECOND load never controlled the FIRST
  // (unawaited) one setScreen itself kicks off. Every normal nav click
  // omits opts and keeps refreshing exactly as before.
  if (!(opts && opts.skipRefresh)) refreshAll();
}
document.querySelectorAll(".desktop-sidebar .nav-item, .mobile-bottom-nav .nav-item[data-screen]").forEach(el => {
  el.addEventListener("click", () => setScreen(el.dataset.screen));
});
document.getElementById("refreshBtn").addEventListener("click", () => refreshAll());

/* ------------------------------------------------------- Settings menu -- */
function openSettingsMenu() {
  document.getElementById("settingsMenu").classList.add("open");
  document.getElementById("settingsMenuBackdrop").classList.add("open");
}
function closeSettingsMenu() {
  document.getElementById("settingsMenu").classList.remove("open");
  document.getElementById("settingsMenuBackdrop").classList.remove("open");
}
function toggleSettingsMenu() {
  const menu = document.getElementById("settingsMenu");
  if (menu.classList.contains("open")) closeSettingsMenu(); else openSettingsMenu();
}
document.getElementById("settingsBtn").addEventListener("click", toggleSettingsMenu);
document.getElementById("mobileMoreNavItem").addEventListener("click", toggleSettingsMenu);
document.getElementById("settingsMenuBackdrop").addEventListener("click", closeSettingsMenu);
document.querySelectorAll(".settings-menu-item[data-screen]").forEach(el => {
  el.addEventListener("click", () => setScreen(el.dataset.screen));
});

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
  if (state.screen === "commandcenter") await loadCommandCenter();
  else if (state.screen === "actioncenter") await loadActionCenter();
  else if (state.screen === "jobs") await loadJobs();
  else if (state.screen === "archive") await loadArchive();
  else if (state.screen === "active") await loadActive();
  else if (state.screen === "closings") await loadClosings();
  else if (state.screen === "history") await loadHistory();
  else if (state.screen === "reports") await loadReports();
  else if (state.screen === "technicians") await loadTechnicians();
  else if (state.screen === "learning") await loadLearningReview();
  else if (state.screen === "performance") await loadPerformance();
}

// The 10 workflow-stage sections are still the API/test source of truth
// (operator_api.py's _ACTION_CENTER_SECTIONS) -- this is only the label
// text for each stage chip shown on an item within a group.
const ACTION_CENTER_STAGE_LABELS = {
  new_jobs_to_review: "New job", company_k_owed: "Company K owed", assign_technician: "Assign technician",
  ready_to_dispatch: "Ready to dispatch", waiting_technician_k: "Waiting on technician K",
  technician_updates_to_review: "Technician update", company_updates_owed: "Company update owed",
  closings_to_review: "Closing to review", closings_ready_to_send: "Closing ready to send",
  human_review_required: "Human review",
};
// operator_api.py's _ACTION_CENTER_SECTIONS, needed here only to map an
// item's next_action code back to a stage label above.
const ACTION_CENTER_NEXT_ACTION_TO_SECTION = {
  REVIEW_NEW_JOB: "new_jobs_to_review", ACK_COMPANY_K: "company_k_owed", ASSIGN_TECHNICIAN: "assign_technician",
  SEND_JOB_TO_TECHNICIAN: "ready_to_dispatch", WAIT_TECHNICIAN_K: "waiting_technician_k",
  REVIEW_TECHNICIAN_UPDATE: "technician_updates_to_review", UPDATE_COMPANY: "company_updates_owed",
  REVIEW_CLOSING: "closings_to_review", SEND_CLOSING_TO_COMPANY: "closings_ready_to_send",
  HUMAN_REVIEW_NEEDED: "human_review_required",
};
const ACTION_CENTER_GROUP_META = {
  action_now: { empty: "Nothing needs action right now." },
  waiting: { empty: "Not waiting on anyone." },
  needs_review: { empty: "Nothing needs manual review." },
};
const ACTION_CENTER_PRIORITY_LABELS = { URGENT: "Urgent", DUE: "Due", NORMAL: "Normal", WAITING: "Waiting" };

async function loadActionCenter() {
  const data = await callOrHandleAuth(() => Api.getActionCenter());
  if (!data) return;
  renderActionCenterScreen(data.groups || []);
}

function actionCenterItemHtml(item) {
  const label = `#${esc(item.ticket || "No ref")}`;
  const age = item.waiting_since ? ageText(minutesSince(item.waiting_since)) : (item.age_minutes != null ? ageText(item.age_minutes) : "");
  const stageKey = ACTION_CENTER_NEXT_ACTION_TO_SECTION[item.next_action];
  const stageLabel = stageKey ? ACTION_CENTER_STAGE_LABELS[stageKey] : "";
  const priorityCls = `chip-priority-${(item.priority || "normal").toLowerCase()}`;
  const priorityLabel = ACTION_CENTER_PRIORITY_LABELS[item.priority] || item.priority;
  return `
    <div class="cc-attention-row">
      <div class="cc-attention-marker" aria-hidden="true">&#33;</div>
      <div class="cc-attention-main">
        <span class="chip ${priorityCls}">${esc(priorityLabel)}</span>
        <span class="cc-attention-ticket">${label}</span>
        <span class="cc-attention-source">${esc(item.source || "")}${item.technician_name ? " &middot; " + esc(item.technician_name) : ""}</span>
        ${stageLabel ? `<span class="cc-attention-source">${esc(stageLabel)}</span>` : ""}
        ${age ? `<span class="cc-attention-age">${esc(age)}</span>` : ""}
      </div>
      <div class="cc-attention-reason">${esc(item.blocking_reason || "")}</div>
      <button type="button" class="btn btn-secondary cc-small-btn" data-acscreen="${esc(item.screen)}" data-ackind="${esc(item.kind)}" data-acid="${esc(String(item.id))}">Open</button>
    </div>`;
}

function renderActionCenterScreen(groups) {
  const root = document.getElementById("actionCenterSections");
  const groupTotal = groups.reduce((sum, g) => sum + g.total, 0);
  if (!groups.length || !groupTotal) {
    root.innerHTML = `<div class="empty-state">Nothing needs your attention right now.</div>`;
    return;
  }
  root.innerHTML = groups.map(g => {
    const meta = ACTION_CENTER_GROUP_META[g.key] || { empty: "Nothing here." };
    const rows = g.items.length
      ? g.items.map(actionCenterItemHtml).join("")
      : `<div class="empty-state">${esc(meta.empty)}</div>`;
    const overflow = g.total > g.items.length
      ? `<div class="detail-meta">Showing ${g.items.length} of ${g.total} -- open a specific stage to see the rest.</div>` : "";
    return `
      <div class="cc-attention-section-wrap" style="margin-bottom:18px;">
        <div class="subsection-label">${esc(g.title)} <span class="count">(${g.total})</span></div>
        ${rows}
        ${overflow}
      </div>`;
  }).join("");
  root.querySelectorAll("[data-acscreen]").forEach(btn => {
    btn.addEventListener("click", () => {
      // navigateToItem (defined in the SEARCH section below -- function
      // declarations are hoisted, so this forward reference is safe)
      // is the one shared "go to this item's Fast Path context"
      // implementation, also used by search results and Next Actionable
      // Job, so all three agree on where a job actually lives.
      navigateToItem({
        entity_type: btn.dataset.ackind === "job" ? "JOB" : "INCOMING_MESSAGE",
        entity_id: btn.dataset.ackind === "job" ? parseInt(btn.dataset.acid, 10) : btn.dataset.acid,
        screen: btn.dataset.acscreen,
      });
    });
  });
}

let newJobsTotalPending = 0;

async function loadJobs() {
  const data = await callOrHandleAuth(() => Api.getNewJobs());
  if (!data) return;
  // Jobs screen shows ONLY the last-60-minutes actionable window -- no
  // automatic fallback to older messages. Archive / Inbox Review (a
  // separate, real, paginated screen -- see loadArchive()) is where
  // historical messages live now.
  newJobsLive = data.live;
  newJobsTotalPending = data.total_pending_messages || 0;
  renderJobsScreen();
}

function nowTimeLabel() {
  return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

document.getElementById("syncBtn").addEventListener("click", async () => {
  const msgEl = document.getElementById("jobsSyncMsg");
  msgEl.style.display = "block";
  msgEl.textContent = "Syncing...";
  try {
    const result = await callOrHandleAuth(() => Api.sync());
    if (!result) return;
    // Deliberately "inbound messages", never "jobs" -- a synced message
    // has not been reviewed or turned into a job yet.
    const label = result.queued === 1 ? "1 new inbound message" : `${result.queued} new inbound messages`;
    msgEl.textContent = `Last Quo sync: ${nowTimeLabel()} · ${label}`;
    await loadJobs();
  } catch (e) {
    msgEl.textContent = e.message;
  }
});

const LIVE_DISPLAY_CAP = 5;

function ageText(minutes) {
  if (minutes === null || minutes === undefined) return "";
  if (minutes < 1) return "just now";
  if (minutes === 1) return "1 min ago";
  if (minutes < 60) return `${minutes} min ago`;
  const hrs = Math.floor(minutes / 60);
  return hrs === 1 ? "1 hr ago" : `${hrs} hr ago`;
}

function jobStatusText(j) {
  if (j.is_closings_stream) return "Closings & Replies";
  if (j.needs_review) return "Needs review";
  if (j.k_required && !j.k_sent) return "Needs K sent";
  if (!j.destination) return "Group not configured";
  if (!j.copied) return "Ready to dispatch";
  if (!j.dispatched) return "Sent -- confirm";
  return "Dispatched";
}

function jobStatusChipClass(j) {
  if (j.needs_review || (!j.destination && !j.is_closings_stream)) return "chip-warning";
  if (j.dispatched) return "chip-settled";
  return j.bucket === "needs_action" ? "chip-needs-action" : "chip-waiting";
}

function jobPrimaryLabel(j) {
  return j.dispatched ? "View job" : "Review job";
}

function renderJobsScreen() {
  // Jobs screen shows only the last-60-minutes actionable window --
  // newJobsLive is the entire selectable population here, always. No
  // fallback ever inserts an older message into this screen.
  document.getElementById("jobsCount").textContent = `(${newJobsLive.length})`;

  const archiveLinkCount = document.getElementById("jobsArchiveLinkCount");
  if (archiveLinkCount) {
    archiveLinkCount.textContent = newJobsTotalPending ? ` (${newJobsTotalPending})` : "";
  }

  const tbody = document.getElementById("jobsTableBody");
  const mobileList = document.getElementById("jobsMobileList");
  const emptyState = document.getElementById("jobsEmptyState");
  const showMoreWrap = document.getElementById("jobsShowMoreWrap");
  const showMoreBtn = document.getElementById("jobsShowMoreBtn");
  if (!newJobsLive.length) {
    tbody.innerHTML = "";
    mobileList.innerHTML = "";
    emptyState.style.display = "block";
    showMoreWrap.style.display = "none";
  } else {
    emptyState.style.display = "none";
    const liveToShow = state.jobsShowAllLive ? newJobsLive : newJobsLive.slice(0, LIVE_DISPLAY_CAP);
    tbody.innerHTML = liveToShow.map(j => jobRowHtml(j, "jobsTableBody")).join("");
    mobileList.innerHTML = liveToShow.map(j => jobMobileRowHtml(j)).join("");
    wireJobRowClicks(tbody, mobileList);
    const hiddenCount = newJobsLive.length - liveToShow.length;
    if (hiddenCount > 0) {
      showMoreWrap.style.display = "block";
      showMoreBtn.textContent = `Show ${hiddenCount} more live job${hiddenCount === 1 ? "" : "s"}`;
    } else if (state.jobsShowAllLive && newJobsLive.length > LIVE_DISPLAY_CAP) {
      showMoreWrap.style.display = "block";
      showMoreBtn.textContent = "Show fewer";
    } else {
      showMoreWrap.style.display = "none";
    }
  }

  // Never auto-select a job the dispatcher hasn't chosen; if the
  // previously-selected message fell out of the live window, clear the
  // detail pane rather than silently substituting an older one.
  if (!newJobsLive.find(j => j.message_id === state.jobsSelected)) {
    state.jobsSelected = newJobsLive.length ? newJobsLive[0].message_id : null;
  }
  if (state.jobsSelected) renderJobDetail(state.jobsSelected, newJobsLive);
  else { document.getElementById("jobsDetailPane").classList.remove("open"); document.getElementById("jobsDetailPane").innerHTML = ""; }
}

document.getElementById("jobsShowMoreBtn").addEventListener("click", () => {
  state.jobsShowAllLive = !state.jobsShowAllLive;
  renderJobsScreen();
});

document.getElementById("jobsArchiveLinkBtn").addEventListener("click", () => setScreen("archive"));

/* ==================================================== ARCHIVE / INBOX ==== */
// Historical inbound messages -- never labeled as jobs anywhere on this
// screen. Real, honest server-side pagination via /api/inbox_review (route_inbox_review),
// which is a completely separate query path from the live Jobs triage
// queue -- it never runs the correlation/dedup gate that would otherwise
// hide "unmatched" messages, which is the whole point of this screen.
const archiveState = { page: 1, pageSize: 25, search: "", unmatchedOnly: false, total: 0 };

async function loadArchive() {
  const data = await callOrHandleAuth(() => Api.getInboxReview({
    page: archiveState.page, page_size: archiveState.pageSize,
    search: archiveState.search, unmatched_only: archiveState.unmatchedOnly ? "1" : "",
  }));
  if (!data) return;
  archiveState.total = data.total;
  renderArchiveScreen(data.messages);
}

function archiveRowHtml(m) {
  return `
    <tr>
      <td>${esc(ageText(minutesSince(m.received_at)) || "")}</td>
      <td>${esc(m.source)}</td>
      <td><span class="chip chip-waiting">${esc(m.status)}</span></td>
      <td>${esc(m.text_preview)}</td>
    </tr>`;
}

function archiveMobileCardHtml(m) {
  return `
    <div class="mobile-row">
      <div class="row-top">${esc(m.source)} <span class="chip chip-waiting">${esc(m.status)}</span> <span class="age">${esc(ageText(minutesSince(m.received_at)) || "")}</span></div>
      <div class="row-bottom">${esc(m.text_preview)}</div>
    </div>`;
}

function renderArchiveScreen(messages) {
  const totalPages = Math.max(1, Math.ceil(archiveState.total / archiveState.pageSize));
  document.getElementById("archiveCount").textContent = `(${archiveState.total} total)`;
  document.getElementById("archivePageLabel").textContent = `Page ${archiveState.page} of ${totalPages}`;
  document.getElementById("archivePrevBtn").disabled = archiveState.page <= 1;
  document.getElementById("archiveNextBtn").disabled = archiveState.page >= totalPages;

  const tbody = document.getElementById("archiveTableBody");
  const mobileList = document.getElementById("archiveMobileList");
  const emptyState = document.getElementById("archiveEmptyState");
  if (!messages.length) {
    tbody.innerHTML = "";
    mobileList.innerHTML = "";
    emptyState.style.display = "block";
  } else {
    emptyState.style.display = "none";
    tbody.innerHTML = messages.map(archiveRowHtml).join("");
    mobileList.innerHTML = messages.map(archiveMobileCardHtml).join("");
  }
}

document.getElementById("archiveSearchBtn").addEventListener("click", () => {
  archiveState.search = document.getElementById("archiveSearch").value.trim();
  archiveState.unmatchedOnly = document.getElementById("archiveUnmatchedOnly").checked;
  archiveState.page = 1;
  loadArchive();
});
document.getElementById("archiveSearch").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("archiveSearchBtn").click();
});
document.getElementById("archivePrevBtn").addEventListener("click", () => {
  if (archiveState.page > 1) { archiveState.page -= 1; loadArchive(); }
});
document.getElementById("archiveNextBtn").addEventListener("click", () => {
  archiveState.page += 1; loadArchive();
});

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
    <div class="mobile-row job-card ${j.bucket === "needs_action" ? "needs-action" : "waiting"}" data-mid="${esc(j.message_id)}">
      <div class="row-top">${sourceChip(j.source)} <strong>${esc(j.ticket || "No ref")}</strong> <span class="age">${esc(ageText(j.age_minutes))}</span></div>
      <div class="row-bottom">${esc(j.service || "Unclear")} · ${esc(j.city || "")}${j.state ? ", " + esc(j.state) : ""}</div>
      <div class="row-status"><span class="chip ${jobStatusChipClass(j)}">${esc(jobStatusText(j))}</span></div>
      <button type="button" class="btn btn-primary btn-block card-primary-btn">${esc(jobPrimaryLabel(j))}</button>
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
    const j = newJobsLive.find(x => x.message_id === mid);
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
    ${lifecycleStepperHtml(j.steps, j.delivery_mode)}
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
    <button class="btn btn-primary btn-block" style="margin-top:6px;" data-jobaction="confirm_prompt" data-mid="${esc(j.message_id)}">Mark sent manually</button>`;
}

function wireJobDetailActions(j) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("jobsDetailPane");
  root.querySelectorAll("[data-jobaction]").forEach(el => {
    el.addEventListener("click", () => handleJobAction(el.dataset.jobaction, el.dataset.mid));
  });
}

async function handleJobAction(action, mid) {
  const j = newJobsLive.find(x => x.message_id === mid);
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
      // Fast Path: confirming dispatch is exactly the moment this job
      // leaves the Jobs queue and becomes an Active job -- take the
      // operator straight there instead of leaving them on the Jobs
      // screen to navigate and re-find it themselves
      // (docs/WORKFLOW_SPEED_AUDIT.md's stage 6 friction).
      if (r.job_id) {
        setScreen("active", { skipRefresh: true });
        await loadActive();
        selectActive(r.job_id);
      }
    } catch (e) { showTransientError(e.message); }
  });
  if (copyAgain) copyAgain.addEventListener("click", () => copyText(j.luxor_message));
  if (notYet) notYet.addEventListener("click", () => hideConfirmModal());
}

const _origShowConfirmModal = showConfirmModal;
showConfirmModal = function (html) {
  _origShowConfirmModal(html);
  const mid = state.jobsSelected;
  const j = newJobsLive.find(x => x.message_id === mid);
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

/* ===================================================== COMMAND CENTER === */
// Every figure here comes straight from /api/command_center
// (kpi_read_model.get_command_center(), see docs/command_center.md) or
// /api/active (the exact same data source the Active screen itself
// uses) -- this file computes zero KPIs of its own, only formats what
// the backend already decided.

function minutesSince(isoString) {
  if (!isoString) return null;
  const then = new Date(isoString).getTime();
  if (Number.isNaN(then)) return null;
  return Math.max(0, Math.round((Date.now() - then) / 60000));
}

function wireScreenLinks(container) {
  container.querySelectorAll("[data-screen]").forEach(el => {
    el.addEventListener("click", () => setScreen(el.dataset.screen));
  });
}

const ccState = { chartPeriod: "today" };
// Show the essential first, reveal the rest only when opened: the
// dashboard is a summary, not a full list -- "View all" links to the
// dedicated screen for anything beyond these caps.
const CC_ATTENTION_MAX = 5;
const CC_ACTIVE_BOARD_MAX = 6;

async function loadCommandCenter() {
  const dashboard = await callOrHandleAuth(() => Api.getCommandCenter(ccState.chartPeriod));
  if (!dashboard) return;
  const activeData = await callOrHandleAuth(() => Api.getActive());
  renderCommandCenterScreen(dashboard, activeData ? activeData.entries : []);
}

function fmtKpiValue(kpi) {
  if (!kpi) return "--";
  if (kpi.data_quality === "needs_business_definition") return "N/A";
  if (kpi.value === null || kpi.value === undefined) return "--";
  return kpi.value;
}

// One restrained tint + icon per KPI category -- distinct at a glance,
// never fully saturated (see --kpi-*-accent/--kpi-*-bg tokens).
const CC_KPI_CATEGORIES = {
  new: { cls: "kpi-new", icon: "&#9737;", secondary: "Last 60 minutes" },
  waiting: { cls: "kpi-waiting", icon: "&#8987;", secondary: "Needs assignment" },
  active: { cls: "kpi-active", icon: "&#9679;", secondary: "With a technician" },
  inprogress: { cls: "kpi-inprogress", icon: "&#9670;", secondary: "En route or on site" },
  closingsdue: { cls: "kpi-closingsdue", icon: "&#36;", secondary: "Awaiting closing" },
  completed: { cls: "kpi-completed", icon: "&#10003;", secondary: "Closed out today" },
};

function ccKpiCardHtml(title, kpi, screenLink, category) {
  const value = fmtKpiValue(kpi);
  const note = kpi.data_quality === "needs_business_definition" ? "Not trackable yet" : "";
  const cat = CC_KPI_CATEGORIES[category] || CC_KPI_CATEGORIES.new;
  return `
    <div class="cc-kpi-card ${cat.cls}" data-screen="${screenLink}" title="${esc(kpi.explanation || "")}">
      <div class="cc-kpi-icon">${cat.icon}</div>
      <div class="cc-kpi-value">${esc(value)}</div>
      <div class="cc-kpi-label">${esc(title)}</div>
      <div class="cc-kpi-secondary">${note ? esc(note) : cat.secondary}</div>
    </div>`;
}

function ccClosedAmountHtml(kpi) {
  if (kpi.data_quality === "ok") {
    return `<div class="cc-closed-amount-card cc-closed-amount-ok">
      <div class="cc-closed-amount-value">$${Number(kpi.value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
      <div class="cc-closed-amount-label">Verified closed amount today</div>
    </div>`;
  }
  if (kpi.data_quality === "zero") {
    return `<div class="cc-closed-amount-card">
      <div class="cc-closed-amount-value">$0.00</div>
      <div class="cc-closed-amount-label">Verified closed amount today</div>
    </div>`;
  }
  return `<div class="cc-closed-amount-card cc-closed-amount-incomplete">
    <div class="cc-closed-amount-value">Closing data incomplete</div>
    <div class="cc-closed-amount-label">Verified closed amount today -- ${kpi.numerator} of ${kpi.denominator} closed job(s) have a reliable numeric amount</div>
  </div>`;
}

function ccAttentionItemHtml(item) {
  const reasonLabels = {
    not_dispatched_within_target: "Waiting beyond the review/assignment target",
    stale_active_job: "No update in over the stale-job target",
    whatsapp_observation_pending: "Real WhatsApp update waiting for review",
  };
  const reason = reasonLabels[item.type] || item.type;
  const label = item.ticket ? `#${esc(item.ticket)}` : (item.group_name ? esc(item.group_name) : "WhatsApp update");
  const age = item.age_minutes !== null && item.age_minutes !== undefined ? ageText(item.age_minutes) : "";
  const targetScreen = item.type === "whatsapp_observation_pending" ? "active" : "jobs";
  return `
    <div class="cc-attention-row">
      <div class="cc-attention-marker" aria-hidden="true">&#33;</div>
      <div class="cc-attention-main">
        <span class="cc-attention-ticket">${label}</span>
        <span class="cc-attention-source">${esc(item.source || "")}</span>
        ${age ? `<span class="cc-attention-age">${esc(age)}</span>` : ""}
      </div>
      <div class="cc-attention-reason">${esc(reason)}</div>
      <button class="btn btn-secondary cc-small-btn" data-screen="${targetScreen}">${esc(item.primary_action || "Review")}</button>
    </div>`;
}

function ccStageBadge(nextAction) {
  if (nextAction === "Awaiting technician") return { label: "Waiting for technician", cls: "chip-waiting" };
  if (nextAction === "Record closing") return { label: "Closing needed", cls: "chip-needs-action" };
  return { label: "Assigned", cls: "chip-settled" };
}

function ccActiveRowHtml(j) {
  const stage = ccStageBadge(j.next_action);
  const ageMin = minutesSince(j.waiting_since);
  return `
    <tr>
      <td><strong>${esc(j.ticket || "No ref")}</strong></td>
      <td>${sourceChip(j.source)}</td>
      <td>${esc(j.city || "")}</td>
      <td>${j.technician_name ? technicianWithAvatarHtml(j.technician_name) : "Not recorded"}</td>
      <td><span class="chip ${stage.cls}">${stage.label}</span></td>
      <td>${ageMin === null ? "--" : ageText(ageMin)}</td>
      <td>${esc(j.next_action)}</td>
      <td><button class="btn btn-secondary cc-small-btn" data-screen="active">Open</button></td>
    </tr>`;
}

function ccActiveMobileCardHtml(j) {
  const stage = ccStageBadge(j.next_action);
  const ageMin = minutesSince(j.waiting_since);
  return `
    <div class="mobile-row job-card" data-screen="active">
      <div class="row-top">${esc(j.ticket || "No ref")} ${sourceChip(j.source)} <span class="age">${ageMin === null ? "" : ageText(ageMin)}</span></div>
      <div class="row-bottom">${esc(j.city || "")} · ${notRecorded(j.technician_name)}</div>
      <div class="row-status"><span class="chip ${stage.cls}">${stage.label}</span></div>
      <div class="row-next-action">${esc(j.next_action)}</div>
    </div>`;
}

/* ------------------------------------------------------------- Charts -- */
// Native SVG/CSS only -- no chart library dependency. Each function takes
// only what the API already returned and renders it; none compute a KPI.

const CC_CHART_COLORS = ["#2955d8", "#1f7d6f", "#9a5a12", "#c8323a", "#6b4fa0", "#0f2a4a"];

function ccDonutChartSvg(counts) {
  let entries = Object.entries(counts).filter(([, n]) => n > 0);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (!total) return `<div class="empty-state">No jobs received yet today.</div>`;
  // Never more than 6 slices -- readability over completeness. Beyond
  // that, keep the top 5 by count and fold the rest into "Other".
  if (entries.length > 6) {
    const sorted = [...entries].sort((a, b) => b[1] - a[1]);
    const top = sorted.slice(0, 5);
    const otherTotal = sorted.slice(5).reduce((sum, [, n]) => sum + n, 0);
    entries = [...top, ["Other", otherTotal]];
  }
  const r = 54, cx = 60, cy = 60, circumference = 2 * Math.PI * r;
  let offset = 0;
  const arcs = entries.map(([label, n], i) => {
    const frac = n / total;
    const dash = frac * circumference;
    const el = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${CC_CHART_COLORS[i % CC_CHART_COLORS.length]}" stroke-width="16" stroke-dasharray="${dash} ${circumference - dash}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})"></circle>`;
    offset += dash;
    return el;
  }).join("");
  const legend = entries.map(([label, n], i) => `
    <div class="cc-legend-row"><span class="cc-legend-dot" style="background:${CC_CHART_COLORS[i % CC_CHART_COLORS.length]}"></span>${esc(label)} <strong>${n}</strong></div>
  `).join("");
  return `
    <div class="cc-donut-wrap">
      <svg viewBox="0 0 120 120" width="120" height="120">${arcs}</svg>
      <div class="cc-legend">${legend}</div>
    </div>`;
}

function ccPipelineBarsHtml(counts) {
  const order = ["New", "Waiting", "Approved / Assigned", "In Progress", "Closed", "Cancelled"];
  const max = Math.max(1, ...order.map(k => counts[k] || 0));
  return `<div class="cc-pipeline">` + order.map(label => {
    const n = counts[label] || 0;
    const pct = Math.round((n / max) * 100);
    return `
      <div class="cc-pipeline-row">
        <div class="cc-pipeline-label">${esc(label)}</div>
        <div class="cc-pipeline-track"><div class="cc-pipeline-fill" style="width:${pct}%"></div></div>
        <div class="cc-pipeline-count">${n}</div>
      </div>`;
  }).join("") + `</div>
  <div class="cc-chart-footnote">"En route" has no signal separate from "In Progress" in the real data yet -- jobs that might be en route show simply as In Progress.</div>`;
}

function ccTechActivityHtml(rows) {
  if (!rows.length) return `<div class="empty-state">No technician activity recorded yet today.</div>`;
  const max = Math.max(1, ...rows.map(t => t.jobs_assigned));
  // Alphabetical, not ranked by volume -- this is an activity view, not a leaderboard.
  const sorted = [...rows].sort((a, b) => a.name.localeCompare(b.name));
  return `<div class="cc-pipeline">` + sorted.map(t => {
    const pct = Math.round((t.jobs_assigned / max) * 100);
    return `
      <div class="cc-pipeline-row">
        <div class="cc-pipeline-label">${esc(t.name)}</div>
        <div class="cc-pipeline-track"><div class="cc-pipeline-fill" style="width:${pct}%"></div></div>
        <div class="cc-pipeline-count">${t.jobs_assigned} assigned · ${t.jobs_completed} done</div>
      </div>`;
  }).join("") + `</div>`;
}

function renderCommandCenterScreen(dashboard, activeEntries) {
  const k = dashboard.kpis;
  document.getElementById("ccGeneratedAt").textContent = "As of " + new Date(dashboard.generated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

  const grid = document.getElementById("ccKpiGrid");
  grid.innerHTML = [
    ccKpiCardHtml("New jobs -- last 60 min", k.new_last_60_minutes, "jobs", "new"),
    ccKpiCardHtml("Waiting for assignment", k.waiting_for_assignment, "jobs", "waiting"),
    ccKpiCardHtml("Active with technician", k.active_with_technician, "active", "active"),
    ccKpiCardHtml("In progress", k.in_progress, "active", "inprogress"),
    ccKpiCardHtml("Closings due", k.closings_due, "closings", "closingsdue"),
    ccKpiCardHtml("Completed today", k.completed_today, "history", "completed"),
  ].join("");
  wireScreenLinks(grid);

  const closedAmountEl = document.getElementById("ccClosedAmount");
  closedAmountEl.innerHTML = ccClosedAmountHtml(dashboard.verified_closed_amount_today);

  const attentionList = document.getElementById("ccAttentionList");
  const attentionEmpty = document.getElementById("ccAttentionEmpty");
  const attentionViewAllWrap = document.getElementById("ccAttentionViewAllWrap");
  const attentionForBoard = dashboard.attention_now.slice(0, CC_ATTENTION_MAX);
  if (attentionForBoard.length) {
    attentionEmpty.style.display = "none";
    attentionList.innerHTML = attentionForBoard.map(ccAttentionItemHtml).join("");
    wireScreenLinks(attentionList);
  } else {
    attentionEmpty.style.display = "block";
    attentionList.innerHTML = "";
  }
  attentionViewAllWrap.style.display = dashboard.attention_now.length > attentionForBoard.length ? "inline" : "none";

  const activeForBoard = activeEntries.filter(j => j.technician_name).slice(0, CC_ACTIVE_BOARD_MAX);
  const activeTbody = document.getElementById("ccActiveTableBody");
  const activeMobile = document.getElementById("ccActiveMobileList");
  const activeEmpty = document.getElementById("ccActiveEmpty");
  const activeViewAllWrap = document.getElementById("ccActiveViewAllWrap");
  if (activeForBoard.length) {
    activeEmpty.style.display = "none";
    activeTbody.innerHTML = activeForBoard.map(ccActiveRowHtml).join("");
    activeMobile.innerHTML = activeForBoard.map(ccActiveMobileCardHtml).join("");
    wireScreenLinks(activeTbody);
    wireScreenLinks(activeMobile);
  } else {
    activeEmpty.style.display = "block";
    activeTbody.innerHTML = "";
    activeMobile.innerHTML = "";
  }
  activeViewAllWrap.style.display = activeEntries.filter(j => j.technician_name).length > activeForBoard.length ? "inline" : "none";
  wireScreenLinks(document.getElementById("ccActiveViewAllWrap"));
  wireScreenLinks(document.getElementById("ccAttentionViewAllWrap"));

  const periodSelect = document.getElementById("ccChartPeriodSelect");
  if (periodSelect && periodSelect.value !== ccState.chartPeriod) periodSelect.value = ccState.chartPeriod;

  document.getElementById("ccSourceMixChart").innerHTML = ccDonutChartSvg(dashboard.charts.job_source_mix);
  document.getElementById("ccPipelineChart").innerHTML = ccPipelineBarsHtml(dashboard.charts.job_pipeline);
  document.getElementById("ccTechActivityChart").innerHTML = ccTechActivityHtml(dashboard.charts.technician_activity);
}

document.getElementById("ccChartPeriodSelect").addEventListener("change", (e) => {
  ccState.chartPeriod = e.target.value;
  loadCommandCenter();
});

/* =============================================================== ACTIVE */
let whatsappPending = [];

async function loadActive() {
  const data = await callOrHandleAuth(() => Api.getActive());
  if (!data) return;
  activeJobs = data.entries;
  renderActiveScreen();
  loadWhatsappBridgeStatus();
  loadWhatsappPending();
}

async function loadWhatsappBridgeStatus() {
  try {
    const status = await callOrHandleAuth(() => Api.getWhatsappBridgeStatus());
    if (!status) return;
    document.getElementById("whatsappBridgeBanner").textContent = status.status;
  } catch (e) { /* non-critical -- Active screen still works without it */ }
}

async function loadWhatsappPending() {
  try {
    const data = await callOrHandleAuth(() => Api.getWhatsappPending());
    if (!data) return;
    whatsappPending = data.pending;
    renderWhatsappPending();
  } catch (e) { /* bridge/adapter unreachable -- CRM keeps working normally */ }
}

const ROLE_LABELS = {
  COMPANY_JOB_INTAKE: "Company job intake",
  COMPANY_CLOSINGS: "Company closings",
  TECH_DISPATCH: "Technician dispatch",
  TECH_CLOSINGS: "Technician closings",
  UNMAPPED_WORK: "Unmapped work group",
  IGNORE: "Ignore",
};

function whatsappNextAction(o) {
  if (o.group_mapping_status === "HUMAN_REVIEW") return "Needs mapping — confirm this group's company/technician, then attach or ignore.";
  if (o.human_review_needed) return "Bare acknowledgement — check context, then attach or ignore.";
  if (o.correlated_job_id) return `Likely reply to job #${o.correlated_job_id} — verify, then attach.`;
  return "Review and attach to the matching job, or ignore.";
}

function renderWhatsappPending() {
  const section = document.getElementById("whatsappPendingSection");
  const list = document.getElementById("whatsappPendingList");
  if (!whatsappPending.length) { section.style.display = "none"; list.innerHTML = ""; return; }
  section.style.display = "block";
  list.innerHTML = whatsappPending.map(o => {
    const roleLabel = ROLE_LABELS[o.group_role] || o.group_role || "Unmapped work group";
    const statusChip = o.group_mapping_status === "CONFIRMED"
      ? `<span class="chip chip-settled">CONFIRMED</span>`
      : `<span class="chip chip-needs-action">HUMAN REVIEW</span>`;
    const who = [o.group_company, o.group_technician_name].filter(Boolean).join(" · ");
    const whoLine = who ? `<div class="detail-meta">${esc(who)}${o.group_service_area ? " · " + esc(o.group_service_area) : ""}</div>` : "";
    return `
    <div class="mobile-row" data-mid="${esc(o.message_id)}">
      <div class="row-top">
        <strong>${esc(o.group_name)}</strong>
        <span class="age">${esc(o.extracted_ticket || "no ticket guess")}</span>
      </div>
      <div class="detail-meta">${esc(roleLabel)} ${statusChip} <span class="chip">${o.direction === "outgoing" ? "OUTGOING (audit only)" : "INCOMING"}</span></div>
      ${whoLine}
      <div class="row-bottom">${esc(o.text)}</div>
      <div class="detail-meta">${esc(whatsappNextAction(o))}</div>
      <div class="action-secondary-row" style="margin-top:6px;">
        <button class="btn btn-primary" data-whatsappaction="attach" data-mid="${esc(o.message_id)}">Attach to selected job</button>
        <button class="btn btn-secondary" data-whatsappaction="ignore" data-mid="${esc(o.message_id)}">Ignore</button>
      </div>
    </div>`;
  }).join("");
  list.querySelectorAll("[data-whatsappaction]").forEach(el => {
    el.addEventListener("click", () => handleWhatsappPendingAction(el.dataset.whatsappaction, el.dataset.mid));
  });
}

async function handleWhatsappPendingAction(action, messageId) {
  try {
    if (action === "ignore") {
      const r = await callOrHandleAuth(() => Api.ignoreWhatsappObservation(messageId)); if (!r) return;
      await loadWhatsappPending();
    } else if (action === "attach") {
      if (!state.activeSelected) { showTransientError("Select an Active job first, then Attach."); return; }
      const r = await callOrHandleAuth(() => Api.attachWhatsappObservation(messageId, state.activeSelected)); if (!r) return;
      // Pre-fill the existing, already-audited paste flow -- attaching
      // never itself calls DispatcherService; only the dispatcher's own
      // subsequent "Save verified update" tap does that, exactly like a
      // manual paste.
      state.activeRawText[state.activeSelected] = r.text;
      delete state.activePreview[state.activeSelected];
      await loadWhatsappPending();
      rerenderActiveDetail(state.activeSelected);
    }
  } catch (e) {
    showTransientError(e.message);
  }
}

function activeMobileRowHtml(j) {
  const actionable = j.bucket === "needs_action";
  const stage = ccStageBadge(j.next_action);
  const lastUpdate = j.waiting_since ? j.waiting_since.slice(0, 16).replace("T", " ") : "—";
  return `
    <div class="mobile-row job-card ${actionable ? "needs-action" : "waiting"}" data-id="${j.id}">
      <div class="row-top"><strong>${esc(j.ticket || "No ref")}</strong> <span class="chip ${stage.cls}">${stage.label}</span></div>
      <div class="row-bottom">${sourceChip(j.source)} ${esc(j.city || "")} · ${esc(j.description || "—")}</div>
      <div class="row-bottom">${esc(j.technician_name || "Unassigned")} · Last update ${esc(lastUpdate)}</div>
      ${actionable
        ? `<button type="button" class="btn btn-primary btn-block card-primary-btn">${esc(j.next_action)}</button>`
        : `<div class="row-next-action">${esc(j.next_action)}</div>`}
    </div>`;
}

function renderActiveScreen() {
  document.getElementById("activeCount").textContent = `(${activeJobs.length})`;
  const tbody = document.getElementById("activeTableBody");
  tbody.innerHTML = activeJobs.length ? activeJobs.map(j => {
    const stage = ccStageBadge(j.next_action);
    return `
    <tr data-id="${j.id}" class="${state.activeSelected === j.id ? "selected" : ""}">
      <td><strong>${esc(j.ticket || "No ref")}</strong></td>
      <td>${sourceChip(j.source)}</td>
      <td>${esc(j.city || "")}</td>
      <td>${esc(j.description || "—")}</td>
      <td>${technicianWithAvatarHtml(j.technician_name)}</td>
      <td><span class="chip ${stage.cls}">${stage.label}</span></td>
      <td>${esc(j.waiting_since ? j.waiting_since.slice(0, 16).replace("T", " ") : "—")}</td>
      <td>${esc(j.next_action)}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="8"><div class="empty-state">No active jobs.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-id]").forEach(tr => tr.addEventListener("click", () => selectActive(parseInt(tr.dataset.id, 10))));

  const mobileList = document.getElementById("activeMobileList");
  mobileList.innerHTML = activeJobs.length ? activeJobs.map(j => activeMobileRowHtml(j)).join("") : `<div class="empty-state">No active jobs.</div>`;
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

const TECHNICIAN_UPDATE_TYPES = ["Going", "ETA", "No Answer", "No Show", "Not Available", "Customer Cancelled", "Completed", "Custom"];

const K_CATEGORY_LABEL = { EXACT: "Exact match", STRONG: "Strong match", AMBIGUOUS: "Ambiguous" };

// Renders the backend-computed lifecycle (dispatcher.workflow_projection.derive_lifecycle_steps) --
// this function only maps a step's already-decided status to markup; it
// never decides completed/current/blocked/pending itself.
function lifecycleStepperHtml(steps, deliveryMode) {
  if (!steps || !steps.length) return "";
  const rows = steps.map(s => {
    const icon = s.status === "completed" ? "&#10003;" : s.status === "blocked" ? "&#33;" : s.status === "current" ? "&#9679;" : "&#9675;";
    return `<div class="lifecycle-step lifecycle-step-${s.status}">
      <span class="lifecycle-step-icon" aria-hidden="true">${icon}</span>
      <span class="lifecycle-step-label">${s.step}. ${esc(s.label)}</span>
    </div>`;
  }).join("");
  return `
    <div class="detail-section lifecycle-stepper">
      <div class="subsection-label">Lifecycle ${deliveryMode ? `<span class="chip chip-waiting" style="margin-left:6px;">${esc(deliveryMode)}</span>` : ""}</div>
      <div class="lifecycle-step-list">${rows}</div>
    </div>`;
}

function technicianKBlockHtml(j) {
  const tk = j.technician_k;
  if (!tk) return "";

  if (tk.status === "accepted") {
    return `
      <div class="detail-section">
        <div class="subsection-label">Technician acceptance</div>
        <div class="checked-line">&#10003; Technician confirmed accepted (${esc((tk.accepted_at || "").slice(0, 16).replace("T", " "))})</div>
      </div>`;
  }
  if (tk.status === "declined") {
    return `
      <div class="detail-section">
        <div class="review-banner">Technician declined: ${esc(tk.reason || "")}</div>
      </div>`;
  }

  const candidates = tk.candidates || [];
  const candidateRows = candidates.length
    ? candidates.map(c => `
      <div class="k-candidate">
        <div class="k-candidate-head"><span class="chip ${c.category === "EXACT" ? "chip-settled" : c.category === "AMBIGUOUS" ? "chip-waiting" : "chip-needs-action"}">${esc(K_CATEGORY_LABEL[c.category] || c.category)}</span></div>
        <div class="detail-meta">${c.reasons.map(esc).join("; ")}</div>
        <button type="button" class="btn btn-primary btn-block" data-activeaction="confirm_k_accepted" data-msgid="${esc(c.message_id)}" data-id="${j.id}">Confirm technician accepted</button>
        <button type="button" class="btn btn-secondary btn-block" style="margin-top:4px;" data-activeaction="confirm_k_declined" data-msgid="${esc(c.message_id)}" data-id="${j.id}">${c.is_decline_shaped ? "Confirm technician declined" : "This is a decline, not an accept"}</button>
      </div>`).join("")
    : `<div class="detail-meta">No matching WhatsApp reply captured yet.</div>`;

  return `
    <div class="detail-section">
      <div class="subsection-label">Technician acceptance -- waiting on K</div>
      ${candidateRows}
    </div>`;
}

function statusUpdateBlockHtml(j) {
  const owed = j.company_update_owed;
  const draft = state.statusUpdateDraft[j.id] || {};
  const recordForm = `
    <div class="form-row">
      <div class="form-field">
        <label>Technician status</label>
        <select id="statusUpdateType-${j.id}">${TECHNICIAN_UPDATE_TYPES.map(t => `<option value="${esc(t)}" ${t === draft.update_type ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
      </div>
      <div class="form-field" id="statusUpdateEtaWrap-${j.id}" style="${draft.update_type === "ETA" ? "" : "display:none;"}">
        <label>ETA (minutes)</label>
        <input type="number" min="1" id="statusUpdateEta-${j.id}" value="${esc(draft.eta_minutes || "")}">
      </div>
    </div>
    <div class="form-field">
      <label>Technician's actual reply (verbatim)</label>
      <textarea id="statusUpdateRaw-${j.id}">${esc(draft.raw_update || "")}</textarea>
    </div>
    <button type="button" class="btn btn-primary btn-block" data-activeaction="record_status_update" data-id="${j.id}">Record technician update</button>`;

  if (!owed) {
    return `
      <div class="detail-section">
        <div class="subsection-label">Technician status update</div>
        ${recordForm}
      </div>`;
  }

  const prepared = state.statusUpdatePrepared[j.id];
  let actionBlock;
  if (prepared) {
    actionBlock = `
      <pre class="msg-block">${esc(prepared.text)}</pre>
      <button type="button" class="btn btn-secondary btn-block" data-activeaction="copy_status_reply" data-id="${j.id}">Copy reply</button>
      <button type="button" class="btn btn-primary btn-block" style="margin-top:6px;" data-activeaction="confirm_status_update_sent" data-id="${j.id}">Mark sent manually</button>`;
  } else if (owed.confirmed) {
    actionBlock = `
      <pre class="msg-block">${esc(owed.suggested_reply)}</pre>
      <button type="button" class="btn btn-secondary btn-block" data-activeaction="prepare_company_update" data-id="${j.id}">Prepare company reply</button>`;
  } else {
    actionBlock = `
      <div class="review-banner">${esc(owed.reason)}</div>
      <div class="form-field"><label>Company reply (typed manually -- no deterministic reply for this update)</label>
      <textarea id="statusUpdateManualText-${j.id}"></textarea></div>
      <button type="button" class="btn btn-secondary btn-block" data-activeaction="prepare_company_update" data-id="${j.id}">Prepare company reply</button>`;
  }

  return `
    <div class="detail-section">
      <div class="subsection-label">Technician status update</div>
      <div class="detail-meta">Latest: <strong>${esc(owed.update_type)}${owed.eta_minutes ? ` (${owed.eta_minutes} min)` : ""}</strong> -- ${esc(owed.raw_update || "")}</div>
      <div class="review-banner" style="background:var(--kpi-waiting-bg);">Company update owed since ${esc((owed.recorded_at || "").slice(0, 16).replace("T", " "))}</div>
      ${actionBlock}
      <details class="disclosure"><summary>Record another update</summary>${recordForm}</details>
    </div>`;
}

function activeDetailHtml(j) {
  const preview = state.activePreview[j.id];
  const rawText = state.activeRawText[j.id] || "";

  let interpretBlock = `
    <div class="subsection-label">Price / commercial update (quote, negotiation)</div>
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
        <button class="btn btn-secondary btn-block" data-activeaction="copy_reply" data-id="${j.id}">Copy reply</button>
        <button class="btn btn-primary btn-block" style="margin-top:6px;" data-activeaction="confirm_reply" data-id="${j.id}">Mark sent manually</button>
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

  // Fast Path: once the technician has reported the job Completed and
  // there's no company update still owed for it, the only thing left to
  // do with this job is go record the closing -- on a different screen.
  // Surface that as the one dominant primary action here instead of
  // making the operator remember to navigate to Closings and find this
  // job again themselves (docs/WORKFLOW_SPEED_AUDIT.md's stage 9 friction).
  const goToClosingBlock = j.next_action === "Record closing" ? `
    <div class="detail-section fast-path-primary">
      <button type="button" class="btn btn-primary btn-block" data-activeaction="go_to_closing" data-id="${j.id}">Go to Closing &rarr;</button>
    </div>` : "";

  // Priority 4: when this job's own next step is passively waiting on
  // someone else (nothing the operator can actually do to it right
  // now), say so plainly and offer a way to keep moving instead of
  // leaving them staring at a job with no action available.
  const waitingBlock = j.technician_k && j.technician_k.status === "waiting" ? `
    <div class="detail-section">
      <span class="chip chip-priority-waiting">Waiting on technician</span>
      <button type="button" class="btn btn-secondary btn-block" style="margin-top:6px;" data-nextactionable="1" data-exclude="${j.id}">Next actionable job &rarr;</button>
    </div>` : "";

  return `
    <div class="detail-header">${sourceChip(j.source)}<span class="detail-ticket">${esc(j.ticket || "No ref")}</span></div>
    <div class="detail-meta">${esc(j.technician_name || "Unassigned")} · ${esc(j.city || "")} · Status: <strong>${esc(j.status)}</strong></div>
    <div class="detail-meta">Next: <strong>${esc(j.next_action)}</strong></div>
    ${j.workflow ? lifecycleStepperHtml(j.workflow.steps, j.workflow.delivery_mode) : ""}
    ${goToClosingBlock}
    ${waitingBlock}
    ${technicianKBlockHtml(j)}
    ${statusUpdateBlockHtml(j)}
    <div class="detail-section">${interpretBlock}</div>
    <div class="detail-section" id="activeEditBlock-${j.id}">${editBlock}</div>
    <div id="activeReplyBlock-${j.id}">${replyBlock}</div>
    ${shadowBlock}`;
}

function wireActiveDetailActions(j) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("activeDetailPane");
  root.querySelectorAll("[data-activeaction]").forEach(el => {
    el.addEventListener("click", () => handleActiveAction(el.dataset.activeaction, parseInt(el.dataset.id, 10), el.dataset.msgid));
  });
  const typeSelect = document.getElementById(`statusUpdateType-${j.id}`);
  const etaWrap = document.getElementById(`statusUpdateEtaWrap-${j.id}`);
  if (typeSelect && etaWrap) {
    typeSelect.addEventListener("change", () => {
      etaWrap.style.display = typeSelect.value === "ETA" ? "" : "none";
    });
  }
}

async function handleActiveAction(action, id, msgId) {
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
    } else if (action === "record_status_update") {
      const update_type = document.getElementById(`statusUpdateType-${id}`).value;
      const eta_minutes = document.getElementById(`statusUpdateEta-${id}`) ? document.getElementById(`statusUpdateEta-${id}`).value : "";
      const raw_update = document.getElementById(`statusUpdateRaw-${id}`).value;
      if (!raw_update.trim()) { showTransientError("Paste the technician's actual reply first."); return; }
      if (update_type === "ETA" && !eta_minutes) { showTransientError("ETA updates require a minutes value."); return; }
      state.statusUpdateDraft[id] = { update_type, eta_minutes, raw_update };
      const r = await callOrHandleAuth(() => Api.recordTechnicianUpdate(id, update_type, raw_update, eta_minutes ? parseInt(eta_minutes, 10) : null));
      if (!r) return;
      delete state.statusUpdatePrepared[id];
      state.statusUpdateDraft[id] = {};
      await loadActive();
    } else if (action === "prepare_company_update") {
      const owed = (activeJobs.find(x => x.id === id) || {}).company_update_owed;
      if (!owed) return;
      const manualTextEl = document.getElementById(`statusUpdateManualText-${id}`);
      const manualText = manualTextEl ? manualTextEl.value : null;
      if (!owed.confirmed && (!manualText || !manualText.trim())) {
        showTransientError("Type the company reply first -- no deterministic reply for this update.");
        return;
      }
      const r = await callOrHandleAuth(() => Api.prepareCompanyUpdate(id, owed.update_type, owed.eta_minutes, manualText));
      if (!r) return;
      state.statusUpdatePrepared[id] = { text: r.prepared_text };
      rerenderActiveDetail(id);
    } else if (action === "copy_status_reply") {
      const prepared = state.statusUpdatePrepared[id];
      if (prepared) await copyText(prepared.text);
    } else if (action === "confirm_status_update_sent") {
      const prepared = state.statusUpdatePrepared[id];
      if (!prepared) return;
      const r = await callOrHandleAuth(() => Api.confirmCompanyUpdateSent(id, prepared.text));
      if (!r) return;
      delete state.statusUpdatePrepared[id];
      await loadActive();
    } else if (action === "confirm_k_accepted") {
      const r = await callOrHandleAuth(() => Api.confirmTechnicianAccepted(id, msgId));
      if (!r) return;
      await loadActive();
    } else if (action === "confirm_k_declined") {
      const reason = window.prompt("Why did the technician decline? (required)");
      if (!reason || !reason.trim()) return;
      const r = await callOrHandleAuth(() => Api.confirmTechnicianDeclined(id, msgId, reason.trim()));
      if (!r) return;
      await loadActive();
    } else if (action === "go_to_closing") {
      // Fast Path: stay on this same job, just switch screens for it --
      // no separate "find it in the Closings list" step.
      setScreen("closings", { skipRefresh: true });
      await loadClosings();
      selectClosing(id);
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
      <button class="btn btn-secondary btn-block" data-closingaction="prepare" data-id="${c.id}">Copy closing</button>
      <button class="btn btn-primary btn-block" style="margin-top:6px;" data-closingaction="confirm_sent" data-id="${c.id}">Mark sent manually</button>`;
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

/* ======================================================= LEARNING REVIEW */
const REVIEW_STATUSES = ["VERIFIED", "CORRECTED", "EXCLUDE", "SENSITIVE", "INCOMPLETE"];

async function loadLearningReview() {
  const data = await callOrHandleAuth(() => Api.getLearningReview());
  if (!data) return;
  learningEpisodes = data.episodes;
  renderLearningReviewScreen(data.reviewed_count);
}

function renderLearningReviewScreen(reviewedCount) {
  const countEl = document.getElementById("learningCount");
  countEl.textContent = reviewedCount > 0
    ? `Reviewed examples: ${reviewedCount}`
    : "No real reviewed examples yet. Complete a supervised job to begin.";

  const tbody = document.getElementById("learningTableBody");
  tbody.innerHTML = learningEpisodes.length ? learningEpisodes.map(e => `
    <tr data-id="${esc(e.episode_id)}" class="${state.learningSelected === e.episode_id ? "selected" : ""}">
      <td><strong>${esc(e.ticket_or_pdl || "No ref")}</strong></td>
      <td>${e.source ? sourceChip(e.source) : "—"}</td>
      <td>${esc(e.normalized_intent || "—")}</td>
      <td>${esc(e.review_status)}</td>
    </tr>`).join("") : `<tr><td colspan="4"><div class="empty-state">No real Shadow episodes yet.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-id]").forEach(tr => tr.addEventListener("click", () => selectLearning(tr.dataset.id)));

  const mobileList = document.getElementById("learningMobileList");
  mobileList.innerHTML = learningEpisodes.length ? learningEpisodes.map(e => `
    <div class="mobile-row" data-id="${esc(e.episode_id)}">
      <div class="row-top">${e.source ? sourceChip(e.source) : ""} <strong>${esc(e.ticket_or_pdl || "No ref")}</strong></div>
      <div class="row-bottom">${esc(e.normalized_intent || "—")} · ${esc(e.review_status)}</div>
    </div>`).join("") : `<div class="empty-state">No real Shadow episodes yet.</div>`;
  mobileList.querySelectorAll(".mobile-row").forEach(row => row.addEventListener("click", () => selectLearning(row.dataset.id)));

  if (!learningEpisodes.find(e => e.episode_id === state.learningSelected)) {
    state.learningSelected = learningEpisodes.length ? learningEpisodes[0].episode_id : null;
  }
  if (state.learningSelected) renderLearningDetail(state.learningSelected);
  else { document.getElementById("learningDetailPane").classList.remove("open"); document.getElementById("learningDetailPane").innerHTML = ""; }
}

function selectLearning(episodeId) {
  state.learningSelected = episodeId;
  renderLearningReviewScreen(learningEpisodes.filter(e => e.review_status !== "UNREVIEWED").length);
  if (isMobile()) {
    const e = learningEpisodes.find(x => x.episode_id === episodeId);
    if (e) { openSheet(learningDetailHtml(e)); wireLearningDetailActions(e); }
  }
}

function learningDetailHtml(e) {
  const actions = REVIEW_STATUSES.map(s =>
    `<button class="btn ${e.review_status === s ? "btn-primary" : "btn-secondary"}" data-learningaction="${s}" data-id="${esc(e.episode_id)}" style="margin:3px;">${s.charAt(0) + s.slice(1).toLowerCase()}</button>`
  ).join("");
  return `
    <div class="detail-header">${e.source ? sourceChip(e.source) : ""}<span class="detail-ticket">${esc(e.ticket_or_pdl || "No ref")}</span></div>
    <div class="detail-meta">Intent: ${esc(e.normalized_intent || "—")}</div>
    <div class="detail-section">
      <div class="subsection-label">Original observation</div>
      <pre class="msg-block">${esc(e.original_observation || "—")}</pre>
      <div class="subsection-label">System suggestion</div>
      <pre class="msg-block">${esc(e.shadow_suggested_reply || "—")}</pre>
      <div class="subsection-label">Dispatcher correction / confirmed response</div>
      <pre class="msg-block">${esc(e.dispatcher_actual_reply || "—")}</pre>
      <div class="detail-meta">Later outcome: ${esc(e.final_job_outcome || e.negotiation_outcome || "Not yet known")}</div>
      ${e.human_review_required ? `<div class="review-banner">Please review before continuing.</div>` : ""}
    </div>
    <div class="detail-section">
      <div class="subsection-label">Review status: ${esc(e.review_status)}</div>
      ${actions}
    </div>`;
}

function wireLearningDetailActions(e) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("learningDetailPane");
  root.querySelectorAll("[data-learningaction]").forEach(el => {
    el.addEventListener("click", async () => {
      try {
        const r = await callOrHandleAuth(() => Api.setLearningReviewStatus(el.dataset.id, el.dataset.learningaction));
        if (!r) return;
        await loadLearningReview();
      } catch (err) {
        showTransientError(err.message);
      }
    });
  });
}

function renderLearningDetail(episodeId) {
  const e = learningEpisodes.find(x => x.episode_id === episodeId);
  const pane = document.getElementById("learningDetailPane");
  if (!e) { pane.classList.remove("open"); pane.innerHTML = ""; return; }
  pane.classList.add("open");
  pane.innerHTML = learningDetailHtml(e);
  wireLearningDetailActions(e);
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

  const missingConfig = t => !t.dispatch_group || !t.closing_group;

  const grid = document.getElementById("techCardGrid");
  grid.innerHTML = technicians.map(t => `
    <div class="tech-card">
      <div class="tech-card-head">
        ${techAvatarHtml(t.name)}
        <div class="tech-card-name-wrap">
          <div class="tech-card-name">${esc(t.name)}</div>
          <span class="tech-card-status ${t.active ? "is-active" : "is-inactive"}">${t.active ? "Active" : "Inactive"}</span>
        </div>
      </div>
      <div class="tech-card-row"><span class="tech-card-label">Service areas</span>${(t.areas || []).map(esc).join(", ") || "—"}</div>
      ${missingConfig(t) ? `<div class="chip chip-warning">Missing WhatsApp destination</div>` : ""}
      <button type="button" class="btn btn-secondary btn-block tech-card-toggle" data-toggledetail="${t.id}">View details</button>
      <div class="tech-card-detail" id="techDetail-${t.id}" style="display:none;">${techEditFormHtml(t)}</div>
    </div>`).join("");
  grid.querySelectorAll("[data-toggledetail]").forEach(btn => {
    btn.addEventListener("click", () => {
      const detail = document.getElementById(`techDetail-${btn.dataset.toggledetail}`);
      detail.style.display = detail.style.display === "none" ? "block" : "none";
    });
  });
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

/* =========================================================== PERFORMANCE */
let perfDashboard = null;
let perfTechniciansShowAll = false;

function fmtKpi(kpi, suffix) {
  if (!kpi) return "—";
  if (kpi.data_quality === "insufficient_data") return "Insufficient data";
  if (kpi.data_quality === "needs_business_definition") return "Blocked -- needs definition";
  if (kpi.value === null || kpi.value === undefined) return "—";
  return `${kpi.value}${suffix || ""}`;
}

function kpiBadgeClass(kpi) {
  if (!kpi) return "chip-waiting";
  if (kpi.data_quality === "insufficient_data") return "chip-waiting";
  if (kpi.data_quality === "needs_business_definition") return "chip-warning";
  if (kpi.data_quality === "zero") return "chip-waiting";
  return "chip-settled";
}

async function loadPerformance() {
  const period = document.getElementById("perfPeriodSelect").value;
  const data = await callOrHandleAuth(() => Api.getPerformance({ period }));
  if (!data) return;
  perfDashboard = data;
  renderPerformanceScreen();
}
document.getElementById("perfPeriodSelect").addEventListener("change", () => loadPerformance());

function renderPerformanceScreen() {
  const dash = perfDashboard;
  if (!dash) return;

  const grid = document.getElementById("perfSummaryGrid");
  const s = dash.summary;
  const tiles = [
    ["New jobs", fmtKpi(s.jobs_received)],
    ["Waiting now", fmtKpi(s.jobs_waiting)],
    ["Active jobs", fmtKpi(s.active_jobs)],
    ["Closings due", fmtKpi(s.closings_due)],
    ["Median dispatch", fmtKpi(s.median_dispatch_minutes, " min")],
    ["Completion rate", fmtKpi(s.completion_rate, "%")],
  ];
  grid.innerHTML = tiles.map(([label, value]) => `
    <div class="perf-tile">
      <div class="perf-tile-value">${esc(value)}</div>
      <div class="perf-tile-label">${esc(label)}</div>
    </div>`).join("");

  const attnSection = document.getElementById("perfAttentionSection");
  const attnList = document.getElementById("perfAttentionList");
  if (dash.attention_now.length) {
    attnSection.style.display = "block";
    attnList.innerHTML = dash.attention_now.slice(0, 5).map(item => `
      <div class="mobile-row" data-jobid="${item.job_id}">
        <div class="row-top">${sourceChip(item.source)} <strong>${esc(item.ticket || "No ref")}</strong></div>
        <div class="row-bottom">${esc(item.type === "not_dispatched_within_target" ? "Not dispatched within target" : "Active job stale beyond target")}</div>
        <button type="button" class="btn btn-primary btn-block card-primary-btn" data-perfaction="${esc(item.primary_action)}" data-jobid="${item.job_id}">${esc(item.primary_action)}</button>
      </div>`).join("");
    attnList.querySelectorAll("[data-perfaction]").forEach(btn => {
      btn.addEventListener("click", () => {
        // Deep-link: jump to the screen most likely to hold this job.
        setScreen("active");
      });
    });
  } else {
    attnSection.style.display = "none";
  }

  document.getElementById("perfOperationsCard").innerHTML = perfOperationsHtml(dash.operations);
  document.getElementById("perfTechniciansCard").innerHTML = perfTechniciansHtml(dash.technicians);
  wirePerfTechniciansShowMore();
  document.getElementById("perfSourcesCard").innerHTML = perfSourcesHtml(dash.sources);
  document.getElementById("perfAiQualityCard").innerHTML = perfAiQualityHtml(dash.ai_quality);
  document.getElementById("perfDataQualityCard").innerHTML = perfDataQualityHtml(dash);
  document.getElementById("perfBossViewCard").innerHTML = perfBossViewHtml(dash);
}

function perfKpiRow(label, kpi, suffix) {
  return `<div class="perf-kpi-row">
    <span class="perf-kpi-label">${esc(label)}</span>
    <span class="chip ${kpiBadgeClass(kpi)}">${esc(fmtKpi(kpi, suffix))}</span>
  </div>`;
}

function perfOperationsHtml(ops) {
  return [
    perfKpiRow("Jobs received", ops.jobs_received),
    perfKpiRow("Jobs dispatched", ops.jobs_dispatched),
    perfKpiRow("Dispatch rate", ops.dispatch_rate, "%"),
    perfKpiRow("Median receive-to-review", ops.median_receive_to_review_minutes, " min"),
    perfKpiRow("Median receive-to-dispatch", ops.median_receive_to_dispatch_minutes, " min"),
    perfKpiRow("P90 receive-to-dispatch", ops.p90_receive_to_dispatch_minutes, " min"),
    perfKpiRow("Completed jobs", ops.completed_jobs),
    perfKpiRow("Cancellation rate", ops.cancellation_rate, "%"),
    perfKpiRow("Pass / manual-handle rate", ops.pass_manual_handle_rate, "%"),
    perfKpiRow("Closing completion rate", ops.closing_completion_rate, "%"),
  ].join("");
}

function perfTechniciansHtml(technicians) {
  if (!technicians.length) return `<div class="empty-state">No technicians configured.</div>`;
  const toShow = perfTechniciansShowAll ? technicians : technicians.slice(0, 5);
  const rows = toShow.map(t => `
    <div class="perf-kpi-row">
      <span class="perf-kpi-label">${esc(t.name)} <span class="age">(${t.jobs_assigned} assigned)</span></span>
      <span class="chip ${kpiBadgeClass(t.completion_rate)}">${esc(fmtKpi(t.completion_rate, "%"))}</span>
    </div>`).join("");
  const hiddenCount = technicians.length - toShow.length;
  const showMore = hiddenCount > 0
    ? `<button type="button" class="btn btn-secondary btn-block" id="perfTechShowMoreBtn" style="margin-top:6px;">View all ${technicians.length}</button>`
    : (perfTechniciansShowAll && technicians.length > 5 ? `<button type="button" class="btn btn-secondary btn-block" id="perfTechShowMoreBtn" style="margin-top:6px;">Show fewer</button>` : "");
  return rows + showMore;
}

function wirePerfTechniciansShowMore() {
  const btn = document.getElementById("perfTechShowMoreBtn");
  if (btn) btn.addEventListener("click", () => { perfTechniciansShowAll = !perfTechniciansShowAll; renderPerformanceScreen(); });
}

function perfSourcesHtml(sources) {
  const names = Object.keys(sources);
  if (!names.length) return `<div class="empty-state">No sources recorded.</div>`;
  return names.map(name => {
    const s = sources[name];
    return `
      <div class="perf-source-block">
        <div class="perf-kpi-label" style="font-weight:700; margin-bottom:4px;">${sourceChip(name)}</div>
        ${perfKpiRow("Received", s.jobs_received)}
        ${perfKpiRow("Dispatch rate", s.dispatch_rate, "%")}
        ${perfKpiRow("Completion rate", s.completion_rate, "%")}
        ${perfKpiRow("Cancellation rate", s.cancellation_rate, "%")}
        ${perfKpiRow("Median dispatch", s.median_dispatch_minutes, " min")}
      </div>`;
  }).join("");
}

function perfAiQualityHtml(ai) {
  return [
    perfKpiRow("Human-review-required rate", ai.human_review_required_rate, "%"),
    perfKpiRow("Suggestion acceptance rate", ai.acceptance_rate, "%"),
    perfKpiRow("Correction rate", ai.correction_rate, "%"),
    perfKpiRow("Excluded / sensitive / incomplete", ai.excluded_sensitive_incomplete_count),
    perfKpiRow("Unrecognized-message rate", ai.unrecognized_message_rate, "%"),
    perfKpiRow("Duplicate-message rate", ai.duplicate_message_rate, "%"),
  ].join("");
}

function perfDataQualityHtml(dash) {
  const allKpis = [
    ...Object.values(dash.summary), ...Object.values(dash.operations),
    dash.ai_quality.human_review_required_rate, dash.ai_quality.acceptance_rate, dash.ai_quality.correction_rate,
  ];
  const insufficient = allKpis.filter(k => k && k.data_quality === "insufficient_data").length;
  return `
    <div class="detail-meta">${esc(dash.data_quality.real_data_volume_note)}</div>
    <div class="perf-kpi-row"><span class="perf-kpi-label">KPIs showing "Insufficient data" this period</span><span class="chip ${insufficient ? "chip-waiting" : "chip-settled"}">${insufficient}</span></div>`;
}

function perfBossViewHtml(dash) {
  const bestSource = Object.entries(dash.sources)
    .map(([name, s]) => [name, s.completion_rate && s.completion_rate.value])
    .filter(([, v]) => v !== null && v !== undefined)
    .sort((a, b) => b[1] - a[1])[0];
  const topTech = dash.technicians.filter(t => t.completion_rate && t.completion_rate.value !== null).sort((a, b) => b.completion_rate.value - a.completion_rate.value)[0];
  return `
    <div class="detail-meta">Jobs in: <strong>${esc(fmtKpi(dash.summary.jobs_received))}</strong> · Dispatched in a median of <strong>${esc(fmtKpi(dash.summary.median_dispatch_minutes, " min"))}</strong></div>
    <div class="detail-meta">Still waiting: <strong>${esc(fmtKpi(dash.summary.jobs_waiting))}</strong> · Active: <strong>${esc(fmtKpi(dash.summary.active_jobs))}</strong> · Completed: <strong>${esc(fmtKpi(dash.operations.completed_jobs))}</strong></div>
    <div class="detail-meta">Best-performing source: <strong>${bestSource ? esc(bestSource[0]) + " (" + bestSource[1] + "%)" : "Insufficient data"}</strong></div>
    <div class="detail-meta">Most reliable technician: <strong>${topTech ? esc(topTech.name) + " (" + topTech.completion_rate.value + "%)" : "Insufficient data"}</strong></div>
    <div class="detail-meta">Jobs stuck (attention now): <strong>${dash.attention_now.length}</strong></div>
    <div class="detail-meta">AI correction rate: <strong>${esc(fmtKpi(dash.ai_quality.correction_rate, "%"))}</strong></div>
    <div class="detail-meta">Data trustworthy enough? <strong>${dash.summary.jobs_received.data_quality === "insufficient_data" ? "Not yet -- too little history" : "Reasonable for the KPIs marked Good above"}</strong></div>`;
}

/* ============================================================== SEARCH ===
   One global search box (desktop: persistent in the header; mobile: a
   search icon opens it as a full-width overlay). Read-only -- selecting
   a result only navigates and pre-selects, it never mutates anything.
   No PII (phone/address/customer name) is requested or rendered here;
   /api/search itself never returns those fields. */
const searchState = { results: [], activeIndex: -1, debounceTimer: null };

function searchResultRowHtml(r, i) {
  const label = `#${esc(r.ticket || "No ref")}`;
  const age = r.entity_type === "INCOMING_MESSAGE"
    ? (r.age != null ? ageText(r.age) : "")
    : (r.age ? ageText(minutesSince(r.age)) : "");
  return `
    <div class="header-search-row ${i === searchState.activeIndex ? "active" : ""}" data-idx="${i}">
      <span class="hs-ticket">${label}</span>
      <span class="hs-meta">${esc(r.source || "")}${r.technician ? " &middot; " + esc(r.technician) : ""} &middot; ${esc(r.next_action || r.stage || "")}</span>
      <span class="hs-age">${esc(age)}</span>
    </div>`;
}

function renderSearchResults() {
  const wrap = document.querySelector(".header-search-wrap.mobile-open") || document.querySelector(".header-search-wrap");
  const panel = wrap.querySelector(".header-search-results");
  const input = document.getElementById("globalSearchInput");
  const hasQuery = input.value.trim().length >= 2;
  if (!searchState.results.length) {
    panel.innerHTML = hasQuery ? `<div class="header-search-empty">No matches.</div>` : "";
    panel.style.display = hasQuery ? "block" : "none";
    return;
  }
  panel.innerHTML = searchState.results.map((r, i) => searchResultRowHtml(r, i)).join("");
  panel.style.display = "block";
  panel.querySelectorAll("[data-idx]").forEach(row => {
    row.addEventListener("click", () => openSearchResult(parseInt(row.dataset.idx, 10)));
  });
}

async function runSearch(q) {
  if (q.trim().length < 2) { searchState.results = []; renderSearchResults(); return; }
  const data = await callOrHandleAuth(() => Api.search(q.trim()));
  if (!data) return;
  searchState.results = data.results;
  searchState.activeIndex = -1;
  renderSearchResults();
}

// Shared by search-result-open, Action Center's "Open" buttons, and
// "Next actionable job" -- one normalized item shape
// (entity_type/entity_id/screen, exactly what operator_api.py's
// _normalize_action_center_item / search results already produce),
// one navigation implementation. setScreen is called with
// {skipRefresh: true} and this function does its own explicit, awaited
// load-then-select instead -- letting setScreen ALSO fire its normal
// unawaited refreshAll() would start a second, uncontrolled concurrent
// fetch of the same screen's data racing this one. This was a real,
// reproduced bug (not just theoretical): even after this function's
// own load was already a plain `await` rather than a `setTimeout(0)`,
// the selection was still intermittently overwritten by setScreen's own
// fire-and-forget load resolving afterward and reapplying its
// default-first-item fallback. "history" has no per-job selection API
// yet, so that case just gets a normal refresh for whatever it shows.
async function navigateToItem(item) {
  setScreen(item.screen, { skipRefresh: true });
  if (item.entity_type === "JOB") {
    if (item.screen === "active") { await loadActive(); selectActive(item.entity_id); }
    else if (item.screen === "closings") { await loadClosings(); selectClosing(item.entity_id); }
    else { await refreshAll(); }
  } else if (item.entity_type === "INCOMING_MESSAGE") {
    await loadJobs(); selectJob(item.entity_id);
  } else {
    await refreshAll();
  }
}

async function openSearchResult(idx) {
  const r = searchState.results[idx];
  if (!r) return;
  closeSearchResults();
  closeMobileSearch();
  await navigateToItem(r);
}

async function goToNextActionableJob(excludeEntityId) {
  const data = await callOrHandleAuth(() => Api.getNextActionableJob(excludeEntityId));
  if (!data) return;
  if (!data.found) { showTransientMessage("Nothing actionable right now -- you're caught up.", "info"); return; }
  await navigateToItem(data.item);
}
document.getElementById("nextActionableJobBtn").addEventListener("click", () => goToNextActionableJob());
// Delegated: [data-nextactionable] can be rendered inside any dynamically
// re-rendered detail pane (e.g. the "waiting on technician" block in
// activeDetailHtml), so a direct addEventListener at render time would
// need re-wiring on every rerender -- one delegated listener covers all
// of them without that.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-nextactionable]");
  if (btn) goToNextActionableJob(btn.dataset.exclude);
});

function closeSearchResults() {
  document.querySelectorAll(".header-search-results").forEach(p => { p.style.display = "none"; p.innerHTML = ""; });
  searchState.results = [];
  searchState.activeIndex = -1;
}

function openMobileSearch() {
  document.querySelector(".header-search-wrap").classList.add("mobile-open");
  document.getElementById("globalSearchInput").focus();
}
function closeMobileSearch() {
  document.querySelector(".header-search-wrap").classList.remove("mobile-open");
}

document.getElementById("globalSearchInput").addEventListener("input", (e) => {
  clearTimeout(searchState.debounceTimer);
  const q = e.target.value;
  searchState.debounceTimer = setTimeout(() => runSearch(q), 200);
});
document.getElementById("globalSearchInput").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); searchState.activeIndex = Math.min(searchState.results.length - 1, searchState.activeIndex + 1); renderSearchResults(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); searchState.activeIndex = Math.max(0, searchState.activeIndex - 1); renderSearchResults(); }
  else if (e.key === "Enter") {
    if (searchState.activeIndex >= 0) openSearchResult(searchState.activeIndex);
    else if (searchState.results.length === 1) openSearchResult(0);
  } else if (e.key === "Escape") {
    closeSearchResults(); e.target.blur(); closeMobileSearch();
  }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".header-search-wrap")) closeSearchResults();
});
document.getElementById("mobileSearchBtn").addEventListener("click", () => {
  const wrap = document.querySelector(".header-search-wrap");
  if (wrap.classList.contains("mobile-open")) closeMobileSearch(); else openMobileSearch();
});

/* ============================================================ KEYBOARD ===
   Safe, low-risk shortcuts only -- a desktop enhancement, never required
   (mobile has no keyboard and must stay fully usable without any of
   this). Explicitly NOT bound to a key: mark sent, assign technician,
   accept/decline technician, closing confirmation, complete, or any
   other external/irreversible action -- those stay click-only, always.
   "/" focuses the search box; it never mutates anything, so it's safe
   even though search itself was added after this comment was first
   written for a version with no search feature. */
function _currentListAndSelection() {
  if (state.screen === "jobs") return { list: newJobsLive, key: "message_id", selected: state.jobsSelected, select: selectJob };
  if (state.screen === "active") return { list: activeJobs, key: "id", selected: state.activeSelected, select: selectActive };
  if (state.screen === "closings") return { list: closingsEntries, key: "id", selected: state.closingsSelected, select: selectClosing };
  return null;
}

function _moveSelection(direction) {
  const ctx = _currentListAndSelection();
  if (!ctx || !ctx.list.length) return;
  const idx = ctx.list.findIndex(item => item[ctx.key] === ctx.selected);
  const nextIdx = idx === -1 ? 0 : Math.min(ctx.list.length - 1, Math.max(0, idx + direction));
  ctx.select(ctx.list[nextIdx][ctx.key]);
}

// "C" only fires when exactly one copy-a-prepared-message action exists
// on the currently open detail/sheet -- ambiguous (zero or multiple
// candidates) means it does nothing, per the brief's explicit rule that
// this shortcut must never guess which of several things to copy.
function _copyShortcutTarget() {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document;
  if (!root) return null;
  const candidates = root.querySelectorAll(
    '[data-jobaction="reveal_k"], [data-jobaction="copy_only"], [data-jobaction="prepare"], ' +
    '[data-activeaction="copy_reply"], [data-activeaction="copy_status_reply"], [data-closingaction="prepare"]'
  );
  return candidates.length === 1 ? candidates[0] : null;
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeSheet(); hideConfirmModal(); closeSearchResults(); closeMobileSearch(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return; // never fight the browser's/OS's own shortcuts
  const tag = (document.activeElement && document.activeElement.tagName) || "";
  const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (document.activeElement && document.activeElement.isContentEditable);
  if (e.key === "/" && !typing) {
    e.preventDefault();
    if (isMobile()) openMobileSearch(); else document.getElementById("globalSearchInput").focus();
    return;
  }
  if (typing) return;
  if (e.key === "j" || e.key === "J") { e.preventDefault(); _moveSelection(1); }
  else if (e.key === "k" || e.key === "K") { e.preventDefault(); _moveSelection(-1); }
  else if (e.key === "Enter" && isMobile()) {
    // Desktop already shows the detail pane the instant J/K changes the
    // selection -- Enter only does something extra on mobile, where
    // selection alone doesn't open the bottom sheet.
    const ctx = _currentListAndSelection();
    if (ctx && ctx.selected != null) ctx.select(ctx.selected);
  } else if (e.key === "c" || e.key === "C") {
    const target = _copyShortcutTarget();
    if (target) { e.preventDefault(); target.click(); }
  }
});

window.addEventListener("resize", () => { if (!isMobile()) closeSheet(); });
