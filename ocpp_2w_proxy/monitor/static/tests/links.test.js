import assert from "node:assert/strict";
import { test } from "node:test";

import { downtime, downtimeText, linkSegments, timelineBar } from "../components/link_history.js";
import { linksPanel } from "../components/links_panel.js";
import { loadFixture } from "./fixture_loader.js";

process.env.TZ = "UTC";

const now = 1760536931;
const window = { from: now - 86400, now };

function changesOf(peer) {
  return loadFixture("links").history.filter((change) => change.peer === peer);
}

test("segments start in the state opposite to the first change", () => {
  assert.deepEqual(linkSegments(changesOf("stats"), { up: false, ...window }), [
    { from: window.from, to: 1760530000, up: true },
    { from: 1760530000, to: 1760530300, up: false },
    { from: 1760530300, to: 1760536690, up: true },
    { from: 1760536690, to: now, up: false },
  ]);
});

test("without changes the link was in its current state all along", () => {
  assert.deepEqual(linkSegments([], { up: true, ...window }), [{ from: window.from, to: now, up: true }]);
});

test("repeated reports of the same state do not split a segment", () => {
  const changes = [{ at: window.from + 10, up: true }, { at: window.from + 20, up: true }];
  assert.deepEqual(linkSegments(changes, { up: true, ...window }), [
    { from: window.from, to: window.from + 10, up: false },
    { from: window.from + 10, to: now, up: true },
  ]);
});

test("downtime counts outages and their length", () => {
  const segments = linkSegments(changesOf("primary"), { up: true, ...window });
  assert.deepEqual(downtime(segments), { count: 1, seconds: 260 });
  assert.equal(downtimeText(segments), "down 1× · 4m20s in 24 h");
  assert.equal(downtimeText([{ from: 0, to: 1, up: true }]), "no outage in 24 h");
});

test("down periods are drawn lower than up periods", () => {
  const svg = String(timelineBar(linkSegments(changesOf("primary"), { up: true, ...window }), window));
  assert.match(svg, /class="timeline__down"[^>]*y="8"[^>]*height="10"/);
  assert.match(svg, /class="timeline__up"[^>]*y="2"[^>]*height="16"/);
  assert.match(svg, /aria-label="down 1× · 4m20s in 24 h"/);
});

test("the links panel shows state, queue depth, rate and history per peer", () => {
  const markup = String(linksPanel(loadFixture("links"), now));
  assert.match(markup, /<span class="role">PRIMARY<\/span> primary/);
  assert.match(markup, /<th scope="row">stats<\/th><td><span class="state state--down"><span aria-hidden="true">✕<\/span> down for 4m01s<\/span><\/td><td><span class="queue">⧗ 14<\/span><\/td><td>35\/h<\/td>/);
  assert.match(markup, /1.2k\/h/);
  assert.match(markup, /down 2× · 9m01s in 24 h/);
});

test("the links panel reports monitor health", () => {
  const markup = String(linksPanel(loadFixture("links"), now));
  assert.match(markup, /⚠ 37 monitor events dropped, last at 14:02:04/);
  assert.match(markup, /recorder failures/);
  assert.match(markup, /figure__value state--down">1<\/span><span class="figure__label">dropped write batches/);
  assert.match(markup, /2025-10-15 14:02:02–14:02:04<\/td><td>37</);
});

test("without chargers the panel still shows monitor health", () => {
  const empty = { chargers: [], monitor: { fanout_failures: 0, dropped_batches: 0 }, history: [], gaps: [] };
  const markup = String(linksPanel(empty, now));
  assert.match(markup, /No charger has connected yet/);
  assert.match(markup, /No monitor gaps/);
});
