// Generic payload card: a key/value tree of any JSON payload. Keys show their human label with
// the raw key as the tooltip; numeric strings are trimmed (" 1.2" → 1.2) because chargers pad them.

import { trimNumericString } from "../format.js";
import { html } from "../html.js";
import { fieldLabel } from "./labels.js";

function scalar(value) {
  if (value === null) return html`<span class="kv-null">null</span>`;
  const trimmed = trimNumericString(value);
  if (typeof trimmed === "number") return html`<span class="kv-num">${trimmed}</span>`;
  if (typeof trimmed === "boolean") return html`<span class="kv-bool">${String(trimmed)}</span>`;
  return html`<span class="kv-str">${trimmed}</span>`;
}

function tree(value) {
  if (Array.isArray(value)) {
    if (value.length === 0) return html`<span class="kv-null">[ ]</span>`;
    return html`<ol class="kv-list" start="0">${value.map((item) => html`<li>${tree(item)}</li>`)}</ol>`;
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value);
    if (entries.length === 0) return html`<span class="kv-null">{ }</span>`;
    return html`<dl class="kv">${entries.map(([key, item]) => html`<dt title="${key}">${fieldLabel(key)}</dt><dd>${tree(item)}</dd>`)}</dl>`;
  }
  return scalar(value);
}

/** The payload as a key/value tree. */
export function payloadTree(payload) {
  return html`<div class="payload-tree">${tree(payload)}</div>`;
}

export function genericCard(payload) {
  return html`<div class="card card--generic">${payloadTree(payload)}</div>`;
}
