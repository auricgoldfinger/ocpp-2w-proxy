// Swimlane markup for the Exchanges list. Pure: models in, SafeHtml out.
// Desktop shows one cell per lane; below 700 px CSS hides the lanes and shows the fan-out strip.

import { formatTime } from "../format.js";
import { html } from "../html.js";

export function badge({ label, icon, tone, title }) {
  return html`<span class="badge tone-${tone}" title="${title ?? label}"><span class="badge__icon" aria-hidden="true">${icon}</span><span class="badge__label">${label}</span></span>`;
}

function chips(items) {
  return items.map((chip) => html`<span class="chip" title="${chip.title}">${chip.label}</span>`);
}

function tags(items) {
  return items.map((tag) => html`<span class="tag" title="${tag.title}">${tag.label}</span>`);
}

function legContent(leg) {
  return html`${badge(leg.view)}${chips(leg.chips)}${tags(leg.tags)}`;
}

function originContent(cell) {
  const marker = cell.lane === "proxy"
    ? html`<span class="marker marker--proxy-origin" aria-hidden="true">◇</span>`
    : html`<span class="marker marker--origin" aria-hidden="true">●</span>`;
  const tag = cell.tag
    ? html`<span class="tag tag--origin" title="${cell.tag.title}">${cell.tag.icon} ${cell.tag.label}</span>`
    : html`<span class="sr-only">origin</span>`;
  return html`${marker}${tag}${cell.leg ? legContent(cell.leg) : ""}`;
}

const CELL_CONTENT = {
  origin: originContent,
  leg: legContent,
  "not-sent": (cell) => badge(cell.view),
  relay: () => html`<span class="marker marker--relay" aria-hidden="true">◆</span>`,
  empty: () => html`<span class="marker marker--empty" aria-hidden="true">·</span>`,
};

function cell(model) {
  return html`<span class="cell cell--${model.kind} path-${model.path}"><span class="sr-only">${model.laneLabel}:</span>${CELL_CONTENT[model.kind](model)}</span>`;
}

function strip(items) {
  const parts = items.map(
    (item) => html`<span class="strip__item tone-${item.tone}" title="${item.title ?? item.label}">${item.peer}<span aria-hidden="true">${item.icon}</span><span class="sr-only"> ${item.label}</span></span>`,
  );
  return html`<span class="strip">${parts}</span>`;
}

function rowFlags(row) {
  return html`${row.incomplete ? html`<span class="tag tag--warn" title="Open during a monitor gap; some events may be missing">⚠ incomplete</span>` : ""}${row.problem ? html`<span class="tag tag--problem" title="A leg failed or was dropped">problem</span>` : ""}`;
}

/** The sticky header row naming every lane. */
export function laneHeader(lanes) {
  const heads = lanes.map((lane) => {
    const role = lane.role === "primary" ? html`<span class="lane-head__role">PRIMARY</span>` : "";
    return html`<span class="lane-head lane-head--${lane.role}" title="${lane.label}">${role}${lane.label}</span>`;
  });
  return html`<div class="lanes-head" aria-hidden="true"><span>time</span><span>action / headline</span>${heads}</div>`;
}

/**
 * One exchange row; `href` opens its detail drawer. The link wraps the action and headline only,
 * so a row can hold other links; the view makes the rest of the row follow it on click.
 */
export function exchangeRow(row, href) {
  const classes = `row row--exchange${row.problem ? " row--problem" : ""}${row.incomplete ? " row--incomplete" : ""}`;
  return html`<li class="${classes}" data-id="${row.id}"><span class="row__time">${formatTime(row.startedAt)}</span><span class="row__what"><a class="row__link" href="${href}"><span class="row__action">${row.action}</span> <span class="row__headline">${row.headline ?? ""}</span></a>${rowFlags(row)}</span>${row.cells.map(cell)}${strip(row.strip)}</li>`;
}

/** The separator for a monitor gap. */
export function gapRow(gap) {
  return html`<li class="row row--gap" role="separator"><span class="gap">⚠ ${gap.dropped} monitor events lost ${formatTime(gap.from_at)}–${formatTime(gap.to_at)} · relaying unaffected</span></li>`;
}
