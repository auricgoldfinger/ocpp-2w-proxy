// GetConfiguration confirmation: a key / value / access table plus the keys the charger does
// not know.

import { html } from "../html.js";
import { asList, configValue, keyList } from "./config_value.js";

function access(readonly) {
  if (readonly === true) return html`<span aria-hidden="true">🔒</span> read-only`;
  if (readonly === false) return html`<span aria-hidden="true">✎</span> writable`;
  return html`<span class="muted">–</span>`;
}

export function keysTable(frame) {
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
