import assert from "node:assert/strict";
import { test } from "node:test";

import { LiveStream } from "../live.js";
import { loadFixture } from "./fixture_loader.js";

class FakeSource {
  constructor(url) {
    this.url = url;
    this.handlers = {};
    this.readyState = 0;
    this.closed = false;
  }

  addEventListener(name, handler) {
    this.handlers[name] = handler;
  }

  fire(name, data) {
    this.handlers[name]?.({ data });
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }
}

function setup() {
  const sources = [];
  const timers = { pending: [], setTimeout: (fn) => timers.pending.push(fn) || 1, clearTimeout: () => {} };
  const stream = new LiveStream({ createSource: (url) => sources.at(sources.push(new FakeSource(url)) - 1), timers });
  const seen = [];
  stream.subscribe({
    exchange: (summary) => seen.push(["exchange", summary.id]),
    link: (link) => seen.push(["link", link.peer]),
    gap: (gap) => seen.push(["gap", gap.dropped]),
    resync: () => seen.push(["resync"]),
    status: (connected) => seen.push(["status", connected]),
  });
  stream.start();
  return { stream, sources, seen, timers };
}

test("events are parsed and passed on", () => {
  const { sources, seen } = setup();
  sources[0].fire("open");
  sources[0].fire("exchange", JSON.stringify(loadFixture("exchanges").exchanges[0]));
  sources[0].fire("link", JSON.stringify({ charger_id: "99224881", peer: "stats", at: 1, up: false }));
  sources[0].fire("gap", JSON.stringify(loadFixture("links").gaps[0]));
  sources[0].fire("exchange", "not json");
  assert.deepEqual(seen, [["status", true], ["exchange", "4611686018427387905"], ["link", "stats"], ["gap", 37]]);
  assert.equal(sources[0].url, "/api/events");
});

test("reset reconnects and re-fetches once the new stream is open", () => {
  const { sources, seen } = setup();
  sources[0].fire("open");
  sources[0].fire("reset", "");
  assert.equal(sources[0].closed, true);
  assert.equal(sources.length, 2);
  sources[1].fire("open");
  assert.deepEqual(seen, [["status", true], ["status", false], ["status", true], ["resync"]]);
});

test("a dropped stream shows as disconnected and re-fetches after the browser reconnects", () => {
  const { sources, seen } = setup();
  sources[0].fire("open");
  sources[0].fire("error");
  sources[0].fire("open");
  assert.deepEqual(seen, [["status", true], ["status", false], ["status", true], ["resync"]]);
});

test("a stream the browser gave up on is restarted after a delay", () => {
  const { sources, timers } = setup();
  sources[0].readyState = 2;
  sources[0].fire("error");
  assert.equal(sources.length, 1);
  timers.pending[0]();
  assert.equal(sources.length, 2);
});
