"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { formatDiagnosticError, safeInspect } = require("../src/diagnostics");

test("normal Error objects: captures name/message/stack/constructor", () => {
  const err = new TypeError("boom");
  const diag = formatDiagnosticError(err);
  assert.equal(diag.isError, true);
  assert.equal(diag.constructorName, "TypeError");
  assert.equal(diag.errorName, "TypeError");
  assert.equal(diag.errorMessage, "boom");
  assert.ok(typeof diag.errorStack === "string" && diag.errorStack.length > 0);
});

test("thrown strings: not an Error, safely represented", () => {
  const diag = formatDiagnosticError("r");
  assert.equal(diag.isError, false);
  assert.equal(diag.typeofValue, "string");
  assert.equal(diag.constructorName, "String");
  assert.equal(diag.safeRepresentation, JSON.stringify("r"));
});

test("minified/non-Error objects: own property names and safe representation captured", () => {
  const weird = { a: 1, b: "two", c: [1, 2, 3] };
  const diag = formatDiagnosticError(weird);
  assert.equal(diag.isError, false);
  assert.equal(diag.typeofValue, "object");
  assert.deepEqual(diag.ownPropertyNames.sort(), ["a", "b", "c"]);
  assert.ok(diag.safeRepresentation.includes('"a": 1'));
});

test("circular objects: formatDiagnosticError never throws and marks the cycle", () => {
  const circular = { name: "loop" };
  circular.self = circular;
  assert.doesNotThrow(() => formatDiagnosticError(circular));
  const diag = formatDiagnosticError(circular);
  assert.ok(diag.safeRepresentation.includes("[circular]"));
});

test("null and undefined thrown values do not crash the formatter", () => {
  assert.doesNotThrow(() => formatDiagnosticError(null));
  assert.doesNotThrow(() => formatDiagnosticError(undefined));
  const diagNull = formatDiagnosticError(null);
  assert.equal(diagNull.typeofValue, "object");
  const diagUndef = formatDiagnosticError(undefined);
  assert.equal(diagUndef.typeofValue, "undefined");
});

test("a getter that throws does not crash safeInspect", () => {
  const trap = {};
  Object.defineProperty(trap, "poison", { enumerable: true, get() { throw new Error("nope"); } });
  assert.doesNotThrow(() => safeInspect(trap));
  const out = safeInspect(trap);
  assert.ok(out.includes("[unreadable]"));
});

test("formatDiagnosticError result is always JSON-serializable (safe to log)", () => {
  const cases = [new Error("x"), "string", { a: 1 }, [1, 2, 3], null, undefined, 42, true];
  for (const c of cases) {
    const diag = formatDiagnosticError(c);
    assert.doesNotThrow(() => JSON.stringify(diag));
  }
});
