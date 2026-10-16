import assert from "node:assert/strict";
import { test } from "node:test";

import { readableView } from "../components/readable_view.js";
import { lanesFor } from "../components/swimlane_model.js";
import { exchangeById, loadFixture } from "./fixture_loader.js";

process.env.TZ = "UTC";

const lanes = lanesFor(loadFixture("config"));
const now = 1760534700;
const view = (detail, extra = {}) => String(readableView({ detail, lanes, now, ...extra }));
const fromFixture = (name) => view(loadFixture(name));
// The markup of the leg block whose heading names `peer`.
const block = (markup, peer) => markup.match(new RegExp(`<article class="leg-block"[^>]*><h4[^>]*><span aria-hidden="true">→</span> ${peer} .*?</article>`, "s"))[0];

test("the story runs request, forwarded legs, then what the requester got back", () => {
  const markup = fromFixture("exchange_detail");
  const order = ["Sequence", "1 · Request from charger", "2 · Forwarded to", "3 · What charger got back"].map((title) => markup.indexOf(`>${title}</h3>`));
  assert.ok(order.every((at) => at > 0), String(order));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  assert.match(markup, /<svg class="sequence"/);
});

test("the request is the action's card without a reply part", () => {
  const markup = view(loadFixture("exchange_detail"), { session: loadFixture("session") });
  assert.match(markup, />7.421 kWh</);
  const request = markup.slice(markup.indexOf("1 · Request"), markup.indexOf("2 · Forwarded"));
  assert.ok(!request.includes("idTagInfo") && !request.includes("Accepted"));
});

test("each MeterValues backend received the reading and acknowledged it", () => {
  const markup = fromFixture("meter_values_fanout_detail");
  assert.match(markup, /<table class="meter-table">/);
  const primary = block(markup, "primary");
  assert.match(primary, /Same as the request/);
  assert.match(primary, /badge__label">Acknowledged<\/span>.*no data in reply/s);
  const homeassistant = block(markup, "homeassistant");
  assert.match(homeassistant, /1d2426a3-7dc9-4f65-be7e-c24595c35403/);
  assert.match(homeassistant, /transactionId<\/th><td>changed<\/td><td class="mono diff__from">10219<\/td><td class="mono diff__to">1791989558<\/td>/);
  assert.match(homeassistant, /<span class="chip" title="10219 → 1791989558">⇄tx<\/span>/);
  assert.match(homeassistant, /badge__label">Acknowledged/);
  const back = markup.slice(markup.indexOf("3 · What charger got back"));
  assert.match(back, /badge__label">Acknowledged/);
});

test("a leg block shows outcome, latency, rewrites and the proxy's decisions", () => {
  const primary = block(fromFixture("exchange_detail"), "primary");
  assert.match(primary, /<span class="mono muted">c0ffee-9<\/span>/);
  assert.match(primary, /badge__label">delivered \+4m12s</);
  assert.match(primary, /answered in 300 ms/);
  assert.match(primary, /<span class="tag">QUEUED<\/span> Queued for primary during an outage; acknowledged locally/);
  assert.match(primary, /badge__label">Accepted</);
});

test("a CallError shows its code and description; a late duplicate shows both replies", () => {
  const markup = fromFixture("call_error_detail");
  const primary = block(markup, "primary");
  assert.match(primary, /ERR FormationViolation.*<span class="reply-error__text">idTag is not a valid token<\/span>/s);
  const homeassistant = block(markup, "homeassistant");
  assert.match(homeassistant, /Reply 1 of 2.*Reply 2 of 2/s);
  assert.match(homeassistant, /<span class="tag" title="Late reply, discarded by the proxy">late<\/span>/);
  assert.match(homeassistant, /<span class="tag" title="Duplicate reply to the same message id">dup<\/span>/);
});

test("a queued leg was sent nothing and has no answer yet", () => {
  const backup = block(fromFixture("call_error_detail"), "backup");
  assert.match(backup, /Nothing was sent to backup/);
  assert.ok(!backup.includes("<h5>Answered</h5>"));
  assert.match(backup, /badge__label">queued</);
});

test("peers that are not configured for the action are listed", () => {
  assert.match(fromFixture("call_error_detail"), /not sent<\/span><\/span> stats: not configured to forward Authorize/);
});

test("a leg answered by the proxy says so", () => {
  const refused = { ...exchangeById("4611686018427387907"), frames: [], decisions: [], payload: {} };
  assert.match(view(refused), /⚙<\/span> Answered by the proxy/);
});

test("an open leg is waiting; a finished one without reply says so", () => {
  const detail = loadFixture("change_configuration_detail");
  detail.frames = detail.frames.slice(0, 2);
  detail.legs[0] = { ...detail.legs[0], outcome: null, reply: null, replied_by: null, replied_at: null };
  assert.match(view(detail), /Waiting for a reply/);
  detail.legs[0] = { ...detail.legs[0], outcome: "failed", reason: "CHARGER_NO_REPLY", reason_text: "The charger did not reply in time" };
  assert.match(view(detail), /⏱<\/span> No reply: The charger did not reply in time/);
  assert.match(view(detail), /Nothing was sent back yet/);
});

test("a backend's call to the charger is told from the backend's side", () => {
  const markup = fromFixture("change_configuration_detail");
  assert.match(markup, />1 · Request from primary</);
  assert.match(block(markup, "charger"), /badge__label">RebootRequired/);
  assert.match(markup, />3 · What primary got back</);
});

test("a proxy-made exchange has no requester to answer", () => {
  const markup = view({ ...loadFixture("change_configuration_detail"), origin: "proxy", proxy_kind: "replay" });
  assert.match(markup, />1 · Request from the proxy</);
  assert.match(markup, />2 · Sent to</);
  assert.ok(!markup.includes("got back"));
});

test("blocks point at their frames for the sequence arrows", () => {
  const markup = fromFixture("exchange_detail");
  for (const index of [0, 1, 2, 6, 7]) assert.match(markup, new RegExp(`data-frame-target="${index}" tabindex="-1"`));
});

test("the readable view holds no JSON", () => {
  const markup = fromFixture("call_error_detail");
  assert.ok(!markup.includes("json-view"));
  assert.ok(!markup.includes("j-key"));
  assert.ok(!/\{"/.test(markup));
});
