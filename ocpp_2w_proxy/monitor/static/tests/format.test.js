import assert from "node:assert/strict";
import { test } from "node:test";

process.env.TZ = "UTC";

const {
  formatCount,
  formatDateTime,
  formatDuration,
  formatIsoDateTime,
  formatKw,
  formatKwh,
  formatLatency,
  formatRate,
  formatTime,
  trimNumericString,
} = await import("../format.js");

test("formatTime shows local wall-clock time", () => {
  assert.equal(formatTime(1760536931.21), "14:02:11");
  assert.equal(formatTime(null), "–");
});

test("formatIsoDateTime shows an ISO timestamp as local date and time", () => {
  assert.equal(formatIsoDateTime("2025-10-15T14:02:11Z"), "2025-10-15 14:02:11");
  assert.equal(formatIsoDateTime("soon"), "soon");
  assert.equal(formatIsoDateTime(undefined), "–");
});

test("formatDateTime shows date and time", () => {
  assert.equal(formatDateTime(1760536931.21), "2025-10-15 14:02:11");
});

test("formatDuration picks the two largest units", () => {
  assert.equal(formatDuration(0.04), "40 ms");
  assert.equal(formatDuration(12.4), "12s");
  assert.equal(formatDuration(252), "4m12s");
  assert.equal(formatDuration(7500), "2h05m");
  assert.equal(formatDuration(3 * 86400 + 4 * 3600), "3d04h");
  assert.equal(formatDuration(null), "–");
});

test("formatLatency needs both ends", () => {
  assert.equal(formatLatency(10, 10.25), "250 ms");
  assert.equal(formatLatency(10, null), "–");
});

test("formatCount and formatRate abbreviate", () => {
  assert.equal(formatCount(980), "980");
  assert.equal(formatCount(1210), "1.2k");
  assert.equal(formatCount(2000), "2k");
  assert.equal(formatCount(3_400_000), "3.4M");
  assert.equal(formatRate(1210), "1.2k/h");
});

test("formatKwh converts watt-hours", () => {
  assert.equal(formatKwh(7421), "7.421 kWh");
  assert.equal(formatKwh(7400), "7.4 kWh");
  assert.equal(formatKwh(0), "0 kWh");
  assert.equal(formatKwh(undefined), "–");
});

test("formatKw converts watts", () => {
  assert.equal(formatKw(7400), "7.4 kW");
  assert.equal(formatKw(11050), "11.05 kW");
  assert.equal(formatKw(0), "0 kW");
  assert.equal(formatKw(null), "–");
});

test("trimNumericString turns padded numeric strings into numbers", () => {
  assert.equal(trimNumericString(" 1.2"), 1.2);
  assert.equal(trimNumericString("-3"), -3);
  assert.equal(trimNumericString("1e3"), 1000);
  assert.equal(trimNumericString("Charging"), "Charging");
  assert.equal(trimNumericString(""), "");
  assert.equal(trimNumericString("1.2.3"), "1.2.3");
  assert.equal(trimNumericString(5), 5);
});
