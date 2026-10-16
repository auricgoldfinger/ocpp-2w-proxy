import assert from "node:assert/strict";
import { test } from "node:test";

import { renderCard, wantsSession } from "../cards/index.js";
import { registerReadings, startFigures, stopFigures } from "../cards/transaction.js";
import { loadFixture } from "./fixture_loader.js";

const stopPayload = () => loadFixture("exchange_detail").payload;
const session = () => loadFixture("session");

test("unknown actions fall back to the generic card", () => {
  const markup = String(renderCard("DataTransfer", { vendorId: "KEBA", data: " 1.20" }));
  assert.match(markup, /card--generic/);
  assert.match(markup, /<dt title="vendorId">vendor<\/dt><dd><span class="kv-str">KEBA<\/span><\/dd>/);
  assert.match(markup, /<span class="kv-num">1.2<\/span>/, "numeric strings are trimmed");
});

test("the generic card renders nested arrays, empties and nulls", () => {
  const markup = String(renderCard("X", { list: [{ a: null }], none: [], obj: {} }));
  assert.match(markup, /<ol class="kv-list" start="0"><li><dl class="kv"><dt title="a">a<\/dt><dd><span class="kv-null">null<\/span>/);
  assert.match(markup, /<dt title="none">none<\/dt><dd><span class="kv-null">\[ \]<\/span>/);
  assert.match(markup, /<dt title="obj">obj<\/dt><dd><span class="kv-null">\{ \}<\/span>/);
});

test("payload keys and values are escaped", () => {
  const markup = String(renderCard("X", { "<k>": "<script>alert(1)</script>" }));
  assert.ok(!markup.includes("<script>") && !markup.includes("<k>"));
});

test("register readings convert kWh and trim padded values", () => {
  assert.deepEqual(registerReadings(stopPayload().transactionData), [
    { at: 1760530000, wh: 1227100 },
    { at: 1760534412, wh: 1234521 },
  ]);
});

test("stop figures prefer the session and fall back to the payload", () => {
  assert.deepEqual(stopFigures(stopPayload(), session()), { energyWh: 7421, durationS: 4412 });
  assert.deepEqual(stopFigures(stopPayload()), { energyWh: 7421, durationS: 4412 });
  assert.deepEqual(stopFigures({ meterStop: 5 }), { energyWh: null, durationS: null });
});

test("the Stop card shows kWh and duration", () => {
  const markup = String(renderCard("StopTransaction", stopPayload(), { session: session() }));
  assert.match(markup, /figure__value">7.421 kWh<\/span><span class="figure__label">delivered/);
  assert.match(markup, /figure__value">1h13m<\/span><span class="figure__label">duration/);
});

test("the Start card shows the meter at start and the running duration", () => {
  const payload = { connectorId: 1, idTag: "***4F2A", meterStart: 1227100, timestamp: "2025-10-15T12:06:40Z" };
  const running = { ...session(), stopped_at: null, energy_wh: 1200 };
  assert.deepEqual(startFigures(payload, running, 1760530600), {
    meterStartWh: 1227100, energyWh: 1200, durationS: 600, running: true,
  });
  const markup = String(renderCard("StartTransaction", payload, { session: running, now: 1760530600 }));
  assert.match(markup, />1227.1 kWh<\/span><span class="figure__label">meter at start/);
  assert.match(markup, />1.2 kWh<\/span><span class="figure__label">so far/);
  assert.match(markup, />10m00s<\/span><span class="figure__label">running for/);
});

test("transaction cards ask for the session", () => {
  assert.ok(wantsSession("StopTransaction") && wantsSession("StartTransaction"));
  assert.ok(!wantsSession("MeterValues"));
});
