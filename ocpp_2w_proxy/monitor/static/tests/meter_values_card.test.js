import assert from "node:assert/strict";
import { test } from "node:test";

import { renderCard } from "../cards/index.js";
import { ENERGY, POWER, latestTotal, measurandRows, meterEntries, phaseColumns, readSample } from "../cards/meter_readings.js";
import { loadFixture } from "./fixture_loader.js";

process.env.TZ = "UTC";

const payload = () => loadFixture("meter_values_detail").payload;
const card = (body) => String(renderCard("MeterValues", body));

test("a sample gets the OCPP defaults and a trimmed number", () => {
  assert.deepEqual(readSample({ value: " 1.2" }), {
    measurand: ENERGY, phase: "", location: "", context: "", value: 1.2, unit: "Wh", unitLabel: "Wh",
  });
  assert.equal(readSample({ value: "34.5", measurand: "Temperature" }).unitLabel, "°C");
  assert.equal(readSample({ value: "n/a" }).value, "n/a");
});

test("totals follow the backend: the newest entry, phase-less first, in Wh or W", () => {
  const entries = meterEntries(payload());
  assert.equal(latestTotal(entries, ENERGY), 1234500);
  assert.equal(latestTotal(entries, POWER), 7400);
  const onlyOlder = meterEntries({ meterValue: [{ sampledValue: [{ value: "5", measurand: POWER, unit: "kW" }] }, { sampledValue: [] }] });
  assert.equal(latestTotal(onlyOlder, POWER), 5000);
  const phased = meterEntries({ meterValue: [{ sampledValue: [{ value: "2", measurand: POWER, phase: "L2" }, { value: "1", measurand: POWER, phase: "L1" }] }] });
  assert.equal(latestTotal(phased, POWER), 2, "the first phase, as in meter_values.py");
  assert.equal(latestTotal(meterEntries({}), ENERGY), null);
});

test("the table has one row per measurand and one column per phase, in phase order", () => {
  const [, latest] = meterEntries(payload());
  assert.deepEqual(phaseColumns(latest.samples), ["", "L1", "L2", "L3", "L1-N", "L2-N", "L3-N"]);
  assert.deepEqual(measurandRows(latest.samples).map((row) => row.measurand), [
    ENERGY, POWER, "Current.Import", "Voltage", "Temperature",
  ]);
});

test("the card shows the key figures in kW, kWh, A, V and °C", () => {
  const markup = card(payload());
  for (const [value, label] of [
    ["7.4 kW", "power"],
    ["1234.5 kWh", "meter"],
    ["10.7 / 10.6 / 10.8 A", "current L1 · L2 · L3"],
    ["231 / 229 / 230 V", "voltage L1-N · L2-N · L3-N"],
    ["34.5 °C", "temperature"],
    ["1", "connector"],
  ]) {
    assert.match(markup, new RegExp(`figure__value">${value}</span><span class="figure__label">${label}<`));
  }
});

test("the latest reading is the table; earlier ones are collapsed", () => {
  const markup = card(payload());
  assert.match(markup, /<caption>2025-10-15 14:02:11 <span class="muted">· Sample.Periodic<\/span><\/caption>/);
  assert.match(markup, /<th scope="row">Current.Import<\/th><td class="muted">–<\/td><td><span class="num">10.7<\/span> <span class="unit">A<\/span><\/td>/);
  assert.match(markup, /<th scope="row">Temperature <span class="muted">\(Body\)<\/span><\/th><td><span class="num">34.5<\/span> <span class="unit">°C<\/span>/);
  assert.match(markup, /<details class="card__more"><summary>1 earlier reading<\/summary>.*2025-10-15 14:01:11.*<th scope="col">value<\/th>/s);
  assert.ok(markup.indexOf("14:02:11") < markup.indexOf("<details"));
});

test("a payload without readings or with junk still renders", () => {
  assert.match(card({ connectorId: 2, meterValue: [] }), /carries no meter values/);
  assert.match(card({ meterValue: [null, { timestamp: "bad", sampledValue: "x" }] }), /–: no sampled values/);
  assert.match(card(null), /carries no meter values/);
});

test("meter values are escaped", () => {
  const markup = card({ meterValue: [{ sampledValue: [{ value: "<b>", measurand: "<i>", unit: "<u>" }] }] });
  assert.ok(!/<[biu]>/.test(markup));
});
