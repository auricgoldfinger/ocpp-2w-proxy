import assert from "node:assert/strict";
import { test } from "node:test";

import { decisionsList, exchangeDetail, legsTable, rawJsonText } from "../components/exchange_detail.js";
import { lanesFor } from "../components/swimlane_model.js";
import { exchangeById, loadFixture } from "./fixture_loader.js";

process.env.TZ = "UTC";

const detail = () => loadFixture("exchange_detail");
const now = 1760534700;

test("the legs table shows wire id, reply, latency, outcome, reason and rewrites", () => {
  const markup = String(legsTable(detail(), now));
  assert.match(markup, /<th scope="row">primary<\/th><td class="mono">c0ffee-9<\/td><td>13:24:24<\/td><td>result<\/td><td>300 ms<\/td>/);
  assert.match(markup, /badge__label">delivered \+4m12s</);
  assert.match(markup, /Queued for primary during an outage; acknowledged locally/);
  assert.match(markup, /<span class="chip" title="10216 → 1791822333">⇄tx<\/span>/);
  assert.match(markup, /<span class="chip" title="10216 → –">⇄tx<\/span>/);
});

test("not-sent peers are listed in the legs table", () => {
  const meterValues = { ...exchangeById("4611686018427387905"), frames: [], decisions: [], payload: {} };
  assert.match(String(legsTable(meterValues, now)), /<th scope="row">stats<\/th>.*not sent.*Not configured to forward MeterValues/);
});

test("a proxy reply is marked in the legs table", () => {
  const refused = exchangeById("4611686018427387907");
  assert.match(String(legsTable(refused, now)), /<td>ERR NotSupported by proxy<\/td>/);
});

test("decisions show code and human text", () => {
  const markup = String(decisionsList(detail().decisions));
  assert.match(markup, /<span class="tag">QUEUED<\/span> Queued for primary during an outage; acknowledged locally/);
  assert.match(String(decisionsList([])), /no decisions/);
});

test("raw JSON is the whole detail, escaped in the drawer", () => {
  const evil = { ...detail(), headline: "</code><script>x</script>" };
  assert.equal(JSON.parse(rawJsonText(evil)).headline, "</code><script>x</script>");
  const markup = String(exchangeDetail({ detail: evil, lanes: lanesFor(loadFixture("config")), now }));
  assert.ok(!markup.includes("<script>"));
});

test("the drawer has every section in the planned order", () => {
  const markup = String(exchangeDetail({ detail: detail(), lanes: lanesFor(loadFixture("config")), session: loadFixture("session"), now }));
  const order = ["Sequence", "Payload", "Legs", "Decisions", "Legs compared with the origin", "Raw JSON"].map((title) => markup.indexOf(`>${title}</h3>`));
  assert.ok(order.every((at) => at > 0));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  assert.match(markup, />7.421 kWh</);
});

test("the transaction and the ⇄tx chips link to the session", () => {
  const txHref = "#/exchanges?tx=10216";
  const markup = String(exchangeDetail({ detail: detail(), lanes: lanesFor(loadFixture("config")), now, txHref }));
  assert.match(markup, /<dt>transaction<\/dt><dd><a class="tx-link" href="#\/exchanges\?tx=10216"[^>]*>tx 10216<\/a><\/dd>/);
  assert.equal(markup.match(/<a class="chip chip--link" href="#\/exchanges\?tx=10216"/g).length, 2);
  assert.match(String(legsTable(detail(), now)), /<span class="chip" title="10216 → 1791822333">⇄tx<\/span>/, "no link without txHref");
});

test("an incomplete exchange explains the monitor gap", () => {
  const incomplete = { ...exchangeById("4611686018427387914"), frames: [], decisions: [], payload: {} };
  assert.match(String(exchangeDetail({ detail: incomplete, lanes: [], now })), /open during a monitor gap/);
});
