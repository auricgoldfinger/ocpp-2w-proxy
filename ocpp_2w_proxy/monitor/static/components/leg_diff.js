// Each outgoing call compared with the origin payload, as a table of changed paths.

import { diffPaths } from "../diff.js";
import { html } from "../html.js";

function showJson(value) {
  return value === undefined ? "–" : JSON.stringify(value);
}

/** One outgoing call `frame` compared with the `origin` payload. */
export function legDiff(frame, origin) {
  const title = html`<h4>→ ${frame.peer} <span class="mono muted">${frame.wire_id ?? ""}</span></h4>`;
  if (frame.same_as_origin) return html`${title}<p class="muted">Identical to the origin.</p>`;
  const changes = diffPaths(origin, frame.payload);
  if (changes.length === 0) return html`${title}<p class="muted">Equal to the origin.</p>`;
  const rows = changes.map(
    (change) => html`<tr class="diff--${change.kind}"><th scope="row" class="mono">${change.path}</th><td>${change.kind}</td><td class="mono diff__from">${showJson(change.from)}</td><td class="mono diff__to">${showJson(change.to)}</td></tr>`,
  );
  return html`${title}<div class="table-scroll"><table class="diff"><thead><tr><th scope="col">path</th><th scope="col">change</th><th scope="col">origin</th><th scope="col">this leg</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

/** Each outgoing call compared with the origin payload. */
export function legDiffs(detail) {
  const calls = detail.frames.filter((frame) => frame.dir === "out" && frame.msg_type === "call");
  if (calls.length === 0) return html`<p class="muted">No calls were sent on.</p>`;
  return html`${calls.map((frame) => legDiff(frame, detail.payload))}`;
}
