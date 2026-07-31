"use strict";

/* Reproduces wwebjs/whatsapp-web.js#201845 -- Client.getChats()/getState()
 * throwing "r: r" once WhatsApp's LID (Linking ID) contact migration left
 * some chats without a matching IndexedDB groupMetadata entry, and
 * separately renamed a message key's `_serialized` property to `$1`.
 *
 * The real fix lives inside whatsapp-web.js's own browser-injected
 * util/Injected/Utils.js (not our src/ wrapper), applied here as a local
 * patch -- see patches/whatsapp-web.js+1.34.7.patch, reapplied automatically
 * by the "postinstall" script on every `npm install` via patch-package.
 *
 * These tests load that real installed file directly in Node by faking the
 * `window`/`window.require` globals it expects at injection time, so the
 * exact failure (and the fix) is exercised without a browser or a paired
 * WhatsApp session. */

const test = require("node:test");
const assert = require("node:assert/strict");

const UTILS_PATH = require.resolve(
  "../node_modules/whatsapp-web.js/src/util/Injected/Utils.js",
);

function loadWWebJS(requireImpl) {
  // Left set (not restored) after returning: the functions this defines
  // read `window.require` lazily, at call time, not at definition time --
  // callers await WWebJS.getChats()/getChatModel() *after* this returns,
  // so `window` must still be in place for that later call to work.
  global.window = { require: requireImpl };
  delete require.cache[UTILS_PATH];
  const { LoadUtils } = require(UTILS_PATH);
  LoadUtils();
  return global.window.WWebJS;
}

function fakeChat({ id, groupMetadataUpdateThrows = false, chatModelThrows = false }) {
  return {
    id: { _serialized: id },
    mute: null,
    groupMetadata: {
      announce: false,
      serialize: () => ({ participants: [] }),
    },
    newsletterMetadata: null,
    msgs: null,
    lastReceivedKey: null,
    formattedTitle: id,
    serialize: () => {
      if (chatModelThrows) throw new Error("r");
      return { id: { _serialized: id }, name: id };
    },
    _groupMetadataUpdateThrows: groupMetadataUpdateThrows,
  };
}

function requireImplFor(chats) {
  return (moduleName) => {
    if (moduleName === "WAWebCollections") {
      return {
        Chat: { getModelsArray: () => chats },
        GroupMetadata: {
          update: async (wid) => {
            const chat = chats.find((c) => c.id._serialized === wid._serialized);
            if (chat && chat._groupMetadataUpdateThrows) {
              // The real failure: an @lid-suffixed chat has no matching
              // IndexedDB entry, so this lookup throws a bare "r".
              throw new Error("r");
            }
          },
        },
        Msg: { get: () => null, getMessagesById: async () => ({ messages: [] }) },
      };
    }
    if (moduleName === "WAWebWidFactory") {
      return { createWid: (id) => ({ _serialized: id }) };
    }
    if (moduleName === "WAWebLidMigrationUtils") {
      return { toPn: (id) => id };
    }
    if (moduleName === "WALinkify") {
      return { findLinks: () => [] };
    }
    throw new Error(`unexpected window.require("${moduleName}") in test`);
  };
}

test("getMsgKeyId falls back from _serialized to $1 (WhatsApp Web's property rename)", () => {
  const WWebJS = loadWWebJS(requireImplFor([]));
  assert.equal(WWebJS.getMsgKeyId({ _serialized: "a" }), "a");
  assert.equal(WWebJS.getMsgKeyId({ $1: "b" }), "b");
  assert.equal(WWebJS.getMsgKeyId({ _serialized: "a", $1: "b" }), "a");
  assert.equal(WWebJS.getMsgKeyId({}), undefined);
  assert.equal(WWebJS.getMsgKeyId(null), undefined);
});

test("getChatModel tolerates groupMetadata.update() rejecting for an LID-based chat (reproduces #201845)", async () => {
  const lidChat = fakeChat({ id: "999@lid", groupMetadataUpdateThrows: true });
  const WWebJS = loadWWebJS(requireImplFor([lidChat]));

  const model = await WWebJS.getChatModel(lidChat);

  assert.equal(model.isGroup, true);
  assert.deepEqual(model.groupMetadata, { participants: [] });
});

test("getChats() skips one chat whose model fails to resolve instead of discarding all chats (reproduces #201845)", async () => {
  const goodChat = fakeChat({ id: "111@g.us" });
  const badChat = fakeChat({ id: "999@lid", chatModelThrows: true });
  const WWebJS = loadWWebJS(requireImplFor([goodChat, badChat]));

  const results = await WWebJS.getChats();

  assert.equal(results.length, 1);
  assert.equal(results[0].id._serialized, "111@g.us");
});

test("getChats() returns every chat when none fail (no regression on the happy path)", async () => {
  const chatA = fakeChat({ id: "111@g.us" });
  const chatB = fakeChat({ id: "222@g.us" });
  const WWebJS = loadWWebJS(requireImplFor([chatA, chatB]));

  const results = await WWebJS.getChats();

  assert.equal(results.length, 2);
});

test("the installed whatsapp-web.js patch is present (fails loudly if npm install ever drops it)", () => {
  const fs = require("node:fs");
  const source = fs.readFileSync(UTILS_PATH, "utf8");
  assert.ok(
    source.includes("window.WWebJS.getMsgKeyId"),
    "expected patches/whatsapp-web.js+1.34.7.patch to be applied -- run `npx patch-package` or `npm install`",
  );
});
