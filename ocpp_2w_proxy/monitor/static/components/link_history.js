// Pure link-history model and its 24 h timeline bar. Down periods are drawn lower as well as
// in another colour, and summarised in words; each outage is also listed with its reason.

import { formatDuration, formatTime } from "../format.js";
import { html } from "../html.js";

export const HISTORY_SECONDS = 24 * 3600;

/**
 * Up/down segments of one link over [from, now]. Before the first recorded change the link was
 * in the opposite state of that change; without changes it was in its current state throughout.
 * Each segment keeps the note of the change that started it (why it went down, how it came up).
 */
export function linkSegments(changes, { up, from, now }) {
  const sorted = changes.filter((change) => change.at <= now).sort((a, b) => a.at - b.at);
  let state = sorted.length > 0 ? !sorted[0].up : up;
  let start = from;
  let note = null;
  const segments = [];
  for (const change of sorted) {
    if (change.up === state) continue;
    if (change.at > start) segments.push({ from: start, to: change.at, up: state, note });
    start = Math.max(change.at, from);
    state = change.up;
    note = change.note ?? null;
  }
  segments.push({ from: start, to: now, up: state, note });
  return segments.filter((segment) => segment.to > segment.from);
}

/** The down segments, newest first, each with the note of the reconnect that ended it (null while still down). */
export function outages(segments) {
  return segments
    .map((segment, index) => ({ segment, next: segments[index + 1] }))
    .filter(({ segment }) => !segment.up)
    .map(({ segment, next }) => ({ ...segment, recovery: next?.note ?? null }))
    .reverse();
}

/** How often and how long the link was down. */
export function downtime(segments) {
  const down = segments.filter((segment) => !segment.up);
  return { count: down.length, seconds: down.reduce((sum, segment) => sum + segment.to - segment.from, 0) };
}

export function downtimeText(segments) {
  const { count, seconds } = downtime(segments);
  if (count === 0) return "no outage in 24 h";
  return `down ${count}× · ${formatDuration(seconds)} in 24 h`;
}

/** The timeline bar for segments within [from, now]. */
export function timelineBar(segments, { from, now }) {
  const span = now - from;
  const x = (at) => ((at - from) / span) * 1000;
  const rects = segments.map((segment) => {
    const state = segment.up ? "up" : "down";
    const [y, height] = segment.up ? [2, 16] : [8, 10];
    const note = segment.note ? ` · ${segment.note}` : "";
    return html`<rect class="timeline__${state}" x="${x(segment.from)}" y="${y}" width="${Math.max(x(segment.to) - x(segment.from), 1)}" height="${height}"><title>${state} ${formatTime(segment.from)}–${formatTime(segment.to)}${note}</title></rect>`;
  });
  return html`<svg class="timeline" viewBox="0 0 1000 20" preserveAspectRatio="none" role="img" aria-label="${downtimeText(segments)}">${rects}</svg>`;
}
