// What one leg's peer received compared with the request: the same payload, or a table of the
// paths the proxy changed.

import { diffPaths } from "../diff.js";
import { html } from "../html.js";

function showJson(value) {
  return value === undefined ? "–" : JSON.stringify(value);
}

/** The outgoing call `frame` compared with the `origin` payload. */
export function legDiff(frame, origin) {
  const changes = frame.same_as_origin ? [] : diffPaths(origin, frame.payload);
  if (changes.length === 0) return html`<p class="muted">Same as the request.</p>`;
  const rows = changes.map(
    (change) => html`<tr class="diff--${change.kind}"><th scope="row" class="mono">${change.path}</th><td>${change.kind}</td><td class="mono diff__from">${showJson(change.from)}</td><td class="mono diff__to">${showJson(change.to)}</td></tr>`,
  );
  return html`<div class="table-scroll"><table class="diff"><thead><tr><th scope="col">path</th><th scope="col">change</th><th scope="col">request</th><th scope="col">sent</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
