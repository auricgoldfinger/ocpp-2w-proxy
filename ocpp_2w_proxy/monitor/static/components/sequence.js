// Mini sequence diagram of one exchange: a lifeline per participant, an arrow per frame and a
// note per proxy decision, in time order. Pure: detail in, SVG string out.

import { formatDuration } from "../format.js";
import { html } from "../html.js";

const LEFT = 64;
const COLUMN = 132;
const TOP = 34;
const ROW = 30;
const HEAD = 7;

function participants(detail, lanes) {
  const present = new Set(["proxy", ...detail.frames.map((frame) => frame.peer)]);
  for (const decision of detail.decisions) present.add(decision.peer);
  if (detail.origin !== "proxy" && detail.origin !== "debug") present.add(detail.origin);
  const ordered = lanes.map((lane) => lane.key).filter((key) => present.has(key));
  const extra = [...present].filter((key) => !ordered.includes(key));
  return [...ordered, ...extra];
}

function frameLabel(frame, action) {
  if (frame.msg_type === "call") return action;
  if (frame.msg_type === "error") return `ERR ${frame.error?.code ?? ""}`.trim();
  return "result";
}

function arrowHead(frame, x, y, direction) {
  const back = x - direction * HEAD;
  const points = `${back},${y - HEAD / 1.6} ${x},${y} ${back},${y + HEAD / 1.6}`;
  if (frame.msg_type === "call") return html`<polygon class="seq-head seq-head--filled" points="${points}"></polygon>`;
  if (frame.msg_type === "error") return html`<text class="seq-cross" x="${x - direction * 4}" y="${y + 4}" text-anchor="middle">✕</text>`;
  return html`<polyline class="seq-head seq-head--open" points="${points}"></polyline>`;
}

function frameRow(frame, y, columnOf, action) {
  const proxyX = columnOf("proxy");
  const peerX = columnOf(frame.peer);
  const [x1, x2] = frame.dir === "in" ? [peerX, proxyX] : [proxyX, peerX];
  const direction = Math.sign(x2 - x1) || 1;
  const label = frameLabel(frame, action);
  return html`<g class="seq-msg seq-msg--${frame.msg_type}"><title>${frame.dir === "in" ? "received from" : "sent to"} ${frame.peer} · ${frame.msg_type} · wire id ${frame.wire_id ?? "–"}</title><line x1="${x1}" y1="${y}" x2="${x2}" y2="${y}"></line>${arrowHead(frame, x2, y, direction)}<text class="seq-label" x="${(x1 + x2) / 2}" y="${y - 5}" text-anchor="middle">${label}</text></g>`;
}

function decisionRow(decision, y, columnOf) {
  const x = columnOf("proxy");
  return html`<g class="seq-note"><title>${decision.text}</title><rect x="${x - 4}" y="${y - 11}" width="${COLUMN - 12}" height="16" rx="2"></rect><text x="${x + 2}" y="${y + 1}">◆ ${decision.reason} → ${decision.peer}</text></g>`;
}

/** The SVG for one exchange detail; `lanes` (from lanesFor) fix the participant order. */
export function sequenceDiagram(detail, lanes) {
  const columns = participants(detail, lanes);
  const columnOf = (key) => LEFT + columns.indexOf(key) * COLUMN + COLUMN / 2;
  const rows = [
    ...detail.frames.map((frame) => ({ at: frame.at, frame })),
    ...detail.decisions.map((decision) => ({ at: decision.at, decision })),
  ].sort((a, b) => a.at - b.at);
  const width = LEFT + columns.length * COLUMN;
  const height = TOP + Math.max(rows.length, 1) * ROW + 10;
  const firstAt = rows[0]?.at ?? detail.started_at;

  const heads = columns.map((key) => html`<text class="seq-actor" x="${columnOf(key)}" y="16" text-anchor="middle">${key}</text><line class="seq-life" x1="${columnOf(key)}" y1="24" x2="${columnOf(key)}" y2="${height - 4}"></line>`);
  const body = rows.map((row, at) => {
    const y = TOP + at * ROW + ROW / 2;
    const time = html`<text class="seq-time" x="4" y="${y + 4}">+${formatDuration(row.at - firstAt)}</text>`;
    const content = row.frame ? frameRow(row.frame, y, columnOf, detail.action) : decisionRow(row.decision, y, columnOf);
    return html`${time}${content}`;
  });
  const empty = rows.length === 0 ? html`<text class="seq-time" x="${LEFT}" y="${TOP + 16}">No frames recorded</text>` : "";
  return html`<svg class="sequence" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="Sequence of ${rows.length} frames and decisions">${heads}${body}${empty}</svg>`;
}
