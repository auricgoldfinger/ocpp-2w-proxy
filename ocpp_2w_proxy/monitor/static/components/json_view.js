// The drawer's JSON view: every frame as the OCPP-J message it was on the wire, then the whole
// API record. Text only — a header per frame, coloured JSON and copy buttons, no graphics.

import { formatDuration } from "../format.js";
import { html } from "../html.js";
import { highlightJson } from "./json_highlight.js";
import { ocppArray } from "./ocpp_frame.js";

const TYPE_NAMES = { call: "CALL", result: "CALLRESULT", error: "CALLERROR" };

/** Pretty JSON of the whole detail, as the API returned it. */
export function rawJsonText(detail) {
  return JSON.stringify(detail, null, 2);
}

/** "1 · charger → proxy · CALL · a1b2 · +0 ms": frame number, direction, type, wire id, offset. */
export function frameHeading(detail, index) {
  const frame = detail.frames[index];
  const [from, to] = frame.dir === "in" ? [frame.peer, "proxy"] : ["proxy", frame.peer];
  const offset = formatDuration(frame.at - detail.frames[0].at);
  return `${index + 1} · ${from} → ${to} · ${TYPE_NAMES[frame.msg_type] ?? frame.msg_type} · ${frame.wire_id ?? "–"} · +${offset}`;
}

function frameJson(detail, index) {
  return ocppArray(detail.frames[index], detail.action);
}

/**
 * The text a copy button puts on the clipboard: `target` is a frame index, "all" (every frame,
 * each under its heading) or "record" (the whole API record).
 */
export function copyText(detail, target) {
  if (target === "record") return rawJsonText(detail);
  if (target === "all") {
    return detail.frames.map((_, index) => `// ${frameHeading(detail, index)}\n${JSON.stringify(frameJson(detail, index), null, 2)}`).join("\n\n");
  }
  return JSON.stringify(frameJson(detail, Number(target)), null, 2);
}

function copyButton(target, label) {
  return html`<button type="button" class="button" data-action="copy" data-copy="${target}" data-key="copy-${target}">${label}</button>`;
}

function codeBlock(target, value) {
  return html`<pre class="json-view" data-copy-source="${target}" data-key="json-${target}"><code>${highlightJson(value)}</code></pre>`;
}

function frameBlock(detail, index) {
  return html`<li class="json-frame"><div class="json-frame__head"><h3 class="json-frame__title">${frameHeading(detail, index)}</h3>${copyButton(index, html`Copy<span class="sr-only"> frame ${index + 1}</span>`)}</div>${codeBlock(index, frameJson(detail, index))}</li>`;
}

function frames(detail) {
  if (detail.frames.length === 0) return html`<p class="muted">No frames recorded.</p>`;
  return html`<ol class="json-frames" data-copy-source="all">${detail.frames.map((_, index) => frameBlock(detail, index))}</ol>`;
}

/** The JSON panel's content. */
export function jsonView(detail) {
  const tools = html`<div class="json-tools">${detail.frames.length > 0 ? copyButton("all", "Copy all frames") : ""} <span class="muted" data-part="copy-status" role="status"></span></div>`;
  const note = html`<p class="muted json-note">Sensitive values were masked by the proxy before they were stored; they read *** here.</p>`;
  const record = html`<details class="json-record" data-key="json-record"><summary data-key="json-record-summary">Full record from the monitor API</summary>${copyButton("record", "Copy record")}${codeBlock("record", detail)}</details>`;
  return html`${tools}${note}${frames(detail)}${record}`;
}
