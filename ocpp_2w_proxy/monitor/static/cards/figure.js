// A key figure (big value, small label) and the row that holds them, shared by the payload cards.

import { html } from "../html.js";

export function figure(label, value) {
  return html`<div class="figure"><span class="figure__value">${value}</span><span class="figure__label">${label}</span></div>`;
}

/** A row of figures; falsy entries are left out so cards can list optional ones inline. */
export function figures(items) {
  return html`<div class="figures">${items.filter(Boolean)}</div>`;
}
