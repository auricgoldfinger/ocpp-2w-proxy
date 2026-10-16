import assert from "node:assert/strict";
import { test } from "node:test";

import { renderCard } from "../cards/index.js";
import { loadFixture } from "./fixture_loader.js";

const card = () => String(renderCard("BootNotification", loadFixture("boot_notification_detail").payload));

test("the card shows the charger identity", () => {
  const markup = card();
  assert.match(markup, /<p class="boot-card__name">KEBA KC-P30<\/p>/);
  assert.match(markup, /<dt>serial number<\/dt><dd>99224881<\/dd>/);
  assert.match(markup, /<dt>firmware<\/dt><dd>1.11.2<\/dd>/);
  assert.match(markup, /<dt>ICCID<\/dt><dd>\*\*\*<\/dd>/, "masked values stay masked");
});

test("the card describes the request only; the reply is drawn per leg", () => {
  assert.ok(!card().includes("registration"));
});

test("identity fields are escaped", () => {
  const markup = String(renderCard("BootNotification", { chargePointVendor: "<v>", firmwareVersion: "<f>" }));
  assert.ok(!/<[vf]>/.test(markup));
});
