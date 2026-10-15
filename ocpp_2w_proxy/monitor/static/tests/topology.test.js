import assert from "node:assert/strict";
import { test } from "node:test";

import { topology } from "../components/topology.js";
import { loadFixture } from "./fixture_loader.js";

const config = loadFixture("config");
const charger = loadFixture("links").chargers[0];
const options = { now: 1760536931, hrefFor: (peer) => `#/exchanges?peer=${peer}` };

test("every backend gets a linked node and the primary is labelled", () => {
  const svg = String(topology(config, charger, options));
  assert.match(svg, /<a href="#\/exchanges\?peer=primary"/);
  assert.match(svg, /<a href="#\/exchanges\?peer=stats"/);
  assert.match(svg, /node--primary/);
  assert.match(svg, />PRIMARY · primary</);
});

test("link state is drawn and written", () => {
  const svg = String(topology(config, charger, options));
  assert.match(svg, /edge edge--down/);
  assert.match(svg, />✕ down</);
  assert.match(svg, />1.2k\/h</);
  assert.match(svg, /✕ down 4m01s · ⧗ 14/);
});

test("queue depth sits on the proxy node", () => {
  assert.match(String(topology(config, charger, options)), />⧗ 14 queued</);
});

test("before the links load, every state is unknown", () => {
  const svg = String(topology(config, null, options));
  assert.match(svg, /edge edge--unknown/);
  assert.ok(!svg.includes("edge--up"));
  assert.match(svg, />99224881</);
});

test("backend names are escaped", () => {
  const evil = { ...config, primary: '<script>"' };
  const svg = String(topology(evil, null, options));
  assert.ok(!svg.includes("<script>"));
});
