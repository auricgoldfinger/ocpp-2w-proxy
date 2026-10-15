// GetConfiguration and ChangeConfiguration cards. GetConfiguration: the requested keys, then the
// reply as a key / value / access table plus the keys the charger does not know.
// ChangeConfiguration: key → value and the reply status. Values masked by the proxy stay "***".

import { badge } from "../components/swimlane.js";
import { html } from "../html.js";
import { replySection } from "./reply_section.js";
import { statusView } from "./status_view.js";

const MASK = "***";

const CHANGE_VIEWS = {
  Accepted: { icon: "✓", tone: "up" },
  RebootRequired: { icon: "↻", tone: "queued" },
  Rejected: { icon: "✕", tone: "failed" },
  NotSupported: { icon: "⊘", tone: "refused" },
};

function configValue(value) {
  if (value === undefined || value === null) return html`<span class="muted">not set</span>`;
  if (value === MASK) return html`<span class="masked" title="Masked by the proxy">${MASK}<span class="sr-only"> (masked)</span></span>`;
  return html`<span class="mono">${typeof value === "string" ? value : JSON.stringify(value)}</span>`;
}

function access(readonly) {
  if (readonly === true) return html`<span aria-hidden="true">🔒</span> read-only`;
  if (readonly === false) return html`<span aria-hidden="true">✎</span> writable`;
  return html`<span class="muted">–</span>`;
}

const asList = (value) => (Array.isArray(value) ? value : []);

function keyList(keys) {
  return keys.map((key, at) => html`${at > 0 ? ", " : ""}<span class="mono">${key}</span>`);
}

function requestedKeys(keys) {
  if (keys.length === 0) return html`<p class="muted">Requested all keys.</p>`;
  return html`<p>Requested ${keys.length === 1 ? "1 key" : `${keys.length} keys`}: ${keyList(keys)}</p>`;
}

function keysTable(frame) {
  const entries = asList(frame.payload?.configurationKey).filter((entry) => entry !== null && typeof entry === "object");
  const unknown = asList(frame.payload?.unknownKey);
  const table = entries.length === 0
    ? html`<p class="muted">The reply lists no keys.</p>`
    : html`<div class="table-scroll"><table class="config-table"><thead><tr><th scope="col">key</th><th scope="col">value</th><th scope="col">access</th></tr></thead><tbody>${entries.map(
      (entry) => html`<tr><th scope="row" class="mono">${entry.key}</th><td>${configValue(entry.value)}</td><td class="config-table__access">${access(entry.readonly)}</td></tr>`,
    )}</tbody></table></div>`;
  const unknownPart = unknown.length === 0
    ? ""
    : html`<p class="config-unknown"><span aria-hidden="true">?</span> Unknown to the charger: ${keyList(unknown)}</p>`;
  return html`${table}${unknownPart}`;
}

export function getConfigurationCard(payload, { detail = null } = {}) {
  return html`<div class="card card--config">${requestedKeys(asList(payload?.key))}${replySection(detail, keysTable)}</div>`;
}

function changeStatus(frame) {
  return html`<p>${badge(statusView(frame.payload?.status, CHANGE_VIEWS))}</p>`;
}

export function changeConfigurationCard(payload, { detail = null } = {}) {
  const body = payload ?? {};
  return html`<div class="card card--config"><p class="config-change"><span class="mono">${body.key ?? "–"}</span> <span aria-hidden="true">→</span><span class="sr-only">set to</span> ${configValue(body.value)}</p>${replySection(detail, changeStatus)}</div>`;
}
