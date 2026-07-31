"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { extractTicket } = require("../src/ticketExtractor");

test("extracts PDL-labeled ticket", () => {
  assert.equal(extractTicket("New job\nPDL: IYRT1\nCar lockout"), "IYRT1");
});

test("extracts Ticket-labeled reference", () => {
  assert.equal(extractTicket("Ticket: ARVJ-SC0E\nCustomer info here"), "ARVJ-SC0E");
});

test("returns null (never invents) when nothing matches", () => {
  assert.equal(extractTicket("Hey, running 10 minutes late"), null);
  assert.equal(extractTicket(""), null);
  assert.equal(extractTicket(null), null);
  assert.equal(extractTicket(undefined), null);
});

test("is case-insensitive on the label but uppercases the extracted ticket", () => {
  assert.equal(extractTicket("pdl: abc123"), "ABC123");
});
