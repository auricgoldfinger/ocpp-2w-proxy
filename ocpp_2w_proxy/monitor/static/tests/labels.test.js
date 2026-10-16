import assert from "node:assert/strict";
import { test } from "node:test";

import { renderCard } from "../cards/index.js";
import { fieldLabel } from "../cards/labels.js";

test("OCPP field names have a human label", () => {
  assert.equal(fieldLabel("connectorId"), "connector");
  assert.equal(fieldLabel("transactionId"), "transaction");
  assert.equal(fieldLabel("idTag"), "ID tag");
  assert.equal(fieldLabel("iccid"), "ICCID");
  assert.equal(fieldLabel("readonly"), "read-only");
});

test("other keys are split at their camelCase humps", () => {
  assert.equal(fieldLabel("retrieveDate"), "retrieve date");
  assert.equal(fieldLabel("chargingSchedulePeriod"), "charging schedule period");
  assert.equal(fieldLabel("firmwareURL"), "firmware URL");
  assert.equal(fieldLabel("HTTPProxyHost"), "HTTP proxy host");
  assert.equal(fieldLabel("start_period"), "start period");
  assert.equal(fieldLabel("status"), "status");
});

test("keys that are not words stay as they are", () => {
  assert.equal(fieldLabel(""), "");
  assert.equal(fieldLabel("0"), "0");
  assert.equal(fieldLabel("constructor"), "constructor", "no prototype lookups");
});

test("the generic card shows the label and keeps the raw key as its title", () => {
  const markup = String(renderCard("X", { connectorId: 1, "<k>": 2 }));
  assert.match(markup, /<dt title="connectorId">connector<\/dt>/);
  assert.match(markup, /<dt title="&lt;k&gt;">&lt;k&gt;<\/dt>/);
});
