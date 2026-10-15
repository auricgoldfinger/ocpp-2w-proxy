import assert from "node:assert/strict";
import { test } from "node:test";

import { ExchangeFeed } from "../exchange_feed.js";
import { loadFixture } from "./fixture_loader.js";

function loadedFeed(options) {
  const feed = new ExchangeFeed(options);
  feed.reset(loadFixture("exchanges"));
  return feed;
}

function summary(id, startedAt) {
  return { ...loadFixture("exchanges").exchanges[0], id, started_at: startedAt };
}

test("reset loads a page with its cursor and heartbeat count", () => {
  const feed = loadedFeed();
  assert.equal(feed.size, 14);
  assert.equal(feed.next, "1760533500.0:1187");
  assert.equal(feed.hidden, 312);
});

test("upsert adds new rows and updates known ones in place", () => {
  const feed = loadedFeed();
  assert.equal(feed.upsert(summary("new", 1760537000)), "added");
  const changed = { ...loadFixture("exchanges").exchanges[1], outcome: "failed" };
  assert.equal(feed.upsert(changed), "updated");
  assert.equal(feed.size, 15);
  assert.equal(feed.exchanges()[0].id, "new");
});

test("held updates count only new rows and apply on release", () => {
  const feed = loadedFeed();
  feed.hold(summary("a", 1760537000));
  feed.hold(summary("a", 1760537000));
  feed.hold({ ...loadFixture("exchanges").exchanges[0], outcome: "failed" });
  assert.equal(feed.pendingNew, 1);
  assert.equal(feed.size, 14);
  feed.release();
  assert.equal(feed.size, 15);
  assert.equal(feed.pendingNew, 0);
  assert.equal(feed.byId.get("4611686018427387905").outcome, "failed");
});

test("appendOlder adds an older page and moves the cursor", () => {
  const feed = loadedFeed();
  feed.appendOlder({ exchanges: [summary("old", 1)], next: null, hidden: { Heartbeat: 312 } });
  assert.equal(feed.exchanges().at(-1).id, "old");
  assert.equal(feed.next, null);
});

test("trim drops the oldest rows and the now useless cursor", () => {
  const feed = loadedFeed({ maxRows: 10 });
  assert.equal(feed.trim(), 4);
  assert.equal(feed.size, 10);
  assert.equal(feed.next, null);
  assert.equal(feed.trimmed, true);
  assert.equal(feed.exchanges()[0].id, "4611686018427387905");
  assert.equal(loadedFeed().trim(), 0);
});

test("gaps interleave by time within the loaded range", () => {
  const feed = loadedFeed();
  feed.setGaps(loadFixture("links").gaps);
  feed.addGap({ charger_id: "99224881", from_at: 100, to_at: 110, dropped: 1 });
  const types = feed.items().map((item) => item.type);
  assert.deepEqual(types.slice(0, 4), ["exchange", "exchange", "gap", "exchange"]);
  assert.equal(types.filter((type) => type === "gap").length, 1, "a gap older than the loaded rows waits for its page");
});
