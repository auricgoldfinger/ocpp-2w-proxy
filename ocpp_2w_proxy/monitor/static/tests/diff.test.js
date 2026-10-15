import assert from "node:assert/strict";
import { test } from "node:test";

import { diffPaths } from "../diff.js";

test("equal payloads have no differences", () => {
  assert.deepEqual(diffPaths({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] }), []);
});

test("a rewritten transactionId is a changed path", () => {
  assert.deepEqual(diffPaths({ transactionId: 10216, meterStop: 5 }, { transactionId: 1791822333, meterStop: 5 }), [
    { path: "transactionId", kind: "changed", from: 10216, to: 1791822333 },
  ]);
});

test("removed and added keys are reported with their values", () => {
  assert.deepEqual(diffPaths({ a: 1, chargingProfile: { id: 3 } }, { a: 1, b: 2 }), [
    { path: "chargingProfile", kind: "removed", from: { id: 3 }, to: undefined },
    { path: "b", kind: "added", from: undefined, to: 2 },
  ]);
});

test("nested arrays get indexed paths", () => {
  const from = { meterValue: [{ sampledValue: [{ value: "1" }, { value: "2" }] }] };
  const to = { meterValue: [{ sampledValue: [{ value: "1" }] }] };
  assert.deepEqual(diffPaths(from, to), [
    { path: "meterValue[0].sampledValue[1]", kind: "removed", from: { value: "2" }, to: undefined },
  ]);
});

test("a type change at the root is one change", () => {
  assert.deepEqual(diffPaths({ a: 1 }, [1]), [{ path: "(whole payload)", kind: "changed", from: { a: 1 }, to: [1] }]);
});
