import assert from "node:assert/strict";
import { test } from "node:test";

import { renderCard } from "../cards/index.js";
import { connectorStatusView } from "../cards/status_notification.js";
import { loadFixture } from "./fixture_loader.js";

const card = (payload) => String(renderCard("StatusNotification", payload));

test("every status has an icon and a word", () => {
  assert.deepEqual(connectorStatusView("Charging"), { label: "Charging", icon: "⚡", tone: "delivered" });
  assert.deepEqual(connectorStatusView("Faulted"), { label: "Faulted", icon: "✕", tone: "failed" });
  assert.deepEqual(connectorStatusView("Bogus"), { label: "Bogus", icon: "?", tone: "unknown" });
  assert.deepEqual(connectorStatusView(undefined), { label: "unknown", icon: "?", tone: "unknown" });
});

test("a fault shows the status pill, the error code, info and vendor fields", () => {
  const markup = card(loadFixture("status_notification_detail").payload);
  assert.match(markup, /status-pill"><span class="badge tone-failed" title="Faulted"><span class="badge__icon" aria-hidden="true">✕<\/span><span class="badge__label">Faulted</);
  assert.match(markup, /badge__label">GroundFailure</);
  assert.match(markup, /<dt>info<\/dt><dd>RCD tripped<\/dd>/);
  assert.match(markup, /<dt>vendor<\/dt><dd>KEBA<\/dd><dt>vendor error<\/dt><dd>0x0042<\/dd>/);
});

test("NoError is not shown", () => {
  const markup = card({ connectorId: 0, status: "Available", errorCode: "NoError" });
  assert.ok(!markup.includes("NoError"));
  assert.match(markup, /badge__label">Available</);
  assert.match(markup, /<dt>connector<\/dt><dd>0 \(whole charger\)<\/dd>/);
  assert.ok(!markup.includes("<dt>info"));
});

test("status fields are escaped", () => {
  const markup = card({ status: "<x>", errorCode: "<y>", info: "<z>" });
  assert.ok(!/<[xyz]>/.test(markup));
});
