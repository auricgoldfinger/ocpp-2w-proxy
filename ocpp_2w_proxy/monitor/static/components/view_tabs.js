// The drawer's "Readable | JSON" switch: an ARIA tablist with a roving tabindex. The selected tab
// is bold and underlined, never told apart by colour alone.

import { html } from "../html.js";

export const VIEWS = [
  { mode: "readable", label: "Readable" },
  { mode: "json", label: "JSON" },
];

export const panelId = (mode) => `drawer-panel-${mode}`;
export const tabId = (mode) => `drawer-tab-${mode}`;

function tab({ mode, label }, active) {
  const selected = mode === active;
  // Only the selected tab names its panel: the other panel is not rendered.
  const controls = selected ? html` aria-controls="${panelId(mode)}"` : "";
  return html`<button type="button" role="tab" class="view-tab" id="${tabId(mode)}" data-view="${mode}" data-key="${tabId(mode)}" aria-selected="${String(selected)}" tabindex="${selected ? "0" : "-1"}"${controls}>${label}</button>`;
}

/** The tablist with `active` selected. */
export function viewTabs(active) {
  return html`<div class="view-tabs" role="tablist" aria-label="Detail view">${VIEWS.map((view) => tab(view, active))}</div>`;
}

/** The panel around the active view's `body`. */
export function viewPanel(active, body) {
  return html`<div role="tabpanel" class="view-panel" id="${panelId(active)}" aria-labelledby="${tabId(active)}">${body}</div>`;
}
