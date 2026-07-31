/* Local-only group allowlist -- the bridge processes messages only from
 * WhatsApp group IDs explicitly listed here. Everything else (personal
 * DMs, unrelated groups, family chats, statuses) is dropped before it
 * ever reaches the dedupe/extraction/adapter pipeline, let alone the
 * Python side. Plain JSON file, not a database -- this is a short,
 * human-editable list a single dispatcher maintains via the local
 * admin page (127.0.0.1 only). */

"use strict";

const fs = require("node:fs");
const path = require("node:path");

function ensureDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function loadAllowlist(allowlistFile) {
  try {
    const raw = fs.readFileSync(allowlistFile, "utf8");
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed.groups)) return { groups: [] };
    return parsed;
  } catch (err) {
    if (err.code === "ENOENT") return { groups: [] };
    throw err;
  }
}

function saveAllowlist(allowlistFile, data) {
  ensureDir(allowlistFile);
  const payload = JSON.stringify({ groups: data.groups || [] }, null, 2);
  fs.writeFileSync(allowlistFile, payload, "utf8");
}

function isApproved(allowlistFile, groupId) {
  const data = loadAllowlist(allowlistFile);
  return data.groups.some((g) => g.id === groupId);
}

// Real WhatsApp group JIDs are "<digits>@g.us" or the older
// "<digits>-<digits>@g.us" -- anything else (a typo, a pasted display
// name, a non-group JID) is rejected here rather than silently
// accepted into the allowlist.
const GROUP_JID_PATTERN = /^\d+(-\d+)?@g\.us$/;

function isValidGroupId(id) {
  return typeof id === "string" && GROUP_JID_PATTERN.test(id);
}

function addGroup(allowlistFile, group) {
  if (!group || !group.id) throw new Error("group.id is required");
  if (!isValidGroupId(group.id)) throw new Error("group.id is not a valid WhatsApp group JID");
  const data = loadAllowlist(allowlistFile);
  if (data.groups.some((g) => g.id === group.id)) return data;
  data.groups.push({ id: group.id, name: group.name || group.id, addedAt: new Date().toISOString() });
  saveAllowlist(allowlistFile, data);
  return data;
}

function removeGroup(allowlistFile, groupId) {
  const data = loadAllowlist(allowlistFile);
  data.groups = data.groups.filter((g) => g.id !== groupId);
  saveAllowlist(allowlistFile, data);
  return data;
}

module.exports = { loadAllowlist, saveAllowlist, isApproved, addGroup, removeGroup, isValidGroupId };
