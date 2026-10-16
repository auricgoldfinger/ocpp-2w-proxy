import assert from "node:assert/strict";
import { test } from "node:test";

import { exchangeDetail } from "../components/exchange_detail.js";
import { lanesFor } from "../components/swimlane_model.js";
import { exchangeById, loadFixture } from "./fixture_loader.js";

process.env.TZ = "UTC";

const detail = () => loadFixture("exchange_detail");
const lanes = lanesFor(loadFixture("config"));
const now = 1760534700;

test("payload text is escaped in the drawer", () => {
  const evil = { ...detail(), headline: "</code><script>x</script>" };
  evil.frames[0].payload = { idTag: "<script>y</script>" };
  assert.ok(!String(exchangeDetail({ detail: evil, lanes, now })).includes("<script>"));
});

test("the transaction and the ⇄tx chips link to the session", () => {
  const txHref = "#/exchanges?tx=10216";
  const markup = String(exchangeDetail({ detail: detail(), lanes, now, txHref }));
  assert.match(markup, /<dt>transaction<\/dt><dd><a class="tx-link" href="#\/exchanges\?tx=10216"[^>]*>tx 10216<\/a><\/dd>/);
  assert.equal(markup.match(/<a class="chip chip--link" href="#\/exchanges\?tx=10216"/g).length, 2);
  assert.ok(!String(exchangeDetail({ detail: detail(), lanes, now })).includes("chip--link"), "no link without txHref");
});

test("an incomplete exchange explains the monitor gap", () => {
  const incomplete = { ...exchangeById("4611686018427387914"), frames: [], decisions: [], payload: {} };
  assert.match(String(exchangeDetail({ detail: incomplete, lanes: [], now })), /open during a monitor gap/);
});
