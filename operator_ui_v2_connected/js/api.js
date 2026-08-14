/* Thin fetch wrapper around operator_api.py's JSON routes. Same-origin
   only -- this file never talks to any other host, never touches
   WhatsApp, never stores a credential. The session itself lives in an
   HttpOnly cookie the browser manages automatically (this file never
   reads or writes it); the CSRF token is kept in an ordinary JS
   variable (not localStorage/sessionStorage) and is lost -- by design
   -- on page reload or logout, at which point the login screen asks
   for the password again. */

const Api = {
  _csrfToken: null,

  async _call(method, path, body) {
    const opts = { method, headers: {}, credentials: "same-origin" };
    if (method === "POST" && this._csrfToken) {
      opts.headers["X-CSRF-Token"] = this._csrfToken;
    }
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (res.status === 401) {
      const err = new Error((data && data.error) || "Not authenticated.");
      err.authRequired = true;
      throw err;
    }
    if (!res.ok) {
      const message = (data && data.error) || `Request failed (${res.status})`;
      throw new Error(message);
    }
    return data;
  },

  async login(password) {
    const result = await this._call("POST", "/api/login", { password });
    this._csrfToken = result.csrf_token;
    return result;
  },
  async logout() {
    try { await this._call("POST", "/api/logout", {}); } finally { this._csrfToken = null; }
  },
  async checkSession() {
    const result = await this._call("GET", "/api/me");
    // A reload with a still-valid session cookie skips login() entirely
    // -- without also picking up the token here, every mutating action
    // would 403 ("Missing or invalid CSRF token") until an explicit
    // logout/login, even though the session itself is fine.
    if (result && result.csrf_token) this._csrfToken = result.csrf_token;
    return result;
  },
  getDemoStatus() { return this._call("GET", "/api/demo_status"); },

  getCommandCenter(chartPeriod) {
    const qs = chartPeriod ? `?chart_period=${encodeURIComponent(chartPeriod)}` : "";
    return this._call("GET", `/api/command_center${qs}`);
  },

  getInboxReview(params) {
    const q = new URLSearchParams(Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== ""));
    const qs = q.toString();
    return this._call("GET", `/api/inbox_review${qs ? "?" + qs : ""}`);
  },

  getActionCenter() { return this._call("GET", "/api/action_center"); },
  getNextActionableJob(excludeEntityId) {
    const qs = excludeEntityId != null ? `?exclude_entity_id=${encodeURIComponent(excludeEntityId)}` : "";
    return this._call("GET", `/api/next_actionable_job${qs}`);
  },
  search(q) { return this._call("GET", `/api/search?q=${encodeURIComponent(q)}`); },

  getNewJobs() { return this._call("GET", "/api/new_jobs"); },
  sync() { return this._call("POST", "/api/sync", {}); },
  getActive() { return this._call("GET", "/api/active"); },
  getTechnicians() { return this._call("GET", "/api/technicians"); },
  getJob(id) { return this._call("GET", `/api/job/${id}`); },

  acknowledge(messageId) { return this._call("POST", `/api/new_jobs/${encodeURIComponent(messageId)}/acknowledge`, {}); },
  prepareDispatch(messageId) { return this._call("POST", `/api/new_jobs/${encodeURIComponent(messageId)}/prepare`, {}); },
  confirmDispatch(messageId) { return this._call("POST", `/api/new_jobs/${encodeURIComponent(messageId)}/confirm`, {}); },
  handleManually(messageId) { return this._call("POST", `/api/new_jobs/${encodeURIComponent(messageId)}/handle_manually`, {}); },
  passJob(messageId, reason) { return this._call("POST", `/api/new_jobs/${encodeURIComponent(messageId)}/pass`, { reason }); },

  updateDestinations(techId, dispatchGroup, closingGroup) {
    return this._call("POST", `/api/technicians/${techId}/destinations`, { dispatch_group: dispatchGroup, closing_group: closingGroup });
  },

  commercialPreview(jobId, rawText) {
    return this._call("POST", `/api/job/${jobId}/commercial/preview`, { raw_technician_text: rawText });
  },
  commercialSave(jobId, rawText, correctedFields) {
    return this._call("POST", `/api/job/${jobId}/commercial/save`, { raw_technician_text: rawText, corrected_fields: correctedFields });
  },
  confirmReplySent(jobId, sentChannel) {
    return this._call("POST", `/api/job/${jobId}/commercial/confirm_reply_sent`, { sent_channel: sentChannel });
  },

  recordTechnicianUpdate(jobId, updateType, rawUpdate, etaMinutes) {
    return this._call("POST", `/api/job/${jobId}/technician_update`, {
      update_type: updateType, raw_update: rawUpdate, eta_minutes: etaMinutes || undefined,
    });
  },
  prepareCompanyUpdate(jobId, updateType, etaMinutes, manualText) {
    return this._call("POST", `/api/job/${jobId}/company_update/prepare`, {
      update_type: updateType, eta_minutes: etaMinutes || undefined, manual_text: manualText || undefined,
    });
  },
  confirmCompanyUpdateSent(jobId, preparedText) {
    return this._call("POST", `/api/job/${jobId}/company_update/confirm_sent`, { prepared_text: preparedText });
  },

  confirmTechnicianAccepted(jobId, messageId) {
    return this._call("POST", `/api/job/${jobId}/technician_acceptance/confirm_accepted`, { message_id: messageId });
  },
  confirmTechnicianDeclined(jobId, messageId, reason) {
    return this._call("POST", `/api/job/${jobId}/technician_acceptance/confirm_declined`, { message_id: messageId, reason });
  },

  getClosings() { return this._call("GET", "/api/closings"); },
  recordClosing(jobId, fields) { return this._call("POST", `/api/closings/${jobId}/record`, fields); },
  prepareClosing(jobId) { return this._call("POST", `/api/closings/${jobId}/prepare`, {}); },
  confirmClosingSent(jobId) { return this._call("POST", `/api/closings/${jobId}/confirm_sent`, {}); },
  completeClosing(jobId) { return this._call("POST", `/api/closings/${jobId}/complete`, {}); },

  getHistory(filters) {
    const q = new URLSearchParams(Object.entries(filters || {}).filter(([, v]) => v));
    const qs = q.toString();
    return this._call("GET", `/api/history${qs ? "?" + qs : ""}`);
  },
  getHistoryDetail(id) { return this._call("GET", `/api/history/${id}`); },

  getReports() { return this._call("GET", "/api/reports"); },

  getPerformance(filters) {
    const q = new URLSearchParams(Object.entries(filters || {}).filter(([, v]) => v));
    const qs = q.toString();
    return this._call("GET", `/api/performance${qs ? "?" + qs : ""}`);
  },

  getLearningReview() { return this._call("GET", "/api/learning_review"); },
  setLearningReviewStatus(episodeId, reviewStatus) {
    return this._call("POST", `/api/learning_review/${encodeURIComponent(episodeId)}/status`, { review_status: reviewStatus });
  },

  getWhatsappPending() { return this._call("GET", "/api/whatsapp_pending"); },
  getWhatsappBridgeStatus() { return this._call("GET", "/api/whatsapp_bridge_status"); },
  attachWhatsappObservation(messageId, jobId) {
    return this._call("POST", `/api/whatsapp_pending/${encodeURIComponent(messageId)}/attach`, { job_id: jobId });
  },
  ignoreWhatsappObservation(messageId) {
    return this._call("POST", `/api/whatsapp_pending/${encodeURIComponent(messageId)}/ignore`, {});
  },
  getWhatsappShadowFeed() { return this._call("GET", "/api/whatsapp_shadow_feed"); },
  getDemoConsole() { return this._call("GET", "/api/demo_console"); },
  getLiveFeed() { return this._call("GET", "/api/live_feed"); },
  getLiveHealth() { return this._call("GET", "/api/live_health"); },
  getApprenticeSuggestions() { return this._call("GET", "/api/apprentice_suggestions"); },
  promoteWhatsappToJob(messageId) {
    return this._call("POST", `/api/whatsapp_pending/${encodeURIComponent(messageId)}/promote_to_job`, {});
  },
};
