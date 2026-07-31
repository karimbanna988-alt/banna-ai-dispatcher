/* Thin fetch wrapper around operator_api.py's JSON routes. Same-origin
   only -- this file never talks to any other host, never touches
   WhatsApp, never stores a credential. */

const Api = {
  async _call(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      const message = (data && data.error) || `Request failed (${res.status})`;
      throw new Error(message);
    }
    return data;
  },
  getNewJobs() { return this._call("GET", "/api/new_jobs"); },
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
};
