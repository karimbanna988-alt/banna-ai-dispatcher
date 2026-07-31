/* Local-terminal-only diagnostic formatting for unexpected thrown
 * values. This module's output must NEVER be sent over HTTP -- see
 * healthServer.js's /groups handler, which logs formatDiagnosticError()'s
 * result to this process's own console only, and always returns a fixed,
 * sanitized error code to the actual HTTP caller regardless of what this
 * produces.
 *
 * Exists because whatsapp-web.js/Puppeteer failures do not always throw
 * a normal Error -- sometimes a bare string, a minified object, or
 * something with a misleading one-character .message. This formatter
 * safely inspects whatever was actually thrown without assuming it is
 * well-formed, and without ever throwing itself. */

"use strict";

const MAX_KEYS = 20;
const MAX_ARRAY_ITEMS = 20;
const MAX_DEPTH = 3;

function safeInspect(value, seen = new WeakSet(), depth = 0) {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  const t = typeof value;
  if (t !== "object" && t !== "function") {
    if (t === "string") return JSON.stringify(value.slice(0, 200));
    return String(value);
  }
  if (t === "function") return `[function ${value.name || "anonymous"}]`;
  if (seen.has(value)) return "[circular]";
  if (depth >= MAX_DEPTH) return "[max depth reached]";
  seen.add(value);
  try {
    if (Array.isArray(value)) {
      const items = value.slice(0, MAX_ARRAY_ITEMS).map((v) => safeInspect(v, seen, depth + 1));
      const suffix = value.length > MAX_ARRAY_ITEMS ? `, ...(${value.length - MAX_ARRAY_ITEMS} more)` : "";
      return `[${items.join(", ")}${suffix}]`;
    }
    let keys;
    try {
      keys = Object.keys(value);
    } catch (e) {
      return "[unreadable object]";
    }
    const shown = keys.slice(0, MAX_KEYS).map((k) => {
      let v;
      try {
        v = value[k];
      } catch (e) {
        v = "[unreadable]";
      }
      return `${JSON.stringify(k)}: ${safeInspect(v, seen, depth + 1)}`;
    });
    const suffix = keys.length > MAX_KEYS ? `, ...(${keys.length - MAX_KEYS} more keys)` : "";
    return `{${shown.join(", ")}${suffix}}`;
  } catch (e) {
    return "[unserializable]";
  } finally {
    seen.delete(value);
  }
}

/**
 * Builds a local-only diagnostic record for any thrown value, safe to
 * pass to console.error/JSON.stringify -- never safe to return over HTTP.
 * @param {*} value - whatever was caught (may not be an Error at all).
 * @returns {object}
 */
function formatDiagnosticError(value) {
  const info = {
    typeofValue: typeof value,
    constructorName: null,
    isError: value instanceof Error,
    errorName: null,
    errorMessage: null,
    errorStack: null,
    ownPropertyNames: [],
    safeRepresentation: null,
  };

  try {
    if (value !== null && value !== undefined && value.constructor) {
      info.constructorName = value.constructor.name || null;
    }
  } catch (e) {
    info.constructorName = "[unreadable]";
  }

  if (info.isError) {
    info.errorName = value.name || null;
    info.errorMessage = typeof value.message === "string" ? value.message : safeInspect(value.message);
    info.errorStack = typeof value.stack === "string" ? value.stack : null;
  }

  try {
    info.ownPropertyNames = value !== null && typeof value === "object" ? Object.getOwnPropertyNames(value) : [];
  } catch (e) {
    info.ownPropertyNames = ["[unreadable]"];
  }

  if (!info.isError) {
    info.safeRepresentation = safeInspect(value);
  }

  return info;
}

module.exports = { formatDiagnosticError, safeInspect };
