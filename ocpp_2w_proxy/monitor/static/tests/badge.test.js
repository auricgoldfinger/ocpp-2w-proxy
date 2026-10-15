import assert from "node:assert/strict";
import { test } from "node:test";

import { notSentView, outcomeView, UNKNOWN_AFTER_SECONDS } from "../components/badge.js";
import { exchangeById } from "./fixture_loader.js";

function legView(idSuffix, peer, extra = {}) {
  const exchange = exchangeById(`46116860184273879${idSuffix}`);
  const leg = exchange.legs.find((candidate) => candidate.peer === peer);
  const context = { startedAt: exchange.started_at, incomplete: exchange.incomplete, now: exchange.last_at, ...extra };
  return outcomeView(leg, context);
}

const brief = ({ label, icon, tone }) => ({ label, icon, tone });

test("a delivered leg is blue with a check", () => {
  assert.deepEqual(brief(legView("05", "primary")), { label: "delivered", icon: "✓", tone: "delivered" });
});

test("a proxy CallResult is blocked", () => {
  assert.deepEqual(brief(legView("06", "charger")), { label: "blocked", icon: "⛔", tone: "blocked" });
});

test("a proxy CallError is refused and names the error code", () => {
  const view = legView("07", "charger");
  assert.deepEqual(brief(view), { label: "refused", icon: "✕", tone: "refused" });
  assert.match(view.title, /ERR NotSupported/);
});

test("a withheld leg shows the lock and the reason text", () => {
  const view = legView("08", "primary");
  assert.deepEqual(brief(view), { label: "withheld", icon: "⊣🔒", tone: "withheld" });
  assert.equal(view.title, "Withheld from primary: the idTag was not authorized");
});

test("a leg delivered after queueing shows how late it was", () => {
  assert.deepEqual(brief(legView("09", "primary")), { label: "delivered +4m12s", icon: "✓", tone: "delivered" });
});

test("no reply from the charger fails with a clock", () => {
  assert.deepEqual(brief(legView("12", "charger")), { label: "failed", icon: "⏱", tone: "failed" });
});

test("a peer CallError fails even when the leg was delivered", () => {
  const view = legView("17", "primary");
  assert.deepEqual(brief(view), { label: "failed", icon: "✕", tone: "failed" });
  assert.equal(view.title, "ERR InternalError");
});

test("queued and dropped legs", () => {
  assert.deepEqual(brief(legView("16", "primary")), { label: "queued", icon: "⧗", tone: "queued" });
  assert.deepEqual(brief(legView("16", "stats")), { label: "dropped", icon: "⌫", tone: "dropped" });
});

test("an open leg of an incomplete exchange is unknown", () => {
  assert.deepEqual(brief(legView("14", "primary")), { label: "unknown (monitor gap)", icon: "?", tone: "unknown" });
});

test("an open leg is pending until it is too old to end normally", () => {
  const exchange = exchangeById("4611686018427387914");
  const leg = exchange.legs[0];
  const fresh = outcomeView(leg, { startedAt: exchange.started_at, now: leg.sent_at + 5 });
  assert.equal(fresh.label, "pending");
  const stale = outcomeView(leg, { startedAt: exchange.started_at, now: leg.sent_at + UNKNOWN_AFTER_SECONDS + 1 });
  assert.equal(stale.label, "unknown (monitor gap)");
});

test("every view has an icon and a word", () => {
  for (const exchange of [exchangeById("4611686018427387916"), exchangeById("4611686018427387908")]) {
    for (const leg of exchange.legs) {
      const view = outcomeView(leg, { startedAt: exchange.started_at, now: exchange.last_at });
      assert.ok(view.icon.length > 0 && view.label.length > 0);
    }
  }
});

test("not sent is a grey dash", () => {
  assert.deepEqual(brief(notSentView()), { label: "not sent", icon: "—", tone: "muted" });
});
