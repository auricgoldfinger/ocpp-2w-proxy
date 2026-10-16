import assert from "node:assert/strict";
import { test } from "node:test";

import { ocppArray } from "../components/ocpp_frame.js";
import { loadFixture } from "./fixture_loader.js";

const frame = (fields) => ({ at: 0, peer: "primary", dir: "in", wire_id: "b7-25", payload: {}, same_as_origin: false, error: null, ...fields });

test("a call is [2, id, action, payload]", () => {
  const detail = loadFixture("change_configuration_detail");
  assert.deepEqual(ocppArray(detail.frames[1], detail.action), [2, "b7-25", "ChangeConfiguration", { key: "HeartbeatInterval", value: "600" }]);
});

test("a result is [3, id, payload]", () => {
  assert.deepEqual(ocppArray(frame({ msg_type: "result", payload: { status: "Accepted" } }), "Reset"), [3, "b7-25", { status: "Accepted" }]);
});

test("an error is [4, id, code, description, details]", () => {
  const error = frame({ msg_type: "error", payload: { key: "X" }, error: { code: "NotSupported", description: "No such key" } });
  assert.deepEqual(ocppArray(error, "Reset"), [4, "b7-25", "NotSupported", "No such key", { key: "X" }]);
});

test("an error without code, description or details still has five slots", () => {
  assert.deepEqual(ocppArray(frame({ msg_type: "error", payload: null, error: null }), "Reset"), [4, "b7-25", "", "", {}]);
});
