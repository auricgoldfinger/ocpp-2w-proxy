// Configuration keys and values as the configuration request and reply views show them.
// Values masked by the proxy stay "***" and say so to screen readers.

import { html } from "../html.js";

const MASK = "***";

export function configValue(value) {
  if (value === undefined || value === null) return html`<span class="muted">not set</span>`;
  if (value === MASK) return html`<span class="masked" title="Masked by the proxy">${MASK}<span class="sr-only"> (masked)</span></span>`;
  return html`<span class="mono">${typeof value === "string" ? value : JSON.stringify(value)}</span>`;
}

/** "a, b, c" with each key in monospace. */
export function keyList(keys) {
  return keys.map((key, at) => html`${at > 0 ? ", " : ""}<span class="mono">${key}</span>`);
}

export const asList = (value) => (Array.isArray(value) ? value : []);
