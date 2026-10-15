// Pure reading of an OCPP 1.6 MeterValues payload, tolerating the sloppy data chargers send.
// It follows monitor/meter_values.py so the card agrees with the list headline: the last
// meterValue entry is the latest, and a total is the phase-less sample, else the first phase.

import { trimNumericString } from "../format.js";

export const ENERGY = "Energy.Active.Import.Register";
export const POWER = "Power.Active.Import";
export const CURRENT = "Current.Import";
export const VOLTAGE = "Voltage";
export const TEMPERATURE = "Temperature";

// OCPP 1.6 sampledValue defaults when the charger leaves the field out.
const DEFAULT_MEASURAND = ENERGY;
const DEFAULT_UNITS = {
  [ENERGY]: "Wh",
  [POWER]: "W",
  [CURRENT]: "A",
  "Current.Export": "A",
  "Current.Offered": "A",
  [VOLTAGE]: "V",
  [TEMPERATURE]: "Celsius",
  SoC: "Percent",
};
const BASE_UNIT_FACTORS = { Wh: 1, kWh: 1000, W: 1, kW: 1000 };
const UNIT_LABELS = { Celsius: "°C", Fahrenheit: "°F", Percent: "%" };

/** Table column order; samples without a phase are the "total" column. */
export const PHASES = ["", "L1", "L2", "L3", "N", "L1-N", "L2-N", "L3-N", "L1-L2", "L2-L3", "L3-L1"];

function epochSeconds(timestamp) {
  const millis = typeof timestamp === "string" ? Date.parse(timestamp) : Number.NaN;
  return Number.isNaN(millis) ? null : millis / 1000;
}

/** One sampledValue with OCPP defaults filled in; `value` is a number when it is numeric. */
export function readSample(raw) {
  const measurand = raw.measurand ?? DEFAULT_MEASURAND;
  const unit = raw.unit ?? DEFAULT_UNITS[measurand] ?? "";
  return {
    measurand,
    phase: raw.phase ?? "",
    location: raw.location ?? "",
    context: raw.context ?? "",
    value: trimNumericString(raw.value),
    unit,
    unitLabel: UNIT_LABELS[unit] ?? unit,
  };
}

/** The meterValue entries as [{at, samples}], in payload order: the last one is the latest. */
export function meterEntries(payload) {
  const entries = Array.isArray(payload?.meterValue) ? payload.meterValue : [];
  return entries
    .filter((entry) => entry !== null && typeof entry === "object")
    .map((entry) => ({
      at: epochSeconds(entry.timestamp),
      samples: (Array.isArray(entry.sampledValue) ? entry.sampledValue : [])
        .filter((raw) => raw !== null && typeof raw === "object")
        .map(readSample),
    }));
}

// An entry's numeric samples of `measurand`: the phase-less ones, else the phases.
function totalsOf(entry, measurand) {
  const matching = entry.samples.filter((sample) => sample.measurand === measurand && typeof sample.value === "number");
  const totals = matching.filter((sample) => sample.phase === "");
  return totals.length > 0 ? totals : matching;
}

/** The numeric samples of `measurand` in the newest entry that has any (see totalsOf). */
export function latestSamples(entries, measurand) {
  for (const entry of [...entries].reverse()) {
    const totals = totalsOf(entry, measurand);
    if (totals.length > 0) return totals;
  }
  return [];
}

/** The newest total of an energy or power measurand in Wh or W, or null. */
export function latestTotal(entries, measurand) {
  for (const entry of [...entries].reverse()) {
    const sample = totalsOf(entry, measurand).find((candidate) => BASE_UNIT_FACTORS[candidate.unit] !== undefined);
    if (sample) return sample.value * BASE_UNIT_FACTORS[sample.unit];
  }
  return null;
}

/** Rows of a measurand × phase table: [{measurand, location, cells: {phase: sample}}], in sample order. */
export function measurandRows(samples) {
  const rows = new Map();
  for (const sample of samples) {
    const key = `${sample.measurand}\u0000${sample.location}`;
    if (!rows.has(key)) rows.set(key, { measurand: sample.measurand, location: sample.location, cells: {} });
    rows.get(key).cells[sample.phase] = sample;
  }
  return [...rows.values()];
}

/** The phases present in `samples`, in table order; unknown phase names go last. */
export function phaseColumns(samples) {
  const present = new Set(samples.map((sample) => sample.phase));
  const known = PHASES.filter((phase) => present.has(phase));
  return [...known, ...[...present].filter((phase) => !PHASES.includes(phase))];
}

/** The reading context when every sample shares one ("Sample.Periodic"), else "". */
export function sharedContext(samples) {
  const contexts = new Set(samples.map((sample) => sample.context));
  return contexts.size === 1 ? [...contexts][0] : "";
}
