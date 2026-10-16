// GetConfiguration and ChangeConfiguration cards. GetConfiguration: the requested keys.
// ChangeConfiguration: key → value. Their replies are drawn by responses.js.

import { html } from "../html.js";
import { asList, configValue, keyList } from "./config_value.js";
import { replySection } from "./reply_section.js";

function requestedKeys(keys) {
  if (keys.length === 0) return html`<p class="muted">Requested all keys.</p>`;
  return html`<p>Requested ${keys.length === 1 ? "1 key" : `${keys.length} keys`}: ${keyList(keys)}</p>`;
}

export function getConfigurationCard(payload, { detail = null } = {}) {
  return html`<div class="card card--config">${requestedKeys(asList(payload?.key))}${replySection(detail)}</div>`;
}

export function changeConfigurationCard(payload, { detail = null } = {}) {
  const body = payload ?? {};
  return html`<div class="card card--config"><p class="config-change"><span class="mono">${body.key ?? "–"}</span> <span aria-hidden="true">→</span><span class="sr-only">set to</span> ${configValue(body.value)}</p>${replySection(detail)}</div>`;
}
