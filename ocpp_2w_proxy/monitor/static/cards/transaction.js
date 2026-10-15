// StartTransaction / StopTransaction cards: energy in kWh and the session duration, above the
// full payload. Figures come from the session aggregate when known, else from the payload.

import { formatDuration, formatKwh, trimNumericString } from "../format.js";
import { html } from "../html.js";
import { figure, figures } from "./figure.js";
import { payloadTree } from "./generic.js";

const REGISTER = "Energy.Active.Import.Register";
const UNIT_TO_WH = { Wh: 1, kWh: 1000 };

function epochSeconds(timestamp) {
  const millis = Date.parse(timestamp);
  return Number.isNaN(millis) ? null : millis / 1000;
}

/** Energy register samples from StopTransaction.transactionData: [{at, wh}], oldest first. */
export function registerReadings(transactionData = []) {
  const readings = [];
  for (const meterValue of transactionData) {
    for (const sample of meterValue.sampledValue ?? []) {
      if ((sample.measurand ?? REGISTER) !== REGISTER) continue;
      const value = trimNumericString(sample.value);
      const factor = UNIT_TO_WH[sample.unit ?? "Wh"];
      if (typeof value !== "number" || factor === undefined) continue;
      readings.push({ at: epochSeconds(meterValue.timestamp), wh: value * factor });
    }
  }
  return readings.sort((a, b) => a.at - b.at);
}

/** {energyWh, durationS} of a stopped transaction; either may be null when unknown. */
export function stopFigures(payload, session = null) {
  const readings = registerReadings(payload.transactionData);
  const first = readings[0];
  const meterStop = trimNumericString(payload.meterStop);
  let energyWh = session?.energy_wh ?? null;
  if (energyWh === null && first && typeof meterStop === "number") energyWh = meterStop - first.wh;
  const stoppedAt = session?.stopped_at ?? epochSeconds(payload.timestamp);
  const startedAt = session?.started_at ?? first?.at ?? null;
  const durationS = startedAt !== null && stoppedAt !== null ? stoppedAt - startedAt : null;
  return { energyWh, durationS };
}

/** {meterStartWh, energyWh, durationS} of a started transaction, "so far" from the session. */
export function startFigures(payload, session = null, now = Date.now() / 1000) {
  const meterStart = trimNumericString(payload.meterStart);
  const startedAt = session?.started_at ?? epochSeconds(payload.timestamp);
  const endedAt = session?.stopped_at ?? now;
  return {
    meterStartWh: typeof meterStart === "number" ? meterStart : null,
    energyWh: session?.energy_wh ?? null,
    durationS: startedAt !== null ? endedAt - startedAt : null,
    running: session ? session.stopped_at === null : null,
  };
}

export function stopCard(payload, { session = null } = {}) {
  const { energyWh, durationS } = stopFigures(payload, session);
  const row = figures([
    figure("delivered", formatKwh(energyWh)),
    figure("duration", formatDuration(durationS)),
    payload.reason && figure("reason", payload.reason),
  ]);
  return html`<div class="card card--transaction">${row}${payloadTree(payload)}</div>`;
}

export function startCard(payload, { session = null, now } = {}) {
  const { meterStartWh, energyWh, durationS, running } = startFigures(payload, session, now);
  const row = figures([
    figure("meter at start", formatKwh(meterStartWh)),
    energyWh !== null && figure(running ? "so far" : "delivered", formatKwh(energyWh)),
    figure(running ? "running for" : "duration", formatDuration(durationS)),
    figure("connector", payload.connectorId ?? "–"),
  ]);
  return html`<div class="card card--transaction">${row}${payloadTree(payload)}</div>`;
}
