import assert from "node:assert/strict";
import { test } from "node:test";

import { legDiffs } from "../components/leg_diff.js";
import { loadFixture } from "./fixture_loader.js";

const detail = () => loadFixture("exchange_detail");

test("the diff compares each outgoing call with the origin", () => {
  const markup = String(legDiffs(detail()));
  assert.match(markup, /→ homeassistant.*<tr class="diff--changed"><th scope="row" class="mono">transactionId<\/th><td>changed<\/td><td class="mono diff__from">10216<\/td><td class="mono diff__to">1791822333<\/td>/s);
  assert.match(markup, /→ stats.*<tr class="diff--removed"><th scope="row" class="mono">transactionId<\/th>/s);
  assert.match(markup, /→ primary.*Identical to the origin/s);
});
