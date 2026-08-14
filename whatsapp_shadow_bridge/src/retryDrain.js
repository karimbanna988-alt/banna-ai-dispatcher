/* Periodic redelivery of anything left in the durable retry queue
 * (deliveryQueue.js) -- observations captured from a real incoming
 * message that could not be delivered to the Python adapter yet. Never
 * touches WhatsApp itself; only re-POSTs an already-captured
 * observation. An observation that keeps failing simply stays queued
 * for the next pass -- never discarded, never retried by re-fetching
 * anything from WhatsApp (this bridge never imports chat history). */

"use strict";

const deliveryQueue = require("./deliveryQueue");
const dedupeStore = require("./dedupeStore");
const { describeNonTextMessage } = require("./messageHandler");

/* An observation queued BEFORE caption handling existed can carry an
 * empty text. The adapter correctly rejects that ("observation.text is
 * required"), so such an entry could never drain: it failed on every
 * pass, forever, and the real capture behind it -- a genuine message
 * from a real group -- was never persisted. Found live with 25 entries
 * stuck in exactly this state.
 *
 * Applying the SAME placeholder the capture path already uses recovers
 * the observation (group, sender, timestamp, message id are all still
 * there) instead of discarding it. Nothing is invented: the queued
 * record carries no media metadata, so it resolves to the generic
 * "no text content" label rather than guessing a media type. */
function deliverable(observation) {
  if (observation.text) return observation;
  return { ...observation, text: describeNonTextMessage({}) };
}

/** One drain pass. Returns { attempted, delivered } for observability/tests. */
async function drainOnce(config, deps = {}) {
  const postObservation = deps.postObservation || require("./adapterClient").postObservation;
  const log = deps.log || (() => {});
  const queued = deliveryQueue.load(config.retryQueueFile);
  let delivered = 0;
  for (const observation of queued) {
    try {
      await postObservation(config, deliverable(observation));
      dedupeStore.markSeen(config.dedupeFile, observation.messageId);
      deliveryQueue.dequeue(config.retryQueueFile, observation.messageId);
      delivered += 1;
      log(`retry-delivered previously-queued observation ${observation.messageId}`);
    } catch (err) {
      log(`retry delivery still failing for ${observation.messageId}: ${err.message}`);
    }
  }
  return { attempted: queued.length, delivered };
}

/** Starts the background interval. Returns the timer handle so a caller
 * (or a test) can clearInterval() it. unref()'d so it never keeps the
 * Node process alive on its own. */
function startRetryDrainLoop(config, deps = {}) {
  const log = deps.log || (() => {});
  drainOnce(config, deps).catch((err) => log(`initial retry drain failed: ${err.message}`));
  const timer = setInterval(() => {
    drainOnce(config, deps).catch((err) => log(`retry drain failed: ${err.message}`));
  }, config.retryDrainIntervalMs);
  if (timer.unref) timer.unref();
  return timer;
}

module.exports = { drainOnce, startRetryDrainLoop };
