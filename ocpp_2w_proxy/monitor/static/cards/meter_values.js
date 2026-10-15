// MeterValues card: key figures (kW, kWh, A, V, °C), then the latest reading as a
// measurand × phase table; earlier readings in the same message sit collapsed below it.

import { formatDateTime, formatKw, formatKwh } from "../format.js";
import { html } from "../html.js";
import { figure, figures } from "./figure.js";
import {
  CURRENT,
  ENERGY,
  POWER,
  TEMPERATURE,
  VOLTAGE,
  latestSamples,
  latestTotal,
  measurandRows,
  meterEntries,
  phaseColumns,
  sharedContext,
} from "./meter_readings.js";

const SPREAD_FIGURES = [
  [CURRENT, "current"],
  [VOLTAGE, "voltage"],
  [TEMPERATURE, "temperature"],
];

/** "16.1 / 16 / 15.9 A" with the phases in the label, or the single total. */
function spreadFigure(entries, measurand, label) {
  const samples = latestSamples(entries, measurand);
  if (samples.length === 0) return null;
  const values = samples.map((sample) => sample.value).join(" / ");
  const phases = samples.map((sample) => sample.phase).filter(Boolean);
  return figure(phases.length > 0 ? `${label} ${phases.join(" · ")}` : label, `${values} ${samples[0].unitLabel}`);
}

function keyFigures(entries, connectorId) {
  const powerW = latestTotal(entries, POWER);
  const energyWh = latestTotal(entries, ENERGY);
  return figures([
    powerW !== null && figure("power", formatKw(powerW)),
    energyWh !== null && figure("meter", formatKwh(energyWh)),
    ...SPREAD_FIGURES.map(([measurand, label]) => spreadFigure(entries, measurand, label)),
    figure("connector", connectorId ?? "–"),
  ]);
}

function cell(sample) {
  if (!sample) return html`<td class="muted">–</td>`;
  return html`<td><span class="num">${sample.value}</span> <span class="unit">${sample.unitLabel}</span></td>`;
}

function caption(entry) {
  const context = sharedContext(entry.samples);
  return html`<caption>${formatDateTime(entry.at)}${context ? html` <span class="muted">· ${context}</span>` : ""}</caption>`;
}

function readingTable(entry) {
  if (entry.samples.length === 0) return html`<p class="muted">${formatDateTime(entry.at)}: no sampled values.</p>`;
  const phases = phaseColumns(entry.samples);
  const heads = phases.map((phase) => html`<th scope="col">${phase || (phases.length > 1 ? "total" : "value")}</th>`);
  const rows = measurandRows(entry.samples).map(
    (row) => html`<tr><th scope="row">${row.measurand}${row.location ? html` <span class="muted">(${row.location})</span>` : ""}</th>${phases.map((phase) => cell(row.cells[phase]))}</tr>`,
  );
  return html`<div class="table-scroll"><table class="meter-table">${caption(entry)}<thead><tr><th scope="col">measurand</th>${heads}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function earlierReadings(entries) {
  if (entries.length === 0) return "";
  const label = entries.length === 1 ? "1 earlier reading" : `${entries.length} earlier readings`;
  return html`<details class="card__more"><summary>${label}</summary>${[...entries].reverse().map(readingTable)}</details>`;
}

export function meterValuesCard(payload) {
  const entries = meterEntries(payload);
  const latest = entries.at(-1);
  const body = latest
    ? html`${readingTable(latest)}${earlierReadings(entries.slice(0, -1))}`
    : html`<p class="muted">This message carries no meter values.</p>`;
  return html`<div class="card card--meter">${keyFigures(entries, payload?.connectorId)}${body}</div>`;
}
