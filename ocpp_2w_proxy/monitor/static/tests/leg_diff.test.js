import assert from "node:assert/strict";
import { test } from "node:test";

import { legDiff } from "../components/leg_diff.js";
import { loadFixture } from "./fixture_loader.js";

const detail = () => loadFixture("exchange_detail");
const call = (peer) => detail().frames.find((frame) => frame.peer === peer && frame.msg_type === "call" && frame.dir === "out");

test("a rewritten call lists each changed path with the request and the sent value", () => {
  assert.match(String(legDiff(call("homeassistant"), detail().payload)), /<tr class="diff--changed"><th scope="row" class="mono">transactionId<\/th><td>changed<\/td><td class="mono diff__from">10216<\/td><td class="mono diff__to">1791822333<\/td>/);
  assert.match(String(legDiff(call("stats"), detail().payload)), /<tr class="diff--removed"><th scope="row" class="mono">transactionId<\/th>/);
});

test("an unchanged call is the same as the request", () => {
  assert.match(String(legDiff(call("primary"), detail().payload)), /Same as the request/);
  const equal = { ...call("homeassistant"), payload: detail().payload };
  assert.match(String(legDiff(equal, detail().payload)), /Same as the request/);
});
