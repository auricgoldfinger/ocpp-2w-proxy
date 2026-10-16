import assert from "node:assert/strict";
import { test } from "node:test";

import { copyText, frameHeading, jsonView, rawJsonText } from "../components/json_view.js";
import { loadFixture } from "./fixture_loader.js";

const strip = (markup) => markup.replace(/<[^>]+>/g, "").replaceAll("&quot;", '"');

test("each frame heading names number, direction, type, wire id and offset", () => {
  const detail = loadFixture("change_configuration_detail");
  assert.equal(frameHeading(detail, 0), "1 · primary → proxy · CALL · b7-25 · +0 ms");
  assert.equal(frameHeading(detail, 2), "3 · charger → proxy · CALLRESULT · b7-25 · +300 ms");
  assert.equal(frameHeading(loadFixture("call_error_detail"), 3), "4 · primary → proxy · CALLERROR · c0ffee-26 · +210 ms");
});

test("every frame is shown as its OCPP-J array, in frame order", () => {
  const markup = String(jsonView(loadFixture("call_error_detail")));
  const text = strip(markup);
  assert.match(text, /\[\n {2}2,\n {2}"a1d7",\n {2}"Authorize",/);
  assert.match(text, /\[\n {2}4,\n {2}"c0ffee-26",\n {2}"FormationViolation",\n {2}"idTag is not a valid token",\n {2}\{\n {4}"field": "idTag"/);
  assert.match(text, /\[\n {2}3,\n {2}"9e41-26",/);
  assert.equal(markup.match(/<li class="json-frame">/g).length, 7);
  assert.match(markup, /class="j-key"/);
});

test("the JSON view is text only and notes the masking", () => {
  const markup = String(jsonView(loadFixture("exchange_detail")));
  assert.ok(!markup.includes("<svg"));
  assert.ok(!markup.includes("badge"));
  assert.match(markup, /masked by the proxy/);
  assert.match(markup, /<details class="json-record" data-key="json-record">/);
});

test("copy text is one frame, all frames under their headings, or the record", () => {
  const detail = loadFixture("change_configuration_detail");
  assert.deepEqual(JSON.parse(copyText(detail, "2")), [3, "b7-25", { status: "RebootRequired" }]);
  const all = copyText(detail, "all");
  assert.match(all, /^\/\/ 1 · primary → proxy · CALL · b7-25 · \+0 ms\n\[/);
  assert.equal(all.match(/^\/\/ /gm).length, 4);
  assert.equal(copyText(detail, "record"), rawJsonText(detail));
});

test("payload text is escaped", () => {
  const detail = loadFixture("change_configuration_detail");
  detail.frames[0].payload = { key: "</code><script>x</script>" };
  assert.ok(!String(jsonView(detail)).includes("<script>"));
});

test("an exchange without frames says so", () => {
  const detail = { ...loadFixture("change_configuration_detail"), frames: [] };
  const markup = String(jsonView(detail));
  assert.match(markup, /No frames recorded/);
  assert.ok(!markup.includes('data-copy="all"'));
});
