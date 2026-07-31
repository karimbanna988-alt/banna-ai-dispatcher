/* LUXOR Operator UI V2 -- prototype interaction logic.
   Vanilla JS only. All state lives in memory (JOBS array from data.js);
   nothing here calls a network API or persists anything. Every "action"
   button only mutates the in-memory fabricated data and re-renders. */

const state = {
  screen: "jobs",
  jobsFilter: "needs_action", // needs_action | waiting | all
  jobsSelected: null,
  activeSelected: null,
  closingsSelected: null,
  reportTech: "all",
};

const LIVE_WINDOW_MIN = 60;

function isMobile() { return window.innerWidth < 900; }
function esc(s) { return (s ?? "").toString().replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

function sourceChip(source) {
  const cls = source === "AGN" ? "chip-source-agn" : source === "Lucky" ? "chip-source-lucky" : "chip-source-mainleads";
  return `<span class="chip ${cls}">${esc(source)}</span>`;
}

/* ============================================================ NAVIGATION */
function setScreen(name) {
  state.screen = name;
  document.querySelectorAll(".screen").forEach(el => el.classList.toggle("active", el.id === "screen-" + name));
  document.querySelectorAll(".desktop-sidebar .nav-item, .mobile-bottom-nav .nav-item").forEach(el => {
    el.classList.toggle("active", el.dataset.screen === name);
  });
  closeSheet();
  renderAll();
}

document.querySelectorAll(".desktop-sidebar .nav-item, .mobile-bottom-nav .nav-item").forEach(el => {
  el.addEventListener("click", () => setScreen(el.dataset.screen));
});

document.getElementById("refreshBtn").addEventListener("click", () => renderAll());
document.getElementById("menuBtn").addEventListener("click", () => {
  alert("Prototype menu -- Log out would appear here. Not wired in this prototype.");
});

/* ============================================================ BOTTOM SHEET */
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

/* ================================================================= JOBS */
function actionSteps(job) {
  return job.kRequired
    ? ["Send K", "Mark K sent", "Copy technician job", "Open WhatsApp", "Confirm sent"]
    : ["Copy technician job", "Open WhatsApp", "Confirm sent"];
}

function jobBucket(job) {
  // "Waiting" = the dispatcher already tapped something (K sent, or
  // mid-dispatch) but hasn't confirmed yet -- come back and finish it.
  if (job.actionState === 0) return "needs_action";
  const steps = actionSteps(job);
  if (job.actionState >= steps.length) return "done";
  return "waiting";
}

function jobsInWindow() {
  return JOBS.filter(j => j.screen === "jobs" && j.receivedMinAgo <= LIVE_WINDOW_MIN && jobBucket(j) !== "done");
}
function jobsOlder() {
  return JOBS.filter(j => j.screen === "jobs" && j.receivedMinAgo > LIVE_WINDOW_MIN && jobBucket(j) !== "done");
}

function renderJobsFilterPills() {
  const live = jobsInWindow();
  const counts = {
    needs_action: live.filter(j => jobBucket(j) === "needs_action").length,
    waiting: live.filter(j => jobBucket(j) === "waiting").length,
    all: live.length,
  };
  const labels = { needs_action: "Needs action", waiting: "Waiting", all: "All" };
  document.getElementById("jobsFilterPills").innerHTML = Object.keys(labels).map(key =>
    `<button class="pill-btn ${state.jobsFilter === key ? "active" : ""}" data-filter="${key}">${labels[key]} (${counts[key]})</button>`
  ).join("");
  document.querySelectorAll("#jobsFilterPills .pill-btn").forEach(btn => {
    btn.addEventListener("click", () => { state.jobsFilter = btn.dataset.filter; renderJobsScreen(); });
  });
}

function ageLabel(min) {
  return min + "m";
}

function jobStatusChip(job) {
  const bucket = jobBucket(job);
  if (bucket === "needs_action") return `<span class="chip chip-needs-action">Needs action</span>`;
  if (bucket === "waiting") return `<span class="chip chip-waiting">In progress</span>`;
  return `<span class="chip chip-settled">Dispatched</span>`;
}

function renderJobsScreen() {
  renderJobsFilterPills();
  const window_ = jobsInWindow();
  const filtered = state.jobsFilter === "all" ? window_ : window_.filter(j => jobBucket(j) === state.jobsFilter);
  filtered.sort((a, b) => a.receivedMinAgo - b.receivedMinAgo);
  document.getElementById("jobsCount").textContent = `(${filtered.length})`;

  // Desktop table
  const tbody = document.getElementById("jobsTableBody");
  tbody.innerHTML = filtered.length ? filtered.map(j => `
    <tr data-job="${j.id}" class="${state.jobsSelected === j.id ? "selected" : ""}">
      <td>${ageLabel(j.receivedMinAgo)}</td>
      <td><strong>${esc(j.ticket)}</strong></td>
      <td>${sourceChip(j.source)}</td>
      <td>${esc(j.service)}</td>
      <td>${esc(j.city)}, ${esc(j.state)}</td>
      <td>${esc(j.technician || "Unassigned")}</td>
      <td>${jobStatusChip(j)}</td>
    </tr>`).join("") : `<tr><td colspan="7"><div class="empty-state">No new live jobs need attention.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-job]").forEach(tr => tr.addEventListener("click", () => selectJob(tr.dataset.job)));

  // Mobile compact rows
  const mobileList = document.getElementById("jobsMobileList");
  mobileList.innerHTML = filtered.length ? filtered.map(j => `
    <div class="mobile-row ${jobBucket(j)}" data-job="${j.id}">
      <div class="row-top">${sourceChip(j.source)} <strong>${esc(j.ticket)}</strong> <span class="age">${ageLabel(j.receivedMinAgo)}</span></div>
      <div class="row-bottom">${esc(j.service)} · ${esc(j.city)}, ${esc(j.state)}</div>
    </div>`).join("") : `<div class="empty-state">No new live jobs need attention.</div>`;
  mobileList.querySelectorAll(".mobile-row").forEach(row => row.addEventListener("click", () => selectJob(row.dataset.job)));

  // Older backlog (collapsed)
  const older = jobsOlder();
  const olderLabel = document.getElementById("olderJobsLabel");
  const olderList = document.getElementById("olderJobsList");
  if (older.length) {
    olderLabel.style.display = "block";
    olderLabel.innerHTML = `<details class="disclosure"><summary>Older unresolved jobs (${older.length})</summary></details>`;
    const details = olderLabel.querySelector("details");
    older.sort((a, b) => a.receivedMinAgo - b.receivedMinAgo);
    older.forEach(j => {
      const row = document.createElement("div");
      row.className = "mobile-row " + jobBucket(j);
      row.innerHTML = `<div class="row-top">${sourceChip(j.source)} <strong>${esc(j.ticket)}</strong> <span class="age">${ageLabel(j.receivedMinAgo)}</span></div>
        <div class="row-bottom">${esc(j.service)} · ${esc(j.city)}, ${esc(j.state)}</div>`;
      row.addEventListener("click", () => selectJob(j.id));
      details.appendChild(row);
    });
    olderList.innerHTML = "";
  } else {
    olderLabel.style.display = "none";
    olderList.innerHTML = "";
  }

  // Auto-select newest live job if nothing valid selected
  if (!filtered.find(j => j.id === state.jobsSelected)) {
    state.jobsSelected = filtered.length ? filtered[0].id : null;
  }
  if (state.jobsSelected) renderJobDetail(state.jobsSelected);
  else { document.getElementById("jobsDetailPane").classList.remove("open"); document.getElementById("jobsDetailPane").innerHTML = ""; }
}

function selectJob(id) {
  state.jobsSelected = id;
  renderJobsScreen();
  if (isMobile()) {
    const job = JOBS.find(j => j.id === id);
    openSheet(jobDetailHtml(job));
    wireJobDetailActions(job);
  }
}

function jobDetailHtml(job) {
  const bucket = jobBucket(job);
  let warn = "";
  if (!job.whatsappGroup) warn = `<div class="detail-warning">WhatsApp group not configured.</div>`;
  return `
    <div class="detail-header">${sourceChip(job.source)}<span class="detail-ticket">${esc(job.ticket)}</span><span class="detail-age">${ageLabel(job.receivedMinAgo)}</span></div>
    <div class="detail-meta">${esc(job.service)} · ${esc(job.city)}, ${esc(job.state)}</div>
    <div class="detail-meta">Technician: <strong>${esc(job.technician || "Unassigned")}</strong> · WhatsApp: <strong>${esc(job.whatsappGroup || "Not configured")}</strong></div>
    ${warn}
    <div class="action-area" id="jobActionArea-${job.id}">${renderJobActions(job)}</div>
    <div class="action-secondary-row"><button class="btn btn-secondary btn-block" data-action="pass" data-job="${job.id}">Pass</button></div>
    <div class="detail-section">
      <details class="disclosure"><summary>Job details</summary>
        <div class="detail-meta">Customer: ${esc(job.customer)}</div>
        <div class="detail-meta">Phone: ${esc(job.phone)}</div>
        <div class="detail-meta">Address: ${esc(job.address)}</div>
        ${job.vehicle ? `<div class="detail-meta">Vehicle: ${esc(job.vehicle)}</div>` : ""}
        ${job.notes ? `<div class="detail-meta">Notes: ${esc(job.notes)}</div>` : ""}
        <div class="subsection-label">Original message</div>
        <pre class="msg-block">${esc(job.originalMessage)}</pre>
        <details class="disclosure"><summary>Preview technician message</summary>
          <pre class="msg-block">${esc(job.technicianMessage)}</pre>
        </details>
      </details>
    </div>`;
}

function renderJobActions(job) {
  const steps = actionSteps(job);
  if (job.actionState >= steps.length) {
    return `<div class="checked-line">&#10003; Dispatched</div>`;
  }
  let html = "";
  const kSteps = job.kRequired ? 2 : 0;

  if (job.kRequired) {
    if (job.actionState >= kSteps) {
      html += `<div class="checked-line">&#10003; K sent</div>`;
    } else {
      if (job.actionState === 1) html += `<code class="msg-block">K</code>`;
      html += `<button class="btn btn-primary btn-block" data-action="advance" data-job="${job.id}">${steps[job.actionState]}</button>`;
      return html; // action area 2 stays locked until K confirmed
    }
  }

  if (!job.whatsappGroup) {
    html += `<div class="chip chip-warning">Group not configured</div>`;
    html += `<button class="btn btn-secondary btn-block" data-action="copy-only" data-job="${job.id}">Copy message</button>`;
    return html;
  }

  if (job.actionState === kSteps + 1) html += `<pre class="msg-block">${esc(job.technicianMessage)}</pre>`;
  html += `<button class="btn btn-primary btn-block" data-action="advance" data-job="${job.id}">${steps[job.actionState]}</button>`;
  return html;
}

function wireJobDetailActions(job) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("jobsDetailPane");
  const advanceBtn = root.querySelector(`[data-action="advance"][data-job="${job.id}"]`);
  if (advanceBtn) advanceBtn.addEventListener("click", () => { job.actionState += 1; refreshJobDetail(job); });
  const copyBtn = root.querySelector(`[data-action="copy-only"][data-job="${job.id}"]`);
  if (copyBtn) copyBtn.addEventListener("click", () => { copyBtn.textContent = "Copied"; });
  const passBtn = root.querySelector(`[data-action="pass"][data-job="${job.id}"]`);
  if (passBtn) passBtn.addEventListener("click", () => {
    JOBS.splice(JOBS.indexOf(job), 1);
    closeSheet();
    renderJobsScreen();
  });
}

function refreshJobDetail(job) {
  renderJobsScreen();
  if (isMobile()) {
    openSheet(jobDetailHtml(job));
    wireJobDetailActions(job);
  }
}

function renderJobDetail(id) {
  const job = JOBS.find(j => j.id === id);
  const pane = document.getElementById("jobsDetailPane");
  if (!job) { pane.classList.remove("open"); pane.innerHTML = ""; return; }
  pane.classList.add("open");
  pane.innerHTML = jobDetailHtml(job);
  wireJobDetailActions(job);
}

/* =============================================================== ACTIVE */
function renderActiveScreen() {
  const jobs = JOBS.filter(j => j.screen === "active");
  document.getElementById("activeCount").textContent = `(${jobs.length})`;
  const tbody = document.getElementById("activeTableBody");
  tbody.innerHTML = jobs.length ? jobs.map(j => `
    <tr data-job="${j.id}" class="${state.activeSelected === j.id ? "selected" : ""}">
      <td>${sourceChip(j.source)} <strong>${esc(j.ticket)}</strong></td>
      <td>${esc(j.technician)}</td>
      <td>${esc(j.city)}, ${esc(j.state)}</td>
      <td>${esc(j.lastUpdate)}</td>
      <td>${j.waitingMin}m</td>
      <td>${esc(j.nextAction)}</td>
    </tr>`).join("") : `<tr><td colspan="6"><div class="empty-state">No active jobs.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-job]").forEach(tr => tr.addEventListener("click", () => selectActive(tr.dataset.job)));

  const mobileList = document.getElementById("activeMobileList");
  mobileList.innerHTML = jobs.length ? jobs.map(j => `
    <div class="mobile-row ${j.waiting ? "waiting" : "needs-action"}" data-job="${j.id}">
      <div class="row-top">${sourceChip(j.source)} <strong>${esc(j.ticket)}</strong> <span class="age">${j.waitingMin}m</span></div>
      <div class="row-bottom">${esc(j.technician)} · ${esc(j.nextAction)}</div>
    </div>`).join("") : `<div class="empty-state">No active jobs.</div>`;
  mobileList.querySelectorAll(".mobile-row").forEach(row => row.addEventListener("click", () => selectActive(row.dataset.job)));

  if (!jobs.find(j => j.id === state.activeSelected)) state.activeSelected = jobs.length ? jobs[0].id : null;
  if (state.activeSelected) renderActiveDetail(state.activeSelected);
  else { document.getElementById("activeDetailPane").classList.remove("open"); document.getElementById("activeDetailPane").innerHTML = ""; }
}

function activeDetailHtml(job) {
  const doneUpdate = job.actionState2 >= 1;
  const doneReply = job.actionState2 >= 2;
  return `
    <div class="detail-header">${sourceChip(job.source)}<span class="detail-ticket">${esc(job.ticket)}</span></div>
    <div class="detail-meta">Next: <strong>${esc(job.nextAction)}</strong></div>
    <div class="detail-meta">${esc(job.city)}, ${esc(job.state)} · Technician: <strong>${esc(job.technician)}</strong></div>
    <div class="detail-meta">Last update: ${esc(job.lastUpdate)}</div>
    <div class="detail-section">
      ${doneUpdate ? `<div class="checked-line">&#10003; Technician update saved</div>` : `<button class="btn btn-primary btn-block" data-active-action="update" data-job="${job.id}">Add technician update</button>`}
      ${doneUpdate ? (doneReply ? `<div class="checked-line">&#10003; Company reply sent</div>` : `<button class="btn btn-primary btn-block" data-active-action="reply" data-job="${job.id}">Prepare company reply</button>`) : ""}
      ${doneReply ? `<button class="btn btn-primary btn-block" data-active-action="close" data-job="${job.id}">Record closing</button>` : ""}
    </div>
    <div class="detail-section">
      <details class="disclosure"><summary>Job details</summary>
        <div class="detail-meta">Customer: ${esc(job.customer)}</div>
        <div class="detail-meta">Phone: ${esc(job.phone)}</div>
        <div class="detail-meta">Address: ${esc(job.address)}</div>
        ${job.quoteType ? `<div class="detail-meta">Quote: ${esc(job.quoteType)} ${esc(job.quoteAmount)}</div>` : ""}
      </details>
    </div>`;
}

function selectActive(id) {
  state.activeSelected = id;
  renderActiveScreen();
  if (isMobile()) {
    const job = JOBS.find(j => j.id === id);
    openSheet(activeDetailHtml(job));
    wireActiveDetailActions(job);
  }
}
function wireActiveDetailActions(job) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("activeDetailPane");
  root.querySelectorAll("[data-active-action]").forEach(btn => {
    btn.addEventListener("click", () => {
      job.actionState2 = (job.actionState2 || 0) + 1;
      renderActiveScreen();
      if (isMobile()) { openSheet(activeDetailHtml(job)); wireActiveDetailActions(job); }
    });
  });
}
function renderActiveDetail(id) {
  const job = JOBS.find(j => j.id === id);
  const pane = document.getElementById("activeDetailPane");
  if (!job) { pane.classList.remove("open"); pane.innerHTML = ""; return; }
  pane.classList.add("open");
  pane.innerHTML = activeDetailHtml(job);
  wireActiveDetailActions(job);
}

/* ============================================================= CLOSINGS */
const CLOSING_STEP_LABELS = {
  needs_review: "Review amount",
  ready_to_send: "Copy closing",
  sent_awaiting_complete: "Confirm sent",
  settled: "Complete",
};
function closingStatusChip(status) {
  if (status === "settled") return `<span class="chip chip-settled">Settled</span>`;
  if (status === "needs_review") return `<span class="chip chip-needs-action">Needs review</span>`;
  return `<span class="chip chip-waiting">In progress</span>`;
}

function renderClosingsScreen() {
  const jobs = JOBS.filter(j => j.screen === "closing" && j.closingStatus !== "settled");
  document.getElementById("closingsCount").textContent = `(${jobs.length})`;
  const tbody = document.getElementById("closingsTableBody");
  tbody.innerHTML = jobs.length ? jobs.map(j => `
    <tr data-job="${j.id}" class="${state.closingsSelected === j.id ? "selected" : ""}">
      <td>${sourceChip(j.source)} <strong>${esc(j.ticket)}</strong></td>
      <td>${esc(j.technician)}</td>
      <td>${esc(j.company)}</td>
      <td>$${esc(j.quoteAmount)}</td>
      <td>$${esc(j.finalAmount)}</td>
      <td>${esc(j.paymentMethod)}</td>
      <td>${closingStatusChip(j.closingStatus)}</td>
    </tr>`).join("") : `<tr><td colspan="7"><div class="empty-state">No closings are waiting.</div></td></tr>`;
  tbody.querySelectorAll("tr[data-job]").forEach(tr => tr.addEventListener("click", () => selectClosing(tr.dataset.job)));

  const mobileList = document.getElementById("closingsMobileList");
  mobileList.innerHTML = jobs.length ? jobs.map(j => `
    <div class="mobile-row" data-job="${j.id}">
      <div class="row-top">${sourceChip(j.source)} <strong>${esc(j.ticket)}</strong></div>
      <div class="row-bottom">${esc(j.technician)} · Quoted $${esc(j.quoteAmount)} · Final $${esc(j.finalAmount)}</div>
    </div>`).join("") : `<div class="empty-state">No closings are waiting.</div>`;
  mobileList.querySelectorAll(".mobile-row").forEach(row => row.addEventListener("click", () => selectClosing(row.dataset.job)));

  if (!jobs.find(j => j.id === state.closingsSelected)) state.closingsSelected = jobs.length ? jobs[0].id : null;
  if (state.closingsSelected) renderClosingDetail(state.closingsSelected);
  else { document.getElementById("closingsDetailPane").classList.remove("open"); document.getElementById("closingsDetailPane").innerHTML = ""; }
}

function closingDetailHtml(job) {
  const step = job.step;
  let action = "";
  if (step === 1) action = `<button class="btn btn-primary btn-block" data-closing-action="advance" data-job="${job.id}">Review amount &amp; continue</button>`;
  if (step === 2) action = `<pre class="msg-block">Closed\n$${esc(job.finalAmount)} ${esc(job.paymentMethod)}</pre><button class="btn btn-primary btn-block" data-closing-action="advance" data-job="${job.id}">Copy closing</button>`;
  if (step === 3) action = `<button class="btn btn-primary btn-block" data-closing-action="advance" data-job="${job.id}">Confirm sent</button>`;
  if (step >= 4) action = `<div class="checked-line">&#10003; Sent</div><button class="btn btn-primary btn-block" data-closing-action="advance" data-job="${job.id}">Complete job</button>`;
  if (step >= 5) action = `<div class="checked-line">&#10003; Job settled</div>`;
  return `
    <div class="detail-header">${sourceChip(job.source)}<span class="detail-ticket">${esc(job.ticket)}</span></div>
    <div class="detail-meta">${esc(job.company)} · Technician: <strong>${esc(job.technician)}</strong></div>
    <div class="detail-meta">Quoted: <strong>$${esc(job.quoteAmount)}</strong> · Final: <strong>$${esc(job.finalAmount)}</strong> · ${esc(job.paymentMethod)}</div>
    <div class="detail-section">${action}</div>`;
}
function selectClosing(id) {
  state.closingsSelected = id;
  renderClosingsScreen();
  if (isMobile()) {
    const job = JOBS.find(j => j.id === id);
    openSheet(closingDetailHtml(job));
    wireClosingDetailActions(job);
  }
}
function wireClosingDetailActions(job) {
  const root = isMobile() ? document.getElementById("bottomSheetContent") : document.getElementById("closingsDetailPane");
  const btn = root.querySelector(`[data-closing-action="advance"][data-job="${job.id}"]`);
  if (btn) btn.addEventListener("click", () => {
    job.step += 1;
    if (job.step >= 5) job.closingStatus = "settled";
    else if (job.step === 4) job.closingStatus = "sent_awaiting_complete";
    else if (job.step === 3) job.closingStatus = "ready_to_send";
    renderClosingsScreen();
    if (isMobile() && job.closingStatus !== "settled") { openSheet(closingDetailHtml(job)); wireClosingDetailActions(job); }
    else closeSheet();
  });
}
function renderClosingDetail(id) {
  const job = JOBS.find(j => j.id === id);
  const pane = document.getElementById("closingsDetailPane");
  if (!job) { pane.classList.remove("open"); pane.innerHTML = ""; return; }
  pane.classList.add("open");
  pane.innerHTML = closingDetailHtml(job);
  wireClosingDetailActions(job);
}

/* =============================================================== REPORTS */
function renderReportsScreen() {
  const techSelect = document.getElementById("reportTechFilter");
  if (techSelect.options.length <= 1) {
    TECHNICIANS.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.name; opt.textContent = t.name;
      techSelect.appendChild(opt);
    });
    techSelect.addEventListener("change", () => { state.reportTech = techSelect.value; renderReportsScreen(); });
  }
  const rows = REPORTS; // fabricated -- technician filter is cosmetic in this prototype
  const tbody = document.getElementById("reportTableBody");
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><strong>${esc(r.job)}</strong></td><td>${esc(r.company)}</td><td>${esc(r.date)}</td><td>${esc(r.area)}</td>
      <td>${esc(r.pct)}</td><td>$${r.total}</td><td>$${r.parts}</td><td>$${r.companyParts}</td>
      <td>$${r.cash}</td><td>$${r.card}</td><td>$${r.check}</td>
      <td>$${r.fees.toFixed(2)}</td><td>$${r.profit.toFixed(2)}</td><td>$${r.balance}</td>
    </tr>`).join("");
  const totals = rows.reduce((acc, r) => {
    ["total","parts","companyParts","cash","card","check","fees","profit","balance"].forEach(k => acc[k] = (acc[k]||0) + r[k]);
    return acc;
  }, {});
  document.getElementById("reportTotalsRow").innerHTML =
    `<td colspan="5">Totals</td><td>$${totals.total.toFixed(2)}</td><td>$${totals.parts.toFixed(2)}</td><td>$${totals.companyParts.toFixed(2)}</td>
     <td>$${totals.cash.toFixed(2)}</td><td>$${totals.card.toFixed(2)}</td><td>$${totals.check.toFixed(2)}</td>
     <td>$${totals.fees.toFixed(2)}</td><td>$${totals.profit.toFixed(2)}</td><td>$${totals.balance.toFixed(2)}</td>`;

  const mobileList = document.getElementById("reportMobileList");
  mobileList.innerHTML = rows.map((r, i) => `
    <div class="report-row-mobile" data-idx="${i}">
      <div class="rrm-top"><span>${esc(r.job)} · ${esc(r.company)}</span><span>$${r.total}</span></div>
      <div class="rrm-bottom">${esc(r.date)} · ${esc(r.area)}</div>
    </div>`).join("");
  mobileList.querySelectorAll(".report-row-mobile").forEach(row => {
    row.addEventListener("click", () => openSheet(reportDetailHtml(rows[row.dataset.idx])));
  });
}
function reportDetailHtml(r) {
  return `
    <div class="detail-header"><span class="detail-ticket">${esc(r.job)}</span></div>
    <div class="detail-meta">${esc(r.company)} · ${esc(r.date)} · ${esc(r.area)}</div>
    <div class="detail-section">
      <div class="detail-meta">Percentage: <strong>${esc(r.pct)}</strong></div>
      <div class="detail-meta">Total: <strong>$${r.total}</strong></div>
      <div class="detail-meta">Parts: $${r.parts} · Company parts: $${r.companyParts}</div>
      <div class="detail-meta">Cash: $${r.cash} · Card: $${r.card} · Check: $${r.check}</div>
      <div class="detail-meta">Fees: $${r.fees.toFixed(2)}</div>
      <div class="detail-meta">Profit: <strong>$${r.profit.toFixed(2)}</strong></div>
      <div class="detail-meta">Balance: <strong>$${r.balance.toFixed(2)}</strong></div>
    </div>`;
}
document.getElementById("reportPrintBtn").addEventListener("click", () => alert("Prototype only -- Print is not functional."));
document.getElementById("reportExportBtn").addEventListener("click", () => alert("Prototype only -- Export is not functional."));

/* =========================================================== TECHNICIANS */
function renderTechniciansScreen() {
  document.getElementById("techCount").textContent = `(${TECHNICIANS.length})`;
  const tbody = document.getElementById("techTableBody");
  tbody.innerHTML = TECHNICIANS.map(t => `
    <tr>
      <td><strong>${esc(t.name)}</strong></td>
      <td>${esc(t.area)}</td>
      <td>${t.skills.map(esc).join(", ")}</td>
      <td>${t.active ? "Active" : "Inactive"}</td>
      <td>${esc(t.dispatchGroup || "Not configured")} ${!t.dispatchGroup ? '<span class="chip chip-warning">!</span>' : ""}</td>
      <td>${esc(t.closingGroup || "Not configured")} ${!t.closingGroup ? '<span class="chip chip-warning">!</span>' : ""}</td>
    </tr>`).join("");

  const mobileList = document.getElementById("techMobileList");
  mobileList.innerHTML = TECHNICIANS.map(t => `
    <div class="mobile-row">
      <div class="row-top"><strong>${esc(t.name)}</strong><span class="age">${t.active ? "Active" : "Inactive"}</span></div>
      <div class="row-bottom">${esc(t.area)} · ${t.skills.map(esc).join(", ")}</div>
      ${(!t.dispatchGroup || !t.closingGroup) ? `<div class="chip chip-warning" style="margin-top:4px;">Missing destination</div>` : ""}
    </div>`).join("");
}

/* =================================================================== ALL */
function renderAll() {
  renderJobsScreen();
  renderActiveScreen();
  renderClosingsScreen();
  renderReportsScreen();
  renderTechniciansScreen();
}

window.addEventListener("resize", () => {
  if (!isMobile()) closeSheet();
});

setScreen("jobs");
